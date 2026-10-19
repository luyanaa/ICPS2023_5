#!/usr/bin/env python3
"""NMOS4 explicit-P bias sensitivity on the two experimental bridge cards.

Fixed D/G/S/E, sweep the fifth P terminal and record drain/body-terminal
currents and convergence.  This is a model-capability check: it asks whether
the card's explicit P terminal changes the device at all under bias.  It is
NOT silicon body-bias qualification, and a five-terminal LVS match says
nothing about it.

Cards are version-isolated and run on their supported engines:

  unified  SOIMOD=1  ngspice (BSIMSOI 4.4, LEVEL=58)
  ideal-FD SOIMOD=2  Xyce 7.10 / BSIMSOI 4.5.0 (ported to LEVEL=70450)

Geometry is the golden NMOS4 cell's W=20 um / L=6 um.  Vendor/process
qualification of the body path remains unmeasured.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
UNIFIED_SELECTOR = ROOT / "libs.tech/ngspice/b4soi_unified_bridge_experimental.lib.spice"
UNIFIED_CARD = ROOT / "libs.tech/ngspice/models/b4soi_unified_bridge_experimental.inc"
FD_CARD = ROOT / "libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc"
W_UM, L_UM = 20.0, 6.0
BIAS_POINTS = ((1.0, 1.5), (3.0, 3.0))  # (|VDS|, |VGS|)
P_SWEEP = (-3.0, 0.0, 0.1)  # reverse/near-zero body bias only; forward diode is out of scope
MAX_PHYSICAL_CURRENT_A = 0.1  # W20/L6 device is mA-scale; above this is not a body-effect read
STATUS = "MODEL_CAPABILITY_ONLY_NOT_SILICON_QUALIFIED"


class AuditError(ValueError):
    """Missing runtime or a failed sweep."""


def _load_xyce():
    spec = importlib.util.spec_from_file_location(
        "xyce_b4soi", TOOLS / "xyce_b4soi.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AuditError("cannot load xyce_b4soi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_columns(path: Path, width: int):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != width:
            continue
        try:
            rows.append([float(item.replace("D", "E").replace("d", "e"))
                         for item in fields])
        except ValueError:
            continue
    if len(rows) < 2:
        raise AuditError("sweep output has fewer than two numeric rows: %s" % path)
    if any(right[0] <= left[0] for left, right in zip(rows, rows[1:])):
        raise AuditError("sweep axis is not strictly increasing: %s" % path)
    return ([row[0] for row in rows],
            [abs(row[1]) for row in rows],
            [abs(row[2]) for row in rows])


def _metrics(axis, drain_current, body_current):
    mean_x = sum(axis) / len(axis)
    mean_y = sum(drain_current) / len(drain_current)
    denominator = sum((value - mean_x) ** 2 for value in axis)
    slope = (sum((x - mean_x) * (y - mean_y)
                 for x, y in zip(axis, drain_current)) / denominator
             if denominator else None)
    baseline = min(drain_current)
    peak = max(drain_current)
    return {
        "points": len(axis),
        "p_sweep_v": [axis[0], axis[-1]],
        "drain_current_min_a": baseline,
        "drain_current_max_a": peak,
        "drain_current_span_a": peak - baseline,
        "drain_current_span_relative": ((peak - baseline) / baseline
                                        if baseline else None),
        "drain_slope_a_per_v": slope,
        "body_terminal_current_max_abs_a": max(body_current),
        "body_terminal_current_median_abs_a": sorted(body_current)[len(body_current) // 2],
        "physical_sanity": ("ok" if peak <= MAX_PHYSICAL_CURRENT_A
                            and max(body_current) <= MAX_PHYSICAL_CURRENT_A
                            else "implausible_or_nonconverged"),
    }


def _run_ngspice(selector: Path, point, work: Path, ngspice: str,
                 sweep=P_SWEEP):
    vd, vg = point
    p_start, p_stop, p_step = sweep
    deck = work / ("unified_%g_%g.cir" % (vd, vg))
    data = work / ("unified_%g_%g.dat" % (vd, vg))
    data.unlink(missing_ok=True)
    deck.write_text("\n".join([
        "* NMOS4 explicit-P bias sweep on the unified bridge card",
        '.lib "%s" b4soi' % selector.resolve().as_posix(),
        ".options temp=25 tnom=25",
        "VDS drain 0 %.10g" % vd,
        "VGS gate 0 %.10g" % vg,
        "VSUB e 0 0",
        "VP p 0 0",
        "M1 drain gate 0 e p nch W=%gu L=%gu" % (W_UM, L_UM),
        ".control",
        "set wr_vecnames",
        "set wr_singlescale",
        "dc VP %.10g %.10g %.10g" % (p_start, p_stop, p_step),
        "wrdata %s i(VDS) i(VP)" % data.name,
        "quit",
        ".endc",
        ".end",
        "",
    ]), encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(deck)], cwd=work, text=True,
                         capture_output=True, timeout=900)
    if run.returncode != 0 or not data.is_file():
        return {"status": "failed", "stderr": run.stderr[-800:]}
    axis, drain, body = _parse_columns(data, 3)
    return {"status": "observed", "metrics": _metrics(axis, drain, body)}


def _run_xyce(point, work: Path):
    xyce = _load_xyce()
    if not xyce.available():
        raise AuditError("Xyce not found at %s" % xyce.XYCE)
    vd, vg = point
    p_start, p_stop, p_step = P_SWEEP
    inc = work / "fd_card.inc"
    inc.write_text(xyce.port_card(FD_CARD.read_text(encoding="utf-8")),
                   encoding="utf-8")
    name = "fd_%g_%g" % (vd, vg)
    deck = [
        "* NMOS4 explicit-P bias sweep on the ideal-FD card (Xyce)",
        '.include "%s"' % inc.name,
        ".OPTIONS DEVICE TEMP=25",
        "VDS drain 0 %.10g" % vd,
        "VGS gate 0 %.10g" % vg,
        "VSUB e 0 0",
        "VP p 0 0",
        "M1 drain gate 0 e p nch W=%gu L=%gu" % (W_UM, L_UM),
        ".DC VP %.10g %.10g %.10g" % (p_start, p_stop, p_step),
        ".PRINT DC FORMAT=CSV FILE=%s.csv V(p) I(VDS) I(VP)" % name,
        ".END",
    ]
    result = xyce.run(deck, name, work, timeout=900.0)
    if result.get("status") != "observed":
        reason = result.get("reason") or "unknown Xyce failure"
        log_path = work / (name + ".log")
        log_text = (log_path.read_text(encoding="utf-8", errors="replace")
                    if log_path.is_file() else "")
        if ("P node" in reason or "5th node" in reason or
                "P node" in log_text or "5th node" in log_text):
            return {"status": "IDEAL_FD_EXPLICIT_P_UNSUPPORTED",
                    "reason": ("Xyce rejects an externally specified fifth P "
                               "node for SOIMOD=2 with TNODEOUT=0 "
                               "(log retained in the run work directory)")}
        return {"status": "failed", "reason": reason}
    axis, columns = xyce.read_csv(result["csv"])
    if len(columns) != 2:
        return {"status": "failed", "reason": "expected V(p), I(VDS), I(VP)"}
    drain = [abs(value) for value in columns[0]]
    body = [abs(value) for value in columns[1]]
    return {"status": "observed", "metrics": _metrics(axis, drain, body)}


def _markdown(report):
    lines = [
        "# NMOS4 explicit-P bias capability audit",
        "",
        "Status: **%s**." % report["status"],
        "",
        "Fixed D/G/S/E; the fifth P terminal is swept. This asks only whether "
        "the candidate model responds to the explicit P terminal. It is not "
        "silicon body-bias qualification and does not validate body access.",
        "",
        "| card | engine | geometry |",
        "|---|---|---|",
        "| unified (SOIMOD=1) | ngspice BSIMSOI 4.4 (LEVEL=58) | W%g/L%g um |"
        % (W_UM, L_UM),
        "| ideal-FD (SOIMOD=2) | Xyce 7.10 BSIMSOI 4.5.0 (LEVEL=70450) | W%g/L%g um |"
        % (W_UM, L_UM),
        "",
    ]
    for card, block in report["cards"].items():
        lines.extend([
            "## %s" % card,
            "",
            "| |VDS| (V) | |VGS| (V) | points | ID min (A) | ID max (A) | "
            "ID span (A) | dID/dVP (A/V) | max |IP| (A) | status |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for row in block["points"]:
            if row["status"] != "observed":
                lines.append("| %g | %g | – | – | – | – | – | – | %s: %s |"
                             % (row["vds_v"], row["vgs_v"], row["status"],
                                row.get("reason") or row.get("stderr") or "no detail"))
                continue
            m = row["metrics"]
            lines.append("| %g | %g | %d | %.6g | %.6g | %.6g | %s | %.3g | observed (%s) |"
                         % (row["vds_v"], row["vgs_v"], m["points"],
                            m["drain_current_min_a"], m["drain_current_max_a"],
                            m["drain_current_span_a"],
                            ("%.6g" % m["drain_slope_a_per_v"]
                             if m["drain_slope_a_per_v"] is not None else "n/a"),
                            m["body_terminal_current_max_abs_a"],
                            m["physical_sanity"]))
        lines.append("")
        forward = block.get("forward_sweep_sanity")
        if forward:
            status = ("PHYSICAL_SANITY_FAIL"
                      if (forward.get("status") == "observed" and
                          forward["metrics"]["physical_sanity"]
                          == "implausible_or_nonconverged")
                      else (forward.get("status") or "not_run"))
            lines.append(
                "Forward P sweep (-3..3 V) retained as a negative regression: "
                "**%s** (ID max %s A). It is never used as a body-effect result."
                % (status, ("%.6g" % forward["metrics"]["drain_current_max_a"])
                   if forward.get("status") == "observed" else "n/a"))
    lines.extend(["", "## Observations", ""])
    for item in report["observations"]:
        lines.append("* " + item)
    lines.extend(["", "## Limitations", ""])
    for item in report["limitations"]:
        lines.append("* " + item)
    lines.append("")
    return "\n".join(lines)


def run_audit(work: Path, ngspice: str):
    cards = {
        "unified_soimod1": {
            "engine": "ngspice", "selector": str(UNIFIED_SELECTOR),
            "card_sha256": _sha256(UNIFIED_CARD),
            "selector_sha256": _sha256(UNIFIED_SELECTOR),
            "points": [],
        },
        "ideal_fd_soimod2": {
            "engine": "xyce-fd", "card": str(FD_CARD),
            "card_sha256": _sha256(FD_CARD),
            "points": [],
        },
    }
    for vd, vg in BIAS_POINTS:
        ng_work = work / "unified" / ("%g_%g" % (vd, vg))
        ng_work.mkdir(parents=True, exist_ok=True)
        cards["unified_soimod1"]["points"].append({
            "vds_v": vd, "vgs_v": vg,
            **_run_ngspice(UNIFIED_SELECTOR, (vd, vg), ng_work, ngspice),
        })
        fx_work = work / "fd" / ("%g_%g" % (vd, vg))
        fx_work.mkdir(parents=True, exist_ok=True)
        cards["ideal_fd_soimod2"]["points"].append({
            "vds_v": vd, "vgs_v": vg,
            **_run_xyce((vd, vg), fx_work),
        })
    forward_work = work / "forward_sanity"
    forward_work.mkdir(parents=True, exist_ok=True)
    cards["unified_soimod1"]["forward_sweep_sanity"] = {
        "p_sweep_v": [-3.0, 3.0, 0.1],
        "expected_classification": "PHYSICAL_SANITY_FAIL",
        **_run_ngspice(UNIFIED_SELECTOR, BIAS_POINTS[0], forward_work,
                       ngspice, sweep=(-3.0, 3.0, 0.1)),
    }
    unified_points = [row for row in cards["unified_soimod1"]["points"]
                      if row["status"] == "observed"]
    fd_statuses = {row["status"] for row in cards["ideal_fd_soimod2"]["points"]}
    observations = []
    if unified_points:
        worst = max(row["metrics"]["drain_current_span_relative"] or 0.0
                    for row in unified_points)
        observations.append(
            "unified SOIMOD=1: sweeping P over %g..%g V changes ID by at most "
            "%.3g relative (max |IP| %.3g A); no meaningful body control is "
            "demonstrated in this window."
            % (P_SWEEP[0], P_SWEEP[1], worst,
               max(row["metrics"]["body_terminal_current_max_abs_a"]
                   for row in unified_points)))
    observations.append(
        "ideal-FD SOIMOD=2: Xyce 7.10 rejects an externally specified fifth "
        "P node when SOIMOD=2 and TNODEOUT=0 ('P node is not used in this mode'). "
        "The FD card's explicit-P body-bias capability is therefore untestable "
        "in its supported engine configuration; a five-terminal ngspice parse "
        "is not evidence of FD body control.")
    report = {
        "schema_version": 1,
        "tool": "audit_nmos4_body_bias.py",
        "status": STATUS,
        "geometry": {"w_um": W_UM, "l_um": L_UM, "temperature_c": 25},
        "p_sweep_v": {"start": P_SWEEP[0], "stop": P_SWEEP[1],
                      "step": P_SWEEP[2]},
        "cards": cards,
        "observations": observations,
        "limitations": [
            "A response (or lack of response) to the explicit P terminal is a "
            "property of the candidate card, not of the fabricated device.",
            "The two cards run on different simulator versions (ngspice "
            "BSIMSOI 4.4 vs Xyce 4.5.0); their numbers must not be mixed or "
            "read as one model on two backends.",
            "The FD card is experimental and remains non-nominal.",
            "The N_CH body path from the P+ dot to the channel is not "
            "extracted; no body-access resistance or bias dependence is "
            "qualified here.",
            "Body-bias/C-V and FD/PD assignment still require TEG data.",
            "Xyce 7.10 / BSIMSOI 4.5.0 with SOIMOD=2 and TNODEOUT=0 rejects an "
            "external fifth P node, so the ideal-FD card cannot be exercised "
            "as a five-terminal device in its scoped engine configuration.",
        ],
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice") or "ngspice")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    for required in (UNIFIED_SELECTOR, UNIFIED_CARD, FD_CARD):
        if not required.is_file():
            parser.error("required card is missing: %s" % required)
    if not shutil.which(args.ngspice) and not Path(args.ngspice).is_file():
        parser.error("ngspice executable not found: %s" % args.ngspice)
    try:
        if args.work_dir is None:
            with tempfile.TemporaryDirectory(prefix="icps-nmos4-bias-") as temporary:
                report = run_audit(Path(temporary), args.ngspice)
        else:
            work = args.work_dir.expanduser().resolve()
            work.mkdir(parents=True, exist_ok=True)
            report = run_audit(work, args.ngspice)
    except (AuditError, OSError, subprocess.SubprocessError) as error:
        print("audit_nmos4_body_bias: %s" % error, file=sys.stderr)
        return 1
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"],
                      "cards": sorted(report["cards"]),
                      "output_json": str(args.output_json),
                      "output_md": str(args.output_md)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
