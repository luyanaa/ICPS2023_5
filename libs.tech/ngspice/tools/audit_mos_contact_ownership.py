#!/usr/bin/env python3
"""Bounded DC I-V experiment for the RDSW / explicit-RCNT separation question.

Three cases are evaluated on the frozen digitized PDK curves with the same
nominal stage-1 BSIMSOI card and the same ngspice engine:

  1. ``bare``            the original fitted W24/L6 MOSFET deck, unchanged;
  2. ``explicit_rcnt``   the same device with one lumped R_cnt = R_unit/N_cnt
                         contact resistor on each D/S side, N_cnt read from the
                         actual PEX MOS_MAP of the generated native cell;
  3. ``full_postlayout`` the actual native PCell -> fab-prep -> fab LVS -> fab
                         PEX -> assemble_postlayout network.

Purpose: make the RDSW/contact contamination visible.  This is an experiment,
not a calibration: the original W24/L6 measurement's contact configuration is
not reported, so a residual shift between cases cannot prove or disprove
double-counting.  No parameter is refitted and no model card is modified.

Engines are version-isolated and never mixed in one report: the default
``ngspice`` path uses the nominal stage-1 card (BSIMSOI 4.4); ``--engine
xyce-fd`` uses the experimental FD bridge card on Xyce 7.10 / BSIMSOI 4.5.0
and writes separate artifacts.

Status emitted by every report: RDSW_RCNT_SEPARATION_UNCALIBRATED.
Independent Kelvin/TLM contact-count measurements remain required.
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
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
MODEL_CARD = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
FD_LIBRARY = ROOT / "libs.tech/ngspice/b4soi_fd_bridge_experimental.lib.spice"
FD_CARD = ROOT / "libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc"
ENGINES = ("ngspice", "xyce-fd")
MEASUREMENTS = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_iv_digitized.csv"
BASELINE = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_baseline.json"
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX_SCRIPT = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
PROFILE = "icps2023_5_1m"
W_UM, L_UM = 24.0, 6.0
CONTACT_RESISTANCE_OHM = {"nmos": 150.0, "pmos": 200.0}
CASE_NAMES = ("bare", "explicit_rcnt", "full_postlayout")
STATUS = "RDSW_RCNT_SEPARATION_UNCALIBRATED"


class AuditError(ValueError):
    """Missing runtime, malformed input, or a failed pipeline step."""


def _load_regress():
    spec = importlib.util.spec_from_file_location(
        "regress_b4soi_stage1_iv", TOOLS / "regress_b4soi_stage1_iv.py")
    if spec is None or spec.loader is None:  # pragma: no cover - packaging guard
        raise AuditError("cannot load regress_b4soi_stage1_iv.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGRESS = _load_regress()


def _load_xyce():
    spec = importlib.util.spec_from_file_location(
        "xyce_b4soi", TOOLS / "xyce_b4soi.py")
    if spec is None or spec.loader is None:  # pragma: no cover - packaging guard
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


def _run(command, *, cwd=None, timeout=600):
    result = subprocess.run(command, text=True, capture_output=True, cwd=cwd,
                            timeout=timeout)
    if result.returncode != 0:
        raise AuditError("command failed (%d): %s\n%s\n%s"
                         % (result.returncode, " ".join(map(str, command)),
                            result.stdout, result.stderr))
    return result


def _environment():
    import os
    return dict(os.environ, ICPS2023_5_PROCESS_PROFILE=PROFILE)


def _sweep_bounds(points, step):
    start = math.floor(min(axis for axis, _current in points) / step) * step
    stop = math.ceil(max(axis for axis, _current in points) / step) * step
    if start == stop:
        stop = start + step
    return start, stop


def _control_block(axis_source, start, stop, step):
    return [
        ".control",
        "set wr_vecnames",
        "set wr_singlescale",
        "dc %s %s %s %s" % (axis_source, REGRESS.format_number(start),
                             REGRESS.format_number(stop),
                             REGRESS.format_number(step)),
        "wrdata sweep.dat i(VDS)",
        "quit",
        ".endc",
        ".end",
        "",
    ]


def _case_deck(case, key, points, step, counts, full):
    """Deck text for one case and one digitized curve.

    ``bare`` reuses the frozen regression deck verbatim.  ``explicit_rcnt``
    inserts measured per-opening resistances; ``full_postlayout`` instantiates
    the assembled native-cell subcircuit with its own pin order.
    """
    device, family, _name, bias_axis, bias = key
    model = "nch" if device == "nmos" else "pch"
    axis_source = "VDS" if family == "output" else "VGS"
    start, stop = _sweep_bounds(points, step)
    gate_bias = bias if bias_axis == "VGS" else 0.0
    drain_bias = bias if bias_axis == "VDS" else 0.0
    library = MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"')

    if case == "bare":
        deck, _start = REGRESS.make_sweep_deck(key, points, step, MODEL_LIBRARY)
        return deck, start

    lines = [
        "* %s case for %s; nominal stage-1 card, no refit" % (case, key[2]),
    ]
    if case != "full_postlayout":
        lines.append('.lib "%s" b4soi' % library)
    lines.extend([
        ".options temp=25 tnom=25",
        "VGS gate 0 %s" % REGRESS.format_number(gate_bias),
        "VDS drain 0 %s" % REGRESS.format_number(drain_bias),
    ])
    if case == "explicit_rcnt":
        drain_cuts = counts["drain"]
        source_cuts = counts["source"]
        unit = CONTACT_RESISTANCE_OHM[device]
        lines.append("R$D drain mos_drain %.9g" % (unit / drain_cuts))
        lines.append("R$S mos_source 0 %.9g" % (unit / source_cuts))
        lines.append("MTAUDIT mos_drain gate mos_source 0 %s L=%gu W=%gu"
                     % (model, L_UM, W_UM))
    elif case == "full_postlayout":
        lines.append("VSUB e_sub 0 0")
        assembled = Path(full["assembled"]).resolve().as_posix().replace('"', '\\"')
        lines.append('.include "%s"' % assembled)
        lines.append("XDUT drain gate 0 e_sub %s" % full["top"])
    else:  # pragma: no cover - guarded by CLI choices
        raise AuditError("unknown case %r" % case)
    lines.extend(_control_block(axis_source, start, stop, step))
    return "\n".join(lines), start


def _curve_metrics(key, points, sim_axis, sim_current):
    predicted = [REGRESS.interpolate(sim_axis, sim_current, axis)
                 for axis, _ in points]
    measured = [current for _, current in points]
    if key[1] == "output":
        error = math.sqrt(sum(
            ((simulation - observation) / observation) ** 2
            for simulation, observation in zip(predicted, measured)
        ) / len(measured)) * 100.0
        metric_name = "relative_rms_percent"
    else:
        error = math.sqrt(sum(
            (math.log10(simulation) - math.log10(observation)) ** 2
            for simulation, observation in zip(predicted, measured)
        ) / len(measured))
        metric_name = "log10_current_rms_decades"
    return {
        "device": key[0], "family": key[1], "bias_axis": key[3],
        "bias_V": key[4], "samples": len(points),
        metric_name: error,
    }


def _evaluate_case(case, curves, ngspice, step, counts, full, work):
    metrics = {}
    for key, points in sorted(curves.items()):
        name = key[2]
        deck_path = work / ("%s_%s.cir" % (case, name))
        data_path = work / "sweep.dat"
        if data_path.exists():
            data_path.unlink()
        deck, start = _case_deck(case, key, points, step, counts, full)
        deck_path.write_text(deck, encoding="utf-8")
        run = subprocess.run([ngspice, "-b", str(deck_path)], cwd=work,
                             text=True, capture_output=True, check=False,
                             timeout=600)
        if run.returncode != 0 or not data_path.is_file():
            raise AuditError("ngspice failed for %s/%s:\n%s\n%s"
                             % (case, name, run.stdout, run.stderr))
        try:
            sim_axis, sim_current = REGRESS.read_sweep(data_path, step, start)
        except REGRESS.RegressionError as error:
            head = "\n".join(data_path.read_text(encoding="utf-8").splitlines()[:6])
            raise AuditError("case=%s curve=%s start=%g step=%g: %s\nfirst lines:\n%s"
                             % (case, name, start, step, error, head)) from error
        metrics[name] = _curve_metrics(key, points, sim_axis, sim_current)
    return metrics


def _xyce_case_deck(case, key, points, step, counts, full, card_inc,
                    netlist_inc):
    """Xyce deck for one case/curve; the FD card is ported to LEVEL=70450."""
    device, family, _name, bias_axis, bias = key
    model = "nch" if device == "nmos" else "pch"
    axis_source = "VDS" if family == "output" else "VGS"
    start, stop = _sweep_bounds(points, step)
    gate_bias = bias if bias_axis == "VGS" else 0.0
    drain_bias = bias if bias_axis == "VDS" else 0.0
    lines = [
        "* Xyce FD-card %s case for %s; no refit" % (case, key[2]),
        '.include "%s"' % card_inc,
        ".OPTIONS DEVICE TEMP=25",
        "VGS gate 0 %.10g" % gate_bias,
        "VDS drain 0 %.10g" % drain_bias,
    ]
    if case == "bare":
        lines.append("MTEST drain gate 0 0 %s W=%gu L=%gu" % (model, W_UM, L_UM))
    elif case == "explicit_rcnt":
        unit = CONTACT_RESISTANCE_OHM[device]
        lines.append("RCNTD drain mtaudit_drain %.9g"
                     % (unit / counts["drain"]))
        lines.append("RCNTS mtaudit_source 0 %.9g"
                     % (unit / counts["source"]))
        lines.append("MTAUDIT mtaudit_drain gate mtaudit_source 0 %s W=%gu L=%gu"
                     % (model, W_UM, L_UM))
    elif case == "full_postlayout":
        lines.extend([
            "VSUB e_sub 0 0",
            '.include "%s"' % netlist_inc,
            "XDUT drain gate 0 e_sub %s" % full["top"],
        ])
    else:  # pragma: no cover - guarded by CASE_NAMES
        raise AuditError("unknown case %r" % case)
    lines.extend([
        ".DC %s %.10g %.10g %.10g" % (axis_source, start, stop, step),
        ".PRINT DC FORMAT=CSV FILE=%s.csv V(%s) I(VDS)"
        % ("%s_%s" % (case, key[2]), "drain" if family == "output" else "gate"),
        ".END",
    ])
    return "\n".join(lines), start


def _evaluate_case_xyce(case, curves, step, counts, full, work):
    xyce = _load_xyce()
    if not xyce.available():
        raise AuditError("Xyce not found at %s" % xyce.XYCE)
    card_inc = work / ("fd_card_%s.inc" % case)
    card_inc.write_text(
        xyce.port_card(FD_CARD.read_text(encoding="utf-8")), encoding="utf-8")
    netlist_inc = None
    if case == "full_postlayout":
        text = Path(full["assembled"]).read_text(encoding="utf-8")
        kept = [line for line in text.splitlines()
                if not line.strip().lower().startswith(".lib ")]
        netlist_inc = work / "fd_full_netlist.spice"
        netlist_inc.write_text("\n".join(kept) + "\n", encoding="utf-8")
    metrics = {}
    for key, points in sorted(curves.items()):
        deck, start = _xyce_case_deck(
            case, key, points, step, counts, full, card_inc.name,
            netlist_inc.name if netlist_inc is not None else None)
        name = "%s_%s_xyce" % (case, key[2])
        result = xyce.run(deck.splitlines(), name, work, timeout=900.0)
        if result.get("status") != "observed":
            raise AuditError("Xyce failed for %s: %r" % (name, result))
        axis, columns = xyce.read_csv(result["csv"])
        if len(columns) != 1:
            raise AuditError("Xyce printed %d columns for %s" % (len(columns), name))
        if len(axis) < 2 or any(b <= a for a, b in zip(axis, axis[1:])):
            raise AuditError("non-monotone Xyce axis for %s" % name)
        current = [abs(value) for value in columns[0]]
        metrics[key[2]] = _curve_metrics(key, points, axis, current)
    return metrics


def _generate_native_cell(work: Path, name: str) -> Path:
    output = work / "gds"
    output.mkdir(parents=True, exist_ok=True)
    _run([shutil.which("klayout") or "klayout", "-b", "-r", str(GENERATOR),
          "-rd", "output_dir=" + str(output),
          "-rd", "pcell_macro=" + str(PCELL_MACRO),
          "-rd", "mos_width=%g" % W_UM, "-rd", "design_suffix=fit"],
         cwd=work)
    path = output / (name + ".gds")
    if not path.is_file():
        raise AuditError("generator did not produce %s" % path)
    return path


def _reference_text(top: str, polarity: str) -> str:
    model = "nch" if polarity == "nmos" else "pch"
    return (".GLOBAL SUB\n"
            ".SUBCKT %s D G S\n"
            "M1 D G S SUB %s L=%gU W=%gU\n"
            ".ENDS %s\n"
            "XTOP D G S %s\n.END\n"
            % (top, model, L_UM, W_UM, top, top))


def _mos_map_records(pex_text: str):
    return [json.loads(line[len("* MOS_MAP "):])
            for line in pex_text.splitlines()
            if line.startswith("* MOS_MAP ")]


def _pipeline(work: Path, polarity: str):
    """Run generation -> fab-prep -> LVS -> PEX -> assembly for one polarity."""
    klayout = shutil.which("klayout") or "klayout"
    top = "%s_min_fit" % polarity
    design = _generate_native_cell(work, top)
    fab = work / (top + ".fab.gds")
    _run([klayout, "-b", "-r", str(FAB_PREP), "-rd", "input=" + str(design),
          "-rd", "output=" + str(fab), "-rd", "process_profile=" + PROFILE],
         cwd=work)
    reference = work / (top + ".cir")
    reference.write_text(_reference_text(top, polarity), encoding="utf-8")
    extracted = work / (top + ".extracted.cir")
    _run([klayout, "-b", "-r", str(LVS_DECK), "-rd", "input=" + str(fab),
          "-rd", "top=" + top, "-rd", "layer_mode=fab",
          "-rd", "process_profile=" + PROFILE,
          "-rd", "schematic=" + str(reference), "-rd", "output=" + str(extracted),
          "-rd", "report=" + str(work / (top + ".lvs.lyrdb"))], cwd=work)
    pex = work / (top + ".pex.spice")
    _run([sys.executable, str(PEX_SCRIPT), "--input", str(fab), "--top", top,
          "--layer-mode", "fab", "--process-profile", PROFILE,
          "--output", str(pex)], cwd=work)
    records = _mos_map_records(pex.read_text(encoding="utf-8"))
    if len(records) != 1:
        raise AuditError("expected exactly one MOS_MAP for %s; found %d"
                         % (top, len(records)))
    record = records[0]
    if record.get("polarity", "").upper() != polarity.upper():
        raise AuditError("MOS_MAP polarity mismatch for %s: %r" % (top, record))
    counts = {
        "drain": int(record.get("drain_contact_cuts", 1)),
        "source": int(record.get("source_contact_cuts", 1)),
    }
    assembled = work / (top + ".postlayout.spice")
    _run([sys.executable, str(ASSEMBLER), "--lvs", str(extracted),
          "--pex", str(pex), "--schematic", str(reference), "--top", top,
          "--output", str(assembled)], cwd=work)
    return {
        "top": top,
        "design": design, "fab": fab, "reference": reference,
        "extracted": extracted, "pex": pex, "assembled": assembled,
        "counts": counts,
    }


def _engine_version(ngspice: str) -> str:
    result = subprocess.run([ngspice, "--version"], text=True,
                            capture_output=True, timeout=60)
    for line in (result.stdout + result.stderr).splitlines():
        if "ngspice" in line.lower():
            return line.strip()
    return "unknown"


def _baseline_cross_check(bare_metrics, baseline):
    """Compare the bare-case metrics with the frozen stage-1 manifest."""
    reference = baseline.get("curves", {})
    tolerances = baseline.get("metric_absolute_tolerance", {})
    rows = []
    matches = True
    for name, row in sorted(bare_metrics.items()):
        metric = ("relative_rms_percent" if row["family"] == "output"
                  else "log10_current_rms_decades")
        frozen = reference.get(name)
        if frozen is None or metric not in frozen:
            matches = False
            rows.append({"curve": name, "metric": metric, "status": "missing_in_baseline"})
            continue
        delta = row[metric] - frozen[metric]
        tolerance = float(tolerances.get(metric, 0.0))
        within = abs(delta) <= tolerance
        matches = matches and within
        rows.append({"curve": name, "metric": metric, "bare": row[metric],
                     "frozen": frozen[metric], "delta": delta,
                     "tolerance": tolerance, "within_tolerance": within})
    return matches, rows


def _case_observations(cases):
    """State whether the full network differs from the explicit-RCNT network."""
    worst = 0.0
    for name, row in cases["explicit_rcnt"].items():
        metric = ("relative_rms_percent" if row["family"] == "output"
                  else "log10_current_rms_decades")
        left = row[metric]
        right = cases["full_postlayout"][name][metric]
        worst = max(worst, abs(left - right) / max(abs(left), 1e-30))
    if worst <= 1e-6:
        return ("full_postlayout reproduces explicit_rcnt on every curve "
                "(worst relative difference %.2g): for this single-MOS bench the "
                "only external series difference is the added contact network, so "
                "the bare-to-case shift is not PEX routing." % worst)
    return ("full_postlayout differs from explicit_rcnt (worst relative "
            "difference %.2g); that difference includes PEX routing R/C and is a "
            "combined confound." % worst)


def _deltas(bare, case):
    deltas = {}
    for name, row in case.items():
        baseline = bare.get(name)
        if baseline is None:
            continue
        metric = ("relative_rms_percent" if row["family"] == "output"
                  else "log10_current_rms_decades")
        deltas[name] = {
            "metric": metric,
            "bare": baseline[metric],
            "case": row[metric],
            "delta": row[metric] - baseline[metric],
        }
    return deltas


def _markdown(report):
    lines = [
        "# MOS contact ownership I-V audit",
        "",
        "Status: **%s**. Same card and engine for every case; "
        "no refit, no RDSW subtraction." % report["status"],
        "",
        "%s (%s)" % (report["engine"].get("name", "engine"),
                     report["engine"].get("version", "unknown")),
        "",
        "| case | meaning |",
        "|---|---|",
        "| bare | original fitted W24/L6 deck, unchanged |",
        "| explicit_rcnt | same device plus R_cnt = R_unit/N_cnt per D/S side |",
        "| full_postlayout | native PCell -> fab-prep -> LVS -> PEX -> assembly |",
        "",
        "## Original measurement",
        "",
        "| field | value |",
        "|---|---|",
    ]
    for key, value in report["original_measurement"].items():
        lines.append("| %s | %s |" % (key, value))
    for polarity, block in report["polarities"].items():
        lines.extend([
            "",
            "## %s (%s)" % (polarity.upper(), block["top"]),
            "",
            "Contact counts read from the actual PEX MOS_MAP: "
            "drain=%d, source=%d; R_unit=%g ohm."
            % (block["counts"]["drain"], block["counts"]["source"],
               CONTACT_RESISTANCE_OHM[polarity]),
            "",
            "| curve | samples | bare | explicit_rcnt | full_postlayout | ΔRCNT | Δfull |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        names = sorted(block["cases"]["bare"])
        for name in names:
            bare_row = block["cases"]["bare"][name]
            metric = ("relative_rms_percent" if bare_row["family"] == "output"
                      else "log10_current_rms_decades")
            rcnt = block["cases"]["explicit_rcnt"][name][metric]
            full = block["cases"]["full_postlayout"][name][metric]
            lines.append("| %s | %d | %.4g | %.4g | %.4g | %+.4g | %+.4g |"
                         % (name, bare_row["samples"], bare_row[metric],
                            rcnt, full, rcnt - bare_row[metric],
                            full - bare_row[metric]))
        lines.append("")
        lines.append("Metric per family: output curves = relative RMS % of drain "
                     "current; transfer curves = log10-current RMS in decades "
                     "(same convention as the frozen stage-1 regression).")
        lines.append("")
        baseline = block["baseline_cross_check"]
        if baseline.get("applicable", True):
            lines.append("Bare case vs frozen stage-1 manifest: **%s** "
                         "(`fit/b4soi_stage1_baseline.json`)."
                         % ("MATCH" if baseline["matches"] else "MISMATCH"))
            for row in baseline["curves"]:
                if row.get("within_tolerance") is False or row.get("status"):
                    lines.append("* deviation on `%s`: %s"
                                 % (row["curve"], json.dumps(row, sort_keys=True)))
        else:
            lines.append("Bare-case baseline cross-check: **NOT APPLICABLE** — "
                         + baseline["reason"])
        for observation in block["observations"]:
            lines.append("* " + observation)
    lines.extend(["", "## Limitations", ""])
    for item in report["limitations"]:
        lines.append("* " + item)
    lines.append("")
    return "\n".join(lines)


def run_audit(work: Path, ngspice: str, step: float = 0.001,
              engine: str = "ngspice"):
    if engine not in ENGINES:
        raise AuditError("engine must be one of %s" % (ENGINES,))
    curves = REGRESS.read_curves(MEASUREMENTS)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    polarities = {}
    for polarity in ("nmos", "pmos"):
        device_curves = {key: points for key, points in curves.items()
                         if key[0] == polarity}
        if not device_curves:
            raise AuditError("digitized data has no %s curves" % polarity)
        pipeline = _pipeline(work / polarity, polarity)
        case_work = work / polarity / "eval"
        case_work.mkdir(parents=True, exist_ok=True)
        cases = {}
        for case in CASE_NAMES:
            full = {"assembled": pipeline["assembled"], "top": pipeline["top"]}
            if engine == "ngspice":
                cases[case] = _evaluate_case(
                    case, device_curves, ngspice, step, pipeline["counts"],
                    full, case_work)
            else:
                cases[case] = _evaluate_case_xyce(
                    case, device_curves, step, pipeline["counts"], full, case_work)
        if engine == "ngspice":
            bare_matches, bare_rows = _baseline_cross_check(cases["bare"], baseline)
            baseline_check = {"applicable": True, "matches": bare_matches,
                              "curves": bare_rows}
        else:
            baseline_check = {
                "applicable": False,
                "reason": ("the FD bridge card is a different, Xyce-scoped card; "
                           "the stage-1 frozen baseline does not apply. The FD fit "
                           "manifest's per-curve metrics were themselves computed "
                           "with ngspice BSIMSOI 4.4, so they are not a cross-check "
                           "for this Xyce 4.5.0 run either."),
            }
        polarities[polarity] = {
            "top": pipeline["top"],
            "counts": pipeline["counts"],
            "cases": cases,
            "baseline_cross_check": baseline_check,
            "observations": [_case_observations(cases)],
            "deltas_vs_bare": {
                "explicit_rcnt": _deltas(cases["bare"], cases["explicit_rcnt"]),
                "full_postlayout": _deltas(cases["bare"], cases["full_postlayout"]),
            },
            "provenance": {
                "design_gds_sha256": _sha256(pipeline["design"]),
                "fab_gds_sha256": _sha256(pipeline["fab"]),
                "lvs_extracted_sha256": _sha256(pipeline["extracted"]),
                "pex_sha256": _sha256(pipeline["pex"]),
                "assembled_sha256": _sha256(pipeline["assembled"]),
            },
        }
    if engine == "ngspice":
        engine_block = {"name": "ngspice", "path": ngspice,
                        "version": _engine_version(ngspice)}
        provenance_block = {
            "measurements_sha256": _sha256(MEASUREMENTS),
            "model_library_sha256": _sha256(MODEL_LIBRARY),
            "model_card_sha256": _sha256(MODEL_CARD),
        }
        limitations = [
            "No Kelvin/TLM or contact-count matrix exists, so the two series "
            "terms cannot be declared physically separate.",
            "full_postlayout carries PEX routing R/C in general; in these "
            "single-MOS benches it added no series element beyond the contact "
            "network, but on other topologies a shift is a combined confound.",
            "Both cases reuse one card; the experiment changes only the "
            "external network, never the fitted parameters.",
            "Digitized curves carry the conflicts already recorded in the "
            "stage-1/bridge audits; they are the only available DC target.",
        ]
    else:
        xyce = _load_xyce()
        engine_block = {
            "name": "xyce-fd",
            "path": xyce.XYCE,
            "version": "Xyce 7.10 / BSIMSOI 4.5.0 (LEVEL=70450)",
            "card": str(FD_CARD),
            "selector": str(FD_LIBRARY),
        }
        provenance_block = {
            "measurements_sha256": _sha256(MEASUREMENTS),
            "fd_model_card_sha256": _sha256(FD_CARD),
            "fd_selector_sha256": _sha256(FD_LIBRARY),
            "nominal_card_sha256_read_only": _sha256(MODEL_CARD),
        }
        limitations = [
            "No Kelvin/TLM or contact-count matrix exists, so the two series "
            "terms cannot be declared physically separate.",
            "Xyce BSIMSOI 4.5.0 (LEVEL=70450) is a different model version from "
            "the ngspice 4.4 nominal path; these FD results must not be mixed "
            "with nominal-card results or read as two backends of one model.",
            "The FD card itself is experimental and version-scoped; this run "
            "does not promote it to nominal.",
            "full_postlayout carries PEX routing R/C in general; in these "
            "single-MOS benches it added no series element beyond the contact "
            "network, but on other topologies a shift is a combined confound.",
            "Digitized curves carry the conflicts already recorded in the "
            "stage-1/bridge audits; they are the only available DC target.",
        ]
    report = {
        "schema_version": 1,
        "tool": "audit_mos_contact_ownership.py",
        "status": STATUS,
        "engine": engine_block,
        "geometry": {"w_um": W_UM, "l_um": L_UM, "temperature_c": 25,
                     "cells": "native nmos_min_fit / pmos_min_fit, fab masks"},
        "original_measurement": {
            "contact_count_used_in_fit": None,
            "note": ("The original W24/L6 measurement's contact configuration "
                     "is unreported; a shift between cases cannot itself prove "
                     "or disprove double-counting."),
        },
        "provenance": {
            **provenance_block,
            "pex_deck_sha256": _sha256(PEX_SCRIPT),
            "lvs_deck_sha256": _sha256(LVS_DECK),
            "sweep_step_V": step,
            "contact_resistance_ohm": CONTACT_RESISTANCE_OHM,
        },
        "polarities": polarities,
        "limitations": limitations,
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="scratch directory for generated layouts/netlists (default: a temporary directory)")
    parser.add_argument("--ngspice", default=shutil.which("ngspice") or "ngspice",
                        help="ngspice binary (default: PATH)")
    parser.add_argument("--step-V", type=float, default=0.001,
                        help="sweep step in V (default 0.001, as the frozen regression)")
    parser.add_argument("--engine", choices=ENGINES, default="ngspice",
                        help="ngspice nominal card, or Xyce 7.10 FD bridge card "
                             "(version-scoped; never mixed with nominal results)")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.step_V) or args.step_V <= 0:
        parser.error("--step-V must be positive and finite")
    for required in (MODEL_LIBRARY, MODEL_CARD, MEASUREMENTS, GENERATOR,
                     PCELL_MACRO, FAB_PREP, LVS_DECK, PEX_SCRIPT, ASSEMBLER):
        if not required.is_file():
            parser.error("required input is missing: %s" % required)
    if args.engine == "xyce-fd":
        if not FD_CARD.is_file() or not FD_LIBRARY.is_file():
            parser.error("FD bridge card/selector is missing")
        xyce = _load_xyce()
        if not xyce.available():
            parser.error("Xyce not found at %s" % xyce.XYCE)
    if shutil.which("klayout") is None:
        parser.error("klayout executable is required on PATH")
    try:
        import klayout.db  # noqa: F401  (PEX runs in this interpreter)
    except ImportError:
        parser.error("this interpreter cannot import klayout.db; run inside the "
                     "repository Nix devShell")
    try:
        if args.work_dir is None:
            with tempfile.TemporaryDirectory(prefix="icps-contact-audit-") as temporary:
                report = run_audit(Path(temporary), args.ngspice, args.step_V,
                                   engine=args.engine)
        else:
            work_dir = args.work_dir.expanduser().resolve()
            work_dir.mkdir(parents=True, exist_ok=True)
            report = run_audit(work_dir, args.ngspice, args.step_V,
                               engine=args.engine)
    except (AuditError, OSError, subprocess.SubprocessError,
            REGRESS.RegressionError) as error:
        print("audit_mos_contact_ownership: %s" % error, file=sys.stderr)
        return 1
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "polarities": sorted(report["polarities"]),
        "output_json": str(args.output_json),
        "output_md": str(args.output_md),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
