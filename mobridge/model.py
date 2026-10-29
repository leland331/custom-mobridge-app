"""The official app's DSP data model, rebuilt from the exe decompile
(re/analysis/MODEL_REGION.md §1, §4, §5; UI_REGION_1.md §3; UI_REGION_2.md §1.6, §2).

Every clamp, default, derived rule and dirty ("SyncProperty") bit here quotes
its source function in a comment so the next reader can check it against the
decompile. Nothing in this module touches the wire: dspwrite.py turns the dirty
bits into frames in the exe's DspWrite order, rax.py reads/writes the .rax file,
and uirules.py holds the spin-box ranges the forms apply BEFORE a value reaches
these setters (the model itself clamps only where the exe's setter clamps).

Layout: DspModel (root) -> 4 presets -> {MasterModel, UpmixModel, TimeAlignModel,
SpeakerModel per speaker role}; plus root-level channel map, input map, input
gains, options and head-unit bytes.
"""
from __future__ import annotations

import math as _math

from . import biquad, filterbins

# --------------------------------------------------------------------------
# SpeakerType role bits (FUN_1400ed8a0 names; FUN_1400ed7b0 wire ordinal -> bit)
# --------------------------------------------------------------------------
SPEAKER_TYPES = [
    # (bit,      xml id / name,          display name,            wire ordinal)
    (0x1,       'FrontLeftTweeter',      'Front Left Tweeter',      1),
    (0x2,       'FrontLeftMidrange',     'Front Left Midrange',     2),
    (0x4,       'FrontLeftWoofer',       'Front Left Woofer',       3),
    (0x8,       'FrontRightTweeter',     'Front Right Tweeter',     4),
    (0x10,      'FrontRightMidrange',    'Front Right Midrange',    5),
    (0x20,      'FrontRightWoofer',      'Front Right Woofer',      6),
    (0x40,      'RearLeftTweeter',       'Rear Left Tweeter',       7),
    (0x80,      'RearLeftMidrange',      'Rear Left Midrange',      8),
    (0x100,     'RearLeftWoofer',        'Rear Left Woofer',        9),
    (0x200,     'RearRightTweeter',      'Rear Right Tweeter',      10),
    (0x400,     'RearRightMidrange',     'Rear Right Midrange',     11),
    (0x800,     'RearRightWoofer',       'Rear Right Woofer',       12),
    (0x1000,    'LeftSubWoofer',         'Left Sub Woofer',         13),
    (0x2000,    'RightSubWoofer',        'Right Sub Woofer',        14),
    (0x8000,    'CenterMidrange',        'Center Midrange',         15),
    (0x10000,   'CenterTweeter',         'Center Tweeter',          16),
    (0x20000,   'HeadrestLeftTweeter',   'Headrest Left Tweeter',   17),
    (0x40000,   'HeadrestRightTweeter',  'Headrest Right Tweeter',  18),
    (0x80000,   'HeadlinerLeftTweeter',  'Headliner Left Tweeter',  19),
    (0x100000,  'HeadlinerRightTweeter', 'Headliner Right Tweeter', 20),
]
ROLE_BY_NAME = {name: bit for bit, name, _d, _o in SPEAKER_TYPES}
ROLE_BY_NAME.update({'FrontLeftFullRange': 0x2, 'FrontRightFullRange': 0x10,     # aliases (FUN_1400ed8a0)
                     'RearLeftFullRange': 0x80, 'RearRightFullRange': 0x400,
                     'CenterSpeaker': 0x8000})
NAME_BY_ROLE = {bit: name for bit, name, _d, _o in SPEAKER_TYPES}
DISPLAY_BY_ROLE = {bit: disp for bit, _n, disp, _o in SPEAKER_TYPES}
ORDINAL_BY_ROLE = {bit: o for bit, _n, _d, o in SPEAKER_TYPES}
ROLE_BY_ORDINAL = {o: bit for bit, _n, _d, o in SPEAKER_TYPES}

# SpeakerClass (FUN_1400f4c00): 1 Tweeter, 2 Midrange, 4 Woofer, 8 SubWoofer, 0 none
CLASS_NONE, CLASS_TWEETER, CLASS_MIDRANGE, CLASS_WOOFER, CLASS_SUBWOOFER = 0, 1, 2, 4, 8
_CLASS = {0x1: 1, 0x2: 2, 0x4: 4, 0x8: 1, 0x10: 2, 0x20: 4, 0x40: 1, 0x80: 2, 0x100: 4, 0x200: 1,
          0x400: 2, 0x800: 4, 0x1000: 8, 0x2000: 8, 0x4000: 0, 0x8000: 2, 0x10000: 1, 0x20000: 1,
          0x40000: 1, 0x80000: 0, 0x100000: 1}
# Location (FUN_1400edfa0)
LOC_FRONT_LEFT, LOC_FRONT_RIGHT, LOC_REAR_LEFT, LOC_REAR_RIGHT = 1, 2, 4, 8
LOC_REAR_SUB, LOC_FRONT_CENTER, LOC_HEADREST, LOC_HEADLINER = 0x10, 0x20, 0x40, 0x80


def speaker_class(role: int) -> int:
    return _CLASS.get(role, 0)


def speaker_location(role: int) -> int:
    if role in (0x1, 0x2, 0x4): return LOC_FRONT_LEFT
    if role in (0x8, 0x10, 0x20): return LOC_FRONT_RIGHT
    if role in (0x40, 0x80, 0x100): return LOC_REAR_LEFT
    if role in (0x200, 0x400, 0x800): return LOC_REAR_RIGHT
    if role in (0x1000, 0x2000): return LOC_REAR_SUB
    if role in (0x8000, 0x10000): return LOC_FRONT_CENTER
    if role in (0x20000, 0x40000): return LOC_HEADREST
    if role in (0x80000, 0x100000): return LOC_HEADLINER
    return 0


