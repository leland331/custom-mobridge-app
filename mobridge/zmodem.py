"""ZMODEM sender (Chuck Forsberg's protocol, as used by lrzsz).

The device firmware embeds a standard ZMODEM receiver (common/zmodem/zmodem.c:
ZCRCE/G/Q/W frame ends, CRC16 + CRC32, "ZMD RX: Bad frame type"), and the app
sends .fif files with it. This is a plain, spec-following sender, verified in
the tests against lrzsz `rz`. Nothing here is Mobridge-specific.
"""
from __future__ import annotations

import binascii
import os
import re
import time
from typing import Callable

from .transport import Transport

ZPAD, ZDLE, ZDLEE = 0x2A, 0x18, 0x58
ZBIN, ZHEX, ZBIN32 = 0x41, 0x42, 0x43
XON = 0x11

(ZRQINIT, ZRINIT, ZSINIT, ZACK, ZFILE, ZSKIP, ZNAK, ZABORT, ZFIN, ZRPOS, ZDATA,
 ZEOF, ZFERR, ZCRC, ZCHALLENGE, ZCOMPL, ZCAN, ZFREECNT, ZCOMMAND) = range(19)
FRAME_NAMES = ('ZRQINIT ZRINIT ZSINIT ZACK ZFILE ZSKIP ZNAK ZABORT ZFIN ZRPOS ZDATA '
               'ZEOF ZFERR ZCRC ZCHALLENGE ZCOMPL ZCAN ZFREECNT ZCOMMAND').split()
ZCRCE, ZCRCG, ZCRCQ, ZCRCW = 0x68, 0x69, 0x6A, 0x6B

CANFC32 = 0x20   # ZRINIT ZF0: receiver can do 32-bit CRC
ESCCTL = 0x40    # ZRINIT ZF0: receiver wants all control chars escaped
ZCBIN = 1        # ZFILE ZF0: binary transfer
ABORT_SEQ = bytes([0x18] * 8 + [0x08] * 10)   # FUN_140172940: 8 CAN + 10 BS

_ESCAPED = {0x10, 0x11, 0x13, 0x18, 0x90, 0x91, 0x93}


class ZmodemError(Exception):
    pass


def crc16(data: bytes, crc: int = 0) -> int:
    return binascii.crc_hqx(data, crc)


def escape(data: bytes, escctl: bool = False) -> bytes:
    out = bytearray()
    prev = 0
    for c in data:
        if c in _ESCAPED or (escctl and (c & 0x60) == 0) or ((c & 0x7F) == 0x0D and (prev & 0x7F) == 0x40):
            out += bytes((ZDLE, c ^ 0x40))
        else:
            out.append(c)
        prev = c
    return bytes(out)


def pos_bytes(pos: int) -> bytes:
    return pos.to_bytes(4, 'little')


def flag_bytes(zf0=0, zf1=0, zf2=0, zf3=0) -> bytes:
    # ZF0 is the last of the 4 header bytes, ZP0 the first
    return bytes((zf3, zf2, zf1, zf0))


def hex_header(ftype: int, b4: bytes) -> bytes:
    body = bytes([ftype]) + b4
    crc = crc16(body)
    out = bytes((ZPAD, ZPAD, ZDLE, ZHEX)) + (body + crc.to_bytes(2, 'big')).hex().encode() + b'\r\x8a'
    return out if ftype in (ZFIN, ZACK) else out + bytes((XON,))


def bin_header(ftype: int, b4: bytes, crc32: bool, escctl=False) -> bytes:
    body = bytes([ftype]) + b4
    if crc32:
        return bytes((ZPAD, ZDLE, ZBIN32)) + escape(body + binascii.crc32(body).to_bytes(4, 'little'), escctl)
    return bytes((ZPAD, ZDLE, ZBIN)) + escape(body + crc16(body).to_bytes(2, 'big'), escctl)


def subpacket(data: bytes, end: int, crc32: bool, escctl=False) -> bytes:
    if crc32:
        crc = binascii.crc32(data + bytes([end])).to_bytes(4, 'little')
    else:
        crc = crc16(data + bytes([end])).to_bytes(2, 'big')
    return escape(data, escctl) + bytes((ZDLE, end)) + escape(crc, escctl)


