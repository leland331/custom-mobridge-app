"""Parity harness: never send bytes that aren't byte-matched to the official app."""
import json

import pytest

from mobridge import encoders, parity
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage


def test_builtin_references_reproduce_exactly():
    rows = parity.check_all()
    assert rows and all(ok for _, ok, _ in rows)


def test_only_captured_0700_writes_are_active_verified():
    # ACTIVE-capture references are the exact write frames the app was sniffed
    # performing: 0x0700 channel-property records and the 0x0704 filter-def from
    # the 2026-10-22 session. They clear byte-exact ONLY -- neither command is in
    # SEND_SAFE_COMMANDS, so there is no command-level unlock for a write.
    # Everything else stays at a weaker source; a command with no reference stays
    # blocked.
    actives = [r for r in parity.load_references()
               if r.source is parity.Status.ACTIVE_CAPTURE]
    assert {r.cmd for r in actives} == {0x0700, 0x0704}
    assert all(r.cmd not in parity.SEND_SAFE_COMMANDS for r in actives)
    assert not parity.can_send(0x0610)
    with pytest.raises(parity.ParityError, match='BLOCKED by parity rule'):
        parity.assert_can_send(0x0610, 'CanFrameSend')


def test_send_policy_blocks_only_the_flash_family():
    # Real framer + client: a DSP write goes out (the gate is gone, 16:07Z),
    # a flash-family frame is refused before a byte leaves.
    from test_mdi_framer import MdiEchoDevice
    from mobridge.client import MobridgeClient
    dev = MdiEchoDevice()
    c = MobridgeClient(dev, MdiFramer())
    f = MdiFramer()
    c.send_frame(f.encode(MdiMessage(cmd=0x0700, data=bytes.fromhex('0000ed00'))))
    assert len(dev.sent) == 1
    for cmd in parity.LOCKED_COMMANDS:
        with pytest.raises(parity.ParityError):
            c.send_frame(f.encode(MdiMessage(cmd=cmd, data=b'')))
    assert len(dev.sent) == 1          # nothing more went on the wire
    assert 0x0101 not in parity.LOCKED_COMMANDS   # LocalIDWrite: capability-gated, not locked
    assert parity.locked_reason(0x0201) is None and parity.locked_reason(0x0700) is None   # 23:18Z "unlock flashing"
    assert 0x0010 not in parity.LOCKED_COMMANDS and 0x0011 not in parity.LOCKED_COMMANDS


def test_capture_reference_unlocks_exactly_that_command(tmp_path):
    f = MdiFramer()
    frame = f.encode(MdiMessage(cmd=0x1234, data=b'\x09'))
    ref = tmp_path / 'refs.json'
    json.dump({'frames': [{'name': 'x', 'cmd': 0x1234, 'data': '09',
                           'expected': frame.hex(), 'source': 'capture'}]}, open(ref, 'w'))
    assert parity.status_for_cmd(0x1234, str(ref)) is parity.Status.ACTIVE_CAPTURE
    assert parity.can_send(0x1234, str(ref))
    assert not parity.can_send(0x0610, str(ref))        # others stay blocked


def test_a_mismatched_capture_does_not_clear():
    # A capture ref whose bytes we DON'T reproduce must not count as verified.
    import tempfile, os
    d = tempfile.mkdtemp(); p = os.path.join(d, 'r.json')
    json.dump({'frames': [{'name': 'bad', 'cmd': 0x1234, 'data': '09',
                           'expected': 'deadbeef', 'source': 'capture'}]}, open(p, 'w'))
    assert parity.status_for_cmd(0x1234, p) is parity.Status.UNCONFIRMED


def test_value_encoders_refuse_until_confirmed():
    for name, enc in encoders.ENCODERS.items():
        assert not enc.confirmed
        with pytest.raises(encoders.EncodingUnconfirmed):
            enc.encode(1.0)
    assert encoders.confirmed_encoders() == []


def test_status_ranks_and_clearance():
    assert not parity.Status.FIRMWARE_DERIVED.clears_sending
    assert not parity.Status.APP_DERIVED.clears_sending
    for s in (parity.Status.CROSS_VALIDATED, parity.Status.STORED_CONFIG,
              parity.Status.PASSIVE_CAPTURE, parity.Status.ACTIVE_CAPTURE):
        assert s.clears_sending


