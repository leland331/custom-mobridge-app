"""Rear Chimes: the extra mono input that custom firmware LB-03 (2.11.11) adds.

Wire format (docs/chime-routing/REAR_CHIMES_BUILD.md §7.9 on fw/rear-chimes):
- level = entry k = 6 of 0x0704 sub 07 (the app already sends all 64 entries);
- routing = bit 6 of each output's u64 input mask in the routing write (0x0701 here,
  read back with 0x0711);
- no volume control (fixed in firmware, like Beeps).

Nothing here sends anything; it only decides what the app shows and plans edits.
On stock or older custom firmware the app never sets bit 6 itself, so its writes
stay byte-for-byte what they were.
"""
from __future__ import annotations

import re

NAME = 'Rear Chimes'
BIT = 6                    # routing mask bit
GAIN_K = 6                 # 0x0704 sub 07 entry

_TAG_LINE = re.compile(r'Build Time\s*:\s*(.*\S)')
_RC_TAG = re.compile(r'LB-RC\d*')
TAG = _RC_TAG              # used by features.FEATURES


def build_tag(lines) -> str | None:
    """The firmware's build tag from its boot banner ("Build Time : Jul 17 2026 LB-03").
    Stock prints a clock time there ("12:57:20"); our LB-NN builds (and LB-01/02's internal "LB-PAC01"/"LB-PAC02") have no Rear Chimes.
    The tag (0x4EF934) is only in the boot log, so it is None unless the banner was seen."""
    tag = None
    buf = ''.join(lines or ())
    for ln in buf.replace('\r', '\n').split('\n'):
        m = _TAG_LINE.search(ln)
        if m:
            tag = m.group(1).split()[-1]
    return tag


def detect(tag: str | None = None) -> bool:
    """True when the unit runs firmware with the Rear Chimes input.
    Rear Chimes is a future build tagged LB-RC<n>; LB-03 2.11.11 is the turn-off snap fix.
    Detection returns True only when the build tag matches LB-RC<n>."""
    if tag:
        return bool(_RC_TAG.fullmatch(tag.strip()))
    return False
