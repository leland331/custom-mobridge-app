"""Bootloader reader (mobridge.bootread) against a simulated unit: the fake answers
0x0000/0x0001/0x0203 like firmware 2.11.8 and, on 0x0202, replies then ZMODEM-sends
data.bin with our own spec-following sender (itself checked against lrzsz)."""
import binascii
import os
import queue
import struct
import threading
import time

import pytest

from mobridge import bootread, zmodem
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage
from mobridge.transport import Transport

FLASH = os.urandom(0x60000)


class _End(Transport):
    """One end of an in-memory serial cable."""

    def __init__(self, rx: queue.Queue, tx: queue.Queue):
        self.rx, self.tx, self.buf = rx, tx, bytearray()
        self.baud = 115200
        self.peer = None

    def write(self, data):
        if self.peer is not None and self.peer.baud != self.baud:
            data = bytes(b ^ 0x5A for b in data)       # wrong baud = garbage
        self.tx.put(bytes(data))

    def read(self, size=4096, timeout=0.1):
        deadline = time.monotonic() + (timeout or 0)
        while not self.buf:
            try:
                self.buf += self.rx.get(timeout=max(0.0, deadline - time.monotonic()) or 0.001)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    return b''
        out = bytes(self.buf[:size]); del self.buf[:size]
        return out

    def set_baud(self, baud):
        self.baud = baud


class FakeUnit:
    def __init__(self, product=0x202C, baud=115200, corrupt_once_at=None, crc_xor=0):
        a, b = queue.Queue(), queue.Queue()
        self.host = _End(a, b)
        self.dev = _End(b, a)
        self.host.peer, self.dev.peer = self.dev, self.host
        self.dev.baud = baud
        self.product, self.crc_xor = product, crc_xor
        self.corrupt_once_at = corrupt_once_at
        self.cmds = []
        self.claimed = False
        self.stop = False
        self.th = threading.Thread(target=self.run, daemon=True)
        self.th.start()

    def send(self, cmd, data):
        self.dev.write(MdiFramer().encode(MdiMessage(cmd=cmd, data=data)))

    def run(self):
        fr = MdiFramer()
        while not self.stop:
            for f in fr.feed(self.dev.read(4096, 0.05)):
                if f.kind != 'mdi' or not f.message:
                    continue
                cmd, d = f.message.cmd, f.message.data
                self.cmds.append((cmd, d))
                if cmd == 0x0000:
                    self.claimed = True
                    self.send(0, b'\x00\x01')
                elif not self.claimed:
                    self.send(0x0FFF, b'\x00\x01' + struct.pack('>H', cmd))
                elif cmd == 0x0001:
                    self.send(1, struct.pack('>H', self.product) + bytes(7) + bytes((2, 11, 9)))
                elif cmd in (0x0202, 0x0203):
                    region, off, n = struct.unpack('>BII', d)
                    assert region == 0
                    body = FLASH[off:off + n]
                    if cmd == 0x0203:
                        self.send(cmd, d + struct.pack('>I', binascii.crc32(body) ^ self.crc_xor))
                        continue
                    self.send(cmd, d + struct.pack('>I', len(body)))
                    if self.corrupt_once_at == off:
                        self.corrupt_once_at = None
                        body = body[:100] + bytes([body[100] ^ 1]) + body[101:]
                    zmodem.send_file(self.dev, body, 'data.bin', timeout=1.0)
                    fr = MdiFramer()

    def close(self):
        self.stop = True
        self.th.join(2)


@pytest.fixture
def small(monkeypatch):
    """Shrink the range to 24 KiB so the behaviour tests run fast."""
    monkeypatch.setattr(bootread, 'BOOT_END', 0x6000)
    return FLASH[:0x6000]


@pytest.fixture
def unit(request):
    u = FakeUnit(**getattr(request, 'param', {}))
    yield u
    u.close()


def test_reads_the_whole_bootloader(unit):
    image, rep = bootread.read_bootloader(unit.host, chunk=0x10000, zmodem_timeout=5.0)
    assert image == FLASH
    assert rep['all_crc_match'] and rep['product'] == '202c' and rep['firmware'] == '2.11.9'
    assert len(rep['chunks']) == 6


def test_sends_only_claim_identify_crc_and_read(unit, small):
    bootread.read_bootloader(unit.host, chunk=0x2000, zmodem_timeout=5.0)
    assert {c for c, _ in unit.cmds} == {0x0000, 0x0001, 0x0202, 0x0203}
    for c, d in unit.cmds:
        if c in (0x0202, 0x0203):
            region, off, n = struct.unpack('>BII', d)
            assert region == 0 and off + n <= 0x60000


@pytest.mark.parametrize('unit', [{'corrupt_once_at': 0x2000}], indirect=True)
def test_bad_chunk_is_read_again(unit, small):
    image, rep = bootread.read_bootloader(unit.host, chunk=0x1000, zmodem_timeout=5.0)
    assert image == small and rep['all_crc_match']
    reads = [d for c, d in unit.cmds if c == 0x0202 and struct.unpack('>BII', d)[1] == 0x2000]
    assert len(reads) == 2


@pytest.mark.parametrize('unit', [{'crc_xor': 0xFFFFFFFF}], indirect=True)
def test_crc_disagreement_reads_twice_then_flags(unit, small):
    image, rep = bootread.read_bootloader(unit.host, chunk=0x3000, zmodem_timeout=5.0)
    assert image == small and not rep['all_crc_match']


@pytest.mark.parametrize('unit', [{'baud': 230400}], indirect=True)
def test_falls_back_to_230400(unit, small):
    image, rep = bootread.read_bootloader(unit.host, chunk=0x3000, zmodem_timeout=5.0)
    assert image == small and rep['baud'] == 230400


@pytest.mark.parametrize('unit', [{'product': 0x2020}], indirect=True)
def test_other_product_stops_before_any_read(unit):
    with pytest.raises(bootread.BootReadError):
        bootread.read_bootloader(unit.host, zmodem_timeout=5.0)
    assert not [c for c, _ in unit.cmds if c in (0x0202, 0x0203)]


def test_refuses_anything_outside_the_bootloader_or_any_other_command():
    with pytest.raises(bootread.BootReadError):
        bootread.region_request(0x5F000, 0x2000)
    with pytest.raises(bootread.BootReadError):
        bootread.region_request(0x60000, 0x10)
    link = bootread.Link(_End(queue.Queue(), queue.Queue()))
    for cmd in (0x0010, 0x0011, 0x0201, 0x0700, 0x0281):
        with pytest.raises(bootread.BootReadError):
            link.ask(cmd)
    assert link.sent == []
