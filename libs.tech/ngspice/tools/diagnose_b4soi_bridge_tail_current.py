#!/usr/bin/env python3
"""Targeted diagnosis of the 0.62 uA tail supply-current fluctuation.

The fd timing matrix left one item unresolved: the loaded inverter's settled tail
carries a mean supply current within 0.12 nA of the DC prediction, but with
RMS(I - mean) = 624 nA and 1.36 uA peak-to-peak.  A near-zero mean with a large
RMS is mathematically consistent with a zero-mean alternating component, so the
average matching the DC operating point does NOT prove the waveform is static.
Status assigned by the reviewer: TAIL_CURRENT_AC_UNRESOLVED.

Three cheap diagnostics, no model parameter is touched:

A  persistence - split the last 100 ns into consecutive 20 ns sub-windows and
   report mean / RMS(about own mean) / peak-to-peak / sign flips per window.
   Sustained fluctuation keeps every sub-window near 0.6 uA; a decaying tail
   shrinks window by window.

B  timestep and integrator convergence - the same bench at TRAP 20/10/5 ps and
   GEAR 20/10 ps.  If the alternation frequency rises as the maximum step
   shrinks, or the amplitude changes materially, the fluctuation is numerical.
   The reported zero-crossing rate is computed on the printed samples; the
   solver's own accepted-step count is reported alongside so sampling aliasing
   is visible rather than hidden.

C  static control - the same fd card and FO4 topology with VIN held at 0 and NO
   input edge, started from the DC operating point (no .IC), compared across
   four loads: real FO4 gates, an equivalent ideal 1 pF capacitor, no load, and
   the stage-1 card with real FO4 gates.  A fluctuation that survives with no
   switching at all is a model-internal or numerical effect, not FO4 charge.

Run:  python3 libs.tech/ngspice/tools/diagnose_b4soi_bridge_tail_current.py
Out:  libs.tech/ngspice/audit/b4soi_fd_bridge_tail_current.md
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
CARD = ROOT / "libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc"
STAGE1 = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
REPORT = ROOT / "libs.tech/ngspice/audit/b4soi_fd_bridge_tail_current.md"

VDD = 3.0
W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
SLEW_S = 2e-9
DELAY_S = 20e-9
WIDTH_S = 200e-9
LAST_EDGE_S = DELAY_S + WIDTH_S
TAIL_S = 100e-9
SUBWINDOW_S = 20e-9
LOAD_CAP_F = 1e-12
SETTINGS = (("trap/20ps", "TRAP", 20e-12), ("trap/10ps", "TRAP", 10e-12),
            ("trap/5ps", "TRAP", 5e-12), ("gear/20ps", "GEAR", 20e-12),
            ("gear/10ps", "GEAR", 10e-12))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_lines(inc_name: str, load: str):
    """Load branch only; the supplies are defined once by bench()."""
    lines = []
    if load == "fo4":
        for index in range(4):
            lines += [
                "MLN%d FL%d OUTF 0 SUB nch W=%gu L=%gu" % (index, index, W_N_UM, L_UM),
                "MLP%d FL%d OUTF VLOAD SUB pch W=%gu L=%gu" % (index, index, W_P_UM, L_UM),
                "CFL%d FL%d 0 1e-15" % (index, index)]
        lines += ["VSO OUT OUTF 0"]
    elif load == "cap":
        lines += ["CL OUT 0 %g" % LOAD_CAP_F]
    return lines


def bench(inc_name: str, load: str, method: str, tmax_s: float, tstop_s: float,
          switching: bool):
    lines = [
        "* tail-current diagnosis load=%s %s/%g tstop=%g switching=%s"
        % (load, method, tmax_s * 1e12, tstop_s * 1e9, switching),
        '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=%s RELTOL=1e-4 ABSTOL=1e-12" % method,
        "VPWRD VDUT 0 %g" % VDD, "VPWRL VLOAD 0 %g" % VDD, "VSUB SUB 0 0",
    ]
    if switching:
        lines.append("VIN IN 0 PULSE(0 %g %g %g %g %g 1e6)"
                     % (VDD, DELAY_S, SLEW_S, SLEW_S, WIDTH_S))
    else:
        lines.append("VIN IN 0 0")
    lines += [
        "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
        "MN ND_N NG_N NS_N NE_N nch W=%gu L=%gu" % (W_N_UM, L_UM),
        "VDP OUT ND_P 0", "VSP VDUT NS_P 0", "VGP IN NG_P 0", "VEP SUB NE_P 0",
        "MP ND_P NG_P NS_P NE_P pch W=%gu L=%gu" % (W_P_UM, L_UM),
    ] + load_lines(inc_name, load) + [
        ".TRAN 2e-9 %g 0 %g" % (tstop_s, tmax_s),
        ".PRINT TRAN FORMAT=CSV FILE=t.csv V(IN) V(OUT) I(VIN) I(VPWRD) I(VPWRL) "
        "I(VSUB) I(VDN) I(VSN) I(VGN) I(VEN) I(VDP) I(VSP) I(VGP) I(VEP)",
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
    return times, cols


def window_stats(times, current, start, stop):
    keep = [k for k, t in enumerate(times) if start <= t <= stop]
    if len(keep) < 2:
        return None
    cur = [current[k] for k in keep]
    mean = sum(cur) / len(cur)
    rms_ac = math.sqrt(sum((x - mean) ** 2 for x in cur) / len(cur))
    flips = sum(1 for a, b in zip(cur, cur[1:]) if (a - mean) * (b - mean) < 0)
    return {"mean_A": mean, "rms_ac_A": rms_ac, "pp_A": max(cur) - min(cur),
            "sign_flips": flips, "samples": len(keep)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-c", action="store_true")
    args = parser.parse_args()
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")
    if not CARD.is_file():
        raise SystemExit("missing %s" % CARD)

    out = ["# Tail supply-current fluctuation diagnosis: fd bridge", "",
           "Card: `%s`" % CARD.relative_to(ROOT),
           "Simulator: `%s` (Xyce NF 7.10.0, BSIMSOI 4.5.0)." % xyce.XYCE, "",
           "Status under diagnosis: TAIL_CURRENT_AC_UNRESOLVED. The settled tail",
           "carries mean(I) within 0.12 nA of the DC prediction but with 0.62 uA RMS",
           "and 1.36 uA peak-to-peak. No model parameter is modified here.", ""]

    with tempfile.TemporaryDirectory(prefix="icps-tail-diag-") as scratch:
        work = Path(scratch)
        inc = work / "card.inc"
        inc.write_text(xyce.port_card(CARD.read_text(encoding="utf-8")), encoding="utf-8")

        # ---- Part A: persistence in consecutive 20 ns sub-windows ----
        out += ["## A persistence: consecutive 20 ns sub-windows of the last 100 ns", "",
                "Sustained fluctuation keeps every sub-window near the same RMS; a",
                "still-decaying recovery shrinks window by window.", "",
                "| sub-window (ns after last edge) | mean (nA) | RMS (nA) | pp (nA) | "
                "sign flips | samples |",
                "|---:|---:|---:|---:|---:|---:|"]
        deck = bench(inc.name, "fo4", "TRAP", 20e-12,
                     LAST_EDGE_S + TAIL_S, True)
        result = xyce.run(deck, "diagA", work, timeout=1800.0, budget_bytes=300_000_000)
        if result["status"] != "observed":
            out += ["| (failed) | | | | | |", "", "- %s" % result.get("reason"), ""]
        else:
            times, cols = read_named(result["csv"])
            result["csv"].unlink(missing_ok=True)
            supply = cols["I(VPWRD)"]
            out += ["- solver accepted steps reported by Xyce: see the run log; printed",
                    "  samples in the window: %d" % len([t for t in times
                                                        if t >= LAST_EDGE_S]), ""]
            for index in range(int(TAIL_S / SUBWINDOW_S)):
                start = LAST_EDGE_S + index * SUBWINDOW_S
                stop = start + SUBWINDOW_S
                stats = window_stats(times, supply, start, stop)
                if stats is None:
                    out.append("| %g-%g | n/a | n/a | n/a | n/a | 0 |"
                               % (start * 1e9, stop * 1e9))
                    continue
                out.append("| %g-%g | %+.2f | %.2f | %.2f | %d | %d |"
                           % (start * 1e9, stop * 1e9, stats["mean_A"] * 1e9,
                              stats["rms_ac_A"] * 1e9, stats["pp_A"] * 1e9,
                              stats["sign_flips"], stats["samples"]))
            out.append("")

        # ---- Part B: timestep and integrator convergence ----
        out += ["## B timestep and integrator convergence (VDD=3 V, FO4)", "",
                "If the alternation frequency rises as tmax shrinks, or the amplitude",
                "changes materially, the fluctuation is numerical. The zero-crossing",
                "rate is per microsecond of the observed tail window.", "",
                "| setting | tail mean (nA) | RMS(I-mean) (nA) | pp (nA) | "
                "sign flips | flips per us | printed samples |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        conv = []
        for label, method, tmax_s in SETTINGS:
            deck = bench(inc.name, "fo4", method, tmax_s,
                         LAST_EDGE_S + TAIL_S, True)
            result = xyce.run(deck, "diagB_%s" % label.replace("/", "_"), work,
                              timeout=1800.0, budget_bytes=300_000_000)
            if result["status"] != "observed":
                conv.append((label, None, None, None, None, None, 0))
                continue
            times, cols = read_named(result["csv"])
            result["csv"].unlink(missing_ok=True)
            keep = [k for k, t in enumerate(times) if t >= LAST_EDGE_S]
            cur = [cols["I(VPWRD)"][k] for k in keep]
            mean = sum(cur) / len(cur)
            rms = math.sqrt(sum((x - mean) ** 2 for x in cur) / len(cur))
            flips = sum(1 for a, b in zip(cur, cur[1:]) if (a - mean) * (b - mean) < 0)
            pp = max(cur) - min(cur)
            conv.append((label, mean * 1e9, rms * 1e9, pp * 1e9, flips,
                         flips / (TAIL_S * 1e6), len(keep)))
        for label, mean, rms, pp, flips, rate, samples in conv:
            if mean is None:
                out.append("| %s | failed | | | | | |" % label)
                continue
            out.append("| %s | %+.2f | %.2f | %.2f | %d | %.1f | %d |"
                       % (label, mean, rms, pp, flips, rate, samples))
        stable = (len({round(row[2], 1) for row in conv if row[1] is not None}) <= 1)
        out += ["", "- RMS(I-mean) across settings: %s."
                % ("stable at the same level" if stable
                  else "CHANGES with the timestep/integrator"), ""]

        # ---- Part C: static control, no switching ----
        if not args.skip_c:
            out += ["## C static control: VIN held at 0, NO input edge", "",
                    "The same card and topology, started from the DC operating point",
                    "(no .IC), with no switching at all. Any fluctuation here is a",
                    "model-internal or numerical effect, not FO4 charge.", "",
                    "| card | load | supply tail RMS (A) | pp (A) | tail mean (A) |",
                    "|---|---|---:|---:|---:|"]
            for card_label, card_path in (("fd", CARD), ("stage1", STAGE1)):
                inc_c = work / ("card_%s.inc" % card_label)
                inc_c.write_text(xyce.port_card(card_path.read_text(encoding="utf-8")),
                                 encoding="utf-8")
                for load in ("fo4", "cap", "none"):
                    deck = bench(inc_c.name, load, "TRAP", 20e-12, TAIL_S, False)
                    result = xyce.run(deck, "diagC_%s_%s" % (card_label, load), work,
                                      timeout=1800.0, budget_bytes=300_000_000)
                    if result["status"] != "observed":
                        out.append("| %s | %s | %s | | |"
                                   % (card_label, load, result.get("status")))
                        continue
                    times, cols = read_named(result["csv"])
                    result["csv"].unlink(missing_ok=True)
                    stats = window_stats(times, cols["I(VPWRD)"], 0.0, times[-1])
                    out.append("| %s | %s | %.3e | %.3e | %+.3e |"
                               % (card_label, load, stats["rms_ac_A"], stats["pp_A"],
                                  stats["mean_A"]))
            out.append("")

    out += ["## Interpretation guide", "",
            "- A: sub-window RMS flat near 0.6 uA -> sustained fluctuation, not recovery.",
            "- A: sub-window RMS shrinking -> still part of the recovery transient.",
            "- B: alternation frequency rises as tmax shrinks -> solver time-step chatter.",
            "- B: amplitude and frequency stable across settings -> a model response.",
            "- C: fluctuation present with no switching -> model-internal state or",
            "  numerical integration; the FO4 charge hypothesis is then excluded.",
            "- C: fluctuation absent without switching -> it is an FO4 charge effect.",
            "",
            "No model parameter is modified by this diagnosis, and no timing or energy",
            "claim is made here.", ""]
    report = REPORT
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
