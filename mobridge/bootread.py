"""Read the bootloader (SPI flash 0x0-0x60000) back from the unit, read only.

Stage 1 of the bootloader recovery test (thread "Bootloader recovery test"):
the bootloader is not inside any .fif or ELF we have, so before anyone enters
recovery mode we read it back and disassemble it in the cloud.

What goes on the wire, and nothing else (ALLOWED below; anything else raises
before a byte leaves):
  - the ZMODEM cancel burst the connect starts with (clears a stale transfer)
  - 0x0000 session claim, 0x0001 identify
  - 0x0203 region CRC and 0x0202 region read, region 0 only, inside 0x0-0x60000
  - ZMODEM receiver control headers (ZRINIT/ZRPOS/ZACK) while the unit sends data.bin

Firmware 2.11.8 behaviour (re/analysis via FW_MDI_HANDLERS_OCT22, handler
0x519F54, re-read 2026-10-23): request [u8 region][u32 offset][u32 len] BE,
len capped at 1 MiB; region 0 = absolute flash address. The handler copies
the range with the SPI driver's read entry (vtable 0x4E2FE8 +0xC, SPI opcode
0x0B FAST READ) into a RAM file, replies [region][offset][len][total] (LEN 13),
then ZMODEM-sends that file as "data.bin". 0x0203 (0x51A0C6) takes the same
request and replies [region][offset][len][u32 CRC-32]. Neither path erases or
programs flash.

Kept as its own module so it can be dropped without touching the app.
"""
from __future__ import annotations

import binascii
import hashlib
import json
import os
import struct
import sys
import time

from . import zmodem
from .framing import MdiFramer
from .message import MdiMessage

BOOT_START, BOOT_END = 0x0, 0x60000
CHUNK = 0x10000                 # 64 KiB per read; 6 reads for the whole bootloader
REGION_ABS = 0
PRODUCT = 0x202C
ALLOWED = {0x0000, 0x0001, 0x0202, 0x0203}
CANCEL = bytes([0x18] * 8 + [0x08] * 10)
BAUDS = (115200, 230400)        # power-on rate first; 230400 if an app left it raised


class BootReadError(RuntimeError):
    pass


def region_request(offset: int, length: int) -> bytes:
    """[u8 region 0][u32 offset][u32 len], big-endian; refuses anything outside the bootloader."""
    if not (BOOT_START <= offset and length > 0 and offset + length <= BOOT_END):
        raise BootReadError('refusing range %#x+%#x: outside 0x0-%#x' % (offset, length, BOOT_END))
    return struct.pack('>BII', REGION_ABS, offset, length)


class Link:
    """MDI request/reply over a transport, limited to ALLOWED commands."""

    def __init__(self, transport, log=None, timeout=2.0):
        self.t = transport
        self.fr = MdiFramer()
        self.log = log or (lambda s: None)
        self.timeout = timeout
        self.sent = []

    def ask(self, cmd: int, payload: bytes = b'', timeout: float | None = None, rest: bytearray | None = None):
        if cmd not in ALLOWED:
            raise BootReadError('command 0x%04x is not allowed in the bootloader reader' % cmd)
        if cmd in (0x0202, 0x0203):
            region_request(*struct.unpack('>xII', payload))    # re-check the range
        frame = self.fr.encode(MdiMessage(cmd=cmd, data=payload))
        self.sent.append(cmd)
        self.t.write(frame)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            frames = self.fr.feed(self.t.read(4096, 0.05))
            for k, f in enumerate(frames):
                if f.kind == 'mdi' and f.message and f.message.cmd in (cmd, 0x0FFF):
                    if rest is not None:     # bytes after the reply in the same read: the ZMODEM start
                        rest += b''.join(x.data for x in frames[k + 1:]) + bytes(self.fr._buf)
                        self.fr._buf.clear()
                    if f.message.cmd == 0x0FFF:
                        code = int.from_bytes(f.message.data[:2], 'big') if len(f.message.data) >= 2 else -1
                        raise BootReadError('unit refused 0x%04x (error code %d)' % (cmd, code))
                    return f.message
        return None


