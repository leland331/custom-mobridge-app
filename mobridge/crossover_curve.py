"""Mode-1 (tweeter / midrange) crossover response curve for the 0x0702 table.

The official app designs these channels' crossovers on the DEVICE (0x0704 sub3
carries type/design/cutoffs/slopes); the host only plots the response and bakes
it into the 1 KiB bins table (MODEL_REGION.md §2.3 / §4: FUN_1400fe280 ->
FUN_1400fda10 -> +0x138, summed with the EQ curve by FUN_14010f020). The plot
objects are Vinnie Falco's DSPFilters classes (RTTI in the exe):

  design 0 Butterworth      LowPass<8> / HighPass<8>, order = slope/6
  design 1 Chebyshev I      ripple 0.7 dB (DAT_14231c0c8)
  design 2 Bessel
  design 3 Linkwitz-Riley   = Butterworth, magnitude squared ("repeat" 1)
  design 4 RBJ ("Biquad")   q = 0.7, repeated slope/12 - 1 extra times

This file is an INDEPENDENT port of DSPFilters written from the library (not
from re/scripts/ref_mode1_bins.py); tools/mode1_sweep_check.py diffs it against
the exe thread's decompile-only reference (0 of 720 cases differ). The three
constants below were read from the exe's .rdata on 2026-10-22 (0.7, 0.99,
1.01), so under the project's "decompile alone" rule the tables are OPEN
(dspwrite.MODE1_UNLOCKED).
"""
from __future__ import annotations

import cmath
import math

FS = 48000.0
RIPPLE_DB = 0.7                 # DAT_14231c0c8
RBJ_Q = 0.7                     # same constant for the RBJ display object
DEAD_LO, DEAD_HI = 0.99, 1.01   # DAT_14231c0d0 / c0d8: |H| strictly inside -> 0 dB
DBL_MAX = 1.7976931348623157e308
GRID = [(i + 1) * 24000.0 / 1024 for i in range(1024)]
_INF = complex(math.inf, 0.0)


# ----------------------------------------------------------------- DSPFilters
class _Stage:
    """Dsp::Biquad: H(z) = (b0 + b1 z^-1 + b2 z^-2) / (1 + a1 z^-1 + a2 z^-2)."""

    def __init__(self, b0, b1, b2, a1, a2):
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0, b1, b2, a1, a2

    def response(self, w: float) -> complex:
        z1 = cmath.exp(complex(0.0, -w))
        z2 = cmath.exp(complex(0.0, -2.0 * w))
        return (self.b0 + self.b1 * z1 + self.b2 * z2) / (1.0 + self.a1 * z1 + self.a2 * z2)


def _stage_from_pair(pole: complex, zero: complex) -> _Stage:
    """Dsp::Cascade::setLayout -> setPoleZeroPair for a conjugate pair."""
    a1, a2 = -2.0 * pole.real, abs(pole) ** 2
    b1, b2 = -2.0 * zero.real, abs(zero) ** 2
    return _Stage(1.0, b1, b2, a1, a2)


def _stage_from_single(pole: complex, zero: complex) -> _Stage:
    """Dsp::BiquadBase::setOnePole (odd order)."""
    return _Stage(1.0, -zero.real, 0.0, -pole.real, 0.0)


class _Layout:
    def __init__(self):
        self.pairs: list[tuple[complex, complex]] = []   # conjugate pairs (pole, zero)
        self.single: tuple[complex, complex] | None = None
        self.normal_w = 0.0
        self.normal_gain = 1.0


