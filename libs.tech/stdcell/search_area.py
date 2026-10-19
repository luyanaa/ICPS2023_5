#!/usr/bin/env python3
"""Area-first, strictly gated search for experimental ICPS2023_5 1M cells.

The reported area is candidate cell geometry and its route envelope, not a
floorplanned or routed-core area. Search results never update the published
standard-cell library.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STD_CELL_SOURCE = Path(__file__).resolve().parent
CONFIG_PATH = STD_CELL_SOURCE / "config.json"
SCHEMA = "ICPS2023_5_area_search_v1"
MAX_WORKERS = 4

try:
    from . import model_binding
except ImportError:  # direct import from the stdcell tool directory
    import model_binding  # type: ignore

_BINDING = model_binding.load(CONFIG_PATH)


_PROVENANCE_FILES = {
    "config": "libs.tech/stdcell/config.json",
    "search_area": "libs.tech/stdcell/search_area.py",
    "logic": "libs.tech/stdcell/logic.py",
    "geometry": "libs.tech/stdcell/geometry.py",
    "channel_geometry": "libs.tech/stdcell/channel_geometry.py",
    "verify": "libs.tech/stdcell/verify.py",
    "characterize": "libs.tech/stdcell/characterize.py",
    "drc_deck": "libs.tech/klayout/drc/icps2023_5.drc",
    "fab_prep_deck": "libs.tech/klayout/drc/icps2023_5_fab_prep.drc",
    "process_profile": "libs.tech/klayout/process_profile.rb",
    "process_profiles": "libs.tech/klayout/process_profiles.json",
    "lvs_deck": "libs.tech/klayout/lvs/icps2023_5.lvs",
    "pex_deck": "libs.tech/klayout/pex/icps2023_5_pex.py",
    "postlayout_assembler": "libs.tech/ngspice/tools/assemble_postlayout.py",
    "model_library": _BINDING["library"],
    "model_include": _BINDING["include"],
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True,
                                    ensure_ascii=True, allow_nan=False) + "\n",
                          encoding="utf-8")
    temporary.replace(path)


def _load_modules():
    """Load project modules lazily so importing this CLI has no tool side effects."""
    source = str(STD_CELL_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)
    import characterize
    import geometry
    import logic
    import verify
    return geometry, logic, verify, characterize


def _provenance() -> dict[str, str]:
    return {
        **{key: _sha256(ROOT / relative)
           for key, relative in sorted(_PROVENANCE_FILES.items())},
        "model_engine": _BINDING["engine"],
        "model_engine_version": _BINDING["engine_version"],
    }


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"area_search.{field} values must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"area_search.{field} values must be numeric") from error
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        constraint = f" >= {minimum:g}" if minimum is not None else " finite"
        raise ValueError(f"area_search.{field} values must be{constraint}")
    return number


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"area_search.{field} values must be positive integers")
    return value


def _area_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("area_search")
    if not isinstance(raw, dict):
        raise ValueError("config.json must define an area_search object")
    result = copy.deepcopy(raw)
    for key in ("heights_um", "total_device_widths_um", "route_channels_um"):
        values = result.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"area_search.{key} must be a nonempty array")
        minimum = 0.0 if key == "route_channels_um" else 0.001
        result[key] = [_finite_number(value, key, minimum=minimum) for value in values]
    result["total_device_widths_um"] = [
        _finite_number(value, "total_device_widths_um", minimum=13.0)
        for value in result["total_device_widths_um"]]
    result["route_channels_um"] = [
        _finite_number(value, "route_channels_um", minimum=0.0)
        for value in result["route_channels_um"]]
    fingers = result.get("fingers")
    if not isinstance(fingers, list) or not fingers:
        raise ValueError("area_search.fingers must be a nonempty array")
    result["fingers"] = [_positive_int(value, "fingers") for value in fingers]
    pin_orders = result.get("pin_orders")
    if (not isinstance(pin_orders, list) or not pin_orders or
            any(value not in ("declared", "reverse_signals") for value in pin_orders)):
        raise ValueError("area_search.pin_orders must contain declared and/or reverse_signals")
    mirrors = result.get("mirrors")
    if not isinstance(mirrors, list) or not mirrors or any(not isinstance(x, bool) for x in mirrors):
        raise ValueError("area_search.mirrors must be a nonempty array of booleans")
    objectives = result.get("pareto_objectives")
    allowed_objectives = {"area_um2", "contacts_count", "local_wire_resistance_proxy_ohm"}
    if (not isinstance(objectives, list) or "area_um2" not in objectives or
            any(value not in allowed_objectives for value in objectives)):
        raise ValueError("area_search.pareto_objectives must use supported objective names and include area_um2")
    if not isinstance(result.get("require_postlayout_logic"), bool):
        raise ValueError("area_search.require_postlayout_logic must be a boolean")
    result["workers"] = _positive_int(result.get("workers"), "workers")
    if result["workers"] > MAX_WORKERS:
        raise ValueError(f"area_search.workers must not exceed the bounded limit {MAX_WORKERS}")
    result["candidate_timeout_s"] = _finite_number(
        result.get("candidate_timeout_s"), "candidate_timeout_s", minimum=0.001)
    return result


def _signal_pin_order(spec: dict[str, Any], variant: str) -> list[str]:
    declared = list(spec["pins"])
    if variant == "declared":
        return declared
    signals = [pin for pin in declared if pin not in {"VDD", "VSS"}]
    power = [pin for pin in declared if pin in {"VDD", "VSS"}]
    return list(reversed(signals)) + power


def _candidate_parameters(config: dict[str, Any], spec: dict[str, Any]):
    for height, width, fingers, pin_variant, mirror, channel in itertools.product(
            config["heights_um"], config["total_device_widths_um"], config["fingers"],
            config["pin_orders"], config["mirrors"], config["route_channels_um"]):
        yield {
            "height_um": height,
            "total_device_width_um": width,
            "fingers": fingers,
            "pin_order_variant": pin_variant,
            "pin_order": _signal_pin_order(spec, pin_variant),
            "mirror": mirror,
            "route_channel_um": channel,
        }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _normalize_paths(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_paths(item, root) for item in value]
    if isinstance(value, str):
        try:
            candidate = Path(value)
            if candidate.is_absolute():
                return _relative_path(candidate, root)
        except (OSError, ValueError):
            pass
    return value


def _artifact_hashes(candidate_dir: Path, output_root: Path) -> list[dict[str, str]]:
    ignored = {"candidate.json", "progress.json", "request.json", "worker.log"}
    artifacts = []
    for path in sorted(candidate_dir.rglob("*")):
        if (not path.is_file() or path.name in ignored or
                path.suffix.casefold() == ".log"):
            continue
        artifacts.append({"path": _relative_path(path, output_root), "sha256": _sha256(path)})
    return artifacts

def _reset_candidate_outputs(candidate_dir: Path) -> None:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    for name in ("verification", "postlayout_logic"):
        path = candidate_dir / name
        if path.is_dir():
            shutil.rmtree(path)
    for name in ("candidate.gds", "physical_reference.spice", "candidate.json",
                 "progress.json", "request.json", "worker.log"):
        path = candidate_dir / name
        if path.is_file():
            path.unlink()
    for path in candidate_dir.glob("*.tmp"):
        if path.is_file():
            path.unlink()


def _artifact_reference(path: Path, output_root: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    return {"path": _relative_path(path, output_root), "sha256": _sha256(path)}


def _checkpoint(candidate_dir: Path, record: dict[str, Any], stage: str) -> None:
    record["stage"] = stage
    record["status"] = "running"
    _write_json(candidate_dir / "progress.json", record)


def _finish_worker(record: dict[str, Any], candidate_dir: Path,
                   output_root: Path) -> dict[str, Any]:
    record["candidate_dir"] = _relative_path(candidate_dir, output_root)
    record["artifact_hashes"] = _artifact_hashes(candidate_dir, output_root)
    _write_json(candidate_dir / "candidate.json", record)
    try:
        (candidate_dir / "progress.json").unlink()
    except FileNotFoundError:
        pass
    return record


def _physical_evidence(record: dict[str, Any], evidence: dict[str, Any] | None,
                       evidence_path: Path, output_root: Path, status: str) -> None:
    ref = _artifact_reference(evidence_path, output_root)
    record["physical_evidence"] = {
        "status": status,
        "path": ref["path"] if ref else _relative_path(evidence_path, output_root),
        "sha256": ref["sha256"] if ref else None,
        "data": _normalize_paths(evidence, output_root) if evidence is not None else None,
    }


def _resistance_proxy(manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    tech = config["technology"]
    sheets = tech["sheet_resistance_ohm_sq"]
    rules = tech["rules_um"]
    ml1_length = float(manifest["routing"]["ML1_wire_length_um"])
    tin_length = float(manifest["routing"]["TiN_wire_length_um"])
    ml1_width = float(rules["ML1_width"])
    tin_width = float(rules["TIN_width"])
    ml1_resistance = float(sheets["ML1"]) * ml1_length / ml1_width
    tin_resistance = float(sheets["TIN"]) * tin_length / tin_width
    return {
        "value_ohm": ml1_resistance + tin_resistance,
        "ML1_ohm": ml1_resistance,
        "TiN_ohm": tin_resistance,
        "ML1_length_um": ml1_length,
        "TiN_length_um": tin_length,
        "ML1_width_um": ml1_width,
        "TiN_width_um": tin_width,
        "sheet_resistance_ohm_sq": {"ML1": float(sheets["ML1"]), "TIN": float(sheets["TIN"])},
        "method": "sum(sheet_resistance_ohm_sq * routed_length_um / minimum_rule_width_um); geometry route-length proxy only",
    }


def _worker(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    candidate_dir = Path(request["candidate_dir"])
    output_root = Path(request["output_root"])
    candidate_dir.mkdir(parents=True, exist_ok=True)
    geometry, logic, verify, characterize = _load_modules()
    source_spec = request["spec"]
    parameters = request["parameters"]
    candidate_id = request["candidate_id"]
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "candidate_id": candidate_id,
        "cell": source_spec["name"],
        "parameters": copy.deepcopy(parameters),
        "configuration_hash": request["configuration_hash"],
        "provenance_hash": request["provenance_hash"],
        "status": "running",
        "stage": "initializing",
        "physical_status": "not_run",
        "electrical_status": "not_run" if request["require_postlayout_logic"] else "not_required",
        "rankable": False,
        "area_domain": "candidate_cell_geometry_route_envelope",
        "qualification": "experimental_candidate_only_not_published_or_process_qualified",
    }
    _checkpoint(candidate_dir, record, "geometry")
    build_spec = copy.deepcopy(source_spec)
    build_spec["name"] = candidate_id
    for mos in build_spec["mos"]:
        mos["w_um"] = parameters["total_device_width_um"]
    try:
        layout, top, manifest = geometry.build_cell(
            build_spec, parameters["height_um"], fingers=parameters["fingers"],
            pin_order=parameters["pin_order"], mirror=parameters["mirror"],
            route_channel_um=parameters["route_channel_um"])
        layout.rename_cell(top.cell_index(), candidate_id)
        expected_width = float(parameters["total_device_width_um"])
        finger_groups: dict[str, list[dict[str, Any]]] = {}
        for device in manifest["devices"]:
            base, separator, suffix = device["name"].rpartition("_f")
            base_name = base if separator and suffix.isdigit() else device["name"]
            finger_groups.setdefault(base_name, []).append(device)
        for original in source_spec["mos"]:
            folded = finger_groups.get(original["name"], [])
            if (len(folded) != parameters["fingers"] or
                    any(float(device["w_um"]) < 13.0 for device in folded) or
                    not math.isclose(sum(float(device["w_um"]) for device in folded),
                                     expected_width, rel_tol=0, abs_tol=0.001)):
                raise ValueError(
                    f"folding for {original['name']} does not preserve selected total width")
    except Exception as error:
        record.update({"status": "geometry_failed", "stage": "geometry",
                       "error": str(error), "physical_status": "not_run",
                       "electrical_status": "not_run" if request["require_postlayout_logic"] else "not_required"})
        _finish_worker(record, candidate_dir, output_root)
        return 0

    gds_path = candidate_dir / "candidate.gds"
    reference_path = candidate_dir / "physical_reference.spice"
    verification_dir = candidate_dir / "verification"
    try:
        gds_path.parent.mkdir(parents=True, exist_ok=True)
        layout.write(str(gds_path))
        physical_spec = copy.deepcopy(build_spec)
        physical_spec["mos"] = [
            {key: device[key] for key in
             ("name", "polarity", "d", "g", "s", "w_um", "l_um")}
            for device in manifest["devices"]]
        logic.write_reference(physical_spec, reference_path,
                              top_name=candidate_id, library=True)
    except Exception as error:
        record.update({"status": "artifact_generation_failed", "stage": "geometry_artifacts",
                       "error": str(error), "physical_status": "not_run",
                       "physical_reference_spec_sha256": _digest(physical_spec) if "physical_spec" in locals() else None})
        _finish_worker(record, candidate_dir, output_root)
        return 0

    resistance_proxy = _resistance_proxy(manifest, request["config"])
    record.update({
        "physical_reference_spec_sha256": _digest(physical_spec),
        "dimensions_um": {"width": float(manifest["width_um"]),
                           "height": float(manifest["height_um"])},
        "bbox_um": copy.deepcopy(manifest["bbox_um"]),
        "boundary_um": copy.deepcopy(manifest["boundary_um"]),
        "boundary_layer": copy.deepcopy(manifest["boundary_layer"]),
        "area_um2": float(manifest["area_um2"]),
        "routed_core_area_um2": None,
        "contacts_count": int(manifest["contacts_count"]),
        "contacts": copy.deepcopy(manifest["contacts"]),
        "pins": copy.deepcopy(manifest["pins"]),
        "active_regions_um": copy.deepcopy(manifest["active_regions_um"]),
        "soi_edge_protection": copy.deepcopy(manifest["soi_edge_protection"]),
        "folding": copy.deepcopy(manifest["folding"]),
        "routing": {
            "ML1_wire_length_um": float(manifest["routing"]["ML1_wire_length_um"]),
            "TiN_wire_length_um": float(manifest["routing"]["TiN_wire_length_um"]),
            "unrouted_nets": copy.deepcopy(manifest["routing"]["unrouted_nets"]),
            "gate_nets_fallback_to_ML1": copy.deepcopy(manifest["routing"]["gate_nets_fallback_to_ML1"]),
            "additional_CNT_vias": int(manifest["routing"]["additional_CNT_vias"]),
            "route_order_variant": copy.deepcopy(manifest["routing"]["route_order_variant"]),
        },
        "gds": _artifact_reference(gds_path, output_root),
        "physical_reference": _artifact_reference(reference_path, output_root),
        "local_wire_resistance_proxy_ohm": resistance_proxy["value_ohm"],
        "local_wire_resistance_proxy": resistance_proxy,
    })

    _checkpoint(candidate_dir, record, "strict_physical")
    evidence_path = verification_dir / "physical.json"
    try:
        evidence = verify.verify_physical(
            gds_path, reference_path, candidate_id, verification_dir)
        required = ("strict_drc", "strict_lvs", "pex", "assembly")
        if not evidence.get("passed") or not all(evidence.get(key) is True for key in required):
            raise RuntimeError("verify.verify_physical returned without all strict evidence gates passing")
        if evidence.get("soi_edge_protection", {}).get("passed") is not True:
            raise RuntimeError("SOI-edge protection gate did not pass")
        record["physical_status"] = "passed"
        _physical_evidence(record, evidence, evidence_path, output_root, "passed")
    except Exception as error:
        evidence = getattr(error, "evidence", None)
        if evidence is not None and not evidence_path.is_file():
            _write_json(evidence_path, evidence)
        record.update({"status": "strict_physical_failed", "stage": "strict_physical",
                       "error": str(error), "physical_status": "failed"})
        _physical_evidence(record, evidence, evidence_path, output_root, "failed")
        _finish_worker(record, candidate_dir, output_root)
        return 0

    postlayout_path = Path(evidence["postlayout_spice"])
    if request["require_postlayout_logic"]:
        record["electrical_status"] = "running"
        _checkpoint(candidate_dir, record, "postlayout_logic")
        logic_dir = candidate_dir / "postlayout_logic"
        logic_dir.mkdir(parents=True, exist_ok=True)
        characterization_spec = copy.deepcopy(source_spec)
        characterization_spec["name"] = candidate_id
        electrical_paths: dict[str, Any] = {}
        try:
            if source_spec.get("sequential"):
                state = characterize.sequential_state(
                    characterization_spec, postlayout_path, logic_dir / "sequential")
                state_path = logic_dir / "sequential" / "state.json"
                if not state_path.is_file():
                    _write_json(state_path, state)
                electrical_paths["sequential_state"] = _artifact_reference(state_path, output_root)
                record["electrical_summary"] = {
                    "kind": "sequential_capture_hold_logic_levels",
                    "sample_count": len(state["samples"]),
                    "logic_levels_passed": bool(state["logic_levels_passed"]),
                }
            else:
                truth = characterize.truth_table(
                    characterization_spec, postlayout_path, logic_dir / "truth",
                    logic.evaluate)
                truth_path = logic_dir / "truth_table.json"
                _write_json(truth_path, truth)
                arcs = characterize.characterize_arcs(
                    characterization_spec, postlayout_path, logic_dir / "arcs",
                    logic.evaluate)
                arcs_path = logic_dir / "arcs.json"
                _write_json(arcs_path, arcs)
                electrical_paths["truth_table"] = _artifact_reference(truth_path, output_root)
                electrical_paths["logic_level_arcs"] = _artifact_reference(arcs_path, output_root)
                record["electrical_summary"] = {
                    "kind": "combinational_postlayout_truth_and_logic_levels",
                    "truth_row_count": len(truth),
                    "arc_count": len(arcs),
                    "logic_levels_passed": True,
                    "timing_objective": "feasibility_only_not_an_optimization_claim",
                }
            if record["electrical_summary"].get("logic_levels_passed") is not True:
                raise RuntimeError("postlayout logic-level check did not pass")
            record["electrical_status"] = "passed"
            record["electrical_evidence"] = electrical_paths
        except Exception as error:
            record.update({"status": "postlayout_logic_failed", "stage": "postlayout_logic",
                           "error": str(error), "electrical_status": "failed",
                           "electrical_evidence": electrical_paths})
            _finish_worker(record, candidate_dir, output_root)
            return 0
    else:
        record["electrical_status"] = "not_required"
        record["electrical_evidence"] = {}

    record.update({"status": "accepted_candidate", "stage": "complete",
                   "rankable": (record["physical_status"] == "passed" and
                                record["electrical_status"] in ("passed", "not_required"))})
    _finish_worker(record, candidate_dir, output_root)
    return 0


def _candidate_process(candidate: dict[str, Any], output_root: Path,
                       timeout_s: float, configuration_hash: str,
                       provenance_hash: str, config: dict[str, Any],
                       require_logic: bool) -> dict[str, Any]:
    candidate_dir = output_root / "candidates" / candidate["cell"] / candidate["candidate_id"]
    _reset_candidate_outputs(candidate_dir)
    request_path = candidate_dir / "request.json"
    request = {
        "candidate_id": candidate["candidate_id"],
        "candidate_dir": str(candidate_dir.resolve()),
        "output_root": str(output_root.resolve()),
        "spec": candidate["spec"],
        "parameters": candidate["parameters"],
        "configuration_hash": configuration_hash,
        "provenance_hash": provenance_hash,
        "config": config,
        "require_postlayout_logic": require_logic,
    }
    _write_json(request_path, request)
    log_path = candidate_dir / "worker.log"
    candidate_json = candidate_dir / "candidate.json"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--_worker-request", str(request_path)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            try:
                progress = json.loads((candidate_dir / "progress.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                progress = {
                    "schema": SCHEMA, "candidate_id": candidate["candidate_id"],
                    "cell": candidate["cell"], "parameters": candidate["parameters"],
                    "configuration_hash": configuration_hash,
                    "provenance_hash": provenance_hash,
                    "physical_status": "not_run", "electrical_status": "not_run",
                    "area_domain": "candidate_cell_geometry_route_envelope",
                    "qualification": "experimental_candidate_only_not_published_or_process_qualified",
                }
            progress.update({
                "status": "candidate_timeout", "stage": "timeout",
                "last_stage": progress.get("stage"),
                "error": f"candidate exceeded its {timeout_s:g}s wall-clock timeout; worker process group terminated",
                "rankable": False,
            })
            progress["candidate_dir"] = _relative_path(candidate_dir, output_root)
            progress["artifact_hashes"] = _artifact_hashes(candidate_dir, output_root)
            _write_json(candidate_json, progress)
            return progress
    if candidate_json.is_file():
        try:
            return json.loads(candidate_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            result = {"schema": SCHEMA, "candidate_id": candidate["candidate_id"],
                      "cell": candidate["cell"], "parameters": candidate["parameters"],
                      "status": "worker_result_invalid", "stage": "worker", "error": str(error),
                      "physical_status": "not_run", "electrical_status": "not_run", "rankable": False}
    else:
        result = {"schema": SCHEMA, "candidate_id": candidate["candidate_id"],
                  "cell": candidate["cell"], "parameters": candidate["parameters"],
                  "status": "worker_failed", "stage": "worker",
                  "error": f"candidate worker exited with status {return_code} without a result",
                  "physical_status": "not_run", "electrical_status": "not_run", "rankable": False}
    result["configuration_hash"] = configuration_hash
    result["provenance_hash"] = provenance_hash
    result["area_domain"] = "candidate_cell_geometry_route_envelope"
    result["qualification"] = "experimental_candidate_only_not_published_or_process_qualified"
    result["candidate_dir"] = _relative_path(candidate_dir, output_root)
    result["artifact_hashes"] = _artifact_hashes(candidate_dir, output_root)
    _write_json(candidate_json, result)
    return result


def _dominance_fields(area_config: dict[str, Any]) -> list[str]:
    fields = ["area_um2", "ML1_wire_length_um", "TiN_wire_length_um",
              "unrouted_nets_count", "mos_access_failure_count", "additional_CNT_vias"]
    fields.extend(area_config["pareto_objectives"])
    return list(dict.fromkeys(fields))


def _candidate_metric(candidate: dict[str, Any], field: str) -> float:
    if field == "area_um2":
        return float(candidate["area_um2"])
    if field == "contacts_count":
        return float(candidate["contacts_count"])
    if field == "local_wire_resistance_proxy_ohm":
        return float(candidate["local_wire_resistance_proxy_ohm"])
    routing = candidate["routing"]
    if field == "ML1_wire_length_um":
        return float(routing["ML1_wire_length_um"])
    if field == "TiN_wire_length_um":
        return float(routing["TiN_wire_length_um"])
    if field == "unrouted_nets_count":
        return float(len(routing["unrouted_nets"]))
    if field == "mos_access_failure_count":
        physical = candidate.get("physical_evidence", {}).get("data") or {}
        return float(sum(not item.get("passed", False) for item in physical.get("mos_access", [])))
    if field == "additional_CNT_vias":
        return float(routing["additional_CNT_vias"])
    raise ValueError(f"unsupported Pareto metric: {field}")


def _ranking_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    routing = candidate["routing"]
    physical = candidate.get("physical_evidence", {}).get("data") or {}
    access_failures = sum(not item.get("passed", False)
                          for item in physical.get("mos_access", []))
    return (candidate["area_um2"],
            routing["ML1_wire_length_um"] + routing["TiN_wire_length_um"],
            len(routing["unrouted_nets"]), access_failures,
            routing["additional_CNT_vias"], candidate["contacts_count"],
            candidate["local_wire_resistance_proxy_ohm"], candidate["candidate_id"])


def _pareto(candidates: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    winners = []
    vectors = {candidate["candidate_id"]:
               tuple(_candidate_metric(candidate, field) for field in fields)
               for candidate in candidates}
    for candidate in candidates:
        own = vectors[candidate["candidate_id"]]
        dominated = False
        for other in candidates:
            if other["candidate_id"] == candidate["candidate_id"]:
                continue
            theirs = vectors[other["candidate_id"]]
            if all(left <= right for left, right in zip(theirs, own)) and any(
                    left < right for left, right in zip(theirs, own)):
                dominated = True
                break
        if not dominated:
            winners.append(candidate)
    return sorted(winners, key=_ranking_key)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", action="append", help="canonical cell name; may be repeated")
    parser.add_argument("--cells", nargs="+", help="one or more canonical cell names")
    parser.add_argument("--output-dir", type=Path,
                        help="output directory (default: deterministic run directory under stdcell/area_search_runs)")
    parser.add_argument("--workers", type=int, help="bounded concurrent candidate worker count")
    parser.add_argument("--candidate-timeout-s", type=float,
                        help="per-candidate wall-clock timeout in seconds")
    parser.add_argument("--_worker-request", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker_request:
        return args
    if args.workers is not None and not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers must be between 1 and {MAX_WORKERS}")
    if args.candidate_timeout_s is not None and (
            not math.isfinite(args.candidate_timeout_s) or args.candidate_timeout_s <= 0):
        parser.error("--candidate-timeout-s must be finite and positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args._worker_request:
        return _worker(Path(args._worker_request))
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        area_config = _area_config(config)
        _, logic, _, _ = _load_modules()
        specs = logic.cell_specs()
        requested = list(args.cell or []) + list(args.cells or [])
        selected_names = sorted(set(requested)) if requested else sorted(specs)
        unknown = sorted(set(selected_names) - set(specs))
        if unknown:
            raise ValueError("unknown canonical cell name(s): " + ", ".join(unknown))
        workers = args.workers if args.workers is not None else area_config["workers"]
        timeout_s = (args.candidate_timeout_s if args.candidate_timeout_s is not None
                     else area_config["candidate_timeout_s"])
        if not 1 <= workers <= MAX_WORKERS or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError(f"worker count must be between 1 and {MAX_WORKERS}; timeout must be positive")
        provenance = _provenance()
        configuration = {
            "area_search": area_config,
            "selected_cells": selected_names,
            "effective_workers": workers,
            "effective_candidate_timeout_s": timeout_s,
        }
        configuration_hash = _digest(configuration)
        provenance_hash = _digest(provenance)
        run_fingerprint = _digest({"configuration_hash": configuration_hash,
                                   "provenance_hash": provenance_hash})
        if args.output_dir is None:
            output_root = STD_CELL_SOURCE / "area_search_runs" / run_fingerprint
        else:
            output_root = args.output_dir.expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        candidates = []
        seen = set()
        for cell_name in selected_names:
            spec = specs[cell_name]
            for parameters in _candidate_parameters(area_config, spec):
                key = {"cell": cell_name, "parameters": parameters}
                variant_hash = _digest(key)
                candidate_id = f"{cell_name}_{variant_hash[:16]}"
                if candidate_id in seen:
                    continue
                seen.add(candidate_id)
                candidates.append({"candidate_id": candidate_id, "cell": cell_name,
                                   "spec": spec, "parameters": parameters})
        candidates.sort(key=lambda candidate: candidate["candidate_id"])
        results_by_id: dict[str, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_candidate_process, candidate, output_root,
                                       timeout_s, configuration_hash, provenance_hash,
                                       config, area_config["require_postlayout_logic"])
                       for candidate in candidates]
            for future, candidate in zip(futures, candidates):
                try:
                    result = future.result()
                except Exception as error:
                    candidate_dir = output_root / "candidates" / candidate["cell"] / candidate["candidate_id"]
                    result = {
                        "schema": SCHEMA, "candidate_id": candidate["candidate_id"],
                        "cell": candidate["cell"], "parameters": candidate["parameters"],
                        "status": "worker_failed", "stage": "worker_launch",
                        "error": str(error), "physical_status": "not_run",
                        "electrical_status": "not_run", "rankable": False,
                        "configuration_hash": configuration_hash,
                        "provenance_hash": provenance_hash,
                        "area_domain": "candidate_cell_geometry_route_envelope",
                        "qualification": "experimental_candidate_only_not_published_or_process_qualified",
                        "candidate_dir": _relative_path(candidate_dir, output_root),
                    }
                    candidate_dir.mkdir(parents=True, exist_ok=True)
                    _write_json(candidate_dir / "candidate.json", result)
                results_by_id[candidate["candidate_id"]] = result
        all_results = [results_by_id[candidate["candidate_id"]] for candidate in candidates]
        rankable = [result for result in all_results if result.get("rankable") is True]
        dominance_fields = _dominance_fields(area_config)
        pareto_by_cell = {}
        ranked_by_cell = {}
        for cell_name in selected_names:
            cell_candidates = [candidate for candidate in rankable
                               if candidate["cell"] == cell_name]
            pareto_by_cell[cell_name] = _pareto(cell_candidates, dominance_fields)
            ranked_by_cell[cell_name] = sorted(cell_candidates, key=_ranking_key)
        pareto = [candidate for cell_name in selected_names
                  for candidate in pareto_by_cell[cell_name]]
        ranked = [candidate for cell_name in selected_names
                  for candidate in ranked_by_cell[cell_name]]
        pareto_summaries = [
            {
                "candidate_id": candidate["candidate_id"],
                "cell": candidate["cell"],
                "candidate_record_path": f'{candidate["candidate_dir"]}/candidate.json',
                "physical_status": candidate["physical_status"],
                "electrical_status": candidate["electrical_status"],
                "metrics": {field: _candidate_metric(candidate, field)
                            for field in dominance_fields},
            }
            for candidate in pareto
        ]
        result_document = {
            "schema": SCHEMA,
            "configuration": configuration,
            "configuration_hash": configuration_hash,
            "provenance": provenance,
            "provenance_hash": provenance_hash,
            "run_fingerprint_sha256": run_fingerprint,
            "selected_cells": selected_names,
            "candidate_count": len(all_results),
            "rankable_candidate_count": len(rankable),
            "pareto_metric_order": dominance_fields,
            "ranking_order": ["area_um2", "ML1_plus_TiN_wire_length_um",
                              "unrouted_nets_count", "MOS_access_failures",
                              "additional_CNT_vias", "contacts_count",
                              "local_wire_resistance_proxy_ohm", "candidate_id"],
            "ranking_scope": "per_canonical_cell; different logic functions are not cross-ranked",
            "ranked_candidate_ids_by_cell": {
                cell_name: [candidate["candidate_id"] for candidate in ranked_by_cell[cell_name]]
                for cell_name in selected_names
            },
            "area_domain": "candidate_cell_geometry_route_envelope",
            "routed_core_area_um2": None,
            "qualification": "experimental_search_only_not_publication_or_process_qualification",
            "candidates": all_results,
            "ranked_candidate_ids": [candidate["candidate_id"] for candidate in ranked],
            "pareto_winner_ids": [candidate["candidate_id"] for candidate in pareto],
            "pareto_winners": pareto_summaries,
        }
        result_path = output_root / "search.json"
        _write_json(result_path, result_document)
        print(json.dumps({"search_json": str(result_path),
                          "candidate_count": len(all_results),
                          "rankable_candidate_count": len(rankable),
                          "pareto_winner_count": len(pareto)}, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"area search failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
