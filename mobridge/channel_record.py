"""Big-endian 0x0700 channel-property record builder — the WIRE serializer.

This is the piece GATE_STATUS.md called the missing blocker: a send-ready wire
builder whose byte order matches the device. The official app writes a channel's
properties as ONE 0x0700 record (read-modify-write of the whole channel), not as
per-field commands. The 2026-10-22 vehicle capture pinned the layout byte-exact
against five independent records (gain, delay, limiter, and a flag set/undo on
ch11):

  LEN16 full record (payload[0..15], all multi-byte fields BIG-ENDIAN):
    [0]     preset         u8   (the reference preset 1 == index 0x00)
    [1]     channel        u8
    [2-3]   gain           s16  raw = round(dB*512) + unity(0xED00 @ DSPNV>207)
    [4]     reserved       u8   (00 in every capture)
    [5-6]   delay          u16  microseconds (5.00 ms -> 0x1388)   <- NOT [6-7]
    [7]     reserved       u8   (00 in every capture)
    [8]     flag           u8   (toggled in the ch11 + mute captures)
    [9]     reserved       u8   (00 in every capture)
    [10-11] reserved       u16  (0000 in every capture)
    [12-15] limiter        s32  0x7FFFFFFF = OFF/max; raw = round(2^31 * 10^(dBFS/20))

  LEN4 gain-only short form (payload[0..3]):
    [0] preset  [1] channel  [2-3] gain s16-BE
  The app emits the short form when only the gain changed.

PARITY: this builder is proven by reproducing the captured frames byte-exact
(tests/test_channel_record.py). It is wire-encoding only; it transmits nothing
on its own. The send path still goes through parity.frame_cleared, which clears
a 0x0700 write only when the exact frame matches a captured reference. Opening
0x0700 to arbitrary (proven-formula) values is a separate, deliberate step that
also needs: the three flag bytes resolved (which of [4]/[7]/[8]/[9] is
mute/phase/passthrough — needs the per-flag frames from the laptop decode), and
the user's go-ahead, since writes persist permanently.

The flag byte positions are therefore NOT assigned here. build_record() takes an
explicit {offset: value} map for any non-zero flag, and refuses an offset that
isn't one of the capture-observed reserved/flag slots, so a caller cannot invent
a flag placement.
"""
from __future__ import annotations

import math as _math
import struct as _struct

CMD_CHANNEL_PROPERTY = 0x0700

# Gain scale — identical to encoders._gain_* but emitted BIG-ENDIAN for the wire.
_GAIN_UNITY_NEW = 0xED00   # DSPNV settings version > 207 (reference unit = 210)
_GAIN_UNITY_OLD = 0xE700   # version <= 207
_GAIN_RAW_MIN = 0xB700     # -27.0 dB
_GAIN_RAW_MAX = 0xFF00     #  +9.0 dB
_LIM_FS = float(2 ** 31)
_LIMITER_OFF = 0x7FFFFFFF   # 0 dBFS, limiter disabled / maximum

# Byte slots observed as 00 (reserved) or toggled (flag) in the capture. A flag
# write MUST target one of these; gain/delay/limiter own the others.
_RESERVED_OR_FLAG_OFFSETS = frozenset({4, 7, 8, 9, 10, 11})
# The only slot ever seen toggled by the official app so far.
PROVEN_FLAG_OFFSET = 8
# Flag positions pinned by the exe decompile + vehicle capture (2026-10-22):
# [4] phase, [7] passthrough, [8] mute; [9-11] are the test-tone bytes (kept 0).
PHASE_OFFSET, PASSTHRU_OFFSET, MUTE_OFFSET = 4, 7, 8
MASTER_CHANNEL = 0


def gain_raw(display_db: float, unity: int = _GAIN_UNITY_NEW) -> int:
    raw = int(round(display_db * 512.0)) + unity
    return max(_GAIN_RAW_MIN, min(_GAIN_RAW_MAX, raw))


