"""cmdspec: every exe command builder is byte-identical to the proven builders
and to the captured frames; parsers round-trip the builders."""
import struct

from mobridge import cmdspec as C
from mobridge import channel_record, eq, biquad, filterbins, connect, parity
from mobridge import dspwrite as D
from mobridge import model as M

FW, PRESETS = 210, 4


def test_handshake_matches_capture():
    assert C.identify() == b'' and C.list_query() == b'' and C.info_block() == b''
    assert C.set_baud(230400) == bytes.fromhex('00038400')
    assert C.stream_flag(True) == b'\x01' and C.receive_file_ack() == b''
    built = {0x0002: C.set_baud(), 0x0001: C.identify(), 0x0020: C.list_query(), 0x0025: C.info_block(),
             0x0400: C.stream_flag(True), 0x0282: C.receive_file_ack(), 0x0000: b''}
    for step in connect.HANDSHAKE:
        assert built[step.cmd] == step.payload, step


def test_channel_record_identical_to_proven_builder():
    want = channel_record.build_record(4, preset=0, gain_db=-3.0, delay_ms=1.5, limiter_dbfs=-6.0)
    got = C.write_channel(3, channel_record.gain_raw(-3.0), False, channel_record.delay_raw(1.5), 0, False,
                          0, 0, channel_record.limiter_raw(-6.0), FW, PRESETS, 0)
    assert got == want and len(got) == 16
    assert C.write_master(0xED00, FW, PRESETS, 1) == channel_record.build_gain_only(0, 0.0, preset=1)
    # legacy layouts shrink exactly as the decompile says
    assert len(C.write_channel(0, 0xED00, 0, 0, 0, 0, 0, 0, 0, 0xCD, PRESETS)) == 12      # no limiter
    assert len(C.write_channel(0, 0xED00, 0, 0, 0, 0, 0, 0, 0, 0xC9, 0)) == 10            # u8 gain, no preset
    assert len(C.write_master(0xED00, 100, PRESETS)) == 3 + 8 + 4       # u8 gain + legacy padding + 4 bytes


def test_eq_points_biquads_bins_identical():
    pts = [(1000.0, 3.0, 2.0), (250.0, -1.5, 0.7, 1.2)]
    assert C.write_chan_points(0, pts, FW, PRESETS, 0) == eq.build_eq_points(1, pts, preset=0)
    assert C.write_master_points(pts, FW, PRESETS, 2) == eq.build_master_points(pts, preset=2)
    assert C.write_chan_points(0, [], FW, PRESETS, 0) == bytes.fromhex('050001')
    stages = [biquad.rbj_peaking(1000.0, 3.0, 2.0), biquad.rbj_lpf(2000.0)]
    assert C.write_biquads(0, stages, PRESETS, 0) == biquad.build_0708(1, stages, preset=0)
    tbl = filterbins.build_0702(1, pts, preset=0)
    bins = [int.from_bytes(tbl[i:i + 2], 'big') for i in range(2, 1026, 2)]
    assert C.write_bins(1, bins, FW, PRESETS, 0) == tbl
    assert len(C.write_bins(0, [0] * 0x400, 0xCE, PRESETS)) == 2 + 0x400         # fw < 207: u8 table


def test_dsp_write_builders_match_dspwrite():
    r = M.DspModel(fw=FW); r.map_speaker(0x2000, 3); r.channel_count = 12
    s = r.speaker(0x2000)
    assert C.write_chan_props(3, 14, 0, FW, PRESETS, 0) == D.channel_appprops_payload(r, s, 3)
    s.set_filter_type(M.FT_LP)
    assert (C.write_filter(3, s.filter_type, s.filter_design, s.f1, s.f2, s.slope1, s.slope2, FW, PRESETS, 0)
            == D.channel_filter_payload(r, s, 3))
    assert C.write_name(b'MyAmp') == D.name_payload('MyAmp')
    assert C.select_preset(2) == D.select_preset_payload(2)
    assert C.write_routing([r.input_map.get(i, 0) for i in range(12)], FW, PRESETS) == D.routing_payload(r)
    assert C.write_sourcesel(0) == D.sourcesel_payload(r)
    r.set_input_gain(3, -6.0, 0)
    raws = [int(round(0xED00 + r.input_gains[0][k] * 512.0)) for k in range(1, 65)]
    assert C.write_input_gains(raws, FW, 0) == D.input_gains_payload(r, 0)
    r.set_input_delay(2.5, 0)
    assert C.write_input_delay(2500, 0) == D.input_delay_payload(r, 0)
    p = r.presets[0]
    assert C.write_timealign(0, 0, 0, FW, True, 0) == D.timealign_payload(r, p.timealign)
    u = p.upmix
    assert C.write_upmix(1 if u.enabled else 0, u.algorithm, u.lmsrate, u.adprate, u.adpgamma, FW, True, 0) \
        == D.upmix_payload(r, u)
    assert C.write_head_unit(0, 24) == bytes.fromhex('0018')


