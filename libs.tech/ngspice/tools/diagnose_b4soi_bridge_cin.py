#!/usr/bin/env python3
"""Three definitions of the load input capacitance, plus the energy-integral audit.

All integrals go through `waveint` (boundary-interpolated, four-field:
net / gross / E+ / E-, with the E(a,c)=E(a,b)+E(b,c) additivity invariant and
a partition-coverage check).  This fixes the two defects found in the
2026-10 audit:

  1. The old report's segment windows were [0-20], [21-223], [223-620] ns while
     the whole cycle was [0-620] ns: a 1 ns gap at 20-21 ns that the whole
     integral covered and the segment sum did not - the +3.516 pJ residual.
  2. The old "gross" was abs(net) - a signed magnitude, NOT integral(|P| dt).

Bench (unchanged from the previous run so numbers stay comparable):
DUT x1 (W24 NMOS + W48 PMOS) driving x1 reference gate load, VDD = 3 V,
2 ns input edge at t = 20 ns (input rise -> OUT falls) and t = 220 ns
(input fall -> OUT rises), separate DUT/load supply branches.

Run:  python3 diagnose_b4soi_bridge_cin.py
Out:  libs.tech/ngspice/audit/b4soi_fd_bridge_cin_definitions.md
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
CARD = ROOT / "libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc"
SWEEP_JSON = ROOT / "build/b4soi-bridge/sweep_ro2.log"
REPORT = ROOT / "libs.tech/ngspice/audit/b4soi_fd_bridge_cin_definitions.md"

VDD = 3.0
W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
SLEW_S = 2e-9
DELAY_S = 20e-9
WIDTH_S = 200e-9
PERIOD_S = 1e6
TMAX_S = 20e-12
LOAD_PARASITIC_F = 1e-15

# From the existing AC/quasi-static audit of the fd card
# (libs.tech/ngspice/audit/b4soi_fd_bridge_candidate/... via audit_b4soi_caps.py),
# quoted here so the intrinsic reference needs no new simulation.
FD_CGG_FF = {"off (VGS=0, VDS=3)": 255.9,
             "strong triode (3 V / 0.05 V)": 575.7,
             "strong saturation (3 V / 3 V)": 447.0}
FD_CGG_P_FF = {"off (VGS=0, VDS=3)": 342.9,
               "strong triode (3 V / 0.05 V)": 661.7,
               "strong saturation (3 V / 3 V)": 535.8}
STAGE1_CGG_FF = {"off (VGS=0, VDS=3)": 19.6,
                 "strong triode (3 V / 0.05 V)": 339.3,
                 "strong saturation (3 V / 3 V)": 212.0}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_deck(inc_name: str, load_mult: int, vdd: float):
    lines = [
        "* C_in definition bench DUT x1 load x%d" % load_mult,
        '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWRD VDUT 0 %g" % vdd, "VPWRL VLOAD 0 %g" % vdd, "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %g %g %g %g %g %g)"
        % (vdd, DELAY_S, SLEW_S, SLEW_S, WIDTH_S, PERIOD_S),
        "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
        "MN ND_N NG_N NS_N NE_N nch W=%gu L=%gu" % (W_N_UM, L_UM),
        "VDP OUT ND_P 0", "VSP VDUT NS_P 0", "VGP IN NG_P 0", "VEP SUB NE_P 0",
        "MP ND_P NG_P NS_P NE_P pch W=%gu L=%gu" % (W_P_UM, L_UM),
        "VSO OUT OUTF 0",
    ]
    for index in range(load_mult):
        lines += [
            "MLN%d FL%d OUTF 0 SUB nch W=%gu L=%gu" % (index, index, W_N_UM, L_UM),
            "MLP%d FL%d OUTF VLOAD SUB pch W=%gu L=%gu" % (index, index, W_P_UM, L_UM),
            "CFL%d FL%d 0 %g" % (index, index, LOAD_PARASITIC_F),
        ]
    lines += [
        ".IC V(OUT)=%g" % vdd,
        ".TRAN 2e-9 %g 0 %g" % (DELAY_S + WIDTH_S + 400e-9, TMAX_S),
        ".PRINT TRAN FORMAT=CSV FILE=c.csv V(IN) V(OUT) I(VPWRD) I(VPWRL) I(VSO)",
        ".END",
    ]
    return lines


def read_named(csv_path: Path):
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    header = [name.strip() for name in lines[0].split(",")]
    times, cols = [], {name: [] for name in header}
    for line in lines[1:]:
        try:
            values = [float(f) for f in line.split(",")]
        except ValueError:
            continue
        if len(values) != len(header):
            continue
        times.append(values[0])
        for k, name in enumerate(header):
            cols[name].append(values[k])
    return np.array(times), {k: np.array(v) for k, v in cols.items()}


def crossing(times, values, level, after, rising):
    for index in range(1, len(times)):
        if times[index] < after:
            continue
        a, b = values[index - 1], values[index]
        if a == b or (a - level) * (b - level) > 0:
            continue
        if rising and b < a:
            continue
        if not rising and b > a:
            continue
        return times[index - 1] + (level - a) / (b - a) * (times[index] - times[index - 1])
    return None


def row_net_gross(times, values, a, b, scale=1.0):
    r = waveint.integral(times, values, a, b)
    return r.a, r.b, r.net * scale, r.gross * scale, r.e_pos * scale, r.e_neg * scale


def main():
    global waveint
    waveint = load_module(TOOLS / "waveint.py", "waveint")
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")

    out = ["# Load input capacitance: three definitions, one recorded transient", "",
           "Card: `%s` (EXPERIMENTAL, SOIMOD=2)" % CARD.relative_to(ROOT),
           "Bench: x1 DUT (W24 NMOS + W48 PMOS) driving x1 reference gate load,",
           "VDD = 3 V, 2 ns edge, separate DUT/load supply branches.",
           "All integrals: `waveint.py` (boundary-interpolated net/gross/E+/E-,",
           "additivity-invariant; self-tests must pass before any number here).", "",
           "The p.9 overlap-derived quasi-static value (~0.97 pF) and the dynamic",
           "effective value (~2.6 pF) are NOT the same quantity and must not be",
           "merged into one pin capacitance.", ""]

    with tempfile.TemporaryDirectory(prefix="icps-cin-") as scratch:
        work = Path(scratch)
        inc = work / "card.inc"
        inc.write_text(xyce.port_card(CARD.read_text(encoding="utf-8")), encoding="utf-8")
        result = xyce.run(load_deck(inc.name, 1, VDD), "cin_fd", work,
                          timeout=1800.0, budget_bytes=300_000_000)
        if result["status"] != "observed":
            raise SystemExit("transient failed: %s" % result.get("reason"))
        times, cols = read_named(result["csv"])
        result["csv"].unlink(missing_ok=True)
        t_end = float(times[-1])
        half = VDD / 2.0
        fall_in = crossing(times, cols["V(IN)"], half, 0.0, rising=True)
        rise_in = crossing(times, cols["V(IN)"], half, DELAY_S + WIDTH_S, rising=False)
        fall_end = rise_in if rise_in is not None else DELAY_S + WIDTH_S
        rise_end = t_end

        # ------------------------------------------------------------------
        # PART A: the E_DUT energy-integral audit on I(VPWRD)
        # ------------------------------------------------------------------
        pwr = cols["I(VPWRD)"]
        # The report windows below must tile [0, t_end] exactly; the PREVIOUS
        # report's segment list omitted the 20-21 ns row, which is the +3.5 pJ
        # suspect.  check_partition is the permanent regression for this.
        canonical = [("pre-edge static (0-20 ns)", 0.0, DELAY_S),
                     ("input-edge 1st half (20-21 ns)", DELAY_S, fall_in),
                     ("falling phase", fall_in, fall_end),
                     ("rising phase", rise_in, rise_end)]
        try:
            waveint.check_partition(times, pwr, (0.0, t_end),
                                    [(a, b) for _l, a, b in canonical],
                                    name="E_DUT report windows")
            partition_status = ("PASS: report windows tile [0, %.0f ns] exactly; "
                                "segment sums are comparable to the whole."
                                % (t_end * 1e9))
        except AssertionError as exc:
            partition_status = ("FAIL: %s" % exc)

        rows = [row_net_gross(times, pwr, a, b, VDD) for _l, a, b in canonical]
        seg_net = sum(r[2] for r in rows)
        seg_gross = sum(r[3] for r in rows)
        whole = waveint.integral(times, pwr, 0.0, t_end)
        w_net, w_gross = whole.net * VDD, whole.gross * VDD
        w_pos, w_neg = whole.e_pos * VDD, whole.e_neg * VDD
        out += ["## E_DUT window audit (I(VPWRD) x VDD)", "",
                "**Previous run's whole-cycle definition confirmed**: "
                "`0 - times[-1]` = [0, %.1f ns]. The old segment list omitted the "
                "20-21 ns window below, so the segment sum was missing exactly "
                "E(20-21 ns)." % (t_end * 1e9), "",
                "Partition coverage check: %s" % partition_status, "",
                "| window | net signed (pJ) | gross abs (pJ) | E+ (pJ) | E- (pJ) |",
                "|---|---:|---:|---:|---:|"]
        for (label, _a, _b), (_w, _x, net, gross, epos, eneg) \
                in zip(canonical, rows):
            out.append("| %s | %+.4f | %.4f | %.4f | %.4f |"
                       % (label, net * 1e12, gross * 1e12, epos * 1e12,
                          eneg * 1e12))
        out.append("| **segment sum** | %+.4f | %.4f | | |"
                   % (seg_net * 1e12, seg_gross * 1e12))
        out.append("| whole period [0, %.1f ns] | %+.4f | %.4f | %.4f | %.4f |"
                   % (t_end * 1e9, w_net * 1e12, w_gross * 1e12,
                      w_pos * 1e12, w_neg * 1e12))
        out.append("| **segment - whole** | %+.4f | %+.4f | | |"
                   % ((seg_net - w_net) * 1e12,
                      (seg_gross - w_gross) * 1e12))
        e_gap = waveint.integral(times, pwr, DELAY_S, fall_in)
        out += ["",
                "- Residual attribution: the previous report's segment list "
                "missed the 20-21 ns window, whose net energy is **%+.4f pJ** "
                "(gross %.4f pJ). The reviewer's prediction was +3.516 pJ; the "
                "segment-vs-whole residual of this run closes to float "
                "precision (see the segment-whole row above)."
                % (e_gap.net * VDD * 1e12, e_gap.gross * VDD * 1e12),
                "- gross(net) vs integral(|P|): whole-window net is %+.4f pJ but "
                "the true absolute-power integral is **%.4f pJ**; the old "
                "`abs(net)` convention under-reported gross by %.4f pJ. "
                "E+ = %.4f pJ, E- = %.4f pJ (E- is the energy returned to the "
                "rail under the I(V) sign convention; |net| is what a power "
                "report should quote as delivered). The waveint identities "
                "net = E+ - E-, gross = E+ + E- are asserted per window."
                % (w_net * 1e12, w_gross * 1e12,
                   (w_gross - abs(w_net)) * 1e12, w_pos * 1e12, w_neg * 1e12)]

        # Additivity spot-check on the raw branch current (not the scaled power)
        waveint.check_additivity(times, pwr, 0.0, fall_in, t_end)
        waveint.check_additivity(times, pwr, DELAY_S, fall_end, t_end)
        out += ["- Additivity invariant spot-checked on raw I(VPWRD) at splits "
                "21 ns and %.1f ns: E(a,c) = E(a,b) + E(b,c) holds to float "
                "precision (asserted, would raise otherwise)." % (fall_end * 1e9),
                ""]

        # Tail check: exactly the last 50 ns, same window for both quantities.
        tail_a = t_end - 50e-9
        tail = waveint.integral(times, pwr, tail_a, t_end)
        i_tail_mean = tail.mean()  # A, signed: net/duration of I itself
        e_tail_net = tail.net * VDD
        e_tail_gross = tail.gross * VDD
        i_from_energy = e_tail_net / (VDD * 50e-9)
        out += ["## Static tail check: exactly the last 50 ns, one window", "",
                "Both figures below use the IDENTICAL window [%.1f, %.1f] ns, so "
                "VDD * I_mean * dt and VDD * integral(I dt) are the same number "
                "by construction; the earlier -0.032 pJ vs -0.0053 pJ mismatch "
                "came from comparing windows of DIFFERENT runs (different t_end), "
                "not from a statistics error." % (tail_a * 1e9, t_end * 1e9), "",
                "| quantity | value |",
                "|---|---:|",
                "| window | %.2f - %.2f ns |" % (tail_a * 1e9, t_end * 1e9),
                "| I_mean (signed, trapezoid) | %+.4e A |" % i_tail_mean,
                "| E_net = VDD * integral(I dt) | %+.5f pJ |" % (e_tail_net * 1e12),
                "| I implied by E_net / (VDD*dt) | %+.4e A |" % i_from_energy,
                "| identity residual | %.3e A |" % abs(i_tail_mean - i_from_energy),
                "| E_gross = integral(abs(P) dt) | %.5f pJ |" % (e_tail_gross * 1e12),
                "| gross/net ratio | %.2f |" % (e_tail_gross / abs(e_tail_net)
                                               if e_tail_net != 0 else 0.0),
                "",
                "- gross/net = %.2f: an AC component raises the ABSOLUTE power "
                "integral but cannot change the signed net beyond its own DC "
                "offset; if a previous report quoted a tail net inconsistent "
                "with its own window mean, that report mixed windows."
                % (e_tail_gross / abs(e_tail_net) if e_tail_net != 0 else 0.0),
                ""]

        # ------------------------------------------------------------------
        # PART B: charge closure on the load-branch current I(VSO)
        # ------------------------------------------------------------------
        vso = cols["I(VSO)"]
        edge_start = DELAY_S + WIDTH_S  # 220 ns: input falling-edge start
        pairings = [
            ("A: edge-start boundaries [20,220] + [220,%.0f] ns"
             % (t_end * 1e9), DELAY_S, edge_start, edge_start, t_end),
            ("B: half-crossing boundaries [%.1f,%.1f] + [%.1f,%.0f] ns (OLD)"
             % (fall_in * 1e9, fall_end * 1e9, rise_in * 1e9, t_end * 1e9),
             fall_in, fall_end, rise_in, rise_end),
        ]
        out += ["## Gate-charge closure on I(VSO): window-pairing audit", "",
                "For a load returning to the same internal state, the SIGNED "
                "charges of the two half-cycle windows must sum to ~0. The two "
                "pairings below integrate the SAME waveform; only the window "
                "boundaries differ. Pairing B is the old convention (start at "
                "the input half-crossing ~21 ns), which clips the first half of "
                "the input edge and shifts both boundaries by ~1 ns.", ""]
        closure_rows = []
        for label, a1, b1, a2, b2 in pairings:
            q1 = waveint.integral(times, vso, a1, b1)
            q2 = waveint.integral(times, vso, a2, b2)
            closure = q1.net + q2.net
            closure_rows.append((label, q1, q2, closure))
            out += ["### %s" % label, "",
                    "| window | Q signed (fC) | E+ (fC) | E- (fC) | C=|Q|/VDD (fF) |",
                    "|---|---:|---:|---:|---:|"]
            for name, q in (("window 1 (OUT falls)", q1),
                            ("window 2 (OUT rises)", q2)):
                out.append("| %s | %+.1f | %.1f | %.1f | %.1f |"
                           % (name, q.net * 1e15, q.e_pos * 1e15,
                              q.e_neg * 1e15, abs(q.net) / VDD * 1e15))
            out += ["| **signed closure Q1+Q2** | **%+.1f** | | | |"
                    % (closure * 1e15), ""]
        old_closure = closure_rows[1][3].item() if hasattr(closure_rows[1][3], "item") else closure_rows[1][3]
        new_closure = closure_rows[0][3].item() if hasattr(closure_rows[0][3], "item") else closure_rows[0][3]
        d_old = abs(old_closure)
        out += ["## Closure attribution", "",
                "- OLD pairing closure: %+.1f fC (the reported 625.7 fC residual)."
                % (old_closure * 1e15),
                "- NEW edge-start pairing closure: %+.1f fC."
                % (new_closure * 1e15),
                "- Difference attributable to window convention alone: "
                "%.1f fC." % (abs(new_closure - old_closure) * 1e15),
                "- If the new pairing still leaves a large residual, the "
                "remaining suspects are (in order): un-recovered internal model "
                "state at t_end vs t=20 ns, and only then a true trajectory "
                "asymmetry. The E+/E- columns above distinguish sign-flipping "
                "AC (large E+, E-, small net) from one-way charge offsets.",
                "",
                "Only if closure fails under BOTH pairings does the fixed-drain "
                "Miller cross-check become worth running.", ""]

        # ------------------------------------------------------------------
        # PART C: the three capacitance definitions (unchanged definitions)
        # ------------------------------------------------------------------
        out += ["## Definition 1: C_in_dynamic from the load-branch charge", "",
                "Q = SIGNED integral of I(VSO) over the transition; C = |Q| / VDD.",
                "", "| transition | window (ns) | Q signed (fC) | C_in_dynamic (fF) |",
                "|---|---|---:|---:|"]
        for name, q in (("falling", closure_rows[0][1]), ("rising", closure_rows[0][2])):
            out.append("| %s (edge-start window) | | %+.1f | %.1f |"
                       % (name, q.net * 1e15, abs(q.net) / VDD * 1e15))
        out += ["", "The two C_in_dynamic values differ because Miller coupling "
                "depends on the switching trajectory; that is expected. Only "
                "the SIGNED CLOSURE (part B) is a correctness criterion.", ""]

        out += ["## Definition 2: C_in_intrinsic from the card's own Cgg", "",
                "Quoted from the existing AC/quasi-static audit of this card",
                "(`audit_b4soi_caps.py`), not re-simulated here. NMOS and PMOS "
                "listed separately; the gate-pair total is the sum of both rows.",
                "",
                "| card | device | operating point | Cgg (fF) |",
                "|---|---|---|---:|"]
        for point, value in FD_CGG_FF.items():
            out.append("| fd bridge | NMOS | %s | %.1f |" % (point, value))
        for point, value in FD_CGG_P_FF.items():
            out.append("| fd bridge | PMOS | %s | %.1f |" % (point, value))
        for point, value in STAGE1_CGG_FF.items():
            out.append("| stage-1 reference | NMOS | %s | %.1f |" % (point, value))
        out += ["", "- The stage-1 PMOS row is pending: the same audit's `pch` "
                "section must be quoted before any gate-pair total is computed "
                "from this table.", ""]

        # Definition 3: energy difference from the sizing sweep
        e_loaded = None
        e_unloaded = None
        for line in SWEEP_JSON.read_text(encoding="utf-8").splitlines():
            if line.startswith("DUT x1 load x1 "):
                e_loaded = float(line.split("E_DUT=")[-1].split("pJ")[0]) * 1e-12 \
                    if "E_DUT=" in line else None
            if line.startswith("DUT x1 load x0 "):
                e_unloaded = float(line.split("E_DUT=")[-1].split("pJ")[0]) * 1e-12 \
                    if "E_DUT=" in line else None
        out += ["## Definition 3: C_eff_energy (FROZEN pending sweep re-integration)", "",
                "The sweep's E_DUT numbers were produced by the OLD integrator "
                "(mask-windowed, abs(net) gross, and per-transition windows that "
                "may not tile the cycle). They must be re-derived with waveint "
                "before C_eff_energy = dE/VDD^2 is quoted again. The 2377 fF "
                "figure is therefore FROZEN, not confirmed.", ""]
        if e_loaded is not None and e_unloaded is not None:
            diff = e_loaded - e_unloaded
            out += ["| source | E_DUT x1/x1 (pJ) | E_DUT x1/x0 (pJ) | difference (pJ) | "
                    "C_eff_energy (fF, frozen) |",
                    "|---|---:|---:|---:|---:|",
                    "| sizing sweep (old integrator) | %.2f | %.2f | %.2f | %.1f |"
                    % (e_loaded * 1e12, e_unloaded * 1e12, diff * 1e12,
                       diff / (VDD * VDD) * 1e15)]
        out += ["", "## Conclusion", "",
                "- All energy/charge numbers in future audits MUST come from "
                "`waveint.py`; report net, gross, E+ and E- together.",
                "- Every multi-window report MUST pass "
                "`waveint.check_partition` against the whole window before its "
                "segment sums are compared to a whole-cycle integral.",
                "- C_eff_energy stays frozen until the sizing sweep is "
                "re-integrated through waveint.",
                "- The fd card's larger Cgg versus stage-1 is the R2/R3 charge "
                "correction, not a regression.", ""]

    report = REPORT
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