def test_cross_validation_clears_sending(tmp_path):
    f = MdiFramer()
    frame = f.encode(MdiMessage(cmd=0x0021, data=b'\x01'))
    ref = tmp_path / 'r.json'
    import json as _j
    _j.dump({'frames': [{'name': 'x', 'cmd': 0x0021, 'data': '01',
                         'expected': frame.hex(), 'source': 'cross'}]}, open(ref, 'w'))
    assert parity.can_send(0x0021, str(ref))


def test_stored_and_passive_sources_parse_and_clear(tmp_path):
    f = MdiFramer()
    import json as _j
    for src in ('stored', 'passive', 'active'):
        frame = f.encode(MdiMessage(cmd=0x0400, data=b'\x02'))
        ref = tmp_path / ('%s.json' % src)
        _j.dump({'frames': [{'name': src, 'cmd': 0x0400, 'data': '02',
                             'expected': frame.hex(), 'source': src}]}, open(ref, 'w'))
        assert parity.can_send(0x0400, str(ref))


def test_stored_config_validator_blocks_without_codec():
    from mobridge import stored_config
    svs = [stored_config.StoredValue('eq_gain', b'\x12\x34')]
    res = stored_config.validate(svs)
    assert res['proven'] == [] and 'eq_gain' in res['blocked']


def test_nvm_region_scan_and_summary():
    from mobridge import stored_config
    data = (stored_config.NVM_MAGIC + b'\x01\x02\x03' + b'\xff' * 100
            + b'ABCD' + b'\xff' * 50 + b"\xee" * 10 + b"\xff" * 16)
    nvm = stored_config.NvmDump(data)
    sm = stored_config.nvm_summary(nvm)
    assert sm['has_magic'] is True
    assert sm['written_bytes'] == 7 + 4 + 10   # magic+123, ABCD, ee*10
    assert len(sm['regions']) == 3


def test_load_dumps_picks_up_nvm_and_rax(tmp_path):
    from mobridge import stored_config
    (tmp_path / 'nvm_settings_0x60000.bin').write_bytes(stored_config.NVM_MAGIC + b'\x00' * 10)
    (tmp_path / 'appimage_0x100000.bin').write_bytes(b'\x01\x50' + b'\x00' * 20)
    (tmp_path / 'backup_1.rax').write_bytes(b'{"x":1}')
    d = stored_config.load_dumps(str(tmp_path))
    assert d['nvm'] is not None and d['app'] is not None and len(d['rax']) == 1
    assert d['rax'][0].json == {'x': 1}


# --- candidate gain formula corroboration (does NOT open the gate) -------------
import os as _os
from mobridge import stored_config as _sc, encoders as _enc

_FIELD_MAP = '/mnt/project-files/device-config/NVM_FIELD_MAP.json'


def test_gain_candidate_matches_stored_config_exactly():
    if not _os.path.exists(_FIELD_MAP):
        import pytest; pytest.skip('field map not present')
    r = _sc.corroborate(_FIELD_MAP, '.gain', 'gain_db', ignore_raw={b'\x00\x00'})
    # every real stored gain reproduced byte-for-byte by the candidate formula
    assert r['fields_checked'] > 0
    assert r['mismatches'] == []
    assert r['exact_matches'] == r['fields_checked']
    # his data hands us these known (display dB -> NVM bytes) pairs for free
    # (display = raw/512 + 9.5, so 0xED00 = unity = 0.0 dB)
    assert r['distinct_pairs'] == {-3.0: '00e7', -2.0: '00e9', 0.0: '00ed'}


def test_gain_still_blocked_despite_corroboration():
    # Corroboration must NOT clear sending: the absolute reference is unanchored.
    import pytest
    with pytest.raises(_enc.EncodingUnconfirmed):
        _enc.ENCODERS['gain_db'].encode(0.0)
    assert _enc.ENCODERS['gain_db'].confirmed is False


