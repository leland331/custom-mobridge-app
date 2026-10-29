"""Read-only CAN decode for the Diagnostics & Health tab.

Turns the vehicle<->Mobridge CAN frames the device actually sees into (1) a live
HealthStatus snapshot (the things worth watching at a glance) and (2) a feed of
human-readable events (volume up, amp enabled, audio on, chime, ...).

SCOPE / HONESTY: we decode ONLY what the Mobridge hardware accepts and what the
firmware map confirms ([C] in device-config/VEHICLE_CAN_MAP.md). The MCP2515 masks
(0x7F0) pass only 0x1D0-1DF, 0x220-22F, 0x2A0-2AF, 0x2E0-2EF, 0x2F0-2FF,
0x590-59F, so most vehicle traffic (door, locks, ...) never reaches the device and
simply never appears here -- no placeholder or "pending" rows. Uncertain
semantics ([U], e.g. which Ford warning a chime id is) are shown literally
(chime id 0x11), never guessed.

OBSERVE-ONLY: nothing here transmits. It decodes inbound/bus frames; the parity
send-gate is irrelevant because no frame is ever produced.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


# IDs the MCP2515 hardware filter actually accepts (mask 0x7F0 ranges collapsed to
# the firmware's real table entries). Frames outside this set never reach the device.
ACCEPTED_RX_IDS = frozenset({0x1DA, 0x1DD, 0x1DF, 0x220, 0x222, 0x223, 0x2A0, 0x2E3, 0x2F4, 0x59E})
# The device's own transmissions (seen by a bus sniffer); carry absolute state.
DEVICE_TX_IDS = frozenset({0x221, 0x224, 0x228, 0x5C6})

VOLTYPE_NAMES = {0: 'Stereo / Media', 1: 'Prompt / Nav', 2: 'Phone', 3: 'Voice', 4: 'Inactive'}


class EventType(str, Enum):
    VOLUME = 'Volume'
    SOURCE = 'Source'
    TONE = 'Tone / EQ'
    AMP = 'Amplifier'
    AUDIO = 'Audio'
    CHIME = 'Chime'
    BEEP = 'Beep'
    NETWORK = 'Network'


@dataclass
class CanEvent:
    ts: float
    type: EventType
    text: str
    can_id: int
    tag: str = 'C'    # confidence from the CAN map: C confirmed, L likely, U uncertain

    def clock(self) -> str:
        return time.strftime('%H:%M:%S', time.localtime(self.ts))


@dataclass
class HealthStatus:
    """Live snapshot. None = not seen yet (shown as a dash, never 'pending')."""
    amp_enabled: bool | None = None       # 0x223 b3[7:6]==3
    audio_on: bool | None = None          # 0x2E3
    source: int | None = None             # voltype 0..4
    stereo_volume: int | None = None      # absolute, re-mapped CAN scale, from 0x221 echo
    bass: int | None = None               # 0..14 (7=flat), from 0x228 echo / 0x1DD
    treble: int | None = None
    mid: int | None = None
    balance: int | None = None
    fade: int | None = None
    preset: int | None = None             # SVC selector -> Preset 1..4 (index 0..3)
    last_chime_id: int | None = None
    nm_vehicle_seen: float | None = None    # last 0x59E (vehicle NM) time
    nm_device_seen: float | None = None   # last 0x5C6 (device NM) time
    a2b_link: str | None = None           # fed by the engine, not CAN

    def source_name(self) -> str:
        return VOLTYPE_NAMES.get(self.source, '—') if self.source is not None else '—'


def _u16(b):
    return (b[0] << 8) | b[1]


@dataclass
class ObservedChime:
    chime_id: int
    count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0


class ChimeCatalog:
    """Chime id -> friendly name, plus a running log of which ids have been seen.

    The vehicle sends only a chime NUMBER (0x220/0x2F4 b1); which Ford warning each
    number is is NOT known from the firmware and we never guess it. So `names`
    starts EMPTY: the feed shows the raw id until a number's meaning is confirmed,
    and the user can annotate observed ids (note what was on the dash when it fired)
    to build the map over normal driving. Persisted to a local JSON file -- that is
    a local annotation, not a device write, so it is not parity-gated."""

    def __init__(self, names: dict[int, str] | None = None):
        self.names: dict[int, str] = dict(names or {})
        self.observed: dict[int, ObservedChime] = {}

    def record(self, chime_id: int, ts: float) -> ObservedChime:
        o = self.observed.get(chime_id)
        if o is None:
            o = self.observed[chime_id] = ObservedChime(chime_id, 0, ts, ts)
        o.count += 1
        o.last_ts = ts
        return o

    def name(self, chime_id: int) -> str | None:
        n = self.names.get(chime_id)
        return n if n else None

    def label(self, chime_id: int) -> str:
        """'<name> (id 0xNN)' when known, else 'id 0xNN' -- no guessed names."""
        n = self.name(chime_id)
        return '%s (id 0x%02X)' % (n, chime_id) if n else 'id 0x%02X' % chime_id

    def set_name(self, chime_id: int, name: str) -> None:
        name = (name or '').strip()
        if name:
            self.names[chime_id] = name
        else:
            self.names.pop(chime_id, None)

    def load(self, path: str) -> None:
        import json
        import os
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            # keys are hex strings like "0x11"; tolerate ints too
            self.names = {int(k, 0) if isinstance(k, str) else int(k): str(v)
                          for k, v in raw.get('names', raw).items() if str(v).strip()}
        except (OSError, ValueError, TypeError):
            pass     # a corrupt annotation file never breaks the live view

    def save(self, path: str) -> str:
        import json
        import os
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'names': {'0x%02X' % k: v for k, v in sorted(self.names.items())}}, f, indent=1)
        return path


class EventFeed:
    """Stateful decoder. feed(can_id, data, ts) updates .status and returns the
    list of new events the frame produced (possibly empty). Keeps enough prior
    state to turn absolute frames into 'increased/decreased/on/off' events and to
    suppress the ~1 Hz chime-cancel flood."""

    def __init__(self, chimes: 'ChimeCatalog | None' = None):
        self.status = HealthStatus()
        self.chimes = chimes if chimes is not None else ChimeCatalog()
        self._chime_cleared = True    # suppress repeated 'cancel all' (id<=1) spam

    # -- public -------------------------------------------------------------
    def feed(self, can_id: int, data: bytes, ts: float | None = None) -> list[CanEvent]:
        ts = time.time() if ts is None else ts
        fn = _DECODERS.get(can_id)
        if fn is None:
            return []     # not an accepted/known ID -> nothing (no placeholder)
        return fn(self, data, ts, can_id) or []

    def feed_log_line(self, line: str, ts: float | None = None) -> list[CanEvent]:
        """Parse one line of the device's debug-console LOG STREAM into events.

        This is the PASSIVE live source: the device streams this log over the
        FTDI link on connect (no auth -- NOTES.md), so reading it sends nothing.
        By default only INFO/WARNING/ERROR and WSNDMGR print, so the default
        stream carries the amp/audio/power/chime lines below plus the harmless
        ~1 Hz chime-cancel flood; the per-CAN-frame channels (CAN_FORD_APP/PMT)
        stay off until enabled via the GATED console path (DebugSend)."""
        ts = time.time() if ts is None else ts
        for rx, et, build, tag in _LOG_PATTERNS:
            m = rx.search(line)
            if m:
                out = build(self, m, line)
                if out is None:
                    return []
                return [CanEvent(ts, et, out, 0, tag)]
        return []

    def set_a2b_link(self, state: str | None):
        self.status.a2b_link = state

    # -- per-ID decoders (only [C] semantics) -------------------------------
    def _volume(self, data, ts, cid):
        # 0x2A0 b6 / 0x1DF b0: 0x1E = no change, each step is one detent.
        idx = 6 if cid == 0x2A0 else 0
        if len(data) <= idx:
            return []
        delta = data[idx] - 0x1E
        if delta == 0:
            return []
        where = 'knob' if cid == 0x2A0 else 'steering'
        src = self.status.source_name()
        n = abs(delta)
        updown = 'up' if delta > 0 else 'down'
        return [CanEvent(ts, EventType.VOLUME, 'Volume %s %d (%s, %s)' % (updown, n, src, where), cid)]

    def _set_tone(self, name, v, ts, cid, out):
        # 0xF = no change. First observation seeds the status tile silently; only a
        # real change afterwards is a feed event (the current value lives in a tile).
        if v == 0xF:
            return
        prev = getattr(self.status, name)
        setattr(self.status, name, v)
        if prev is not None and prev != v:
            out.append(CanEvent(ts, EventType.TONE, '%s -> %d (7=flat)' % (name.capitalize(), v), cid))

    def _tone_svc(self, data, ts, cid):      # 0x1DD: bass/treble/balance/fade/SVC, 0xF=nochange
        if len(data) < 3:
            return []
        out = []
        self._set_tone('bass', data[0] >> 4, ts, cid, out)
        self._set_tone('treble', data[0] & 0xF, ts, cid, out)
        self._set_tone('balance', data[1] >> 4, ts, cid, out)
        self._set_tone('fade', data[1] & 0xF, ts, cid, out)
        svc = data[2] >> 4
        preset = {0: 0, 1: 1, 4: 2, 7: 3}.get(svc)
        if svc != 0xF and preset is not None and self.status.preset != preset:
            prev = self.status.preset
            self.status.preset = preset
            if prev is not None:   # seed silently on first sight; tile shows current
                out.append(CanEvent(ts, EventType.SOURCE, 'Preset -> %d (SVC/adaptive-volume selector)' % (preset + 1), cid))
        return out

    def _mid_mode(self, data, ts, cid):      # 0x1DA: midrange + audio-mode/source
        if len(data) < 2:
            return []
        out = []
        self._set_tone('mid', data[1] & 0xF, ts, cid, out)
        mode = data[0]
        newsrc = {0x24: 3, 0x26: 2, 0x28: 0, 0x40: 0, 0x44: 0, 0x46: 0, 0x48: 0}.get(mode)
        if newsrc is None and mode not in (0x20, 0x0C, 0x6C) and (mode & 0x40):
            newsrc = 0
        if newsrc is not None and self.status.source != newsrc:
            self.status.source = newsrc
            tag = 'C' if mode in (0x24, 0x26, 0x44, 0x46, 0x48) else 'L'
            out.append(CanEvent(ts, EventType.SOURCE, 'Source -> %s' % VOLTYPE_NAMES[newsrc], cid, tag))
        return out

    def _amp_voltype(self, data, ts, cid):   # 0x223: amp enable + voltype
        if len(data) < 4:
            return []
        out = []
        enabled = (data[3] >> 6) == 3
        if self.status.amp_enabled != enabled:
            self.status.amp_enabled = enabled
            out.append(CanEvent(ts, EventType.AMP, 'Amplifier %s' % ('ENABLED' if enabled else 'disabled'), cid))
        w = _u16(data)
        if w & 0x80:
            mono = {4: 0, 3: 3, 2: 2}.get((w >> 8) & 0x7)   # mono sub-field pos is [L]
            if mono is not None and self.status.source != mono:
                self.status.source = mono
                out.append(CanEvent(ts, EventType.SOURCE, 'Source -> %s' % VOLTYPE_NAMES[mono], cid, 'L'))
        elif w & 0x40 and self.status.source != 1:
            self.status.source = 1
            out.append(CanEvent(ts, EventType.SOURCE, 'Source -> %s' % VOLTYPE_NAMES[1], cid))
        return out

    def _audio(self, data, ts, cid):         # 0x2E3: audio on/off
        if not data:
            return []
        on = bool(data[0] & 0x02)
        off = bool(data[0] & 0x01)
        state = True if on else (False if off else None)
        if state is None or self.status.audio_on == state:
            return []
        self.status.audio_on = state
        return [CanEvent(ts, EventType.AUDIO, 'Audio turned %s' % ('ON' if state else 'off'), cid)]

    def _chime(self, data, ts, cid):         # 0x220 / 0x2F4
        if len(data) < 2:
            return []
        cid_chime = data[1]
        if cid_chime <= 1:                    # 'cancel all' -- the harmless 1 Hz flood
            if self._chime_cleared:
                return []
            self._chime_cleared = True
            return [CanEvent(ts, EventType.CHIME, 'Chimes cleared', cid)]
        self._chime_cleared = False
        self.status.last_chime_id = cid_chime
        self.chimes.record(cid_chime, ts)
        repeating = cid_chime in (0x11, 0x12, 0x1C, 0x1D)
        kind = 'Repeating chime' if repeating else 'Chime'
        # which Ford warning this id is is [U] -- show the name if the user has
        # annotated it, otherwise the raw id; never a guessed name.
        named = self.chimes.name(cid_chime) is not None
        return [CanEvent(ts, EventType.CHIME, '%s: %s' % (kind, self.chimes.label(cid_chime)),
                         cid, 'C' if named else 'U')]

    def _beep(self, data, ts, cid):          # 0x222: only b0==0x50 does anything
        if data and data[0] == 0x50:
            return [CanEvent(ts, EventType.BEEP, 'Beep', cid)]
        return []

    def _vehicle_nm(self, data, ts, cid):      # 0x59E
        self.status.nm_vehicle_seen = ts
        return []                             # keep-alive, not a feed-worthy event each time

    def _dev_nm(self, data, ts, cid):        # 0x5C6 (device TX)
        self.status.nm_device_seen = ts
        return []

    def _stereo_vol_echo(self, data, ts, cid):   # 0x221: 4A VV 08 .. absolute stereo volume
        if len(data) < 2:
            return []
        vv = data[1]
        if self.status.stereo_volume == vv:
            return []
        prev = self.status.stereo_volume
        self.status.stereo_volume = vv
        if prev is None:
            return []     # first reading seeds the status, not an up/down event
        return [CanEvent(ts, EventType.VOLUME, 'Stereo volume -> %d (%s)' % (vv, 'up' if vv > prev else 'down'), cid)]

    def _eq_echo(self, data, ts, cid):       # 0x228: absolute EQ echo (b1/b2/b6)
        if len(data) < 7:
            return []
        # b6 = treble<<4 | mid ; keep status in sync without spamming the feed.
        self.status.treble = data[6] >> 4
        self.status.mid = data[6] & 0xF
        return []

    def _audio_type(self, data, ts, cid):    # 0x224: 04 XX .. audio type state
        return []     # XX is ambiguous (0x80 = stereo/voice/off); source tracked elsewhere


_DECODERS = {
    0x2A0: EventFeed._volume, 0x1DF: EventFeed._volume,
    0x1DD: EventFeed._tone_svc, 0x1DA: EventFeed._mid_mode,
    0x223: EventFeed._amp_voltype, 0x2E3: EventFeed._audio,
    0x220: EventFeed._chime, 0x2F4: EventFeed._chime, 0x222: EventFeed._beep,
    0x59E: EventFeed._vehicle_nm, 0x5C6: EventFeed._dev_nm,
    0x221: EventFeed._stereo_vol_echo, 0x228: EventFeed._eq_echo, 0x224: EventFeed._audio_type,
}


# --- debug-console log-stream patterns (the passive source) ---------------------
# Firmware log strings from VEHICLE_CAN_MAP.md grep hints. Each entry:
#   (compiled regex, EventType, builder(feed, match, line) -> text|None, tag)
# A builder may update feed.status and return None to swallow the line (e.g. the
# chime-cancel flood after the first). All [C] unless marked.

def _log_amp(feed, m, line):
    on = 'ENABLED' in line.upper()
    feed.status.amp_enabled = on
    return 'Amplifier %s' % ('ENABLED' if on else 'disabled')

def _log_audio(feed, m, line):
    on = 'on' in m.group(0).lower()
    feed.status.audio_on = on
    return 'Audio turned %s' % ('ON' if on else 'off')

def _log_power(feed, m, line):
    return 'Power control -> %s' % m.group(1)

def _log_ampremote(feed, m, line):
    return 'Amp remote -> %s' % m.group(1)

def _log_a2bnet(feed, m, line):
    return 'A2B network %s' % m.group(1).upper()

def _log_dsp_stop(feed, m, line):
    return 'DSP stopped'

def _log_ign(feed, m, line):
    return 'Ignition timer reset'

def _log_chime_cancel(feed, m, line):
    if feed._chime_cleared:
        return None                 # swallow the ~1 Hz flood after the first
    feed._chime_cleared = True
    return 'Chimes cleared'

_LOG_PATTERNS = [
    (re.compile(r'AMP_STATE_(ENABLED|DISABLED)'), EventType.AMP, _log_amp, 'C'),
    (re.compile(r'Audio turned (on|off)', re.I), EventType.AUDIO, _log_audio, 'C'),
    (re.compile(r'pwrcntrl_set_power:\s*(-?\d+)', re.I), EventType.AMP, _log_power, 'C'),
    (re.compile(r'Set amp remote:\s*(-?\d+)', re.I), EventType.AMP, _log_ampremote, 'C'),
    (re.compile(r'A2B Network (ON|OFF)', re.I), EventType.NETWORK, _log_a2bnet, 'C'),
    (re.compile(r'DSP stopped', re.I), EventType.AUDIO, _log_dsp_stop, 'C'),
    (re.compile(r'Reset ignition timer', re.I), EventType.NETWORK, _log_ign, 'C'),
    (re.compile(r'WSNDMGR_CancelWarning', re.I), EventType.CHIME, _log_chime_cancel, 'C'),
]
