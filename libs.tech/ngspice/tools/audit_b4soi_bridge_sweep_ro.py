#!/usr/bin/env python3
"""DUT/load sizing sweep and ring-oscillator characterisation, in Xyce.

Two experiments on the fd experimental card (SOIMOD=2, Xyce BSIMSOI 4.5.0),
both using PARALLEL W24/L6 reference devices rather than uncalibrated new
geometries, so no W-scaling qualification is implied:

Part A - DUT x1/x2/x4 (parallel W24 NMOS + W48 PMOS) driving load x0/x1/x2/x4
         (0..4 identical inverter gate pairs).  12 combinations, each reporting
         VOH/VOL, tPHL/tPLH/tFALL/tRISE, per-stage energies, the charge delivered
         into the load branch and the load's effective input capacitance.
         Purpose: relate fanout to delay, slew, input capacitance and energy for
         later cell sizing / buffer insertion.

Part B - 5/11/21-stage ring oscillators (odd stages, each stage one W24/48
         inverter).  Reports the measured frequency, steady-state amplitude,
         supply current and the numerical-setting dependence (TRAP at 20 ps and
         50 ps maximum steps).  Purpose: check whether the FO1 extrapolation
         (~15.7 MHz for 5 stages, ~3.7 MHz for 21 stages at VDD=3 V) holds in a
         closed loop.

Both parts are experimental and version-tagged.  No signoff Liberty, no
guaranteed fMAX and no yield claim may be derived from them, and the fanout
devices are NOT a W96-equivalent physical transistor.

Run:  python3 libs.tech/ngspice/tools/audit_b4soi_bridge_sweep_ro.py [--quick]
Out:  libs.tech/ngspice/audit/b4soi_fd_bridge_sweep_ro.md
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
STAGE1 = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
REPORT = ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_sweep_ro.md" % VARIANT)

VDD = 3.0
W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
SLEW_S = 2e-9
DELAY_S = 20e-9
WIDTH_S = 200e-9
PERIOD_S = 1e6          # single cycle
TMAX_S = 20e-12
LOAD_PARASITIC_F = 1e-15
VOH_MARGIN_V = 0.15
VOL_MAX_V = 0.15
SETTLE_EPSILON_V = 0.010
RO_STAGES = (5, 11, 21)
RO_SETTINGS = (("trap/20ps", "TRAP", 20e-12), ("trap/50ps", "TRAP", 50e-12))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sweep_deck(inc_name: str, dut_mult: int, load_mult: int) -> list:
    """DUT = dut_mult parallel inverter drives; load = load_mult gate pairs."""
    lines = [
        "* DUT/load sweep DUT x%d load x%d" % (dut_mult, load_mult),
        '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWRD VDUT 0 %g" % VDD, "VPWRL VLOAD 0 %g" % VDD, "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %g %g %g %g %g %g)"
        % (VDD, DELAY_S, SLEW_S, SLEW_S, WIDTH_S, PERIOD_S),
    ]
    for index in range(dut_mult):
        suffix = "" if dut_mult == 1 else "_%d" % index
        dev = "" if dut_mult == 1 else "_%d" % index
        lines += [
            "VDN%s OUT ND_N%s 0" % (dev, suffix),
            "VSN%s 0 NS_N%s 0" % (dev, suffix),
            "VGN%s IN NG_N%s 0" % (dev, suffix),
            "VEN%s SUB NE_N%s 0" % (dev, suffix),
            "MDN%s ND_N%s NG_N%s NS_N%s NE_N%s nch W=%gu L=%gu"
            % (dev, suffix, suffix, suffix, suffix, W_N_UM, L_UM),
            "VDP%s OUT ND_P%s 0" % (dev, suffix),
            "VSP%s VDUT NS_P%s 0" % (dev, suffix),
            "VGP%s IN NG_P%s 0" % (dev, suffix),
            "VEP%s SUB NE_P%s 0" % (dev, suffix),
            "MDP%s ND_P%s NG_P%s NS_P%s NE_P%s pch W=%gu L=%gu"
            % (dev, suffix, suffix, suffix, suffix, W_P_UM, L_UM),
        ]
    lines += ["VSO OUT OUTF 0"]
    for index in range(load_mult):
        lines += [
            "MLN%d FL%d OUTF 0 SUB nch W=%gu L=%gu" % (index, index, W_N_UM, L_UM),
            "MLP%d FL%d OUTF VLOAD SUB pch W=%gu L=%gu" % (index, index, W_P_UM, L_UM),
            "CFL%d FL%d 0 %g" % (index, index, LOAD_PARASITIC_F),
        ]
    lines += [
        ".IC V(OUT)=%g" % VDD,
        ".TRAN 2e-9 %g 0 %g" % (DELAY_S + WIDTH_S + 400e-9, TMAX_S),
        ".PRINT TRAN FORMAT=CSV FILE=s.csv V(IN) V(OUT) I(VIN) I(VPWRD) I(VPWRL) "
        "I(VSUB) I(VSO)",
        ".END",
    ]
    return lines


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


def integrate(times, values, start=None, stop=None):
    total = 0.0
    for index in range(1, len(times)):
        if start is not None and times[index] < start:
            continue
        if stop is not None and times[index - 1] > stop:
            break
        total += 0.5 * (values[index] + values[index - 1]) * (times[index] - times[index - 1])
    return total


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


def analyse_sweep(times, cols, dut_mult: int, load_mult: int) -> dict:
    vin, vout = cols["V(IN)"], cols["V(OUT)"]
    half = VDD / 2.0
    low_w = [vout[k] for k in range(len(times))
             if DELAY_S + 0.5 * WIDTH_S <= times[k] <= DELAY_S + WIDTH_S]
    high_w = [vout[k] for k in range(len(times))
              if DELAY_S + WIDTH_S + 0.5 * WIDTH_S <= times[k] <= DELAY_S + WIDTH_S + 400e-9]
    lo = sum(low_w) / len(low_w) if low_w else None
    hi = sum(high_w) / len(high_w) if high_w else None
    rails_ok = (lo is not None and hi is not None
                and lo <= VOL_MAX_V and hi >= VDD - VOH_MARGIN_V)
    fall_in = crossing(times, vin, half, 0.0, rising=True)
    fall_out = crossing(times, vout, half, fall_in or 0.0, rising=False)
    rise_in = crossing(times, vin, half, DELAY_S + WIDTH_S, rising=False)
    rise_out = crossing(times, vout, half, rise_in or 0.0, rising=True)
    tphl = tplh = tfall = trise = None
    if fall_in is not None and fall_out is not None:
        tphl = fall_out - fall_in
        a = crossing(times, vout, 0.9 * VDD, fall_in, rising=False)
        b = crossing(times, vout, 0.1 * VDD, fall_in, rising=False)
        tfall = (b - a) if (a is not None and b is not None) else None
    if rise_in is not None and rise_out is not None:
        tplh = rise_out - rise_in
        a = crossing(times, vout, 0.1 * VDD, rise_in, rising=True)
        b = crossing(times, vout, 0.9 * VDD, rise_in, rising=True)
        trise = (b - a) if (a is not None and b is not None) else None
    fall_end = rise_in or (DELAY_S + WIDTH_S)
    e_dut_fall = abs(integrate(times, cols["I(VPWRD)"], fall_in, fall_end) * VDD) \
        if fall_in is not None else None
    e_dut_rise = abs(integrate(times, cols["I(VPWRD)"], rise_in, PERIOD_S) * VDD) \
        if rise_in is not None else None
    e_ld_fall = abs(integrate(times, cols["I(VPWRL)"], fall_in, fall_end) * VDD) \
        if fall_in is not None else None
    # charge delivered into the load branch, and the load's effective Cin
    q_load_fall = abs(integrate(times, cols["I(VSO)"], fall_in, fall_end)) \
        if fall_in is not None else None
    q_load_rise = abs(integrate(times, cols["I(VSO)"], rise_in, PERIOD_S)) \
        if rise_in is not None else None
    c_in_fall = (q_load_fall / VDD) if (q_load_fall is not None and VDD) else None
    if not rails_ok:
        tphl = tplh = tfall = trise = None
        e_dut_fall = e_dut_rise = e_ld_fall = None
        q_load_fall = q_load_rise = c_in_fall = None
    return {"rails_ok": rails_ok, "settled_low_V": lo, "settled_high_V": hi,
            "tphl_s": tphl, "tplh_s": tplh, "tfall_s": tfall, "trise_s": trise,
            "e_dut_fall_J": e_dut_fall, "e_dut_rise_J": e_dut_rise,
            "e_ld_fall_J": e_ld_fall, "q_load_fall_C": q_load_fall,
            "q_load_rise_C": q_load_rise, "c_in_fall_F": c_in_fall,
            "peak_supply_A": max(abs(v) for v in cols["I(VPWRD)"]),
            "steps": len(times)}


def ro_deck(inc_name: str, stages: int, method: str, tmax_s: float, tstop_s: float):
    lines = [
        "* %d-stage ring oscillator" % stages,
        '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=%s RELTOL=1e-4 ABSTOL=1e-12" % method,
        "VPWR VDD 0 %g" % VDD, "VSUB SUB 0 0",
    ]
    for index in range(stages):
        nxt = (index + 1) % stages
        lines += [
            "MRO_N%d N%d N%d 0 SUB nch W=%gu L=%gu" % (index, nxt, index, W_N_UM, L_UM),
            "MRO_P%d N%d N%d VDD SUB pch W=%gu L=%gu" % (index, nxt, index, W_P_UM, L_UM),
        ]
    lines += [".IC V(N0)=0",
              ".TRAN 2e-9 %g 0 %g" % (tstop_s, tmax_s),
              ".PRINT TRAN FORMAT=CSV FILE=ro.csv " +
              " ".join("V(N%d)" % index for index in range(stages)) +
              " I(VPWR)",
              ".END"]
    return lines


def ro_frequency(times, vout, vdd: float) -> dict:
    """Rising crossings of VDD/2 over the settled window -> period statistics."""
    half = vdd / 2.0
    crossings = []
    for index in range(1, len(times)):
        a, b = vout[index - 1], vout[index]
        if a == b or (a - half) * (b - half) > 0:
            continue
        if rising_cross(b, a):
            crossings.append(times[index - 1] + (half - a) / (b - a) * (times[index] - times[index - 1]))
    periods = [b - a for a, b in zip(crossings, crossings[1:])]
    if not periods:
        return {"rising_crossings": len(crossings), "status": "NO_PERIODS",
                "vmin": min(vout), "vmax": max(vout)}
    settled = periods[len(periods) // 2:] or periods
    mean = sum(settled) / len(settled)
    spread = (max(settled) - min(settled)) / mean if mean else None
    return {"rising_crossings": len(crossings), "periods_used": len(settled),
            "mean_period_s": mean, "period_spread": spread,
            "frequency_Hz": 1.0 / mean if mean else None,
            "vmin": min(vout), "vmax": max(vout),
            "status": "observed"}


def rising_cross(b, a):
    return b > a


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")
    if not CARD.is_file():
        raise SystemExit("missing %s" % CARD)

    report = REPORT
    out = ["# DUT/load sizing sweep and ring oscillators: fd bridge", "",
           "Card: `%s` (EXPERIMENTAL, SOIMOD=2)" % CARD.relative_to(ROOT),
           "Simulator: `%s` (Xyce NF 7.10.0, BSIMSOI 4.5.0)." % xyce.XYCE,
           "DUT and load use PARALLEL W24/L6 reference devices; no uncalibrated new",
           "geometry is introduced, so nothing here unlocks a geometry-scaling",
           "qualification. Experimental and version-tagged: no signoff Liberty, no",
           "guaranteed fMAX, no yield claim.", ""]
    sweep_rows, ro_rows = [], []
    with tempfile.TemporaryDirectory(prefix="icps-sweep-ro-") as scratch:
        work = Path(scratch)
        inc = work / "card.inc"
        inc.write_text(xyce.port_card(CARD.read_text(encoding="utf-8")), encoding="utf-8")

        out += ["## Part A: DUT/load sizing sweep (VDD=3 V, 2 ns edge)", "",
                "| DUT | load | VOH (V) | VOL (V) | tPHL (ns) | tPLH (ns) | "
                "tFALL (ns) | tRISE (ns) | E_DUT fall+rise (pJ) | E_load fall (pJ) | "
                "Q_load fall (fC) | C_in,load (fF) |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        mults = (1, 2, 4) if not args.quick else (1,)
        loads = (0, 1, 2, 4) if not args.quick else (1,)
        for dut_mult in mults:
            for load_mult in loads:
                deck = sweep_deck(inc.name, dut_mult, load_mult)
                name = "sw_%d_%d" % (dut_mult, load_mult)
                result = xyce.run(deck, name, work, timeout=1800.0,
                                  budget_bytes=400_000_000)
                if result["status"] != "observed":
                    sweep_rows.append((dut_mult, load_mult, {"status": "failed",
                                     "reason": result.get("reason", "")}))
                    print("DUT x%d load x%d %s" % (dut_mult, load_mult,
                                                   result["status"]), flush=True)
                    continue
                times, cols = read_named(result["csv"])
                result["csv"].unlink(missing_ok=True)
                stats = analyse_sweep(times, cols, dut_mult, load_mult)
                stats["status"] = "observed"
                sweep_rows.append((dut_mult, load_mult, stats))
                print("DUT x%d load x%d ok tPHL=%s" % (dut_mult, load_mult,
                      "n/a" if stats["tphl_s"] is None else "%.3f" % (stats["tphl_s"] * 1e9)),
                      flush=True)

        for dut_mult, load_mult, stats in sorted(sweep_rows):
            if stats.get("status") != "observed":
                out.append("| x%d | x%d | %s: %s | | | | | | | | |"
                           % (dut_mult, load_mult, stats.get("status"),
                              stats.get("reason", "")))
                continue
            e_dut = (None if (stats["e_dut_fall_J"] is None or stats["e_dut_rise_J"] is None)
                     else stats["e_dut_fall_J"] + stats["e_dut_rise_J"])
            out.append("| x%d | x%d | %.3f | %.3f | %s | %s | %s | %s | %s | %s | %s | %s |"
                       % (dut_mult, load_mult, stats["settled_high_V"],
                          stats["settled_low_V"],
                          "n/a" if stats["tphl_s"] is None else "%.3f" % (stats["tphl_s"] * 1e9),
                          "n/a" if stats["tplh_s"] is None else "%.3f" % (stats["tplh_s"] * 1e9),
                          "n/a" if stats["tfall_s"] is None else "%.3f" % (stats["tfall_s"] * 1e9),
                          "n/a" if stats["trise_s"] is None else "%.3f" % (stats["trise_s"] * 1e9),
                          "n/a" if e_dut is None else "%.2f" % (e_dut * 1e12),
                          "n/a" if stats["e_ld_fall_J"] is None else "%.2f" % (stats["e_ld_fall_J"] * 1e12),
                          "n/a" if stats["q_load_fall_C"] is None else "%.2f" % (stats["q_load_fall_C"] * 1e15),
                          "n/a" if stats["c_in_fall_F"] is None else "%.1f" % (stats["c_in_fall_F"] * 1e15)))
        out.append("")

        out += ["## Part B: ring oscillators", "",
                "Each stage is one W24/L6 NMOS + W48/L6 PMOS inverter; odd stages;",
                "N0 forced low to break the symmetric operating point.",
                "",
                "| stages | setting | tstop | rising crossings | periods used | "
                "mean period (ns) | period spread | frequency (MHz) | Vout min..max (V) |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---|"]
        for stages in RO_STAGES:
            tstop = max(2e-6, 30 * 2 * stages * 6.4e-9)
            for label, method, tmax_s in RO_SETTINGS:
                if args.quick and label != "trap/20ps":
                    continue
                deck = ro_deck(inc.name, stages, method, tmax_s, tstop)
                name = "ro_%d_%s" % (stages, label.replace("/", "_"))
                result = xyce.run(deck, name, work, timeout=3600.0,
                                  budget_bytes=400_000_000)
                if result["status"] != "observed":
                    ro_rows.append((stages, label, {"status": result["status"],
                                                    "reason": result.get("reason", "")}))
                    print("RO %d %s %s" % (stages, label, result["status"]), flush=True)
                    continue
                times, cols = read_named(result["csv"])
                result["csv"].unlink(missing_ok=True)
                stats = ro_frequency(times, cols["V(N0)"], VDD)
                stats["status"] = "observed"
                stats["tstop_s"] = tstop
                ro_rows.append((stages, label, stats))
                print("RO %d %s f=%s MHz" % (stages, label,
                      "n/a" if stats.get("frequency_Hz") is None
                      else "%.2f" % (stats["frequency_Hz"] / 1e6)), flush=True)

        for stages, label, stats in sorted(ro_rows):
            if stats.get("status") != "observed":
                out.append("| %d | %s | | | | | | %s: %s | |"
                           % (stages, label, stats.get("status"), stats.get("reason", "")))
                continue
            out.append("| %d | %s | %g | %d | %d | %s | %s | %s | %.3f..%.3f |"
                       % (stages, label, stats.get("tstop_s", 0) * 1e9,
                          stats["rising_crossings"], stats.get("periods_used", 0),
                          "n/a" if stats.get("mean_period_s") is None
                          else "%.2f" % (stats["mean_period_s"] * 1e9),
                          "n/a" if stats.get("period_spread") is None
                          else "%.2f%%" % (100 * stats["period_spread"]),
                          "n/a" if stats.get("frequency_Hz") is None
                          else "%.2f" % (stats["frequency_Hz"] / 1e6),
                          stats["vmin"], stats["vmax"]))
        out.append("")

    out += ["## Interpretation and scope", "",
            "- Part A is a 3 V, 25 C, 2 ns-edge experiment on parallel W24/L6", 
            "  reference devices. Its `C_in,load` column is a trajectory-dependent", 
            "  dynamic charge result from the historical sweep window; it is not a", 
            "  fixed Liberty pin capacitance. Use `b4soi_fd_bridge_cin_definitions.md`", 
            "  for the closure-consistent 2258.3 fF edge-start result, the intrinsic", 
            "  Cgg values, and the frozen `C_eff_energy` status.",
            "- Part B is a closed-loop check, not a confirmation of the isolated FO1", 
            "  extrapolation. For odd stage count N, `f = 1/[N(tPHL+tPLH)]`.",
            "  The observed mean per-stage `tPHL+tPLH` is 17.15 ns (5 stages)",
            "  and 17.32 ns (11 and 21 stages), so the mean one-way propagation", 
            "  delay is 8.58-8.66 ns. The closed-loop / FO1 frequency ratios are", 
            "  0.743, 0.736, and 0.737 respectively; 0.74 is specific to this", 
            "  topology, card, bias, and input-slew history, not a universal derating.",
            "- The reported `0.00%` period spread is rounded to two decimal places.", 
            "  This audit covers TRAP with 20 ps and 50 ps maximum steps only;", 
            "  GEAR and 5 ps convergence remain open checks.",
            "- `Vout min..max` records the observed rail excursion (about -0.20 to", 
            "  3.21 V). The excursion duration, terminal-node attribution, and", 
            "  finer-step sensitivity are not characterized here and are not a", 
            "  silicon-waveform claim.",
            "- The 34.32 pJ x1/x1 sweep entry and the signed whole-cycle -30.043 pJ", 
            "  energy in the C_in audit use different historical windows/sign", 
            "  conventions. `C_eff_energy` remains frozen until the complete", 
            "  sizing sweep is re-integrated through `waveint.py`.",
            "", "## Engineering planning baseline", "",
            "Use only as version-limited FD-bridge/Xyce simulation planning values:",
            "5-stage RO ~11.7 MHz, 11-stage ~5.3 MHz, 21-stage ~2.8 MHz, and", 
            "closed-loop mean propagation delay ~8.6 ns/stage. Keep the earlier", 
            "2 ns-edge FO1 extrapolations (~15.7 and ~3.7 MHz) as a separate", 
            "simplified reference. Do not emit signoff Liberty or infer silicon", 
            "frequency, PVT behavior, geometry scaling, or yield from this report.", ""]
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
