"""Bootloader (recovery) protocols, as the official mObridge DSP app drives them.

Exe VAs (image base 0x140000000). Sources: re/analysis/FLASH_JOBS_REGION.md §3,
FLASH_APP_SIDE.md §1-2, RECOVERY_ENTRY_OCT23.md §2-3. Nothing here was captured on a
unit; byte values are read from the exe, the step ORDER follows the job tables.

Language A, BOOTSM text console (identify job table 0x14258edf0):
  FUN_14016fa50  "echo off\\n"   -> any text line ("OK")
  FUN_14016f930  "version\\n"    -> "Prod=0x%x, Ver=%d.%d.%d"
  FUN_14016fb40  exit: host side only, no bytes

Language B, LW single-char tokens (TX table 0x14258eca0, RX table 0x14258ed50):
  FUN_1401736f0  00-frame + 'u' a13d23de  -> 'U' 7c2e45bb   (knock)
  FUN_140173770  'i'                       -> 'I' u8 u16le u8 u8 u8 (product, version)
  FUN_1401739d0  'x'                       -> 'X'            (exit)
  The LW flash job (table 0x142594040: Restart FUN_140182f50, Erase FUN_140182ff0,
  Program FUN_1401830a0, Verify FUN_1401831a0, Final FUN_140183270) is not decoded in
  the RE notes (erase/verify arguments, 'p' block layout), so it is not implemented.

BOOTS flash job (FLASH_JOBS_REGION.md §3), one step per handler:
  FUN_140179d80  open the .fif, 0x400 buffer
  FUN_140179ae0  "\\necho off\\n"            -> any text line
  FUN_140179b50  baud 19200
  FUN_140179bf0  baud 115200, ZMODEM cancel burst 18x8 08x10 (DAT_149d18de8)
  FUN_140179d00  00-frame + 'u' knock       -> 'U' 7c2e45bb (retry: 'u' alone, FUN_140173920)
  FUN_140179ec0  "baud 230400\\n", baud 230400, "ping\\n" -> any text line
  FUN_140179f60  "\\n\\n\\nflash -uart\\n"   -> "READY"
  FUN_14017a030  the whole .fif file, raw, in chunks of <= 0x400 bytes (no ZMODEM)
  FUN_14017a1a0  OPENING / MD5 CHECK / PROGRAMMING / PROGRESS %d / ERROR
  FUN_14017a850  ZMODEM cancel burst again (DAT_149d18e00), exit/reboot

Which unit speaks what (device side is authoritative; BOOTLOADER_DISASM_OCT23.md §5):
  Product 0x202c (DA-G2.Pro A2B, bootloader 2.11.4 read off the reference unit) speaks NEITHER
  language above. PROVEN from the image: its console matches commands with strcmp against
  registered name strings (dispatcher 0xffa07438..0xffa07524), and no "echo", "version",
  "flash", "uart" command, "baud", "READY", "Prod=" or LW magic exists anywhere in it.
  Its only flash path is MDI 0x0201 (handler 0xffa0ba88: reply [01], flash_cb status 0,
  arm a file receive into "/temp.ini" with mdimod_flashfile_received_cb), the same
  structure as the 2.11.x application (FW_FLASH_PATH_OCT22.md), i.e. MDI 0x0201 + raw
  ZMODEM + 0x0200 progress frames = flashflow.run_flash. So BootsFlasher refuses
  MDI_RECOVERY_PRODUCTS; the BOOTS/LW paths stay for other families, unverified on hardware.
  PROVEN (command table 0xff8035c4): 0x0000 auth -> [00 01]; 0x0001 -> 202c 00000000 202c
  00 02 0b 04 (bootloader 2.11.4); 0x0002 BE32 baud in {115200, 230400, 460800} -> [01] at
  the old rate, then switch (UART opens at 115200), so connect's SetBaud 230400 works as on
  the app; 0x0201 -> [01] + 0x0200 sub 1 + ZMODEM receive; 0x0200 = BE16 sub + BE32 arg.
  Commands other than 0x0000/0x0002 need the 0x0000 session first (else 0x0fff).
  INFERRED: the ZFIN answer and the order of fif_flash's progress statuses.
  In the app: startRecovery -> engine.start_recovery -> connect.run_connect(recovery=True)
  (identify only, state 5) -> FIRMWARE RECOVERY -> the FlashFile job (flashflow.run_flash).

The official app only reaches this job in recovery (state 5), after the identify above has
given it the product; the file then passes FUN_1400ab740 (product == device or 0, ...).
BootsFlasher.flash does the same: identify, flashflow.validate in recovery state, flash.
"""
from __future__ import annotations

