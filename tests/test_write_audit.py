"""Byte audit: every write payload the flush sends equals the CMD_TABLE
transcription (cmdspec) for every firmware branch and with/without the
device preset byte (dev+0x46)."""
import pytest

from mobridge import cmdspec as C, dspwrite as D, model as M

FWS = [100, 150, 199, 200, 201, 202, 203, 205, 206, 207, 208, 209, 210, 211]


def _root(fw, presets):
    r = M.DspModel(fw=fw)
    r.dev_presets = presets
    r.map_speaker(next(iter(M.NAME_BY_ROLE)), 0)
    return r


@pytest.mark.parametrize('fw', FWS)
@pytest.mark.parametrize('presets', [0, 4])
def test_flush_payloads_match_cmd_table(fw, presets):
    r = _root(fw, presets)
    s = r.speaker(r.channel_role[0], 0)
    s.set_gain(-3.0); s.set_delay(1.5); s.set_mute(True); s.eq.add(1000, 2.0)
    raw = M.gain_raw(-3.0, fw) & 0xFFFF
    from mobridge import channel_record
    exp = C.write_channel(0, raw, False, 1500, 0, True, s.testtone, s.testtone_type,
                          channel_record.limiter_raw(s.limit_dbfs), fw, presets, 0)
    assert D.channel_props_payload(r, s, 0) == exp
    assert D.channel_appprops_payload(r, s, 0) == C.write_chan_props(
        0, M.ORDINAL_BY_ROLE.get(s.role, 0), 0, fw, presets, 0)
    if fw in (201, 202):
        chain = s.biquad_chain()
        if chain:
            import struct
            base = C.write_filter(0, s.filter_type, s.filter_design, s.f1, s.f2, s.slope1, s.slope2, fw, presets, 0)
            assert D.channel_filter_payload(r, s, 0) == base[:-20] + struct.pack('>5f', *chain[0][:5])
        else:
            with pytest.raises(D.Held):
                D.channel_filter_payload(r, s, 0)
    elif fw >= 200:
        assert D.channel_filter_payload(r, s, 0) == C.write_filter(
            0, s.filter_type, s.filter_design, s.f1, s.f2, s.slope1, s.slope2, fw, presets, 0)
    assert D.channel_points_payload(s, 0, r) == C.write_chan_points(0, s.eq.tuples(), fw, presets, 0)
    assert D.master_points_payload(r, 0) == C.write_master_points(r.presets[0].master.eq.tuples(), fw, presets, 0)
    if fw == 100:
        assert D.master_props_payload(r, 0) == C.write_master(M.gain_raw(0.0, fw) & 0xFFFF, fw, presets, 0,
                                                             tuple(M.gain_unity(fw) >> 8 for _ in range(4)))
    else:
        assert D.master_props_payload(r, 0) == C.write_master(M.gain_raw(0.0, fw) & 0xFFFF, fw, presets, 0)
    if fw < 207:
        b = D.master_bins_payload(r, 0)
        assert len(b) == (0x402 if presets else 0x401) and set(b[-0x400:]) == {0xE7}
    else:
        assert len(D.master_bins_payload(r, 0)) == (0x402 if presets else 0x401)


@pytest.mark.parametrize('op', ['mute', 'phase', 'pass', 'delay', 'limit'])
def test_flag_and_scalar_setters_send_a_lone_0700(op):
    """CH1_CONTROL_FRAMES.md: delay/limiter/phase/mute/passthrough = one 0x0700."""
    r = _root(210, 4)
    s = r.speaker(r.channel_role[0], 0); D.flush(r, product_flags=0xffffffff)
    {'mute': lambda: s.set_mute(True), 'phase': lambda: s.set_invert_phase(True),
     'pass': lambda: s.set_passthrough(True), 'delay': lambda: s.set_delay(5.0),
     'limit': lambda: s.set_limit(-20.0)}[op]()
    assert D.flush(r, product_flags=0xffffffff).cmds() == [0x0700]


