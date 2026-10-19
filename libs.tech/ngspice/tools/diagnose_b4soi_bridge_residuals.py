#!/usr/bin/env python3
"""Decompose BSIMSOI stage-1 I-V residuals into threshold/slope/shape terms.

Motivation: the frozen `models/icps2023_5_b4soi.inc` cards fit only
VTH0/U0/NFACTOR/K1/RDSW and leave every strong-field parameter (UA/UB/UC/
VSAT, PCLM/PDIBLC*) at its ngspice default.  The reported PMOS transfer
residual (0.74/0.88 decades of log10|ID|) is therefore not explained by the
single aggregate metric.  This tool separates, per digitized curve:

* a constant log10|ID| offset (threshold-like misalignment),
* a log10|ID|-vs-VGS slope error (subthreshold-swing misalignment),
* the residual left after removing both (nonlinear shape error),

and, for output curves, the pointwise relative error and its trend with
|VDS| (series-resistance / mobility-degradation signature).

The decomposition is descriptive of the fixed cards.  It fits the residual
model `r ~ a + b*x` over the digitized points; it does not refit or edit any
model parameter, and a small decomposed term is not evidence that the
underlying parameter has the corresponding physical meaning.

Usage:
  python3 libs.tech/ngspice/tools/diagnose_b4soi_bridge_residuals.py \
    --card libs.tech/ngspice/models/icps2023_5_b4soi.inc \
    --out build/b4soi-bridge
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
DEFAULT_CARD = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
FROZEN_LIB = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"

# Subthreshold reference used to report an effective swing in decades/volt.
SUBT1 = 1e-9
SUBT2 = 1e-7


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_card_library(card: Path, work: Path) -> Path:
    """Wrap one .inc card in a self-contained `.lib b4soi` selector."""
    library = work / "diagnose_b4soi.lib.spice"
    library.write_text(
        "* Residual-decomposition scratch selector; explicit card, no defaults injected.\n"
        '.lib b4soi\n.include "%s"\n.endl b4soi\n'
        % Path(card).resolve().as_posix(),
        encoding="utf-8",
    )
    return library


def evaluate(curves, ngspice, library, step, work):
    """Return per-point simulated/measured pairs for every digitized curve."""
    regress = load_module(TOOLS / "regress_b4soi_stage1_iv.py", "b4soi_iv_regression")
    observed = {}
    for key, points in sorted(curves.items()):
        name = key[2]
        deck, start = regress.make_sweep_deck(key, points, step, library)
        deck_path = work / (name + ".cir")
        data_path = work / "sweep.dat"
        data_path.unlink(missing_ok=True)
        deck_path.write_text(deck, encoding="utf-8")
        run = subprocess.run([ngspice, "-b", str(deck_path)], cwd=work, text=True,
                             capture_output=True, check=False, timeout=180)
        if run.returncode != 0 or not data_path.is_file():
            raise RuntimeError("ngspice failed for %s:\n%s\n%s"
                               % (name, run.stdout, run.stderr))
        axis, current = regress.read_sweep(data_path, step, start)
        observed[key] = {
            "axis": np.array([point[0] for point in points]),
            "meas": np.array([point[1] for point in points]),
            "sim": np.array([regress.interpolate(axis, current, point[0])
                             for point in points]),
        }
    return observed


def slope_through(x, y):
    """Least-squares slope/intercept of y on x, plus the residual RMS."""
    design = np.vstack([np.ones_like(x), x]).T
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ coefficients
    return float(coefficients[0]), float(coefficients[1]), float(
        math.sqrt(float(np.mean(residual ** 2))))


def effective_swing(axis, current):
    """Subthreshold swing in decades/volt from points between SUBT1 and SUBT2."""
    mask = (current > SUBT1) & (current < SUBT2)
    if int(mask.sum()) < 3:
        return None
    x = axis[mask]
    y = np.log10(current[mask])
    _intercept, slope, _rms = slope_through(x, y)
    if slope == 0.0:
        return None
    return 1.0 / abs(slope)


def summarise(observed):
    curves = {}
    for key, data in observed.items():
        device, family, name, bias_axis, bias = key
        axis, meas, sim = data["axis"], data["meas"], data["sim"]
        record = {
            "device": device, "family": family, "bias_axis": bias_axis,
            "bias_V": bias, "samples": int(axis.size),
            "measured_swing_dec_per_V": effective_swing(axis, meas),
            "simulated_swing_dec_per_V": effective_swing(axis, sim),
        }
        if family == "transfer":
            residual = np.log10(sim) - np.log10(meas)
            offset, slope, shape = slope_through(axis, residual)
            record.update({
                "metric_log10_current_rms_decades": float(
                    math.sqrt(float(np.mean(residual ** 2)))),
                "log_offset_at_axis0_decades": offset,
                "log_slope_decades_per_V": slope,
                "log_rms_after_offset_slope_decades": shape,
                "swing_mismatch_V_per_decade": (
                    None if (record["measured_swing_dec_per_V"] is None
                             or record["simulated_swing_dec_per_V"] is None)
                    else abs(record["measured_swing_dec_per_V"]
                             - record["simulated_swing_dec_per_V"])
                    / (record["measured_swing_dec_per_V"]
                       * record["simulated_swing_dec_per_V"])),
            })
        else:
            relative = sim / meas - 1.0
            record.update({
                "metric_relative_rms_percent": float(
                    100.0 * math.sqrt(float(np.mean(relative ** 2)))),
                "mean_relative_percent": float(100.0 * float(np.mean(relative))),
                "relative_at_max_abs_axis_percent": float(
                    100.0 * relative[int(np.argmax(np.abs(axis)))]),
                "relative_at_min_abs_axis_percent": float(
                    100.0 * relative[int(np.argmin(np.abs(axis)))]),
            })
        curves[name] = record
    return curves


def render(curves, card, ngspice):
    lines = ["# B4SOI stage-1 residual decomposition", "",
             "- card: `%s`" % Path(card).resolve().relative_to(ROOT),
             "- simulator: `%s`" % ngspice,
             "- residual model: `log10(sim) - log10(meas) ~ a + b*axis`, least squares",
             ""]
    lines += ["## Transfer curves", "",
              "| curve | device | fixed bias | log10 RMS (dec) | offset a (dec) | "
              "slope b (dec/V) | shape RMS (dec) | meas swing (V/dec) | "
              "sim swing (V/dec) |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, record in sorted(curves.items()):
        if record["family"] != "transfer":
            continue
        lines.append(
            "| %s | %s | %+.2f V | %.4f | %+.4f | %+.4f | %.4f | %s | %s |"
            % (name, record["device"].upper(), record["bias_V"],
               record["metric_log10_current_rms_decades"],
               record["log_offset_at_axis0_decades"],
               record["log_slope_decades_per_V"],
               record["log_rms_after_offset_slope_decades"],
               "n/a" if record["measured_swing_dec_per_V"] is None
               else "%.4f" % record["measured_swing_dec_per_V"],
               "n/a" if record["simulated_swing_dec_per_V"] is None
               else "%.4f" % record["simulated_swing_dec_per_V"]))
    lines += ["", "## Output curves", "",
              "| curve | device | fixed bias | relative RMS (%) | mean (%) | "
              "err at max \\|VDS\\| (%) | err at min \\|VDS\\| (%) |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for name, record in sorted(curves.items()):
        if record["family"] != "output":
            continue
        lines.append(
            "| %s | %s | %+.2f V | %.2f | %+.2f | %+.2f | %+.2f |"
            % (name, record["device"].upper(), record["bias_V"],
               record["metric_relative_rms_percent"],
               record["mean_relative_percent"],
               record["relative_at_max_abs_axis_percent"],
               record["relative_at_min_abs_axis_percent"]))
    lines.append("")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"))
    parser.add_argument("--card", default=str(DEFAULT_CARD))
    parser.add_argument("--out", default=str(ROOT / "build/b4soi-bridge"))
    parser.add_argument("--step-V", type=float, default=0.001)
    args = parser.parse_args()
    if not args.ngspice:
        parser.error("ngspice is required")

    regress = load_module(TOOLS / "regress_b4soi_stage1_iv.py", "b4soi_iv_regression")
    curves = regress.read_curves(regress.MEASUREMENTS)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="icps-bridge-diag-") as scratch:
        work = Path(scratch)
        library = write_card_library(Path(args.card), work)
        observed = evaluate(curves, args.ngspice, library, args.step_V, work)

    summary = summarise(observed)
    (out / "residual_decomposition.json").write_text(
        json.dumps({"card": str(Path(args.card).resolve()),
                    "ngspice": args.ngspice, "curves": summary},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = render(summary, args.card, args.ngspice)
    (out / "residual_decomposition.md").write_text("\n".join(lines),
                                                  encoding="utf-8")
    print("\n".join(lines))
    print("wrote %s" % (out / "residual_decomposition.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
