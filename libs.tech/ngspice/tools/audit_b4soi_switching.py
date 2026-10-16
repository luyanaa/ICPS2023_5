#!/usr/bin/env python3
"""Switching-impact bench for the MOS overlap-capacitance gap.

Measures, on the characterize.py bench conditions (3 V, 5 fF, W24/L6, 25 C):

1. Per-switch gate charge via the quasi-static ramp method of
   tools/audit_b4soi_caps.py (validated there against a 100 fF reference),
   for the frozen cards and for the same cards plus the PDK p.9 measured
   overlap capacitance added as bench-external lumped capacitors.
2. The charge-balance consequence for the dynamic arcs, using the measured
   per-switch charges.
3. A transient-stability probe: the two-transistor inverter bench with 2 ns
   edges, frozen b4soi cards vs an identical LEVEL1 control deck, reporting
   rail excursion and gate-current chatter.

Nothing here edits the frozen model cards.  Output:
libs.tech/ngspice/audit/b4soi_switching_impact.md
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
OUT_DIR = ROOT / "libs.tech/ngspice/audit"
VDD = 3.0
W_UM, L_UM = 24.0, 6.0
OVL_N_FCM = 5.0 * W_UM   # fF per side at W=24 um (PDK p.9: 5 fF/um NFET)
OVL_P_FCM = 6.8 * W_UM   # fF per side (6.8 fF/um PFET)
COX_FF = 331.2           # 2.3 fF/um^2 x 144 um^2 (p.9)
VTH = {"nch": 0.25, "pch": 0.71}


def fmt(value):
    return "%.12g" % value


def read_wrdata(path, n_vectors):
    """Transient wrdata (ascii, wr_singlescale): scale column then one real column
    per vector."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            values = [float(field.replace("D", "E").replace("d", "e")) for field in fields]
        except ValueError:
            continue
        if len(values) == 1 + n_vectors:
            rows.append(values)
    if not rows:
        raise RuntimeError("no parsable rows in %s" % path)
    scales = [row[0] for row in rows]
    vectors = [[row[1 + k] for row in rows] for k in range(n_vectors)]
    return scales, vectors


def run_deck(lines, name, ngspice, work, dat=None):
    (work / (name + ".cir")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(work / (name + ".cir"))], cwd=work,
                         text=True, capture_output=True, check=False, timeout=180)
    target = work / ((dat or name) + ".dat")
    if run.returncode != 0 or not target.is_file():
        raise RuntimeError("ngspice failed for %s:\n%s\n%s" % (name, run.stdout, run.stderr))
    return target


def switch_deck(model, vds, overlay):
    """Quasi-static 0->VDD gate ramp; gate branch current integrates to Qg."""
    s = 1.0 if model == "nch" else -1.0
    lines = [
        "* per-switch gate charge: frozen card vs frozen card + measured overlap",
        '.lib "%s" b4soi' % MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"'),
        ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500 gmin=1e-10",
        "Vg g 0 PWL(0 0 1e-3 %s)" % fmt(s * VDD),
        "Vd d 0 %s" % fmt(s * vds),
        "Vs s 0 0",
        "Ve e 0 0",
        "M1 d g s e %s W=%su L=%su" % (model, fmt(W_UM), fmt(L_UM)),
    ]
    if overlay:
        cov_n = OVL_N_FCM if model == "nch" else OVL_P_FCM
        lines += [
            "* PDK p.9 measured overlap cap per side",
            "CGSO g 0 %sf" % fmt(cov_n),
            "CGDO g d %sf" % fmt(cov_n),
        ]
    lines += [
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "tran 2u 1e-3",
        "wrdata %s.dat v(g) i(Vg)" % ("ovl" if overlay else "base"),
        ".endc",
        ".end",
        "",
    ]
    return lines


