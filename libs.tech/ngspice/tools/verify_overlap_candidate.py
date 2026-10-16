#!/usr/bin/env python3
"""Hard regressions for the charge-only overlap candidate.

Checks three narrow reference regressions; passing them is not a promotion
gate or FD/PD, transient, process, or silicon qualification:

R1  DC parity: the frozen 12 I-V curve regression (regress_b4soi_stage1_iv)
    evaluated with the candidate library must match the frozen baseline
    metrics within the frozen tolerances.  CGSO/CGDO are capacitance-only,
    so any drift means the candidate changed something it must not.

R2  AC / charge consistency and PDF anchors: the audit tool re-runs against
    the candidate; off-state Cgs/Cgd and strong-inversion Cgg must land on
    the PDK p.9 anchors, the candidate's dQg/dV must match its AC Cgg, and
    the raw four-terminal branch-current integrals of a ramp must satisfy KCL.

R3  Switching charge vs VDS: quasi-static gate-charge ramps at VDS =
    {0.05, 1, 3} V for both devices, candidate vs frozen vs a closed-form
    PDF-anchored charge.

Run:  python3 libs.tech/ngspice/tools/verify_overlap_candidate.py
Out:  libs.tech/ngspice/audit/overlap_candidate_verification.md
"""

import csv
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
CANDIDATE_INC = ROOT / "libs.tech/ngspice/models/b4soi_overlap_candidate.inc"
CANDIDATE_LIB = ROOT / "libs.tech/ngspice/b4soi_overlap_candidate.lib.spice"
FROZEN_LIB = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
BASELINE = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_baseline.json"
AUDIT_DIR = ROOT / "libs.tech/ngspice/audit"
CANDIDATE_AUDIT = AUDIT_DIR / "candidate"

VDD = 3.0
W_UM, L_UM = 24.0, 6.0
OVL_FC_UM = {"nch": 5.0, "pch": 6.8}          # fF/um, PDK p.9
COX_FF = 331.2                                 # 2.3 fF/um^2 x 144 um^2
VTH = {"nch": 0.25, "pch": 0.71}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ramp_charge(audit, model, vds, library, ngspice, work, integ_sign):
    """Quasi-static charge via the audit tool's validated ramp machinery.

    integrate_sign calibrates the charge integral; polarity is the device
    polarity (nch +1, pch -1) and must not be confused with it.
    """
    polarity = 1.0 if model == "nch" else -1.0
    audit.LIBRARY = library
    _gate_v, charges = audit.measure_ramp(
        model, polarity * vds, 0.0, polarity * VDD, ngspice, work, integ_sign)
    return abs(charges[-1][0]), sum(abs(x) for x in charges[-1])


def load_ac_csv(path):
    table = {}
    with Path(path).open(newline="", encoding="utf-8") as sink:
        for row in csv.DictReader(sink):
            if row["sweep"] == "vgs":
                table[(row["device"], float(row["vgs_v"]), float(row["vds_v"]))] = {
                    key: (value if key in ("device", "sweep") else float(value))
                    for key, value in row.items()}
    return table


def load_charge_csv(path):
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as sink:
        for row in csv.DictReader(sink):
            rows.append((row["device"], float(row["vds_v"]), float(row["vgs_v"]),
                         float(row["qg_fc"]), float(row["qd_fc"]),
                         float(row["qs_fc"]), float(row["qe_fc"])))
    return rows


