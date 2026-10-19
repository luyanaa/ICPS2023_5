#!/usr/bin/env python3
"""Run evidence-bound post-layout research reliability screens.

All boundaries, waveform mappings, topology anchors, access sensitivities and
thermal parameters are explicit request inputs. This driver does not qualify
the process or provide manufacturing acceptance.
"""
from __future__ import annotations
import argparse

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

try:
    from . import analyze_power_integrity as power_integrity
    from . import analyze_voltage_thermal as voltage_thermal
    from . import check_postlayout_erc as postlayout_erc
    from . import reliability_netlist
except ImportError:  # direct script execution
    import analyze_power_integrity as power_integrity
    import analyze_voltage_thermal as voltage_thermal
    import check_postlayout_erc as postlayout_erc
    import reliability_netlist


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
POLICY_PATH = ROOT / "libs.tech/ngspice/reliability_policy.json"
ARTIFACT_KEYS = ("gds", "lvs", "pex", "assembled")
POLICY_TOPICS = (
    "rc_coverage", "body_backgate", "voltage_limits", "pvt_timing",
    "electromigration", "electrothermal", "antenna", "pad_esd_eos",
)


class ReliabilityError(ValueError):
    """Malformed reliability request or missing immutable qualification policy."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _request(config: Any, config_path: str | Path | None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    request_path = None
    if isinstance(config, (str, Path)):
        if config_path is not None:
            raise ReliabilityError("supply a request path or config_path, not both")
        request_path = Path(config).expanduser().resolve()
        try:
            config = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReliabilityError("cannot read reliability request %s: %s" % (request_path, error)) from error
    elif config_path is not None:
        request_path = Path(config_path).expanduser().resolve()
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ReliabilityError("reliability request root must be an object")
    base_dir = request_path.parent if request_path is not None else Path.cwd()
    request = copy.deepcopy(config)
    request_evidence = None
    if request_path is not None and request_path.is_file():
        request_evidence = {"path": str(request_path), "sha256": _sha256(request_path)}
    return request, base_dir, {"request": request_evidence}


def _policy() -> dict[str, Any]:
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReliabilityError("cannot read immutable reliability policy %s: %s" % (POLICY_PATH, error)) from error
    if (not isinstance(policy, dict) or policy.get("schema_version") != 1 or
            not isinstance(policy.get("topics"), dict) or
            not isinstance(policy.get("manufacturing_acceptance"), dict)):
        raise ReliabilityError("reliability policy has an unsupported or malformed schema")
    missing = [topic for topic in POLICY_TOPICS if topic not in policy["topics"]]
    if missing:
        raise ReliabilityError("reliability policy is missing topics: %s" % ", ".join(missing))
    for topic in POLICY_TOPICS:
        if policy["topics"][topic].get("status") != "BLOCKED":
            raise ReliabilityError("immutable qualification policy topic %s must remain BLOCKED" % topic)
    if (policy["manufacturing_acceptance"].get("status") != "BLOCKED" or
            policy["manufacturing_acceptance"].get("accepted") is not False):
        raise ReliabilityError("immutable manufacturing acceptance policy must remain BLOCKED")
    return policy


def _resolve(base_dir: Path, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ReliabilityError("%s must be a non-empty path" % label)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _path_record(path: Path, *, expected_sha256: Any = None) -> dict[str, Any]:
    exists = path.is_file()
    record: dict[str, Any] = {"path": str(path), "exists": exists}
    if exists:
        record["sha256"] = _sha256(path)
    if expected_sha256 is not None:
        record["expected_sha256"] = expected_sha256
        record["sha256_matches"] = bool(exists and isinstance(expected_sha256, str) and
                                         record["sha256"].casefold() == expected_sha256.casefold())
    return record


def _artifact_value(value: Any, base_dir: Path, label: str) -> tuple[Path, Any]:
    expected_hash = None
    if isinstance(value, dict):
        if set(value) - {"path", "sha256"}:
            raise ReliabilityError("%s artifact accepts only path and optional sha256" % label)
        expected_hash = value.get("sha256")
        value = value.get("path")
    return _resolve(base_dir, value, "%s artifact path" % label), expected_hash


def _artifacts(request: dict[str, Any], base_dir: Path,
               supplied: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, Path], list[str]]:
    configured = request.get("artifacts", {})
    if not isinstance(configured, dict):
        return {}, {}, ["artifacts must map artifact roles to paths"]
    if supplied is not None and not isinstance(supplied, dict):
        return {}, {}, ["physical_artifacts must be a mapping"]
    records: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    errors: list[str] = []
    roles = list(ARTIFACT_KEYS) + ["fab_gds"]
    for role in roles:
        expected = configured.get(role)
        source = supplied.get(role) if supplied is not None and role in supplied else expected
        if source is None:
            records[role] = {"path": None, "exists": False}
            continue
        try:
            path, source_hash = _artifact_value(source, base_dir, role)
            expected_hash = source_hash
            if supplied is not None and expected is not None:
                expected_path, configured_hash = _artifact_value(expected, base_dir, role)
                if expected_path != path:
                    errors.append("configured %s artifact path %s does not match generated physical evidence %s" %
                                  (role, expected_path, path))
                if configured_hash is not None:
                    expected_hash = configured_hash
            record = _path_record(path, expected_sha256=expected_hash)
            if record["exists"] and expected_hash is not None and not record.get("sha256_matches"):
                errors.append("%s artifact SHA-256 does not match the configured evidence" % role)
            records[role] = record
            if record["exists"]:
                paths[role] = path
        except ReliabilityError as error:
            records[role] = {"path": None, "exists": False, "error": str(error)}
            errors.append(str(error))
    return records, paths, errors


def _block(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "BLOCKED", "reason": reason, **extra}


def _status_from_erc(result: dict[str, Any]) -> str:
    status = result.get("status")
    return status if status in ("PASS", "FAIL", "BLOCKED") else "BLOCKED"


def _screen_failures(result: dict[str, Any]) -> list[str]:
    failures = []
    for scenario in result.get("scenarios", []):
        scenario_name = scenario.get("name", "unnamed")
        cases = [scenario.get("baseline") or {}] + scenario.get("access_sensitivity_cases", [])
        for case in cases:
            if not isinstance(case, dict):
                continue
            for branch in case.get("branches", []):
                if branch.get("stress_status") == "EXCEEDS_EXPLICIT_QUALIFIED_LIMIT":
                    failures.append("%s/%s branch %s exceeds its explicitly supplied current screen" %
                                    (scenario_name, case.get("name", "baseline"),
                                     branch.get("name", "unnamed")))
    return failures


def _has_access_sensitivity(result: dict[str, Any], configured_scenarios: list[Any],
                            network: dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    configured_by_name: dict[str, list[dict[str, Any]]] = {}
    for scenario in configured_scenarios:
        if isinstance(scenario, dict) and isinstance(scenario.get("name"), str):
            configured_by_name.setdefault(scenario["name"].casefold(), []).append(scenario)
    missing = []
    for scenario_result in result.get("scenarios", []):
        baseline = scenario_result.get("baseline")
        name = scenario_result.get("name") or (
            baseline.get("name") if isinstance(baseline, dict) else None) or "unnamed"
        matches = configured_by_name.get(str(name).casefold(), [])
        if len(matches) != 1:
            missing.append({"scenario": str(name), "reason": "scenario configuration is missing or ambiguous"})
            continue
        scenario = matches[0]
        cases = scenario.get("access_sensitivity_cases", [])
        if not isinstance(cases, list) or not cases:
            if network.get("mos"):
                missing.append({"scenario": str(name), "reason": "no explicit access sensitivity case"})
            elif not isinstance(scenario.get("no_omitted_access_justification"), str) or not scenario[
                    "no_omitted_access_justification"].strip():
                missing.append({"scenario": str(name), "reason": "MOS-free circuit needs explicit no_omitted_access_justification"})
            continue
        if network.get("mos"):
            has_series_input = any(
                isinstance(case, dict) and isinstance(case.get("branches"), list) and
                any(isinstance(branch, dict) and isinstance(branch.get("series_with"), str) and
                    branch["series_with"].strip() for branch in case["branches"])
                for case in cases)
            has_series_result = any(
                isinstance(case, dict) and any(
                    branch.get("branch_type") == "conditional_series_access_sensitivity" and
                    branch.get("series_access")
                    for branch in case.get("branches", []) if isinstance(branch, dict))
                for case in scenario_result.get("access_sensitivity_cases", []))
            if not has_series_input or not has_series_result:
                missing.append({"scenario": str(name),
                                "reason": "MOS access requires an executed explicit series_with sensitivity"})
            elif scenario_result.get("access_sensitivity_status") != "PROVIDED_CONDITIONAL_UNQUALIFIED_CASES":
                missing.append({"scenario": str(name),
                                "reason": "one or more explicit access sensitivity cases did not complete"})
        elif scenario_result.get("access_sensitivity_status") != "PROVIDED_CONDITIONAL_UNQUALIFIED_CASES":
            missing.append({"scenario": str(name), "reason": "explicit access sensitivity was not executed"})
    if len(result.get("scenarios", [])) != len(configured_scenarios):
        missing.append({"scenario": "request", "reason": "one or more configured scenarios have no result"})
    return not missing, missing


def _run_ir_scenarios(network: dict[str, Any], request: dict[str, Any],
                      analysis_type: str, base_dir: Path) -> dict[str, Any]:
    """Keep successful baselines and cases when a later configured case is invalid."""
    scenarios = request.get("scenarios", [])
    output_scenarios = []
    errors = []

    def run_one(scenario):
        if analysis_type == "static":
            return power_integrity.analyze_static(network, scenario)
        return power_integrity.analyze_dynamic(network, scenario, base_dir=base_dir)

    for index, raw_scenario in enumerate(scenarios):
        name = (raw_scenario.get("name") if isinstance(raw_scenario, dict) else None)
        if not isinstance(name, str) or not name.strip():
            name = "scenario-%d" % (index + 1)
        if not isinstance(raw_scenario, dict):
            errors.append({"scenario": name, "error": "scenario must be an object"})
            output_scenarios.append({"name": name, "baseline": None,
                                     "access_sensitivity_cases": [],
                                     "access_sensitivity_status": "INCOMPLETE_EXPLICIT_SENSITIVITY"})
            continue

        scenario = copy.deepcopy(raw_scenario)
        raw_cases = scenario.pop("access_sensitivity_cases", [])
        valid_case_list = isinstance(raw_cases, list)
        if not valid_case_list:
            errors.append({"scenario": name, "error": "access_sensitivity_cases must be an array"})
            raw_cases = []
        if analysis_type == "dynamic" and scenario.get("waveform_csv") is not None:
            try:
                scenario["waveform_csv"] = str(_resolve(
                    base_dir, scenario["waveform_csv"], "scenario waveform_csv"))
            except ReliabilityError as error:
                errors.append({"scenario": name, "error": str(error)})
                output_scenarios.append({
                    "name": name, "baseline": None, "access_sensitivity_cases": [],
                    "access_sensitivity_status": "INCOMPLETE_EXPLICIT_SENSITIVITY",
                })
                continue

        try:
            baseline_result = run_one(scenario)
        except (power_integrity.PowerIntegrityError, OSError, ValueError,
                TypeError, KeyError) as error:
            baseline_result = None
            errors.append({"scenario": name, "case": "baseline", "error": str(error)})

        executed_cases = []
        for case_index, case in enumerate(raw_cases):
            if not isinstance(case, dict):
                errors.append({"scenario": name, "case_index": case_index,
                               "error": "sensitivity case must be an object"})
                continue
            case_request = copy.deepcopy(scenario)
            case_request["access_sensitivity_cases"] = [case]
            try:
                case_result = run_one(case_request)
                completed = case_result.get("access_sensitivity_cases", [])
                if len(completed) != 1:
                    raise ReliabilityError("sensitivity case did not produce exactly one result")
                executed_cases.append(completed[0])
            except (power_integrity.PowerIntegrityError, OSError, ValueError,
                    TypeError, KeyError, ReliabilityError) as error:
                errors.append({"scenario": name, "case": case.get("name", case_index),
                               "error": str(error)})

        wrapper = dict(baseline_result or {})
        wrapper["name"] = name
        wrapper["baseline"] = baseline_result.get("baseline") if baseline_result else None
        wrapper["access_sensitivity_cases"] = executed_cases
        if not raw_cases and valid_case_list and baseline_result:
            wrapper["access_sensitivity_status"] = baseline_result.get(
                "access_sensitivity_status", "MISSING_EXPLICIT_SENSITIVITY")
        elif valid_case_list and len(executed_cases) == len(raw_cases) and raw_cases:
            wrapper["access_sensitivity_status"] = "PROVIDED_CONDITIONAL_UNQUALIFIED_CASES"
        else:
            wrapper["access_sensitivity_status"] = "INCOMPLETE_EXPLICIT_SENSITIVITY"
        output_scenarios.append(wrapper)

    return {
        "schema_version": 1, "analysis_type": analysis_type,
        "network": {
            "name": network.get("name"), "source": network.get("source"),
            "sha256": network.get("sha256"),
            "resistor_count": len(network.get("resistors", [])),
            "capacitor_count": len(network.get("capacitors", [])),
            "mos_count": len(network.get("mos", [])),
            "metadata": network.get("metadata", {}),
        },
        "status": "UNQUALIFIED_SCREENING_NOT_FOUNDRY_SIGNOFF",
        "scenarios": output_scenarios,
        "scenario_errors": errors,
    }


def _ir_screen(result: dict[str, Any], configured_scenarios: list[Any],
               network: dict[str, Any]) -> dict[str, Any]:
    sensitivity_ok, missing_sensitivity = _has_access_sensitivity(
        result, configured_scenarios, network)
    failures = _screen_failures(result)
    errors = result.get("scenario_errors", [])
    status = ("FAIL" if failures else
              "BLOCKED" if errors or not sensitivity_ok else "ASSESSED_NUMERICALLY")
    return {
        "status": status,
        "access_sensitivity": {
            "status": "PROVIDED_CONDITIONAL_UNQUALIFIED_CASES" if sensitivity_ok else "BLOCKED",
            "required_for_integrated_readiness": True,
            "missing_scenarios": missing_sensitivity,
        },
        "failures": failures,
        "scenario_errors": errors,
        "analysis": result,
    }


def _documented_voltage_screen(result: dict[str, Any], policy_topic: dict[str, Any]) -> dict[str, Any]:
    rating = policy_topic.get("documented_absolute_maximum")
    if not isinstance(rating, dict):
        return _block("the immutable policy has no documented absolute-maximum envelope")
    magnitude = rating.get("magnitude_v")
    metrics = rating.get("metrics")
    if (isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)) or
            magnitude <= 0 or not isinstance(metrics, list) or not metrics):
        return _block("the immutable documented absolute-maximum envelope is malformed")
    limit = float(magnitude)
    observations = []
    violations = []
    missing = []
    for scenario in result.get("scenarios", []):
        scenario_name = scenario.get("name", "unnamed")
        coverage = scenario.get("coverage", {})
        for device in scenario.get("devices", []):
            for metric_name in metrics:
                metric = device.get("metrics", {}).get(metric_name)
                if not isinstance(metric, dict):
                    missing.append({"scenario": scenario_name, "device": device.get("name"),
                                    "metric": metric_name, "reason": "terminal metric is not available"})
                    continue
                history = metric.get("history")
                if not isinstance(history, dict) or not isinstance(history.get("voltage_v"), list) or not history[
                        "voltage_v"]:
                    missing.append({"scenario": scenario_name, "device": device.get("name"),
                                    "metric": metric_name, "reason": metric.get("reason", "terminal waveform is missing")})
                    continue
                covered = (metric.get("full_window_coverage") is True and
                           coverage.get("full_window_coverage") is True)
                try:
                    times = np.asarray(history["time_s"], dtype=float)
                    values = np.asarray(history["voltage_v"], dtype=float)
                    requested = coverage["requested_window_s"]
                    if (times.ndim != 1 or values.shape != times.shape or len(times) < 2 or
                            not np.all(np.isfinite(times)) or not np.all(np.isfinite(values)) or
                            np.any(np.diff(times) <= 0)):
                        raise ValueError("malformed terminal time history")
                    start = max(float(requested[0]), float(times[0]))
                    stop = min(float(requested[1]), float(times[-1]))
                    if stop <= start:
                        raise ValueError("no overlap with the requested window")
                    _, selected = power_integrity._window_samples(times, values, start, stop)
                    minimum, maximum = float(np.min(selected)), float(np.max(selected))
                except (KeyError, IndexError, TypeError, ValueError):
                    missing.append({"scenario": scenario_name, "device": device.get("name"),
                                    "metric": metric_name, "reason": "terminal voltage history is malformed"})
                    continue
                peak = max(abs(minimum), abs(maximum))
                observation = {
                    "scenario": scenario_name, "device": device.get("name"),
                    "metric": metric_name, "minimum_v": minimum, "maximum_v": maximum,
                    "peak_absolute_v": peak, "limit_magnitude_v": limit,
                    "status": ("VIOLATION" if peak > limit else
                               "WITHIN_DOCUMENTED_ENVELOPE" if covered else "INCOMPLETE_WINDOW_COVERAGE"),
                    "full_window_coverage": covered,
                }
                observations.append(observation)
                if peak > limit:
                    violations.append(observation)
                elif not covered:
                    missing.append({"scenario": scenario_name, "device": device.get("name"),
                                    "metric": metric_name, "reason": "full requested waveform window is not covered"})
    if violations:
        status = "FAIL"
    elif missing or not observations:
        status = "BLOCKED"
    else:
        status = "ASSESSED_NUMERICALLY"
    return {
        "status": status,
        "rating": copy.deepcopy(rating),
        "observations": observations,
        "violations": violations,
        "missing_coverage": missing,
        "interpretation": "inside-envelope observations are not a pass, SOA, back-gate, duration, temperature or manufacturing qualification",
    }



def _validate_thermal_export(source: Any, network: dict[str, Any],
                             branch_records: list[Any] | None = None) -> None:
    if not isinstance(source, dict):
        raise ReliabilityError("thermal export must be an object")
    times = source.get("times_s")
    branches = source.get("branches")
    if not isinstance(times, list) or not isinstance(branches, list):
        raise ReliabilityError("thermal export must contain times_s and branches arrays")
    base_by_name = {}
    for resistor in network.get("resistors", []):
        name = resistor.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ReliabilityError("assembled network has a resistor without a name")
        key = name.casefold()
        if key in base_by_name:
            raise ReliabilityError("assembled network has ambiguous resistor name %s" % name)
        base_by_name[key] = resistor

    expected = {}
    if branch_records is None:
        expected = base_by_name
    else:
        if not isinstance(branch_records, list):
            raise ReliabilityError("selected IR case has no resistor branch records")
        nodes = {str(node).casefold() for node in network.get("nodes", [])}
        for record in branch_records:
            if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                raise ReliabilityError("selected IR case has a malformed branch record")
            name = record["name"]
            key = name.casefold()
            if key in expected:
                raise ReliabilityError("selected IR case has duplicate branch %s" % name)
            base = base_by_name.get(key)
            resistance = record.get("resistance_ohm")
            if isinstance(resistance, bool) or not isinstance(resistance, (int, float)):
                raise ReliabilityError("selected IR branch %s has no numeric resistance" % name)
            if base is not None:
                endpoints = {str(record.get("a", "")).casefold(),
                             str(record.get("b", "")).casefold()}
                if endpoints != {str(base["a"]).casefold(), str(base["b"]).casefold()}:
                    raise ReliabilityError("selected IR branch endpoints do not match assembled branch %s" % name)
                series = record.get("series_access", [])
                if series:
                    if record.get("branch_type") != "conditional_series_access_sensitivity":
                        raise ReliabilityError("series access metadata on %s has no conditional marker" % name)
                    extracted = record.get("extracted_resistance_ohm")
                    if (isinstance(extracted, bool) or not isinstance(extracted, (int, float)) or
                            not math.isclose(float(extracted), float(base["resistance_ohm"]),
                                             rel_tol=1e-9, abs_tol=1e-12)):
                        raise ReliabilityError("series access branch %s does not retain its assembled resistance" % name)
                    added = 0.0
                    for extra in series:
                        if not isinstance(extra, dict) or str(extra.get("series_with", "")).casefold() != key:
                            raise ReliabilityError("series access branch does not identify target %s" % name)
                        endpoints = {str(extra.get("a", "")).casefold(),
                                     str(extra.get("b", "")).casefold()}
                        if endpoints != {str(base["a"]).casefold(), str(base["b"]).casefold()}:
                            raise ReliabilityError("series access endpoints do not match assembled branch %s" % name)
                        exclusions = {str(item).casefold() for item in extra.get("excludes_existing", [])}
                        if not {"measured_cnt", "fitted_rdsw"} <= exclusions:
                            raise ReliabilityError("series access %s does not preserve measured CNT and fitted RDSW ownership" % name)
                        extra_r = extra.get("resistance_ohm")
                        if isinstance(extra_r, bool) or not isinstance(extra_r, (int, float)) or extra_r <= 0:
                            raise ReliabilityError("series access branch %s has invalid added resistance" % name)
                        added += float(extra_r)
                    if not math.isclose(float(resistance), float(extracted) + added,
                                        rel_tol=1e-9, abs_tol=1e-12):
                        raise ReliabilityError("series access total resistance for %s is inconsistent" % name)
                elif not math.isclose(float(resistance), float(base["resistance_ohm"]),
                                      rel_tol=1e-9, abs_tol=1e-12):
                    raise ReliabilityError("thermal export changes assembled resistor %s without series-access evidence" % name)
            else:
                if record.get("branch_type") != "hypothetical_access_sensitivity":
                    raise ReliabilityError("thermal export contains unknown branch %s" % name)
                if ({str(record.get("a", "")).casefold(), str(record.get("b", "")).casefold()} - nodes):
                    raise ReliabilityError("hypothetical branch %s has unknown endpoints" % name)
                if resistance <= 0:
                    raise ReliabilityError("hypothetical branch %s has invalid resistance" % name)
            expected[key] = record

    actual = {}
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("name"), str):
            raise ReliabilityError("thermal export has a malformed resistor branch")
        key = branch["name"].casefold()
        if key in actual:
            raise ReliabilityError("thermal export duplicates resistor branch %s" % branch["name"])
        actual[key] = branch
    if set(actual) != set(expected):
        raise ReliabilityError(
            "thermal export resistor coverage does not match selected network/case; missing=%s extra=%s" %
            (sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))))
    for key, branch in actual.items():
        expected_record = expected[key]
        expected_resistance = (expected_record.get("resistance_ohm")
                               if branch_records is not None else
                               expected_record.get("resistance_ohm"))
        value = branch.get("resistance_ohm")
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isclose(float(value), float(expected_resistance),
                                 rel_tol=1e-9, abs_tol=1e-12)):
            raise ReliabilityError("thermal export resistance for %s does not match selected network/case" %
                                  branch["name"])
        currents = branch.get("current_a")
        if not isinstance(currents, list) or len(currents) != len(times):
            raise ReliabilityError("thermal export current history for %s does not cover every time sample" %
                                  branch["name"])
        try:
            if not all(math.isfinite(float(item)) for item in currents):
                raise ValueError("non-finite current")
        except (TypeError, ValueError, OverflowError) as error:
            raise ReliabilityError("thermal export current history for %s is malformed" %
                                  branch["name"]) from error


def _load_external_thermal_export(path_value: Any, base_dir: Path,
                                  network: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve(base_dir, path_value, "thermal_input_file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReliabilityError("cannot read thermal export %s: %s" % (path, error)) from error
    identity = document.get("network") if isinstance(document, dict) else None
    if not isinstance(identity, dict) or identity.get("sha256") != network.get("sha256"):
        raise ReliabilityError("thermal export must identify the exact assembled network SHA-256")
    source = document.get("thermal_input")
    _validate_thermal_export(source, network)
    return source, {"path": str(path), "sha256": _sha256(path),
                    "network_sha256": identity["sha256"]}


def _select_dynamic_thermal_export(reference: dict[str, Any], dynamic_report: dict[str, Any],
                                   network: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = dynamic_report.get("network", {})
    if identity.get("sha256") != network.get("sha256"):
        raise ReliabilityError("dynamic IR report is not bound to the selected assembled network")
    scenario_name = reference["scenario"]
    selected = []
    for scenario in dynamic_report.get("scenarios", []):
        baseline = scenario.get("baseline")
        name = scenario.get("name") or (
            baseline.get("name") if isinstance(baseline, dict) else None)
        if isinstance(name, str) and name.casefold() == scenario_name.casefold():
            selected.append(scenario)
    if len(selected) != 1:
        raise ReliabilityError("thermal reference must match exactly one completed dynamic scenario")
    case_name = reference["case"]
    if case_name.casefold() == "baseline":
        case_result = selected[0].get("baseline")
    else:
        cases = [case for case in selected[0].get("access_sensitivity_cases", [])
                 if isinstance(case.get("name"), str) and case["name"].casefold() == case_name.casefold()]
        case_result = cases[0] if len(cases) == 1 else None
    if not isinstance(case_result, dict) or not isinstance(case_result.get("thermal_input"), dict):
        raise ReliabilityError("selected dynamic case has no complete thermal_input export")
    _validate_thermal_export(case_result["thermal_input"], network,
                             branch_records=case_result.get("branches"))
    return case_result["thermal_input"], {
        "kind": "dynamic_ir", "scenario": scenario_name, "case": case_name,
        "network_sha256": identity["sha256"],
    }


def _thermal_instabilities(thermal: Any) -> list[dict[str, Any]]:
    if not isinstance(thermal, dict):
        return []
    results = thermal.get("results", [])
    found = []
    if isinstance(results, list):
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            if (result.get("conductor_feedback_stability") == "UNSTABLE" or
                    result.get("steady_status") == "UNSTABLE"):
                found.append({
                    "result_index": index,
                    "conductor_feedback_stability": result.get("conductor_feedback_stability"),
                    "steady_status": result.get("steady_status"),
                    "maximum_feedback_gain": result.get("maximum_feedback_gain"),
                    "parameters": result.get("parameters"),
                })
    if thermal.get("status") == "UNSTABLE" and not found:
        found.append({"aggregate_status": "UNSTABLE"})
    return found


def _combine_voltage_thermal(terminal_result: dict[str, Any], thermal: dict[str, Any],
                             policy_topic: dict[str, Any],
                             supplied_limits: bool) -> dict[str, Any]:
    analysis = dict(terminal_result)
    analysis["thermal"] = thermal
    metrics = [metric for scenario in terminal_result.get("scenarios", [])
               for device in scenario.get("devices", [])
               for metric in device.get("metrics", {}).values()]
    user_violations = [item for metric in metrics for item in (metric.get("violations") or [])]
    documented_voltage = _documented_voltage_screen(terminal_result, policy_topic)
    instabilities = _thermal_instabilities(thermal)
    terminal_unassessed = terminal_result.get("terminal_analysis_status") != "ASSESSED_NUMERICALLY"
    if (user_violations or documented_voltage.get("status") == "FAIL" or
            any(metric.get("limit_status") == "FAIL" for metric in metrics) or instabilities):
        status = "FAIL"
    elif (not terminal_result.get("scenarios") or terminal_unassessed or
          documented_voltage.get("status") == "BLOCKED" or thermal.get("status") != "ASSESSED"):
        status = "BLOCKED"
    else:
        status = "ASSESSED_NUMERICALLY"
    return {
        "status": status,
        "unqualified_user_limits_ignored_for_pdk_qualification": True,
        "explicit_numeric_limits_supplied": supplied_limits,
        "terminal_analysis_status": terminal_result.get("terminal_analysis_status", "UNASSESSED"),
        "documented_absolute_maximum": documented_voltage,
        "user_limit_violations": user_violations,
        "thermal_instabilities": instabilities,
        "analysis": analysis,
    }


def _run_voltage_thermal(raw_config: dict[str, Any], base_dir: Path,
                         network: dict[str, Any], network_path: Path,
                         policy_topic: dict[str, Any],
                         dynamic_report: dict[str, Any] | None) -> dict[str, Any]:
    voltage_config = _bind_wave_paths(raw_config, base_dir, "csv")
    voltage_config["network"] = {"path": str(network_path), "top": network.get("name")}
    supplied_limits = False
    raw_limits = voltage_config.get("limits", {})
    if isinstance(raw_limits, dict):
        for limit in raw_limits.values():
            if isinstance(limit, dict) and ("min_v" in limit or "max_v" in limit):
                supplied_limits = True
                limit["qualified"] = False
    thermal_binding = None
    thermal_error = None
    dynamic_reference = voltage_config.pop("thermal_input_from_dynamic", None)
    thermal_file = voltage_config.pop("thermal_input_file", None)
    if "thermal_input" in voltage_config:
        voltage_config.pop("thermal_input")
        thermal_error = (
            "inline thermal_input is not accepted; use a network-bound thermal_input_file "
            "or explicit thermal_input_from_dynamic export")
    elif dynamic_reference is not None and thermal_file is not None:
        thermal_error = "thermal_input_from_dynamic cannot be combined with thermal_input_file"
    elif dynamic_reference is not None:
        if (not isinstance(dynamic_reference, dict) or set(dynamic_reference) != {"scenario", "case"} or
                not all(isinstance(dynamic_reference.get(field), str) and
                        dynamic_reference[field].strip() for field in ("scenario", "case"))):
            thermal_error = "thermal_input_from_dynamic must name both scenario and case"
        elif not isinstance(dynamic_report, dict):
            thermal_error = "thermal_input_from_dynamic requires a completed dynamic IR report"
        else:
            try:
                source, thermal_binding = _select_dynamic_thermal_export(
                    dynamic_reference, dynamic_report, network)
                voltage_config["thermal_input"] = source
            except (OSError, ValueError, TypeError, KeyError) as error:
                thermal_error = str(error)
    elif thermal_file is not None:
        try:
            source, thermal_binding = _load_external_thermal_export(
                thermal_file, base_dir, network)
            voltage_config["thermal_input"] = source
        except (OSError, ValueError, TypeError, KeyError) as error:
            thermal_error = str(error)
    if thermal_error is not None:
        thermal_binding = {"status": "BLOCKED", "error": thermal_error}
    terminal_config = copy.deepcopy(voltage_config)
    terminal_config.pop("thermal", None)
    terminal_config.pop("thermal_input", None)
    terminal_config.pop("thermal_input_file", None)
    terminal_result = voltage_thermal.analyze(terminal_config, base_dir=base_dir)
    thermal_result: dict[str, Any]
    if thermal_error is not None:
        thermal_result = _block("thermal export or configuration could not be bound",
                                error=thermal_error)
    elif thermal_binding is None and voltage_config.get("thermal_input") is None:
        thermal_result = {"status": "UNASSESSED",
                          "reason": "no validated thermal export was supplied",
                          "calibration": "none", "junction_temperature_claim": "NONE"}
    else:
        try:
            full_result = voltage_thermal.analyze(voltage_config, base_dir=base_dir)
            thermal_result = full_result.get("thermal", {
                "status": "UNASSESSED", "reason": "thermal analysis returned no result"})
        except (voltage_thermal.VoltageThermalError, OSError, ValueError,
                TypeError, KeyError) as error:
            thermal_result = _block("thermal analysis could not be completed", error=str(error))
    combined = _combine_voltage_thermal(
        terminal_result, thermal_result, policy_topic, supplied_limits)
    combined["thermal_input_binding"] = thermal_binding
    return combined


def _overall_status(statuses: list[str], binding_errors: list[str] | None = None) -> str:
    if any(status == "FAIL" for status in statuses) or binding_errors:
        return "FAIL"
    if not statuses or any(status not in ("PASS", "ASSESSED_NUMERICALLY", "SCREENED")
                            for status in statuses):
        return "BLOCKED"
    return "SCREENED"


def _flatten_branch_records(screen_results: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for analysis_name in ("static_ir", "dynamic_ir"):
        result = screen_results.get(analysis_name, {}).get("analysis")
        if not isinstance(result, dict):
            continue
        for scenario in result.get("scenarios", []):
            cases = [scenario.get("baseline") or {}] + scenario.get("access_sensitivity_cases", [])
            for case in cases:
                if not isinstance(case, dict):
                    continue
                for branch in case.get("branches", []):
                    rows.append({"analysis": analysis_name, "scenario": scenario.get("name"),
                                 "case": case.get("name"), "case_type": case.get("case_type"),
                                 **branch})
    return rows


def _input_path_evidence(request: dict[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    references: list[tuple[str, Any]] = []
    for analysis_name, path_field in (("dynamic_ir", "waveform_csv"),
                                      ("voltage_thermal", "csv")):
        screen = request.get(analysis_name if analysis_name != "voltage_thermal" else analysis_name)
        if not isinstance(screen, dict):
            continue
        for scenario in screen.get("scenarios", []) if isinstance(screen.get("scenarios", []), list) else []:
            if isinstance(scenario, dict) and scenario.get(path_field) is not None:
                references.append(("%s.%s" % (analysis_name, path_field), scenario[path_field]))
    voltage = request.get("voltage_thermal")
    if isinstance(voltage, dict) and voltage.get("thermal_input_file") is not None:
        references.append(("voltage_thermal.thermal_input_file", voltage["thermal_input_file"]))
    rows = []
    for label, value in references:
        try:
            path = _resolve(base_dir, value, label)
            rows.append({"role": label, **_path_record(path)})
        except ReliabilityError as error:
            rows.append({"role": label, "path": None, "exists": False, "error": str(error)})
    return rows


def _bind_wave_paths(screen: dict[str, Any], base_dir: Path, path_field: str) -> dict[str, Any]:
    bound = copy.deepcopy(screen)
    scenarios = bound.get("scenarios", [])
    if not isinstance(scenarios, list):
        return bound
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get(path_field) is not None:
            scenario[path_field] = str(_resolve(base_dir, scenario[path_field],
                                                "scenario %s" % path_field))
    return bound


def run_reliability(config: Any = None, *, config_path: str | Path | None = None,
                    physical_artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run configured screens against actual physical artifacts.

    ``physical_artifacts`` is used by ``verify_physical`` to bind this request to
    freshly generated evidence. If request artifact paths or hashes are present,
    they must identify those exact generated files.
    """
    request, base_dir, request_evidence = _request(config, config_path)
    policy = _policy()
    configured_top = request.get("top")
    generated_top = (physical_artifacts.get("top")
                     if isinstance(physical_artifacts, dict) else None)
    top_binding_errors = []
    if generated_top is not None:
        if configured_top is not None and configured_top != generated_top:
            top_binding_errors.append(
                "configured top %r does not match generated physical top %r" %
                (configured_top, generated_top))
        request["top"] = generated_top
    top_valid = isinstance(request.get("top"), str) and bool(request["top"].strip())
    top_binding = {
        "configured": configured_top, "generated": generated_top,
        "resolved": request.get("top"),
        "status": "FAIL" if top_binding_errors else "PASS" if top_valid else "BLOCKED",
    }
    artifact_records, artifact_paths, artifact_errors = _artifacts(
        request, base_dir, physical_artifacts)
    binding_errors = top_binding_errors + artifact_errors
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "postlayout_reliability.py",
        "qualification": policy.get("qualification"),
        "request_evidence": request_evidence,
        "policy_evidence": {"path": str(POLICY_PATH), "sha256": _sha256(POLICY_PATH),
                            "source_review": copy.deepcopy(policy.get("source_review"))},
        "artifacts": artifact_records,
        "artifact_binding": {"errors": binding_errors},
        "top_binding": top_binding,
        "input_files": _input_path_evidence(request, base_dir),
        "screens": {},
        "topics": {},
        "manufacturing_acceptance": copy.deepcopy(policy["manufacturing_acceptance"]),
        "limitations": [
            "This report is research screening only; policy blockers cannot be cleared by request-supplied qualification declarations.",
            "Linear RC IR screens do not solve or couple nonlinear MOS channels.",
            "No arbitrary topology equivalence, calibrated RC coverage, PVT timing, current-density lifetime, or foundry signoff is inferred.",
            "Measured CNT/access and LVS-owned R_TIN branches are retained as extracted; the driver adds no default resistance.",
        ],
    }
    valid_artifacts = all(role in artifact_paths for role in ARTIFACT_KEYS) and top_valid
    missing_artifacts = [role for role in ARTIFACT_KEYS if role not in artifact_paths]
    if not top_valid:
        missing_artifacts.append("top")
    artifact_binding_status = ("FAIL" if binding_errors else
                               "BLOCKED" if missing_artifacts else "PASS")
    report["artifact_binding"]["status"] = artifact_binding_status
    if binding_errors:
        report["screens"]["artifact_binding"] = {
            "status": "FAIL", "errors": binding_errors, "top_binding": top_binding}
    elif missing_artifacts:
        report["screens"]["artifact_binding"] = {
            "status": "BLOCKED", "missing": missing_artifacts, "top_binding": top_binding}
    else:
        report["screens"]["artifact_binding"] = {
            "status": "PASS", "errors": [], "top_binding": top_binding}

    network = None
    network_error = None
    if "assembled" in artifact_paths:
        try:
            network = reliability_netlist.read_network(artifact_paths["assembled"], top=request.get("top"))
            report["assembled_network"] = {
                "path": str(artifact_paths["assembled"]), "sha256": network["sha256"],
                "top": network["name"], "mos_count": len(network["mos"]),
                "resistor_count": len(network["resistors"]),
                "capacitor_count": len(network["capacitors"]),
            }
        except (OSError, reliability_netlist.ReliabilityNetlistError, ValueError) as error:
            network_error = str(error)
            report["assembled_network"] = {"path": str(artifact_paths["assembled"]),
                                            "error": network_error}

    # Topology ERC always sees the same concrete GDS/LVS/PEX/assembly set as the
    # request; lack of explicit anchors remains a real ERC BLOCKED state.
    erc_config = request.get("erc", {})
    if not isinstance(erc_config, dict):
        report["screens"]["erc"] = _block("erc must be an object")
    elif valid_artifacts:
        bound_erc = copy.deepcopy(erc_config)
        bound_erc.update({key: str(artifact_paths[key]) for key in ARTIFACT_KEYS})
        bound_erc["top"] = request["top"]
        bound_erc["gds_top"] = request["top"]
        try:
            erc_result = postlayout_erc.run_erc(bound_erc)
            report["screens"]["erc"] = {"status": _status_from_erc(erc_result), "analysis": erc_result}
        except (postlayout_erc.PostlayoutERCError, reliability_netlist.ReliabilityNetlistError,
                OSError, ValueError, RuntimeError) as error:
            report["screens"]["erc"] = _block("topology ERC could not be completed", error=str(error))
    else:
        report["screens"]["erc"] = _block(
            "actual GDS/LVS/PEX/assembled artifacts and a bound top are required",
            missing=[role for role in ARTIFACT_KEYS if role not in artifact_paths] +
                    ([] if top_valid else ["top"]))

    for analysis_name in ("static_ir", "dynamic_ir"):
        raw = request.get(analysis_name)
        if not isinstance(raw, dict):
            report["screens"][analysis_name] = _block(
                "no explicit %s scenario configuration was supplied" % analysis_name)
            continue
        scenarios = raw.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            report["screens"][analysis_name] = _block(
                "%s.scenarios must contain explicit scenarios" % analysis_name)
            continue
        if network is None:
            report["screens"][analysis_name] = _block(
                "the actual assembled RC network is unavailable", error=network_error)
            continue
        try:
            result = _run_ir_scenarios(
                network, raw, "static" if analysis_name == "static_ir" else "dynamic",
                base_dir)
            report["screens"][analysis_name] = _ir_screen(result, scenarios, network)
        except (power_integrity.PowerIntegrityError, reliability_netlist.ReliabilityNetlistError,
                OSError, ValueError, TypeError, KeyError) as error:
            report["screens"][analysis_name] = _block(
                "%s analysis could not be completed" % analysis_name, error=str(error))
    # Terminal voltages and first-order electrothermal response are evaluated
    # only from explicit simulator mappings and supplied thermal exports/params.
    raw_voltage = request.get("voltage_thermal")
    if not isinstance(raw_voltage, dict):
        report["screens"]["voltage_thermal"] = _block(
            "no explicit voltage/thermal configuration was supplied")
    elif network is None:
        report["screens"]["voltage_thermal"] = _block(
            "the actual assembled RC/M network is unavailable", error=network_error)
    else:
        try:
            dynamic_report = report["screens"].get("dynamic_ir", {}).get("analysis")
            report["screens"]["voltage_thermal"] = _run_voltage_thermal(
                raw_voltage, base_dir, network, artifact_paths["assembled"],
                policy["topics"]["voltage_limits"], dynamic_report)
        except (voltage_thermal.VoltageThermalError, ReliabilityError,
                reliability_netlist.ReliabilityNetlistError, OSError, ValueError,
                TypeError, KeyError) as error:
            report["screens"]["voltage_thermal"] = _block(
                "terminal voltage analysis could not be completed", error=str(error))

    # IO DFM is optional but, when requested, is bound to the actual design and
    # fab-prepared GDS. Passing no pad count avoids the IO tool's generic 28-pad default.
    io_config = request.get("io_dfm")
    if io_config is None:
        report["screens"]["io_dfm"] = _block("no explicit pad/IO DFM specification was supplied")
    elif not isinstance(io_config, dict):
        report["screens"]["io_dfm"] = _block("io_dfm must be an object")
    elif "gds" not in artifact_paths or "fab_gds" not in artifact_paths:
        report["screens"]["io_dfm"] = _block("actual design and fab-prepared GDS are required for IO DFM")
    else:
        try:
            try:
                from libs.tech.stdcell import io_dfm
            except ImportError:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "icps_integrated_io_dfm", ROOT / "libs.tech/stdcell/io_dfm.py")
                io_dfm = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(io_dfm)
            options = {key: value for key, value in io_config.items()
                       if key in {"pad_mapping", "process_profile", "expected_pad_count",
                                  "esd_devices", "engineering_reservations_um"}}
            options.setdefault("expected_pad_count", None)
            result = io_dfm.check_io_dfm(
                artifact_paths["gds"], fab_gds_path=artifact_paths["fab_gds"], **options)
            structural_status = result.get("structural_status")
            checks = result.get("checks", {})
            pad_mapping_status = checks.get("explicit_pad_mapping", {}).get("status")
            esd_status = checks.get("explicit_esd_devices", {}).get("status")
            status = "FAIL" if structural_status == "FAIL" else (
                "ASSESSED_NUMERICALLY" if structural_status == "PASS" and
                pad_mapping_status == "COMPLETE" and esd_status == "DECLARED_UNQUALIFIED"
                else "BLOCKED")
            report["screens"]["io_dfm"] = {
                "status": status,
                "missing_evidence": (["complete explicit pad mapping"]
                                     if pad_mapping_status != "COMPLETE" else []) +
                                    (["explicit ESD-device declarations"]
                                     if esd_status == "NOT_DECLARED" else []),
                "analysis": result}
        except (OSError, ValueError, RuntimeError) as error:
            report["screens"]["io_dfm"] = _block("IO DFM analysis could not be completed", error=str(error))

    branch_records = _flatten_branch_records(report["screens"])
    report["screens"]["electromigration"] = {
        "status": ("ASSESSED_NUMERICALLY" if branch_records else "BLOCKED"),
        "basis": "actual reported branch current and I-squared-R results only; no current-density, lifetime, or rating inference",
        "branch_results": [{key: branch.get(key) for key in (
            "analysis", "scenario", "case", "case_type", "name", "a", "b", "material",
            "resistance_ohm", "extracted_resistance_ohm", "series_access",
            "current_limit_scope", "current_a_from_a_to_b", "peak_current_a",
            "peak_positive_current_a", "peak_negative_current_a", "rms_current_a",
            "average_current_a", "average_absolute_current_a", "energy_J", "power_W",
            "power_W_samples", "stress_status", "geometry", "geometry_status")} for branch in branch_records],
    }

    # Policy qualification is always copied as BLOCKED. Screening observations
    # remain visible beside it and an observed FAIL always wins over blockers.
    body_screen = _block("configured topology/body ERC did not complete")
    erc_report = report["screens"].get("erc", {}).get("analysis")
    if isinstance(erc_report, dict):
        body_checks = erc_report.get("checks", {})
        body_rows = {name: body_checks.get(name, {}) for name in
                     ("body_state_and_local_ties", "external_e_environment")}
        if any(item.get("status") == "FAIL" for item in body_rows.values()):
            body_screen = {"status": "FAIL", "checks": body_rows}
        elif all(item.get("status") == "PASS" for item in body_rows.values()):
            body_screen = {"status": "PASS", "checks": body_rows}
        else:
            body_screen = {"status": "BLOCKED", "checks": body_rows}
    voltage_screen = report["screens"].get("voltage_thermal", {})
    ir_screens = [report["screens"].get(name, {}) for name in ("static_ir", "dynamic_ir")]
    thermal_report = voltage_screen.get("analysis", {}).get("thermal", {})
    thermal_instabilities = voltage_screen.get("thermal_instabilities", [])
    topic_screens: dict[str, dict[str, Any]] = {
        "rc_coverage": {
            "status": "BLOCKED",
            "screen_status": ("ASSESSED_NUMERICALLY" if valid_artifacts and network is not None else "BLOCKED"),
            "observation": report.get("assembled_network"),
            "reason": "repository R/C extraction is not calibrated coverage/equivalence evidence",
        },
        "body_backgate": {"status": "BLOCKED", "screen_status": body_screen.get("status"),
                          "observation": body_screen},
        "voltage_limits": {"status": "BLOCKED",
                           "screen_status": voltage_screen.get("status", "BLOCKED"),
                           "observation": voltage_screen,
                           "reason": "request-supplied limits never clear immutable PDK voltage-limit blockers"},
        "pvt_timing": {"status": "BLOCKED", "screen_status": "BLOCKED",
                       "reason": "no qualified multi-PVT timing closure is provided by these screens"},
        "electromigration": {"status": "BLOCKED",
                             "screen_status": report["screens"]["electromigration"]["status"],
                             "observation": report["screens"]["electromigration"]},
        "electrothermal": {
            "status": "BLOCKED",
            "screen_status": ("FAIL" if thermal_instabilities else
                              "ASSESSED_NUMERICALLY" if thermal_report.get("status") == "ASSESSED" else
                              "BLOCKED"),
            "observation": {"thermal": thermal_report,
                            "observed_instabilities": thermal_instabilities},
            "reason": "user-supplied Rth/Cth is an uncalibrated lumped screen, not process qualification"},
        "antenna": {"status": "BLOCKED", "screen_status": "BLOCKED",
                    "reason": "no antenna-ratio qualification analyzer or process limit is present"},
        "pad_esd_eos": {"status": "BLOCKED",
                        "screen_status": report["screens"].get("io_dfm", {}).get("status", "BLOCKED"),
                        "observation": report["screens"].get("io_dfm"),
                        "reason": "structural IO geometry or device declarations do not prove ESD/EOS qualification"},
    }
    report["topics"] = {}
    for topic_name in POLICY_TOPICS:
        policy_topic = copy.deepcopy(policy["topics"][topic_name])
        observed = topic_screens[topic_name]
        report["topics"][topic_name] = {
            **policy_topic,
            "status": "BLOCKED",
            "screen_status": observed.get("screen_status", "BLOCKED"),
            "screen": observed,
        }

    required_screens = ("artifact_binding", "erc", "static_ir", "dynamic_ir",
                        "voltage_thermal", "electromigration")
    if request.get("io_dfm") is not None:
        required_screens += ("io_dfm",)
    screen_statuses = [report["screens"].get(name, {}).get("status", "BLOCKED")
                       for name in required_screens]
    overall = _overall_status(screen_statuses, binding_errors)
    report["screening_status"] = overall
    report["status"] = overall
    report["access_sensitivity"] = {
        "required_for_integrated_readiness": True,
        "static_ir": report["screens"].get("static_ir", {}).get("access_sensitivity",
                                                                     _block("static IR is not configured")),
        "dynamic_ir": report["screens"].get("dynamic_ir", {}).get("access_sensitivity",
                                                                      _block("dynamic IR is not configured")),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="reliability request JSON")
    parser.add_argument("--report", help="write JSON report here; stdout if omitted")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    try:
        result = run_reliability(config_path) if config_path is not None else run_reliability()
    except ReliabilityError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    rendered = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.report:
        output_path = Path(args.report).expanduser()
        if not output_path.is_absolute():
            output_path = (config_path.parent if config_path is not None else Path.cwd()) / output_path
        output_path.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    status = result["screening_status"]
    return 1 if status == "FAIL" else 2 if status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