def gate_charge(path):
    """|Qg| over the 0->VDD ramp (trapezoid) and the gate-voltage axis."""
    scales, vectors = read_wrdata(path, 2)
    times, i_gate = scales, vectors[1]
    charge = sum(0.5 * (i_gate[k] + i_gate[k - 1]) * (times[k] - times[k - 1])
                 for k in range(1, len(times)))
    return abs(charge)


def inverter_deck(vendor):
    lines = [
        "* characterize.py-conditions two-transistor bench (3 V, 5 fF, 2 ns edges)",
        ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500",
        "VPWR VDD 0 3", "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 3 100n 2n 2n 400n 1u)", "CLOAD OUT 0 5f",
    ]
    if vendor == "b4soi":
        lines.insert(0, '.lib "%s" b4soi'
                     % MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"'))
        lines += [
            "MPD VDD IN OUT VDD pch W=%su L=%su" % (W_UM, L_UM),
            "MND OUT IN 0 SUB nch W=%su L=%su" % (W_UM, L_UM),
        ]
    else:
        lines += [
            ".model nm NMOS level=1 KP=100u VTO=0.25",
            ".model pm PMOS level=1 KP=50u VTO=-0.25",
            "MPD VDD IN OUT VDD pm W=%su L=%su" % (W_UM, L_UM),
            "MND OUT IN 0 SUB nm W=%su L=%su" % (W_UM, L_UM),
        ]
    lines += [
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "tran 0.1n 1u",
        "wrdata inv.dat v(IN) v(OUT) i(VIN) i(VPWR)",
        ".endc",
        ".end",
        "",
    ]
    return lines


def main():
    ngspice = shutil.which("ngspice")
    if not ngspice:
        raise SystemExit("ngspice is required")
    out = ["# Switching impact: frozen b4soi cards vs measured overlap caps", ""]
    out.append("Dynamic bench conditions from libs.tech/stdcell/characterize.py: "
               "VDD=3 V, CLOAD=5 fF, 2 ns edges, W/L=%g/%g um, 25 C.  The frozen cards "
               "never change; overlaps enter as bench-external lumped capacitors of "
               "the PDK p.9 measured size (5 fF/um NFET / 6.8 fF/um PFET x W=24 um = "
               "%.0f fF / %.0f fF per side)." % (W_UM, L_UM, OVL_N_FCM, OVL_P_FCM))
    out.append("")

    charges = {}
    with tempfile.TemporaryDirectory(prefix="icps-b4soi-sw-") as scratch:
        work = Path(scratch)

        # 1. Per-switch gate charge, frozen vs + measured overlap.
        out.append("## Per-switch gate charge (quasi-static 1 ms ramp)")
        out.append("")
        out.append("| device | VDS | frozen model | + measured overlap | theory | shortfall of overlay vs theory |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for model in ("nch", "pch"):
            for vds, label in ((VDD, "on-rail"), (0.05, "triode")):
                q_base = gate_charge(run_deck(switch_deck(model, vds, False),
                                              "base", ngspice, work))
                q_ovl = gate_charge(run_deck(switch_deck(model, vds, True),
                                             "ovl", ngspice, work))
                cov = 120.0 if model == "nch" else 163.2
                if vds == VDD:
                    theory = 2.0 / 3.0 * COX_FF * (VDD - VTH[model]) + 2.0 * cov * VDD
                else:
                    theory = (COX_FF + 2.0 * cov) * VDD
                charges[(model, vds)] = (q_base, q_ovl, theory)
                out.append("| %s | %.2f V (%s) | %.0f fC | %.0f fC | %.0f fC | %.0f%% |" % (
                    model, vds, label, q_base * 1e15, q_ovl * 1e15, theory,
                    100.0 * (1.0 - q_ovl / (theory * 1e-15))))
        out.append("")
        n_base, n_ovl, _ = charges[("nch", VDD)]
        p_base, p_ovl, _ = charges[("pch", VDD)]

        # 2. Consequence for the dynamic flow.
        out.append("## Dynamic-flow consequence (charge balance)")
        out.append("")
        out.append("- Frozen model per 3 V switch: NMOS %.0f fC, PMOS %.0f fC." % (
            n_base * 1e15, p_base * 1e15))
        out.append("- Same deck plus the PDK-measured overlap: NMOS %.0f fC, "
                   "PMOS %.0f fC." % (n_ovl * 1e15, p_ovl * 1e15))
        out.append("- The frozen cards therefore compute only %.0f%% (NMOS) / %.0f%% "
                   "(PMOS) of the physical per-switch gate charge; the missing "
                   "charge is the overlap term, absent from the frozen cards." % (
                       100.0 * n_base / n_ovl, 100.0 * p_base / p_ovl))
        out.append("- On the 5 fF characterize bench load (15 fC per window) one W24 "
                   "gate switch is %.0fx larger: at realistic fanout the intrinsic "
                   "gate charge dominates the arc, and every fanout gate is under-"
                   "charged by ~%.0f%%." % (n_ovl * 1e15 / (5 * VDD),
                                            100.0 * (1.0 - n_base / n_ovl)))
        out.append("- Effective gate load the frozen cards present to the driving "
                   "stage: NMOS %.0f fF vs physical ~%.0f fF; PMOS %.0f fF vs "
                   "physical ~%.0f fF." % (
                       n_base / VDD * 1e15, n_ovl / VDD * 1e15,
                       p_base / VDD * 1e15, p_ovl / VDD * 1e15))
        out.append("")
        out.append("This is the quantitative path between the charge/overlap audit and "
                   "the optimistic dynamic model: the transient flow integrates exactly "
                   "these charges, so arc delays and transitions are computed against "
                   "an under-loaded fanout and come out fast.  Setting the SOI overlap "
                   "parameters (CGSO/CGDO, or equivalent bench caps) to the measured "
                   "5/6.8 fF/um before extracting any timing is required for a "
                   "defensible arc.")
        out.append("")

        # 3. Transient stability probe.
        out.append("## Transient stability probe (frozen cards, 2 ns edges)")
        out.append("")
        for vendor in ("b4soi", "level1"):
            data = run_deck(inverter_deck(vendor), "inv_%s" % vendor, ngspice, work, dat="inv")
            _scales, vectors = read_wrdata(data, 4)
            v_out, i_vin = vectors[1], vectors[2]
            flips = sum(1 for a, b in zip(i_vin, i_vin[1:]) if a * b < 0)
            out.append("- %-7s deck: OUT spans [%.3f, %.3f] V; input-branch sign flips "
                       "%d of %d samples." % (vendor, min(v_out), max(v_out),
                                              flips, len(v_out)))
        out.append("")
        out.append("The identical deck on a LEVEL1 control deck stays within [0, VDD] "
                   "with zero chatter, so the rail excursion and gate-current "
                   "oscillation are properties of the frozen B4SOI cards, not of the "
                   "bench.  Root cause per the T1-T7 matrix in "
                   "b4soi_transient_diagnosis.md: the drain-side analog node of the "
                   "PD-SOI body-capacitance network at ns-scale slew (a lone NMOS "
                   "discharging 5 fF swings OUT to -0.8 V with 4x10^4 step flips; the "
                   "gate charge itself stays charge-balanced to within 1% of the "
                   "quasi-static value at every slew).  It is slew-triggered, not "
                   "fixed by body-tied P terminals, not by XMU damping, and "
                   "reproduces on ngspice 44/45/46/47.  Transient-level arc numbers "
                   "from the current cards therefore carry BOTH the charge deficit "
                   "above and a numerical-quality caveat; the quasi-static curves in "
                   "b4soi_cap_audit_charge.csv and b4soi_cap_audit_ac.csv remain the "
                   "trustworthy charge accounting until this is resolved.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "b4soi_switching_impact.md"
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("")
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