# Filter type (FilterType enum) / design (FilterDesign enum)
FT_NONE, FT_LP, FT_HP, FT_BAND = 0, 1, 2, 3
FILTER_TYPE_NAMES = {0: 'NoFilter', 1: 'LowPass', 2: 'HighPass', 3: 'BandPass'}
FD_BUTTERWORTH, FD_CHEBYSHEV, FD_BESSEL, FD_LINKWITZ_RILEY, FD_RBJ = 0, 1, 2, 3, 4
FILTER_DESIGN_NAMES = {0: 'Butterworth', 1: 'Chebyshev', 2: 'Bessel', 3: 'LinkwitzRiley', 4: 'RBJ'}

# EqualizerPointListModel modes (priv+0x18): 0 master, 1 speaker (device designs the
# crossover), 2 speaker with app-computed biquads (woofer/sub class, "Biquad")
MODE_MASTER, MODE_DEVICE_FILTER, MODE_BIQUAD = 0, 1, 2

# SpeakerModel SyncProperty bits (+0x110), FUN_1400fa060
SYNC_MAP, SYNC_INVERT_PHASE, SYNC_GAIN, SYNC_PASSTHROUGH = 0x1, 0x2, 0x4, 0x8
SYNC_MUTE, SYNC_TESTTONE, SYNC_XOVER_LINK, SYNC_TIME = 0x10, 0x20, 0x40, 0x80
SYNC_LIMIT, SYNC_FILTER, SYNC_BINS, SYNC_IIR, SYNC_EQ = 0x100, 0x200, 0x400, 0x800, 0x1000
SYNC_ALL = 0x1fff
# MasterModel flags (+0x2110): 0x61 all
MASTER_GAIN, MASTER_BINS, MASTER_EQ = 0x1, 0x20, 0x40
# UpmixModel flags (+0x34): 0x1f all
UPMIX_ENABLED, UPMIX_ALGORITHM, UPMIX_LMSRATE, UPMIX_ADPRATE, UPMIX_ADPGAMMA = 1, 2, 4, 8, 0x10
# TimeAlignModel flags (+0x2c): 7 all
TA_UNITS, TA_CLOSEST, TA_FURTHEST = 1, 2, 4
# Root dirty word (+0x1c378), FUN_1400f8580
ROOT_NAME, ROOT_MAP, ROOT_PRESET, ROOT_INPUT_MAP = 0x1, 0x2, 0x4, 0x8
ROOT_INPUT_GAINS, ROOT_INPUT_DELAY, ROOT_SOURCESEL, ROOT_OPTIONS = 0x10, 0x20, 0x40, 0x2000
ROOT_UPMIX, ROOT_TA_RELOAD = 0x200, 0x400          # 0x200..0x1000 are reload requests
# HeadunitModel flags (+0x18)
HU_BASS, HU_MID, HU_TREBLE, HU_FADE, HU_BALANCE, HU_VOLUME = 1, 2, 4, 8, 0x10, 0x20

# Constants read from the exe (UI_REGION_2.md §9, UI_REGION_1.md §5.1)
CUTOFF_MIN, CUTOFF_MAX = 20, 23080            # FUN_1400f4500 / FUN_1400f4120: 0x14..0x5a28
SLOPE_MIN, SLOPE_MAX = 6, 48                  # FUN_1400f4650 / FUN_1400f4270: 6..0x30
BAND_MIN_GAP_HZ = 20                          # FUN_1400fe280: f1 - f2 < 20 -> f1 = f2 + 20
DEFAULT_LOP, DEFAULT_HIP, DEFAULT_SLOPE = 150, 2000, 12
DEFAULT_Q = 3.0                               # EqualizerPointModel ctor 0x4008000000000000
UPMIX_LMSRATE_DEFAULT = 0.08                  # 0x3fb47ae147ae147b
CM_PER_MS, IN_PER_MS = 34.3, 13.5039443       # DAT_14231b450 / DAT_14231b448
MS_PER_CM, MS_PER_IN = 0.0291545189504373, 0.0740524381457942   # DAT_14231b428 / 430
CM_TO_IN, IN_TO_CM = 0.393701, 2.53999862840074                 # DAT_14231b438 / 440
MAX_DIST_CM, MAX_DIST_IN = 686.0, 270.0       # DAT_14231b460 / 458 (FUN_1400f50f0)
INPUT_GAIN_MIN, INPUT_GAIN_MAX = -48.0, 9.0   # DAT_140201480 .. DAT_1401fdd00 (FUN_1400ece60)
INPUT_DELAY_MAX_MS = 10.0                     # DAT_1401fd510 (FUN_1400ecce0), wire cap 10000 us
# Routing u64 input-id bits (DEVICE_CONFIG.md +0x568 "input mix mask" [L bit names]):
# the video's Front Right Woofer = Right+Prompt+Beeps+Phone+Voice = 0x3e agrees.
INPUT_BITS = {'Left': 0, 'Right': 1, 'Prompt': 2, 'Beeps': 3, 'Phone': 4, 'Voice': 5}
INPUT_GAIN_INDEX = {'Stereo': 1, 'Prompt': 2, 'Beeps': 3, 'Phone': 4, 'Voice': 5}   # 0x0704 sub7 entries 1..5 (fw uses 1..6)
MAX_MASTER_POINTS = 0x40                      # FUN_1400f6650 mode != 2
MAX_PRESETS = 4                               # FUN_1400ebdd0 / connect preset mask 0xf
PRESET_NAMES = ['Preset 1', 'Preset 2', 'Preset 3', 'Preset 4']
UNTITLED = 'Untitled'
UPMIX_ALGORITHMS = ['Stereo', 'Stereo0-L', 'Stereo0-R', 'Stereo1-L', 'Stereo1-R']   # upmixAlgorithmComboBox

_REL_EPS = 1e-12    # DAT_1401fcca8: the fuzzy-compare idiom used by every double setter


def _changed(old: float, new: float) -> bool:
    """The exe's eps compare (FUN_1400f4030 et al.): |a-b| > 1e-12 * min(|a|,|b|)
    with both offset by 1.0 first, so values near zero still compare sanely."""
    a, b = old + 1.0, new + 1.0
    return abs(a - b) * 1e12 > min(abs(a), abs(b))