def test_gain_keeps_the_captured_four_frames():
    r = _root(210, 4)
    s = r.speaker(r.channel_role[0], 0); D.flush(r, product_flags=0xffffffff)
    s.set_gain(-6.0)
    assert D.flush(r, product_flags=0xffffffff).cmds() == [0x0700, 0x0702, 0x0708, 0x0704]


def test_no_preset_bytes_without_device_presets():
    r = _root(210, 0)
    r.root_flags |= M.ROOT_PRESET | M.ROOT_INPUT_MAP
    for p in r.presets:
        p.upmix.flags = 1; p.timealign.flags = M.TA_UNITS
    f = D.flush(r, product_flags=0xffffffff)
    assert 0x0716 not in f.cmds()
    assert D.routing_payload(r)[:1] != b'\x00' or r.channel_count == 0
    up = [pl for c, pl in zip(f.cmds(), f.payloads()) if c == 0x0707] if hasattr(f, 'payloads') else []
    for pl in up:
        assert len(pl) in (1 + 2 + 12, 1 + 1 + 4)


def test_upmix_all_presets_then_timealign():
    r = _root(210, 4); D.flush(r, product_flags=0xffffffff)
    for p in r.presets:
        p.upmix.flags = 1; p.timealign.flags = M.TA_UNITS
    f = D.flush(r, product_flags=0xffffffff)
    subs = [w[5] for w in f.wire() if w[3:5] == b'\x07\x07']
    assert subs == [2] * r.preset_count + [3] * r.preset_count


def test_headunit_never_sends_subgain():
    r = _root(210, 4)
    r.headunit.set('subgain', 5); r.headunit.set('volume', 3)
    assert D.headunit_payloads(r.headunit) == [bytes([5, 3])]


def test_preset_byte_rules_0701_0707():
    for presets, extra in ((0, 0), (4, 1)):
        r = _root(210, presets); r.channel_count = 2
        assert len(D.routing_payload(r)) == extra + 2 * 8
        assert len(D.upmix_payload(r, r.presets[0].upmix)) == 1 + extra + 2 + 12
        assert len(D.timealign_payload(r, r.presets[0].timealign)) == 1 + extra + 1 + 4


def test_feature_writes_once_without_device_presets():
    r = _root(210, 0); D.flush(r, product_flags=0xffffffff)
    for p in r.presets:
        p.upmix.flags = 1; p.timealign.flags = M.TA_UNITS
    subs = [w[5] for w in D.flush(r, product_flags=0xffffffff).wire() if w[3:5] == b'\x07\x07']
    assert subs == [2, 3]


def test_xover_link_on_matches_capture_order():
    r = M.DspModel(fw=210); r.dev_presets = 4
    r.map_speaker(0x100, 0); r.map_speaker(0x800, 1)
    D.flush(r, product_flags=0xffffffff)
    a, b = r.speaker(0x100, 0), r.speaker(0x800, 0)
    a.link_xover(b)
    f = D.flush(r, product_flags=0xffffffff)
    seq = [(w[3] << 8 | w[4], w[5]) for w in f.wire()]
    assert seq == [(0x0708, 0), (0x0704, 2), (0x0704, 3), (0x0708, 0), (0x0704, 2), (0x0704, 3)]
    subs2 = [w[5:-1] for w in f.wire() if w[3:6] == b'\x07\x04\x02']
    assert subs2 == [bytes.fromhex('02000109' '01'), bytes.fromhex('0200020c' '01')]


def test_xover_partner_table():
    r = M.DspModel(fw=210); r.dev_presets = 4
    r.map_speaker(0x100, 0); r.map_speaker(0x800, 1); r.map_speaker(0x8000, 2)
    assert r.link_xover_partner(r.speaker(0x100, 0)) is r.speaker(0x800, 0)
    assert r.link_xover_partner(r.speaker(0x8000, 0)) is None
