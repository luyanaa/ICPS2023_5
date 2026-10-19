#!/usr/bin/env python3
"""Long-window recovery study for the FO4 conditions that failed `NOT_SETTLED_TO_OP`.

Approved scope: re-run ONLY the five failed fd FO4 conditions, extending the
*hold time after the last input switch* and nothing else - the DUT, the load, the
input slew, the models and the numerical settings are unchanged, and the 10 mV
`SETTLE_EPSILON` is kept. The 19 accepted conditions are not re-run; the timing
matrix retains its own waveforms and hashes for offline re-acceptance.

The input is a single pulse (one rising and one falling edge) so that extending
the stop time lengthens the final hold instead of adding switching events:

    PULSE(0 VDD 20n <slew> <slew> 200n 1e6)     (period >> stop: one cycle)

The last switch is therefore at 220 ns, and the hold after it is the stop time
minus 220 ns.

What is measured, per hold:

* `e(t) = VOUT(t) - VOUT_OP`, where VOUT_OP is a forced-DC operating point at
  VIN = 0 (the final input state) on the *same* DUT/load/supply topology.
* `t_restore_10mV` - the earliest time after the last switch at which the output
  enters and *remains* within OP +/- 10 mV. If it has not recovered by the end of
  the run the status is CENSORED and the observed lower bound is recorded instead
  of an invented recovery time.
* tau_eff - only when the error keeps its sign, decays monotonically and sits
  above numerical noise; never fitted to a multi-time-constant system.
* supply-current decomposition, per supply branch (DUT and load stage have
  separate branches at the same voltage): I_OP (the DC prediction), mean(I_tail),
  RMS(I_tail - mean) (sustained AC chatter), RMS(I_tail - I_OP) (transient-vs-DC
  consistency) and peak-to-peak. The branch is recorded, because a shared supply
  integral conflates the DUT stage with the load stage.

Interpretation is left to the reviewer; this tool only separates the cases:
  A  error decays into +/- 10 mV          -> INSUFFICIENT_RECOVERY_WINDOW
  B  error plateaus at a fixed value      -> DC / internal-state question
  C  voltage recovers but currents chatter -> FALSE_QUIET_OR_CHATTER

Run:  python3 libs.tech/ngspice/tools/audit_b4soi_bridge_recovery.py
Out:  libs.tech/ngspice/audit/b4soi_fd_bridge_recovery.md
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
VARIANT = "fd"
CARD = ROOT / ("libs.tech/ngspice/models/b4soi_%s_bridge_experimental.inc" % VARIANT)
REPORT = ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_recovery.md" % VARIANT)

VDD = 3.0
W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
SLEW_S = 2e-9
DELAY_S = 20e-9
WIDTH_S = 200e-9
LAST_EDGE_S = DELAY_S + WIDTH_S          # 220 ns
PERIOD_S = 1e6                            # one cycle only
TMAX_S = 20e-12
SETTLE_EPSILON_V = 0.010

# The five failed conditions from the timing matrix.
FAILED = ((2.5, 2.0), (2.5, 10.0), (3.0, 2.0), (3.0, 10.0), (3.5, 10.0))
HOLDS_NS = (400.0,)
TAIL_WINDOW_S = 100e-9


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deck(inc_name: str, vdd: float, slew_s: float, hold_s: float) -> list:
    """Single-cycle pulse so extending the stop time only lengthens the hold."""
    return [
        "* recovery hold bench VDD=%g slew=%gns hold=%gns" % (vdd, slew_s * 1e9, hold_s * 1e9),
        '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWRD VDUT 0 %g" % vdd, "VPWRL VLOAD 0 %g" % vdd, "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %g %g %g %g %g %g)"
        % (vdd, DELAY_S, slew_s, slew_s, WIDTH_S, PERIOD_S),
        "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
        "MN ND_N NG_N NS_N NE_N nch W=%gu L=%gu" % (W_N_UM, L_UM),
        "VDP OUT ND_P 0", "VSP VDUT NS_P 0", "VGP IN NG_P 0", "VEP SUB NE_P 0",
        "MP ND_P NG_P NS_P NE_P pch W=%gu L=%gu" % (W_P_UM, L_UM),
        "MLN FL OUT 0 SUB nch W=%gu L=%gu" % (W_N_UM, L_UM),
        "MLP FL OUT VLOAD SUB pch W=%gu L=%gu" % (W_P_UM, L_UM),
        "CFL FL 0 1e-15",
        ".TRAN 2e-9 %g 0 %g" % (LAST_EDGE_S + hold_s, TMAX_S),
        ".PRINT TRAN FORMAT=CSV FILE=r.csv V(IN) V(OUT) I(VPWRD) I(VPWRL) I(VSUB)",
        ".END",
    ]


def op_deck(inc_name: str, vdd: float) -> list:
    return ["* recovery DC reference", '.include "%s"' % inc_name,
            ".OPTIONS DEVICE TEMP=25",
            "VPWRD VDUT 0 %g" % vdd, "VPWRL VLOAD 0 %g" % vdd, "VSUB SUB 0 0",
            "VIN IN 0 0",
            "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
            "MN ND_N NG_N NS_N NE_N nch W=%gu L=%gu" % (W_N_UM, L_UM),
            "VDP OUT ND_P 0", "VSP VDUT NS_P 0", "VGP IN NG_P 0", "VEP SUB NE_P 0",
            "MP ND_P NG_P NS_P NE_P pch W=%gu L=%gu" % (W_P_UM, L_UM),
            "MLN FL OUT 0 SUB nch W=%gu L=%gu" % (W_N_UM, L_UM),
            "MLP FL OUT VLOAD SUB pch W=%gu L=%gu" % (W_P_UM, L_UM),
            "CFL FL 0 1e-15",
            "VDMY DMY 0 0", "RDMY DMY 0 1e9", ".DC VDMY 0 0.001 0.001",
            ".PRINT DC FORMAT=CSV FILE=op.csv V(OUT) I(VPWRD) I(VPWRL)", ".END"]


def read_named(csv_path: Path):
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    header = [name.strip() for name in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        try:
            values = [float(f) for f in line.split(",")]
        except ValueError:
            continue
        if len(values) == len(header):
            rows.append(values)
    if not rows:
        raise ValueError("no numeric rows in %s" % csv_path)
    last = rows[-1]
    return {name: last[k] for k, name in enumerate(header)}


def series(xyce, csv_path: Path):
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    header = [name.strip() for name in lines[0].split(",")]
    out = {name: [] for name in header}
    times = []
    for line in lines[1:]:
        try:
            values = [float(f) for f in line.split(",")]
        except ValueError:
            continue
        if len(values) != len(header):
            continue
        times.append(values[0])
        for k, name in enumerate(header):
            out[name].append(values[k])
    return times, out


def stats_for(times, cols, vout_op, i_pwr_d_op, i_pwr_l_op, hold_s):
    """Recovery and current decomposition over the hold window only.

    TWO DIFFERENT error quantities, which must never be conflated:

    * `hold_max_abs_e_V` is the maximum over the WHOLE hold window. It includes
      the instant right after the last switch, when the output is still at its
      pre-switch level, so it is of the order of VDD and is NOT a settling
      error. Reporting it as one was the bug that made a normal FO4 recovery
      look like a 3-4 V overshoot.
    * `tail_max_abs_e_V` is the maximum over the LAST 100 ns only, which is the
      quantity the acceptance criterion actually constrains.
    """
    start = LAST_EDGE_S
    keep = [k for k, t in enumerate(times) if t >= start]
    t = [times[k] for k in keep]
    v = [cols["V(OUT)"][k] for k in keep]
    vin = [cols["V(IN)"][k] for k in keep]
    e_signed = [value - vout_op for value in v]
    e = [abs(value) for value in e_signed]
    stop = t[-1]
    tail_start = stop - TAIL_WINDOW_S
    tail = [k for k in range(len(t)) if t[k] >= tail_start]
    tail_max = max(e[k] for k in tail) if tail else None
    tail_max_sample = (t[tail[0]], vin[tail[0]], v[tail[0]]) if tail else None
    max_e = max(e) if e else None
    # t_restore: first entry that never leaves the +/- 10 mV band afterwards.
    restore = None
    for k in range(len(t)):
        if e[k] <= SETTLE_EPSILON_V and all(x <= SETTLE_EPSILON_V for x in e[k:]):
            restore = t[k]
            break
    censored = restore is None
    # tau_eff: only if the error keeps one sign, decays monotonically and clears
    # the acceptance floor. Never fitted to a multi-time-constant system.
    tau_eff = None
    signs = {(1 if (value - vout_op) >= 0 else -1) for value in v}
    decaying = all(e[k] >= e[k + 1] for k in range(len(e) - 1))
    if len(signs) == 1 and decaying and max_e > SETTLE_EPSILON_V:
        for k in range(len(e)):
            if e[k] <= SETTLE_EPSILON_V:
                tau_eff = -(t[k] - t[0]) / math.log(e[k] / max_e)
                break
    def branch(cur, op):
        mean = sum(cur) / len(cur)
        rms_ac = math.sqrt(sum((x - mean) ** 2 for x in cur) / len(cur))
        rms_op = math.sqrt(sum((x - op) ** 2 for x in cur) / len(cur))
        # Permanent regression assertion (reviewer-specified): the two RMS values
        # must satisfy RMS(I-I_OP)^2 = RMS(I-mean)^2 + (mean-I_OP)^2 exactly, or the
        # reported decomposition is internally inconsistent.
        expected = math.sqrt(rms_ac ** 2 + (mean - op) ** 2)
        tol = max(1e-18, 1e-9 * max(abs(rms_op), abs(expected)))
        if abs(rms_op - expected) > tol:
            raise ValueError(
                "current decomposition inconsistent: RMS(I-I_OP)=%.6e but "
                "sqrt(RMS(I-mean)^2+(mean-I_OP)^2)=%.6e (mean=%.6e, I_OP=%.6e)"
                % (rms_op, expected, mean, op))
        return {"mean_A": mean, "rms_ac_A": rms_ac, "rms_vs_op_A": rms_op,
                "pp_A": max(cur) - min(cur), "op_A": op}
    # Current decomposition over the SAME last-100 ns window as the voltage tail.
    # `tail` indexes the truncated hold subset, so the full-time arrays must be
    # indexed through `keep` - indexing them directly pulled samples from the
    # switching region and produced a decomposition that said nothing about the
    # settled state.
    dut_cur = [cols["I(VPWRD)"][keep[k]] for k in tail]
    ld_cur = [cols["I(VPWRL)"][keep[k]] for k in tail]
    return {
        "hold_s": hold_s, "samples": len(t), "max_abs_e_V": max_e,
        "tail_max_abs_e_V": tail_max, "tail_window_s": TAIL_WINDOW_S,
        "tail_max_sample": tail_max_sample,
        "e_final_signed_V": e_signed[-1], "e_final_V": e[-1],
        "first_after_edge_V": v[0], "t_restore_s": restore, "censored": censored,
        "tau_eff_s": tau_eff,
        "sign_uniform": len(signs) == 1, "monotone_decay": decaying,
        "dut": branch(dut_cur, i_pwr_d_op), "load": branch(ld_cur, i_pwr_l_op),
        "vout_tail_mean_V": sum(v) / len(v),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ngspice", default=None)
    args = parser.parse_args()
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")
    if not CARD.is_file():
        raise SystemExit("missing %s" % CARD)

    out = ["# FO4 long-window recovery study: fd bridge (SOIMOD=2)", "",
           "Card: `%s`" % CARD.relative_to(ROOT),
           "Approved for the five FO4 conditions that failed `NOT_SETTLED_TO_OP`.",
           "Model, load, input slew and numerical settings are unchanged; only the hold",
           "after the last switch is extended, and the 10 mV epsilon is kept.", "",
           "Input: one cycle only (`period = 1e6`), so extending the stop time lengthens",
           "the hold without adding switching events. The last switch is at 220 ns.", ""]

    with tempfile.TemporaryDirectory(prefix="icps-recovery-") as scratch:
        work = Path(scratch)
        inc = work / "card.inc"
        inc.write_text(xyce.port_card(CARD.read_text(encoding="utf-8")), encoding="utf-8")
        for vdd, slew_ns in FAILED:
            out += ["## VDD = %.1f V, slew = %g ns, FO4" % (vdd, slew_ns), ""]
            op_result = xyce.run(op_deck(inc.name, vdd), "op_%g_%g" % (vdd, slew_ns),
                                 work, timeout=300.0)
            if op_result["status"] != "observed":
                out.append("- DC operating point failed: %s" % op_result.get("reason"))
                out.append("")
                continue
            try:
                op = read_named(op_result["csv"])
            except ValueError as error:
                out.append("- DC operating point unreadable: %s" % error)
                out.append("")
                continue
            op_result["csv"].unlink(missing_ok=True)
            vout_op, i_dut_op, i_ld_op = op["V(OUT)"], op["I(VPWRD)"], op["I(VPWRL)"]

            # ONE transient per hold; the recovery table, the worst-error sample and
            # the current decomposition are all derived from that single run.
            records = []
            for hold_ns in HOLDS_NS:
                result = xyce.run(deck(inc.name, vdd, slew_ns * 1e-9, hold_ns * 1e-9),
                                  "rec_%g_%g_%g" % (vdd, slew_ns, hold_ns), work,
                                  timeout=1800.0, budget_bytes=400_000_000)
                if result["status"] != "observed":
                    records.append({"hold_ns": hold_ns, "status": result["status"],
                                    "reason": result.get("reason", "")})
                    continue
                times, cols = series(xyce, result["csv"])
                result["csv"].unlink(missing_ok=True)
                rec = stats_for(times, cols, vout_op, i_dut_op, i_ld_op,
                                hold_ns * 1e-9)
                rec["hold_ns"] = hold_ns
                rec["status"] = "observed"
                records.append(rec)

            out += ["| hold (ns) | steps | VOUT tail mean (V) | "
                    "max \\|e\\| over WHOLE hold (mV) | max \\|e\\| in last 100 ns (mV) | "
                    "e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |",
                    "|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
            for rec in records:
                if rec.get("status") != "observed":
                    out.append("| %g | | | | | | %s: %s | |"
                               % (rec["hold_ns"], rec["status"], rec.get("reason", "")))
                    continue
                status = ("CENSORED (tail still above 10 mV at the end)" if rec["censored"]
                          else "RECOVERED")
                out.append("| %g | %d | %.4f | %.2f | %.2f | %+.2f | %s | %s | %s |"
                           % (rec["hold_ns"], rec["samples"], rec["vout_tail_mean_V"],
                              rec["max_abs_e_V"] * 1e3, rec["tail_max_abs_e_V"] * 1e3,
                              rec["e_final_signed_V"] * 1e3,
                              "n/a" if rec["t_restore_s"] is None
                              else "%.1f" % (rec["t_restore_s"] * 1e9),
                              status, "n/a" if rec["tau_eff_s"] is None
                              else "%.1f" % (rec["tau_eff_s"] * 1e9)))

            sampled = [rec for rec in records if rec.get("status") == "observed"
                       and rec.get("tail_max_sample")]
            if sampled:
                t_s, vin_v, v_v = sampled[0]["tail_max_sample"]
                out += ["",
                        "- Sample at the largest last-100 ns error: t = %.1f ns after the "
                        "last edge, VIN = %.4f V, VOUT = %.4f V, VOUT_OP = %.4f V, "
                        "signed error = %+.2f mV."
                        % (t_s * 1e9, vin_v, v_v, vout_op, (v_v - vout_op) * 1e3)]
            out += ["",
                    "- VOUT_OP = %.4f V, I(VPWR_D)_OP = %.3e A, I(VPWR_L)_OP = %.3e A"
                    % (vout_op, i_dut_op, i_ld_op),
                    "| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | "
                    "RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for rec in records:
                if rec.get("status") != "observed":
                    continue
                for branch in ("dut", "load"):
                    b = rec[branch]
                    # Mean and I_OP at full precision: at 3 V they differ by only
                    # ~1e-10 A, which a %.3e display rounds into an apparent
                    # inconsistency with the RMS identity.
                    out.append("| %s | %g | %+.6e | %+.6e | %+.3e | %.6e | %.6e | %.4e |"
                               % (branch, rec["hold_ns"], b["op_A"], b["mean_A"],
                                  b["mean_A"] - b["op_A"], b["rms_ac_A"],
                                  b["rms_vs_op_A"], b["pp_A"]))
            out.append("")

    report = REPORT
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