def gain_raw(db: float, fw: int) -> int:
    """FUN_1400f8a70: round(unity + dB * 512), unity 0xED00 for fw > 207 else 0xE700."""
    return int(round((0xED00 if fw > 0xCF else 0xE700) + db * 512.0))


def gain_unity(fw: int) -> int:
    return 0xED00 if fw > 0xCF else 0xE700


# --------------------------------------------------------------------------
# EQ points
# --------------------------------------------------------------------------
class EqPoint:
    """fddsp::model::EqualizerPointModel (MODEL_REGION §1.3): freq, gain ("mag"),
    Q left (below f0), Q right (at/above f0). Setters have the eps compare only,
    NO clamps (the spin boxes clamp; uirules.py)."""

    def __init__(self, freq: float, gain: float, q1: float = DEFAULT_Q, q2: float | None = None):
        self.freq = float(freq)
        self.gain = float(gain)
        self.q1 = float(q1)
        self.q2 = float(q1 if q2 is None else q2)
        self.app_biquad = False      # +0x30: set when the owning list is mode 2

    def as_tuple(self):
        return (self.freq, self.gain, self.q1, self.q2)

    def biquad(self) -> list[float]:
        """The stored 5-float biquad = the Q1 set, float32 (FUN_1400f7200)."""
        return biquad.rbj_peaking(self.freq, self.gain, self.q1)

    def response_db(self, f_hz: float) -> float:
        return filterbins.point_response(self.as_tuple(), f_hz)

    def __repr__(self):
        return 'EqPoint(%g Hz, %g dB, Q %g/%g)' % self.as_tuple()


class EqPointList:
    """fddsp::model::EqualizerPointListModel (MODEL_REGION §1.2, §4)."""

    def __init__(self, mode: int, owner=None):
        self.mode = mode
        self.owner = owner
        self.points: list[EqPoint] = []

    def max_points(self, fw: int) -> int:
        """FUN_1400f6650: 0x40 unless mode 2; mode 2: 17 - stages (fw > 205) else 9 - stages."""
        if self.mode != MODE_BIQUAD:
            return MAX_MASTER_POINTS
        stages = self.owner.stage_count() if self.owner is not None else 1
        return (0x11 if fw > 0xCD else 9) - stages

    def add(self, freq, gain, q1=DEFAULT_Q, q2=None, fw: int = 210) -> EqPoint | None:
        """FUN_14010f340: refused (None) beyond the max."""
        if len(self.points) >= self.max_points(fw):
            return None
        p = EqPoint(freq, gain, q1, q2)
        p.app_biquad = self.mode == MODE_BIQUAD          # FUN_1400f3230 -> FUN_1400f79d0(point, 1)
        self.points.append(p)
        self._touch()
        return p

    def remove(self, point: EqPoint) -> None:
        self.points.remove(point)
        self._touch()

    def clear(self) -> None:
        self.points.clear()
        self._touch()

    def sorted(self) -> list[EqPoint]:
        return sorted(self.points, key=lambda p: p.freq)       # FUN_1400f5a40

    def curve(self) -> list[float]:
        """1024-point linear-grid crossfade curve (FUN_1400f60e0), feeds the bins."""
        return filterbins.eq_curve([p.as_tuple() for p in self.points])

    def tuples(self):
        return [p.as_tuple() for p in self.points]      # list-insertion order (wire order)

    def _touch(self):
        if self.owner is not None:
            self.owner.on_eq_changed()


