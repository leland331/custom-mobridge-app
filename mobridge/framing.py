"""Framing: bytes on the wire <-> frames. This is the swappable layer.

The MDI serial framing (sync bytes, length, escaping, CRC) has not been
byte-pinned from the decompile yet. Until it is, the default MDI framer
refuses to encode and passes received bytes through as raw frames, so
nothing invented ever goes on the wire.

To plug in the real framing once it is known:

    from mobridge.framing import Framer, register_framer

    @register_framer
    class FdpFramer(Framer):
        name = 'fdp'
        def encode(self, msg): ...      # MdiMessage -> bytes
        def feed(self, data): ...       # bytes -> [Frame]

then run with `--framer fdp` (or pass framer=FdpFramer() to MobridgeClient).
tests/test_framer.py has a fake length-prefixed framer showing the shape.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .message import MdiMessage


class FramingUnknown(NotImplementedError):
    pass


@dataclass
class Frame:
    kind: str                         # 'text' | 'mdi' | 'raw' | 'error'
    data: bytes = b''
    message: MdiMessage | None = None

    @property
    def text(self) -> str:
        return self.data.decode('latin-1').rstrip('\r\n')


class Framer(ABC):
    name = 'abstract'

    @abstractmethod
    def encode(self, msg: MdiMessage) -> bytes: ...

    @abstractmethod
    def feed(self, data: bytes) -> list[Frame]: ...

    def reset(self) -> None:
        pass


class TextFramer(Framer):
    """Line framer for the BOOTS console and the device's debug log stream."""
    name = 'text'

    def __init__(self):
        self._buf = bytearray()

    def encode(self, msg: MdiMessage) -> bytes:
        raise FramingUnknown('the text console does not carry MDI messages')

    @staticmethod
    def encode_line(line: str) -> bytes:
        return line.encode('latin-1') + b'\n'

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        out = []
        while True:
            i = self._buf.find(b'\n')
            if i < 0:
                break
            out.append(Frame('text', bytes(self._buf[:i + 1])))
            del self._buf[:i + 1]
        return out

    def flush(self) -> list[Frame]:
        if not self._buf:
            return []
        f = Frame('text', bytes(self._buf))
        self._buf.clear()
        return [f]

    def reset(self) -> None:
        self._buf.clear()


class UnconfirmedMdiFramer(Framer):
    """Default MDI framer: never encodes, surfaces everything received as raw."""
    name = 'unconfirmed'

    def encode(self, msg: MdiMessage) -> bytes:
        raise FramingUnknown(
            'MDI wire framing is not confirmed yet (sync/length/CRC still being recovered '
            'from the app decompile). Refusing to send %s. Plug in a real Framer, or use '
            'the Lua-in-app route (lua/README.md).' % msg)

    def feed(self, data: bytes) -> list[Frame]:
        return [Frame('raw', data)] if data else []



MDI_SYNC = 0xAA
CKSUM_BASE = 0x100   # FRAMING.md: checksum = (0x100 - sum(bytes after sync)) & 0xFF


class MdiFramer(Framer):
    """The real MDI frame, derived from the 2.11.8 firmware (see FRAMING.md):

        0xAA | LEN(BE16) | CMD(BE16) | payload[LEN] | CKSUM

    CKSUM = (0x100 - sum(every byte after the sync)) & 0xFF.

    Decompile-derived: verify against one capture before trusting writes. It is
    NOT the default framer; select it with --framer mdi once confirmed. Any extra
    mux_spp channel/escape wrapper (if a capture shows one) is layered under this.
    """
    name = 'mdi'
    MAX_LEN = 0x1000   # firmware rejects W[P1+0x6] > 0xff0 in the RX path

    def __init__(self):
        self._buf = bytearray()

    @staticmethod
    def checksum(body: bytes) -> int:
        return (CKSUM_BASE - sum(body)) & 0xFF

    def encode(self, msg: MdiMessage) -> bytes:
        if msg.cmd is None:
            raise FramingUnknown('MdiFramer needs msg.cmd set (the 16-bit command code)')
        if len(msg.data) > self.MAX_LEN:
            raise ValueError('payload %d exceeds MDI max %d' % (len(msg.data), self.MAX_LEN))
        body = len(msg.data).to_bytes(2, 'big') + (msg.cmd & 0xFFFF).to_bytes(2, 'big') + msg.data
        return bytes([MDI_SYNC]) + body + bytes([self.checksum(body)])

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        out = []
        while True:
            i = self._buf.find(MDI_SYNC)
            if i < 0:
                if self._buf:
                    out.append(Frame('text', bytes(self._buf))); self._buf.clear()
                return out
            if i:
                out.append(Frame('text', bytes(self._buf[:i]))); del self._buf[:i]
            if len(self._buf) < 5:         # sync + LEN + CMD
                return out
            length = int.from_bytes(self._buf[1:3], 'big')
            cmd = int.from_bytes(self._buf[3:5], 'big')
            total = 1 + 4 + length + 1
            if length > self.MAX_LEN:       # stray 0xAA inside noise; skip it
                del self._buf[0]
                continue
            if len(self._buf) < total:
                return out
            body = bytes(self._buf[1:5 + length])
            cksum = self._buf[5 + length]
            frame = bytes(self._buf[:total]); del self._buf[:total]
            if (sum(body) + cksum) & 0xFF != 0:
                out.append(Frame('error', frame))
                continue
            out.append(Frame('mdi', frame, MdiMessage(cmd=cmd, data=body[4:])))
        return out

    def reset(self) -> None:
        self._buf.clear()


FRAMERS: dict[str, type[Framer]] = {}


def register_framer(cls: type[Framer]) -> type[Framer]:
    FRAMERS[cls.name] = cls
    return cls


register_framer(TextFramer)
register_framer(UnconfirmedMdiFramer)
register_framer(MdiFramer)


def get_framer(name: str) -> Framer:
    try:
        return FRAMERS[name]()
    except KeyError:
        raise KeyError('unknown framer %r (have: %s)' % (name, ', '.join(FRAMERS)))
