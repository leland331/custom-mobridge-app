"""0x0704 EQ-band wire format (gain/Q proven vs the 0x0708 coefficients 2026-10-22)."""
import struct
from mobridge import eq


def test_eq_band_layout_and_halffloat_gain_q():
    # 05 | chan be16 | freq f32 BE | gain half BE | Q half BE | Q half BE
    p = eq.build_eq_band(1, 1000.0, 3.0, 5.0)
    assert len(p) == 13
    assert p[0] == 0x05 and p[1:3] == b'\x00\x01'
    assert struct.unpack('>f', p[3:7])[0] == 1000.0
    assert p[7:9] == bytes.fromhex('4200')     # +3.0 dB half-float
    assert p[9:11] == p[11:13]                 # Q written twice
    assert struct.unpack('>e', p[9:11])[0] == 5.0


def test_eq_band_roundtrip():
    p = eq.build_eq_band(4, 50.7, -8.66, 4.0)
    d = eq.decode_eq_band(p)
    assert d['channel'] == 4
    assert abs(d['freq_hz'] - 50.7) < 0.01
    assert abs(d['gain_db'] - (-8.66)) < 0.02     # half-float precision
    assert d['q'] == 4.0


def test_remove_form_decodes_to_none():
    assert eq.decode_eq_band(eq.EQ_REMOVE) is None
