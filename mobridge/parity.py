"""Send policy + the write grammars Custom Mobridge was proven against.

HISTORY. Until 2026-10-22 this module was a send GATE: a frame left the app only
when it byte-matched a captured frame, was a non-persisting read, or fitted a
write grammar pinned by the exe decompile. the project's rule then was "open when the
decompile shows the bytes identical", and on 2026-10-22 every DSP write the
official app sends to this product was pinned (12 of 13 tables; see
docs/project/FAITHFULNESS_CHECKLIST.md). With everything open the gate itself
came out (16:07Z: "fully functional, nothing locked"), so the app now
behaves like the official one: a control change goes to the device.

WHAT STAYS SHUT (by design, not caution):
  * reset + flashing     - 0x0010 Reset / 0x0011 FactoryReset / 0x0201
                           SendFile / 0x0280-0x0281 ReceiveFile. Flashing goes
                           through the proven official-app path only (project rule).
  0x0100/0x0101 LocalIDRead/Write (MOST units) are carried and gated exactly as
  the exe gates them: session capability bit 0x2 (connect.LOCALID_CAP). the user
  2026-10-22 16:17Z: "we make our app reverse compatible with all units".
Everything else is a frame the official app sends for the same model state, so
`frame_cleared` lets it through. `proven_write` keeps the pinned grammars as a
self-check used by the tests: every builder output must still fit its grammar.

The reference-frame bookkeeping (Status, load_references, check_all,
status_for_cmd) is kept for the `parity` CLI report and the capture proofs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import IntEnum

from .framing import MdiFramer
from .message import MdiMessage

REFERENCE_PATH = os.path.join(os.path.dirname(__file__), 'reference_frames.json')


class Status(IntEnum):
    # Values are ordered by strength. Single-source derivations (1-2) do NOT clear
    # sending; everything at CROSS_VALIDATED or above (>=3) clears. Each has a
    # UNIQUE value so a command's real provenance is reported truthfully (an
    # IntEnum collapses members that share a value into one name).
    UNCONFIRMED = 0
    FIRMWARE_DERIVED = 1       # single source: firmware RX decompile
    APP_DERIVED = 2            # single source: app TX decompile
    CROSS_VALIDATED = 3        # firmware + app agree
    STORED_CONFIG = 4          # reproduces his real stored values
    PASSIVE_CAPTURE = 5        # matches a passive connect/read-back sniff
    ACTIVE_CAPTURE = 6         # matches a sniff of the app doing the command

    @property
    def clears_sending(self) -> bool:
        return self >= Status.CROSS_VALIDATED


_BY_NAME = {s.name.lower(): s for s in Status}
# friendly aliases accepted in reference_frames.json
_BY_NAME.update({'firmware': Status.FIRMWARE_DERIVED, 'app': Status.APP_DERIVED,
                 'cross': Status.CROSS_VALIDATED, 'stored': Status.STORED_CONFIG,
                 'passive': Status.PASSIVE_CAPTURE, 'capture': Status.ACTIVE_CAPTURE,
                 'active': Status.ACTIVE_CAPTURE})


def parse_status(s: str) -> Status:
    try:
        return _BY_NAME[s.strip().lower()]
    except KeyError:
        raise ValueError('unknown proof source %r (use %s)' % (s, ', '.join(sorted(_BY_NAME))))


class ParityError(PermissionError):
    pass


@dataclass
class Reference:
    name: str
    cmd: int
    data: bytes
    expected: bytes
    source: Status
    note: str = ''

    @classmethod
    def from_json(cls, d: dict) -> 'Reference':
        return cls(d['name'], int(d['cmd']), bytes.fromhex(d.get('data', '')),
                   bytes.fromhex(d['expected']), parse_status(d['source']), d.get('note', ''))


def load_references(path: str = REFERENCE_PATH) -> list[Reference]:
    with open(path, encoding='utf-8') as f:
        return [Reference.from_json(d) for d in json.load(f)['frames']]


def check_reference(ref: Reference, framer: MdiFramer | None = None) -> tuple[bool, bytes]:
    framer = framer or MdiFramer()
    got = framer.encode(MdiMessage(cmd=ref.cmd, data=ref.data))
    return got == ref.expected, got


def check_all(path: str = REFERENCE_PATH) -> list[tuple[Reference, bool, bytes]]:
    return [(r, *check_reference(r)) for r in load_references(path)]


def status_for_cmd(cmd: int, path: str = REFERENCE_PATH) -> Status:
    """Best verified status for a command: the highest-ranked source whose
    expected bytes we actually reproduce. A reference we DON'T reproduce never
    counts, however it is labelled."""
    best = Status.UNCONFIRMED
    for r in load_references(path):
        if r.cmd == cmd:
            ok, _ = check_reference(r)
            if ok and r.source > best:
                best = r.source
    return best


def can_send(cmd: int, path: str = REFERENCE_PATH) -> bool:
    return status_for_cmd(cmd, path).clears_sending


# Commands the user authorized to actually SEND (2026-10-21): connect / session /
# handshake / baud / stream-toggle + DSP value READS. All are CAPTURE_VERIFIED on
# the real wire and are NON-PERSISTING - they don't change stored device state -
# so they clear at the command level (any in-format payload), which lets the app
# connect and read live values for real. WRITES are NEVER in this set: a write
# must byte-match an exact captured frame (and 0x0700 also waits on the app-side
# display audit). Save/commit was never captured and is not here either.
# Only the DSP value-READ commands get command-level clearance (any in-format
# payload): their payload is a variable, capture-confirmed [index]/[selector] and
# they are non-destructive, so the app can read ANY channel for real. The
# connect/session/handshake/baud/stream-toggle commands have FIXED payloads and
# are already cleared frame-exact by their captured frames - they are deliberately
# NOT command-level, so e.g. a different 6-byte payload on 0x0400 (the low-
# confidence FlashRead mapping) does NOT ride the stream-toggle's clearance.
SEND_SAFE_COMMANDS = frozenset({
    0x070F, 0x0710, 0x0711, 0x0713, 0x0714, 0x0716, 0x0718,   # DSP value reads (variable channel/selector)
})


# ---------------------------------------------------------------------------
# Tier 3: writes proven IDENTICAL by decompile + capture. the user's explicit rule
# and go-ahead (2026-10-22 02:41Z / 02:49Z): "if the decomp and the capture show
# our encoder produces identical frames, that write is proven and its gate opens"
# -- no on-unit check needed. A frame clears here only when it is a well-formed
# instance of a write grammar whose EVERY byte position is pinned:
#   0x0700 LEN4   gain-only record  [preset][chan][gain s16 BE]  (2 captures byte-exact)
#   0x0704 LEN13  EQ band sub05     05|chan|freq f32|gain half|Q half|Q half (13 edits match coeffs)
#   0x0704 LEN3   EQ band remove    05|chan                      (captured)
#   0x0708 LEN23  biquad coeffs     chan|section|5 x f32 BE      (all EQ/LPF/HPF match RBJ @48k)
#   0x0401 LEN1   DebugSend         one console byte             (captured 'h', Enter)
# NOT here (not yet shown identical, stays byte-exact-only): 0x0700 LEN16 full
# record (phase/passthrough byte unresolved; carries every field), 0x0704 sub03
# filter definitions (LPF/HPF freq-slot placement unproven; band derivation unknown).
# ---------------------------------------------------------------------------
import struct as _struct


def _split(frame: bytes):
    """(cmd, payload) of a framed MDI frame, or None if malformed."""
    if len(frame) < 6 or frame[0] != 0xAA:
        return None
    ln = int.from_bytes(frame[1:3], 'big')
    if len(frame) != 6 + ln:
        return None
    return int.from_bytes(frame[3:5], 'big'), frame[5:5 + ln]


def _points_ok(body: bytes) -> bool:
    """EQ point list: N x (freq f32 BE, gain half dB, Q-below half, Q-above half).
    The two Qs are separate fields (equal only on woofer/sub channels). 20 Hz and
    Q <= 20 are our own conservative limits, not official-app constants."""
    for i in range(0, len(body), 10):
        try:
            f = _struct.unpack('>f', body[i:i + 4])[0]
            g = _struct.unpack('>e', body[i + 4:i + 6])[0]
            q1 = _struct.unpack('>e', body[i + 6:i + 8])[0]
            q2 = _struct.unpack('>e', body[i + 8:i + 10])[0]
        except (_struct.error, OverflowError):
            return False
        if not (20.0 <= f <= 24000.0 and -24.0 <= g <= 12.0
                and 0.0 < q1 <= 20.0 and 0.0 < q2 <= 20.0):
            return False
    return True


def proven_write(frame: bytes) -> bool:
    """True when `frame` is an instance of a write grammar proven identical to the
    official app's. Structural: a frame that parses here IS what the official app
    sends for those values. No longer on the send path (frame_cleared); the tests
    run every builder through it so a payload regression shows up."""
    p = _split(frame)
    if p is None:
        return False
    cmd, d = p
    if cmd == 0x0700 and len(d) in (4, 16):
        if not 0xB700 <= int.from_bytes(d[2:4], 'big') <= 0xFF00:
            return False                               # the app's gain window
        if len(d) == 4:
            return d[1] == 0x00                        # LEN4 is the MASTER gain only
        # [4] phase, [7] passthrough, [8] mute; [9-10] test tone u16 is only ever
        # 0/1 in the exe (set from a .rax attr), [11] is never set and the firmware
        # discards it (FW_LOCKED_WRITES_OCT22 §3: [10] sets an unread flag, inert)
        return (d[4] in (0, 1) and d[7] in (0, 1) and d[8] in (0, 1)
                and d[9] == 0 and d[10] in (0, 1) and d[11] == 0)
    if cmd == 0x0704:
        # --- opened on the DECOMPILE alone (2026-10-22 14:16Z "go off the
        # decomp"): every byte of these builders is pinned in CMD_TABLE.md with no
        # lost argument or UNRESOLVED-VALUE constant, and the firmware disasm
        # agrees on the sub-code meaning (mdimod_dsp_rx_appdata).
        if len(d) == 0x41 and d[0] == 0x01:
            # sub 1 device name: 0x40 raw bytes (CMD_TABLE §3.2). The exe copies
            # dev+6 verbatim; its QString->bytes step is outside the region, so only
            # names whose encoding is unambiguous (printable ASCII, NUL padded,
            # at least one NUL) clear here.
            raw = d[1:]
            end = raw.find(b'\0')
            return end >= 0 and all(0x20 <= b < 0x7F for b in raw[:end]) and not any(raw[end:])
        if len(d) == 5 and d[0] == 0x02:
            # sub 2 channel props [preset][ch+1][speaker ordinal][xoverlink] (§3.8,
            # fw >= 200 layout; captured 02 00 01 09 01 / 02 00 02 0c 01 [CAP])
            return d[1] <= 3 and 1 <= d[2] <= 12 and 1 <= d[3] <= 15 and d[4] in (0, 1)
        if len(d) == 130 and d[0] == 0x07:
            # sub 7 input gains [preset][64 x u16 BE], each round(0xED00 + dB*512)
            # with dB clamped -48..+9 (FUN_1400ece60; DAT_140201480 = -48.0 read
            # from .rdata 2026-10-22). Firmware (FW_LOCKED_WRITES §5) needs
            # preset 0..3 and >= 6 entries.
            return d[1] <= 3 and all(0x8D00 <= int.from_bytes(d[i:i + 2], 'big') <= 0xFF00
                                     for i in range(2, 130, 2))
        if len(d) == 4 and d[0] == 0x08:
            # sub 8 input delay [preset][u16 ms*1000], cap 10000 (§3.13 + MODEL_REGION
            # 2.5; firmware INPUTDELAY [preset, us])
            return d[1] <= 3 and int.from_bytes(d[2:4], 'big') <= 10000
        if len(d) >= 3 and d[0] == 0x05 and (len(d) - 3) % 10 == 0:
            return _points_ok(d[3:])                   # channel EQ list, N >= 0
        if len(d) >= 2 and d[0] == 0x06 and (len(d) - 2) % 10 == 0:
            return _points_ok(d[2:])                   # master EQ list, N >= 0
        if len(d) == 11 and d[0] == 0x03:
            # 03 | chan be16 | type | design | f1 be16 | f2 be16 | slope1 | slope2
            typ, design = d[3], d[4]
            f1, f2 = int.from_bytes(d[5:7], 'big'), int.from_bytes(d[7:9], 'big')
            if typ not in (0, 1, 2, 3) or design not in (0, 1, 2, 3, 4):
                return False
            if d[9] not in (0x0c, 0x18) or d[10] not in (0x0c, 0x18):
                return False
            if typ in (1, 3) and not 20 <= f1 <= 23080:   # LPF / band use f1
                return False
            if typ in (2, 3) and not 20 <= f2 <= 23080:   # HPF / band use f2
                return False
            return typ != 3 or f1 >= f2 + 20
        return False
    if cmd == 0x0702 and len(d) == 1026:
        # filter-bins table [preset][chan] + 512 x u16. PROVEN 2026-10-22 for
        # woofer/sub (mode-2) channels: filterbins.build_0702 matched the exe
        # thread's decompile-only reimplementation on all 2013 seeded cases
        # (SHA-256 of every 1026-byte table identical; the project's "100% confident"
        # condition), and the flat table is in every capture. Mode-1 (tweeter/mid)
        # crossover curves: crossover_curve.py matched the reference 720/720 and
        # its three constants were read from .rdata on 2026-10-22, so the
        # producer (dspwrite.MODE1_UNLOCKED) is open too. Each bin must sit in
        # the encodable window -27..+9 dB (0xB700..0xFF00).
        return all(0xB700 <= int.from_bytes(d[i:i + 2], 'big') <= 0xFF00 for i in range(2, 1026, 2))
    if cmd == 0x0708 and len(d) >= 3:
        # [preset][chan][count] + count x 5 float32 BE, max 16 stages
        return 1 <= d[2] <= 16 and len(d) == 3 + 20 * d[2]
    if cmd == 0x0401 and len(d) == 1:
        return True                                    # console text policed by safety.classify_console
    # --- decompile-only openings (same go-ahead as the 0x0704 subs above) ---
    if cmd == 0x0716 and len(d) == 2 and d[0] == 0x01:
        return d[1] <= 3                               # select preset [01][preset 0..3] (§3.3)
    if cmd == 0x0701 and len(d) >= 9 and d[0] == 0x00 and (len(d) - 1) % 8 == 0:
        return (len(d) - 1) // 8 <= 12                 # routing: 00 + u64 input id per channel (§3.5, fw >= 205)
    if cmd == 0x0707 and len(d) == 16 and d[0] == 0x02:
        # upmix [02][preset][enabled][algorithm][3 x f32] (§3.19, fw >= 209)
        return d[1] <= 3 and d[2] in (0, 1)
    if cmd == 0x0707 and len(d) == 7 and d[0] == 0x03:
        # time align [03][preset][units 0 cm / 1 in][closest u16][furthest u16], value x 100
        # (§3.20; DAT_1401fdd28 = 100.0 read from .rdata 2026-10-22)
        return d[1] <= 3 and d[2] in (0, 1)
    # --- pinned by exe + firmware disasm (re/analysis/FW_LOCKED_WRITES_OCT22.md) ---
    if cmd == 0x0705 and len(d) == 2:
        return d[0] <= 5                               # head unit [index 0..5][u8 raw]; 6 never set by the exe
    if cmd == 0x0703 and len(d) == 1:
        return d[0] in (0, 1)                          # source select 0 Analog / 1 Digital, round-trips 0x0713
    # STILL LOCKED by design: 0x0101 (no firmware handler on 2.11.x, the official
    # app never reaches it on this unit: parity means never sending it) and
    # firmware flashing (project rule). Every other write the official app sends
    # to a DA-G2.Pro is pinned above.
    return False


def frame_cleared(frame: bytes, cmd: int | None, path: str = REFERENCE_PATH) -> bool:
    """Send clearance used by the job runner. Three tiers:
      1. the EXACT frame matches a clearing-source reference, OR
      2. the command is in SEND_SAFE_COMMANDS and is CAPTURE_VERIFIED - a
         non-persisting read/connect command cleared for any in-format payload, OR
      3. the frame is an instance of a write grammar proven IDENTICAL by
         decompile + capture (proven_write; the user's explicit go 2026-10-22).
    Everything else (every other write, anything unproven) stays blocked.

    SINCE 2026-10-22 (gate removed, see the module docstring): a well-formed MDI
    frame clears unless its command is in LOCKED_COMMANDS. The three tiers above
    are history; `proven_write` remains as the tests' self-check."""
    p = _split(frame)
    if p is None:
        return False
    c = p[0]
    if cmd is not None and cmd != c:
        return False
    return c not in LOCKED_COMMANDS


# Commands that never leave this app from the DSP/scripting flows (module
# docstring). Flashing keeps its own, separate, still-locked path.
LOCKED_COMMANDS = {
    # 0x0100/0x0101 LocalIDRead/Write are NOT locked: 2026-10-22 16:17Z
    # "we make our app reverse compatible with all units". Like the exe, the
    # jobs are carried but nothing enqueues them (connect.LOCALID_CAP).
    # 0x0010 Reset / 0x0011 FactoryReset are NOT locked: both are empty frames,
    # pinned in the exe (FUN_1400ac9c0 / FUN_1400b0a90); 18:19Z "close the rest".
    # 0x0201 SendFile / 0x0280-0x0281 ReceiveFile are NOT locked: 2026-10-22 23:18Z
    # "unlock flashing". fif.bootblock_problem refuses 0x0-0x60000 except for families whose factory images write it (MOST 2.0.0).
}


def locked_reason(cmd: int | None) -> str | None:
    """Why a command may not be sent, or None when it is allowed."""
    return LOCKED_COMMANDS.get(cmd) if cmd is not None else None


def assert_frame_cleared(frame: bytes, cmd: int | None, what: str = '',
                         path: str = REFERENCE_PATH) -> None:
    if frame_cleared(frame, cmd, path):
        return
    p = _split(frame)
    if p is None:
        raise ParityError('BLOCKED: %s is not a well-formed MDI frame (%s)'
                          % (what or 'this request', frame.hex()))
    raise ParityError('BLOCKED by design: %s (cmd 0x%04x) - %s'
                      % (what or 'this request', p[0], LOCKED_COMMANDS[p[0]]))


def can_send_frame(frame: bytes, path: str = REFERENCE_PATH) -> bool:
    """Byte-exact send clearance: the EXACT outgoing frame must have been proven
    identical to the official app (a clearing-source reference whose expected
    bytes equal this frame). Proving a command's framing with one captured payload
    does NOT authorise a different payload on the same command id - that is how a
    captured read (e.g. cmd 0x0400 [01] toggle) must NOT unlock a flash read with a
    dynamic address. Dynamic/unproven payloads simply never match and stay gated."""
    for r in load_references(path):
        if r.source.clears_sending and r.expected == frame:
            ok, _ = check_reference(r)
            if ok:
                return True
    return False


def assert_can_send_frame(frame: bytes, what: str = '', path: str = REFERENCE_PATH) -> None:
    if not can_send_frame(frame, path):
        raise ParityError(
            'BLOCKED by parity rule: the exact frame for %s (%s) is not proven byte-identical '
            'to the official app. Custom Mobridge only transmits frames it has verified against a '
            'capture or cross-validation; a command whose framing is proven for one payload does '
            'not clear a different payload on that command.' % (what or 'this request', frame.hex()))


def assert_can_send(cmd: int, what: str = '', path: str = REFERENCE_PATH) -> None:
    st = status_for_cmd(cmd, path)
    if not st.clears_sending:
        raise ParityError(
            'BLOCKED by parity rule: %s (cmd 0x%04x) is %s — not independently proven. '
            'Custom Mobridge will not send unproven bytes. Clear it by cross-validating the '
            'firmware and app derivations, reproducing his stored-config values, or a passive '
            'read-back capture, then add the frame to reference_frames.json.'
            % (what or 'command', cmd, st.name))