# --------------------------------------------------------------------------
# Speaker
# --------------------------------------------------------------------------
class SpeakerModel:
    """fddsp::model::SpeakerModel (MODEL_REGION §1.1). One per (role, preset)."""

    def __init__(self, role: int, preset: int, root: 'DspModel | None' = None):
        self.role = role
        self.id = NAME_BY_ROLE.get(role, 'Unknown')
        self.name = DISPLAY_BY_ROLE.get(role, self.id)
        self.preset = preset
        self.root = root
        self.cls = speaker_class(role)
        # ctor: list mode 2 for class 4/8 (woofer/sub), else 1
        self.eq = EqPointList(MODE_BIQUAD if self.cls in (CLASS_WOOFER, CLASS_SUBWOOFER)
                              else MODE_DEVICE_FILTER, owner=self)
        self.filter_type = FT_NONE
        self.filter_design = FD_BUTTERWORTH
        self.f1 = DEFAULT_LOP           # +0x5c "LoP"
        self.f2 = DEFAULT_HIP           # +0x60 "HiP"
        self.slope1 = DEFAULT_SLOPE     # +0x64
        self.slope2 = DEFAULT_SLOPE     # +0x68
        self.lp_atten_db = -3.0         # +0x70 (display only)
        self.hp_atten_db = -3.0         # +0x78 (display only)
        self.invert_phase = False       # +0x80
        self.passthrough = False        # +0x81
        self.mute = False               # +0x82
        self.testtone = 0               # +0x84 (u16 on the wire)
        self.testtone_type = 0          # +0x88 (u8 on the wire)
        self.gain_db = 0.0              # +0x90, NO clamp in the setter
        self.sync = 0                   # +0x110 SyncProperty flags
        self.distance = 0.0             # +0x118 time-align distance (cm/in), no sync bit
        self.delay_ms = 0.0             # +0x120
        self.limit_dbfs = 0.0           # +0x128
        self.xover_partner: SpeakerModel | None = None   # +0x130
        self.dirty = False              # +0x08
        self.reset(notify=False)

    # --- helpers
    @property
    def mode(self) -> int:
        return self.eq.mode

    @property
    def fw(self) -> int:
        return self.root.fw if self.root is not None else 210

    def _mark(self, bit: int) -> None:
        self.sync |= bit
        self.dirty = True
        if self.root is not None:
            self.root.modified = True

    # --- reset (FUN_1400f3620)
    def reset(self, notify: bool = True) -> None:
        self.gain_db = 0.0
        self.invert_phase = self.passthrough = self.mute = False
        self.testtone = self.testtone_type = 0
        self.limit_dbfs = 0.0
        self.sync = 0
        self.eq.points.clear()
        auto = self.root.option(0) if self.root is not None else False   # root option key 0 (AddFilterAuto)
        if auto:
            t = {CLASS_TWEETER: FT_HP, CLASS_MIDRANGE: FT_BAND, CLASS_WOOFER: FT_LP,
                 CLASS_SUBWOOFER: FT_LP}.get(self.cls, FT_NONE)
        else:
            t = FT_NONE
        self.filter_type = t
        band = t == FT_BAND
        self.f2 = DEFAULT_LOP if band else DEFAULT_HIP      # +0x60 = band ? 150 : 2000
        self.f1 = DEFAULT_HIP if band else DEFAULT_LOP      # +0x5c = band ? 2000 : 150
        self.slope1 = self.slope2 = DEFAULT_SLOPE
        self.filter_design = FD_RBJ if self.mode == MODE_BIQUAD else FD_BUTTERWORTH
        self.distance = 0.0
        self.delay_ms = 0.0
        self.dirty = False

    # --- simple setters (eps compare, sync bit, dirty)
    def set_gain(self, db: float) -> bool:                  # FUN_1400f4030, no clamp
        if not _changed(self.gain_db, db):
            return False
        self.gain_db = float(db)
        # [CAP] the vehicle capture shows every channel-record write followed by
        # 0x0702 + 0x0708 + 0x0704 sub5 (the UI path recomputes the curves), so a
        # record change also marks bins / biquads / EQ list.
        self._mark(SYNC_GAIN | SYNC_BINS | SYNC_IIR | SYNC_EQ)
        return True

    def set_invert_phase(self, on: bool) -> bool:            # FUN_1400f4370
        if self.invert_phase == bool(on):
            return False
        self.invert_phase = bool(on)
        self._mark(SYNC_INVERT_PHASE)        # CH1_CONTROL_FRAMES.md / decomp: a lone 0x0700
        return True

    def set_passthrough(self, on: bool) -> bool:             # FUN_1400f38e0
        if self.passthrough == bool(on):
            return False
        self.passthrough = bool(on)
        self._mark(SYNC_PASSTHROUGH)        # CH1_CONTROL_FRAMES.md / decomp: a lone 0x0700
        return True

    def set_mute(self, on: bool) -> bool:                    # FUN_1400f4790
        if self.mute == bool(on):
            return False
        self.mute = bool(on)
        self._mark(SYNC_MUTE)        # capture 2026-10-21 @4347/@4369: a lone 0x0700 on and off
        return True

    def set_testtone(self, value: int) -> bool:              # FUN_1400f4820
        if self.testtone == int(value):
            return False
        self.testtone = int(value)
        self._mark(SYNC_TESTTONE)        # CH1_CONTROL_FRAMES.md / decomp: a lone 0x0700
        return True

    def set_delay(self, ms: float) -> bool:                  # FUN_1400f48b0, no clamp
        if not _changed(self.delay_ms, ms):
            return False
        self.delay_ms = float(ms)
        self._mark(SYNC_TIME)        # CH1_CONTROL_FRAMES.md / decomp: a lone 0x0700
        return True

    def set_limit(self, dbfs: float) -> bool:                # FUN_1400f4400, no clamp
        if not _changed(self.limit_dbfs, dbfs):
            return False
        self.limit_dbfs = float(dbfs)
        self._mark(SYNC_LIMIT)        # CH1_CONTROL_FRAMES.md / decomp: a lone 0x0700
        return True

    def set_distance(self, d: float) -> bool:                # FUN_1400f4a10, no sync bit
        if not _changed(self.distance, d):
            return False
        self.distance = float(d)
        if self.root is not None:
            self.root.modified = True
        return True

    # --- crossover (FUN_1400f4500 / 4120 / 4650 / 4270 / 3ad0 / 3e80 / 3c50)
    @staticmethod
    def clamp_cutoff(hz: int) -> int:
        return max(CUTOFF_MIN, min(CUTOFF_MAX, int(hz)))

    @staticmethod
    def clamp_slope(s: int) -> int:
        return max(SLOPE_MIN, min(SLOPE_MAX, int(s)))

    def set_lop(self, hz: int) -> None:                      # FUN_1400f4500
        v = self.clamp_cutoff(hz)
        if v != self.f1:
            self.f1 = v
            self._filter_changed()

    def set_hip(self, hz: int) -> None:                      # FUN_1400f4120
        v = self.clamp_cutoff(hz)
        if v != self.f2:
            self.f2 = v
            self._filter_changed()

    def set_slope1(self, s: int) -> None:                    # FUN_1400f4650
        v = self.clamp_slope(s)
        if v != self.slope1:
            self.slope1 = v
            self._filter_changed()

    def set_slope2(self, s: int) -> None:                    # FUN_1400f4270
        v = self.clamp_slope(s)
        if v != self.slope2:
            self.slope2 = v
            self._filter_changed()

    def set_design(self, design: int) -> bool:               # FUN_1400f3ad0: refused in mode 2
        if self.mode == MODE_BIQUAD or design not in FILTER_DESIGN_NAMES:
            return False
        if design != self.filter_design:
            self.filter_design = design
            self._filter_changed()
        return True

    def set_filter_type(self, t: int) -> None:
        """FUN_1400f3e80: type with its cutoff defaults / Band swap rules."""
        old = self.filter_type
        if t == FT_LP:
            if self.f1 == 0:
                self.f1 = DEFAULT_LOP
            elif old == FT_BAND:
                self.f1 = self.f2
        elif t == FT_HP:
            if self.f2 == 0:
                self.f2 = DEFAULT_HIP
            elif old == FT_BAND:
                self.f2 = self.f1
        elif t == FT_BAND:
            if self.f2 - self.f1 < BAND_MIN_GAP_HZ:
                self.f1, self.f2 = DEFAULT_LOP, DEFAULT_HIP
            f1, f2 = self.f1, self.f2
            # Band: f1 (LoP) is the UPPER cutoff, f2 (HiP) the lower one
            self.f1 = self.clamp_cutoff(DEFAULT_HIP if f1 == 0 else f2 if f2 else DEFAULT_HIP)
            self.f2 = self.clamp_cutoff(f1 if f2 != 0 else DEFAULT_LOP)
            if self.f1 - self.f2 < BAND_MIN_GAP_HZ:          # FUN_1400fe280
                self.f1 = self.clamp_cutoff(self.f2 + BAND_MIN_GAP_HZ)
        self.filter_type = t
        self._filter_changed()

    def set_filter(self, type_=-1, design=-1, f1=-1, f2=-1, s1=-1, s2=-1, _propagate=True) -> None:
        """FUN_1400f3c50 bulk set (-1 = keep), same clamps, design ignored in mode 2,
        propagated to the x-over linked speaker."""
        if type_ != -1: self.filter_type = int(type_)
        if design != -1 and self.mode != MODE_BIQUAD: self.filter_design = int(design)
        if f1 != -1: self.f1 = self.clamp_cutoff(f1)
        if f2 != -1: self.f2 = self.clamp_cutoff(f2)
        if s1 != -1: self.slope1 = self.clamp_slope(s1)
        if s2 != -1: self.slope2 = self.clamp_slope(s2)
        self._filter_changed()
        if _propagate and self.xover_partner is not None:
            self.xover_partner.set_filter(type_, design, f1, f2, s1, s2, _propagate=False)

    def _filter_changed(self) -> None:
        # FUN_1400fc300 -> FUN_1400fe280: 0x200 (+0x800 biquad in mode 2, +0x400 bins)
        bits = SYNC_FILTER | SYNC_BINS
        if self.mode == MODE_BIQUAD:
            bits |= SYNC_IIR
        self._mark(bits)

    def on_eq_changed(self) -> None:
        # FUN_1400f30d0: an EQ edit marks the point list (0x1000) and, in mode 2,
        # the biquad chain (0x800). [CAP] the vehicle capture shows 0x0708 + 0x0704
        # sub5 and NO 0x0702 after EQ edits, so bins are not marked here.
        bits = SYNC_EQ
        if self.mode == MODE_BIQUAD:
            bits |= SYNC_IIR
        self._mark(bits)

    def stage_count(self) -> int:
        """FUN_1400f3320: mode != 2 -> 1; LP s1/12; HP s2/12; Band max(s1,s2)/12; else 1."""
        if self.mode != MODE_BIQUAD:
            return 1
        if self.filter_type == FT_LP:
            return self.slope1 // 12
        if self.filter_type == FT_HP:
            return self.slope2 // 12
        if self.filter_type == FT_BAND:
            return max(self.slope1, self.slope2) // 12
        return 1

    def crossover_stages(self) -> list[list[float]]:
        """The crossover biquad(s) for mode 2 (FUN_1400fdcf0 math via biquad.py);
        a single zero stage when there is no filter (FUN_1400f3b80 zeros)."""
        if self.mode != MODE_BIQUAD or self.filter_type == FT_NONE:
            return [list(biquad.ZERO_STAGE)]
        if self.filter_type == FT_LP:
            return biquad.crossover_stages('lpf', self.f1, self.slope1)
        if self.filter_type == FT_HP:
            return biquad.crossover_stages('hpf', self.f2, self.slope2)
        return biquad.crossover_stages('band', self.f1, self.slope1, self.f2, self.slope2)

    def biquad_chain(self) -> list[list[float]]:
        """SyncIirBiquad payload order: crossover stage(s) then one peaking stage
        per EQ point in list order, max 16 (FUN_1400fa060 0x800)."""
        chain = list(self.crossover_stages())
        for p in self.eq.points:
            if len(chain) >= biquad.MAX_STAGES:
                break
            chain.append(p.biquad())
        return chain

    def bins_curve(self) -> list[float]:
        """+0x4138: EQ crossfade curve + crossover response (mode 1 only; FUN_1400fbf60)."""
        if self.mode == MODE_DEVICE_FILTER and self.filter_type != FT_NONE:
            from . import crossover_curve as _xc
            return _xc.combine(self.eq.curve(), _xc.crossover_curve(
                self.filter_type, self.filter_design, self.f1, self.f2, self.slope1, self.slope2))
        return self.eq.curve()

    def link_xover(self, other: 'SpeakerModel | None') -> None:
        """FUN_1400f4af0: links both ways and copies this filter to the partner."""
        if self.xover_partner is other:
            return
        if self.xover_partner is not None:
            old, self.xover_partner = self.xover_partner, None
            old.xover_partner = None
            old._mark(SYNC_XOVER_LINK)
        self.xover_partner = other
        if other is None:
            self._mark(SYNC_XOVER_LINK)
            return
        other.xover_partner = self
        other.filter_type = self.filter_type
        if other.mode != MODE_BIQUAD:
            other.filter_design = self.filter_design
        other.f1, other.f2 = self.f1, self.f2
        other.slope1, other.slope2 = self.slope1, self.slope2
        # capture 2026-10-22 19:34:32 (CH1/CH2 woofers): each side 0x0708 + 0x0704
        # sub 2 + sub 3, no 0x0702
        for spk in (self, other):
            spk._mark(SYNC_XOVER_LINK | SYNC_FILTER | (SYNC_IIR if spk.mode == MODE_BIQUAD else 0))

    def mark_all(self) -> None:          # FUN_1400f4ce0 (= 0x1fff)
        self._mark(SYNC_ALL)

    def clear_sync(self) -> None:        # FUN_1400f4d70
        self.sync = 0