class _Prefixed:
    def __init__(self, t, head: bytes):
        self.t, self.head = t, bytearray(head)

    def read(self, n=4096, timeout=None):
        if self.head:
            out = bytes(self.head[:n]); del self.head[:n]
            return out
        return self.t.read(n, timeout)

    def write(self, b):
        self.t.write(b)


def open_session(link: Link, log) -> dict:
    """Cancel burst, session claim, identify. Tries 115200 then 230400."""
    for baud in BAUDS:
        try:
            link.t.set_baud(baud)
        except NotImplementedError:
            pass
        link.t.write(CANCEL)
        time.sleep(0.05)
        link.t.flush_input()
        link.fr = MdiFramer()
        claim = link.ask(0x0000)
        if claim is None:
            log('no answer at %d baud' % baud)
            continue
        ident = link.ask(0x0001)
        if ident is None or len(ident.data) < 2:
            raise BootReadError('unit answered the session claim but not identify')
        product = int.from_bytes(ident.data[0:2], 'big')
        version = ('%d.%d.%d' % tuple(ident.data[9:12])) if len(ident.data) >= 12 else '?'
        log('connected at %d baud: product %04x, firmware %s' % (baud, product, version))
        if product != PRODUCT:
            raise BootReadError('product %04x is not the DA-G2.Pro A2B Ford (202c); stopping' % product)
        return {'baud': baud, 'product': '%04x' % product, 'firmware': version}
    raise BootReadError('no answer from the unit. Is it powered, on this port, and is the official app closed?')


def settle(t, quiet: float = 0.3, limit: float = 3.0) -> None:
    """Let the unit's ZMODEM sender wind down: drop input until `quiet` s of silence.
    The unit routes every incoming byte to ZMODEM while a transfer is open."""
    end = time.monotonic() + limit
    while time.monotonic() < end and t.read(4096, quiet):
        pass


def device_crc(link: Link, offset: int, length: int) -> int:
    req = region_request(offset, length)
    rep = None
    for _ in range(3):           # a CRC request is harmless to repeat
        rep = link.ask(0x0203, req, timeout=3.0)
        if rep is not None:
            break
        settle(link.t)
    if rep is None or len(rep.data) != 13 or rep.data[:9] != req:
        raise BootReadError('bad CRC reply for %#x+%#x: %r' % (offset, length, rep and rep.data.hex()))
    return int.from_bytes(rep.data[9:13], 'big')


def read_chunk(link: Link, offset: int, length: int, zmodem_timeout: float) -> bytes:
    req = region_request(offset, length)
    rest = bytearray()
    rep = link.ask(0x0202, req, timeout=10.0, rest=rest)
    if rep is None:
        raise BootReadError('no reply to the read of %#x+%#x' % (offset, length))
    if len(rep.data) != 13 or rep.data[:9] != req:
        raise BootReadError('unexpected read reply %s' % rep.data.hex())
    total = int.from_bytes(rep.data[9:13], 'big')
    try:
        name, data = zmodem.receive_file(_Prefixed(link.t, bytes(rest)), timeout=zmodem_timeout,
                                         max_size=length)
    except Exception:
        zmodem.abort(link.t)
        raise
    finally:
        settle(link.t)
        link.fr = MdiFramer()
    if len(data) != length or total != length:
        raise BootReadError('got %d bytes (unit said %d), wanted %d' % (len(data), total, length))
    return data


