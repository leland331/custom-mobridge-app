"""The official app's other two link modes: WIZARD (FF 55 frames) and LW
(single-character tokens), plus the mode detection connect uses when a unit does
not answer MDI.

Sources: re/analysis/TRANSPORT_REGION.md §1.4-1.5, §2.4, §6;
re/analysis/RECOVERY_ENTRY_OCT23.md §3 (LW TX/RX tables, step functions);
re/analysis/FLASH_APP_SIDE.md §2 (LW token parsers).

What is implemented, and only that:
  * WIZARD framing, both ways, byte-exact to FUN_14016e1f0 (RX) / FUN_14016e4f0 (TX).
    The WIZARDM job table (0x14258ef70) is not in the dump, so no wizard job is
    sent: a unit seen in WIZARD mode is reported as "Wizard", as the exe's UI does.
  * LW tokens: the RX tokenizer (table 0x14258ed50) and the TX serializers for the
    read-only tokens only (null, `u` knock, `i` identify). Erase/program/verify/
    flash/exit tokens are never built here (RECOVERY_ENTRY_OCT23.md §4 exclude list).
  * lw_identify: the exe's LW enter+identify steps (FUN_1401736f0 then FUN_140173770):
    null frame + `u`, wait for `U`+0x7c2e45bb, then `i`, read `I` (product, version).
  * detect_mode: which link a silent-to-MDI unit is speaking, from bytes it sent.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .modes import DeviceMode

# --- WIZARD (FF 55) --------------------------------------------------------------
WIZARD_SYNC = b'\xff\x55'
WIZARD_MAX_PAYLOAD = 0x400              # state 8: payload <= 0x400, else resync


@dataclass(frozen=True)
class WizardFrame:
    type: int
    id: int                             # 16-bit when type == 4 (states 6+7), else 8-bit (state 7)
    payload: bytes = b''


def _wizard_id_len(typ: int) -> int:
    return 2 if typ == 4 else 1


def wizard_encode(f: WizardFrame) -> bytes:
    """FUN_14016e4f0: FF 55, length (1 byte, or 00 hi lo when >= 0x100), type,
    id (u16 BE when type 4, else u8), payload, checksum = -sum(after sync) & 0xff.
    Length counts type + id + payload (parser state 8: payload = len - 3 / len - 2)."""
    if len(f.payload) > WIZARD_MAX_PAYLOAD:
        raise ValueError('wizard payload > 0x400')
    body = bytes([f.type & 0xFF]) + int(f.id).to_bytes(_wizard_id_len(f.type), 'big') + bytes(f.payload)
    n = len(body)
    head = bytes([n]) if n < 0x100 else b'\x00' + n.to_bytes(2, 'big')
    ck = (0x100 - sum(head + body)) & 0xFF
    return WIZARD_SYNC + head + body + bytes([ck])


class WizardParser:
    """FUN_14016e1f0 as a byte-fed parser; bad checksum -> frame dropped, resync."""

    def __init__(self):
        self.errors = 0
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[WizardFrame]:
        self._buf += data
        out = []
        b = self._buf
        while True:
            i = b.find(WIZARD_SYNC)
            if i < 0:
                del b[:max(0, len(b) - 1)]          # keep a trailing 0xff
                return out
            del b[:i]
            if len(b) < 3:
                return out
            if b[2] == 0:                           # extended length: 00 hi lo
                if len(b) < 5:
                    return out
                n, start = int.from_bytes(b[3:5], 'big'), 5
            else:
                n, start = b[2], 3
            if len(b) < start + 1:
                return out
            typ = b[start]
            idl = _wizard_id_len(typ)
            plen = n - 1 - idl
            if plen < 0 or plen > WIZARD_MAX_PAYLOAD:
                del b[:2]; self.errors += 1
                continue
            total = start + n + 1
            if len(b) < total:
                return out
            frame = bytes(b[:total]); del b[:total]
            if (sum(frame[2:-1]) + frame[-1]) & 0xFF != 0:
                self.errors += 1
                continue
            body = frame[start:-1]
            out.append(WizardFrame(typ, int.from_bytes(body[1:1 + idl], 'big'), body[1 + idl:]))


# --- LW single-character tokens ----------------------------------------------------
LW_KNOCK_ARG = 0xA13D23DE               # `u` argument (FUN_14016f230, u32 LE)
LW_BOOT_MAGIC = 0x7C2E45BB              # device reply `U` + this, LE
LW_NULL_LEN = 0x42                      # `00` frame: 66 bytes of zero

# RX table 0x14258ed50: first char -> data length (D: up to 0x20, NUL-terminated)
LW_RX = {ord('U'): 4, ord('I'): 6, ord('R'): 1, ord('E'): 1, ord('P'): 1, ord('V'): 1,
         ord('F'): 1, ord('X'): 0, ord('D'): 0x20}


def lw_null() -> bytes:
    return bytes(LW_NULL_LEN)


def lw_knock() -> bytes:
    return b'u' + LW_KNOCK_ARG.to_bytes(4, 'little')


def lw_identify_query() -> bytes:
    return b'i'


@dataclass(frozen=True)
class LwToken:
    char: str
    data: bytes

    @property
    def word(self) -> int | None:
        """`U`: the LE u32 after the char (FUN_14016f260)."""
        return int.from_bytes(self.data, 'little') if self.char == 'U' else None

    @property
    def ident(self) -> tuple[int, tuple[int, int, int]] | None:
        """`I`: {u8, u16 LE product, u8, u8, u8 version} (FUN_14016f290)."""
        if self.char != 'I':
            return None
        d = self.data
        return int.from_bytes(d[1:3], 'little'), (d[3], d[4], d[5])


class LwParser:
    """FUN_14016f5c0: match the first byte against the RX table, collect its data
    bytes (D: until NUL, max 0x20), emit a token; unknown bytes are skipped."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[LwToken]:
        self._buf += data
        out = []
        b = self._buf
        while b:
            c = b[0]
            if c not in LW_RX:
                del b[0]
                continue
            if c == ord('D'):
                end = b.find(b'\0', 1)
                if end < 0:
                    if len(b) > 1 + LW_RX[c]:
                        end = 1 + LW_RX[c]
                    else:
                        return out
                out.append(LwToken('D', bytes(b[1:end])))
                del b[:end + 1]
                continue
            n = 1 + LW_RX[c]
            if len(b) < n:
                return out
            out.append(LwToken(chr(c), bytes(b[1:n])))
            del b[:n]
        return out