# --------------------------------------------------------------------------
# Master / Upmix / TimeAlign / Headunit
# --------------------------------------------------------------------------
class MasterModel:
    """MODEL_REGION §1.4: gain + EQ list (mode 0) + flags (0x61 all). Volume points
    exist in the XML but never reach the device; kept as raw (notch, mag) pairs."""

    def __init__(self, preset: int, root: 'DspModel | None' = None):
        self.preset, self.root = preset, root
        self.gain_db = 0.0
        self.eq = EqPointList(MODE_MASTER, owner=self)
        self.volume_points: list[tuple[int, float]] = []
        self.flags = 0

    def _mark(self, bit):
        self.flags |= bit
        if self.root is not None:
            self.root.modified = True

    def set_gain(self, db: float) -> bool:                    # FUN_1400f6ef0
        if not _changed(self.gain_db, db):
            return False
        self.gain_db = float(db)
        self._mark(MASTER_GAIN | MASTER_BINS | MASTER_EQ)   # [CAP] LEN4 -> 0x0702 ch0 -> 0704 06
        return True

    def on_eq_changed(self):
        self._mark(MASTER_EQ | MASTER_BINS)

    def stage_count(self):
        return 1

    def reset(self):
        self.gain_db = 0.0
        self.eq.points.clear()
        self.volume_points.clear()
        self.flags = 0

    def bins_curve(self) -> list[float]:                      # FUN_1400fc050
        return self.eq.curve()


