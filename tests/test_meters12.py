"""Live meters for outputs 9-12 on LB-RC<n> firmware (DspTest 0x0719 type 8, docs/chime-routing/LB-RC1.md)."""
from mobridge import cmdspec as C, metering as M


def test_request_types():
    assert M.request(0) == (7, 0x01) and M.request(7) == (7, 0x80)
    assert M.request(8) == (8, 0x01) and M.request(11) == (8, 0x08)


def test_max_channel():
    assert M.max_channel(False) == 8 and M.max_channel(True) == 12


def test_type8_wire_is_u8_mask():
    # the firmware cave reads only the byte after the type (B[P3 + 0xd]); a u64 would put the MSB there
    assert C.dsp_test(8, 0x04) == bytes([8, 4])
    assert C.dsp_test(7, 0x80) == bytes([7, 0x80])


def test_type8_reply_parse():
    assert C.parse_dsp_test(bytes([8, 0x02, 0xF6]), 8, 0x02) == {'db': [-10]}
    assert C.parse_dsp_test(bytes([8, 0x02]), 8, 0x02) == {'db': []}     # output missing: no level


def test_silence_reads_as_no_signal():
    # firmware: int8(20*log10(rms)+3), rms 0 -> -inf -> saturates, low byte 0x00
    assert M.rta_dbfs(0) == float('-inf') and M.rta_dbfs(-12) == -12.0 and M.rta_dbfs(4) is None
