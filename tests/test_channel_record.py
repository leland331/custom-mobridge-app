"""The 0x0700 channel-property record builder must reproduce the official app's
captured frames BYTE-EXACT. These vectors are the 2026-10-22 vehicle capture
(USBPcap, all checksums valid), decoded on the laptop. If any assert here fails,
the builder is NOT proven and must not feed the wire.
"""
import struct

from mobridge import channel_record as cr
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage


def _frame(payload_hex):
    """Frame a 0x0700 payload and return the full on-wire hex."""
    return MdiFramer().encode(
        MdiMessage(cmd=cr.CMD_CHANNEL_PROPERTY, data=bytes.fromhex(payload_hex))).hex()


# Captured full-wire frames (sync|len|cmd|payload|cksum), verbatim from the decode.
CAP_GAIN_M6_CH1    = 'aa001007000001e10000000000000000007fffffff8b'
CAP_DELAY_5MS_CH1  = 'aa001007000001ed0000138800000000007fffffffe4'
CAP_LIMITER_M20CH1 = 'aa001007000001ed0000000000000000000cccccd087'
CAP_CH11_FLAG_ON   = 'aa00100700000bed0000000000010000007fffffff74'
CAP_CH11_FLAG_OFF  = 'aa00100700000bed0000000000000000007fffffff75'
# LEN4 gain-only short form
CAP_GAIN_M0P1_LEN4 = 'aa000407000000eccd3c'
CAP_GAIN_0P0_LEN4  = 'aa000407000000ed0008'


def test_builder_reproduces_captured_gain_record():
    # Ch1 gain -6.0 dB, everything else at its current device value (delay 0,
    # limiter off/max).
    payload = cr.build_record(1, gain_db=-6.0, delay_ms=0.0, limiter_dbfs=None)
    assert _frame(payload.hex()) == CAP_GAIN_M6_CH1


def test_builder_reproduces_captured_delay_record():
    # Ch1 delay 5.00 ms (gain unity 0.0, limiter off). Proves delay is be16 at
    # bytes [5-6] -- a [6-7] placement would NOT match this frame.
    payload = cr.build_record(1, gain_db=0.0, delay_ms=5.0, limiter_dbfs=None)
    assert _frame(payload.hex()) == CAP_DELAY_5MS_CH1
    assert payload[5:7] == (5000).to_bytes(2, 'big')
    assert payload[6:8] != (5000).to_bytes(2, 'big')   # guard against the old off-by-one


def test_builder_reproduces_captured_limiter_record():
    payload = cr.build_record(1, gain_db=0.0, delay_ms=0.0, limiter_dbfs=-20.0)
    assert _frame(payload.hex()) == CAP_LIMITER_M20CH1


def test_builder_reproduces_captured_flag_set_and_undo():
    on = cr.build_record(11, gain_db=0.0, delay_ms=0.0, limiter_dbfs=None,
                         flags={cr.PROVEN_FLAG_OFFSET: 1})
    off = cr.build_record(11, gain_db=0.0, delay_ms=0.0, limiter_dbfs=None,
                          flags={cr.PROVEN_FLAG_OFFSET: 0})
    assert _frame(on.hex()) == CAP_CH11_FLAG_ON
    assert _frame(off.hex()) == CAP_CH11_FLAG_OFF


def test_builder_reproduces_len4_gain_only():
    assert _frame(cr.build_gain_only(0, -0.1).hex()) == CAP_GAIN_M0P1_LEN4
    assert _frame(cr.build_gain_only(0, 0.0).hex()) == CAP_GAIN_0P0_LEN4


def test_limiter_off_is_max_int():
    assert cr.limiter_raw(None) == 0x7FFFFFFF
    assert cr.limiter_raw(0.0) == 0x7FFFFFFF
    # -20 dBFS is one tenth of full scale (20*log10(0.1) = -20)
    assert cr.limiter_raw(-20.0) == 0x0cccccd0


def test_flag_offset_cannot_collide_with_a_scalar_field():
    # bytes owned by gain/delay/limiter must be refused as flag targets
    for bad in (2, 3, 5, 6, 12, 15):
        try:
            cr.build_record(1, flags={bad: 1})
        except ValueError:
            continue
        raise AssertionError('byte %d should be refused as a flag slot' % bad)


def test_roundtrip_decode():
    payload = cr.build_record(4, gain_db=-3.0, delay_ms=2.5, limiter_dbfs=-12.0)
    d = cr.decode_record(payload)
    assert d['channel'] == 4
    assert abs(d['gain_db'] - (-3.0)) < 1e-9
    assert abs(d['delay_ms'] - 2.5) < 1e-6
    assert abs(d['limiter_dbfs'] - (-12.0)) < 0.01


def test_eq_gain_is_ieee_half_float_not_512_per_db():
    # The capture showed 0x4200=+3.0, 0x4300=+3.5, 0x4400=+4.0, 0x4500=+5.0 dB,
    # which is IEEE half-float, NOT the 512-units/dB gain scheme.
    for raw_be, db in ((0x4200, 3.0), (0x4300, 3.5), (0x4400, 4.0), (0x4500, 5.0)):
        assert struct.unpack('>e', raw_be.to_bytes(2, 'big'))[0] == db