import re
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

from . import fif
from .commands import Access
from .framing import TextFramer
from .safety import Policy
from .transport import Transport

# Products whose bootloader is proven to have no BOOTS console and no LW knock: recovery
# flashing for them is the MDI 0x0201 + ZMODEM path (flashflow.run_flash), never this module.
MDI_RECOVERY_PRODUCTS = frozenset({0x202c})
MDI_ONLY = ('product %#06x: its bootloader has no BOOTS/LW console (flash -uart does not '
            'exist); recover it with the MDI 0x0201 + ZMODEM flash instead (app: Recovery state, '
            'FIRMWARE RECOVERY)')

# The 0x202c recovery bytes, pinned (BOOTLOADER_DISASM_OCT23.md §5). Frame = AA LEN16 CMD16
# payload CK (framing.MdiFramer). Probe/identify are the same commands the app answers;
# the flash start is the bare 0x0201 the bootloader handler 0xffa0ba84 answers with [01],
# then raw ZMODEM (mobridge.zmodem) and 0x0200 progress, driven by flashflow.run_flash.
MDI_PROBE = bytes.fromhex('aa0000000000')        # 0x0000
MDI_IDENTIFY = bytes.fromhex('aa00000001ff')     # 0x0001 (mdimod_general_auth)
MDI_FLASH_START = bytes.fromhex('aa00000201fd')  # 0x0201 (flashfile), empty payload
MDI_FLASH_START_REPLY = b'\x01'                  # handler sets reply len 1, byte 1

CANCEL_BURST = b'\x18' * 8 + b'\x08' * 10      # DAT_149d18de8 / DAT_149d18e00
ECHO_OFF_FLASH = b'\necho off\n'               # FUN_140179ae0
ECHO_OFF = b'echo off\n'                       # FUN_14016fa50
VERSION = b'version\n'                         # FUN_14016f930
BAUD_CMD = b'baud 230400\n'                    # FUN_140179ec0 "baud %d\n", 0x38400
PING = b'ping\n'
ENTER_FLASH = b'\n\n\nflash -uart\n'           # FUN_140179f60
BAUD_CONSOLE, BAUD_CMD_RATE, BAUD_FLASH = 19200, 115200, 230400
CHUNK = 0x400                                  # FUN_14017a030 min(txfree, 0x400)

LW_KNOCK_ARG = 0xa13d23de                      # 'u' argument (FUN_14016f230, u32 LE)
LW_MAGIC = 0x7c2e45bb                          # 'U' reply word (FUN_14016f260)
LW_NULL = b'\x00' * 66                         # char 0x00, FUN_14016f210
LW_KNOCK = b'u' + struct.pack('<I', LW_KNOCK_ARG)
LW_IDENT = b'i'
LW_EXIT = b'x'
# RX token table 0x14258ed50: first char -> bytes that follow ('D' is up to 0x20, NUL-ended)
LW_RX = {'U': 4, 'I': 6, 'R': 1, 'E': 1, 'P': 1, 'V': 1, 'F': 1, 'X': 0, 'D': 0x20}

_PROD = re.compile(r'Prod=0x([0-9a-fA-F]+),\s*Ver=(\d+)\.(\d+)\.(\d+)')
_PROGRESS = re.compile(r'PROGRESS\s+(\d+)(?:\s*:\s*(\d+)\s*/\s*(\d+))?', re.I)


@dataclass
class Token:
    char: str
    data: bytes

    @property
    def word(self) -> int:          # 'U': LE u32 after the char (FUN_14016f260)
        return struct.unpack('<I', self.data[:4])[0]