def _butterworth(n: int) -> _Layout:
    """Dsp::Butterworth::AnalogLowPass::design."""
    lay = _Layout()
    n2 = 2.0 * n
    for i in range(n // 2):
        lay.pairs.append((cmath.exp(complex(0.0, math.pi / 2 + (2 * i + 1) * math.pi / n2)), _INF))
    if n & 1:
        lay.single = (complex(-1.0, 0.0), _INF)
    return lay


def _chebyshev1(n: int, ripple_db: float) -> _Layout:
    """Dsp::ChebyshevI::AnalogLowPass::design."""
    lay = _Layout()
    eps = math.sqrt(1.0 / math.exp(-0.1 * ripple_db * math.log(10.0)) - 1.0)
    v0 = math.asinh(1.0 / eps) / n
    sinh_v0, cosh_v0 = -math.sinh(v0), math.cosh(v0)
    n2 = 2.0 * n
    for i in range(1, n // 2 + 1):
        k = 2 * i - 1
        a = sinh_v0 * math.cos((k - n) * math.pi / n2)
        b = cosh_v0 * math.sin((k - n) * math.pi / n2)
        lay.pairs.append((complex(a, b), _INF))
    if n & 1:
        lay.single = (complex(sinh_v0, 0.0), _INF)
    lay.normal_gain = 1.0 if (n & 1) else math.pow(10.0, -ripple_db / 20.0)
    return lay


def _reverse_bessel(n: int) -> list[float]:
    """Dsp::Bessel::reversebessel: coefficients a_k = (2n-k)! / (2^(n-k) k! (n-k)!), ascending."""
    return [math.factorial(2 * n - k) / (2.0 ** (n - k) * math.factorial(k) * math.factorial(n - k))
            for k in range(n + 1)]


def _roots(coef: list[float]) -> list[complex]:
    """Roots of a real polynomial (ascending coefficients). DSPFilters uses a
    Laguerre solver; any solver gives the same roots to double precision, and the
    roots are polished with Newton steps here."""
    n = len(coef) - 1
    a = [c / coef[-1] for c in coef]
    rs = [cmath.exp(complex(0.0, 2 * math.pi * k / n + 0.4)) for k in range(n)]
    for _ in range(1000):
        new = []
        for i, r in enumerate(rs):
            num = sum(a[k] * r ** k for k in range(n + 1))
            den = 1.0
            for j, s in enumerate(rs):
                if j != i:
                    den *= (r - s)
            new.append(r - num / den)
        done = max(abs(x - y) for x, y in zip(new, rs)) < 1e-16
        rs = new
        if done:
            break
    out = []
    for r in rs:
        for _ in range(50):
            f = sum(a[k] * r ** k for k in range(n + 1))
            df = sum(k * a[k] * r ** (k - 1) for k in range(1, n + 1))
            if df == 0:
                break
            step = f / df
            r -= step
            if abs(step) < 1e-18:
                break
        out.append(r)
    return out


def _bessel(n: int) -> _Layout:
    """Dsp::Bessel::AnalogLowPass::design: roots of the reverse Bessel polynomial,
    conjugate pairs (positive imaginary kept), the real root as the single stage."""
    lay = _Layout()
    roots = _roots(_reverse_bessel(n))
    pairs = sorted([r for r in roots if r.imag > 1e-12], key=lambda c: -c.imag)
    for r in pairs:
        lay.pairs.append((complex(r.real, r.imag), _INF))
    reals = [r for r in roots if abs(r.imag) <= 1e-12]
    if n & 1:
        lay.single = (complex(reals[0].real, 0.0), _INF)
    return lay


def _lowpass(analog: _Layout, fc_norm: float) -> _Layout:
    """Dsp::LowPassTransform: s -> z bilinear with f = tan(pi fc)."""
    f = math.tan(math.pi * fc_norm)

    def tr(c: complex) -> complex:
        if c.real == math.inf:
            return complex(-1.0, 0.0)
        c = f * c
        return (1.0 + c) / (1.0 - c)
    dig = _Layout()
    dig.pairs = [(tr(p), tr(z)) for p, z in analog.pairs]
    dig.single = (tr(analog.single[0]), tr(analog.single[1])) if analog.single else None
    dig.normal_w, dig.normal_gain = analog.normal_w, analog.normal_gain
    return dig


def _highpass(analog: _Layout, fc_norm: float) -> _Layout:
    """Dsp::HighPassTransform: f = 1/tan(pi fc), c -> -(1+c)/(1-c), normal at pi."""
    f = 1.0 / math.tan(math.pi * fc_norm)

    def tr(c: complex) -> complex:
        if c.real == math.inf:
            return complex(1.0, 0.0)
        c = f * c
        return -(1.0 + c) / (1.0 - c)
    dig = _Layout()
    dig.pairs = [(tr(p), tr(z)) for p, z in analog.pairs]
    dig.single = (tr(analog.single[0]), tr(analog.single[1])) if analog.single else None
    dig.normal_w, dig.normal_gain = math.pi, analog.normal_gain
    return dig


class Cascade:
    """Dsp::Cascade: stages + the scale applied to stage 0 so |H(normal_w)| = normal_gain."""

    def __init__(self, stages: list[_Stage], normal_w: float | None = None, normal_gain: float = 1.0):
        self.stages = stages
        if normal_w is not None and stages:
            r = abs(self.response_w(normal_w))
            scale = normal_gain / r if r != 0.0 else 1.0
            s0 = stages[0]
            s0.b0, s0.b1, s0.b2 = s0.b0 * scale, s0.b1 * scale, s0.b2 * scale

    @classmethod
    def from_layout(cls, lay: _Layout) -> 'Cascade':
        stages = [_stage_from_pair(p, z) for p, z in lay.pairs]
        if lay.single:
            stages.append(_stage_from_single(*lay.single))
        return cls(stages, lay.normal_w, lay.normal_gain)

    def response_w(self, w: float) -> complex:
        h = complex(1.0, 0.0)
        for s in self.stages:
            h *= s.response(w)
        return h

    def magnitude(self, f_hz: float) -> float:
        return abs(self.response_w(2.0 * math.pi * f_hz / FS))


def rbj(kind: str, fc_hz: float, q: float) -> Cascade:
    """Dsp::RBJ::LowPass / HighPass (Audio EQ Cookbook)."""
    w0 = 2.0 * math.pi * fc_hz / FS
    cs, sn = math.cos(w0), math.sin(w0)
    al = sn / (2.0 * q)
    if kind == 'lp':
        b0 = (1.0 - cs) / 2.0; b1 = 1.0 - cs; b2 = b0
    else:
        b0 = (1.0 + cs) / 2.0; b1 = -(1.0 + cs); b2 = b0
    a0, a1, a2 = 1.0 + al, -2.0 * cs, 1.0 - al
    return Cascade([_Stage(b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)])


def order_from_slope(slope: int) -> int:
    """FUN_1400fe280: slope/6 when slope % 6 == 0 and slope <= 48, else 2."""
    return slope // 6 if (slope % 6 == 0 and slope <= 48) else 2


def design(kind: str, design_id: int, order: int, cutoff_hz: float) -> tuple[Cascade, int]:
    """The DSPFilters object FUN_1400fe280 builds and its 'repeat' count
    (how many extra times the magnitude is multiplied in)."""
    if design_id == 4:
        return rbj(kind, cutoff_hz, RBJ_Q), max(order * 6 // 12 - 1, 0)
    if design_id == 1:
        analog = _chebyshev1(order, RIPPLE_DB)
    elif design_id == 2:
        analog = _bessel(order)
    else:
        analog = _butterworth(order)           # 0 Butterworth, 3 Linkwitz-Riley
    lay = _lowpass(analog, cutoff_hz / FS) if kind == 'lp' else _highpass(analog, cutoff_hz / FS)
    return Cascade.from_layout(lay), (1 if design_id == 3 else 0)


def _db(m: float) -> float:
    """FUN_1400fda10: dead band on |H|, then 20*log10, floored at -DBL_MAX."""
    if DEAD_LO < m < DEAD_HI:
        return 0.0
    if m <= 0.0:
        return -DBL_MAX
    return max(20.0 * math.log10(m), -DBL_MAX)


def crossover_curve(ftype: int, design_id: int, f1: float, f2: float, slope1: int, slope2: int) -> list[float]:
    """+0x138: 1024 dB values on the linear grid. ftype 0 none, 1 LPF(f1, slope1),
    2 HPF(f2, slope2), 3 Band = LPF(f1) * HPF(f2) with f1 forced >= f2 + 20."""
    if ftype == 0:
        return [0.0] * 1024
    objs: list[Cascade] = []
    rep = 0
    if ftype in (1, 3):
        if ftype == 3 and f1 - f2 < 20.0:
            f1 = f2 + 20.0
        lp, rep = design('lp', design_id, order_from_slope(slope1), float(f1))
        objs.append(lp)
    if ftype in (2, 3):
        hp, rep2 = design('hp', design_id, order_from_slope(slope2), float(f2))
        objs.append(hp)
        if ftype == 2:
            rep = rep2
    # Band: FUN_1400fe280 computes ONE repeat count (from the LPF / slope1) and
    # applies it to both magnitudes (exe thread reading; differs from "each
    # object its own repeat" only for RBJ bands with unequal slopes).
    out = []
    for f in GRID:
        m = 1.0
        for obj in objs:
            mag = obj.magnitude(f)
            m *= mag ** (rep + 1)
        out.append(_db(m))
    return out


def combine(eq_curve: list[float], xo_curve: list[float]) -> list[float]:
    """FUN_14010f020: per-bin sum with the NaN / overflow substitutions."""
    out = []
    for a, b in zip(eq_curve, xo_curve):
        s = a + b
        if s != s:
            s = -128.0
        elif s > DBL_MAX:
            s = 128.0
        elif s < -DBL_MAX:
            s = -128.0
        out.append(s)
    return out