def test_localid_and_dead_jobs_have_exact_bytes():
    assert C.localid_read() == bytes.fromhex('00000002')
    assert C.localid_write(5) == bytes.fromhex('0000000200000005')
    assert C.parse_localid_read(bytes.fromhex('0000000200000007')) == {'node_id': 7}
    # never cleared to send on this unit (no firmware handler); parity keeps them shut
    assert not parity.proven_write(parity_frame(0x0101, C.localid_write(5)))
    assert C.parse_error(bytes.fromhex('0002')) == {'code': 2}
    assert C.parse_flash_progress(bytes.fromhex('08' + '00000000'))['phase'] == 'complete'
    assert C.parse_flash_progress(bytes.fromhex('0009' + '00000001'))['phase'] == 'error'


def parity_frame(cmd, payload):
    from mobridge.framing import MdiFramer
    from mobridge.message import MdiMessage
    return MdiFramer().encode(MdiMessage(cmd=cmd, data=payload))


def test_read_builders_and_parsers_round_trip():
    assert C.read_fw_version() == b'\x00' and C.read_name() == b'\x01'
    assert C.read_chan_props(3, PRESETS, 1) == bytes.fromhex('020104')
    assert C.read_master_points(PRESETS, 0) == bytes.fromhex('0600')
    assert C.read_routing(PRESETS) == b'\x00' and C.read_routing(0) == b''
    assert C.read_channel(3, PRESETS, 0) == bytes.fromhex('0004')
    assert C.read_timealign(FW, PRESETS, 1) == bytes.fromhex('0301')
    assert C.read_timealign(0xD0, PRESETS, 1) == b'\x03'              # preset byte needs fw >= 209
    assert C.parse_fw_version(b'\x00' + (210).to_bytes(4, 'big')) == {'fw': 210, 'state': None}
    assert C.parse_fw_version(b'\x00' + (211).to_bytes(4, 'big'))['state'] == 0x17
    assert C.parse_fw_version(b'\x00' + (400).to_bytes(4, 'big'))['fw'] == 0
    rec = C.write_channel(3, 0xE700, 1, 1500, 0, 1, 0, 0, 0x7FFFFFFF, FW, PRESETS, 0)
    # reply order differs from the request order (§3.7): build the reply by hand
    reply = bytes([0, 4]) + rec[2:4] + bytes([1, 0, 1]) + b'\x00\x00' + rec[5:7] + rec[12:16]
    got = C.parse_channel(reply, FW, True)
    assert got['channel'] == 3 and got['gain_raw'] == 0xE700 and got['invert'] == 1 and got['mute'] == 1
    assert got['delay_us'] == 1500 and got['limiter_raw'] == 0x7FFFFFFF
    pts = [(1000.0, 3.0, 2.0, 2.0)]
    parsed = C.parse_chan_points(C.write_chan_points(0, pts, FW, PRESETS, 0), FW, True)
    assert parsed['channel'] == 0 and parsed['points'][0][0] == 1000.0 and abs(parsed['points'][0][1] - 3.0) < 1e-3
    f = C.parse_filter(C.write_filter(1, 1, 0, 150, 2000, 12, 24, FW, PRESETS, 0), FW, True)
    assert f == {'preset': 0, 'channel': 1, 'type': 1, 'design': 0, 'f1': 150, 'f2': 2000, 's1': 12, 's2': 24}
    ig = C.parse_input_gains(b'\x07\x00' + b'\xed\x00' * 64, FW, True)
    assert set(ig['raws']) == set(range(1, 65)) and ig['raws'][64] == 0xED00
    assert C.parse_input_delay(bytes.fromhex('08030bb8'), True) == {'preset': 3, 'us': 3000}
    assert C.parse_head_unit(bytes.fromhex('05b4')) == {'index': 5, 'value': 180}
    ta = C.parse_timealign(C.write_timealign(1, 1234, 5678, FW, False, 0))   # 0x070f reply has no preset byte
    assert ta == {'units': 1, 'closest_x100': 1234, 'furthest_x100': 5678}
    up = C.parse_upmix(C.write_upmix(1, 2, 0.5, 0.25, 0.125, FW, False, 0))
    assert up == {'b0': 1, 'b1': 2, 'fa': 0.5, 'fb': 0.25, 'fc': 0.125}
    rec0 = {'b0': 1, 'b1': 2, 'f4': 1.0, 'f8': [1.0, 2.0, 3.0, 4.0], 'f18': 5.0, 'f1c': 6.0, 'b20': [7, 8], 'b22': [9, 10]}
    w = C.write_upmix0(rec0, FW, True, 0)
    assert len(w) == 2 + 3 + 4 + 16 + 4 + 4 + 4
    back = C.parse_upmix0(b'\x00' + w[2:4] + w[5:])   # 0x070f reply: no preset byte, no one-shot byte
    assert back['f8'] == [1.0, 2.0, 3.0, 4.0] and back['b22'] == [9, 10]
    assert C.parse_preset_changed(bytes.fromhex('0302')) == {'preset': 2}
    assert C.parse_preset_changed(bytes.fromhex('0102')) is None
    assert C.parse_routing(b'\x00' + (5).to_bytes(8, 'big') * 2, FW, True, 12)['input_ids'] == [5, 5]
    assert C.parse_dir_list(bytes.fromhex('0001') + bytes.fromhex('0007') + b'config.rax'.ljust(0x100, b'\0')
                            + (1234).to_bytes(4, 'big'))['entries'] == [{'index': 7, 'name': 'config.rax', 'size': 1234}]


