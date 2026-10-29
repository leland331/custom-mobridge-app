"""Safety policy. Every outgoing job and console line goes through here.

Rules carried over from the recovery kit (RECOVERY_README.md):
  1. never raw-write or erase the bootblock 0x0-0x60000 (no override exists)
  2. always keep a known-good .fif
  3. dry-run a recovery reflash before the first custom flash
Default policy is read-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .commands import Access, Command
from .fif import BOOTBLOCK_END


class SafetyError(PermissionError):
    pass


def overlaps_bootblock(addr: int, size: int) -> bool:
    return addr < BOOTBLOCK_END and addr + max(size, 1) > 0


def check_flash_range(addr: int, size: int, writing: bool) -> None:
    if writing and overlaps_bootblock(addr, size):
        raise SafetyError('refusing to write/erase %#x+%#x: overlaps the bootblock 0x0-%#x'
                          % (addr, size, BOOTBLOCK_END))


_NUM = r'(0x[0-9a-fA-F]+|\d+)'
_CONSOLE_RULES = [
    # device console commands from hw_debug.c / nvm menus (NOTES.md 11b)
    (re.compile(r'^\s*(hw\s+)?spiflash\s+(erase|write)\s+' + _NUM + r'(\s+' + _NUM + r')?', re.I), Access.DESTRUCTIVE),
    (re.compile(r'^\s*(hw\s+)?spiflash\s+(erase|write)\b', re.I), Access.DESTRUCTIVE),
    (re.compile(r'^\s*(hw\s+)?spiflash\s+(read|regs)\b', re.I), Access.READ),
    (re.compile(r'^\s*nvm\s+(clear|set)\b', re.I), Access.WRITE),
    (re.compile(r'^\s*nvm\s+list\b', re.I), Access.READ),
    (re.compile(r'^\s*(hw\s+)?(crash|reset|watchdog)\b', re.I), Access.DESTRUCTIVE),
    (re.compile(r'^\s*flash\b', re.I), Access.WRITE),
    (re.compile(r'^\s*(hw\s+)?gpio\s+set\b', re.I), Access.WRITE),   # 'gpio set' changes a hw line: a write, not a read
    (re.compile(r'^\s*(hw\s+)?(clock|memory|gpio|mdidebug|dspdebug|listdsp|help|\?)\b', re.I), Access.READ),
    (re.compile(r'^\s*cleardsp\b', re.I), Access.WRITE),
    (re.compile(r'^\s*(ping)?\s*$', re.I), Access.READ),
]


def classify_console(line: str) -> Access:
    """Unknown console commands are treated as WRITE."""
    m = _CONSOLE_RULES[0][0].match(line)
    if m:
        addr = int(m.group(3), 0)
        size = int(m.group(5), 0) if m.group(5) else 1
        check_flash_range(addr, size, writing=True)
        return Access.DESTRUCTIVE
    for rx, access in _CONSOLE_RULES[1:]:
        if rx.match(line):
            return access
    return Access.WRITE


@dataclass
class Policy:
    allowed: set = field(default_factory=lambda: {Access.READ, Access.SESSION})

    @classmethod
    def read_only(cls) -> 'Policy':
        return cls()

    @classmethod
    def allow(cls, *levels: Access) -> 'Policy':
        return cls({Access.READ, Access.SESSION, *levels})

    def check(self, access: Access, what: str) -> None:
        if access not in self.allowed:
            raise SafetyError('%s is %s; policy allows only %s (use --allow %s)'
                              % (what, access.value, ', '.join(sorted(a.value for a in self.allowed)), access.value))

    def check_command(self, cmd: Command) -> None:
        self.check(cmd.access, 'MDI job %s' % cmd.name)

    def check_console(self, line: str) -> None:
        self.check(classify_console(line), 'console command %r' % line.strip())