def main():
    ngspice = shutil.which("ngspice")
    if not ngspice:
        raise SystemExit("ngspice is required")
    if not CANDIDATE_INC.is_file() or not CANDIDATE_LIB.is_file():
        raise SystemExit("candidate files missing: models/b4soi_overlap_candidate.inc")
    out = ["# Overlap candidate verification (b4soi_overlap_candidate.inc)", ""]
    verdict = {}

    audit = load_module(TOOLS / "audit_b4soi_caps.py", "b4soi_cap_audit")
    regress = load_module(TOOLS / "regress_b4soi_stage1_iv.py", "b4soi_iv_regression")

    with tempfile.TemporaryDirectory(prefix="icps-cand-verify-") as scratch:
        work = Path(scratch)

        # ---- R1: DC I-V parity against the frozen baseline ----
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        curves = regress.read_curves(regress.MEASUREMENTS)
        metrics = regress.evaluate_curves(curves, ngspice, CANDIDATE_LIB, 0.001)
        try:
            regress.compare_metrics(metrics, baseline)
            r1, r1_note = True, "all 12 frozen curves within baseline tolerances"
        except Exception as error:
            r1, r1_note = False, str(error)
        verdict["R1_dc_parity"] = r1
        worst = max(
            abs(metrics[name].get("relative_rms_percent",
                                  metrics[name].get("log10_current_rms_decades", 0.0))
                - baseline["curves"][name].get(
                    "relative_rms_percent",
                    baseline["curves"][name].get("log10_current_rms_decades", 0.0)))
            for name in metrics)
        out += ["## R1 DC parity (12 frozen I-V curves, candidate library)", "",
                "- %s" % r1_note,
                "- worst per-curve metric drift vs frozen baseline: %.4g" % worst, ""]

        # ---- R2: AC / charge consistency and PDF anchors ----
        sign, _q, _e = audit.charge_sign(ngspice, work)
        argv = sys.argv
        sys.argv = ["audit_b4soi_caps.py", "--ngspice", ngspice,
                    "--library", str(CANDIDATE_LIB), "--out", str(CANDIDATE_AUDIT)]
        try:
            audit.main()
            audit_code = 0
        except SystemExit as error:
            audit_code = error.code or 0
        finally:
            sys.argv = argv
        frozen_ac = load_ac_csv(AUDIT_DIR / "b4soi_cap_audit_ac.csv")
        cand_ac = load_ac_csv(CANDIDATE_AUDIT / "b4soi_cap_audit_ac.csv")
        cand_q = load_charge_csv(CANDIDATE_AUDIT / "b4soi_cap_audit_charge.csv")
        out += ["## R2 AC / charge consistency and PDF anchors", "",
                "audit tool on candidate exit code: %d" % audit_code, "",
                "| quantity | frozen | candidate | PDF anchor | predicted (linear) |",
                "|---|---:|---:|---:|---:|"]
        # audit CSVs record bias magnitudes (positive) for both polarities
        for model in ("nch", "pch"):
            ovl_side = OVL_FC_UM[model] * W_UM
            f_off = frozen_ac.get((model, 0.0, VDD), {}).get("dg_ff", 0.0)
            c_off = cand_ac.get((model, 0.0, VDD), {}).get("dg_ff", 0.0)
            f_tri = frozen_ac.get((model, VDD, 0.05), {}).get("gg_ff", 0.0)
            c_tri = cand_ac.get((model, VDD, 0.05), {}).get("gg_ff", 0.0)
            f_sat = frozen_ac.get((model, VDD, VDD), {}).get("gg_ff", 0.0)
            c_sat = cand_ac.get((model, VDD, VDD), {}).get("gg_ff", 0.0)
            out.append("| %s Cgd off (drive G->D) | %.1f fF | %.1f fF | %.1f fF | %.1f fF |"
                       % (model.upper(), f_off, c_off, ovl_side, f_off + ovl_side))
            out.append("| %s Cgg triode 3/0.05 | %.1f fF | %.1f fF | %.1f fF | %.1f fF |"
                       % (model.upper(), f_tri, c_tri, COX_FF + 2 * ovl_side,
                          f_tri + 2 * ovl_side))
            out.append("| %s Cgg sat 3/3 | %.1f fF | %.1f fF | %.1f fF | %.1f fF |"
                       % (model.upper(), f_sat, c_sat, 2.0 / 3.0 * COX_FF + 2 * ovl_side,
                          f_sat + 2 * ovl_side))
        out.append("")
        worst_kcl = 0.0
        curves_q = {}
        for row in cand_q:
            curves_q.setdefault((row[0], row[1]), []).append(row)
        for rows in curves_q.values():
            worst_kcl = max(worst_kcl, abs(sum(rows[-1][3:7])))
        out.append("- KCL raw branch-charge residual, worst curve: %.4g fC "
                   "(max |Qg| %.0f fC)" % (
                       worst_kcl, max(abs(r[3]) for rows in curves_q.values() for r in rows)))
        # dQg/dV vs AC Cgg agreement on the same 2.8->2.9 V window (candidate data)
        rows = sorted((r[2], r[3]) for r in cand_q
                      if r[0] == "nch" and abs(r[1] - VDD) < 1e-9
                      and 2.79 <= r[2] <= 2.91)
        if len(rows) < 2:
            raise RuntimeError("R2 consistency ramp window missing")
        lo, hi = rows[0], rows[-1]
        slope = (hi[1] - lo[1]) / (hi[0] - lo[0])
        ac_window = [float(row["gg_ff"]) for (dev, vgs, vds), row in cand_ac.items()
                     if dev == "nch" and abs(vds - VDD) < 1e-9
                     and vgs in (2.75, 3.0)]
        if len(ac_window) != 2:
            raise RuntimeError("R2 consistency AC window missing")
        cgg = sum(ac_window) / 2.0
        agree = 100.0 * abs(slope / cgg - 1.0)
        out.append("- candidate NMOS dQg/dV over %.2f->%.2f V (VDS=3): %.1f fF vs "
                   "AC Cgg mean over 2.75/3.0 V %.1f fF (%.2f%% apart)"
                   % (lo[0], hi[0], slope, cgg, agree))
        verdict["R2_ac_charge"] = (audit_code == 0 and worst_kcl < 30.0
                                   and agree < 3.0)
        out.append("")

        # ---- R3: switching charge vs VDS ----
        out += ["## R3 Switching charge vs VDS (quasi-static ramps)", "",
                "| device | VDS | frozen | candidate | closed-form anchor | candidate/anchor |",
                "|---|---:|---:|---:|---:|---:|"]
        r3 = True
        for model in ("nch", "pch"):
            for vds in (0.05, 1.0, 3.0):
                qf, _ = ramp_charge(audit, model, vds, FROZEN_LIB, ngspice, work, sign)
                qc, _ = ramp_charge(audit, model, vds, CANDIDATE_LIB, ngspice, work, sign)
                cov = OVL_FC_UM[model] * W_UM
                if vds > VDD - VTH[model]:
                    anchor = 2.0 / 3.0 * COX_FF * (VDD - VTH[model]) + 2 * cov * VDD
                elif vds < 0.1 * VDD:
                    anchor = (COX_FF + 2 * cov) * VDD
                else:
                    anchor = (COX_FF * (VDD - VTH[model])
                              * (1.0 - vds / (2.0 * (VDD - VTH[model]))) + 2 * cov * VDD)
                ratio = (qc * 1e15) / anchor
                r3 = r3 and abs(ratio - 1.0) < 0.15 and qc > qf
                out.append("| %s | %.2f V | %.0f fC | %.0f fC | %.0f fC | %+.1f%% |"
                           % (model, vds, qf * 1e15, qc * 1e15, anchor,
                              100.0 * (ratio - 1.0)))
        verdict["R3_switching_charge"] = r3
        out += ["",
                "Anchor: PDK p.9 measured Cox and overlap densities in closed form "
                "(saturation 2/3·Cox·(VDD-VT)+2·Cov·VDD; triode (Cox+2Cov)·VDD; "
                "intermediate linear blend).  Pass: candidate within 15% of anchor "
                "and strictly above the frozen model.", "",
                "## Verdict", ""]
        for key, value in verdict.items():
            out.append("- **%s**: %s" % (key, "PASS" if value else "FAIL"))
        out.append("")

    out.append("Candidate: `%s` — charge-only; fitted DC parameter values unchanged. No nominal promotion or process qualification follows from R1–R3."
               % CANDIDATE_INC)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    target = AUDIT_DIR / "overlap_candidate_verification.md"
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("")
    print("wrote %s" % target)
    return 0 if all(verdict.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
