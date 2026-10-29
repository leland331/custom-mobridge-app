"""Value encoders: dB -> raw gain, Hz -> filter coefficients, Q, slope codes,
delay/time-align units, etc. These convert what the user types in the GUI into
the exact bytes the device expects.

PARITY RULE: every encoder here starts UNCONFIRMED and raises. The official app
computes these client-side (biquad/crossover coefficients, scaling/quantization);
until each formula is recovered from the decompiled encoder AND byte-matched
against a capture, we must NOT invent one. A GUI control bound to an unconfirmed
encoder is shown BLOCKED (disabled) rather than sending a guess.

When the laptop thread extracts an encoder (e.g. DSPNV_WriteParam scaling), add it
here with a reference vector in reference_frames.json / tests, flip `confirmed`,
and the bound control unlocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class EncodingUnconfirmed(NotImplementedError):
    pass


@dataclass
class ValueEncoder:
    name: str
    unit: str
    # UI constraints come from the decompiled screen constructor; None until known.
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    decimals: int | None = None
    confirmed: bool = False
    _fn: Callable[[float], bytes] | None = field(default=None, repr=False)
    _decode_fn: Callable[[bytes], float] | None = field(default=None, repr=False)
    # A candidate formula may be wired while confirmed is still False: it is used
    # ONLY for offline corroboration against stored config, never to send. The
    # send path (encode) still refuses until `confirmed` is flipped.
    candidate: bool = False
    note: str = 'encoding not yet recovered from the decompile'

    def decode(self, raw: bytes) -> float:
        if self._decode_fn is None or not (self.confirmed or self.candidate):
            raise EncodingUnconfirmed('no decoder for %s (%s)' % (self.name, self.note))
        return self._decode_fn(raw)

    def encode_candidate(self, value: float) -> bytes:
        """Encode with the candidate formula for corroboration only. NOT gated by
        `confirmed`; callers must never put the result on the wire."""
        if self._fn is None or not (self.confirmed or self.candidate):
            raise EncodingUnconfirmed('no candidate encoder for %s' % self.name)
        return self._fn(value)

    def encode(self, value: float) -> bytes:
        if not self.confirmed or self._fn is None:
            raise EncodingUnconfirmed(
                'BLOCKED: %s encoding is unconfirmed (%s). Custom Mobridge will not send a guessed '
                'value to the device.' % (self.name, self.note))
        if self.minimum is not None and value < self.minimum:
            raise ValueError('%s %s below minimum %s' % (value, self.unit, self.minimum))
        if self.maximum is not None and value > self.maximum:
            raise ValueError('%s %s above maximum %s' % (value, self.unit, self.maximum))
        return self._fn(value)


import struct as _struct
import math as _math


# ---------------------------------------------------------------------------
# All formulas below are CODE-verified in the laptop app decompile (native
# decoder FUN_1400f8c50 + .rdata) and are wired as DISPLAY/decode only. Every
# encoder here stays confirmed=False (candidate=True): decode()/encode_candidate()
# work for reading the device back and offline corroboration, but encode() still
# refuses, and the frame-level gate (parity.frame_cleared) blocks any 0x0700/DSP
# write that is not a byte-exact captured frame. So controls show the right
# numbers but cannot send a value. Only channel GAIN is additionally [DEV]-anchored
# by the passive capture; the rest are [CODE] from the decoder, [ASSUMED] on exact
# wire field width/offset until the live serializer is extracted.
# ---------------------------------------------------------------------------

# GAIN / EQ-magnitude. display_dB = (raw - unity)/512. The unity reference is
# gated on the DSPNV SETTINGS-FILE version (decoder FUN_1400f8c50 compares to
# 0xcf = 207): version > 207 uses 0xED00 (-4864); version <= 207 uses 0xE700
# (-6400). This is the DSPNV/NVM settings-file version, NOT the app version
# string ("2.11.08"). the reference unit = DSPNV_VERS 210 (from the .fif preamble
# {"DSPNV_VERS":210,...} and his NVM header), 210 > 207 -> 0xED00, consistent with
# his sub decoding -3.0 dB at raw 0xE700. Default to the reference unit (0xED00).
_GAIN_UNITY_NEW = -4864   # 0xED00, DSPNV ver > 207 -> +9.5 dB display offset
_GAIN_UNITY_OLD = -6400   # 0xE700, DSPNV ver <= 207 -> +12.5 dB display offset
_gain_unity = _GAIN_UNITY_NEW
_GAIN_RAW_MIN = -18688   # 0xB700 -> -27.0 dB (reference fw window; attenuate-only)
_GAIN_RAW_MAX = -256     # 0xFF00 ->  +9.0 dB


GAIN_UNITY_VERSION_THRESHOLD = 207   # 0xCF; decoder: unity = (version > 0xCF) ? 0xED00 : 0xE700


def set_gain_unity_for_fw(dspnv_version: int) -> None:
    """Switch the gain/EQ-magnitude unity reference by the DSPNV settings version.

    CONFIRMED (laptop thread, native decoder FUN_1400f8c50): the compared value is
    the DspModel "version" field @ app-model offset 0x1c068 (getter FUN_1400e8670) —
    the DSPNV/settings version that both preset writers serialize as the preset
    `version` attribute. It is NOT the firmware/app version ("2.11.08") and NOT the
    product id (0x202c). Gate: version > 207 -> 0xED00, <= 207 -> 0xE700. the project's
    unit = 210 -> 0xED00. The engine reads this at connect and calls us; default
    stays the reference unit (0xED00) until a version is seen.

    Only affects DISPLAY/decode; sending stays gated regardless.

    DO NOT CONFLATE with the .rax legacy gain offset: a separate -3.0 dB offset
    lives ONLY in the .rax FILE parser, gated by (model_version>207 AND
    file_version<208), and fires ONLY when importing an OLD (pre-v208) preset file.
    It is never part of the live/device path -- see stored_config.rax_legacy_gain_offset."""
    global _gain_unity
    _gain_unity = _GAIN_UNITY_NEW if dspnv_version > GAIN_UNITY_VERSION_THRESHOLD else _GAIN_UNITY_OLD


def _gain_encode(display_db: float) -> bytes:
    # Integer-baseline (no literal 9.5 float): raw counts from the unity reference.
    raw = int(round(display_db * 512.0)) + _gain_unity
    raw = max(_GAIN_RAW_MIN, min(_GAIN_RAW_MAX, raw))
    return _struct.pack('<h', raw)   # NVM little-endian form (corroboration)


def _gain_decode(raw: bytes) -> float:
    return (_struct.unpack('<h', raw)[0] - _gain_unity) / 512.0


# EQ-POINT MAGNITUDE: NOT the gain's 512-units/dB scheme. The 2026-10-22 capture
# showed the 0x0704 EQ band carries the gain as an IEEE half-float of the dB value
# directly (0x4200=+3.0, 0x4300=+3.5, 0x4400=+4.0, 0x4500=+5.0 dB), big-endian.
# [DEV] from 4 captured values. Range -24..+12 dB step 0.5 [CODE].
def _eqmag_encode(db: float) -> bytes:
    return _struct.pack('>e', db)      # IEEE half-float dB, big-endian on the wire
def _eqmag_decode(raw: bytes) -> float:
    return float(_struct.unpack('>e', raw[:2])[0])


# FREQUENCY: integer Hz, direct. Wire read as unsigned big-endian (width per the
# captured payloads); decode tolerates any width. [CODE] semantics, [ASSUMED] field.
def _hz_encode(hz: float) -> bytes:
    return int(round(hz)).to_bytes(2, 'big')
def _hz_decode(raw: bytes) -> float:
    return float(int.from_bytes(raw, 'big'))


# SLOPE: dB/octave, direct integer (= filter order x 6, max 48). [CODE] semantics.
def _slope_encode(dboct: float) -> bytes:
    return int(round(dboct)).to_bytes(2, 'big')
def _slope_decode(raw: bytes) -> float:
    return float(int.from_bytes(raw, 'big'))


# DELAY (time alignment): wire = u16 microseconds; display ms = us/1000 (0-65 ms).
def _delay_encode(ms: float) -> bytes:
    return int(round(ms * 1000.0)).to_bytes(2, 'big')
def _delay_decode(raw: bytes) -> float:
    return int.from_bytes(raw, 'big') / 1000.0


# LIMITER: wire = signed i32; threshold dBFS = 20*log10(raw / 2**31).
# 0x7FFFFFFF = 0 dBFS = OFF/max. (2**31 normalize + log10 are code-verified; x20
# is the standard dB factor.) Returns 0.0 dBFS at full scale, -inf guarded.
_LIM_FS = float(2 ** 31)
def _limiter_encode(dbfs: float) -> bytes:
    raw = int(round(_LIM_FS * (10.0 ** (dbfs / 20.0))))
    raw = max(0, min(0x7FFFFFFF, raw))
    return _struct.pack('>i', raw)
def _limiter_decode(raw: bytes) -> float:
    v = _struct.unpack('>i', raw)[0]
    if v <= 0:
        return float('-inf')
    return 20.0 * _math.log10(v / _LIM_FS)


# FLAGS: single-byte booleans, 1 = on. invertPhase / passthrough / mute are three
# SEPARATE bytes (NVM 0x46a / 0x46e bit0 / 0x46f). [CODE] they are distinct bools;
# which one the captured 0x0700 index-8 toggle was is still SOFT (see note).
def _bool_encode(on: float) -> bytes:
    return b'\x01' if on else b'\x00'
def _bool_decode(raw: bytes) -> float:
    return 1.0 if (raw and raw[0]) else 0.0


# The catalogue of values the DSP screens need. Verified scales are candidate
# (display-only, SEND gated); genuinely unknown ones stay fully unconfirmed.
ENCODERS: dict[str, ValueEncoder] = {
    'gain_db':      ValueEncoder('gain_db', 'dB', minimum=-27.0, maximum=9.0, step=0.1,
                                 decimals=1, confirmed=False, candidate=True,
                                 _fn=_gain_encode, _decode_fn=_gain_decode,
                                 note='display dB = (raw-unity)/512, unity 0xED00=0.0dB (fw>v207); CODE+DEVICE+CAPTURE verified; SEND gated pending 0x0700 record builder'),
    'eq_gain':      ValueEncoder('eq_gain', 'dB', minimum=-24.0, maximum=12.0, step=0.5,
                                 decimals=1, confirmed=False, candidate=True,
                                 _fn=_eqmag_encode, _decode_fn=_eqmag_decode,
                                 note='EQ-point magnitude: IEEE half-float dB BE on 0x0704 (DEV: 0x4200=+3.0..0x4500=+5.0), NOT the gain 512/dB scheme; SEND gated'),
    'eq_freq':      ValueEncoder('eq_freq', 'Hz', minimum=20.0, maximum=24000.0, step=10.0,
                                 confirmed=False, candidate=True,
                                 _fn=_hz_encode, _decode_fn=_hz_decode,
                                 note='integer Hz direct (CODE); exact wire field width ASSUMED u16-BE; SEND gated'),
    'xover_freq':   ValueEncoder('xover_freq', 'Hz', minimum=20.0, maximum=23080.0, step=1.0,
                                 confirmed=False, candidate=True,
                                 _fn=_hz_encode, _decode_fn=_hz_decode,
                                 note='crossover 3dB cutoff, integer Hz direct (CODE 0x14..0x5a28); SEND gated'),
    'xover_slope':  ValueEncoder('xover_slope', 'dB/oct', minimum=6.0, maximum=48.0, step=6.0,
                                 confirmed=False, candidate=True,
                                 _fn=_slope_encode, _decode_fn=_slope_decode,
                                 note='dB/octave direct (= order x6, CODE max 0x30); value-vs-order wire form ASSUMED; SEND gated'),
    'delay_time':   ValueEncoder('delay_time', 'ms', minimum=0.0, maximum=65.535, step=0.001,
                                 decimals=3, confirmed=False, candidate=True,
                                 _fn=_delay_encode, _decode_fn=_delay_decode,
                                 note='time align: wire u16 microseconds (NVM 0x46c), display ms = us/1000 (FUN_1400f8c50); SEND gated'),
    'limiter_thresh': ValueEncoder('limiter_thresh', 'dBFS', minimum=-90.0, maximum=0.0, step=0.1,
                                 decimals=1, confirmed=False, candidate=True,
                                 _fn=_limiter_encode, _decode_fn=_limiter_decode,
                                 note='wire i32; dBFS = 20*log10(raw/2^31); 0x7FFFFFFF=0dBFS=OFF (FUN_1400f8c50); SEND gated'),
    'phase':        ValueEncoder('phase', 'bool', confirmed=False, candidate=True,
                                 _fn=_bool_encode, _decode_fn=_bool_decode,
                                 note='invertPhase: single-byte bool 1=inverted (NVM 0x46a, CODE); SEND gated'),
    'passthru':     ValueEncoder('passthru', 'bool', confirmed=False, candidate=True,
                                 _fn=_bool_encode, _decode_fn=_bool_decode,
                                 note='passthrough: single-byte bool 1=on (NVM 0x46e bit0, CODE); SEND gated'),
    'mute':         ValueEncoder('mute', 'bool', confirmed=False, candidate=True,
                                 _fn=_bool_encode, _decode_fn=_bool_decode,
                                 note='mute: single-byte bool 1=muted (NVM 0x46f, CODE); captured 0x0700 index-8 flag id (mute vs passthru) still SOFT; SEND gated'),
    # EQ Q: IEEE half-float BE on the 0x0704 band (written twice); verified against the
    # 0x0708 coefficients in the 2026-10-22 capture (Q 3.0..5.0 in 0.5 steps). Display only.
    'eq_q':         ValueEncoder('eq_q', 'Q', minimum=0.1, maximum=20.0, step=0.5, decimals=2,
                                 confirmed=False, candidate=True,
                                 _fn=lambda q: _struct.pack('>e', float(q)),
                                 _decode_fn=lambda raw: float(_struct.unpack('>e', raw[:2])[0]),
                                 note='EQ Q: IEEE half-float BE on 0x0704 sub05 (DEV, matches 0x0708 coeffs); range ASSUMED; SEND gated'),
    # Genuinely unknown -- keep fully unconfirmed (encode AND decode refuse):
    'ripple':       ValueEncoder('ripple', 'dB', note='Chebyshev passband ripple: wire scale UNKNOWN; blocked'),
    'filter_model': ValueEncoder('filter_model', 'enum'),
    'biquad_coeff': ValueEncoder('biquad_coeff', 'coeff'),
}


def get(name: str) -> ValueEncoder:
    return ENCODERS[name]


def confirmed_encoders() -> list[str]:
    return [n for n, e in ENCODERS.items() if e.confirmed]
