#!/usr/bin/env python3
"""Signed/absolute waveform integration with a permanent additivity invariant.

Every energy/charge number reported by the bridge audits must come from this
module.  It fixes the two defects the 2026-10 audit found in ad-hoc integrals:

  1. Boundary loss.  Boolean-mask windowing (``values[t >= a]``) silently drops
     the trapezoid area between the window edge and the next sample.  Here the
     waveform is re-sampled by exact linear interpolation AT the window
     boundaries, so the pieces tile perfectly.

  2. Fake "gross".  ``abs(trapezoid(P))`` is a SIGNED net magnitude, not the
     absolute-power integral ``integral(|P| dt)``.  The two differ whenever the
     power changes sign inside the window.  Every integral therefore returns
     FOUR fields and asserts their identity:

       net    = integral(P dt)            (signed)
       gross  = integral(|P| dt)          (true absolute)
       e_pos  = integral(max(P, 0) dt)
       e_neg  = integral(max(-P, 0) dt)   (returned positive)

       net  == e_pos - e_neg                (exactly, up to float rounding)
       gross == e_pos + e_neg

  3. Additivity invariant.  For ANY a < b < c,
         E(a, c) == E(a, b) + E(b, c)
     holds to float tolerance, because boundaries are interpolated, never
     masked.  A partition coverage helper exists for the complementary check
     that a set of report windows actually tiles the whole window - the 20-21 ns
     gap that produced the unexplained 3.5 pJ residual is exactly the failure
     mode this catches.

Run ``python3 waveint.py`` to execute the analytic self-tests (constant power,
+/- square wave, triangle, boundaries off-sample, and the negative fixture: a
1 ns spike whose window start is deliberately shifted past the spike, which
must FAIL the partition coverage check).
"""

from __future__ import annotations

import numpy as np

RTOL_IDENTITY = 1e-9
RTOL_ADDITIVITY = 1e-9


class Integral:
    """Four-field result of one windowed integral of P(t) over [a, b]."""

    __slots__ = ("a", "b", "net", "gross", "e_pos", "e_neg")

    def __init__(self, a: float, b: float, net: float, gross: float,
                 e_pos: float, e_neg: float):
        self.a, self.b = a, b
        self.net, self.gross = net, gross
        self.e_pos, self.e_neg = e_pos, e_neg

    def validate(self) -> None:
        # Identity tolerance is scaled by the SIGNAL magnitude (gross), not by
        # the (possibly near-zero) net, so a sign-symmetric waveform whose net
        # is ~0 still passes at float precision.
        tol = RTOL_IDENTITY * max(self.gross, abs(self.net), 1e-300)
        if abs(self.net - (self.e_pos - self.e_neg)) > tol:
            raise AssertionError(
                "net != e_pos - e_neg over [%g, %g]: %r vs %r"
                % (self.a, self.b, self.net, self.e_pos - self.e_neg))
        if abs(self.gross - (self.e_pos + self.e_neg)) > tol:
            raise AssertionError(
                "gross != e_pos + e_neg over [%g, %g]: %r vs %r"
                % (self.a, self.b, self.gross, self.e_pos + self.e_neg))

    def mean(self) -> float:
        """Signed time-average of P over the window (net / duration)."""
        return self.net / (self.b - self.a)


def _interp_at(times: np.ndarray, values: np.ndarray, t: float) -> float:
    return float(np.interp(t, times, values))


