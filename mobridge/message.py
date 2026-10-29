"""The logical MDI message, independent of how it is framed on the wire.

From the app decompile (NOTES.md 7 / 11b): a MOST-FBlock style message,
devid(u16) . fblock(u8) . inst(u8) . fkt(u16) . op(u8) . data[]
printed by the app as  MSG:{devid:04x}.{fblock:02x}.{inst:02x}.{fkt:03x}.{op:1x}.({len})
The app's message class also carries target node and service id; where they
go on the wire is part of the unconfirmed framing.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MdiMessage:
    devid: int = 0
    fblock: int = 0
    inst: int = 0
    fkt: int = 0
    op: int = 0
    data: bytes = b''
    target_node: int | None = None
    service_id: int | None = None
    session: int | None = None   # firmware checks "Session mismatch: Got: %d, curr: %d"
    cmd: int | None = None       # flat 16-bit MDI command code (fw dispatch table); see FRAMING.md
    meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if self.cmd is not None:
            return 'MDI:cmd=%04x.(%d)' % (self.cmd, len(self.data))
        # identical to the app's own log format, so our logs diff cleanly against its logs
        return 'MSG:%04x.%02x.%02x.%03x.%1x.(%d)' % (self.devid, self.fblock, self.inst, self.fkt, self.op, len(self.data))


# TODO(decompile): MOST payloads are big-endian; confirm encodeNumber's byte order.
NUMBER_BYTEORDER = 'big'


def encode_number(value: int, width: int, signed: bool = False) -> bytes:
    if not 1 <= width <= 8:
        raise ValueError('Width Out of Range (1-8)')  # same wording as the app
    return value.to_bytes(width, NUMBER_BYTEORDER, signed=signed)


def decode_number(data: bytes, offset: int = 0, width: int = 1, signed: bool = False) -> int:
    if not 1 <= width <= 8:
        raise ValueError('Width Out of Range (1-8)')
    if offset + width > len(data):
        raise ValueError('message too short for a %d-byte number at %d' % (width, offset))
    return int.from_bytes(data[offset:offset + width], NUMBER_BYTEORDER, signed=signed)
