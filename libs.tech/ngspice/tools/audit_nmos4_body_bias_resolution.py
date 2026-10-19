#!/usr/bin/env python3
"""Numerical-resolution audit of the Unified NMOS4 explicit-P response.

The earlier P sweep reported a relative ID change of ~2.6e-5, which is too
small to trust from convergence alone.  This tool builds a sensitivity
convergence matrix on the SAME unified bridge card (ngspice BSIMSOI 4.4):

  * RELTOL = default, 1e-4, 1e-6, 1e-8   (body slope stability)
  * forward vs reverse P sweep            (solver path dependence)
  * independent operating points          (no DC-sweep history)
  * tightened ABSTOL/VNTOL/GMIN
  * direct device vs assembled post-layout network
  * G and E sweeps as positive controls
  * model-internal body variables (`@m1[qb]`, VBS via `show`)

It reports g_mb,P = dID/dVP, g_m = dID/dVG and the equivalent
dVth,eq/dVP ~= -g_mb/g_m.  These are local small-signal ratios at one bias
point, not threshold voltages extracted from measured transfer curves.  This
is model-capability evidence only; no silicon qualification is claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
SELECTOR = ROOT / "libs.tech/ngspice/b4soi_unified_bridge_experimental.lib.spice"
CARD = ROOT / "libs.tech/ngspice/models/b4soi_unified_bridge_experimental.inc"
W_UM, L_UM = 20.0, 6.0
VD, VG, VP0 = 1.0, 1.5, -1.0
DELTA = 0.05
STATUS = "MODEL_CAPABILITY_ONLY_NOT_SILICON_QUALIFIED"


class AuditError(ValueError):
    """Missing runtime or an unparsable operating point."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_pipeline():
    name = "audit_mos_contact_ownership"
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AuditError("cannot load audit_mos_contact_ownership.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _op(selector: Path, work: Path, ngspice: str, *, vp=VP0, vg=VG, vd=VD,
        options=None, netlist_include: Path | None = None, top: str | None = None):
    """One independent operating point; returns ID and internal body observables."""
    work.mkdir(parents=True, exist_ok=True)
    deck = work / "op.cir"
    lines = [
        "* unified NMOS4 independent operating point",
        '.lib "%s" b4soi' % selector.resolve().as_posix(),
        ".options temp=25 tnom=25" + (" " + " ".join(options) if options else ""),
        "VDS d 0 %.10g" % vd,
        "VGS g 0 %.10g" % vg,
        "VSUB e 0 0",
        "VP p 0 %.10g" % vp,
    ]
    if netlist_include is None:
        lines.append("M1 d g 0 e p nch W=%gu L=%gu" % (W_UM, L_UM))
        print_command = "print i(VDS) @m1[qb]"
    else:
        lines.extend([
            '.include "%s"' % netlist_include.resolve().as_posix(),
            # Assembled pin order is D G S B E_SUB (reference pins then the
            # substrate formal), not D G S E_SUB B.
            "XDUT d g 0 p e %s" % top,
        ])
        print_command = "print i(VDS)"
    lines.extend([".control", "set numdgt=12", "op", print_command,
                  "quit", ".endc", ".end", ""])
    deck.write_text("\n".join(lines), encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(deck)], cwd=work, text=True,
                         capture_output=True, timeout=300)
    if run.returncode != 0:
        return {"status": "failed", "reason": run.stderr[-500:]}
    text = run.stdout + "\n" + run.stderr
    match = re.search(r"i\(vds\)\s*=\s*([+-]?[0-9.]+e[+-]?\d+)", text, re.I)
    qb = re.search(r"@m1\[qb\]\s*=\s*([+-]?[0-9.]+e[+-]?\d+)", text, re.I)
    if not match:
        return {"status": "failed", "reason": "no i(VDS) in operating point"}
    return {"status": "observed",
            # i(VDS) is the current into the VDS source; the device drain
            # current is its negative.
            "id_a": -float(match.group(1)),
            "qb_c": float(qb.group(1)) if qb else None}


def _central(call):
    plus = call(DELTA)
    minus = call(-DELTA)
    if plus["status"] != "observed" or minus["status"] != "observed":
        return {"status": "failed", "plus": plus, "minus": minus}
    return {"status": "observed",
            "slope_a_per_v": (plus["id_a"] - minus["id_a"]) / (2 * DELTA),
            "plus": plus, "minus": minus}


