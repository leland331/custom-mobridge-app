"""Biquad coefficient design + the 0x0708 coefficient frame — the math the app runs
before it writes a filter/EQ to the device.

Pinned 2026-10-22 by matching the app's captured 0x0708 coefficients against the
standard RBJ Audio-EQ-Cookbook formulas at EXACTLY fs = 48 kHz:
  * parametric EQ  = RBJ "peaking EQ"; fed freq (float32), gain dB + Q (half-floats)
    from the 0x0704 sub05 band command. All 13 captured EQ edits match to float32
    precision. There is NO frequency pre-warp (an earlier ~1.5% estimate was an
    analysis error, not device behaviour).
  * crossover LPF / HPF = RBJ 2nd-order Butterworth (Q = 1/sqrt2). All 6 captured
    versions match exactly (LPF 150/151/2000, HPF 150/2000/2001). 24 dB/oct = the
    same biquad applied twice (2 sections).
  * band (type 3) = one bandpass at band_centre(f1,f2) with the app's log2-bandwidth
    alpha (735 Hz / Q 0.2954 for 150/2000) -- rbj_band, pinned from the exe
    (FUN_1400fdcf0) 2026-10-22.
  * The exe rounds crossover coefficients to float32 BEFORE normalising and divides
    in float32; EQ stays double with one rounding at the store. That ordering is
    what makes our bytes identical, not merely within 1 ULP.

Anchor used in tests: the native audit decoded a captured 0x0708 section as
b0=9.51e-5 b1=1.90e-4 b2=9.51e-5 a1=-1.9722 a2=0.9726 -- exactly rbj_lpf(150).

Wire form (0x0708): chan·be16 | section·u8 | 5 x float32 BIG-ENDIAN [b0,b1,b2,a1,a2]
(normalised by a0, a0 omitted). This module builds/decodes bytes only; it transmits
nothing. Filter/EQ writes stay parity-gated until checked against the device.
"""
from __future__ import annotations

import math as _math
import struct as _struct

FS = 48000.0                      # device DSP rate, exact (pinned by coefficient match)
BUTTERWORTH_Q = 1.0 / _math.sqrt(2.0)   # 0.70710678: 2nd-order crossover Q
CMD_BIQUAD = 0x0708


def _f32(x: float) -> float:
    return _struct.unpack('>f', _struct.pack('>f', x))[0]


def _f32div(x: float, y: float) -> float:
    """float32 / float32 -> float32. Dividing the two float32 values in double and
    rounding once to float32 equals a native single division (binary64 carries
    more than 2p+2 bits of binary32, so the double rounding is innocuous for /)."""
    return _f32(_f32(x) / _f32(y))


def _w0(f0_hz: float):
    """w0 exactly as the app computes it: (f * 6.283185307179586) / 48000.0 in doubles."""
    w0 = (float(f0_hz) * 6.283185307179586) / FS
    return _math.cos(w0), _math.sin(w0)


def _xover_norm(b0, b1, b2, a0, a1, a2) -> list[float]:
    """Crossover normalisation as the app's FUN_1400fdcf0 does it: every
    coefficient is rounded to float32 BEFORE normalising, then divided by a0 in
    float32. (That staging is what makes the captured bytes exact.)"""
    a0 = _f32(a0)
    return [_f32div(_f32(b0), a0), _f32div(_f32(b1), a0), _f32div(_f32(b2), a0),
            _f32div(_f32(a1), a0), _f32div(_f32(a2), a0)]


def rbj_lpf(f0_hz: float, q: float = BUTTERWORTH_Q) -> list[float]:
    """2nd-order Butterworth low-pass (crossover), app-exact: alpha = sin/sqrt2,
    b1 = f32(1-cos), b0 = b2 = f32(double(b1) * 0.5)."""
    c, s = _w0(f0_hz)
    al = s / 1.4142135623730951 if q == BUTTERWORTH_Q else s / (2.0 * q)
    b1 = _f32(1.0 - c)
    b0 = b2 = _f32(float(b1) * 0.5)
    return _xover_norm(b0, b1, b2, al + 1.0, c * -2.0, 1.0 - al)


def rbj_hpf(f0_hz: float, q: float = BUTTERWORTH_Q) -> list[float]:
    """2nd-order Butterworth high-pass (crossover), app-exact: t = cos + 1,
    b1 = f32(-t), b0 = b2 = f32(t * 0.5)."""
    c, s = _w0(f0_hz)
    al = s / 1.4142135623730951 if q == BUTTERWORTH_Q else s / (2.0 * q)
    t = c + 1.0
    return _xover_norm(_f32(t * 0.5), _f32(-t), _f32(t * 0.5), al + 1.0, c * -2.0, 1.0 - al)


def band_centre(f1_hz: float, f2_hz: float) -> int:
    """The app's band-pass centre for a (lower f2, upper f1) pair:
    (int) trunc(f2 + (sqrt(10) * (f1 - f2)) / 10)  -> 735 for 150/2000."""
    return int(_math.trunc(f2_hz + (_math.sqrt(10.0) * (f1_hz - f2_hz)) / 10.0))