def _is_boot_magic(t: LwToken) -> bool:
    return t.char == 'U' and t.word == LW_BOOT_MAGIC


@dataclass
class LwIdent:
    product_id: int
    version: str


def lw_identify(transport, timeout: float = 1.0, log=None) -> LwIdent | None:
    """FUN_1401736f0 + FUN_140173770: null frame and `u`, wait `U`+magic, then `i`
    and read `I`. Read-only tokens only. None when the unit does not answer."""
    log = log or (lambda s: None)
    parser = LwParser()

    def wait(pred):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for t in parser.feed(transport.read(4096, 0.05)):
                if pred(t):
                    return t
        return None

    transport.write(lw_null())
    transport.write(lw_knock())
    if wait(_is_boot_magic) is None:
        log('lw: no U reply')
        return None
    transport.write(lw_identify_query())
    t = wait(lambda t: t.char == 'I')
    if t is None:
        log('lw: no I reply')
        return None
    pid, ver = t.ident
    return LwIdent(pid, '%d.%d.%d' % ver)


# --- detection -------------------------------------------------------------------
WIZARD_MIN_FRAMES = 2                   # positive evidence, not one lucky checksum in noise
# whole console lines only: a stray "OK" inside binary is not a BOOTS line
_BOOTS_RX = re.compile(rb'(^|[\r\n])(READY|OK|Prod=0x[0-9a-fA-F]+, Ver=\d+\.\d+\.\d+)[\r\n]')


def detect_mode(data: bytes) -> DeviceMode:
    """Which non-MDI link the bytes a unit sent belong to (UNKNOWNM sub-modes):
    LW = `U`+0x7c2e45bb token, WIZARD = WIZARD_MIN_FRAMES valid FF 55 frames,
    BOOTS = a console line the BOOTS parser accepts (READY / OK / Prod=...).
    Otherwise UNKNOWN.

    One FF 55 frame is not evidence: wrong-rate garbage around the baud switch can
    hold an FF 55 whose 8-bit checksum matches by chance (~1/256 per sync); two
    independent valid frames are ~1/65536."""
    if any(_is_boot_magic(t) for t in LwParser().feed(data)):
        return DeviceMode.LW
    if len(WizardParser().feed(data)) >= WIZARD_MIN_FRAMES:
        return DeviceMode.WIZARD
    if _BOOTS_RX.search(data):
        return DeviceMode.BOOTS
    return DeviceMode.UNKNOWN