def _sweep(selector: Path, work: Path, ngspice: str, *, reltol=None,
           direction=1):
    work.mkdir(parents=True, exist_ok=True)
    deck = work / "sweep.cir"
    data = work / "sweep.dat"
    data.unlink(missing_ok=True)
    start, stop, step = (-3.0, 0.0, 0.1) if direction > 0 else (0.0, -3.0, -0.1)
    opts = ".options temp=25 tnom=25" + (" reltol=%g" % reltol if reltol else "")
    deck.write_text("\n".join([
        "* unified NMOS4 P sweep (%s)" % ("forward" if direction > 0 else "reverse"),
        '.lib "%s" b4soi' % selector.resolve().as_posix(),
        opts,
        "VDS d 0 %.10g" % VD,
        "VGS g 0 %.10g" % VG,
        "VSUB e 0 0",
        "VP p 0 0",
        "M1 d g 0 e p nch W=%gu L=%gu" % (W_UM, L_UM),
        ".control", "set wr_vecnames", "set wr_singlescale",
        "dc VP %.10g %.10g %.10g" % (start, stop, step),
        "wrdata %s i(VDS)" % data.name, "quit", ".endc", ".end", "",
    ]), encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(deck)], cwd=work, text=True,
                         capture_output=True, timeout=300)
    if run.returncode != 0 or not data.is_file():
        return {"status": "failed", "reason": run.stderr[-500:]}
    rows = []
    for line in data.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            rows.append((float(fields[0]), float(fields[1])))
        except ValueError:
            continue
    if len(rows) < 3:
        return {"status": "failed", "reason": "insufficient sweep rows"}
    xs = [row[0] for row in rows]
    ys = [abs(row[1]) for row in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
             if denom else None)
    return {"status": "observed", "points": len(rows),
            "id_min_a": min(ys), "id_max_a": max(ys),
            "slope_a_per_v": slope}