class TokenParser:
    """FUN_14016f5c0: byte-fed tokenizer. A byte matching a table entry starts a token of
    that entry's fixed length; other bytes are skipped."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[Token]:
        self.buf += data
        out = []
        while self.buf:
            c = chr(self.buf[0])
            if c not in LW_RX:
                del self.buf[0]
                continue
            if c == 'D':
                end = self.buf.find(b'\0', 1, 2 + LW_RX['D'])
                if end < 0 and len(self.buf) < 1 + LW_RX['D']:
                    break
                n = end if end >= 0 else LW_RX['D']
                out.append(Token(c, bytes(self.buf[1:n + 1]).rstrip(b'\0')))
                del self.buf[:n + 1]
                continue
            n = LW_RX[c]
            if len(self.buf) < 1 + n:
                break
            out.append(Token(c, bytes(self.buf[1:1 + n])))
            del self.buf[:1 + n]
        return out


@dataclass
class Identity:
    product: int
    version: tuple
    language: str        # 'text' (BOOTSM) or 'lw'


@dataclass
class BootsStatus:
    state: str = 'idle'   # idle|ready|opening|md5|programming|progress|complete|error
    percent: int | None = None
    done: int | None = None
    total: int | None = None
    sent: int = 0
    lines: list = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.state in ('complete', 'error')


def parse_line(line: str, st: BootsStatus) -> BootsStatus:
    """FUN_14017a1a0 (WaitComplete) line matcher."""
    s = line.strip()
    up = s.upper()
    if not s:
        return st
    st.lines.append(s)
    if 'ERROR' in up:
        st.state = 'error'
    elif up.startswith('READY'):
        st.state = 'ready'
    elif 'OPENING' in up:
        st.state = 'opening'
    elif 'MD5 CHECK' in up:
        st.state = 'md5'
    elif 'PROGRAMMING' in up:
        st.state = 'programming'
    elif (m := _PROGRESS.search(s)):
        st.state = 'progress'
        st.percent = int(m.group(1))
        if m.group(2):
            st.done, st.total = int(m.group(2)), int(m.group(3))
        # No BOOTS-family "done" line is known (INFERRED): 100% (or done == total) finishes.
        if st.percent >= 100 or (st.total and st.done == st.total):
            st.state = 'complete'
    return st


def plan(path: str, expect_product: int | None = None) -> tuple[fif.FifInfo, bytes, list[str]]:
    info, data = fif.load(path)
    return info, data, fif.flash_problems(info, expect_product)


class BootsFlasher:
    def __init__(self, t: Transport, policy: Policy, log: Callable[[str], None] = print):
        self.t = t
        self.policy = policy
        self.log = log
        self.text = TextFramer()
        self.tokens = TokenParser()

    # -- low level -------------------------------------------------------------------
    def _baud(self, rate: int) -> None:
        self.t.set_baud(rate)                 # FUN_14015c680

    def _line(self, timeout: float, want: Callable[[str], bool] = lambda s: True) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for fr in self.text.feed(self.t.read(4096, 0.1)):
                s = fr.text.strip()
                if s:
                    self.log(s)
                    if want(s):
                        return s
        raise TimeoutError('BOOTS: no reply line')

    def _token(self, timeout: float, want: Callable[[Token], bool]) -> Token:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for tok in self.tokens.feed(self.t.read(4096, 0.1)):
                if want(tok):
                    return tok
        raise TimeoutError('LW: no reply token')

    def _knock(self, timeout: float) -> None:
        """FUN_140179d00 / FUN_1401736f0: 00-frame + 'u', wait 'U'+magic; on timeout
        FUN_140173920 sends 'u' alone once more."""
        is_magic = lambda k: k.char == 'U' and k.word == LW_MAGIC
        self.t.write(LW_NULL + LW_KNOCK)
        try:
            self._token(timeout, is_magic)
        except TimeoutError:
            self.t.write(LW_KNOCK)
            self._token(timeout, is_magic)

    # -- recovery identify / exit ----------------------------------------------------------
    def identify_text(self, timeout: float = 2.0) -> Identity:
        """Language A (job table 0x14258edf0): echo off -> line, version -> Prod=..."""
        self.t.flush_input()
        self.t.write(ECHO_OFF)
        self._line(timeout)
        self.t.write(VERSION)
        m = _PROD.search(self._line(timeout, lambda s: bool(_PROD.search(s))))
        return Identity(int(m.group(1), 16), tuple(int(m.group(i)) for i in (2, 3, 4)), 'text')

    def identify_lw(self, timeout: float = 2.0) -> Identity:
        """Language B: knock, then 'i' -> 'I' {u8, u16le product, u8 u8 u8 version}
        (FUN_140173770, parser FUN_14016f290)."""
        self.t.flush_input()
        self.tokens = TokenParser()
        self._knock(timeout)
        self.t.write(LW_IDENT)
        tok = self._token(timeout, lambda k: k.char == 'I')
        _, prod, a, b, c = struct.unpack('<BHBBB', tok.data)
        return Identity(prod, (a, b, c), 'lw')

    def identify(self, timeout: float = 2.0) -> Identity:
        """Try the text console, then the LW knock. Order is this module's choice (the exe's
        UNKNOWNM auto-detect order is not decoded); neither probe writes flash, and a unit
        that answers neither (e.g. 0x202c, which speaks MDI) times out on both."""
        try:
            return self.identify_text(timeout)
        except TimeoutError:
            return self.identify_lw(timeout)

    def exit_recovery(self, ident: Identity, timeout: float = 2.0) -> bool:
        """Text: host side only (FUN_14016fb40). LW: 'x' -> 'X' (FUN_1401739d0)."""
        if ident.language != 'lw':
            return True
        self.t.write(LW_EXIT)
        try:
            self._token(timeout, lambda k: k.char == 'X')
            return True
        except TimeoutError:
            return False

    # -- flash -----------------------------------------------------------------------
    @staticmethod
    def check(path: str, product: int, version: tuple) -> list[str]:
        """FUN_1400ab740 in recovery state (codes 0/1 go ahead, as scripting flashFile),
        plus fif.bootblock_problem."""
        from . import flashflow, uirules
        p = flashflow.validate(path, product, version, uirules.STATE_RECOVERY)
        problems = [] if p.code in (flashflow.OK, flashflow.OLDER) else [flashflow.message(p)]
        return problems + list(p.guard or [])

    def flash(self, path: str, *, execute: bool = False, expect_product: int | None = None,
              line_timeout: float = 6.0, ready_timeout: float = 10.0,
              program_timeout: float = 300.0) -> BootsStatus:
        info, data, problems = plan(path, expect_product)
        self.log(fif.describe(info))
        fam = info.product or expect_product
        if fam in MDI_RECOVERY_PRODUCTS:
            problems.append(MDI_ONLY % fam)
        if problems:
            raise ValueError('not flashing: ' + '; '.join(problems))
        if not execute:
            self.log('DRY RUN: image is valid. Would identify the unit, then send %d bytes raw '
                     'after %r at %d baud.' % (len(data), ENTER_FLASH, BAUD_FLASH))
            return BootsStatus('idle')
        self.policy.check(Access.WRITE, 'firmware flash')
        ident = self.identify(line_timeout)
        self.log('recovery: product %#06x, version %d.%d.%d' % ((ident.product,) + ident.version))
        problems = self.check(path, ident.product, ident.version)
        if ident.product in MDI_RECOVERY_PRODUCTS:
            problems.append(MDI_ONLY % ident.product)
        if problems:
            raise ValueError('not flashing: ' + '; '.join(problems))
        return self._flash_job(data, line_timeout, ready_timeout, program_timeout)

    def _flash_job(self, data: bytes, line_timeout: float, ready_timeout: float,
                   program_timeout: float) -> BootsStatus:
        st = BootsStatus()
        t = self.t
        self.tokens = TokenParser()
        t.flush_input()
        t.write(ECHO_OFF_FLASH)                       # FUN_140179ae0
        self._line(line_timeout)
        self._baud(BAUD_CONSOLE)                      # FUN_140179b50
        self._baud(BAUD_CMD_RATE)                     # FUN_140179bf0
        t.write(CANCEL_BURST)
        self._knock(line_timeout)                     # FUN_140179d00
        t.write(BAUD_CMD)                             # FUN_140179ec0
        self._baud(BAUD_FLASH)
        t.write(PING)
        self._line(line_timeout)
        t.write(ENTER_FLASH)                          # FUN_140179f60
        self._line(ready_timeout, lambda s: s.startswith('READY'))
        st.state = 'ready'
        try:
            for off in range(0, len(data), CHUNK):    # FUN_14017a030
                t.write(data[off:off + CHUNK])
                st.sent = min(off + CHUNK, len(data))
                for fr in self.text.feed(t.read(4096, 0)):
                    parse_line(fr.text, st)
                if st.state == 'error':
                    return st
            deadline = time.monotonic() + program_timeout   # FUN_14017a1a0
            while not st.finished:
                if time.monotonic() > deadline:
                    raise TimeoutError('BOOTS: timed out in state %s' % st.state)
                for fr in self.text.feed(t.read(4096, 0.1)):
                    self.log(fr.text)
                    parse_line(fr.text, st)
        finally:
            t.write(CANCEL_BURST)                     # FUN_14017a850
        return st