def delay_raw(ms: float) -> int:
    us = int(round(ms * 1000.0))
    if not 0 <= us <= 0xFFFF:
        raise ValueError('delay %.3f ms out of u16 microsecond range' % ms)
    return us


def _f32(x: float) -> float:
    """Round to IEEE single precision, as the device's float math does."""
    return _struct.unpack('f', _struct.pack('f', x))[0]


def limiter_raw(dbfs: float | None) -> int:
    """dBFS -> s32 Q31. None or >= 0 dBFS => OFF (0x7FFFFFFF).

    The official app stores the linear limiter amplitude as a float32, then
    scales it to Q31: raw = round(float32(10^(dBFS/20)) * 2^31). Doing the
    10^(x/20) in float32 is what reproduces the captured -20.0 dBFS word
    (0x0CCCCCD0) exactly -- a double-precision round comes out 3 LSB low."""
    if dbfs is None or dbfs >= 0.0:
        return _LIMITER_OFF
    raw = int(round(_f32(10.0 ** (dbfs / 20.0)) * _LIM_FS))
    return max(0, min(_LIMITER_OFF, raw))


def build_record(channel: int, *, preset: int = 0,
                 gain_db: float = 0.0, delay_ms: float = 0.0,
                 limiter_dbfs: float | None = None,
                 flags: dict[int, int] | None = None,
                 unity: int = _GAIN_UNITY_NEW) -> bytes:
    """Build the LEN16 channel-property payload (big-endian), the full
    read-modify-write record. `flags` maps a byte offset (one of the
    capture-observed reserved/flag slots) to its u8 value; any offset owned by a
    scalar field, or outside the record, is refused."""
    if not 0 <= channel <= 0xFF:
        raise ValueError('channel out of range')
    buf = bytearray(16)
    buf[0] = preset & 0xFF
    buf[1] = channel & 0xFF
    buf[2:4] = gain_raw(gain_db, unity).to_bytes(2, 'big')
    buf[5:7] = delay_raw(delay_ms).to_bytes(2, 'big')
    buf[12:16] = limiter_raw(limiter_dbfs).to_bytes(4, 'big')
    for off, val in (flags or {}).items():
        if off not in _RESERVED_OR_FLAG_OFFSETS:
            raise ValueError('byte %d is not a flag/reserved slot (owned by a '
                             'scalar field or outside the record)' % off)
        buf[off] = val & 0xFF
    return bytes(buf)


def build_gain_only(channel: int, gain_db: float, *, preset: int = 0,
                    unity: int = _GAIN_UNITY_NEW) -> bytes:
    """The LEN4 short form the app uses when only the gain changed."""
    return bytes([preset & 0xFF, channel & 0xFF]) + gain_raw(gain_db, unity).to_bytes(2, 'big')


# ---- decode (read-back / corroboration) ----------------------------------

def decode_record(payload: bytes, unity: int = _GAIN_UNITY_NEW) -> dict:
    """Decode a LEN16 (or LEN4 gain-only) 0x0700 payload to human values."""
    if len(payload) == 4:
        gain = int.from_bytes(payload[2:4], 'big')
        return {'preset': payload[0], 'channel': payload[1],
                'gain_db': (gain - unity) / 512.0}
    if len(payload) != 16:
        raise ValueError('expected 4 or 16-byte 0x0700 payload, got %d' % len(payload))
    gain = int.from_bytes(payload[2:4], 'big')
    us = int.from_bytes(payload[5:7], 'big')
    lim = int.from_bytes(payload[12:16], 'big')
    out = {
        'preset': payload[0], 'channel': payload[1],
        'gain_db': (gain - unity) / 512.0,
        'delay_ms': us / 1000.0,
        'flag8': payload[8],
        'reserved': {o: payload[o] for o in sorted(_RESERVED_OR_FLAG_OFFSETS) if payload[o]},
    }
    out['limiter_dbfs'] = (None if lim >= _LIMITER_OFF
                           else (20.0 * _math.log10(lim / _LIM_FS) if lim > 0 else float('-inf')))
    return out