def test_bus_commands_layout():
    assert C.bus_msg_send(0, 1, 2, 0x0304, 5, b'\xaa\xbb') == bytes.fromhex('0102030405000002aabb')
    assert C.bus_msg_send(0x1234, 1, 2, 3, 4, b'') == bytes.fromhex('12340102000304000000')
    assert C.bus_0610(1, 2, 3, b'\x01' * 10) == bytes.fromhex('0001' '02' '00000003' + '01' * 8)
    assert C.bus_query(3) == b'\x03' and C.parse_bus_query(bytes.fromhex('03aabb07')) == {'type': 3, 'b36': 0xAA, 'b37': 0xBB, 'status': 7}
    assert C.bus_0624(None) == b'' and C.bus_0624(9) == b'\x09'
    assert C.parse_event_0646(bytes.fromhex('04aa1234')) == {'sub': 4, 'b14': 0xAA, 'w16': 0x1234}
    assert C.parse_event_070e(b'\x00') == {'type': 0}


def test_coverage_counts():
    cov = C.coverage()
    assert cov['tx_total'] >= 70 and cov['rx_total'] == 7
    names = [s.name for s in C.SPECS]
    assert len(names) == len(set(names)), 'duplicate spec'
    # the exe's TX inventory (CMD_TABLE §6) is fully represented
    inv = [0x0001, 0x0020, 0x0025, 0x0100, 0x0101, 0x0202, 0x0203, 0x0282, 0x0302, 0x0303, 0x0304, 0x0305,
           0x0400, 0x0401, 0x0610, 0x0612, 0x0620, 0x0621, 0x0624, 0x0625, 0x0644, 0x0645, 0x0649, 0x064a,
           0x064b, 0x064c, 0x0650, 0x0700, 0x0701, 0x0702, 0x0703, 0x0704, 0x0705, 0x0706, 0x0707, 0x0708,
           0x070f, 0x0710, 0x0711, 0x0713, 0x0714, 0x0716, 0x0717, 0x0718, 0x0719]
    have = {s.cmd for s in C.SPECS if s.direction == 'tx'}
    assert all(c in have for c in inv)
    assert {s.sub for s in C.SPECS if s.cmd == 0x0704} == {1, 2, 3, 5, 6, 7, 8}
    assert {s.sub for s in C.SPECS if s.cmd == 0x0714} == {0, 1, 2, 3, 5, 6, 7, 8}
    assert {s.sub for s in C.SPECS if s.cmd == 0x0707} == {0, 1, 2, 3}
    assert {s.sub for s in C.SPECS if s.cmd == 0x070f} == {0, 1, 2, 3}
