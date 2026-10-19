#!/usr/bin/env python3
"""Compare the frozen nominal BSIMSOI cards with digitized PDK I-V samples.

The manifest (`fit/b4soi_stage1_baseline.json`) freezes whichever card is
currently nominal and its expected per-curve metrics.  The file and tool keep
their historical "stage1" names, but since 2026-10-17 the nominal card is the
verified bridge fit (SOIMOD=1); the superseded stage-1 card and its manifest
are preserved verbatim as `models/stage1_b4soi_reference.inc` and
`fit/stage1_b4soi_baseline.json` for the historical audits.

The expected selector is read from the manifest rather than hard-coded, so the
check still fails on silent drift.
"""

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL_CARD = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
MEASUREMENTS = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_iv_digitized.csv"
BASELINE = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_baseline.json"
REQUIRED_MODELS = {"nch", "pch"}
MODEL_RE = re.compile(r"(?is)\.model\s+(\w+)\s+\w+\s*\((.*?)\)")
PARAMETER_RE = re.compile(
    r"(?i)([a-z][a-z0-9_]*)\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)"
)


class RegressionError(ValueError):
    pass


def model_parameters(path):
    models = {}
    text = Path(path).read_text(encoding="utf-8")
    for match in MODEL_RE.finditer(text):
        name, body = match.groups()
        models[name.casefold()] = {
            key.upper(): float(value)
            for key, value in PARAMETER_RE.findall(body)
        }
    return models


def read_curves(path):
    curves = defaultdict(list)
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        required = {
            "device", "family", "curve", "bias_axis", "bias_V",
            "axis_V", "Id_magnitude_A",
        }
        if not required <= set(reader.fieldnames or ()):
            raise RegressionError("digitized CSV is missing required columns")
        for row in reader:
            device = row["device"].casefold()
            family = row["family"].casefold()
            bias_axis = row["bias_axis"].upper()
            expected_bias_axis = "VGS" if family == "output" else "VDS"
            if device not in ("nmos", "pmos") or family not in ("output", "transfer"):
                raise RegressionError("unsupported digitized curve: %r" % row)
            if bias_axis != expected_bias_axis:
                raise RegressionError("unexpected bias axis in digitized curve: %r" % row)
            key = (device, family, row["curve"], bias_axis, float(row["bias_V"]))
            axis = float(row["axis_V"])
            current = float(row["Id_magnitude_A"])
            if not math.isfinite(axis) or not math.isfinite(current) or current <= 0.0:
                raise RegressionError("invalid digitized I-V sample: %r" % row)
            curves[key].append((axis, current))
    for key, points in curves.items():
        points.sort()
        if len({axis for axis, _current in points}) != len(points):
            raise RegressionError("duplicate axis sample in curve %s" % (key[2],))
    return curves


def verify_frozen_inputs(baseline, model_path, measurements_path):
    digest = hashlib.sha256(Path(measurements_path).read_bytes()).hexdigest()
    if digest != baseline.get("measurements_sha256"):
        raise RegressionError("digitized I-V dataset hash differs from frozen baseline")

    models = model_parameters(model_path)
    frozen_models = baseline.get("models", {})
    if set(models) != set(frozen_models) or set(frozen_models) != REQUIRED_MODELS:
        raise RegressionError("model names differ from the frozen BSIMSOI cards")
    for model, expected in frozen_models.items():
        actual = models[model]
        if set(actual) != set(expected):
            raise RegressionError("model parameters differ from the frozen %s card" % model)
        for parameter, expected_value in expected.items():
            if actual[parameter] != float(expected_value):
                raise RegressionError(
                    "frozen %s.%s changed: expected %s, got %s"
                    % (model, parameter, expected_value, actual[parameter])
                )
        expected_soimod = float(baseline.get("expected_soimod", 0))
        if actual.get("SOIMOD") != expected_soimod:
            raise RegressionError(
                "nominal SOIMOD changed: expected %g, got %s"
                % (expected_soimod, actual.get("SOIMOD"))
            )


def format_number(value):
    return "%.12g" % value


def make_sweep_deck(key, points, step, library_path):
    device, family, _curve_name, bias_axis, bias = key
    model = "nch" if device == "nmos" else "pch"
    axis_source = "VDS" if family == "output" else "VGS"
    start = math.floor(min(axis for axis, _current in points) / step) * step
    stop = math.ceil(max(axis for axis, _current in points) / step) * step
    if start == stop:
        stop = start + step

    gate_bias = bias if bias_axis == "VGS" else 0.0
    drain_bias = bias if bias_axis == "VDS" else 0.0
    library = Path(library_path).resolve().as_posix().replace('"', '\\"')
    lines = [
        "* Fixed stage-1 model evaluation; no fitting or parameter changes",
        '.lib "%s" b4soi' % library,
        ".options temp=25 tnom=25",
        "VGS gate 0 %s" % format_number(gate_bias),
        "VDS drain 0 %s" % format_number(drain_bias),
        "MTEST drain gate 0 0 %s L=6u W=24u" % model,
        ".control",
        "set wr_vecnames",
        "set wr_singlescale",
        "dc %s %s %s %s" % (
            axis_source, format_number(start), format_number(stop), format_number(step)
        ),
        "wrdata sweep.dat i(VDS)",
        "quit",
        ".endc",
        ".end",
        "",
    ]
    return "\n".join(lines), start


