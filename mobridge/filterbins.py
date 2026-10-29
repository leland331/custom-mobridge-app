"""0x0702 filter-bins table: the 1026-byte frame the official app sends after every
0x0700 write (channel gain -> that channel's table; master gain -> channel 0).

Pinned 2026-10-22 from the exe (MODEL_REGION.md §2.3 / filtermath.txt) and the
vehicle capture; firmware side is mdimod_dsp_rx_filterbins (2.11.8 @0x51689c,
requires LEN 0x402). This module builds bytes only; parity.proven_write decides
what may be sent (today: the all-flat table only, which is what every capture
shows for a channel without EQ points).

Sent after GAIN writes only (0x0700 LEN4/LEN16); an EQ edit sends 0x0708 + 0x0704
sub05 without it (vehicle capture: all 19 tables follow gain writes, none follow EQ
edits). Wire: [preset][chan] + 512 x u16 BE. The app evaluates a 1024-point dB curve on the
linear grid f(i) = i * 24000 / 1024, i = 1..1024, clamps it to [-27, +9] dB and
encodes round(0xED00 + dB * 512) (0xE700 on DSPNV <= 207); for fw >= 207 only the
EVEN bins (0, 2, ..., 1022) are sent. Curve = EQ curve + crossover curve:
  * EQ curve: with the channel's points sorted by frequency and idx(f) =
    round(f / 24000 * 1024), bin i between points P (idx p) and C (idx c) is the
    CROSSFADE v = C(i)*t + P(i)*(1-t), t = ((i+1) - p) / (c - p) (p == c: the mean);
    the point cursor advances by FREQUENCY, at most once per bin (eq_curve);
    outside the range the nearest point's own response. Each point's response is
    its peaking biquad magnitude in dB from the app's DOUBLE coefficients
    (float32 only on the 0x0708 wire), Q-below set for f < f0, Q-above for f >= f0.
  * crossover curve: non-zero only for mode-1 (tweeter/mid) channels, where it is
    the DSPFilters response. For woofer/sub channels it is zero. Mode-1 curves are
    NOT built here (not pinned) -> such a table cannot be produced.
No EQ points + no mode-1 crossover => every bin 0xED00 (matches every capture).
"""
from __future__ import annotations

import math as _math
import struct as _struct

from . import biquad

CMD_FILTER_BINS = 0x0702
BINS = 1024
BINS_SENT = 512
FLAT_RAW = 0xED00
UNITY_RAW_LEGACY = 0xE700
GRID_HZ = 24000.0
PAYLOAD_LEN = 2 + 2 * BINS_SENT          # 1026 = the firmware's required LEN 0x402


def _mag_db(c: list[float], f_hz: float) -> float:
    """20*log10|H| of a normalised biquad [b0,b1,b2,a1,a2] at f_hz (doubles)."""
    b0, b1, b2, a1, a2 = c
    w = 2.0 * _math.pi * f_hz / biquad.FS
    cw, c2w = _math.cos(w), _math.cos(2.0 * w)
    num = b0 * b0 + b1 * b1 + b2 * b2 + 2.0 * (b0 * b1 + b1 * b2) * cw + 2.0 * b0 * b2 * c2w
    den = 1.0 + a1 * a1 + a2 * a2 + 2.0 * (a1 + a1 * a2) * cw + 2.0 * a2 * c2w
    # IEEE semantics like the exe's doubles: x/0 -> inf, 0/0 -> nan, log10(0) -> -inf.
    # encode_curve maps nan -> -128 and clamps +-128 before the dB clamp.
    if den == 0.0:
        return _math.nan if num == 0.0 else _math.inf
    r = num / den
    if r <= 0.0:
        return _math.nan if r < 0.0 else -_math.inf
    return 20.0 * _math.log10(_math.sqrt(r))