class HeaderReader:
    """Pulls ZMODEM headers out of a byte stream that may also contain text noise."""

    def __init__(self, transport: Transport):
        self.t = transport
        self.buf = bytearray()
        self.noise = bytearray()  # non-ZMODEM bytes seen (device log text etc.)
        self.last_kind = None     # ZHEX / ZBIN / ZBIN32 of the last header read

    def _need(self, n: int, deadline: float) -> None:
        while len(self.buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('ZMODEM: timed out waiting for header')
            self.buf += self.t.read(4096, min(left, 0.2))

    def _unescape_take(self, n: int, deadline: float) -> bytes:
        out = bytearray()
        while len(out) < n:
            self._need(1, deadline)
            c = self.buf.pop(0)
            if c == ZDLE:
                self._need(1, deadline)
                c2 = self.buf.pop(0)
                if c2 == 0x18:
                    raise ZmodemError('receiver cancelled (CAN CAN)')
                out.append(c2 ^ 0x40)
            elif c in (0x11, 0x13, 0x91, 0x93):
                continue
            else:
                out.append(c)
        return bytes(out)

    def read(self, timeout: float = 10.0) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        while True:
            self._need(1, deadline)
            c = self.buf.pop(0)
            if c != ZPAD:
                self.noise.append(c)
                continue
            self._need(1, deadline)
            while self.buf[0] == ZPAD:
                self.buf.pop(0)
                self._need(1, deadline)
            if self.buf[0] != ZDLE:
                continue
            self.buf.pop(0)
            self._need(1, deadline)
            kind = self.buf.pop(0)
            if kind == ZHEX:
                self._need(14, deadline)
                raw = bytes(self.buf[:14]); del self.buf[:14]
                try:
                    hdr = bytes.fromhex(raw.decode('ascii'))
                except ValueError:
                    continue
                if crc16(hdr[:5]) != int.from_bytes(hdr[5:7], 'big'):
                    continue
                # a hex header ends CR LF|0x80 (+ XON except ZACK/ZFIN): consume them
                for skip in (0x0D, None, 0x11):
                    try:
                        self._need(1, time.monotonic() + 0.05)
                    except TimeoutError:
                        break
                    c = self.buf[0]
                    if (skip is None and c & 0x7F == 0x0A) or c == skip:
                        self.buf.pop(0)
                    else:
                        break
                self.last_kind = ZHEX
                return hdr[0], hdr[1:5]
            if kind == ZBIN:
                hdr = self._unescape_take(7, deadline)
                if crc16(hdr[:5]) != int.from_bytes(hdr[5:7], 'big'):
                    continue
                self.last_kind = ZBIN
                return hdr[0], hdr[1:5]
            if kind == ZBIN32:
                hdr = self._unescape_take(9, deadline)
                if binascii.crc32(hdr[:5]) != int.from_bytes(hdr[5:9], 'little'):
                    continue
                self.last_kind = ZBIN32
                return hdr[0], hdr[1:5]

    def subpacket(self, crc32: bool, timeout: float = 10.0) -> tuple[bytes, int]:
        """Read one data subpacket; returns (data, frame-end ZCRCE/G/Q/W)."""
        deadline = time.monotonic() + timeout
        out = bytearray()
        while True:
            self._need(1, deadline)
            c = self.buf.pop(0)
            if c in (0x11, 0x13, 0x91, 0x93):
                continue
            if c != ZDLE:
                out.append(c)
                continue
            self._need(1, deadline)
            c2 = self.buf.pop(0)
            if c2 in (ZCRCE, ZCRCG, ZCRCQ, ZCRCW):
                crc = self._unescape_take(4 if crc32 else 2, deadline)
                if crc32:
                    ok = binascii.crc32(bytes(out) + bytes([c2])) == int.from_bytes(crc, 'little')
                else:
                    ok = crc16(bytes(out) + bytes([c2])) == int.from_bytes(crc, 'big')
                if not ok:
                    raise ZmodemError('bad subpacket CRC')
                return bytes(out), c2
            if c2 == 0x18:
                raise ZmodemError('sender cancelled (CAN CAN)')
            if c2 == 0x6C:          # ZRUB0
                out.append(0x7F)
            elif c2 == 0x6D:        # ZRUB1
                out.append(0xFF)
            else:
                out.append(c2 ^ 0x40)


_HEX_HDR = re.compile(rb'\*\*\x18B[0-9a-fA-F]{14}\r?[\n\x8a]?\x11?')
_ZFIN_HEX = hex_header(ZFIN, pos_bytes(0))[4:6]           # b'08': the type nibbles


def send_file(t: Transport, data: bytes, name: str, *, block: int = 1024,
              timeout: float = 10.0, progress: Callable[[int, int], None] | None = None,
              send_rz: bool = False, fin_wait: float = 1.0,
              on_extra: Callable[[bytes], None] | None = None) -> None:
    """Send one file. Raises ZmodemError / TimeoutError on failure, after sending the
    abort sequence (a Mobridge receiver has no timeout and would otherwise stay in ZMODEM).

    End of session: the Mobridge firmware (bootloader 0x404dcc/0xffa0ad1c, app 2.11.8
    0x526e42/0x515118) answers ZEOF with ZRINIT and leaves ZMODEM at once; it never answers
    ZFIN. So ZFIN gets at most `fin_wait` seconds, and every byte that is not ZMODEM read
    after the final ZRINIT (the device's MDI frames) goes to `on_extra`, never dropped."""
    try:
        _send_file(t, data, name, block, timeout, progress, send_rz, fin_wait, on_extra)
    except (ZmodemError, TimeoutError):
        abort(t)
        raise


def _send_file(t, data, name, block, timeout, progress, send_rz, fin_wait, on_extra) -> None:
    rd = HeaderReader(t)
    if send_rz:
        t.write(b'rz\r')
    t.write(hex_header(ZRQINIT, pos_bytes(0)))

    # wait for ZRINIT
    for _ in range(10):
        ftype, b4 = rd.read(timeout)
        if ftype == ZRINIT:
            break
        if ftype == ZCHALLENGE:
            t.write(hex_header(ZACK, b4))
        elif ftype in (ZABORT, ZFERR, ZCAN):
            raise ZmodemError('receiver refused session: %s' % FRAME_NAMES[ftype])
        else:
            t.write(hex_header(ZRQINIT, pos_bytes(0)))
    else:
        raise ZmodemError('no ZRINIT from receiver')
    zf0 = b4[3]
    rxbuf = b4[0] | b4[1] << 8
    crc32 = bool(zf0 & CANFC32)
    escctl = bool(zf0 & ESCCTL)

    info = name.encode() + b'\0' + ('%d %o 100644 0 1 %d' % (len(data), int(time.time()), len(data))).encode() + b'\0'
    t.write(bin_header(ZFILE, flag_bytes(zf0=ZCBIN), crc32, escctl) + subpacket(info, ZCRCW, crc32, escctl))

    # A receiver often sends ZRINIT twice (unsolicited, then for our ZRQINIT). Resending
    # ZFILE for the duplicate would earn a second ZRPOS 0 that later reads as a rewind,
    # so duplicates are ignored and ZFILE is only resent on silence.
    pos = None
    for _ in range(4):
        try:
            while pos is None:
                ftype, b4 = rd.read(timeout)
                if ftype == ZRPOS:
                    pos = int.from_bytes(b4, 'little')
                elif ftype == ZSKIP:
                    raise ZmodemError('receiver skipped the file')
                elif ftype in (ZABORT, ZFERR, ZCAN, ZFIN):
                    raise ZmodemError('receiver aborted: %s' % FRAME_NAMES[ftype])
            break
        except TimeoutError:
            t.write(bin_header(ZFILE, flag_bytes(zf0=ZCBIN), crc32, escctl) + subpacket(info, ZCRCW, crc32, escctl))
    if pos is None:
        raise ZmodemError('no ZRPOS for ZFILE')

    # data phase; stop-and-wait (ZCRCW) when the receiver advertises a buffer limit
    while True:
        t.write(bin_header(ZDATA, pos_bytes(pos), crc32, escctl))
        restart = False
        while pos < len(data):
            chunk = data[pos:pos + block]
            last = pos + len(chunk) >= len(data)
            end = ZCRCW if rxbuf else (ZCRCE if last else ZCRCG)
            t.write(subpacket(chunk, end, crc32, escctl))
            pos += len(chunk)
            if progress:
                progress(pos, len(data))
            if end == ZCRCW:
                ftype, b4 = rd.read(timeout)
                if ftype == ZRPOS:
                    pos = int.from_bytes(b4, 'little'); restart = True
                    break
                if ftype != ZACK:
                    raise ZmodemError('unexpected %s during data' % FRAME_NAMES[ftype])
                if not last:
                    t.write(bin_header(ZDATA, pos_bytes(pos), crc32, escctl))
            if last:
                break
        if restart:
            continue
        if not data:
            t.write(subpacket(b'', ZCRCE, crc32, escctl))
        t.write(hex_header(ZEOF, pos_bytes(len(data))))
        ftype, b4 = rd.read(timeout)
        if ftype == ZRPOS:
            pos = int.from_bytes(b4, 'little')
            continue
        if ftype != ZRINIT:
            raise ZmodemError('expected ZRINIT after ZEOF, got %s' % FRAME_NAMES[ftype])
        break

    # end of session: ZFIN, a short bounded wait for a standard receiver's ZFIN, then "OO".
    # Read raw (HeaderReader would eat 0x2A bytes of MDI frames as header starts).
    t.write(hex_header(ZFIN, pos_bytes(0)))
    raw = bytearray(rd.buf)
    rd.buf.clear()
    deadline = time.monotonic() + max(0.0, fin_wait)
    while True:
        m = next((h for h in _HEX_HDR.finditer(raw) if h.group()[4:6] == _ZFIN_HEX), None)
        if m is not None:
            break
        left = deadline - time.monotonic()
        if left <= 0:
            break
        raw += t.read(4096, min(left, 0.1))
    t.write(b'OO')
    if on_extra is not None:
        extra = _HEX_HDR.sub(b'', bytes(raw))   # drop repeated ZRINIT / the ZFIN reply
        if extra:
            on_extra(extra)

CANFDX, CANOVIO = 0x01, 0x02


def receive_file(t: Transport, *, timeout: float = 10.0, max_size: int = 1 << 20,
                 rxinit_flags: int = CANFDX | CANOVIO | CANFC32,
                 send_init: bool = True) -> tuple[str, bytes]:
    """Receive one file from a ZMODEM sender (the device sends temp.ini this way
    after the 0x0282 ack). Returns (name, data). Raises ZmodemError/TimeoutError."""
    rd = HeaderReader(t)
    # exe ZRINIT: ZP0/ZP1 = 0x2800 receive buffer, ZF0 = 0x23 (capture 2026-10-21)
    zrinit = hex_header(ZRINIT, bytes((0x00, 0x28, 0x00, rxinit_flags)))
    if send_init:
        t.write(zrinit)
    name, data, pos = '', bytearray(), 0
    # a stop-and-wait sender opens a new ZDATA header per 1 KiB subpacket
    for _ in range(64 + max_size // 512):
        ftype, b4 = rd.read(timeout)
        crc32 = rd.last_kind == ZBIN32
        if ftype in (ZRQINIT, ZSINIT):
            if ftype == ZSINIT:
                rd.subpacket(crc32, timeout)
                t.write(hex_header(ZACK, pos_bytes(0)))
            else:
                t.write(zrinit)
        elif ftype == ZFILE:
            info, _end = rd.subpacket(crc32, timeout)
            name = info.split(b'\0', 1)[0].decode('latin-1')
            data, pos = bytearray(), 0
            t.write(hex_header(ZRPOS, pos_bytes(0)))
        elif ftype == ZDATA:
            if int.from_bytes(b4, 'little') != pos:
                t.write(hex_header(ZRPOS, pos_bytes(pos)))
                continue
            while True:
                chunk, end = rd.subpacket(crc32, timeout)
                data += chunk; pos += len(chunk)
                if pos > max_size:
                    abort(t)
                    raise ZmodemError('file larger than %d bytes' % max_size)
                if end in (ZCRCQ, ZCRCW):
                    t.write(hex_header(ZACK, pos_bytes(pos)))
                if end in (ZCRCE, ZCRCW):
                    break
        elif ftype == ZEOF:
            if int.from_bytes(b4, 'little') == pos:
                t.write(zrinit)
        elif ftype == ZFIN:
            t.write(ABORT_SEQ + ABORT_SEQ)               # the exe ends with two cancel bursts, no ZFIN
            return name, bytes(data)
        elif ftype in (ZABORT, ZFERR, ZCAN):
            raise ZmodemError('sender aborted: %s' % FRAME_NAMES[ftype])
    raise ZmodemError('ZMODEM receive did not finish')


def abort(t: Transport) -> None:
    t.write(ABORT_SEQ)


def send_path(t: Transport, path: str, **kw) -> None:
    with open(path, 'rb') as f:
        send_file(t, f.read(), os.path.basename(path), **kw)
