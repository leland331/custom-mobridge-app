"""Tests for the firmware-derived MDI framer (FRAMING.md)."""
import json
import os

import pytest

from mobridge.framing import MdiFramer, get_framer
from mobridge.message import MdiMessage
from mobridge.transport import Transport


def test_frame_layout_matches_framing_md():
    f = MdiFramer()
    wire = f.encode(MdiMessage(cmd=0x0610, data=bytes([0x01, 0x02, 0x03])))
    assert wire[0] == 0xAA
    assert wire[1:3] == b'\x00\x03'          # LEN big-endian
    assert wire[3:5] == b'\x06\x10'          # CMD big-endian
    assert wire[5:8] == b'\x01\x02\x03'
    body = wire[1:-1]
    assert (sum(body) + wire[-1]) & 0xFF == 0  # checksum zeroes the sum


def test_roundtrip_and_noise_handling():
    f = MdiFramer()
    a = f.encode(MdiMessage(cmd=0x0002, data=b''))
    b = f.encode(MdiMessage(cmd=0x0714, data=bytes(range(20))))
    frames = MdiFramer().feed(b'junk log\r\n' + a + b'\xaagarbage-not-a-frame' + b)
    mdi = [fr for fr in frames if fr.kind == 'mdi']
    assert len(mdi) == 2
    assert mdi[0].message.cmd == 0x0002 and mdi[0].message.data == b''
    assert mdi[1].message.cmd == 0x0714 and mdi[1].message.data == bytes(range(20))
    assert any(fr.kind == 'text' for fr in frames)


def test_split_across_reads():
    f = MdiFramer()
    wire = f.encode(MdiMessage(cmd=0x0021, data=b'SN12345'))
    rx = MdiFramer()
    assert rx.feed(wire[:3]) == [] or all(x.kind != 'mdi' for x in rx.feed(b''))
    out = []
    for byte in wire[3:]:
        out += rx.feed(bytes([byte]))
    mdi = [fr for fr in out if fr.kind == 'mdi']
    assert len(mdi) == 1 and mdi[0].message.data == b'SN12345'


def test_bad_checksum_is_error_frame():
    f = MdiFramer()
    wire = bytearray(f.encode(MdiMessage(cmd=0x0001, data=b'\x05')))
    wire[-1] ^= 0xFF
    frames = MdiFramer().feed(bytes(wire))
    assert any(fr.kind == 'error' for fr in frames)
    assert not any(fr.kind == 'mdi' for fr in frames)


def test_encode_needs_cmd_and_bounds():
    from mobridge.framing import FramingUnknown
    with pytest.raises(FramingUnknown):
        MdiFramer().encode(MdiMessage(data=b'x'))
    with pytest.raises(ValueError):
        MdiFramer().encode(MdiMessage(cmd=1, data=b'x' * 0x2000))


def test_wiremap_codes_are_in_range_and_registered():
    wm = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'mobridge', 'wiremap.json')))
    assert wm['framer'] == 'mdi' and isinstance(get_framer('mdi'), MdiFramer)
    for name, w in wm['jobs'].items():
        assert 0 <= w['cmd'] <= 0xFFFF
        assert w['confidence'] in ('high', 'med', 'low')


class MdiEchoDevice(Transport):
    """Minimal device: answers each request frame with the same cmd + a canned body,
    and floods log text, so matching + noise routing are exercised over the real framer."""
    REPLIES = {0x0002: b'\x00', 0x0001: b'02.11.08\x00', 0x0021: b'\x00'}

    def __init__(self):
        self.fr = MdiFramer()
        self.out = bytearray()
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        for f in self.fr.feed(data):
            if f.kind == 'mdi':
                self.out += b'[WSNDMGR] noise\r\n'
                body = self.REPLIES.get(f.message.cmd, b'')
                self.out += MdiFramer().encode(MdiMessage(cmd=f.message.cmd, data=body))

    def read(self, size=4096, timeout=0.1):
        d = bytes(self.out[:size]); del self.out[:size]
        return d


def test_job_runner_over_real_framer():
    from mobridge.client import MobridgeClient
    dev = MdiEchoDevice()
    c = MobridgeClient(dev, MdiFramer(), require_parity=False)
    assert c.sw_version() == '02.11.08'           # cmd 0x0001
    assert c.job('Authenticate').data == b'\x00'  # cmd 0x0002