def integral(times, values, a: float, b: float) -> Integral:
    """Trapezoid integral of values(t) over exactly [a, b].

    `a` and `b` need not coincide with samples; the waveform is linearly
    interpolated at both boundaries so adjacent windows tile exactly. Positive
    and negative areas split each sign-changing segment at its interpolated zero.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if not (times[0] <= a <= b <= times[-1]):
        raise ValueError("window [%g, %g] outside data [%g, %g]"
                         % (a, b, times[0], times[-1]))
    if a == b:
        return Integral(a, b, 0.0, 0.0, 0.0, 0.0)

    keep = (times > a) & (times < b)
    t = np.concatenate(([a], times[keep], [b]))
    v = np.concatenate(([_interp_at(times, values, a)],
                        values[keep],
                        [_interp_at(times, values, b)]))
    net = float(np.trapezoid(v, t))
    e_pos = float(np.trapezoid(np.clip(v, 0.0, None), t))
    e_neg = float(np.trapezoid(np.clip(-v, 0.0, None), t))
    left, right = v[:-1], v[1:]
    crossing = (left * right) < 0.0
    if np.any(crossing):
        positive = np.abs(left[crossing])
        negative = np.abs(right[crossing])
        correction = float(np.sum(
            np.diff(t)[crossing] * positive * negative /
            (2.0 * (positive + negative))))
        e_pos -= correction
        e_neg -= correction
    result = Integral(a, b, net, e_pos + e_neg, e_pos, e_neg)
    result.validate()
    return result


def integral_square(times, values, a: float, b: float) -> Integral:
    """Integrate the square of piecewise-linear values, with exact boundaries.

    This is the second moment for RMS and resistor I²R energy. Trapezoids
    over squared samples are not exact for a linearly interpolated current.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if not (times[0] <= a <= b <= times[-1]):
        raise ValueError("window [%g, %g] outside data [%g, %g]"
                         % (a, b, times[0], times[-1]))
    if a == b:
        return Integral(a, b, 0.0, 0.0, 0.0, 0.0)
    keep = (times > a) & (times < b)
    t = np.concatenate(([a], times[keep], [b]))
    v = np.concatenate(([_interp_at(times, values, a)],
                        values[keep], [_interp_at(times, values, b)]))
    left, right = v[:-1], v[1:]
    net = float(np.sum(np.diff(t) *
                       (left * left + left * right + right * right) / 3.0))
    result = Integral(a, b, net, net, net, 0.0)
    result.validate()
    return result


def check_additivity(times, values, a: float, b: float, c: float) -> None:
    """E(a, c) == E(a, b) + E(b, c) for arbitrary interior split b."""
    whole = integral(times, values, a, c)
    left = integral(times, values, a, b)
    right = integral(times, values, b, c)
    for name in ("net", "gross", "e_pos", "e_neg"):
        w = getattr(whole, name)
        parts = getattr(left, name) + getattr(right, name)
        if abs(w - parts) > RTOL_ADDITIVITY * max(abs(w), abs(parts), 1e-300):
            raise AssertionError(
                "additivity violated for %s over [%g,%g] vs [%g,%g]+[%g,%g]: "
                "%r vs %r" % (name, a, c, a, b, b, c, w, parts))


def check_partition(times, values, whole: tuple[float, float],
                    windows: list[tuple[float, float]], name: str = "") -> None:
    """Assert that `windows` exactly tile `whole` (sorted, contiguous).

    Raises with the total uncovered area if there is a gap or overlap - the
    permanent regression for the 'segments sum vs whole cycle' comparison.
    """
    a, c = whole
    cursor = a
    covered = 0.0
    for (wa, wb) in sorted(windows):
        if wa < cursor - 1e-15 * max(1.0, abs(cursor)):
            raise AssertionError(
                "%s: window [%g, %g] overlaps previous coverage (cursor %g)"
                % (name, wa, wb, cursor))
        if wb > c + 1e-15 * max(1.0, abs(c)):
            raise AssertionError("%s: window [%g, %g] exceeds whole [%g, %g]"
                                 % (name, wa, wb, a, c))
        covered += wb - wa
        cursor = wb
    duration = c - a
    if abs(covered - duration) > 1e-9 * duration:
        raise AssertionError(
            "%s: windows cover %.17g s of the %.17g s whole window "
            "(uncovered %.6g ns) - segment sums are NOT comparable to the "
            "whole-window integral" % (name, covered, duration,
                                       (duration - covered) * 1e9))


def partition_energy(times, values, whole: tuple[float, float],
                     windows: list[tuple[float, float]], field: str = "net"
                     ) -> tuple[float, float]:
    """Sum of one field over the windows, plus the whole-window value.

    check_partition MUST be called first (or an AssertionError accepted);
    this convenience function raises if the windows do not tile `whole`.
    """
    check_partition(times, values, whole, windows, name="partition")
    total = sum(getattr(integral(times, values, a, b), field)
                for (a, b) in windows)
    return total, getattr(integral(times, values, *whole), field)


# ---------------------------------------------------------------------------
# Analytic self-tests
# ---------------------------------------------------------------------------

