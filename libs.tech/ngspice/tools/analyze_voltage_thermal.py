#!/usr/bin/env python3
"""Analyze actual MOS terminal histories and explicit first-order thermal screens.

Input is JSON naming a selected assembled SPICE network, real terminal-wave CSVs,
and optionally an IR ``thermal_input`` export. This is a research screening tool;
it never supplies device ratings, junction calibration, or manufacturing approval.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

try:
    from . import reliability_netlist, waveint
except ImportError:  # direct CLI execution from this tools directory
    import reliability_netlist
    import waveint


class VoltageThermalError(ValueError):
    """The supplied waveform or electrothermal contract is incomplete/invalid."""


_METRICS = {
    "VGS": ("g", "s"),
    "VGD": ("g", "d"),
    "VDS": ("d", "s"),
    "VGE": ("g", "e"),
    "VPS": ("p", "s"),
    "VPE": ("p", "e"),
    "VGP": ("g", "p"),
}


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise VoltageThermalError("%s must be a finite number" % label)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise VoltageThermalError("%s must be a finite number" % label) from None
    if not math.isfinite(result):
        raise VoltageThermalError("%s must be a finite number" % label)
    return result


def _positive(value: Any, label: str) -> float:
    result = _finite(value, label)
    if result <= 0:
        raise VoltageThermalError("%s must be positive" % label)
    return result


def _window(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise VoltageThermalError("%s must be [start_s, end_s]" % label)
    start, end = (_finite(value[0], label), _finite(value[1], label))
    if end <= start:
        raise VoltageThermalError("%s must have positive duration" % label)
    return start, end


def _casefold_map(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VoltageThermalError("%s must be an object" % label)
    result = {}
    for key, item in value.items():
        folded = str(key).casefold()
        if folded in result:
            raise VoltageThermalError("%s contains case-insensitive duplicate key %r" % (label, key))
        result[folded] = item
    return result


def _resolve(base_dir: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise VoltageThermalError("%s must be a nonempty path" % label)
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _read_csv(path: Path, time_column: str, column_map: dict[str, str]
              ) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, str]]:
    try:
        handle = path.open(newline="", encoding="utf-8-sig")
    except OSError as error:
        raise VoltageThermalError("cannot read waveform CSV %s: %s" % (path, error)) from error
    with handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise VoltageThermalError("waveform CSV %s has no header" % path)
        headers: dict[str, str] = {}
        for header in reader.fieldnames:
            if header is None:
                continue
            folded = header.strip().casefold()
            if folded in headers:
                raise VoltageThermalError("CSV %s has ambiguous case-insensitive header %r" % (path, header))
            headers[folded] = header
        if time_column.casefold() not in headers:
            raise VoltageThermalError("CSV %s has no explicitly mapped time column %r" % (path, time_column))
        resolved: dict[str, str] = {}
        for node, column in column_map.items():
            if not isinstance(column, str) or column.casefold() not in headers:
                raise VoltageThermalError("CSV %s has no column %r mapped to node %r" % (path, column, node))
            resolved[node.casefold()] = headers[column.casefold()]
        rows = list(reader)
    if not rows:
        raise VoltageThermalError("waveform CSV %s has no samples" % path)
    times = np.empty(len(rows), dtype=float)
    values = {node: np.empty(len(rows), dtype=float) for node in resolved}
    for index, row in enumerate(rows):
        try:
            times[index] = _finite(row[headers[time_column.casefold()]], "CSV time row %d" % (index + 2))
            for node, header in resolved.items():
                values[node][index] = _finite(row[header], "CSV %s row %d" % (header, index + 2))
        except (KeyError, TypeError) as error:
            raise VoltageThermalError("malformed CSV row %d in %s: %s" % (index + 2, path, error)) from error
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise VoltageThermalError("CSV %s needs at least two strictly increasing time samples" % path)
    original_headers = {node: resolved[node] for node in resolved}
    return times, values, original_headers


def _violation_intervals(times: np.ndarray, values: np.ndarray,
                         lower: float | None, upper: float | None,
                         window: tuple[float, float]) -> list[dict[str, float]]:
    """Find exact crossing-bounded exceedance spans for piecewise-linear data."""
    wa, wb = window
    start, end = max(wa, float(times[0])), min(wb, float(times[-1]))
    if end <= start:
        return []
    knots = [start, end]
    knots.extend(float(t) for t in times if start < t < end)
    for index in range(len(times) - 1):
        t0, t1 = float(times[index]), float(times[index + 1])
        if t1 <= start or t0 >= end:
            continue
        v0, v1 = float(values[index]), float(values[index + 1])
        if v0 == v1:
            continue
        for bound in (lower, upper):
            if bound is not None and min(v0, v1) < bound < max(v0, v1):
                crossing = t0 + (bound - v0) * (t1 - t0) / (v1 - v0)
                if start < crossing < end:
                    knots.append(crossing)
    knots = sorted(set(knots))
    spans: list[list[float]] = []
    for a, b in zip(knots, knots[1:]):
        midpoint = (a + b) / 2.0
        sample = float(np.interp(midpoint, times, values))
        outside = ((lower is not None and sample < lower)
                   or (upper is not None and sample > upper))
        if outside:
            if spans and spans[-1][1] == a:
                spans[-1][1] = b
            else:
                spans.append([a, b])
    ones = np.ones(len(times), dtype=float)
    return [{"start_s": a, "end_s": b,
             "duration_s": waveint.integral(times, ones, a, b).net}
            for a, b in spans]


def _limit_record(limits: dict[str, Any], metric: str) -> dict[str, Any] | None:
    record = limits.get(metric.casefold())
    if record is None:
        return None
    if not isinstance(record, dict):
        raise VoltageThermalError("limit for %s must be an object" % metric)
    lower = record.get("min_v")
    upper = record.get("max_v")
    if lower is None and upper is None:
        raise VoltageThermalError("limit for %s requires min_v and/or max_v" % metric)
    result: dict[str, Any] = {}
    if lower is not None:
        result["min_v"] = _finite(lower, "%s min_v" % metric)
    if upper is not None:
        result["max_v"] = _finite(upper, "%s max_v" % metric)
    if lower is not None and upper is not None and result["min_v"] > result["max_v"]:
        raise VoltageThermalError("limit for %s has min_v greater than max_v" % metric)
    source = record.get("source")
    qualified = record.get("qualified") is True
    result["source"] = source if isinstance(source, str) and source.strip() else None
    result["qualified"] = qualified and result["source"] is not None
    return result


def _scenario_report(network: dict[str, Any], scenario: dict[str, Any],
                     limits: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    name = scenario.get("name")
    if not isinstance(name, str) or not name.strip():
        raise VoltageThermalError("every scenario needs a nonempty name")
    csv_path = _resolve(base_dir, scenario.get("csv"), "scenario %s csv" % name)
    time_column = scenario.get("time_column")
    if not isinstance(time_column, str) or not time_column:
        raise VoltageThermalError("scenario %s requires an explicit time_column" % name)
    window = _window(scenario.get("window_s"), "scenario %s window_s" % name)
    raw_map = scenario.get("column_map", scenario.get("node_columns"))
    if not isinstance(raw_map, dict):
        raise VoltageThermalError("scenario %s requires an explicit column_map from SPICE nodes to CSV columns" % name)
    column_map = {}
    for node, column in raw_map.items():
        folded = str(node).casefold()
        if folded in column_map:
            raise VoltageThermalError("scenario %s has duplicate mapped node %r" % (name, node))
        column_map[folded] = column
    network_nodes = {str(node).casefold() for node in network.get("nodes", [])}
    unknown_nodes = set(column_map) - network_nodes
    if unknown_nodes:
        raise VoltageThermalError("scenario %s column_map names nodes absent from selected network: %s" %
                                  (name, ", ".join(sorted(unknown_nodes))))
    times, node_values, resolved_headers = _read_csv(csv_path, time_column, column_map)
    a, b = window
    covered_start, covered_end = max(a, float(times[0])), min(b, float(times[-1]))
    coverage = {
        "requested_window_s": [a, b],
        "data_window_s": [float(times[0]), float(times[-1])],
        "covered_window_s": ([covered_start, covered_end] if covered_end > covered_start else None),
        "full_window_coverage": bool(times[0] <= a and times[-1] >= b),
    }
    folded_limits = _casefold_map(limits, "limits")
    unknown_limits = set(folded_limits) - {metric.casefold() for metric in _METRICS}
    if unknown_limits:
        raise VoltageThermalError("limits name unsupported terminal metrics: %s" %
                                  ", ".join(sorted(unknown_limits)))
    output_devices = []
    for device in network.get("mos", []):
        terminals = {key: device.get(key) for key in ("d", "g", "s", "e", "p")}
        terminal_nodes = {key: (str(value).casefold() if value is not None else None)
                          for key, value in terminals.items()}
        metrics = {}
        for metric, pair in _METRICS.items():
            if "p" in pair and terminal_nodes["p"] is None:
                continue
            first, second = pair
            node_a, node_b = terminal_nodes[first], terminal_nodes[second]
            missing = []
            if node_a is None:
                missing.append(first.upper() + " terminal absent")
            elif node_a not in node_values:
                missing.append("node %s has no explicit waveform column" % terminals[first])
            if node_b is None:
                missing.append(second.upper() + " terminal absent")
            elif node_b not in node_values:
                missing.append("node %s has no explicit waveform column" % terminals[second])
            if missing:
                metrics[metric] = {
                    "status": "UNASSESSED", "reason": "; ".join(missing),
                    "required_nodes": [terminals[first], terminals[second]],
                    "history": None, "extrema": None, "limit": None,
                }
                continue
            volts = node_values[node_a] - node_values[node_b]
            min_index, max_index = int(np.argmin(volts)), int(np.argmax(volts))
            summary: dict[str, Any] = {
                "status": "ASSESSED_NUMERICALLY",
                "polarity": "%s-%s" % (first.upper(), second.upper()),
                "required_nodes": [terminals[first], terminals[second]],
                "columns": [resolved_headers[node_a], resolved_headers[node_b]],
                "history": {"time_s": times.tolist(), "voltage_v": volts.tolist()},
                "extrema": {
                    "min_v": float(volts[min_index]), "min_time_s": float(times[min_index]),
                    "max_v": float(volts[max_index]), "max_time_s": float(times[max_index]),
                },
                "full_window_coverage": coverage["full_window_coverage"],
            }
            bound = _limit_record(folded_limits, metric)
            if bound is None:
                summary.update({"limit": None, "limit_status": "UNASSESSED_NO_LIMIT",
                                "violations": None})
            else:
                lower, upper = bound.get("min_v"), bound.get("max_v")
                violation = _violation_intervals(times, volts, lower, upper, window)
                outside_duration = sum(item["duration_s"] for item in violation)
                qualified = bound["qualified"]
                if coverage["full_window_coverage"]:
                    assessment = ("FAIL" if violation else "PASS") if qualified else "UNQUALIFIED_LIMIT_INPUT"
                else:
                    assessment = "INCOMPLETE_WINDOW_COVERAGE"
                summary.update({
                    "limit": bound,
                    "limit_status": assessment,
                    "violations": violation,
                    "outside_duration_s": (outside_duration if covered_end > covered_start else None),
                    "violation_duration_fraction_of_covered_window": (
                        outside_duration / (covered_end - covered_start)
                        if covered_end > covered_start else None
                    ),
                    "violation_duration_is_full_window": coverage["full_window_coverage"],
                })
            metrics[metric] = summary
        output_devices.append({
            "name": device.get("name"), "model": device.get("model"),
            "terminals": terminals, "body_state": device.get("body_state"),
            "layout_device": device.get("layout_device"), "metrics": metrics,
        })
    return {"name": name, "csv": str(csv_path), "time_column": time_column,
            "column_map": raw_map, "coverage": coverage, "devices": output_devices}


def _thermal_source(config: dict[str, Any], base_dir: Path) -> dict[str, Any] | None:
    inline = config.get("thermal_input")
    file_value = config.get("thermal_input_file")
    if inline is not None and file_value is not None:
        raise VoltageThermalError("use either thermal_input or thermal_input_file, not both")
    if inline is not None:
        source = inline
    elif file_value is not None:
        path = _resolve(base_dir, file_value, "thermal_input_file")
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VoltageThermalError("cannot read thermal input JSON %s: %s" % (path, error)) from error
        if isinstance(source, dict) and "thermal_input" in source:
            source = source["thermal_input"]
    else:
        return None
    if not isinstance(source, dict):
        raise VoltageThermalError("thermal_input must be an object")
    return source


def _numeric_series(source: dict[str, Any]) -> tuple[np.ndarray, list[dict[str, Any]], np.ndarray | None]:
    if not isinstance(source.get("times_s"), list) or not source["times_s"]:
        raise VoltageThermalError("thermal_input.times_s must contain at least one sample")
    times = np.asarray([_finite(value, "thermal_input time") for value in source["times_s"]], dtype=float)
    if np.any(np.diff(times) <= 0):
        raise VoltageThermalError("thermal_input.times_s must be strictly increasing")
    raw_branches = source.get("branches")
    if not isinstance(raw_branches, list):
        raise VoltageThermalError("thermal_input.branches must be an array")
    branches = []
    names = set()
    for raw in raw_branches:
        if not isinstance(raw, dict):
            raise VoltageThermalError("each thermal branch must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise VoltageThermalError("each thermal branch needs a name")
        if name.casefold() in names:
            raise VoltageThermalError("duplicate thermal branch name %s" % name)
        names.add(name.casefold())
        resistance = _positive(raw.get("resistance_ohm"), "branch %s resistance_ohm" % name)
        current_raw = raw.get("current_a")
        if not isinstance(current_raw, list) or len(current_raw) != len(times):
            raise VoltageThermalError("branch %s current_a must match thermal_input.times_s" % name)
        current = np.asarray([_finite(value, "branch %s current" % name) for value in current_raw], dtype=float)
        material = raw.get("material")
        branches.append({"name": name, "material": material, "resistance_ohm": resistance,
                         "current_a": current})
    power_raw = source.get("device_power_w")
    if power_raw is None:
        device_power = None
    else:
        if not isinstance(power_raw, list) or len(power_raw) != len(times):
            raise VoltageThermalError("device_power_w must match thermal_input.times_s or be null")
        device_power = np.asarray([_finite(value, "device_power_w") for value in power_raw], dtype=float)
        if np.any(device_power < 0):
            raise VoltageThermalError("dissipative device_power_w cannot be negative")
    return times, branches, device_power


def _values(thermal: dict[str, Any], name: str, positive: bool = False,
            nonnegative: bool = False) -> list[float]:
    raw = thermal.get(name)
    if isinstance(raw, (list, tuple)):
        if not raw:
            raise VoltageThermalError("thermal.%s sensitivity list cannot be empty" % name)
        items = list(raw)
    else:
        items = [raw]
    if positive:
        return [_positive(item, "thermal.%s" % name) for item in items]
    values = [_finite(item, "thermal.%s" % name) for item in items]
    if nonnegative and any(item < 0 for item in values):
        raise VoltageThermalError("thermal.%s values must be nonnegative" % name)
    return values


def _ownership_state(network: dict[str, Any], thermal: dict[str, Any],
                     device_power: np.ndarray | None
                     ) -> tuple[bool, list[str], np.ndarray | None, dict[str, Any]]:
    """Establish heat ownership without interpreting supply power as MOS dissipation."""
    reasons = []
    ownership = thermal.get("power_ownership")
    if not isinstance(ownership, dict):
        reasons.append("explicit power_ownership is missing; MOS heat and SHMOD ownership are unknown")
        unknown_shmod = {str(device["name"]): "unknown" for device in network.get("mos", [])}
        reasons.extend("SHMOD unknown for %s" % name for name in unknown_shmod)
        return False, reasons, None, {
            "status": "UNASSESSED", "external_mos_devices": [],
            "internal_selfheat_devices": [], "excluded_devices": [],
            "shmod_by_device": unknown_shmod, "external_power_source": None,
        }
    external_raw = ownership.get("external_mos_devices", [])
    internal_raw = ownership.get("internal_selfheat_devices", [])
    excluded_raw = ownership.get("excluded_devices", [])
    for key, value in (("external_mos_devices", external_raw),
                       ("internal_selfheat_devices", internal_raw), ("excluded_devices", excluded_raw)):
        if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
            raise VoltageThermalError("power_ownership.%s must be an array of MOS names" % key)
    external = {name.casefold() for name in external_raw}
    internal = {name.casefold() for name in internal_raw}
    excluded = {name.casefold() for name in excluded_raw}
    if external & internal or external & excluded or internal & excluded:
        raise VoltageThermalError("a MOS device cannot have multiple thermal power owners")
    shmod_raw = ownership.get("shmod_by_device", {})
    shmod = _casefold_map(shmod_raw, "power_ownership.shmod_by_device")
    mos = network.get("mos", [])
    device_names = {str(device["name"]).casefold() for device in mos}
    unknown_names = (external | internal | excluded) - device_names
    if unknown_names:
        raise VoltageThermalError("power ownership names do not match network MOS devices: %s" %
                                  ", ".join(sorted(unknown_names)))
    unowned = device_names - external - internal - excluded
    if unowned:
        reasons.append("MOS power ownership missing for: %s" % ", ".join(sorted(unowned)))
    shmod_status = {}
    for device in mos:
        folded = str(device["name"]).casefold()
        raw_state = shmod.get(folded, "unknown")
        state = str(raw_state).casefold()
        if state not in {"enabled", "disabled", "unknown"}:
            raise VoltageThermalError("SHMOD state for %s must be enabled, disabled, or unknown" % device["name"])
        shmod_status[device["name"]] = state
        if state == "unknown":
            reasons.append("SHMOD unknown for %s" % device["name"])
        if folded in external and state == "enabled":
            raise VoltageThermalError("external power for %s duplicates enabled BSIMSOI SHMOD" % device["name"])
        if folded in internal and state == "disabled":
            raise VoltageThermalError("internal self-heating ownership for %s conflicts with SHMOD disabled" % device["name"])
    usable_device_power = None
    provenance = ownership.get("external_power_source")
    if external:
        if device_power is None:
            reasons.append("externally owned MOS power is missing (device_power_w is null)")
        elif not isinstance(provenance, dict):
            reasons.append("external MOS power has no dissipative-source provenance")
        else:
            quantity = provenance.get("quantity")
            source_name = provenance.get("source")
            source_devices = provenance.get("devices")
            if quantity != "dissipative_mos_power":
                raise VoltageThermalError("external MOS heat must be explicitly partitioned dissipative_mos_power; supply power is not valid")
            if not isinstance(source_name, str) or not source_name.strip():
                raise VoltageThermalError("external_power_source.source must identify the actual dissipative waveform")
            if not isinstance(source_devices, list) or {str(name).casefold() for name in source_devices} != external:
                raise VoltageThermalError("external_power_source.devices must match external_mos_devices exactly")
            if any(shmod.get(name, "unknown").casefold() != "disabled" for name in external):
                reasons.append("external MOS heat cannot be combined while SHMOD is enabled or unknown")
            else:
                usable_device_power = device_power
    elif device_power is not None:
        reasons.append("device_power_w is not used: no MOS devices were assigned external power ownership")
    if internal:
        reasons.append("internally self-heated MOS power is outside this external lumped thermal model")
    if excluded:
        reasons.append("explicitly excluded MOS devices are outside the thermal power total: %s" %
                       ", ".join(sorted(excluded)))
    if not mos and device_power is not None:
        reasons.append("device_power_w is not associated with any MOS device ownership")
    complete = (not reasons and (not mos or external == device_names) and
                (not external or usable_device_power is not None))
    status = "ASSESSED" if complete else "UNASSESSED"
    return complete, reasons, usable_device_power, {
        "status": status, "external_mos_devices": sorted(external),
        "internal_selfheat_devices": sorted(internal), "excluded_devices": sorted(excluded),
        "shmod_by_device": shmod_status,
        "external_power_source": provenance if usable_device_power is not None else None,
    }


def _interp(values: np.ndarray, times: np.ndarray, at: float) -> float:
    return float(np.interp(at, times, values))


def _integrate_thermal(times: np.ndarray, branches: list[dict[str, Any]],
                       device_power: np.ndarray | None, window: tuple[float, float],
                       rth: float, cth: float, ambient: float, tempco: float,
                       load_scale: float, initial_delta: float, max_step: float,
                       ownership_complete: bool, ownership: dict[str, Any]) -> dict[str, Any]:
    start, end = window
    if start < times[0] or end > times[-1]:
        return {"status": "UNASSESSED", "reason": "thermal source does not cover the full integration window",
                "full_window_coverage": False, "steady_delta_t_k": None,
                "steady_status": "UNASSESSED"}
    if len(times) < 2:
        return {"status": "UNASSESSED", "reason": "thermal source has fewer than two time samples",
                "full_window_coverage": False, "steady_delta_t_k": None,
                "steady_status": "UNASSESSED"}
    source_grid = np.concatenate(([start], times[(times > start) & (times < end)], [end]))
    response_times = [float(start)]
    delta_values = [initial_delta]
    feedback_gains = []
    for interval in range(len(source_grid) - 1):
        left, right = float(source_grid[interval]), float(source_grid[interval + 1])
        count = max(1, int(math.ceil((right - left) / max_step)))
        step = (right - left) / count
        state = float(delta_values[-1])
        for substep in range(count):
            at = left + (substep + 0.5) * step
            resistive_w = 0.0
            feedback_w_per_k = 0.0
            for branch in branches:
                current = _interp(branch["current_a"], times, at) * load_scale
                base_w = current * current * branch["resistance_ohm"]
                resistive_w += base_w
                feedback_w_per_k += base_w * tempco
            mos_w = (_interp(device_power, times, at) * load_scale
                     if device_power is not None else 0.0)
            coefficient = (feedback_w_per_k - 1.0 / rth) / cth
            forcing = (resistive_w + mos_w) / cth
            feedback_gains.append(rth * feedback_w_per_k)
            exponent = coefficient * step
            if exponent > 700:
                return {"status": "UNSTABLE", "reason": "thermal positive feedback overflowed the response",
                        "full_window_coverage": True, "steady_delta_t_k": None,
                        "steady_status": "UNSTABLE", "maximum_feedback_gain": max(feedback_gains)}
            if abs(coefficient) < 1e-15:
                midpoint_state = state + forcing * step / 2.0
                state = state + forcing * step
            else:
                midpoint_exponent = exponent / 2.0
                midpoint_state = (state * math.exp(midpoint_exponent)
                                  + forcing * math.expm1(midpoint_exponent) / coefficient)
                state = (state * math.exp(exponent)
                         + forcing * math.expm1(exponent) / coefficient)
            if not math.isfinite(state) or not math.isfinite(midpoint_state):
                return {"status": "UNSTABLE", "reason": "thermal response is non-finite under positive feedback",
                        "full_window_coverage": True, "steady_delta_t_k": None,
                        "steady_status": "UNSTABLE", "maximum_feedback_gain": max(feedback_gains)}
            step_end = right if substep == count - 1 else left + (substep + 1) * step
            response_times.extend((at, step_end))
            delta_values.extend((midpoint_state, state))
    grid = np.asarray(response_times, dtype=float)
    delta = np.asarray(delta_values, dtype=float)
    if np.any(ambient + delta <= 0):
        raise VoltageThermalError("computed absolute temperature is nonpositive")
    resistance_power = np.zeros(len(grid), dtype=float)
    branch_report = []
    for branch in branches:
        resistance = branch["resistance_ohm"] * (1.0 + tempco * delta)
        if np.any(resistance <= 0) or not np.all(np.isfinite(resistance)):
            raise VoltageThermalError("temperature coefficient makes branch %s resistance nonpositive" % branch["name"])
        current = np.interp(grid, times, branch["current_a"]) * load_scale
        power = current * current * resistance
        resistance_power += power
        ratio = resistance / branch["resistance_ohm"]
        branch_report.append({
            "name": branch["name"], "material": branch["material"],
            "base_resistance_ohm": branch["resistance_ohm"],
            "resistance_at_peak_temperature_ohm": float(resistance[int(np.argmax(delta))]),
            "resistance_drift_fraction_min": float(np.min(ratio) - 1.0),
            "resistance_drift_fraction_max": float(np.max(ratio) - 1.0),
            "resistance_history_ohm": resistance.tolist(),
        })
    used_device_power = (np.interp(grid, times, device_power) * load_scale
                         if device_power is not None else None)
    total_power = resistance_power + (used_device_power if used_device_power is not None else 0.0)
    resistor_energy = waveint.integral(grid, resistance_power, start, end).net
    device_energy = (waveint.integral(grid, used_device_power, start, end).net
                     if used_device_power is not None else None)
    total_energy = (resistor_energy + (device_energy or 0.0)
                    if ownership_complete else None)
    peak = int(np.argmax(delta))
    maximum_gain = max(feedback_gains, default=0.0)
    unstable = maximum_gain >= 1.0
    window_currents = [
        np.interp(grid, times, branch["current_a"]) * load_scale
        for branch in branches
    ]
    constant_input = all(
        np.allclose(current * current, current[0] * current[0],
                    rtol=1e-12, atol=1e-30)
        for current in window_currents
    )
    window_device_power = (np.interp(grid, times, device_power) * load_scale
                           if device_power is not None else None)
    if window_device_power is not None:
        constant_input = constant_input and np.allclose(
            window_device_power, window_device_power[0], rtol=1e-12, atol=1e-15)
    steady = None
    steady_status = "UNDEFINED_TIME_VARYING_INPUT"
    if constant_input:
        steady_resistive = sum(
            float(current[0]) ** 2 * branch["resistance_ohm"]
            for current, branch in zip(window_currents, branches)
        )
        steady_feedback = sum(
            float(current[0]) ** 2 * branch["resistance_ohm"] * tempco
            for current, branch in zip(window_currents, branches)
        )
        steady_device = (float(window_device_power[0])
                         if window_device_power is not None else 0.0)
        denominator = 1.0 / rth - steady_feedback
        if denominator > 0:
            steady = (steady_resistive + steady_device) / denominator
            steady_status = "STABLE"
        else:
            steady_status = "UNSTABLE"
    status = ("UNASSESSED" if not ownership_complete else
              ("UNSTABLE" if unstable else "ASSESSED"))
    response_scope = "total_owned_power" if ownership_complete else "resistor_only_or_partial_power"
    reason = ("MOS heat/SHMOD ownership is incomplete; numerical response is not a total-device thermal prediction"
              if not ownership_complete else
              ("positive electrothermal feedback at sampled loads; no stable steady state"
               if unstable else None))
    return {
        "status": status,
        "reason": reason,
        "response_scope": response_scope,
        "full_window_coverage": True,
        "feedback_mode": "fixed_current_electrothermal_resistance_feedback; electrical currents are not re-solved",
        "numerical_method": "piecewise-linear waveform interpolation; midpoint-frozen linear coefficients with exact exponential update per substep",
        "resistance_temperature_model": "R(T)=R0*(1+alpha*(T-ambient_k)); one shared explicit alpha for supplied branches",
        "max_step_s": max_step,
        "parameters": {"Rth_k_per_w": rth, "Cth_j_per_k": cth, "ambient_k": ambient,
                       "resistance_tempco_per_k": tempco, "resistance_reference_temperature_k": ambient,
                       "load_scale": load_scale, "integration_window_s": [start, end],
                       "initial_delta_t_k": initial_delta, "power_ownership": ownership},
        "junction_temperature_claim": "NONE; uncalibrated lumped thermal response only",
        "feedback_stability": ("UNASSESSED" if not ownership_complete else
                               ("UNSTABLE" if unstable else "STABLE_OVER_SAMPLED_LOADS")),
        "conductor_feedback_stability": "UNSTABLE" if unstable else "STABLE_OVER_SAMPLED_LOADS",
        "maximum_feedback_gain": maximum_gain,
        "steady_delta_t_k": steady,
        "steady_status": steady_status,
        "steady_response_scope": response_scope,
        "pulse_accumulation": {
            "initial_delta_t_k": initial_delta,
            "peak_delta_t_k": float(delta[peak]),
            "peak_time_s": float(grid[peak]),
            "peak_rise_above_initial_k": float(delta[peak] - initial_delta),
            "end_delta_t_k": float(delta[-1]),
            "cooldown_delta_t_k": float(delta[-1] - delta[peak]),
        },
        "energy_j": {"resistor_i2r": resistor_energy,
                     "externally_owned_mos": device_energy,
                     "total_owned": total_energy},
        "branches": branch_report,
        "response": {"time_s": grid.tolist(), "delta_t_k": delta.tolist(),
                      "temperature_k": (ambient + delta).tolist(),
                      "resistor_power_w": resistance_power.tolist(),
                      "externally_owned_device_power_w": (used_device_power.tolist()
                                                           if used_device_power is not None else None),
                      "modeled_power_w": total_power.tolist()},
    }


def _thermal_report(network: dict[str, Any], config: dict[str, Any],
                    base_dir: Path) -> dict[str, Any]:
    thermal = config.get("thermal")
    if thermal is None:
        return {"status": "UNASSESSED", "reason": "no explicit thermal configuration was supplied",
                "calibration": "none", "junction_temperature_claim": "NONE"}
    if not isinstance(thermal, dict):
        raise VoltageThermalError("thermal must be an object")
    source = _thermal_source(config, base_dir)
    if source is None:
        return {"status": "UNASSESSED", "reason": "no actual thermal_input export or branch waveform was supplied",
                "calibration": "none", "junction_temperature_claim": "NONE"}
    times, branches, raw_device_power = _numeric_series(source)
    input_source = ({
        "kind": "file", "path": str(_resolve(base_dir, config["thermal_input_file"], "thermal_input_file"))
    } if config.get("thermal_input_file") is not None else {
        "kind": "inline", "path": None
    })
    source_evidence = {
        "input": input_source,
        "sample_count": len(times),
        "branch_count": len(branches),
        "device_power_waveform_present": raw_device_power is not None,
    }
    window = _window(thermal.get("window_s"), "thermal.window_s")
    if len(times) < 2:
        return {"status": "UNASSESSED", "reason": "static stress export has no thermal time history",
                "input_provenance": source_evidence,
                "source_window_s": [float(times[0]), float(times[-1])],
                "requested_window_s": list(window), "full_window_coverage": False,
                "calibration": "none", "junction_temperature_claim": "NONE"}
    if window[0] < times[0] or window[1] > times[-1]:
        return {"status": "UNASSESSED", "reason": "thermal source does not cover the full integration window",
                "input_provenance": source_evidence,
                "source_window_s": [float(times[0]), float(times[-1])],
                "requested_window_s": list(window), "full_window_coverage": False,
                "calibration": "none", "junction_temperature_claim": "NONE"}
    ownership_complete, ownership_reasons, device_power, ownership = _ownership_state(
        network, thermal, raw_device_power)
    rth_values = _values(thermal, "Rth_k_per_w", positive=True)
    cth_values = _values(thermal, "Cth_j_per_k", positive=True)
    ambient_values = _values(thermal, "ambient_k", positive=True)
    tempco_values = _values(thermal, "resistance_tempco_per_k")
    load_values = _values(thermal, "load_scale", nonnegative=True)
    if "initial_delta_t_k" not in thermal:
        raise VoltageThermalError("thermal.initial_delta_t_k must be explicit")
    initial_delta = _finite(thermal["initial_delta_t_k"], "thermal.initial_delta_t_k")
    max_step = _positive(thermal.get("max_step_s"), "thermal.max_step_s")
    if initial_delta + min(ambient_values) <= 0:
        raise VoltageThermalError("thermal.initial_delta_t_k produces nonpositive absolute temperature")
    results = []
    parameter_sets = itertools.product(rth_values, cth_values, ambient_values, tempco_values, load_values)
    for rth, cth, ambient, tempco, load_scale in parameter_sets:
        results.append(_integrate_thermal(
            times, branches, device_power, window, rth, cth, ambient, tempco,
            load_scale, initial_delta, max_step, ownership_complete, ownership,
        ))
    result_reasons = list(dict.fromkeys(
        item["reason"] for item in results if item.get("reason")
    ))
    if not ownership_complete or any(item["status"] == "UNASSESSED" for item in results):
        overall = "UNASSESSED"
    elif any(item["status"] == "UNSTABLE" for item in results):
        overall = "UNSTABLE"
    else:
        overall = "ASSESSED"
    reasons = list(dict.fromkeys(ownership_reasons + result_reasons))
    return {
        "status": overall,
        "input_provenance": source_evidence,
        "reason": "; ".join(reasons) if reasons else None,
        "source_window_s": [float(times[0]), float(times[-1])],
        "integration_window_s": list(window), "full_window_coverage": True,
        "power_ownership": ownership,
        "ownership_reasons": list(dict.fromkeys(ownership_reasons)),
        "current_feedback_scope": "source branch currents are treated as fixed actual waveforms; only R(T) and I^2R heat are recomputed",
        "calibration": "none", "junction_temperature_claim": "NONE",
        "delay_trend": "UNASSESSED; no temperature-coupled circuit rerun",
        "leakage_trend": "UNASSESSED; no temperature-coupled circuit rerun",
        "sensitivity_grid": {"Rth_k_per_w": rth_values, "Cth_j_per_k": cth_values,
                             "ambient_k": ambient_values,
                             "resistance_tempco_per_k": tempco_values,
                             "load_scale": load_values},
        "results": results,
    }


def analyze(config: dict[str, Any], base_dir: str | Path | None = None) -> dict[str, Any]:
    """Analyze explicitly mapped terminal CSVs and optional branch electrothermal data."""
    if not isinstance(config, dict):
        raise VoltageThermalError("top-level JSON input must be an object")
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    network_spec = config.get("network")
    if isinstance(network_spec, str):
        network_path = _resolve(base, network_spec, "network")
        top = config.get("top")
    elif isinstance(network_spec, dict):
        network_path = _resolve(base, network_spec.get("path"), "network.path")
        top = network_spec.get("top", config.get("top"))
    else:
        raise VoltageThermalError("network must be a path or {path, top} object")
    try:
        network = reliability_netlist.read_network(network_path, top=top)
    except (OSError, reliability_netlist.ReliabilityNetlistError) as error:
        raise VoltageThermalError("cannot read reliability network: %s" % error) from error
    raw_scenarios = config.get("scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise VoltageThermalError("scenarios must be an array")
    scenario_names = set()
    scenarios = []
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        raise VoltageThermalError("limits must be an object")
    for scenario in raw_scenarios:
        if not isinstance(scenario, dict):
            raise VoltageThermalError("each scenario must be an object")
        name = scenario.get("name")
        if isinstance(name, str) and name.casefold() in scenario_names:
            raise VoltageThermalError("scenario names must be unique case-insensitively")
        if isinstance(name, str):
            scenario_names.add(name.casefold())
        scenarios.append(_scenario_report(network, scenario, limits, base))
    thermal_report = _thermal_report(network, config, base)
    numeric_metrics = [
        metric for scenario in scenarios for device in scenario["devices"]
        for metric in device["metrics"].values() if metric.get("history") is not None
    ]
    has_unassessed_terminals = not numeric_metrics or any(
        metric.get("status") == "UNASSESSED"
        for scenario in scenarios for device in scenario["devices"]
        for metric in device["metrics"].values()
    )
    qualified_limits = bool(numeric_metrics) and all(
        (metric.get("limit") or {}).get("qualified") is True for metric in numeric_metrics
    )
    return {
        "network": {"name": network.get("name"), "source": network.get("source"),
                    "sha256": network.get("sha256")},
        "scenarios": scenarios,
        "terminal_analysis_status": "UNASSESSED" if has_unassessed_terminals else "ASSESSED_NUMERICALLY",
        "limit_input_coverage": "ALL_NUMERIC_TERMINALS_HAVE_QUALIFIED_LIMITS" if qualified_limits else "INCOMPLETE_OR_UNQUALIFIED",
        "thermal": thermal_report,
        "manufacturing_acceptance": "BLOCKED; research reliability screening is not foundry-qualified signoff",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON analysis configuration")
    parser.add_argument("--output", help="write result JSON here (default: stdout)")
    args = parser.parse_args(argv)
    input_path = Path(args.input).resolve()
    try:
        config = json.loads(input_path.read_text(encoding="utf-8"))
        result = analyze(config, base_dir=input_path.parent)
    except (OSError, json.JSONDecodeError, VoltageThermalError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = input_path.parent / output_path
        output_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
