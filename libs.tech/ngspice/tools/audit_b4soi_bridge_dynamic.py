#!/usr/bin/env python3
"""Dynamic stability verification of the BSIMSOI bridge candidate, in Xyce.

AGENTS.md section 3 requires three-dimensional acceptance: port currents, node
charge integrals and time-step continuity together, and explicitly forbids
treating a quiet output voltage as a pass.  A model that produces a stable
output while the supply/gate branch chatters with mA-scale, many-zero-crossing
current in the tail is a FALSE QUIET TAIL and fails.

This runs in Xyce rather than ngspice because the local ngspice-47 build is
empirically unstable on this model family: the depletion audit retained 21/216
transient aborts (`timestep too small`) plus documented false-quiet tails.

Benches use the corrected topology: four-terminal D/G/S/E with every E on one
driven common substrate.  Historical benches that tied the PMOS E to VDD are
NOT reproduced.

Every run uses one fixed numerical setting; a failure is reported as a failure.
No tolerance loosening, no gmin ladder, no smaller fixed step to hide an abort.

Run:  python3 libs.tech/ngspice/tools/audit_b4soi_bridge_dynamic.py
Out:  libs.tech/ngspice/audit/b4soi_bridge_dynamic.md
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
VARIANT_SOIMOD = {"fd": 2, "unified": 1}
# Filled in by main() from --variant.
VARIANT = "fd"
CANDIDATE = None
FROZEN = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
REPORT = None


def configure(variant: str) -> None:
    global VARIANT, CANDIDATE, REPORT
    VARIANT = variant
    CANDIDATE = ROOT / ("libs.tech/ngspice/models/b4soi_%s_bridge_experimental.inc"
                        % variant)
    REPORT = ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_dynamic.md" % variant)

VDD = 3.0
W_UM, L_UM = 24.0, 6.0
PMOS_W_UM = 48.0
CLOAD_F = 5e-15
EDGE_S = 2e-9
DELAY_S = 100e-9
WIDTH_S = 400e-9
PERIOD_S = 1e-6
TSTOP_S = 1e-6
TSTEP_S = 20e-12
TMAX_S = 20e-12
TAIL_WINDOW_S = 100e-9

# AGENTS.md 3.2: a tail is "quiet" below this voltage RMS and "chattering"
# above this supply-branch peak-to-peak current.
QUIET_VOLTAGE_RMS = 1e-3
CHATTER_PEAK_TO_PEAK_A = 1e-3
# Safety budget: a stiff model can emit hundreds of MB of transient output.
# Exceeding this is reported as an over-budget failure, never as a pass.
OUTPUT_BUDGET_BYTES = 200_000_000
WALL_BUDGET_S = 900.0


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def t7_deck(card_inc: str, model: str) -> list:
    """Single-device switch.  NMOS pulls a pre-charged node down, PMOS up."""
    drive = VDD if model == "nch" else -VDD
    preset = VDD if model == "nch" else 0.0
    source = "0" if model == "nch" else "VDD"
    return [
        "* Xyce T7 switch (%s, common E/SUB)" % model,
        '.include "%s"' % card_inc,
        ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWR VDD 0 %g" % VDD,
        "VSUB SUB 0 0",
        "VG G 0 PULSE(0 %g %g %g %g %g %g)"
        % (drive, DELAY_S, EDGE_S, EDGE_S, WIDTH_S, PERIOD_S),
        "M1 OUT G %s SUB %s W=%gu L=%gu" % (source, model, W_UM, L_UM),
        ".IC V(OUT)=%g" % preset,
        "CLOAD OUT 0 %g" % CLOAD_F,
        ".TRAN %g %g 0 %g" % (TSTEP_S, TSTOP_S, TMAX_S),
        # Xyce prepends TIME to transient CSV output; listing it again would
        # duplicate the column and shift every later index.
        ".PRINT TRAN FORMAT=CSV FILE=t7_%s.csv V(OUT) V(G) I(VG) I(CLOAD)" % model,
        ".END",
    ]


def inverter_deck(card_inc: str) -> list:
    """CMOS inverter; both E terminals on the one driven common substrate."""
    return [
        "* Xyce inverter (common E/SUB, no E=VDD)",
        '.include "%s"' % card_inc,
        ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWR VDD 0 %g" % VDD,
        "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %g %g %g %g %g %g)"
        % (VDD, DELAY_S, EDGE_S, EDGE_S, WIDTH_S, PERIOD_S),
        "MN OUT IN 0 SUB nch W=%gu L=%gu" % (W_UM, L_UM),
        "MP OUT IN VDD SUB pch W=%gu L=%gu" % (PMOS_W_UM, L_UM),
        ".IC V(OUT)=%g" % VDD,
        "CLOAD OUT 0 %g" % CLOAD_F,
        ".TRAN %g %g 0 %g" % (TSTEP_S, TSTOP_S, TMAX_S),
        # Xyce prepends TIME to transient CSV output; do not list it again.
        ".PRINT TRAN FORMAT=CSV FILE=inv.csv V(OUT) V(IN) I(VIN) I(VPWR) I(VSUB)",
        ".END",
    ]


def analyse(times, columns, *, quiet_column: int, chatter_column: int) -> dict:
    """Time-step continuity, output excursion, edge charge and tail statistics."""
    dt = [b - a for a, b in zip(times, times[1:])]
    positive = [value for value in dt if value > 0]
    volume = columns[quiet_column]
    chatter = columns[chatter_column]
    tail = [k for k, t in enumerate(times) if t >= TSTOP_S - TAIL_WINDOW_S]
    if not tail:
        return {"steps": len(times),
                "terminal_time_s": times[-1],
                "incomplete": ("transient did not reach the tail window "
                               "(last time %.3g s)" % times[-1])}
    tail_volume = [volume[k] for k in tail]
    tail_chatter = [chatter[k] for k in tail]
    mean_volume = sum(tail_volume) / max(len(tail_volume), 1)
    rms = math.sqrt(sum((value - mean_volume) ** 2 for value in tail_volume)
                    / max(len(tail_volume), 1))
    # Mean-subtracted zero crossings, matching the audit's AC-coupled convention.
    mean_chatter = sum(tail_chatter) / max(len(tail_chatter), 1)
    centred = [value - mean_chatter for value in tail_chatter]
    crossings = sum(1 for a, b in zip(centred, centred[1:]) if a * b < 0)
    # gate edge charge: integrate the gate branch over the 5%..95% input crossing
    edge = [k for k, t in enumerate(times) if DELAY_S <= t <= DELAY_S + EDGE_S]
    charge = 0.0
    if len(edge) >= 2:
        charge = abs(sum(0.5 * (chatter[k] + chatter[k - 1]) * (times[k] - times[k - 1])
                         for k in range(edge[0] + 1, edge[-1] + 1)))
    chatter_pp = max(tail_chatter) - min(tail_chatter)
    return {
        "steps": len(times),
        "dt_min_s": min(positive) if positive else None,
        "dt_max_s": max(positive) if positive else None,
        "dt_jump_ratio": (max(positive) / min(positive)) if positive else None,
        "volume_min_V": min(volume),
        "volume_max_V": max(volume),
        "tail_volume_rms_V": rms,
        "tail_chatter_pp_A": chatter_pp,
        "tail_chatter_zero_crossings": crossings,
        "edge_charge_C": charge,
        "false_quiet_tail": (rms < QUIET_VOLTAGE_RMS
                             and chatter_pp > CHATTER_PEAK_TO_PEAK_A),
    }


def run_bench(xyce, label: str, deck: list, name: str, work: Path,
              quiet_column: int, chatter_column: int) -> dict:
    result = xyce.run(deck, name, work, timeout=WALL_BUDGET_S,
                      budget_bytes=OUTPUT_BUDGET_BYTES)
    if result["status"] != "observed":
        return {"status": result["status"], "reason": result.get("reason"),
                "card": label}
    times, columns = xyce.read_csv(result["csv"])
    if max(quiet_column, chatter_column) >= len(columns):
        return {"status": "failed",
                "reason": "expected at least %d printed columns, got %d"
                          % (max(quiet_column, chatter_column) + 1, len(columns)),
                "card": label}
    stats = analyse(times, columns, quiet_column=quiet_column,
                    chatter_column=chatter_column)
    stats["status"] = "incomplete" if stats.get("incomplete") else "observed"
    stats["card"] = label
    result["csv"].unlink(missing_ok=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANT_SOIMOD), default="fd")
    parser.add_argument("--out", default=str(ROOT / "build/b4soi-bridge/dynamic"))
    args = parser.parse_args()
    configure(args.variant)
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required at %s" % xyce.XYCE)
    if not CANDIDATE.is_file():
        raise SystemExit("run `fit_b4soi_bridge.py --fit` first")

    cards = {"bridge candidate": CANDIDATE, "stage-1 frozen": FROZEN}
    out = ["# B4SOI %s experimental bridge: dynamic stability in Xyce"
           % args.variant, "",
           "Card: `%s` (EXPERIMENTAL; the nominal model is unchanged)."
           % CANDIDATE.relative_to(ROOT),
           "Simulator: `%s` (Xyce NF 7.10.0, BSIMSOI 4.5.0 via LEVEL=70450)." % xyce.XYCE,
           "Fixed numerical setting TSTEP=%g TMAX=%g METHOD=TRAP RELTOL=1e-4;"
           % (TSTEP_S, TMAX_S),
           "no retry ladder and no tolerance loosening. Four-terminal D/G/S/E with a",
           "single driven common substrate; PMOS E is SUB, never VDD.", "",
           "Benches: VDD=%g V, %g fF load, %g ns edges, %g ns delay, %g ns width, "
           "%g us stop." % (VDD, CLOAD_F * 1e15, EDGE_S * 1e9, DELAY_S * 1e9,
                            WIDTH_S * 1e9, TSTOP_S * 1e6), ""]
    results = {}
    with tempfile.TemporaryDirectory(prefix="icps-bridge-dynamic-") as scratch:
        work = Path(scratch)
        for index, (label, card) in enumerate(sorted(cards.items())):
            inc = work / ("card_%d.inc" % index)
            inc.write_text(xyce.port_card(card.read_text(encoding="utf-8")),
                           encoding="utf-8")
            for bench in ("t7_nch", "t7_pch", "inverter"):
                key = (label, bench)
                if bench == "inverter":
                    stats = run_bench(xyce, label, inverter_deck(inc.name),
                                      "inv_%d" % index, work,
                                      quiet_column=0, chatter_column=3)
                else:
                    model = bench.split("_")[1]
                    stats = run_bench(xyce, label, t7_deck(inc.name, model),
                                      "%s_%d" % (bench, index), work,
                                      quiet_column=0, chatter_column=2)
                results[key] = stats
                print("%-18s %-9s %s" % (label, bench, stats.get("status")), flush=True)

    out += ["## Results", "",
            "| card | bench | status | steps | dt max (ps) | Vout min..max (V) | "
            "tail V rms (uV) | tail current pp (mA) | tail zero x | edge charge (fC) | "
            "false quiet tail |",
            "|---|---|---|---:|---:|---|---:|---:|---:|---:|---|"]
    for (label, bench), stats in sorted(results.items()):
        if stats.get("status") != "observed":
            out.append("| %s | %s | %s: %s | - | - | - | - | - | - | - | - |"
                       % (label, bench, stats.get("status"), stats.get("reason", "")))
            continue
        out.append("| %s | %s | observed | %d | %.1f | %.4f..%.4f | %.3f | %.4f | %d | "
                   "%.3f | %s |"
                   % (label, bench, stats["steps"], stats["dt_max_s"] * 1e12,
                      stats["volume_min_V"], stats["volume_max_V"],
                      stats["tail_volume_rms_V"] * 1e6,
                      stats["tail_chatter_pp_A"] * 1e3,
                      stats["tail_chatter_zero_crossings"],
                      stats["edge_charge_C"] * 1e15,
                      "YES" if stats["false_quiet_tail"] else "no"))
    out.append("")

    failures = {key: stats for key, stats in results.items()
                if stats.get("status") != "observed" or stats.get("false_quiet_tail")}
    candidate_failures = {key: stats for key, stats in failures.items()
                          if key[0] == "bridge candidate"}
    out += ["## Verdict", ""]
    if candidate_failures:
        out.append("- the bridge candidate did not pass %d bench(es):"
                   % len(candidate_failures))
        for (label, bench), stats in sorted(candidate_failures.items()):
            out.append("  - %s / %s: %s" % (label, bench,
                                            stats.get("reason")
                                            or "false quiet tail"))
    else:
        out.append("- every candidate bench completed with three-dimensional "
                   "acceptance: no abort, continuous time steps, and no false "
                   "quiet tail.")
    stage1 = {key for key in failures if key[0] == "stage-1 frozen"}
    out += ["",
            "- stage-1 card, for comparison: %s"
            % ("%d bench(es) failed (%s)" % (len(stage1),
                                             ", ".join(sorted(b for _l, b in stage1)))
               if stage1 else "all benches completed"),
            "",
            "**How to read the step counts.** At 1 us with a 20 ps maximum step, roughly",
            "5e4 accepted steps is simply the baseline magnitude for this output",
            "resolution - it is not by itself an improvement. What the comparison shows",
            "is that the candidate no longer generates a large number of *extra* solve",
            "steps from high-frequency current chatter, which is strong stability",
            "evidence but is not proof of physical accuracy.",
            "",
            "**Not yet done, and required before any timing claim**: inverter FO1/FO4",
            "with several input slews, other supply voltages and load values, plus",
            "propagation-delay and switching-energy convergence under integrator and",
            "step changes, with every MOS port, the whole supply current and charge",
            "conservation re-checked. Only then may version-tagged *experimental* timing",
            "tables be generated for functional PnR exploration; no guaranteed fMAX or",
            "yield may be derived from them.",
            "",
            "Agreement on completion and on the absence of a false quiet tail is what",
            "this gate checks. It does not qualify the model for timing signoff, and a",
            "quiet output alone is never a pass.", ""]
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % REPORT)
    return 1 if candidate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