# --- frame-level (byte-exact) gate after the 2026-10-21 passive capture ---------
def test_frame_gate_clears_exact_capture_but_not_other_payloads():
    f = MdiFramer()
    # The exact captured Ping (cmd 0x0001, empty payload) is proven -> clears.
    ping = f.encode(MdiMessage(cmd=0x0001, data=b''))
    assert parity.can_send_frame(ping)
    # cmd 0x0400 was captured as a 1-byte toggle [01]; a FlashRead builds 0x0400
    # with a 6-byte addr/size payload -> DIFFERENT frame -> must stay BLOCKED even
    # though the command id's framing is proven.
    toggle = f.encode(MdiMessage(cmd=0x0400, data=b'\x01'))
    flashread = f.encode(MdiMessage(cmd=0x0400, data=b'\x00\x06\x00\x00\x00\x10'))
    assert parity.can_send_frame(toggle)          # exactly what the app sent
    assert not parity.can_send_frame(flashread)   # same command, unproven payload
    # No DSP write was captured -> every write frame stays blocked.
    assert not parity.can_send_frame(f.encode(MdiMessage(cmd=0x0700, data=b'\x00\x00')))


# --- authorised ungating of connect + reads (2026-10-21) ----------------
def _fc(cmd, data=''):
    from mobridge.message import MdiMessage
    f = MdiFramer()
    return parity.frame_cleared(f.encode(MdiMessage(cmd=cmd, data=bytes.fromhex(data))), cmd)


def test_connect_and_reads_are_cleared_to_send():
    # connect / handshake / baud / stream-toggle: exact captured frames
    assert _fc(0x0001) and _fc(0x0020) and _fc(0x0025) and _fc(0x0282)
    assert _fc(0x0002, '00038400')      # SetBaud 230400
    assert _fc(0x0400, '01')            # stream toggle (the captured 1-byte form)
    # DSP reads clear for ANY channel/selector (command-level), not just captured ones
    assert _fc(0x0710, '0700') and _fc(0x0714, '0605') and _fc(0x0716, '09')


def _pw(cmd, data=''):
    """The pinned write GRAMMAR (self-check, no longer the send path)."""
    from mobridge.message import MdiMessage
    f = MdiFramer()
    return parity.proven_write(f.encode(MdiMessage(cmd=cmd, data=bytes.fromhex(data))))


def test_every_frame_the_official_app_sends_clears():
    # the gate is gone: any DSP write/read frame the official app would send clears
    assert _fc(0x0400, '000600000010')
    assert _fc(0x0700, '0004e70000000000000100007fffffff')
    assert _fc(0x0707, '0000')
    assert _fc(0x0100, '00000002') and _fc(0x0101, '0000000200000005')   # LocalID (MOST, capability-gated)
    for cmd in (0x0201, 0x0280, 0x0281):    # flashing unlocked (23:18Z)
        assert _fc(cmd), hex(cmd)
    assert _fc(0x0010, '') and _fc(0x0011, '')                      # Reset / FactoryReset: empty frames
    assert not parity.frame_cleared(b'\xaa\x00\x01', None)             # malformed
    assert not parity.frame_cleared(MdiFramer().encode(MdiMessage(cmd=0x0700, data=b'')), 0x0704)  # cmd mismatch


def test_captured_0700_writes_clear_only_byte_exact():
    # The ACTIVE-capture 0x0700 writes (the exact gain/delay/limiter/flag frames
    # the app sent) clear as byte-exact frames...
    assert _fc(0x0700, '0000eccd')                       # gain -0.1 dB (len4)
    assert _fc(0x0700, '0000ed00')                       # gain  0.0 dB (len4)
    assert _fc(0x0700, '000bed0000000000010000007fffffff')  # ch11 flag ON (len16)
    assert _fc(0x0700, '000bed0000000000000000007fffffff')  # ch11 flag OFF (len16)
    assert _fc(0x0700, '0001e10000000000000000007fffffff')  # ch1 gain -6.0 (len16)
    assert _fc(0x0700, '0001ed0000138800000000007fffffff')  # ch1 delay 5 ms (len16)
    assert _fc(0x0700, '0001ed0000000000000000000cccccd0')  # ch1 limiter -20 (len16)
    assert _fc(0x0704, '030001030407d000960c0c')             # ch1 cheby 150/2000 12/12
    # ...master LEN4 gain clears for any in-window value (tier 3, proven identical)...
    assert _fc(0x0700, '0000ec00')
    # ...and LEN16 records / filter defs on other channels clear by grammar (tier 3),
    # while neither command is a command-level unlock.
    assert _fc(0x0700, '000ced0000000000010000007fffffff')      # ch12 mute
    assert _fc(0x0700, '0002e10000000000000000007fffffff')      # ch2 gain -6
    assert _fc(0x0704, '030002030407d000960c0c')                # same filter on ch2
    assert not _pw(0x0700, '0002e100')                          # LEN4 is master only (grammar)