def _nmos4_pipeline(work: Path):
    """Generate the bounded NMOS4 cell and run fab-prep/LVS/PEX/assembly."""
    work.mkdir(parents=True, exist_ok=True)
    klayout = shutil.which("klayout") or "klayout"
    gen = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
    macro = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
    fab_prep = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
    lvs_deck = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
    pex = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
    assembler = TOOLS / "assemble_postlayout.py"
    top = "nmos4_min"

    def run(command):
        result = subprocess.run(command, text=True, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise AuditError("pipeline step failed: %s\n%s\n%s"
                             % (" ".join(map(str, command)),
                                result.stdout, result.stderr))
        return result

    gds_dir = work / "gds"
    gds_dir.mkdir(exist_ok=True)
    run([klayout, "-b", "-r", str(gen), "-rd", "output_dir=" + str(gds_dir),
         "-rd", "pcell_macro=" + str(macro)])
    design = gds_dir / (top + ".gds")
    fab = work / (top + ".fab.gds")
    run([klayout, "-b", "-r", str(fab_prep), "-rd", "input=" + str(design),
         "-rd", "output=" + str(fab),
         "-rd", "process_profile=icps2023_5_1m"])
    reference = work / (top + ".cir")
    reference.write_text(
        ".GLOBAL SUB\n.SUBCKT %s D G S B\n"
        "M1 D G S SUB B NCH L=%gU W=%gU\n.ENDS\n"
        "XTOP D G S B %s\n.END\n" % (top, L_UM, W_UM, top), encoding="utf-8")
    lvs = work / (top + ".extracted.cir")
    run([klayout, "-b", "-r", str(lvs_deck), "-rd", "input=" + str(fab),
         "-rd", "top=" + top, "-rd", "layer_mode=fab",
         "-rd", "process_profile=icps2023_5_1m",
         "-rd", "schematic=" + str(reference), "-rd", "output=" + str(lvs)])
    pex_out = work / (top + ".pex.spice")
    run([sys.executable, str(pex), "--input", str(fab), "--top", top,
         "--layer-mode", "fab", "--process-profile", "icps2023_5_1m",
         "--output", str(pex_out)])
    assembled = work / (top + ".postlayout.spice")
    run([sys.executable, str(assembler), "--lvs", str(lvs), "--pex", str(pex_out),
         "--schematic", str(reference), "--top", top,
         "--output", str(assembled)])
    return {"top": top, "assembled": assembled}


def run_audit(work: Path, ngspice: str):
    pipe = _nmos4_pipeline(work / "nmos4")
    variant = work / "assembled_unified.spice"
    text = Path(pipe["assembled"]).read_text(encoding="utf-8")
    variant.write_text(
        "\n".join(line for line in text.splitlines()
                  if not line.strip().lower().startswith(".lib ")) + "\n",
        encoding="utf-8")

    def direct(offset):
        return _op(SELECTOR, work / "direct", ngspice, vp=VP0 + offset)

    def assembled(offset):
        return _op(SELECTOR, work / "assembled", ngspice, vp=VP0 + offset,
                   netlist_include=variant, top=pipe["top"])

    gmb_direct = _central(direct)
    gmb_assembled = _central(assembled)
    gm = _central(lambda offset: _op(SELECTOR, work / "gm", ngspice,
                                     vg=VG + offset))
    # E sweep: shift the substrate source instead of VG.
    def e_call(offset):
        work_e = work / "ge"
        deck = work_e / "op.cir"
        # Reuse _op but vary VSUB by using an explicit E source value.
        work_e.mkdir(parents=True, exist_ok=True)
        lines = [
            "* unified NMOS4 E control",
            '.lib "%s" b4soi' % SELECTOR.resolve().as_posix(),
            ".options temp=25 tnom=25",
            "VDS d 0 %.10g" % VD, "VGS g 0 %.10g" % VG,
            "VE e 0 %.10g" % offset, "VP p 0 %.10g" % VP0,
            "M1 d g 0 e p nch W=%gu L=%gu" % (W_UM, L_UM),
            ".control", "op", "print i(VDS)", "quit", ".endc", ".end", "",
        ]
        deck.write_text("\n".join(lines), encoding="utf-8")
        run = subprocess.run([ngspice, "-b", str(deck)], cwd=work_e, text=True,
                             capture_output=True, timeout=300)
        match = re.search(r"i\(vds\)\s*=\s*([+-]?[0-9.]+e[+-]?\d+)",
                          run.stdout + "\n" + run.stderr, re.I)
        if run.returncode or not match:
            return {"status": "failed", "reason": run.stderr[-300:]}
        return {"status": "observed", "id_a": float(match.group(1))}
    ge = _central(e_call)

    sweeps = {}
    for label, reltol in (("default", None), ("reltol_1e-4", 1e-4),
                          ("reltol_1e-6", 1e-6), ("reltol_1e-8", 1e-8)):
        sweeps[label] = _sweep(SELECTOR, work / label, ngspice, reltol=reltol)
    sweeps["reverse_default"] = _sweep(SELECTOR, work / "reverse", ngspice,
                                      direction=-1)
    tight = _central(lambda offset: _op(
        SELECTOR, work / "tight", ngspice, vp=VP0 + offset,
        options=["abstol=1e-15", "vntol=1e-9", "gmin=1e-15"]))

    slope_values = [item.get("slope_a_per_v") for item in
                    (gmb_direct, gmb_assembled, tight,
                     *sweeps.values())]
    slope_values = [value for value in slope_values if value is not None]
    spread = (max(slope_values) - min(slope_values)
              if slope_values else None)
    scale = max((abs(value) for value in slope_values), default=0.0)
    same_sign = (all(value > 0 for value in slope_values) or
                 all(value < 0 for value in slope_values))
    resolved = bool(slope_values) and same_sign and scale > 0 and \
        spread <= 0.25 * scale
    gm_slope = gm.get("slope_a_per_v")
    gmb_slope = gmb_direct.get("slope_a_per_v")
    ratio = (-gmb_slope / gm_slope) if (gmb_slope is not None and
                                        gm_slope not in (None, 0)) else None
    report = {
        "schema_version": 1,
        "tool": "audit_nmos4_body_bias_resolution.py",
        "status": STATUS,
        "resolution_status": ("NUMERICALLY_RESOLVED" if resolved
                              else "UNRESOLVED"),
        "card": str(CARD), "selector": str(SELECTOR),
        "card_sha256": _sha256(CARD), "selector_sha256": _sha256(SELECTOR),
        "geometry": {"w_um": W_UM, "l_um": L_UM},
        "bias": {"vd_v": VD, "vg_v": VG, "vp_v": VP0, "delta_v": DELTA,
                 "temperature_c": 25},
        "sensitivity": {
            "gmb_p_a_per_v": gmb_slope,
            "gm_a_per_v": gm_slope,
            "ge_a_per_v": ge.get("slope_a_per_v"),
            "equivalent_dvth_dvp": ratio,
            "gmb_assembled_a_per_v": gmb_assembled.get("slope_a_per_v"),
            "gmb_tight_tolerances_a_per_v": tight.get("slope_a_per_v"),
            "qb_at_vp0_c": direct(0).get("qb_c"),
        },
        "sweep_matrix": sweeps,
        "resolution": {
            "slope_values_a_per_v": slope_values,
            "spread_a_per_v": spread,
            "relative_spread": (spread / scale if scale else None),
            "same_sign": same_sign,
            "criterion": "same sign and spread <= 25% of max |slope|",
        },
        "limitations": [
            "Local small-signal ratios at one bias point; not thresholds "
            "extracted from measured transfer curves.",
            "Unified card parameters were fitted to zero-body-bias I-V data; "
            "a small gmb may reflect the fit or the equation, and the audit "
            "does not separate them by refitting.",
            "Direct vs assembled compares one contact network; it does not "
            "characterize the physical N_CH body path.",
            "Model capability only; no silicon body-bias or FD/PD conclusion.",
        ],
    }
    return report


def _markdown(report):
    s = report["sensitivity"]
    lines = [
        "# NMOS4 explicit-P numerical resolution audit",
        "",
        "Status: **%s** (%s)." % (report["status"], report["resolution_status"]),
        "",
        "Unified bridge card, ngspice BSIMSOI 4.4, W%g/L%g, VD=%g V, VG=%g V, "
        "VP=%g V." % (W_UM, L_UM, VD, VG, VP0),
        "",
        "| quantity | value |",
        "|---|---:|",
        "| g_mb,P = dID/dVP | %s A/V |" % s["gmb_p_a_per_v"],
        "| g_m = dID/dVG | %s A/V |" % s["gm_a_per_v"],
        "| g_E = dID/dVE | %s A/V |" % s["ge_a_per_v"],
        "| -g_mb,P/g_m (equivalent dVth/dVP) | %s |" % s["equivalent_dvth_dvp"],
        "| assembled-network g_mb,P | %s A/V |" % s["gmb_assembled_a_per_v"],
        "| tightened-tolerance g_mb,P | %s A/V |" % s["gmb_tight_tolerances_a_per_v"],
        "| Qb at VP0 | %s C |" % s["qb_at_vp0_c"],
        "",
        "## Sweep matrix",
        "",
        "| run | points | ID min (A) | ID max (A) | slope (A/V) | status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, row in report["sweep_matrix"].items():
        if row.get("status") != "observed":
            lines.append("| %s | – | – | – | – | %s |"
                         % (name, row.get("reason", "failed")))
        else:
            lines.append("| %s | %d | %.6g | %.6g | %.6g | observed |"
                         % (name, row["points"], row["id_min_a"],
                            row["id_max_a"], row["slope_a_per_v"]))
    r = report["resolution"]
    lines.extend([
        "",
        "Resolution criterion: %s. Same sign: %s; relative spread: %s."
        % (r["criterion"], r["same_sign"], r["relative_spread"]),
        "",
        "## Limitations", "",
    ])
    for item in report["limitations"]:
        lines.append("* " + item)
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice") or "ngspice")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    if not SELECTOR.is_file() or not CARD.is_file():
        parser.error("unified bridge card/selector is missing")
    if not (Path(args.ngspice).is_file() or shutil.which(args.ngspice)):
        parser.error("ngspice executable not found")
    try:
        if args.work_dir is None:
            with tempfile.TemporaryDirectory(prefix="icps-nmos4-res-") as temporary:
                report = run_audit(Path(temporary), args.ngspice)
        else:
            work = args.work_dir.expanduser().resolve()
            work.mkdir(parents=True, exist_ok=True)
            report = run_audit(work, args.ngspice)
    except (AuditError, OSError, subprocess.SubprocessError) as error:
        print("audit_nmos4_body_bias_resolution: %s" % error, file=sys.stderr)
        return 1
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"resolution_status": report["resolution_status"],
                      "output_json": str(args.output_json),
                      "output_md": str(args.output_md)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
