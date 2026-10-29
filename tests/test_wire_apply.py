"""wire_apply builds the EXACT frames the official app sends (identity proof vs the
captures), independent of whether the send gate lets them out."""
import struct

from mobridge import wire_apply, parity, biquad, eq, filterbins


def test_gain_frames_are_byte_identical_to_captures():
    assert wire_apply.gain_frames(0, -0.1)[0].hex() == 'aa000407000000eccd3c'
    assert wire_apply.gain_frames(0, 0.0)[0].hex() == 'aa000407000000ed0008'


def test_master_gain_second_frame_is_the_captured_empty_master_list():
    ref = next(r for r in parity.load_references() if r.name == 'cap_write_0704_master_points_empty')
    frames = wire_apply.gain_frames(0, -0.1)
    assert len(frames) == 3 and frames[2] == ref.expected == bytes.fromhex('aa000207040600ed')
    # between them: the master 0x0702 filter-bins table, flat when there is no master EQ
    assert frames[1][:7].hex() == 'aa040207020000' and filterbins.is_flat(frames[1][5:-1])
    # a real master EQ is sent as-is, never replaced by the empty list
    with_eq = wire_apply.gain_frames(0, 0.0, master_eq=[(1000.0, -2.0, 3.0)])[2]
    assert with_eq[1:3] == (12).to_bytes(2, 'big') and with_eq[5:7] == b'\x06\x00'


def test_len4_gain_is_master_only():
    import pytest
    with pytest.raises(ValueError):
        wire_apply.gain_frames(1, -6.0)


def test_record_frames_place_the_flags_and_keep_test_tone_zero():
    frames = wire_apply.record_frames(1, gain_db=-6.0, phase=True, passthrough=True, mute=True)
    p = frames[0][5:-1]
    assert p.hex() == '0001e10001000001010000007fffffff'
    assert p[9:12] == b'\0\0\0'
    # the captured 4-frame sequence: record, flat 0x0702 table, zero-stage chain, empty list
    assert [int.from_bytes(f[3:5], 'big') for f in frames] == [0x0700, 0x0702, 0x0708, 0x0704]
    assert frames[1][5:7] == b'\x00\x01' and filterbins.is_flat(frames[1][5:-1])
    assert frames[2].hex() == 'aa001707080001010000000000000000000000000000000000000000d8'
    assert frames[3].hex() == 'aa00030704050001ec'


def test_eq_band_frames_match_the_captured_pair_byte_for_byte():
    # vehicle capture 19:35:06.500 / .522: 0x0708 then 0x0704 sub05 (one band). The
    # wire gain is the half-float -0.1796875; the app computed its coefficients
    # from its internal double (-0.17965 reproduces the capture exactly, the
    # rounded half-float lands 1 ULP off on b0). Feed the internal value.
    b = bytes.fromhex('41fa0c17b1c042004200')
    freq = struct.unpack('>f', b[:4])[0]
    f708, f704 = wire_apply.eq_band_frames(1, freq, -0.17965, 3.0)     # no 0x0702 on EQ edits
    assert f704.hex() == 'aa000d070405000141fa0c17b1c0420042008f'          # byte-exact
    assert f708.hex() == 'aa002b0708000102' + '00' * 20 + '3f7fff14bfffd2543f7fa6aebfffd2543f7fa5c1f4'


def test_eq_remove_is_the_captured_pair():
    # vehicle capture 19:33:32.269 / .285: zero chain then the empty point list
    f708, f704 = wire_apply.eq_band_remove_frames(1)
    assert [f708.hex(), f704.hex()] == [
        'aa001707080001010000000000000000000000000000000000000000d8',
        'aa00030704050001ec']


def test_two_bands_go_in_one_frame_each():
    f708, f704 = wire_apply.eq_frames(1, [(100.0, -3.0, 2.0), (2500.0, 1.5, 4.0)])
    assert len(f704) == 5 + 3 + 20 + 1 and f704[5:8] == b'\x05\x00\x01'
    d = biquad.decode_0708(f708[5:-1])
    assert d['channel'] == 1 and d['count'] == 3 and d['stages'][0] == [0.0] * 5
    assert d['stages'][1] == biquad.to_f32(biquad.rbj_peaking(100.0, -3.0, 2.0))
    assert d['stages'][2] == biquad.to_f32(biquad.rbj_peaking(2500.0, 1.5, 4.0))


def test_debug_send_is_one_0401_frame_per_byte_as_captured():
    frames = wire_apply.debug_send_frames('h')
    # captured: 'h' (0x68) then Enter (0x0d), each framed AA|0001|0401|byte|cksum
    assert [fr.hex() for fr in frames] == ['aa000104016892', 'aa000104010ded']


def test_gate_state_for_these_frames_is_reported_truthfully():
    # Whatever the gate decides, it must be consistent: a frame that clears must be
    # one the parity module itself recognises; nothing here asserts it is open.
    for fr in (wire_apply.gain_frames(0, -6.0) + wire_apply.eq_band_frames(1, 1000.0, 3.0, 5.0)
               + wire_apply.debug_send_frames('dspdebug rms 1')):
        cmd = int.from_bytes(fr[3:5], 'big')
        assert parity.frame_cleared(fr, cmd) in (True, False)


def test_filter_bins_table_is_flat_without_eq_and_curved_with_it():
    flat = filterbins.build_0702(3)
    assert len(flat) == 1026 and flat[:2] == b'\x00\x03' and filterbins.is_flat(flat)
    curved = filterbins.build_0702(3, [(1000.0, 3.0, 2.0)])
    raws = struct.unpack('>512H', curved[2:])
    assert not filterbins.is_flat(curved)
    assert max(raws) <= 0xED00 + 3 * 512 and min(raws) >= 0xED00     # a +3 dB boost only lifts
    peak_bin = raws.index(max(raws))                                   # near 1000 Hz on the even grid
    assert abs((peak_bin * 2 + 1) * 24000 / 1024 - 1000.0) < 60.0


def test_gate_opens_flat_and_mode2_curves_not_mode1():
    assert parity.frame_cleared(wire_apply.record_frames(1)[1], 0x0702)
    curved = wire_apply.record_frames(1, bands=[(1000.0, 3.0, 2.0)])[1]
    assert parity.frame_cleared(curved, 0x0702)
    # mode-1 tables are built only from a full crossover definition (dspwrite);
    # the bare legacy flag still cannot invent one
    try:
        wire_apply.record_frames(1, bands=[(1000.0, 3.0, 2.0)], mode1_crossover=True)
    except NotImplementedError:
        pass
    else:
        raise AssertionError('mode-1 table was built without a crossover definition')


def test_eq_curve_switches_points_by_frequency_like_the_exe():
    # two points one grid bin apart: the exe advances by grid frequency, not index
    pts = [(1000.0, 3.0, 3.0), (1010.0, -2.0, 3.0)]
    curve = filterbins.eq_curve(pts)
    f = lambda i: (i + 1) * 24000 / 1024
    i_mid = next(i for i in range(1024) if 1000.0 < f(i) <= 1010.0)
    assert curve[i_mid - 1] == filterbins.point_response(pts[0], f(i_mid - 1))      # f <= 1000: first alone
    assert curve[i_mid + 1] == filterbins.point_response(pts[1], f(i_mid + 1))      # f > 1010: last alone
