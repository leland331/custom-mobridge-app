"""High-level client: what a GUI or the CLI calls.

Read flows (flash export, CRC verify) are written against the job layer, so
they start working the moment the framer and wiremap.json are filled in.
"""
from __future__ import annotations

import binascii
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .framing import Frame, Framer, TextFramer, UnconfirmedMdiFramer
from .jobs import JobRunner
from .message import decode_number, encode_number
from .modes import DeviceMode
from .safety import Policy, check_flash_range
from .transport import Transport

FLASH_REGIONS = {
    # name: (offset, size). From the live device log + .fif header (NOTES.md 11a).
    'bootblock': (0x000000, 0x060000),
    'app': (0x060000, 0x3A0000),
    'all': (0x000000, 0x400000),
}

_INFO_KEYS = ('Name', 'Application', 'Product ID', 'DSP', 'Build', 'Git SHA', 'VEH')
# TODO(capture): exact log line format unknown; this matches "Key: value" / "Key = value" loosely.
_INFO_RX = re.compile(r'(%s)\s*[:=]\s*(.+)' % '|'.join(re.escape(k) for k in _INFO_KEYS), re.I)


@dataclass
class DetectResult:
    mode: DeviceMode
    note: str
    bytes_seen: int = 0
    text_lines: list = field(default_factory=list)
    info: dict = field(default_factory=dict)


def classify_traffic(data: bytes) -> tuple[DeviceMode, str]:
    if not data:
        return DeviceMode.UNKNOWN, ('silent: device idle in BOOTS, in MDI with nothing to say, '
                                    'or not powered. Nothing was sent to find out.')
    printable = sum(1 for c in data if 32 <= c < 127 or c in (9, 10, 13))
    if printable / len(data) > 0.95:
        return DeviceMode.UNKNOWN, ('text stream: application firmware is running and streaming its '
                                    'debug log (the state seen on connect, before the app switches to MDI)')
    return DeviceMode.UNKNOWN, 'binary traffic: possibly MDI frames. Record it with --capture for the framing work.'


class MobridgeClient:
    def __init__(self, transport: Transport, framer: Framer | None = None, policy: Policy | None = None,
                 wiremap: dict | None = None, on_frame: Callable[[Frame], None] | None = None,
                 require_parity: bool = True):
        self.t = transport
        self.policy = policy or Policy.read_only()
        self.on_frame = on_frame or (lambda f: None)
        self.jobs = JobRunner(transport, framer or UnconfirmedMdiFramer(), self.policy, wiremap,
                              on_frame=self.on_frame, require_parity=require_parity)

    # ---- passive ----------------------------------------------------------
    def listen(self, seconds: float = 3.0, echo: Callable[[str], None] | None = None) -> DetectResult:
        """Listen without sending anything and guess what state the device is in."""
        tf = TextFramer()
        raw = bytearray()
        lines = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.t.read(4096, 0.1)
            raw += chunk
            for fr in tf.feed(chunk):
                lines.append(fr.text)
                if echo:
                    echo(fr.text)
        lines += [f.text for f in tf.flush()]
        mode, note = classify_traffic(bytes(raw))
        info = {}
        for ln in lines:
            m = _INFO_RX.search(ln)
            if m:
                info.setdefault(m.group(1), m.group(2).strip())
        return DetectResult(mode, note, len(raw), lines, info)

    # ---- raw console (bootloader's uart console, not MDI) ------------------
    def console_send(self, line: str) -> None:
        self.policy.check_console(line)
        self.t.write(TextFramer.encode_line(line))

    # ---- proven frames --------------------------------------------------------
    def send_frame(self, frame: bytes) -> None:
        """Write one already-framed MDI frame. It must clear the parity gate
        (byte-exact reference, authorised read, or a write proven identical)."""
        from . import parity
        if self.jobs.require_parity:
            parity.assert_frame_cleared(frame, None, 'send_frame')
        self.t.write(frame)

    def debug_send(self, line: str) -> None:
        """Console text over MDI the way the app does it: one 0x0401 frame per
        byte (captured). Policed by the console safety policy first, so a
        destructive console command never leaves even though 0x0401 is proven."""
        from . import wire_apply
        self.policy.check_console(line)
        for fr in wire_apply.debug_send_frames(line):
            self.send_frame(fr)

    # ---- MDI jobs ---------------------------------------------------------
    def job(self, name: str, data: bytes = b'', timeout: float = 2.0):
        """Run any of the 43 catalogued jobs by name (see commands.COMMANDS)."""
        return self.jobs.call(name, data, timeout)

    def ping(self):
        return self.jobs.call('Ping')

    def sw_version(self) -> str:
        return self.jobs.call('SWVersion').data.decode('latin-1').rstrip('\0')

    # TODO(decompile): request payload layouts below are ASSUMED (addr u32, len u16 / u32).
    # They are never sent until wiremap.json has real ids, and must be checked then.
    def flash_read(self, addr: int, size: int) -> bytes:
        check_flash_range(addr, size, writing=False)
        r = self.jobs.call('FlashRead', encode_number(addr, 4) + encode_number(size, 2))
        return r.data[:size]

    def flash_crc(self, addr: int, size: int) -> int:
        r = self.jobs.call('FlashCrc', encode_number(addr, 4) + encode_number(size, 4))
        return decode_number(r.data, 0, 4)

    def export_flash(self, addr: int, size: int, path: str, chunk: int = 256,
                     progress: Callable[[int, int], None] | None = None, verify: bool = True) -> bytes:
        out = bytearray()
        while len(out) < size:
            n = min(chunk, size - len(out))
            part = self.flash_read(addr + len(out), n)
            if len(part) != n:
                raise IOError('short read at %#x: %d of %d bytes' % (addr + len(out), len(part), n))
            out += part
            if progress:
                progress(len(out), size)
        if verify:
            dev = self.flash_crc(addr, size)
            mine = binascii.crc32(out)
            if dev != mine:
                raise IOError('CRC mismatch: device %08x, read %08x' % (dev, mine))
        with open(path, 'wb') as f:
            f.write(out)
        return bytes(out)
