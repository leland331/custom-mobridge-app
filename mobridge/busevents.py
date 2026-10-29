"""Unsolicited bus events (exe FUN_14015de30, UNSOLICITED_EVENTS.md).

Only two of the five device-initiated event frames have consumers in the exe:
0x0646 (event 0xc, A2B bus message -> QFdpA2b) and 0x0615 (event 8, a received CAN
frame -> QFdpCan). 0x0651, 0x070e and 0x0720
reach no consumer, so they are dropped here as well."""
from __future__ import annotations

from dataclasses import dataclass, field

A2B_EVENT = 0x0646
CAN_EVENT = 0x0615
ROUTED = (A2B_EVENT, CAN_EVENT)


@dataclass
class BusEvent:
    bus: str                      # 'a2b' | 'can'
    cmd: int
    raw: bytes
    sub: int | None = None        # A2B subtype 0..5
    fields: dict = field(default_factory=dict)


def decode(cmd: int, data: bytes) -> BusEvent | None:
    """Decode an unsolicited frame as the exe does, or None when it has no consumer."""
    d = bytes(data or b'')
    if cmd == A2B_EVENT and d:
        ev = BusEvent('a2b', cmd, d, sub=d[0])
        b = d[1:]
        if d[0] == 0 and len(b) >= 5:                 # A2bMsgStruct: u16, u8, u16 len, len x u8
            n = int.from_bytes(b[3:5], 'big')
            ev.fields = {'id': int.from_bytes(b[0:2], 'big'), 'flags': b[2], 'data': b[5:5 + n]}
        elif d[0] == 3 and len(b) >= 2:
            ev.fields = {'a': b[0], 'b': b[1]}
        elif d[0] == 4 and len(b) >= 3:
            ev.fields = {'a': b[0], 'b': int.from_bytes(b[1:3], 'big')}
        elif d[0] == 5 and len(b) >= 2:
            ev.fields = {'a': int.from_bytes(b[0:2], 'big')}
        return ev
    if cmd == CAN_EVENT and len(d) >= 11:
        # FUN_140173220: u32 id, u16, u8, u32, then up to 8 data bytes (all BE)
        return BusEvent('can', cmd, d, fields={'id': int.from_bytes(d[0:4], 'big'), 'data': d[11:19]})
    return None