def point_response(point, f_hz: float) -> float:
    """dB response of one EQ point (freq, gain, q_below[, q_above]) at f_hz,
    evaluated from the app's DOUBLE coefficients (the float32 rounding applies
    only to the 0x0708 payload, not to the bins)."""
    f0, g = point[0], point[1]
    q = point[2] if f_hz < f0 else (point[3] if len(point) > 3 else point[2])
    return _mag_db(biquad.rbj_peaking_double(f0, g, q), f_hz)


def eq_curve(points) -> list[float]:
    """1024-point dB curve (index i -> f = (i+1) * 24000 / 1024) for the EQ list.
    The exe walks the frequency-sorted points with one cursor: before each bin
    it advances the cursor ONCE if the cursor point's freq is below the grid f
    (so points sharing a frequency advance one per successive bin). Bins before
    the first point get the first point's own response, bins after the cursor
    runs out get the last point's own response, and in between the value is the
    crossfade v = C(i)*t + P(i)*(1-t) with t = ((i+1) - p) / (c - p) from the
    points' ROUNDED grid indices (p == c -> plain mean)."""
    pts = sorted(points, key=lambda p: p[0])
    n = len(pts)
    if n == 0:
        return [0.0] * BINS
    idx = [int(round(p[0] / GRID_HZ * BINS)) for p in pts]
    out = []
    j = 0                                   # cursor = index of the "current" point
    for i in range(BINS):
        f = (i + 1) * GRID_HZ / BINS
        if j < n and pts[j][0] < f:
            j += 1
        if j == 0:
            v = point_response(pts[0], f)
        elif j >= n:
            v = point_response(pts[n - 1], f)
        else:
            P, C = pts[j - 1], pts[j]
            p, c = idx[j - 1], idx[j]
            if p == c:
                v = (point_response(P, f) + point_response(C, f)) * 0.5
            else:
                t = ((i + 1) - p) / (c - p)
                v = point_response(C, f) * t + point_response(P, f) * (1.0 - t)
        out.append(v)
    return out


def encode_curve(curve_db: list[float], unity_raw: int = FLAT_RAW) -> list[int]:
    raws = []
    for v in curve_db:
        if v != v:                       # NaN
            v = -128.0
        v = max(-128.0, min(128.0, v))
        v = max(-27.0, min(9.0, v))
        raws.append(int(round(unity_raw + v * 512.0)))
    return raws


def build_0702(channel: int, points=(), preset: int = 0, mode1_crossover=False,
               unity_raw: int = FLAT_RAW, legacy: bool = False) -> bytes:
    """The 1026-byte payload. mode1_crossover: False for woofer/sub (mode 2) and
    for mode-1 channels without a filter; for a mode-1 channel WITH a filter pass
    (ftype, design, f1, f2, slope1, slope2) and the DSPFilters response curve
    (crossover_curve.py, decompile-only, 720/720 against the exe thread's
    reference, NO capture yet) is summed in per FUN_14010f020."""
    curve = eq_curve(points)
    if mode1_crossover is True:
        raise NotImplementedError('mode-1 (tweeter/mid) crossover curve needs (ftype, design, f1, f2, s1, s2)')
    if mode1_crossover:
        from . import crossover_curve as _xc
        curve = _xc.combine(curve, _xc.crossover_curve(*mode1_crossover))
    raws = encode_curve(curve, unity_raw)
    if legacy:                            # fw < 207: all 1024 bins, hi byte (DSP_FIELD_MEANINGS 15.2)
        return bytes([preset & 0xFF, channel & 0xFF]) + bytes((r >> 8) & 0xFF for r in raws)
    sent = raws[0:BINS:2]                 # even bins for fw >= 207
    return bytes([preset & 0xFF, channel & 0xFF]) + _struct.pack('>%dH' % BINS_SENT, *sent)


def is_flat(payload: bytes, unity_raw: int = FLAT_RAW) -> bool:
    return (len(payload) == PAYLOAD_LEN
            and all(r == unity_raw for r in _struct.unpack('>%dH' % BINS_SENT, payload[2:])))
