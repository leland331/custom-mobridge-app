"""Biquad design math pinned by the 2026-10-22 capture: RBJ cookbook at exactly 48 kHz,
no frequency pre-warp. The LPF anchor is a REAL captured 0x0708 section decoded in the
native audit (b0=9.51e-5 b1=1.90e-4 b2=9.51e-5 a1=-1.9722 a2=0.9726)."""
import math
import struct

from mobridge import biquad


def test_butterworth_lpf_150_matches_captured_section():
    # the audit's decoded values are 3-4 significant figures; match to that precision
    b0, b1, b2, a1, a2 = biquad.rbj_lpf(150.0)
    assert abs(b0 - 9.51e-5) < 1e-7
    assert abs(b1 - 1.90e-4) < 1e-6
    assert abs(b2 - 9.51e-5) < 1e-7
    assert abs(a1 - (-1.9722)) < 1e-4
    assert abs(a2 - 0.9726) < 1e-4
    assert biquad.FS == 48000.0 and abs(biquad.BUTTERWORTH_Q - 1 / math.sqrt(2)) < 1e-12


def _mag_db(coeffs, f_hz):
    """|H(e^jw)| in dB for a normalised biquad [b0,b1,b2,a1,a2]."""
    b0, b1, b2, a1, a2 = coeffs
    w = 2 * math.pi * f_hz / biquad.FS
    z1 = complex(math.cos(-w), math.sin(-w))
    z2 = z1 * z1
    h = (b0 + b1 * z1 + b2 * z2) / (1 + a1 * z1 + a2 * z2)
    return 20 * math.log10(abs(h))


def test_crossovers_are_minus_3db_at_cutoff_and_symmetric():
    # Butterworth: -3.01 dB at f0; HPF mirrors LPF
    assert abs(_mag_db(biquad.rbj_lpf(2000.0), 2000.0) - (-3.01)) < 0.02
    assert abs(_mag_db(biquad.rbj_hpf(150.0), 150.0) - (-3.01)) < 0.02
    assert _mag_db(biquad.rbj_lpf(150.0), 20.0) > -0.1        # passband flat
    assert _mag_db(biquad.rbj_hpf(2000.0), 20000.0) > -0.1


def test_peaking_eq_hits_requested_gain_at_f0_and_is_flat_far_away():
    c = biquad.rbj_peaking(1000.0, 3.0, 5.0)
    assert abs(_mag_db(c, 1000.0) - 3.0) < 1e-6
    assert abs(_mag_db(c, 20.0)) < 0.01
    assert abs(_mag_db(c, 20000.0)) < 0.01
    # a negative-gain band dips by the same amount
    assert abs(_mag_db(biquad.rbj_peaking(50.7, -8.66, 4.0), 50.7) - (-8.66)) < 1e-4   # float32-stored coefficients


def test_24db_slope_is_two_identical_sections_12db_is_one():
    s12 = biquad.crossover_stages('hpf', 100.0, 12)
    s24 = biquad.crossover_stages('hpf', 100.0, 24)
    assert len(s12) == 1 and len(s24) == 2
    assert s24[0] == s24[1] == s12[0]                   # same biquad applied twice
    d = biquad.decode_0708(biquad.build_0708(1, s24))
    assert d['count'] == 2 and d['stages'][0] == d['stages'][1]


def test_0708_wire_layout_is_preset_chan_count_then_float32_be():
    c = biquad.rbj_lpf(150.0)
    p = biquad.build_0708(4, [c])
    assert len(p) == 23
    assert p[0:3] == b'\x00\x04\x01'
    assert list(struct.unpack('>5f', p[3:])) == biquad.to_f32(c)
    assert biquad.decode_0708(p)['channel'] == 4
    # the captured empty chain: ch1, one all-zero stage
    assert biquad.build_0708(1, [biquad.ZERO_STAGE]).hex() == '000101' + '00' * 20


def test_band_type_matches_the_exe_derivation():
    # exe FUN_1400fdcf0: 150/2000 -> centre 735 Hz, log2-bandwidth alpha -> Q 0.2954
    import math
    assert biquad.band_centre(2000.0, 150.0) == 735
    b0, b1, b2, a1, a2 = biquad.rbj_band(2000.0, 150.0)
    assert b1 == 0.0 and b2 == -b0
    w0 = 2 * math.pi * 735 / 48000
    alpha = b0 / (1 - b0)                      # b0 = alpha / (1 + alpha)
    assert abs(math.sin(w0) / (2 * alpha) - 0.2954) < 2e-4
    assert len(biquad.crossover_stages('band', 2000.0, 12, 150.0, 24)) == 2


def test_crossover_coefficients_are_float32_staged_like_the_exe():
    # every coefficient is a float32 value (rounded before AND after normalising)
    for c in (biquad.rbj_lpf(150.0), biquad.rbj_hpf(2000.0), biquad.rbj_band(2000.0, 150.0)):
        assert c == biquad.to_f32(c)