def rbj_band(f1_hz: float, f2_hz: float) -> list[float]:
    """Band-pass crossover (type 3) exactly as the app builds it (FUN_1400fdcf0):
    f0 = band_centre, alpha = sinh((log2(f1/f2) * (ln2/2) * w0) / sin) * sin,
    b0 = f32(alpha), b1 = 0, b2 = f32(-alpha), then float32 normalisation."""
    f0 = band_centre(f1_hz, f2_hz)
    c, s = _w0(f0)
    w0 = (float(f0) * 6.283185307179586) / FS
    al = _math.sinh((_math.log2(f1_hz / f2_hz) * (_math.log(2.0) * 0.5) * w0) / s) * s
    return _xover_norm(_f32(al), 0.0, _f32(-al), al + 1.0, c * -2.0, 1.0 - al)


def rbj_peaking_double(f0_hz: float, gain_db: float, q: float) -> list[float]:
    """Parametric EQ band as the app holds it internally (FUN_1400f7200): the
    normalised coefficients as DOUBLES. The app evaluates the 1024-point
    response (0x0702 bins) from these; only the five stored/sent 0x0708 values
    are rounded to float32 (see rbj_peaking)."""
    A = 10.0 ** (gain_db / 40.0)
    c, s = _w0(f0_hz)
    al = s / (q + q)
    aA, a_A, cs2 = al * A, al / A, c * -2.0
    a0 = a_A + 1.0
    return [(aA + 1.0) / a0, cs2 / a0, (1.0 - aA) / a0, cs2 / a0, (1.0 - a_A) / a0]


def rbj_peaking(f0_hz: float, gain_db: float, q: float) -> list[float]:
    """Parametric EQ band, app-exact (FUN_1400f7200): all double, one float32
    rounding at the store. gain_db/q must be the app's INTERNAL values (the
    0x0704 half-floats are rounded copies; feeding those back can land 1 ULP off)."""
    return [_f32(x) for x in rbj_peaking_double(f0_hz, gain_db, q)]


def sections_for_slope(slope_db_oct: int) -> int:
    """Crossover slope -> number of identical biquad sections (12 dB/oct per section)."""
    if slope_db_oct not in (12, 24):
        raise ValueError('slope %r dB/oct: only 12 (1 section) and 24 (2 sections) are captured' % slope_db_oct)
    return slope_db_oct // 12


def to_f32(coeffs: list[float]) -> list[float]:
    """Round to the float32 the device/app uses."""
    return [_struct.unpack('>f', _struct.pack('>f', x))[0] for x in coeffs]


MAX_STAGES = 16
ZERO_STAGE = [0.0] * 5            # what the app sends in the crossover slot when there is none


def build_0708(channel: int, stages: list[list[float]], preset: int = 0) -> bytes:
    """0x0708 payload: preset | chan | count | count x 5 float32 BE [b0,b1,b2,a1,a2].
    The channel's WHOLE biquad chain in one frame: crossover stages first, then
    one stage per EQ point (captured: 00 01 01 + zeros; 00 01 02 + zeros + peaking)."""
    if not 1 <= len(stages) <= MAX_STAGES:
        raise ValueError('%d biquad stages: need 1..%d' % (len(stages), MAX_STAGES))
    out = bytes([preset & 0xFF, channel & 0xFF, len(stages)])
    for c in stages:
        if len(c) != 5:
            raise ValueError('need 5 coefficients [b0,b1,b2,a1,a2]')
        out += _struct.pack('>5f', *c)
    return out


def decode_0708(payload: bytes) -> dict:
    if len(payload) < 3 or len(payload) != 3 + 20 * payload[2]:
        raise ValueError('0x0708 payload length %d does not match its stage count' % len(payload))
    n = payload[2]
    return {'preset': payload[0], 'channel': payload[1], 'count': n,
            'stages': [list(_struct.unpack('>5f', payload[3 + 20 * i:23 + 20 * i])) for i in range(n)]}


def crossover_stages(kind: str, f0_hz: float, slope_db_oct: int,
                     f2_hz: float | None = None, slope2_db_oct: int | None = None) -> list[list[float]]:
    """Crossover stages: the one biquad repeated slope/12 times (type 1 LPF uses
    slope1, type 2 HPF slope2, type 3 band max(slope1, slope2)/12).
    kind = 'lpf' | 'hpf' | 'band' (band: f0_hz = upper f1, f2_hz = lower f2)."""
    if kind == 'band':
        c = rbj_band(f0_hz, f2_hz)
        n = sections_for_slope(max(slope_db_oct, slope2_db_oct or slope_db_oct))
    else:
        c = {'lpf': rbj_lpf, 'hpf': rbj_hpf}[kind](f0_hz)
        n = sections_for_slope(slope_db_oct)
    return [c] * n


def chain_stages(bands, crossover: list[list[float]] | None = None) -> list[list[float]]:
    """Crossover stages (or one zero stage when there is none), then one RBJ
    peaking stage per EQ point. Each band is (freq, gain, q[, q_above])."""
    stages = list(crossover) if crossover else [ZERO_STAGE]
    stages += [rbj_peaking(b[0], b[1], b[2]) for b in bands]
    return stages
