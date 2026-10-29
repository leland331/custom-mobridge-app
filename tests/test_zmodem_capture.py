"""The temp.ini receive against the device's real bytes and the exe's real replies
(USBPcap 2026-10-21: RX usb1.non_frame_text_sample, TX stream_out.hex)."""
from mobridge import zmodem

DEVICE = (b'**\x18B0400000801108e\r\x8a\x11temp.ini\x0040 0\x18k\xe1\xcf\x11*\x18A\n\x00\x00\x00\x00F\xae'
          b'[vehicle]\ntype=forda2bg1.1\nvariant=cph1\n\x18k\xad$\x11'
          b'**\x18B0b280000005e72\r\x8a\x11**\x18B0800000000022d\r\x8a')
EXE = (b'**\x18B01002800239137\r\x8a\x11**\x18B0900000000a87c\r\x8a\x11**\x18B03280000005c5f\r\x8a'
       b'**\x18B01002800239137\r\x8a\x11' + (b'\x18' * 8 + b'\x08' * 10) * 2)


class T:
    def __init__(self, rx):
        self.rx, self.tx = bytearray(rx), bytearray()

    def read(self, n, timeout=None):
        out = bytes(self.rx[:n]); del self.rx[:n]
        return out

    def write(self, b):
        self.tx += b


def test_receive_temp_ini_matches_capture():
    t = T(DEVICE)
    name, data = zmodem.receive_file(t, timeout=0.5)
    assert (name, data) == ('temp.ini', b'[vehicle]\ntype=forda2bg1.1\nvariant=cph1\n')
    assert bytes(t.tx) == EXE


def test_abort_seq_is_exe():
    assert zmodem.ABORT_SEQ == b'\x18' * 8 + b'\x08' * 10


def test_zmodem_bytes_in_the_0282_read_are_kept():
    """R2: the ZFILE header arriving in the same read as the 0x0282 ack still reaches ZMODEM."""
    from mobridge import connect
    from mobridge.framing import MdiFramer
    from mobridge.message import MdiMessage
    f = MdiFramer()
    ack = f.encode(MdiMessage(cmd=0x0282, data=b'\x01'))
    t = T(ack + DEVICE)
    rest = bytearray()
    rep = connect._exchange(t, f, f.encode(MdiMessage(cmd=0x0282, data=b'')), 0x0282, 0.2,
                            lambda fr: None, rest=rest)
    assert rep.data == b'\x01'
    name, data = zmodem.receive_file(connect._Prefixed(t, bytes(rest)), timeout=0.5)
    assert name == 'temp.ini' and data.startswith(b'[vehicle]')
