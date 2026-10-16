#!/usr/bin/env python3
"""Probe signed B4SOI terminal charges and source/drain symmetry.

The mirror experiment exchanges the absolute D/S voltage values while keeping
one physical device wiring unchanged. It compares signed QD/QS/QB and QG
reconstructed from charge conservation, plus the signed public Cij fields.
This is a diagnostic only; it does not modify or promote a model card.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
AUDIT_DIR = ROOT / "libs.tech/ngspice/audit"
W_UM, L_UM = 24.0, 6.0
QUERY_RE = re.compile(
    r"@m1\[(?P<name>qd|qs|qb)\]\s*=\s*(?P<value>[-+0-9.eEdD]+)"
)
C_KEYS = ("cgg", "cgd", "cgs", "cdg", "cdd", "cds", "cbg", "cbd", "cbs")


def fmt(value: float) -> str:
    return "%.12g" % value


def op_deck(vg: float, vd: float, vs: float) -> str:
    return "\n".join([
        ".options temp=25 tnom=25",
        '.lib "%s" b4soi' % LIBRARY.resolve().as_posix().replace('"', '\\"'),
        "VG g 0 %s" % fmt(vg), "VD d 0 %s" % fmt(vd),
        "VS s 0 %s" % fmt(vs), "VE e 0 0",
        "M1 d g s e nch W=%su L=%su" % (W_UM, L_UM),
        ".control", "op", "print @m1[qd] @m1[qs] @m1[qb]",
        "quit", ".endc", ".end", "",
    ])


def ac_deck(vg: float, vd: float, vs: float) -> str:
    # Public Cij state is computed at op and then exported after AC. The
    # `DC ... AC 0` syntax is required; without a DC value ngspice treats a
    # source with AC data as 0 V and the intended bias point is lost.
    return "\n".join([
        ".options temp=25 tnom=25",
        '.lib "%s" b4soi' % LIBRARY.resolve().as_posix().replace('"', '\\"'),
        "VG g 0 DC %s AC 1" % fmt(vg),
        "VD d 0 DC %s AC 0" % fmt(vd),
        "VS s 0 DC %s AC 0" % fmt(vs),
        "VE e 0 DC 0 AC 0",
        "M1 d g s e nch W=%su L=%su" % (W_UM, L_UM),
        ".control", "set filetype=ascii", "set wr_singlescale",
        "op", "ac lin 1 1000 1000",
        "wrdata ac.dat " + " ".join("@m1[%s]" % key for key in C_KEYS),
        "quit", ".endc", ".end", "",
    ])


def run(deck: str, work: Path, name: str, ngspice: str) -> str:
    cir = work / (name + ".cir")
    cir.write_text(deck, encoding="utf-8")
    result = subprocess.run([ngspice, "-b", str(cir)], cwd=work,
                            text=True, capture_output=True, check=False,
                            timeout=180)
    if result.returncode != 0:
        raise RuntimeError("ngspice failed for %s:\n%s" %
                           (name, result.stdout + result.stderr))
    return result.stdout + result.stderr


def parse_charge(text: str) -> dict[str, float]:
    values = {m.group("name"): float(m.group("value").replace("D", "E").replace("d", "e"))
              for m in QUERY_RE.finditer(text)}
    if set(values) != {"qd", "qs", "qb"}:
        raise RuntimeError("missing charge query in ngspice output")
    values["qg"] = -(values["qd"] + values["qs"] + values["qb"])
    return values


def parse_cap(path: Path) -> dict[str, float]:
    fields = path.read_text(encoding="utf-8").split()
    if len(fields) != 1 + len(C_KEYS):
        raise RuntimeError("unexpected AC query row: %s" % path)
    return {key: float(fields[index + 1].replace("D", "E").replace("d", "e"))
            for index, key in enumerate(C_KEYS)}


def pct(a: float, b: float, floor: float = 1e-18) -> float:
    return 100.0 * abs(a - b) / max(abs(a), abs(b), floor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"))
    parser.add_argument("--out", default=str(AUDIT_DIR / "b4soi_charge_symmetry.md"))
    args = parser.parse_args()
    if not args.ngspice:
        parser.error("ngspice is required")
    if not LIBRARY.is_file():
        parser.error("model library missing: %s" % LIBRARY)

    points = [(0.0, -0.2, 0.0), (0.0, 0.2, 0.0),
              (1.5, -0.2, 0.0), (1.5, 0.2, 0.0),
              (1.5, 3.0, 0.0)]
    report = [
        "# B4SOI full source/drain charge symmetry", "",
        "Model: `icps2023_5.lib.spice`, LEVEL=58, W=24 µm, L=6 µm, 25 °C.",
        "Deck B keeps the same D/G/S/E wiring and swaps only the absolute D/S voltage values.",
        "Expected mirror: `(Qg,Qd,Qs,Qb)B = (Qg,Qs,Qd,Qb)A`; for the available Cij fields, `C_B = P C_A P^T`.",
        "", "`ngspice-47` exposes QD, QS, and QB but not QG/QE through the instance-query interface. QG is reconstructed from the model charge-conservation relation; QE is not independently observable in this build.", "",
        "| VG | VD(A) | QG A (fC) | QD A | QS A | QB A | QG B | QD B | QS B | QB B | max charge deviation |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    cap_rows = []
    with tempfile.TemporaryDirectory(prefix="icps-b4soi-sym-") as temp:
        work = Path(temp)
        for index, (vg, vd, vs) in enumerate(points):
            a = parse_charge(run(op_deck(vg, vd, vs), work, "op_a_%d" % index, args.ngspice))
            b = parse_charge(run(op_deck(vg, vs, vd), work, "op_b_%d" % index, args.ngspice))
            expected = {"qg": a["qg"], "qd": a["qs"], "qs": a["qd"], "qb": a["qb"]}
            deviations = [pct(b[key], expected[key]) for key in expected]
            report.append("| %g | %+g | %.6g | %.6g | %.6g | %.6g | %.6g | %.6g | %.6g | %.6g | %.3g%% |" % (
                vg, vd, a["qg"] * 1e15, a["qd"] * 1e15, a["qs"] * 1e15, a["qb"] * 1e15,
                b["qg"] * 1e15, b["qd"] * 1e15, b["qs"] * 1e15, b["qb"] * 1e15, max(deviations)))
            run(ac_deck(vg, vd, vs), work, "ac_a_%d" % index, args.ngspice)
            cap_a = parse_cap(work / "ac.dat")
            run(ac_deck(vg, vs, vd), work, "ac_b_%d" % index, args.ngspice)
            cap_b = parse_cap(work / "ac.dat")
            expected_cap = {"cgg": cap_a["cgg"], "cgd": cap_a["cgs"], "cgs": cap_a["cgd"],
                            "cdg": cap_a["cds"], "cdd": cap_a["cdd"], "cds": cap_a["cdg"],
                            "cbg": cap_a["cbs"], "cbd": cap_a["cbs"], "cbs": cap_a["cbd"]}
            cap_deviations = {key: pct(cap_b[key], expected_cap[key]) for key in C_KEYS}
            cap_rows.append((vg, vd, cap_deviations))

    report += ["", "## Public capacitance/Jacobian mirror check", "",
               "The Cij values are signed public model quantities. Only the nine fields available in ngspice-47 are compared; missing substrate-column fields are not inferred.",
               "", "| VG | VD(A) | max signed Cij mirror deviation | largest field |", "|---:|---:|---:|---|"]
    for vg, vd, deviations in cap_rows:
        key = max(deviations, key=deviations.get)
        report.append("| %g | %+g | %.3g%% | %s |" % (vg, vd, deviations[key], key))
    report += ["", "## Interpretation", "",
               "- The charge test uses signed QD/QS/QB values and the correct absolute-voltage D/S swap; it is not the invalid comparison of `Cgg(VD=-0.2)` against `Cgg(VD=+0.2)` with source held fixed.",
               "- Non-reciprocal Cgd/Cdg and Cgs/Cds are allowed. The test checks the complete D/S permutation instead of element-wise reciprocity.",
               "- The missing independent QE query limits the charge-vector test to the exposed B4SOI quantities plus conservation-derived QG; it does not prove hidden substrate/body derivatives.",
               "- This report does not establish dynamic stability; F2 remains a separate transient/timestep experiment.", ""]
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