class UpmixModel:
    """MODEL_REGION §1.5: exact compares, no clamps."""

    def __init__(self, preset: int, root=None):
        self.preset, self.root = preset, root
        self.enabled = False
        self.algorithm = 0
        self.lmsrate = UPMIX_LMSRATE_DEFAULT
        self.adprate = 0.0
        self.adpgamma = 0.0
        self.flags = 0

    def _set(self, attr, value, bit):
        if getattr(self, attr) == value:
            return False
        setattr(self, attr, value)
        self.flags |= bit
        if self.root is not None:
            self.root.modified = True
        return True

    def set_enabled(self, on): return self._set('enabled', bool(on), UPMIX_ENABLED)
    def set_algorithm(self, a): return self._set('algorithm', int(a), UPMIX_ALGORITHM)
    def set_lmsrate(self, v): return self._set('lmsrate', float(v), UPMIX_LMSRATE)
    def set_adprate(self, v): return self._set('adprate', float(v), UPMIX_ADPRATE)
    def set_adpgamma(self, v): return self._set('adpgamma', float(v), UPMIX_ADPGAMMA)

    def reset(self):
        self.enabled, self.algorithm = False, 0
        self.lmsrate, self.adprate, self.adpgamma = UPMIX_LMSRATE_DEFAULT, 0.0, 0.0
        self.flags = 0


class TimeAlignModel:
    """MODEL_REGION §1.6 + UI_REGION_1 §5.1. units 0 = cm, 1 = in."""

    def __init__(self, preset: int, root: 'DspModel | None' = None):
        self.preset, self.root = preset, root
        self.units = 0
        self.closest = 0.0      # +0x20 distanceclosest
        self.furthest = 0.0     # +0x18 distancefurthest
        self.flags = 0

    def _mark(self, bit):
        self.flags |= bit
        if self.root is not None:
            self.root.modified = True

    @property
    def unit_label(self) -> str:          # FUN_1400f5120
        return 'in' if self.units == 1 else 'cm'

    @property
    def max_distance(self) -> float:      # FUN_1400f50f0
        return MAX_DIST_IN if self.units == 1 else MAX_DIST_CM

    def _speakers(self):
        return self.root.speakers_in_preset(self.preset) if self.root is not None else []

    def set_closest(self, d):
        if _changed(self.closest, d):
            self.closest = float(d); self._mark(TA_CLOSEST)

    def set_furthest(self, d):
        if _changed(self.furthest, d):
            self.furthest = float(d); self._mark(TA_FURTHEST)

    def set_units(self, units: int) -> None:
        """FUN_1400f5330: rescales every speaker distance and both bounds."""
        units = 1 if units else 0
        if units == self.units:
            return
        k = CM_TO_IN if units == 1 else IN_TO_CM
        for s in self._speakers():
            s.set_distance(s.distance * k)
        self.closest *= k
        self.furthest *= k
        self.units = units
        self._mark(TA_UNITS | TA_CLOSEST | TA_FURTHEST)

    def delay_for_distance(self, d: float) -> float:
        """FUN_1400f51d0: delay = K * (furthest - d), 0 when negative."""
        x = self.furthest - d
        if x < 0.0:
            return 0.0
        return x * (MS_PER_IN if self.units else MS_PER_CM)

    def distances_to_delays(self) -> None:
        """FUN_1400f56d0: nearest/farthest from the speakers, then every delay."""
        spk = self._speakers()
        if not spk:
            return
        far = max(s.distance for s in spk)
        near = min(min(s.distance for s in spk), self.max_distance)
        self.set_closest(near)
        self.set_furthest(far)
        for s in spk:
            s.set_delay(self.delay_for_distance(s.distance))

    def delays_to_distances(self) -> None:
        """FUN_1400f5890: furthest = max(delay)*k + closest; dist = furthest - k*delay."""
        spk = self._speakers()
        if not spk:
            return
        k = IN_PER_MS if self.units == 1 else CM_PER_MS
        self.furthest = max(s.delay_ms for s in spk) * k + self.closest
        for s in spk:
            s.set_distance(self.furthest - k * s.delay_ms)

    def reset(self, zero_channels: bool = True) -> None:       # FUN_1400f5210
        self.closest = self.furthest = 0.0
        if zero_channels:
            for s in self._speakers():
                s.set_delay(0.0)
                s.set_distance(0.0)
        self.flags = 0


