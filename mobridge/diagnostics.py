"""Automated, READ-ONLY diagnostic capture for the stuck / parked-amp wake fault.

Reproduces the wake-fault thread's console recipe as a one-click action:
/mnt/project-files/fault-capture/CONSOLE_RECIPE.md (owner: "Parked amp wake
fault"). Open the dprint log-stream menu, enable the streams, dump the A2B
registers, grab the build banner, while the device streams its log over USB
serial; save the streamed text to a file.

PARITY POSTURE (this is why the in-app button is DISABLED today)
---------------------------------------------------------------
Our app cannot send console text yet: the DebugSend wire mapping is unconfirmed
(candidate cmd 0x0e00, listed in wiremap.json `_unmapped_jobs`). Per the app's
rule we never send bytes we have not byte-matched to the official app, so this
action is BUILT but GATED OFF -- `can_run()` returns False until DebugSend is
mapped in wiremap.json AND a USBPcap of the official app performing it clears the
frame. Until then the user runs the official-app driver,
`/mnt/project-files/fault-capture/fault_capture.py`, which also produces exactly
that capture (`--usbpcap`) so the native button can later be unlocked.

SAFETY (independent of the gate)
--------------------------------
Everything here is logging-only and resets on reboot (the dprint stream toggles
are RAM-only). The sequence is a fixed whitelist of read-only menu tokens; each
is additionally run through `safety.classify_console` and REFUSED if it is
destructive, and the recipe's hard-block list can never appear in it. Nothing in
this module writes DSP/NVM/flash or changes device state.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import safety
from .commands import Access


@dataclass(frozen=True)
class Step:
    token: str        # the exact console line typed (without the trailing Enter)
    purpose: str
    wait_s: float     # how long to record before the next line
    tag: str          # confidence tag from the recipe: C / L / U


# The confirmed sequence, verbatim from CONSOLE_RECIPE.md. `all` enables every
# stream (the numbering is only printed at runtime, so we do not pick numbers).
STEPS: tuple[Step, ...] = (
    Step('d',       'open the dprint (log stream) menu', 1.5, 'L'),
    Step('all',     'enable every log stream',           1.5, 'C/L'),
    Step('Return',  'back to the main menu',             1.0, 'C'),
    Step('hw',      'hardware menu',                     1.0, 'C'),
    Step('regs',    'A2B register dump (read-only I2C reads)', 2.0, 'C'),
    Step('Return',  'back to the main menu',             1.0, 'C'),
    Step('version', 'build banner',                      1.0, 'C'),
)

# After the sequence, record ~2 min while the user cycles radio/volume/ignition.
RECORD_SECONDS = 120

# Only these console tokens may ever be emitted by this action (lower-cased, with
# bare integers allowed for dprint stream toggles). From the recipe whitelist.
WHITELIST = frozenset({'d', 'all', 'none', 'return', 'hw', 'regs', 'version', 'tasks', 'help'})

# The recipe's hard-block list, kept for tests/visibility. These must never be
# emitted and must classify as WRITE or DESTRUCTIVE in safety.classify_console.
HARD_BLOCK_EXAMPLES = (
    'spiflash erase 0x180000 0x60000', 'spiflash write 0x0 0x10', 'nvm set 1 2',
    'nvm clear', 'reset', 'crash', 'watchdog', 'cleardsp', 'gpio set 3 1',
)

# What to grep for in the captured log (hands the UI a highlight list).
GREP_HINTS = (
    'handle_vol_type', 'AMP_STATE_ENABLED', 'AMP_STATE_DISABLED',
    'Reset ignition timer', 'pwrcntrl_set_power', 'Set amp remote',
    'A2B Network ON', 'A2B Network OFF', 'Audio turned on', 'Audio turned off',
    'DSP stopped', 'canstat=',
)


def _token_word(token: str) -> bool:
    t = token.strip().lower()
    return t in WHITELIST or t.isdigit()


def assert_sequence_safe() -> None:
    """Belt-and-suspenders: every step is a whitelisted read-only token and none
    classifies as destructive. Raises AssertionError on any violation. (The gate
    below already blocks sending; this guards the recipe content itself.)"""
    for s in STEPS:
        assert _token_word(s.token), 'diagnostic step %r is not in the read-only whitelist' % s.token
        # classify_console never widens anything here; we only use it to REFUSE a
        # destructive token sneaking in. (Menu tokens read as WRITE=unknown, which
        # is fine -- we gate sending separately; what must never pass is DESTRUCTIVE.)
        assert safety.classify_console(s.token) is not Access.DESTRUCTIVE, \
            'diagnostic step %r classifies as destructive' % s.token


def debugsend_sendable(wiremap: dict | None = None) -> bool:
    """True only when DebugSend has a real wire mapping AND that command is parity
    cleared to send. Today it is unmapped (candidate 0x0e00, in `_unmapped_jobs`),
    so this is False and the action cannot run. Ties the gate to the live state:
    add the mapping to wiremap.json plus a byte-matched capture reference for its
    command and it unlocks automatically."""
    from .jobs import load_wiremap
    wm = wiremap if wiremap is not None else load_wiremap()
    jobs = wm.get('jobs', {})
    j = jobs.get('DebugSend')
    if not j or wm.get('devid') is None:
        return False
    if any(k not in j for k in ('fblock', 'fkt')):
        return False
    # Flat MDI command id = fblock<<8 | fkt (FRAMING.md); require it parity-cleared.
    cmd = ((j['fblock'] & 0xFF) << 8) | (j['fkt'] & 0xFF)
    try:
        from . import parity
        return parity.can_send(cmd)
    except Exception:
        return False


def can_run(engine) -> tuple[bool, str]:
    """(ok, reason). False until the device is connected AND DebugSend is a
    confirmed, parity-cleared frame. Reason is shown on the disabled button."""
    if not debugsend_sendable():
        return (False,
                'Diagnostic Capture is read-only but cannot run in-app yet: sending console '
                'text needs the DebugSend wire frame (candidate 0x0e00), which is not byte-'
                'matched to the official app. Run fault-capture/fault_capture.py (the official-app '
                'driver) to capture now; its --usbpcap output will clear DebugSend and unlock this button.')
    if not getattr(engine, 'connected', False):
        return (False, 'Connect to the device first.')
    return (True, '')


def run(engine, out_path: str):
    """Execute the read-only capture and save the streamed log to out_path.
    Refuses unless can_run(engine) is True. (Inert today -- the gate is closed.)"""
    ok, reason = can_run(engine)
    if not ok:
        raise safety.SafetyError('Diagnostic Capture blocked: %s' % reason)
    assert_sequence_safe()
    # Future (once DebugSend is cleared): for each step, send token via the engine's
    # DebugSend path (which re-checks parity per frame), wait step.wait_s while the
    # device streams its log, then record RECORD_SECONDS, and write the collected
    # log text to out_path. No stop command is needed; toggles are RAM-only.
    raise NotImplementedError('native DebugSend path lands when 0x0e00 is capture-verified')
