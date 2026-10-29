"""0x0704 EQ-band wire format (sub-op 0x05), from the 2026-10-22 decode.

The official app writes a parametric EQ band as:
    0x0704 payload = 05 | channel·be16 | freq·f32-BE | gain·half-BE | Q·half-BE | Q·half-BE
(the Q value is written twice). The 2026-10-22 capture verified gain and Q
against the coefficients the app put in the paired 0x0708 biquad, so the gain and
Q encodings are proven. Frequency is NOT fully proven to WRITE yet: the cutoff the
captured coefficients imply is ~1.5% below the float32 the app sent, i.e. the app
pre-warps the frequency (or the DSP rate isn't exactly 48 kHz). Until that is
pinned against the app/firmware math, an EQ frequency write would be ~1.5% off, so
it stays gated.

This module only BUILDS/decodes the bytes; it transmits nothing. No byte-anchored
reference frame is added here (the full captured EQ frame lives in the laptop
decode), so the parity gate keeps every 0x0704 EQ write blocked regardless.
"""
from __future__ import annotations

import struct as _struct

CMD_FILTER = 0x0704
EQ_SUBOP = 0x05
MASTER_SUBOP = 0x06      # MASTERPOINTS: 06 | preset | N x point
MAX_POINTS = 16
EQ_REMOVE = bytes.fromhex('050001')   # LEN3 payload that clears the band (captured)


def _point(band) -> bytes:
    """One 10-byte point: freq f32 | gain half | Q-below half | Q-above half.
    `band` is (freq, gain, q) or (freq, gain, q_below, q_above)."""
    freq_hz, gain_db, q1 = band[0], band[1], band[2]
    q2 = band[3] if len(band) > 3 else q1
    return (_struct.pack('>f', float(freq_hz)) + _struct.pack('>e', float(gain_db))
            + _struct.pack('>e', float(q1)) + _struct.pack('>e', float(q2)))


def build_eq_points(channel: int, bands, preset: int = 0) -> bytes:
    """0x0704 sub05 payload: the channel's WHOLE EQ point list (firmware handler
    "CHANPOINTS"): 05 | preset | chan | N x point. Each frame REPLACES the list, so
    every band goes in one frame. N = 0 clears it (the captured 05 00 01)."""
    bands = list(bands)
    if len(bands) > MAX_POINTS:
        raise ValueError('%d EQ points: at most %d' % (len(bands), MAX_POINTS))
    return bytes([EQ_SUBOP, preset & 0xFF, channel & 0xFF]) + b''.join(_point(b) for b in bands)


def build_master_points(bands=(), preset: int = 0) -> bytes:
    """0x0704 sub06 payload: the master EQ list. Empty = the captured 06 00."""
    bands = list(bands)
    if len(bands) > MAX_POINTS:
        raise ValueError('%d EQ points: at most %d' % (len(bands), MAX_POINTS))
    return bytes([MASTER_SUBOP, preset & 0xFF]) + b''.join(_point(b) for b in bands)


def build_eq_band(channel: int, freq_hz: float, gain_db: float, q: float) -> bytes:
    """A one-band list (N = 1). Only correct when the channel has exactly one band."""
    return build_eq_points(channel, [(freq_hz, gain_db, q)])


def decode_eq_band(payload: bytes) -> dict | None:
    """Decode a 0x0704 EQ-band payload. Returns None for the LEN3 'remove' form."""
    if len(payload) <= 3:
        return None
    if payload[0] != EQ_SUBOP:
        raise ValueError('not an EQ-band (sub-op 0x05) payload')
    channel = int.from_bytes(payload[1:3], 'big')
    freq = _struct.unpack('>f', payload[3:7])[0]
    gain = _struct.unpack('>e', payload[7:9])[0]
    q = _struct.unpack('>e', payload[9:11])[0]
    return {'channel': channel, 'freq_hz': freq, 'gain_db': gain, 'q': q}