def read_sweep(path, step, start):
    axis = []
    current = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            values = [float(field.replace("D", "E").replace("d", "e")) for field in fields]
        except ValueError:
            continue
        if len(values) >= 2:
            axis.append(values[0])
            current.append(abs(values[-1]))
        elif len(values) == 1:
            axis.append(start + len(axis) * step)
            current.append(abs(values[0]))
    if len(axis) < 2:
        raise RegressionError("ngspice sweep output is missing numeric samples")
    if any(not math.isfinite(value) for value in axis + current):
        raise RegressionError("ngspice produced a non-finite sweep value")
    if any(right <= left for left, right in zip(axis, axis[1:])):
        raise RegressionError("ngspice sweep axis is not strictly increasing")
    if any(value <= 0.0 for value in current):
        raise RegressionError("ngspice produced zero/non-positive current at a data point")
    return axis, current


def interpolate(axis, values, target):
    index = bisect.bisect_left(axis, target)
    if index < len(axis) and math.isclose(axis[index], target, rel_tol=0.0, abs_tol=1e-12):
        return values[index]
    if index == 0 or index == len(axis):
        raise RegressionError("measurement axis lies outside ngspice sweep")
    fraction = (target - axis[index - 1]) / (axis[index] - axis[index - 1])
    return values[index - 1] + fraction * (values[index] - values[index - 1])


def evaluate_curves(curves, ngspice, model_library, step):
    result = {}
    with tempfile.TemporaryDirectory(prefix="icps-b4soi-iv-") as tempdir:
        work = Path(tempdir)
        for key, points in sorted(curves.items()):
            name = key[2]
            deck_path = work / (name + ".cir")
            data_path = work / "sweep.dat"
            deck, start = make_sweep_deck(key, points, step, model_library)
            deck_path.write_text(deck, encoding="utf-8")
            run = subprocess.run(
                [ngspice, "-b", str(deck_path)],
                cwd=work,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if run.returncode != 0 or not data_path.is_file():
                raise RegressionError(
                    "ngspice failed for %s:\n%s\n%s"
                    % (name, run.stdout, run.stderr)
                )
            sim_axis, sim_current = read_sweep(data_path, step, start)
            predicted = [interpolate(sim_axis, sim_current, axis) for axis, _ in points]
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
            result[name] = {
                "device": key[0],
                "family": key[1],
                "bias_axis": key[3],
                "bias_V": key[4],
                "samples": len(points),
                metric_name: error,
            }
    return result


def compare_metrics(metrics, baseline):
    expected = baseline.get("curves", {})
    if set(metrics) != set(expected):
        raise RegressionError(
            "curve set differs from frozen baseline: current=%s baseline=%s"
            % (sorted(metrics), sorted(expected))
        )
    for name, actual in metrics.items():
        reference = expected[name]
        if actual["samples"] != reference["samples"]:
            raise RegressionError("sample count changed for %s" % name)
        metric_name = (
            "relative_rms_percent" if actual["family"] == "output"
            else "log10_current_rms_decades"
        )
        tolerance = baseline["metric_absolute_tolerance"][metric_name]
        if abs(actual[metric_name] - float(reference[metric_name])) > tolerance:
            raise RegressionError(
                "%s drifted: baseline=%g current=%g tolerance=%g"
                % (name, reference[metric_name], actual[metric_name], tolerance)
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"),
                        help="ngspice executable (default: PATH)")
    parser.add_argument("--step-V", type=float, default=0.001,
                        help="maximum DC sweep step before interpolation")
    parser.add_argument("--compute-only", action="store_true",
                        help="report errors without comparing the frozen metric snapshot")
    args = parser.parse_args()
    if not args.ngspice:
        parser.error("ngspice is required")
    if not math.isfinite(args.step_V) or args.step_V <= 0.0:
        parser.error("--step-V must be finite and positive")

    try:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        verify_frozen_inputs(baseline, MODEL_CARD, MEASUREMENTS)
        curves = read_curves(MEASUREMENTS)
        if len(curves) != baseline["curve_count"]:
            raise RegressionError("digitized I-V curve count differs from frozen baseline")
        if sum(len(points) for points in curves.values()) != baseline["sample_count"]:
            raise RegressionError("digitized I-V sample count differs from frozen baseline")
        metrics = evaluate_curves(curves, args.ngspice, MODEL_LIBRARY, args.step_V)
        if not args.compute_only:
            compare_metrics(metrics, baseline)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("BSIMSOI IV regression failed: %s" % error, file=sys.stderr)
        return 1

    print(json.dumps({
        "curve_count": len(metrics),
        "sample_count": sum(curve["samples"] for curve in metrics.values()),
        "sweep_step_V": args.step_V,
        "metric_weighting": "unweighted digitized sample points",
        "curves": metrics,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
