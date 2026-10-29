"""Live output-level meter for the selected channel's gain fader.

Source: DspTest 0x0719 type 7 (firmware DBFS_RMS_OUT, handler 0x518D48), the same
level read the official app's Limiter RTA uses. Request `[07][u8 mask]`, reply
`[07][mask]` + one int8 dB per set bit. It changes no setting: the firmware only
sets a RAM flag so its audio loop computes RMS for that output (a reboot clears it).
The mask is one byte, so type 7 covers outputs 1-8. Custom firmware LB-RC<n> adds type 8
(same request/reply, bit n = output 9+n; docs/chime-routing/LB-RC1.md); on other firmware 9-12 show no bar.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

POLL_MS = 300                    # the official RTA's poll period
MAX_CHANNEL = 8                  # u8 output mask (type 7)
MAX_CHANNEL_LB_RC = 12           # + type 8 on LB-RC<n> firmware
METER_FLOOR_DB = -60.0           # bottom of the bar
PEAK_HOLD_S = 1.2                # how long a peak marker lingers
RTA_MAX_DB = 3                   # firmware sends int8(20*log10(rms) + 3); rms <= 1


def rta_dbfs(value: int) -> float | None:
    """One signed int8 from the type-7 reply -> the level to show, in dB.

    The firmware computes int8(20*log10(rms) + 3) (FW_MDI_HANDLERS_OCT22 §3.2).
    rms is at most full scale, so a real reading is never above +3; anything
    higher is not a level and is dropped (None). With no signal rms is 0 and
    log10(0) is -inf; converting -inf to an integer saturates to INT32_MIN, whose
    low byte is 0x00, so silence arrives as 0 -- the same byte as full scale.
    0 is therefore shown as no signal (-inf, bottom of the bar), not as a full
    bar: a level that really sits at the top moves around it (-1, +1, +2) and
    still reads red."""
    if value > RTA_MAX_DB:
        return None
    if value == 0:
        return float('-inf')
    return float(value)


def max_channel(meters12: bool = False) -> int:
    """Highest output (1-based) the connected firmware can meter."""
    return MAX_CHANNEL_LB_RC if meters12 else MAX_CHANNEL


def request(ch: int) -> tuple[int, int]:
    """(DspTest type, mask) for output index ch (0-based): type 7 for 0-7, type 8 for 8-15."""
    return (7, 1 << ch) if ch < 8 else (8, 1 << (ch - 8))


@dataclass
class LevelReading:
    channel: int
    dbfs: float                  # current level, dBFS (0 = full scale)
    peak_dbfs: float = None      # held peak
    ts: float = field(default_factory=time.monotonic)

    def fraction(self, floor_db: float = METER_FLOOR_DB) -> float:
        """0..1 bar height from the current level."""
        return _frac(self.dbfs, floor_db)

    def peak_fraction(self, floor_db: float = METER_FLOOR_DB) -> float:
        return _frac(self.peak_dbfs if self.peak_dbfs is not None else self.dbfs, floor_db)


def _frac(dbfs: float, floor_db: float) -> float:
    if dbfs is None:
        return 0.0
    if dbfs >= 0.0:
        return 1.0
    if dbfs <= floor_db:
        return 0.0
    return (dbfs - floor_db) / (0.0 - floor_db)


class MeterState:
    """Held-peak tracking for the one metered output."""

    def __init__(self, peak_hold_s: float = PEAK_HOLD_S):
        self.reading: LevelReading | None = None
        self.peak_hold_s = peak_hold_s

    def update(self, chan: int, dbfs: float) -> LevelReading:
        now = time.monotonic()
        prev = self.reading
        peak = dbfs
        if prev is not None and prev.channel == chan and prev.peak_dbfs is not None:
            if dbfs < prev.peak_dbfs and (now - prev.ts) <= self.peak_hold_s:
                peak = prev.peak_dbfs
        self.reading = LevelReading(chan, dbfs, peak_dbfs=peak, ts=now)
        return self.reading

    def clear(self) -> None:
        self.reading = None