class HeadunitModel:
    """MODEL_REGION §1.7: seven u8 values, dirty bits 1..0x40 (0x0705 index 0..6)."""
    # 0x0705 index order: firmware jump table (FW_LOCKED_WRITES_OCT22 §1); the exe
    # stores fade at +0xdb4ef / balance at +0xdb4f0 but dirty bits 0x10 / 0x08
    # make the same swap, so index 3 = balance, 4 = fade.
    FIELDS = ('bass', 'mid', 'treble', 'balance', 'fade', 'volume', 'subgain')   # 6 = subgain (bit 0x40)

    def __init__(self, root=None):
        self.root = root
        self.values = dict.fromkeys(self.FIELDS, 0)
        self.flags = 0

    def set(self, field: str, value: int) -> bool:
        if self.values[field] == int(value) & 0xFF:
            return False
        self.values[field] = int(value) & 0xFF
        self.flags |= 1 << self.FIELDS.index(field)
        if self.root is not None:
            self.root.modified = True
        return True


# --------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------
class Preset:
    def __init__(self, index: int, root: 'DspModel'):
        self.index = index
        self.name = PRESET_NAMES[index] if index < len(PRESET_NAMES) else 'Preset %d' % (index + 1)
        self.master = MasterModel(index, root)
        self.upmix = UpmixModel(index, root)
        self.timealign = TimeAlignModel(index, root)
        self.speakers: dict[int, SpeakerModel] = {}


