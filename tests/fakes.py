"""Test doubles. FakeFramer is NOT the real Mobridge wire format; it only shows
what a real Framer has to do, so the job/client layers can be tested end to end."""
import binascii
import struct
import threading
import queue

from mobridge.framing import Frame, Framer
from mobridge.message import MdiMessage
from mobridge.transport import Transport

TEST_WIREMAP = {'devid': 0x0100, 'jobs': {
    'Ping': {'fblock': 0x01, 'inst': 0, 'fkt': 0x000, 'op': 1, 'resp_op': 0xC},
    'SWVersion': {'fblock': 0x01, 'inst': 0, 'fkt': 0x001, 'op': 1, 'resp_op': 0xC},
    'FlashRead': {'fblock': 0x02, 'inst': 0, 'fkt': 0x010, 'op': 1, 'resp_op': 0xC},
    'FlashCrc': {'fblock': 0x02, 'inst': 0, 'fkt': 0x011, 'op': 1, 'resp_op': 0xC},
}}


class FakeFramer(Framer):
    """0x7E | len u16 | devid u16 fblock fkt u16 op | data | crc16"""
    name = 'fake'

    def __init__(self):
        self.buf = bytearray()

    def encode(self, m):
        body = struct.pack('>HBHB', m.devid, m.fblock, m.fkt, m.op) + m.data
        return b'\x7e' + struct.pack('>H', len(body)) + body + struct.pack('>H', binascii.crc_hqx(body, 0))

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b'\x7e')
            if i < 0:
                if self.buf:
                    out.append(Frame('text', bytes(self.buf))); self.buf.clear()
                return out
            if i:
                out.append(Frame('text', bytes(self.buf[:i]))); del self.buf[:i]
            if len(self.buf) < 3:
                return out
            n = struct.unpack_from('>H', self.buf, 1)[0]
            if len(self.buf) < 3 + n + 2:
                return out
            body = bytes(self.buf[3:3 + n]); del self.buf[:3 + n + 2]
            devid, fb, fkt, op = struct.unpack_from('>HBHB', body)
            out.append(Frame('mdi', body, MdiMessage(devid, fb, 0, fkt, op, body[6:])))


class FakeMdiDevice(Transport):
    """In-memory device speaking FakeFramer, backed by a fake 4 MiB flash."""

    def __init__(self, flash: bytes, log_noise=True):
        self.flash = flash
        self.fr = FakeFramer()
        self.rx = queue.Queue()
        self.sent = []
        self.noise = log_noise

    def write(self, data):
        self.sent.append(data)
        for f in self.fr.feed(data):
            if f.kind != 'mdi':
                continue
            m = f.message
            if self.noise:
                self.rx.put(b'[WSNDMGR] tick\r\n')   # the real device floods log text
            if (m.fblock, m.fkt) == (0x01, 0x000):
                d = b''
            elif (m.fblock, m.fkt) == (0x01, 0x001):
                d = b'02.11.08\0'
            elif (m.fblock, m.fkt) == (0x02, 0x010):
                a, n = struct.unpack('>IH', m.data); d = self.flash[a:a + n]
            elif (m.fblock, m.fkt) == (0x02, 0x011):
                a, n = struct.unpack('>II', m.data); d = struct.pack('>I', binascii.crc32(self.flash[a:a + n]))
            else:
                continue
            self.rx.put(self.fr.encode(MdiMessage(m.devid, m.fblock, 0, m.fkt, 0xC, d)))

    def read(self, size=4096, timeout=0.1):
        try:
            return self.rx.get(timeout=timeout)
        except queue.Empty:
            return b''


class ScriptedTransport(Transport):
    """Replies to fixed byte triggers with fixed bytes; records everything written."""

    def __init__(self, replies=(), idle=b''):
        self.replies = list(replies)
        self.sent = bytearray()
        self.out = bytearray(idle)
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            self.sent += data
            for trig, resp in list(self.replies):
                if trig in self.sent:
                    self.out += resp
                    self.replies.remove((trig, resp))

    def read(self, size=4096, timeout=0.1):
        with self.lock:
            d = bytes(self.out[:size]); del self.out[:size]
        return d
