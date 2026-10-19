#!/usr/bin/env python3
"""2x2 DUT/load cross-model experiment, plus the overlap-only control.

Motivation. The experimental fd card is ~2.4x slower than the stage-1 reference
in a loaded inverter. That comparison changes several things at once: the DC I-V
and effective drive, the gate-source/gate-drain overlap charge (stage-1 has
none; the experimental cards carry the PDK p.9 measured CGSO/CGDO), the DUT's own
Miller capacitance, the fanout's input capacitance, and SOIMOD. So the 2.4x is a
circuit-level difference between two complete models and cannot be decomposed
into "PMOS on-resistance" versus "capacitance" from that data alone.

The design note that matters: the ratios are tPHL 2.38x, tPLH 2.42x, tFALL 3.08x,
tRISE 2.44x. The degradation is NOT asymmetric between pull-down and pull-up, so
the timing data do not by themselves support the PMOS linear-region regression as
the cause. A uniform slowdown across both edges is the signature of a capacitive
load change, which is exactly what this experiment separates.

Arms (fixed VDD=3 V, 2 ns edge, one real FO1 gate load):
  1. stage-1 DUT  + stage-1 load   frozen reference
  2. fd DUT       + fd load        experimental reference
  3. fd DUT       + stage-1 load   new drive, old input capacitance
  4. stage-1 DUT  + fd load        old drive, new input capacitance
  5. overlap DUT  + stage-1 load   CONTROL: old DC card + p.9 overlap only
  6. overlap DUT  + overlap load   control, both ends

The overlap arm is the counterfactual that separates the charge contribution from
the device contribution: it keeps stage-1's DC parameters and SOIMOD=0 and adds
only the measured CGSO/CGDO that already passed R1-R3. The separation

    dt = dt_charge + dt_device + dt_interaction

is an engineering attribution from controlled counterfactuals, not a strict
independent linear decomposition, and is labelled as such.

Energy boundaries: the DUT stage and the load stage have SEPARATE supply branches
at the same voltage, so the reported energies are per stage rather than one lumped
integral, and they are split into falling-edge, rising-edge, full-period and
static-baseline windows.

Model-name collisions: both cards define `nch`/`pch`, so every included card is
renamed with a per-arm prefix before it is written out. Without that, `.include`
order silently overrides one model with the other.

Run:  python3 libs.tech/ngspice/tools/audit_b4soi_bridge_crossmodel.py
Out:  libs.tech/ngspice/audit/b4soi_crossmodel_timing.md
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent

CARDS = {
    "stage1": ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc",
    "fd": ROOT / "libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc",
    "overlap": ROOT / "libs.tech/ngspice/models/b4soi_overlap_candidate.inc",
}
ARMS = (
    ("stage1", "stage1", "frozen reference"),
    ("fd", "fd", "experimental reference"),
    ("fd", "stage1", "new drive, old input capacitance"),
    ("stage1", "fd", "old drive, new input capacitance"),
    ("overlap", "stage1", "CONTROL: old DC card + p.9 overlap charge only"),
    ("overlap", "overlap", "control, overlap at both ends"),
)

VDD = 3.0
W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
SLEW_S = 2e-9
DELAY_S = 20e-9
WIDTH_S = 200e-9
PERIOD_S = 400e-9
TMAX_S = 20e-12
LOAD_PARASITIC_F = 1e-15

VOL_MAX_V = 0.15
VOH_MARGIN_V = 0.15


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rename_models(text: str, prefix: str) -> str:
    """Prefix every `.model <name> <type>` so two cards can coexist."""
    return re.sub(r"(?im)^(\.model\s+)(\w+)", lambda m: "%s%s%s" % (m.group(1), prefix, m.group(2)),
                  text)


def build_deck(dut_prefix: str, load_prefix: str) -> list:
    dut_n, dut_p = dut_prefix + "nch", dut_prefix + "pch"
    ld_n, ld_p = load_prefix + "nch", load_prefix + "pch"
    includes = []
    for tag in (dut_prefix, load_prefix):
        if tag not in includes:
            includes.append(tag)
    return [
        "* cross-model DUT/load bench: DUT=%s load=%s" % (dut_prefix, load_prefix),
    ] + ['.include "%s.inc"' % tag for tag in includes] + [
        ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VPWRD VDUT 0 %g" % VDD,
        "VPWRL VLOAD 0 %g" % VDD,
        "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %g %g %g %g %g %g)"
        % (VDD, DELAY_S, SLEW_S, SLEW_S, WIDTH_S, PERIOD_S),
        # DUT, all four terminals sensed; E goes through its own 0 V source so the
        # per-device KCL sum can actually close. Without sensing E, a SOIMOD=0 card
        # with real body current shows a spurious ~100 fC closure failure.
        "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
        "MN ND_N NG_N NS_N NE_N %s W=%gu L=%gu" % (dut_n, W_N_UM, L_UM),
        "VDP OUT ND_P 0", "VSP VDUT NS_P 0", "VGP IN NG_P 0", "VEP SUB NE_P 0",
        "MP ND_P NG_P NS_P NE_P %s W=%gu L=%gu" % (dut_p, W_P_UM, L_UM),
        # FO1 gate load: DRAIN to its own output FL, GATE to the DUT output OUT.
        "MLN FL OUT 0 SUB %s W=%gu L=%gu" % (ld_n, W_N_UM, L_UM),
        "MLP FL OUT VLOAD SUB %s W=%gu L=%gu" % (ld_p, W_P_UM, L_UM),
        "CFL FL 0 %g" % LOAD_PARASITIC_F,
        ".IC V(OUT)=%g" % VDD,
        ".TRAN 2e-9 %g 0 %g" % (PERIOD_S, TMAX_S),
        ".PRINT TRAN FORMAT=CSV FILE=x.csv V(IN) V(OUT) I(VIN) I(VPWRD) I(VPWRL) "
        "I(VSUB) I(VDN) I(VSN) I(VGN) I(VEN) I(VDP) I(VSP) I(VGP) I(VEP)",
        ".END",
    ]


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
    """Header-keyed columns; Xyce does not guarantee every requested variable."""
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    header = [name.strip() for name in lines[0].split(",")]
    times = []
    cols = {name: [] for name in header}
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


def analyse(times, col):
    vin, vout = col["V(IN)"], col["V(OUT)"]
    i_vin, i_pwrd, i_pwrl, i_sub = (col["I(VIN)"], col["I(VPWRD)"],
                                    col["I(VPWRL)"], col["I(VSUB)"])
    i_dn, i_sn, i_gn, i_en = (col["I(VDN)"], col["I(VSN)"], col["I(VGN)"],
                              col["I(VEN)"])
    i_dp, i_sp, i_gp, i_ep = (col["I(VDP)"], col["I(VSP)"], col["I(VGP)"],
                              col["I(VEP)"])
    half = VDD / 2.0
    low_w = [vout[k] for k in range(len(times))
             if DELAY_S + 0.5 * WIDTH_S <= times[k] <= DELAY_S + WIDTH_S]
    high_w = [vout[k] for k in range(len(times))
              if DELAY_S + WIDTH_S + 0.5 * WIDTH_S <= times[k] <= PERIOD_S]
    lo = sum(low_w) / len(low_w) if low_w else None
    hi = sum(high_w) / len(high_w) if high_w else None
    rails_ok = (lo is not None and hi is not None
                and lo <= VOL_MAX_V and hi >= VDD - VOH_MARGIN_V)
    tphl = tplh = tfall = trise = None
    fall_in = crossing(times, vin, half, 0.0, rising=True)
    fall_out = crossing(times, vout, half, fall_in or 0.0, rising=False)
    rise_in = crossing(times, vin, half, DELAY_S + WIDTH_S, rising=False)
    rise_out = crossing(times, vout, half, rise_in or 0.0, rising=True)
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
    fall_end = rise_in or PERIOD_S
    rise_end = PERIOD_S
    e_dut = {
        "fall_edge_J": abs(integrate(times, i_pwrd, fall_in, fall_end) * VDD)
        if fall_in is not None else None,
        "rise_edge_J": abs(integrate(times, i_pwrd, rise_in, rise_end) * VDD)
        if rise_in is not None else None,
        "full_period_J": abs(integrate(times, i_pwrd) * VDD),
        "static_tail_J": abs(integrate(times, i_pwrd,
                                       DELAY_S + WIDTH_S + 0.5 * WIDTH_S, PERIOD_S) * VDD),
    }
    e_ld = {
        "fall_edge_J": abs(integrate(times, i_pwrl, fall_in, fall_end) * VDD)
        if fall_in is not None else None,
        "rise_edge_J": abs(integrate(times, i_pwrl, rise_in, rise_end) * VDD)
        if rise_in is not None else None,
        "full_period_J": abs(integrate(times, i_pwrl) * VDD),
        "static_tail_J": abs(integrate(times, i_pwrl,
                                       DELAY_S + WIDTH_S + 0.5 * WIDTH_S, PERIOD_S) * VDD),
    }
    # Per-device four-terminal charge closure over the full period. All four
    # terminals are sensed, so this sum is a real KCL check: a non-zero value
    # means charge is not conserved across the device terminals.
    nmos_closure = abs(sum(integrate(times, c) for c in (i_dn, i_sn, i_gn, i_en)))
    pmos_closure = abs(sum(integrate(times, c) for c in (i_dp, i_sp, i_gp, i_ep)))
    if not rails_ok:
        tphl = tplh = tfall = trise = None
        e_dut = {k: None for k in e_dut}
        e_ld = {k: None for k in e_ld}
    return {"rails_ok": rails_ok, "settled_low_V": lo, "settled_high_V": hi,
            "tphl_s": tphl, "tplh_s": tplh, "tfall_s": tfall, "trise_s": trise,
            "e_dut": e_dut, "e_ld": e_ld,
            "nmos_closure_C": nmos_closure, "pmos_closure_C": pmos_closure,
            "dut_static_A": (sum(i_pwrd[k] for k in range(len(times))
                                 if times[k] >= DELAY_S + WIDTH_S + 0.5 * WIDTH_S)
                             / max(sum(1 for t in times
                                       if t >= DELAY_S + WIDTH_S + 0.5 * WIDTH_S), 1)),
            "load_static_A": (sum(i_pwrl[k] for k in range(len(times))
                                  if times[k] >= DELAY_S + WIDTH_S + 0.5 * WIDTH_S)
                              / max(sum(1 for t in times
                                        if t >= DELAY_S + WIDTH_S + 0.5 * WIDTH_S), 1)),
            "steps": len(times)}


def ns(v):
    return "n/a" if v is None else "%.3f" % (v * 1e9)


def pj(v):
    return "n/a" if v is None else "%.2f" % (v * 1e12)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    args = parser.parse_args()
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")

    out = ["# Cross-model DUT/load timing experiment (VDD=3 V, 2 ns, real FO1)", "",
           "Simulator: `%s` (Xyce NF 7.10.0, BSIMSOI 4.5.0)." % xyce.XYCE,
           "DUT and load stages have SEPARATE supply branches at the same voltage, so",
           "the energies below are per stage, split into falling edge, rising edge,",
           "full period and static tail.", "",
           "`overlap` = the stage-1 DC card plus only the PDK p.9 measured CGSO/CGDO.",
           "It is the counterfactual that separates the overlap-charge contribution",
           "from the device-parameter contribution. The separation is an engineering",
           "attribution from controlled counterfactuals, not a strict linear",
           "decomposition.", ""]
    rows = {}
    with tempfile.TemporaryDirectory(prefix="icps-crossmodel-") as scratch:
        work = Path(scratch)
        texts = {}
        for tag, path in CARDS.items():
            if not path.is_file():
                raise SystemExit("missing card %s" % path)
            texts[tag] = xyce.port_card(path.read_text(encoding="utf-8"))
        for dut, load, note in ARMS:
            # When both roles use the same card, include it once: including the
            # same file twice redefines its (prefixed) models and Xyce rejects the
            # netlist, which silently dropped the three diagonal arms.
            distinct = []
            for tag in (dut, load):
                if tag not in distinct:
                    distinct.append(tag)
            for tag in distinct:
                (work / ("%s.inc" % tag)).write_text(
                    rename_models(texts[tag], tag), encoding="utf-8")
            deck = build_deck(dut, load)
            name = "cm_%s_%s" % (dut, load)
            result = xyce.run(deck, name, work, timeout=1800.0, budget_bytes=400_000_000)
            if result["status"] != "observed":
                rows[(dut, load)] = {"status": result["status"],
                                     "reason": result.get("reason"), "note": note}
                print("DUT=%-8s load=%-8s %s %s" % (dut, load, result["status"],
                                                    result.get("reason", "")), flush=True)
                continue
            times, cols = read_named(result["csv"])
            result["csv"].unlink(missing_ok=True)
            required = {"V(IN)", "V(OUT)", "I(VIN)", "I(VPWRD)", "I(VPWRL)",
                        "I(VSUB)", "I(VDN)", "I(VSN)", "I(VGN)", "I(VEN)",
                        "I(VDP)", "I(VSP)", "I(VGP)", "I(VEP)"}
            missing = sorted(required - set(cols))
            if missing:
                rows[(dut, load)] = {"status": "failed",
                                     "reason": "missing columns %s" % missing,
                                     "note": note}
                print("DUT=%-8s load=%-8s missing %s" % (dut, load, missing), flush=True)
                continue
            stats = analyse(times, cols)
            stats["status"] = "observed"
            stats["note"] = note
            rows[(dut, load)] = stats
            print("DUT=%-8s load=%-8s %s tPHL=%s tPLH=%s" % (dut, load, result["status"],
                                                            ns(stats["tphl_s"]),
                                                            ns(stats["tplh_s"])), flush=True)

    out += ["## Delays and level restoration", "",
            "| DUT | load | VOH (V) | VOL (V) | rails | tPHL (ns) | tPLH (ns) | "
            "tFALL (ns) | tRISE (ns) |",
            "|---|---|---:|---:|---|---:|---:|---:|---:|"]
    for dut, load, _note in ARMS:
        s = rows.get((dut, load), {})
        if s.get("status") != "observed":
            out.append("| %s | %s | | | %s | | | | |" % (dut, load, s.get("status")))
            continue
        out.append("| %s | %s | %.3f | %.3f | %s | %s | %s | %s | %s |"
                   % (dut, load, s["settled_high_V"], s["settled_low_V"],
                      "ok" if s["rails_ok"] else "**FAIL**",
                      ns(s["tphl_s"]), ns(s["tplh_s"]), ns(s["tfall_s"]), ns(s["trise_s"])))
    out.append("")

    out += ["## Energy attribution per stage", "",
            "| DUT | load | DUT E full (pJ) | DUT E fall (pJ) | DUT E rise (pJ) | "
            "DUT static (uW) | load E full (pJ) | load E fall (pJ) | load E rise (pJ) | "
            "load static (uW) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for dut, load, _note in ARMS:
        s = rows.get((dut, load), {})
        if s.get("status") != "observed":
            continue
        d, l = s["e_dut"], s["e_ld"]
        out.append("| %s | %s | %s | %s | %s | %.2f | %s | %s | %s | %.2f |"
                   % (dut, load, pj(d["full_period_J"]), pj(d["fall_edge_J"]),
                      pj(d["rise_edge_J"]), abs(s["dut_static_A"] * VDD) * 1e6,
                      pj(l["full_period_J"]), pj(l["fall_edge_J"]),
                      pj(l["rise_edge_J"]), abs(s["load_static_A"] * VDD) * 1e6))
    out.append("")

    out += ["## Per-device terminal charge closure (full period)", "",
            "| DUT | load | NMOS closure (aC) | PMOS closure (aC) | steps |",
            "|---|---|---:|---:|---:|"]
    for dut, load, _note in ARMS:
        s = rows.get((dut, load), {})
        if s.get("status") != "observed":
            continue
        out.append("| %s | %s | %.4f | %.4f | %d |"
                   % (dut, load, s["nmos_closure_C"] * 1e18,
                      s["pmos_closure_C"] * 1e18, s["steps"]))
    out.append("")

    out += ["## Attribution, read off this table and nothing else", "",
            "1. `fd/fd` versus `stage1/stage1` is the headline difference. It stays a",
            "   whole-model circuit difference.",
            "2. `fd/stage1` versus `stage1/stage1` isolates the **drive** change at an",
            "   unchanged load input capacitance.",
            "3. `stage1/fd` versus `stage1/stage1` isolates the **input-capacitance**",
            "   change at an unchanged drive.",
            "4. `overlap/stage1` versus `stage1/stage1` isolates the **overlap charge**",
            "   alone, holding the DC card and SOIMOD=0 fixed. If adding the measured",
            "   p.9 overlap already slows the cell substantially, then part of the",
            "   fd-card slowdown represents real parasitic burden that the stage-1",
            "   timing was implicitly understating, not a modelling regression.",
            "5. The symmetric-versus-asymmetric split matters: if tPHL and tPLH move",
            "   together, the dominant term is capacitive; if only one moves, the",
            "   dominant term is that device's drive.",
            "",
            "No PMOS RDSW/U0 refit should be attempted until this table says whether",
            "the residual is drive or capacitance. These numbers are experimental and",
            "version-tagged; they are not a signoff Liberty and no fMAX or yield claim",
            "follows from them.", ""]
    report = ROOT / "libs.tech/ngspice/audit/b4soi_crossmodel_timing.md"
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