class DspModel:
    """fddsp::model::DspModel root (MODEL_REGION §1.8, UI_REGION_1 §3)."""

    def __init__(self, fw: int = 0, product: int = 0):
        self.fw = fw                       # +0x1c068 (0 = offline/unknown)
        self.product = product             # +0x1c06c
        self.name = UNTITLED               # +0x1c070 (device/project name, 0x40 bytes on the wire)
        self.ampserial = ''                # +0x1c358
        self.path: str | None = None       # +0x1c090 project file path
        self.current_preset = 0            # +0x1c0b0
        self.preset_count = MAX_PRESETS    # +0x1c0b4
        self.presets = [Preset(i, self) for i in range(MAX_PRESETS)]
        self.channel_role: dict[int, int] = {}   # +0x1c110: channel -> role bit
        self.role_channel: dict[int, int] = {}   # +0x1c100: role bit -> channel
        self.enabled_mask = 0                    # +0x1c0fc
        self.channel_count = 0                   # dev+0x48 (from the product table)
        self.input_map: dict[int, int] = {}      # +0x1c150: channel -> u64 input id
        self.routing_read = False                # input_map came whole from a 0x0711 read (devread); gates live 0x0701
        self.input_gains = [[0.0] * 65 for _ in range(MAX_PRESETS)]   # [preset][k 1..64] dB
        self.input_delay_ms = [0.0] * MAX_PRESETS                      # +0x1cc18 (dev+0x458)
        self.sourcesel = 0                       # +0x1c350 (0x0703 byte, meaning unresolved)
        self.eq_point_cap = 12                   # FUN_1400eeb10 uVar5: 15 with settings key 2, else 12
        self.options: dict[int, bool] = {}       # +0x1c140 (0 AddFilterAuto, 2 biquad mode, 3 loudness, 4 headunitbassctrlsub, 5 rearfill)
        self.headunit = HeadunitModel(self)
        self.root_flags = 0                      # +0x1c378
        self.modified = False                    # vfunc 0x58 isModified
        self.connected = False
        self.feature_raw: dict = {}              # 0x070f sub 0/1 records (kept as read; no screen edits them)

    # --- presets / speakers
    def preset(self, index: int | None = None) -> Preset:
        return self.presets[self.current_preset if index is None or index < 0 else index]

    def speakers_in_preset(self, preset: int) -> list[SpeakerModel]:
        p = self.presets[preset]
        return [p.speakers[r] for r in sorted(p.speakers) if r in self.role_channel]

    def speaker(self, role: int, preset: int | None = None) -> SpeakerModel:
        """FUN_1400e9070: the SpeakerModel for a role in a preset (created on demand)."""
        p = self.preset(preset)
        if role not in p.speakers:
            p.speakers[role] = SpeakerModel(role, p.index, self)
        return p.speakers[role]

    def speaker_on_channel(self, channel: int, preset: int | None = None) -> SpeakerModel | None:
        role = self.channel_role.get(channel)
        return self.speaker(role, preset) if role else None

    def channel_for_role(self, role: int) -> int:      # FUN_1400ed740: -1 if none
        return self.role_channel.get(role, -1)

    # --- mapping (FUN_1400eaef0 / FUN_1400ee5e0 / FUN_1400ee490)
    def map_speaker(self, role: int, channel: int) -> None:
        old = self.channel_role.get(channel)
        if old:
            self.role_channel.pop(old, None)
        prev = self.role_channel.get(role)
        if prev is not None:
            self.channel_role.pop(prev, None)
        self.channel_role[channel] = role
        self.role_channel[role] = channel
        self.enabled_mask |= role
        self.root_flags |= ROOT_MAP
        self.modified = True
        for p in self.presets:                            # FUN_1400f4ce0 on every preset
            self.speaker(role, p.index).mark_all()

    def unmap_channel(self, channel: int) -> None:
        role = self.channel_role.pop(channel, None)
        if role:
            self.role_channel.pop(role, None)
            for p in self.presets:
                self.speaker(role, p.index)._mark(SYNC_MAP)
        self.root_flags |= ROOT_MAP
        self.modified = True

    def unmap_all(self) -> None:                           # actionToolUnmapAll
        for ch in list(self.channel_role):
            self.unmap_channel(ch)
        self.channel_role.clear()
        self.role_channel.clear()

    def enable_speaker(self, role: int, on: bool) -> None:   # FUN_1400e8680 / FUN_1400e7f50
        if on:
            self.enabled_mask |= role
        else:
            self.enabled_mask &= ~role
        self.modified = True

    def enabled_roles(self) -> int:                          # FUN_1400e8800: enabled | mapped
        m = self.enabled_mask
        for r in self.role_channel:
            m |= r
        return m

    def channels(self) -> list[int]:
        return sorted(self.channel_role)

    # --- options
    def option(self, key: int) -> bool:
        return bool(self.options.get(key, False))

    def set_option(self, key: int, value: bool) -> None:     # FUN_1400ed170
        self.options[key] = bool(value)
        if key in (3, 4, 5):
            self.root_flags |= ROOT_OPTIONS
            self.modified = True
        if key == 2:
            self._apply_biquad_mode()

    def _apply_biquad_mode(self) -> None:
        """FUN_1400eeb10 (PRODUCT_FLAG_MASKS §2.3): fw < 203 -> mode 1 with a
        12-point cap; fw >= 203 -> mode 2 (Biquad) on EVERY channel, cap 15 when
        settings key 2 (product flag bit 17) is set, else 12."""
        if self.fw < 0xCB:
            mode, self.eq_point_cap = MODE_DEVICE_FILTER, 12
        else:
            mode, self.eq_point_cap = MODE_BIQUAD, (15 if self.option(2) else 12)
        for p in self.presets:
            for s in p.speakers.values():
                s.eq.mode = mode

    def option_bits(self) -> int:                            # dev+0x460
        return (1 if self.option(3) else 0) | (2 if self.option(4) else 0) | (4 if self.option(5) else 0)

    # --- root setters
    def set_name(self, name: str) -> None:
        if name != self.name:
            self.name = name
            self.root_flags |= ROOT_NAME
            self.modified = True

    def select_preset(self, index: int) -> bool:              # FUN_1400ed320
        if not 0 <= index < self.preset_count:
            return False
        if index != self.current_preset:
            self.current_preset = index
            self.root_flags |= ROOT_PRESET
        return True

    def set_preset_count(self, n: int) -> None:               # FUN_1400ecae0
        self.preset_count = n if (0 < n < 5) else 4

    def set_input_gain(self, k: int, db: float, preset: int | None = None) -> None:
        """FUN_1400ece60: clamped to -48..+9 dB; k outside 1..64 -> 1."""
        if not 1 <= k <= 64:
            k = 1
        v = db if INPUT_GAIN_MIN <= db <= INPUT_GAIN_MAX else (INPUT_GAIN_MIN if db < INPUT_GAIN_MIN else INPUT_GAIN_MAX)
        p = self.current_preset if preset is None or preset < 0 else preset
        if self.input_gains[p][k] != v:
            self.input_gains[p][k] = float(v)
            self.root_flags |= ROOT_INPUT_GAINS
            self.modified = True

    def set_input_delay(self, ms: float, preset: int | None = None) -> None:
        """FUN_1400ecce0: 0..10 ms, out of range -> 10."""
        v = ms if 0.0 <= ms <= INPUT_DELAY_MAX_MS else INPUT_DELAY_MAX_MS
        p = self.current_preset if preset is None or preset < 0 else preset
        if self.input_delay_ms[p] != v:
            self.input_delay_ms[p] = float(v)
            self.root_flags |= ROOT_INPUT_DELAY
            self.modified = True

    def set_input_map(self, channel: int, input_id: int) -> None:   # FUN_1400ecfb0
        if channel < 64 and self.input_map.get(channel) != input_id:
            self.input_map[channel] = int(input_id)
            self.root_flags |= ROOT_INPUT_MAP
            self.modified = True

    def set_sourcesel(self, value: int) -> None:              # FUN_1400ed580
        if self.sourcesel != value & 0xFF:
            self.sourcesel = value & 0xFF
            self.root_flags |= ROOT_SOURCESEL
            self.modified = True

    # DAT_149d16900: the exe's 7 bidirectional crossover partner pairs (CROSSOVER_PARTNERS.md)
    XOVER_PAIRS = ((0x001, 0x008), (0x002, 0x010), (0x004, 0x020), (0x040, 0x200),
                   (0x080, 0x400), (0x100, 0x800), (0x1000, 0x2000))

    def link_xover_partner(self, s: SpeakerModel) -> SpeakerModel | None:
        """FUN_1400ed5d0: xoverlink = link with the partner from the pair table
        (match on either side); a role not in the table has no partner."""
        for a, b in self.XOVER_PAIRS:
            if s.role in (a, b):
                partner = b if s.role == a else a
                if partner in self.role_channel:
                    other = self.speaker(partner, s.preset)
                    s.link_xover(other)
                    return other
                return None
        return None

    # --- tools
    def all_muted(self, preset: int | None = None) -> bool:   # FUN_1400e56e0(model, -1)
        spk = self.speakers_in_preset(self.preset(preset).index)
        return bool(spk) and all(s.mute for s in spk)

    def mute_all(self, on: bool, preset: int | None = None) -> None:   # FUN_1400eb700
        for s in self.speakers_in_preset(self.preset(preset).index):
            s.set_mute(on)

    def set_fw(self, fw: int) -> None:                         # FUN_1400ecca0
        crossed = (self.fw < 0xCB) != (fw < 0xCB)
        self.fw = fw
        if crossed:
            self._apply_biquad_mode()

    def new_tune(self, connected: bool, clear_product: bool = False, clear_fw: bool = False) -> None:
        """FUN_1400ebdd0: "Untitled", preset 0, 4 presets; every preset's master EQ,
        time-align (listening point 0,0 + every delay/distance 0) and upmix reset.
        Offline (not connected) the channel settings are reset too."""
        self.name = UNTITLED
        self.current_preset = 0
        self.preset_count = MAX_PRESETS
        if clear_fw:
            self.fw = 0
        if clear_product:
            self.product = 0
        for p in self.presets:
            p.master.reset()
            p.upmix.reset()
            p.timealign.reset(zero_channels=True)
            if not connected:
                for s in p.speakers.values():
                    s.reset(notify=False)
        self.path = None
        self.modified = False
        self.root_flags = 0
        self.connected = connected

    def is_modified(self) -> bool:
        return self.modified

    def clear_dirty(self) -> None:
        """Forget every pending-write bit (after a device read or a flush)."""
        self.root_flags = 0
        self.headunit.flags = 0
        self.modified = False
        for p in self.presets:
            p.master.flags = p.upmix.flags = p.timealign.flags = 0
            for s in p.speakers.values():
                s.sync = 0
                s.dirty = False