def read_bootloader(transport, log=None, *, chunk: int = CHUNK, tries: int = 3,
                    zmodem_timeout: float = 15.0) -> tuple[bytes, dict]:
    """Read 0x0-0x60000. Each chunk is checked against the unit's own CRC-32 (0x0203)
    and re-read on a mismatch. Returns (image, report)."""
    log = log or (lambda s: None)
    link = Link(transport, log)
    report = open_session(link, log)
    report['chunks'] = []
    image = bytearray()
    for off in range(BOOT_START, BOOT_END, chunk):
        n = min(chunk, BOOT_END - off)
        want = device_crc(link, off, n)
        reads = []
        for attempt in range(1, tries + 1):
            try:
                data = read_chunk(link, off, n, zmodem_timeout)
            except (BootReadError, zmodem.ZmodemError, TimeoutError) as e:
                log('  %#07x: read %d failed: %s' % (off, attempt, e))
                time.sleep(0.5)
                transport.flush_input()
                continue
            got = binascii.crc32(data)
            if got == want:
                break
            log('  %#07x: CRC %08x, unit says %08x; reading again' % (off, got, want))
            reads.append(data)
            # two identical reads that both disagree with the unit's CRC: keep them,
            # flag it (the CRC routine may differ from zlib's), do not loop forever
            if len(reads) >= 2 and reads[-1] == reads[-2]:
                break
        else:
            raise BootReadError('could not read %#x+%#x after %d tries' % (off, n, tries))
        ok = binascii.crc32(data) == want
        report['chunks'].append({'offset': '%#x' % off, 'length': n, 'crc_unit': '%08x' % want,
                                 'crc_read': '%08x' % binascii.crc32(data), 'match': ok})
        image += data
        log('  %#07x-%#07x  %s' % (off, off + n, 'OK' if ok else 'read twice, same bytes, CRC differs'))
    report['size'] = len(image)
    report['sha256'] = hashlib.sha256(image).hexdigest()
    report['crc32'] = '%08x' % binascii.crc32(image)
    report['all_crc_match'] = all(c['match'] for c in report['chunks'])
    report['commands_sent'] = sorted('0x%04x' % c for c in set(link.sent))
    return bytes(image), report


def _pick_port() -> str:
    from .transport import list_ports
    ftdi = [p for p, _d, is_ftdi in list_ports() if is_ftdi]
    return ftdi[0] if len(ftdi) == 1 else 'COM3'


def main(argv=None) -> int:
    import argparse
    from .transport import open_transport
    ap = argparse.ArgumentParser(description='Read the Mobridge bootloader (flash 0x0-0x60000). Read only.')
    ap.add_argument('--port', help='serial port or network port such as rfc2217://host:4000 (default: the one FTDI port, else COM3)')
    from .sitecfg import captures_dir
    ap.add_argument('--out', default=captures_dir(),
                    help='folder for the results')
    a = ap.parse_args(argv)
    port = a.port or _pick_port()
    os.makedirs(a.out, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    base = os.path.join(a.out, 'bootloader-%s' % stamp)

    def log(s):
        print(s, flush=True)

    log('Mobridge bootloader read (read only). Port %s.' % port)
    log('This reads flash 0x0-0x60000 and changes nothing on the unit.')
    try:
        t = open_transport(port, capture=base + '.wire.jsonl')
    except Exception as e:
        log('Could not open %s: %s' % (port, e))
        log('Close the official app and Custom Mobridge, check the USB cable, then try again.')
        return 2
    t0 = time.monotonic()
    try:
        image, report = read_bootloader(t, log)
    except Exception as e:
        log('STOPPED: %s' % e)
        log('Nothing was changed on the unit. The wire log is in %s.wire.jsonl' % base)
        return 1
    finally:
        t.close()
    report['seconds'] = round(time.monotonic() - t0, 1)
    report['port'] = port
    report['utc'] = stamp
    with open(base + '.bin', 'wb') as f:
        f.write(image)
    with open(base + '.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    log('')
    log('DONE in %.0f s. Saved:' % report['seconds'])
    log('  %s.bin  (%d bytes, sha256 %s)' % (base, len(image), report['sha256'][:16]))
    log('  %s.json' % base)
    if not report['all_crc_match']:
        log('Note: some chunks did not match the unit\'s CRC (see the .json). Tell Claude.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