def _self_test() -> int:
    failures = []

    def expect_ok(label, fn):
        try:
            fn()
            print("PASS  %s" % label)
        except AssertionError as exc:
            failures.append(label)
            print("FAIL  %s: %s" % (label, exc))

    # 1. Constant power: P = 2 W over [0, 10] -> net = gross = e_pos = 20 J.
    def t_constant():
        t = np.linspace(0.0, 10.0, 41)
        r = integral(t, np.full_like(t, 2.0), 0.0, 10.0)
        assert abs(r.net - 20.0) < 1e-12 and abs(r.gross - 20.0) < 1e-12
        assert abs(r.e_pos - 20.0) < 1e-12 and abs(r.e_neg) < 1e-12
        assert abs(r.mean() - 2.0) < 1e-12
    expect_ok("constant power 2 W x 10 s = 20 J", t_constant)

    # 2. +/- square wave, period 2 s, amplitude 1: over 10 s net = 0,
    #    gross = e_pos = e_neg = 5.  Over [0.5, 7.3]: 1.5 s of +1, then
    #    alternating; analytic: starts inside a positive half-pulse [0,1).
    def t_square():
        # Fine grid: trapezoid on a sampled step has O(dt) error per sign
        # transition, so tolerances are set at ~50 transitions' worth.
        t = np.linspace(0.0, 10.0, 100001)
        v = np.where((np.floor(t) % 2) == 0, 1.0, -1.0)
        r = integral(t, v, 0.0, 10.0)
        assert abs(r.net) < 5e-3, r.net
        assert abs(r.gross - 10.0) < 5e-3
        assert abs(r.e_pos - 5.0) < 5e-3 and abs(r.e_neg - 5.0) < 5e-3
        # abs(net) != gross whenever the sign changes: the old bug.
        assert r.gross > abs(r.net) + 1.0
        r2 = integral(t, v, 0.5, 7.3)
        # +: [0.5,1) U [2,3) U [4,5) U [6,7) = 0.5+1+1+1 = 3.5
        # -: [1,2) U [3,4) U [5,6) U [7,7.3) = 1+1+1+0.3 = 3.3
        assert abs(r2.net - 0.2) < 5e-3, r2.net
        assert abs(r2.e_pos - 3.5) < 5e-3 and abs(r2.e_neg - 3.3) < 5e-3
        assert abs(r2.gross - 6.8) < 5e-3
    expect_ok("square wave net/gross/E+/- vs analytic", t_square)

    # 3. Triangle P(t) = t over [0, 2]: net = 2, boundaries off-sample.
    def t_triangle():
        t = np.linspace(0.0, 2.0, 37)  # 2/36 spacing: 0.37 is OFF-sample
        r = integral(t, t, 0.37, 1.93)
        analytic = 0.5 * (1.93 ** 2 - 0.37 ** 2)
        assert abs(r.net - analytic) < 1e-9, (r.net, analytic)
        assert abs(r.gross - analytic) < 1e-9  # positive throughout
        check_additivity(t, t, 0.0, 0.83, 2.0)
    expect_ok("triangle, window edges off-sample, vs analytic", t_triangle)

    # 4. Additivity for arbitrary splits on a sign-changing waveform,
    #    including splits ON and OFF samples.
    def t_additivity():
        t = np.linspace(0.0, 620e-9, 12347)
        v = np.sin(2 * np.pi * 3.7e6 * t) * (1 + 0.3 * t * 1e6)
        for b in (0.0, 21e-9, 123.456e-9, 619.999e-9):
            check_additivity(t, v, 0.0, b, 620e-9)
        check_additivity(t, v, 20e-9, 20.5e-9, 21e-9)
    expect_ok("additivity E(a,c)=E(a,b)+E(b,c) incl. off-sample splits",
              t_additivity)

    # 5. Boundary-loss regression: masked windowing loses the partial
    #    trapezoid near the edges; this module must not.
    def t_boundary_loss():
        t = np.array([0.0, 1.0, 2.0, 3.0])
        v = np.array([0.0, 10.0, 10.0, 0.0])
        # v ramps 0->10 on [0,1], holds 10 on [1,2], ramps 10->0 on [2,3].
        # [0.5, 2.5]: ramp-half 0.5*(5+10)*0.5 + hold 10 + ramp-half 3.75
        r = integral(t, v, 0.5, 2.5)
        assert abs(r.net - 17.5) < 1e-12, r.net
        # window edges exactly ON samples: pure hold = 15
        r2 = integral(t, v, 1.0, 2.0)
        assert abs(r2.net - 10.0) < 1e-12
        # a window edge strictly inside the ramp: v=10t, so the integral is
        # 0.5*(2.5 + 7.5)*0.5 = 2.5 (trapezoid is exact on linear data)
        r3 = integral(t, v, 0.25, 0.75)
        assert abs(r3.net - 2.5) < 1e-12, r3.net
    expect_ok("partial trapezoid at window edges is interpolated, not dropped",
              t_boundary_loss)

    # 6. NEGATIVE fixture: most energy sits in a 1 ns spike; the report
    #    windows deliberately start 1 ns late (the 20->21 ns shift that
    #    produced the 3.5 pJ residual).  check_partition must FAIL.
    def t_negative_fixture():
        t = np.linspace(0.0, 620e-9, 6201)
        v = np.zeros_like(t)
        spike = (t >= 0.0) & (t <= 1e-9)      # 1 mA-ish spike, 1 ns
        v[spike] = 1e-3
        whole = (0.0, 620e-9)
        windows = [(20e-9, 223e-9), (223e-9, 620e-9)]  # note: no [0, 20 ns)
        try:
            check_partition(t, v, whole, windows, name="negative-fixture")
        except AssertionError:
            pass
        else:
            raise AssertionError("partition with a 20 ns gap was accepted")
        # and the energy sums must disagree by exactly the spike energy.
        # On this grid the rectangle 1e-3 A x 1 ns integrates (trapezoid, with
        # the artificial ramp-down at t=1 ns) to 1e-12 + 0.5*1e-3*1e-10 J.
        spike_e = 1e-3 * 1e-9 + 0.5 * 1e-3 * 1e-10
        seg = sum(integral(t, v, a, b).net for (a, b) in windows)
        w = integral(t, v, *whole).net
        assert abs((w - seg) - spike_e) <= 1e-9 * spike_e, (w, seg, spike_e)
    expect_ok("negative fixture: gap between windows is rejected",
              t_negative_fixture)

    # 7. partition_energy on a GOOD tiling must be exactly additive.
    def t_partition_good():
        t = np.linspace(0.0, 620e-9, 6201)
        v = np.sin(2 * np.pi * 5e6 * t) * 1e-3
        whole = (0.0, 620e-9)
        windows = [(0.0, 20e-9), (20e-9, 21e-9), (21e-9, 223e-9),
                   (223e-9, 620e-9)]
        seg, w = partition_energy(t, v, whole, windows)
        assert abs(seg - w) <= 1e-9 * max(abs(w), 1e-300)
    expect_ok("good tiling: partition sum == whole-window integral",
              t_partition_good)

    def t_current_square():
        assert abs(integral_square([0, 1], [0, 1], 0, 1).net - 1/3) < 1e-14
        assert abs(integral_square([0, 1], [-1, 1], 0, 1).net - 1/3) < 1e-14
        times = np.array([0.0, 0.4, 1.7, 2.0])
        a, split, b = 0.13, 0.83, 1.91
        whole = integral_square(times, times, a, b).net
        parts = (integral_square(times, times, a, split).net +
                 integral_square(times, times, split, b).net)
        assert abs(whole - (b**3 - a**3)/3) < 1e-13
        assert abs(parts - whole) < 1e-13
    expect_ok("current-square moment: ramps, sign crossing and off-sample tiling",
              t_current_square)

    def t_unsampled_zero_crossing():
        times, values = [0.0, 2.0], [1.0, -1.0]
        result = integral(times, values, 0.0, 2.0)
        assert abs(result.net) < 1e-14
        assert abs(result.gross - 1.0) < 1e-14
        assert abs(result.e_pos - 0.5) < 1e-14
        assert abs(result.e_neg - 0.5) < 1e-14
        windows = [(0.0, 0.75), (0.75, 1.5), (1.5, 2.0)]
        for field in ("net", "gross", "e_pos", "e_neg"):
            total, whole = partition_energy(times, values, (0.0, 2.0), windows, field)
            assert abs(total - whole) < 1e-14, (field, total, whole)
    expect_ok("unsampled zero crossing: exact signed areas and off-sample partition",
              t_unsampled_zero_crossing)

    print()
    if failures:
        print("%d SELF-TEST(S) FAILED: %s" % (len(failures), failures))
        return 1
    print("all waveint self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