# --- tier 3: writes proven identical (the user's explicit go, 2026-10-22) ------------
def test_proven_identical_writes_clear_and_the_rest_stay_blocked():
    from mobridge import wire_apply
    # gain LEN4 for ANY in-window value, EQ band + its biquad, remove, console byte
    for fr in (wire_apply.gain_frames(0, -6.0) + wire_apply.gain_frames(0, 2.5)
               + wire_apply.record_frames(1, gain_db=-6.0, phase=True, mute=True)
               + wire_apply.record_frames(11, passthrough=True)
               + wire_apply.eq_band_frames(1, 1000.0, 3.0, 5.0)
               + wire_apply.eq_frames(2, [(100.0, -3.0, 2.0), (2500.0, 1.5, 4.0, 3.0)])
               + wire_apply.eq_band_remove_frames(1) + wire_apply.debug_send_frames('dspdebug rms 1')):
        assert parity.proven_write(fr), fr.hex()
    # 0x0702: flat and mode-2 EQ curves clear (sweep-proven 2026-10-22); a bin
    # outside the -27..+9 dB window or a short table stays blocked
    assert parity.proven_write(wire_apply.record_frames(1, bands=[(1000.0, 3.0, 5.0)])[1])
    assert _pw(0x0702, '0001' + 'ed00' * 511 + 'ed01')
    assert not _pw(0x0702, '0001' + 'ed00' * 511 + 'ff01')
    assert not _pw(0x0702, '0001' + 'ed00' * 511 + 'b6ff')
    assert not _pw(0x0702, '0001' + 'ed00' * 511)
    # still blocked:
    assert not _pw(0x0700, '0001b000')                            # out-of-window gain
    assert not _pw(0x0700, '0001ed00')                            # LEN4 on a channel
    assert not _pw(0x0700, '0001ed0002000000000000007fffffff')    # phase byte not 0/1
    assert _pw(0x0700, '0001ed0000000000000001007fffffff')        # test-tone [10]=1: inert in fw, exe-pinned (FW_LOCKED_WRITES §3)
    assert not _pw(0x0700, '0001ed0000000000000000017fffffff')    # test-tone [11] never set by the exe
    assert not _pw(0x0704, '030001030507d000960c0c')              # design 5 unknown
    assert not _pw(0x0704, '030001020400000005000c0c')            # wrong LEN
    assert not _pw(0x0704, '030001020400000005 0c0c'.replace(' ', ''))  # HPF f2 = 5 Hz
    assert not _pw(0x0704, '0300010104000500000c0c')              # LPF f1 = 5 Hz
    assert not _pw(0x0704, '030001030407d007d00c0c')              # band f1 < f2 + 20
    assert not _pw(0x0704, '030001030407d000961e0c')              # slope 30 not allowed
    assert not _pw(0x0704, '050001447a00004200450046')            # LEN 12: not 3 + 10N
    assert not _pw(0x0704, '050001447a00004200' + '4e00' + '4200')  # Q 24 > 20
    assert not _pw(0x0704, '050001447a0000' + '4f00' + '42004200')  # gain +28 dB
    assert not _pw(0x0708, '000102' + '00' * 20)                   # count 2, one stage sent
    assert _pw(0x0702, '0000' + 'ed00' * 512)                      # 0x0702 FLAT table (captured) clears
    assert not parity.proven_write(b'\xaa\x00\x01\x07\x00\x01\xf7')   # LEN1 0x0700 nonsense
