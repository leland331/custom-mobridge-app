"""Custom firmware features: one registry the app reads to know what to show.

Each feature says
- how it is detected: the firmware build tag (boot banner "Build Time : ... LB-RC1")
  and, if it ever needs them, session capability bits that must also be set;
- how much latency it adds to the MUSIC path of which outputs (a function of the
  tune, so every toggle/preset combination is covered).

Screens ask the engine which features are active (`engine.features`). Rear Chimes
keeps its own rows on CONFIG & MASTER EQ (rearchimes.py); later features (upmix)
get their own tab, not rows in the input grid.

Delay and phase compensation (project rule, 2026-10-28): when a feature delays
the music on some outputs, the music on every other output gets a matching extra
delay so all outputs stay time-aligned (a pure delay that matches keeps phase
aligned too). Only the music path is ever delayed: chimes, beeps, prompts, phone
and voice (UNDELAYED_INPUTS, and feature inputs such as Rear Chimes) stay
undelayed. So the compensation cannot use the device's per-output delay (that
delays everything on the output); the firmware needs a music-only delay. `plan()` does
that maths. It only computes; nothing here sends anything to the device. No
current feature adds latency, so the plan is all zeros today; real values arrive
with the upmix.

Adding a feature = one `Feature(...)` entry in FEATURES (plus its firmware).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

from . import rearchimes

SAMPLE_RATE = 48000          # DSP rate (crossover_curve.FS); latencies are in samples
MUSIC_INPUTS = ('Left', 'Right')                     # the only inputs latency/compensation applies to
UNDELAYED_INPUTS = ('Prompt', 'Beeps', 'Phone', 'Voice')   # never delayed (plus feature inputs)


@dataclass(frozen=True)
class Feature:
    key: str
    name: str
    tag: re.Pattern                      # build tags that carry this feature
    caps: int = 0                        # session capability bits that must also be set (0 = none)
    # latency(root) -> {output index (0-based): delay added to that output's MUSIC path,
    # in samples}; None = adds none. Feature inputs (e.g. Rear Chimes) are never delayed.
    latency: Callable[[object], Mapping[int, int]] | None = field(default=None, compare=False)

    def detected(self, tag: str | None, caps: int = 0) -> bool:
        if not tag or not self.tag.fullmatch(tag.strip()):
            return False
        return (caps & self.caps) == self.caps


FEATURES: tuple[Feature, ...] = (
    Feature('rear_chimes', rearchimes.NAME, rearchimes.TAG),
    Feature('meters12', 'Meters 9-12', rearchimes.TAG),     # DspTest type 8 (LB-RC<n>)
    # Later, same shape: upmix (with latency=...), surround decoder.
)


def active(tag: str | None, caps: int = 0, registry: Iterable[Feature] = FEATURES) -> frozenset[str]:
    """Keys of the features the connected firmware has."""
    return frozenset(f.key for f in registry if f.detected(tag, caps))


def get(key: str, registry: Iterable[Feature] = FEATURES) -> Feature:
    for f in registry:
        if f.key == key:
            return f
    raise KeyError(key)


# --- delay / phase compensation ---------------------------------------------
@dataclass(frozen=True)
class Compensation:
    extra: dict[int, int]       # output -> extra MUSIC-path delay in samples, so all outputs line up
    # (applies to MUSIC_INPUTS only; chimes/beeps/prompts/phone/voice stay undelayed)
    ok: bool                    # False: cannot stay aligned (feature must not ship / be enabled)
    reason: str = ''

    def extra_ms(self, rate: int = SAMPLE_RATE) -> dict[int, float]:
        return {o: s * 1000.0 / rate for o, s in self.extra.items()}


def path_latency(keys: Iterable[str], root, outputs: Iterable[int],
                 registry: Iterable[Feature] = FEATURES) -> dict[int, int]:
    """Total added latency per output from all active features (they are in series)."""
    total = {o: 0 for o in outputs}
    keys = set(keys)
    for f in registry:
        if f.key in keys and f.latency is not None:
            for o, s in f.latency(root).items():
                if s < 0:
                    raise ValueError('%s reports negative latency on output %d' % (f.key, o))
                if o in total:
                    total[o] += int(s)
    return total


def compensate(latency: Mapping[int, int], headroom: Mapping[int, int] | None = None) -> Compensation:
    """Extra delay per output = (largest latency) - (its own latency).

    `headroom`: per output, how many more samples of delay the device can still
    add on top of the user's own delay setting. If any output would need more,
    the result is not ok (alignment cannot be kept, per the 2026-10-28 rule)."""
    if not latency:
        return Compensation({}, True)
    top = max(latency.values())
    extra = {o: top - s for o, s in latency.items()}
    if headroom is not None:
        short = sorted(o for o, s in extra.items() if s > headroom.get(o, 0))
        if short:
            return Compensation(extra, False, 'not enough delay range on output(s) %s'
                                % ', '.join(str(o + 1) for o in short))
    return Compensation(extra, True)


def plan(keys: Iterable[str], root, outputs: Iterable[int], headroom: Mapping[int, int] | None = None,
         registry: Iterable[Feature] = FEATURES) -> Compensation:
    """The compensation the app should keep applied for this tune and these features."""
    return compensate(path_latency(keys, root, outputs, registry), headroom)
