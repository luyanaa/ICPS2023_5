#!/usr/bin/env python3
"""Unpublished native-1M sequential-cell physical and storage experiments.

Channel geometry is the default; compact_rows is available only when explicitly
selected. Postlayout storage results are nominal-model timing-feasibility evidence,
never publication, placement, clock, or process qualification.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]

_ACTIVE_BENCH = None   # bench engine binding set by the CLI/tests; default nominal ngspice


def set_bench(bench):
    """Select the bench engine binding for this process (default: nominal ngspice)."""
    global _ACTIVE_BENCH
    _ACTIVE_BENCH = bench
STD_CELL_SOURCE = Path(__file__).resolve().parent
TARGET_CELLS = ("LATCH_TG_X1", "LATCH_GATE_X1", "DFF_TG_X1", "DFF_GATE_X1")
ARCHITECTURES = ("channel", "compact_rows")
DEFAULT_ARCHITECTURE = "channel"
TALLER_HEIGHT_DELTA_UM = 50.0
DEFAULT_ROUTE_CHANNEL_UM = 0.0
LOGICAL_REFERENCE_AUX_STATE_LOAD_FF = 10.0
LOGICAL_REFERENCE_AUX_STATE_RESISTANCE_OHM = 1e12
LOGICAL_OUTPUT_LOAD_FF = 10.0
STORAGE_OUTPUT_LOAD_FF = 10.0
STORAGE_INPUT_SLEW_NS = 2.0
STORAGE_HOLD_TIMES_US = (10.0, 1000.0)
STORAGE_MODEL = "nominal ICPS2023_5_1m B4SOI model card"
STAGE_NAMES = (
    "runtime_dependencies",
    "logical_model_state",
    "geometry",
    "geometry_manifest",
    "physical_reference",
    "gds_write",
    "strict_physical_verification",
    "postlayout_state",
)
DIAGNOSTIC_ONLY_STAGES = ("logical_model_state",)
PROCESS_PROFILE = "icps2023_5_1m"


def _load_modules():
    source = str(STD_CELL_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)
    modules = {}
    errors = {}
    for name in ("logic", "geometry", "verify", "characterize", "bench_engine", "model_binding"):
        try:
            modules[name] = importlib.import_module(name)
        except Exception as error:
            errors[name] = {**_exception_details(error)}
    return modules, errors


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True,
                                     ensure_ascii=True, allow_nan=False) + "\n",
                          encoding="utf-8")
    temporary.replace(path)


def _path_record(path: Path, output_root: Path) -> str:
    return path.resolve().relative_to(output_root.resolve()).as_posix()

def _existing_artifacts(output_root: Path, **paths: Path) -> dict[str, str]:
    return {name: _path_record(path, output_root)
            for name, path in paths.items() if path.exists()}


def _height_candidates(spec: dict[str, Any], architecture: str,
                       heights: Iterable[float] | None
                       ) -> tuple[tuple[float, ...], float | None]:
    if architecture not in ARCHITECTURES:
        raise ValueError(f"unsupported architecture {architecture!r}")
    template_minimum = None
    if architecture == "channel":
        channel_geometry = importlib.import_module("channel_geometry")
        template_minimum = float(channel_geometry.minimum_height_um(spec, fingers=1))
    if heights is None:
        if architecture != "channel":
            raise ValueError(
                "compact_rows requires an explicit height; no automatic fallback is selected")
        selected = (template_minimum, template_minimum + TALLER_HEIGHT_DELTA_UM)
    else:
        try:
            selected = tuple(float(height) for height in heights)
        except (TypeError, ValueError) as error:
            raise ValueError("heights must be a nonempty sequence of positive finite values") from error
    if not selected:
        raise ValueError("heights must be a nonempty sequence of positive finite values")
    if any(not math.isfinite(height) or height <= 0 for height in selected):
        raise ValueError("heights must be positive finite values")
    if len(set(selected)) != len(selected):
        raise ValueError("heights must not contain duplicates")
    return selected, template_minimum


def parameter_sweep(spec: dict[str, Any], *,
                    architecture: str = DEFAULT_ARCHITECTURE,
                    heights: Iterable[float] | None = None,
                    mirror: bool = False) -> tuple[dict[str, Any], ...]:
    """Return only explicitly selected cell variants without changing MOS dimensions."""
    if not isinstance(mirror, bool):
        raise ValueError("mirror must be a boolean")
    selected_heights, template_minimum = _height_candidates(
        spec, architecture, heights)
    parameters = []
    for height in selected_heights:
        parameters.append({
            "architecture": architecture,
            "height_um": height,
            "template_minimum_height_um": template_minimum,
            "route_channel_um": DEFAULT_ROUTE_CHANNEL_UM,
            "pin_order_variant": "declared",
            "pin_order": list(spec["pins"]),
            "mirror": mirror,
            "fingers": 1,
            "mos_dimensions_changed": False,
        })
    return tuple(parameters)


def _candidate_id(cell: str, index: int, parameters: dict[str, Any]) -> str:
    height = f"{parameters['height_um']:g}"
    channel = f"{parameters['route_channel_um']:g}"
    architecture = parameters["architecture"]
    mirror = int(parameters["mirror"])
    return f"{cell}_C{index:03d}_A{architecture}_H{height}_CH{channel}_PD_M{mirror}"


def _area_metrics(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "cell_geometry_area_um2": float(manifest["area_um2"]),
        "cell_bbox_um": list(manifest["bbox_um"]),
        "array_geometry_area_um2": None,
        "routed_core_area_um2": None,
        "area_scope": "cell geometry bounding box including route-channel whitespace; no array or routed core was evaluated",
    }


def _finalize_candidate(record: dict[str, Any]) -> None:
    failed = [name for name, stage in record["stages"].items()
              if stage["status"] == "failed"]
    record["failed_stages"] = failed
    record["diagnostic_failures"] = [
        name for name in failed if name in DIAGNOSTIC_ONLY_STAGES]
    record["gate_failures"] = [
        name for name in failed if name not in DIAGNOSTIC_ONLY_STAGES]
    record["failure_stage"] = record["gate_failures"][0] if record["gate_failures"] else None
    record["incomplete_gate_stages"] = [
        name for name, stage in record["stages"].items()
        if name not in DIAGNOSTIC_ONLY_STAGES and stage["status"] != "passed"
    ]
    record["accepted_for_publication"] = False
    if any(stage["status"] == "pending" for stage in record["stages"].values()):
        record["status"] = "in_progress"
    elif not record["incomplete_gate_stages"]:
        record["status"] = "verified_unpublished_candidate"
    else:
        record["status"] = "not_accepted"


def _set_stage(record: dict[str, Any], name: str, status: str, **details: Any) -> None:
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError(f"invalid final stage status {status!r}")
    record["stages"][name] = {"status": status, **details}
    _finalize_candidate(record)


def _checkpoint(record: dict[str, Any], path: Path) -> None:
    _finalize_candidate(record)
    _write_json(path, record)


def _exception_details(error: Exception) -> dict[str, str]:
    return {"error_type": type(error).__name__, "error": str(error)}


def _logical_reference(spec: dict[str, Any], path: Path, logic: Any,
                       model_library: Path) -> None:
    """Write a logical reference with auxiliary state loads and explicit SUB port."""
    reference_spec = dict(spec)
    reference_spec["pins"] = list(spec["pins"])
    if "SUB" not in reference_spec["pins"]:
        reference_spec["pins"].append("SUB")
    logic.write_reference(reference_spec, path, library=True)
    lines = [(_ACTIVE_BENCH.model_line() if _ACTIVE_BENCH is not None
              else f'.lib "{model_library.resolve()}" b4soi')]
    lines.extend(path.read_text(encoding="utf-8").splitlines())
    pins = set(spec["pins"])
    internal_nets = sorted({mos[terminal] for mos in spec["mos"]
                            for terminal in ("d", "g", "s")
                            if mos[terminal] not in pins})
    rewritten: list[str] = []
    inserted = False
    expected_end = f".ENDS {spec['name']}"
    for line in lines:
        if line == expected_end:
            for index, net in enumerate(internal_nets):
                rewritten.extend((
                    f"RSTATE{index} {net} 0 {LOGICAL_REFERENCE_AUX_STATE_RESISTANCE_OHM:g}",
                    f"CSTATE{index} {net} 0 {LOGICAL_REFERENCE_AUX_STATE_LOAD_FF:g}f",
                ))
            inserted = True
        rewritten.append(line)
    if not inserted:
        raise RuntimeError(f"logical reference is missing {expected_end}")
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _run_logical_state(spec: dict[str, Any], cell_dir: Path,
                       output_root: Path, characterize: Any, logic: Any) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "status": "failed",
        "scope": "logical_model_reference; not physical-layout evidence",
        "gate_role": "diagnostic_only",
        "auxiliary_internal_state_loads": {
            "scope": "logical_model_reference_only",
            "resistance_ohm": LOGICAL_REFERENCE_AUX_STATE_RESISTANCE_OHM,
            "capacitance_ff": LOGICAL_REFERENCE_AUX_STATE_LOAD_FF,
            "applied_to_postlayout": False,
        },
        "output_load_ff": LOGICAL_OUTPUT_LOAD_FF,
        "artifacts": {},
    }
    reference = cell_dir / "logical_model_reference.spice"
    state_dir = cell_dir / "logical_model_state"
    try:
        _logical_reference(spec, reference, logic, characterize.MODELS)
        result = characterize.sequential_state(
            spec, reference, state_dir, load_ff=LOGICAL_OUTPUT_LOAD_FF,
            bench=_ACTIVE_BENCH)
        state_path = state_dir / "state.json"
        stage.update({
            "status": "passed",
            "result": result,
            "artifacts": _existing_artifacts(
                output_root,
                reference=reference,
                state_dir=state_dir,
                bench=state_dir / "bench.cir",
                simulator_log=state_dir / "ngspice.log",
                state=state_path,
            ),
        })
    except Exception as error:
        stage.update(_exception_details(error))
        stage["artifacts"] = _existing_artifacts(
            output_root,
            reference=reference,
            state_dir=state_dir,
            bench=state_dir / "bench.cir",
            simulator_log=state_dir / "ngspice.log",
            state=state_dir / "state.json",
        )
    return stage


def _storage_characterization_details(integration_method: str = "trap") -> dict[str, Any]:
    return {
        "scope": (
            "strictly verified extracted-and-assembled layout; nominal-model "
            "preservation and setup/hold/pulse timing feasibility only"),
        "characterization": "characterize_storage",
        "model": STORAGE_MODEL,
        "model_fingerprint": "SHA-256 recorded as result.model_sha256",
        "output_load_ff": STORAGE_OUTPUT_LOAD_FF,
        "input_slew_ns": STORAGE_INPUT_SLEW_NS,
        "stopped_clock_hold_times_us": list(STORAGE_HOLD_TIMES_US),
        "integration_method": integration_method,
        "timing_objective": "feasibility_only",
        "postlayout_internal_state_loads_added": False,
        "postlayout_forced_initial_state": False,
        "qualification": (
            "nominal model only; no qualified clock, placement site, process, "
            "IO/ESD, PVT, silicon, or metastability qualification"),
    }


def _storage_artifacts(output_root: Path, state_dir: Path) -> dict[str, str]:
    capture_dir = state_dir / "capture"
    return _existing_artifacts(
        output_root,
        characterization_dir=state_dir,
        storage=state_dir / "storage.json",
        capture_dir=capture_dir,
        capture_bench=capture_dir / "bench.cir",
        capture_simulator_log=capture_dir / "ngspice.log",
        capture_state=capture_dir / "state.json",
    )
def _storage_evidence_is_complete(spec: dict[str, Any],
                                  result: dict[str, Any]) -> bool:
    required_bounds = {"setup", "hold", "high_pulse", "low_pulse"}
    bounds = result.get("timing_bounds")
    if (result.get("passed") is not True or
            result.get("preservation_correctness") is not True or
            result.get("hold_correctness") is not True or
            result.get("pulse_correctness") is not True or
            result.get("internal_state_loads_added") is not False or
            result.get("timing_objective") != "feasibility_only" or
            result.get("load_ff") != STORAGE_OUTPUT_LOAD_FF or
            result.get("input_slew_ns") != STORAGE_INPUT_SLEW_NS or
            not isinstance(result.get("model_sha256"), str) or
            not isinstance(result.get("capture"), dict) or
            not result["capture"].get("samples") or
            not isinstance(bounds, dict) or
            not required_bounds.issubset(bounds)):
        return False

    state = spec.get("sequential", {})
    clock_levels = (0, 1) if state.get("kind") == "flip_flop" else (0,)
    expected_retention = {
        (bit, level, hold_time)
        for bit in (0, 1)
        for level in clock_levels
        for hold_time in STORAGE_HOLD_TIMES_US
    }
    retention = result.get("retention")
    if not isinstance(retention, list):
        return False
    observed_retention = {
        (record.get("stored_bit"), record.get("stopped_clock_level"),
         record.get("hold_time_us"))
        for record in retention if isinstance(record, dict)
    }
    if (not expected_retention.issubset(observed_retention) or
            any(record.get("passed") is not True
                for record in retention if isinstance(record, dict))):
        return False

    experiments = result.get("aperture_experiments")
    if not isinstance(experiments, dict):
        return False
    for kind in ("setup", "hold", "high_pulse", "low_pulse"):
        bound = bounds[kind]
        if not isinstance(bound, dict):
            return False
        records = experiments.get(kind)
        if not isinstance(records, list):
            return False
        if kind == "low_pulse" and state.get("kind") == "latch":
            if bound.get("applicable") is not False or records:
                return False
            continue
        if (bound.get("applicable") is not True or
                "conservative_tested_bound_ns" not in bound or not records):
            return False
        for record in records:
            transitions = record.get("transitions") if isinstance(record, dict) else None
            if (not isinstance(transitions, list) or
                    {item.get("captured_bit") for item in transitions
                     if isinstance(item, dict)} != {0, 1}):
                return False
    return True


def _run_postlayout_storage(spec: dict[str, Any], candidate_id: str,
                            postlayout_path: Path, state_dir: Path,
                            output_root: Path, characterize: Any, *,
                            integration_method: str = "trap") -> dict[str, Any]:
    stage: dict[str, Any] = {
        "status": "failed",
        **_storage_characterization_details(integration_method),
    }
    characterization_spec = copy.deepcopy(spec)
    characterization_spec["name"] = candidate_id
    try:
        result = characterize.characterize_storage(
            characterization_spec, postlayout_path, state_dir,
            load_ff=STORAGE_OUTPUT_LOAD_FF,
            hold_times_us=STORAGE_HOLD_TIMES_US,
            slew_ns=STORAGE_INPUT_SLEW_NS, integration_method=integration_method,
            bench=_ACTIVE_BENCH)
        if not isinstance(result, dict):
            raise RuntimeError("storage characterizer returned a non-object result")
        stage["result"] = result
        if not _storage_evidence_is_complete(characterization_spec, result):
            raise RuntimeError(
                "storage characterizer did not return complete passing preservation/timing evidence")
        stage["status"] = "passed"
    except Exception as error:
        stage.update(_exception_details(error))
    stage["artifacts"] = _storage_artifacts(output_root, state_dir)
    return stage




def _new_candidate(cell: str, candidate_id: str,
                   parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ICPS2023_5_sequential_closure_candidate_v2",
        "cell": cell,
        "candidate_id": candidate_id,
        "parameters": copy.deepcopy(parameters),
        "status": "in_progress",
        "accepted_for_publication": False,
        "qualification": (
            "experimental unpublished candidate; nominal-model timing feasibility only; "
            "no qualified clock, placement-site, process, IO/ESD, or PVT qualification"),
        "stages": {name: {"status": "pending"} for name in STAGE_NAMES},
        "failed_stages": [],
        "failure_stage": None,
        "diagnostic_failures": [],
        "gate_failures": [],
        "incomplete_gate_stages": [],
        "metrics": {
            "cell_geometry_area_um2": None,
            "cell_bbox_um": None,
            "array_geometry_area_um2": None,
            "routed_core_area_um2": None,
            "area_scope": "not measured; no array or routed core was evaluated",
        },
    }


def _validate_output_root(output_root: Path) -> None:
    protected = (ROOT / "libs.ref", ROOT / "libs.tech")
    for directory in protected:
        try:
            output_root.relative_to(directory.resolve())
        except ValueError:
            continue
        raise ValueError(f"output directory must not be under published/source tree {directory}")
    if output_root == ROOT:
        raise ValueError("output directory must be separate from the repository root")
    if output_root.exists():
        if not output_root.is_dir():
            raise ValueError("output directory path exists and is not a directory")
        if any(output_root.iterdir()):
            raise ValueError("output directory must be empty so prior evidence is never overwritten")
    else:
        output_root.mkdir(parents=True)


def _summary_failures(summary: dict[str, Any]) -> list[dict[str, str]]:
    failures = []
    for candidate in summary["candidates"]:
        for name, stage in candidate["stage_results"].items():
            if stage["status"] == "failed":
                failures.append({
                    "cell": candidate["cell"],
                    "candidate_id": candidate["candidate_id"],
                    "record": candidate["record"],
                    "stage": name,
                    "gate_role": (
                        "diagnostic_only" if name in DIAGNOSTIC_ONLY_STAGES
                        else "acceptance_gate"),
                    "error": stage.get("error", "stage failed without an error string"),
                })
    return failures


def _record_candidate(summary: dict[str, Any], record: dict[str, Any],
                      record_path: Path, output_root: Path,
                      summary_path: Path) -> None:
    summary["candidates"].append({
        "cell": record["cell"],
        "candidate_id": record["candidate_id"],
        "status": record["status"],
        "record": _path_record(record_path, output_root),
        "stage_results": copy.deepcopy(record["stages"]),
        "failed_stages": list(record["failed_stages"]),
        "diagnostic_failures": list(record["diagnostic_failures"]),
        "gate_failures": list(record["gate_failures"]),
        "incomplete_gate_stages": list(record["incomplete_gate_stages"]),
        "metrics": copy.deepcopy(record["metrics"]),
    })
    summary["failures"] = _summary_failures(summary)
    summary["attempt_count"] = len(summary["candidates"])
    summary["failure_count"] = len(summary["failures"])
    if record["status"] == "verified_unpublished_candidate":
        summary["verified_unpublished_candidates"].append(record["candidate_id"])
    _write_json(summary_path, summary)


def _module_failure(name: str, errors: dict[str, dict[str, str]]) -> dict[str, Any]:
    failure = errors.get(name, {
        "error_type": "ModuleUnavailable",
        "error": f"required module {name} is unavailable",
    })
    return {"missing_module": name, **failure}


def run_closure(output_dir: str | Path, *, cells: Iterable[str] = TARGET_CELLS,
                architecture: str = DEFAULT_ARCHITECTURE,
                heights: Iterable[float] | None = None,
                mirror: bool = False,
                integration_method: str = "trap") -> dict[str, Any]:
    """Run selected unpublished attempts and persist every stage result."""
    output_root = Path(output_dir).expanduser().resolve()
    selected_cells = tuple(cells)
    if not selected_cells or len(set(selected_cells)) != len(selected_cells):
        raise ValueError("cells must be a nonempty sequence without duplicates")
    unknown = [cell for cell in selected_cells if cell not in TARGET_CELLS]
    if unknown:
        raise ValueError(f"unsupported sequential cells: {unknown}")
    if architecture not in ARCHITECTURES:
        raise ValueError(f"unsupported architecture {architecture!r}")
    if not isinstance(mirror, bool):
        raise ValueError("mirror must be a boolean")
    if integration_method not in {"trap", "gear"}:
        raise ValueError("integration_method must be trap or gear")
    if architecture == "compact_rows" and heights is None:
        raise ValueError(
            "compact_rows requires an explicit height; no automatic fallback is selected")
    selected_heights = None
    if heights is not None:
        try:
            selected_heights = tuple(float(height) for height in heights)
        except (TypeError, ValueError) as error:
            raise ValueError("heights must be a nonempty sequence of positive finite values") from error
        if (not selected_heights or
                any(not math.isfinite(height) or height <= 0 for height in selected_heights) or
                len(set(selected_heights)) != len(selected_heights)):
            raise ValueError("heights must be unique positive finite values")
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema": "ICPS2023_5_sequential_closure_v2",
        "process_profile": PROCESS_PROFILE,
        "status": "running",
        "scope": (
            "experimental native-1M sequential-cell closure; no publication, "
            "foundry, or process qualification"),
        "limits": [
            "no foundry, placement-site, qualified-clock, IO/ESD, or PVT qualification",
            "nominal-model results only; no measured wafer calibration",
            "no user target RTL, array placement, or routed-core area was supplied or evaluated",
            "no combinational geometry or behavior is modified",
        ],
        "logical_and_physical_state_evidence_are_separate": True,
        "diagnostic_only_stages": list(DIAGNOSTIC_ONLY_STAGES),
        "selection": {
            "architecture": architecture,
            "heights_um": list(selected_heights) if selected_heights is not None else None,
            "default_height_policy": (
                f"channel template minimum and {TALLER_HEIGHT_DELTA_UM:g}um taller comparison"
                if selected_heights is None else "explicit height selection"),
            "mirror": mirror,
            "route_channel_um": DEFAULT_ROUTE_CHANNEL_UM,
        },
        "postlayout_storage_characterization": _storage_characterization_details(integration_method),
        "area_semantics": {
            "cell_geometry_area_um2": "candidate bounding-box area including configured route-channel whitespace",
            "array_geometry_area_um2": None,
            "routed_core_area_um2": None,
            "note": "array and routed-core area are not evaluated by this single-cell runner",
        },
        "selection_policy": "selected parameter sets are attempted independently; candidates are never ranked",
        "candidates": [],
        "failures": [],
        "verified_unpublished_candidates": [],
    }
    summary_path = output_root / "sequential_closure.json"
    _write_json(summary_path, summary)

    modules, dependency_errors = _load_modules()
    summary["runtime_dependencies"] = {
        "loaded_modules": sorted(modules),
        "errors": copy.deepcopy(dependency_errors),
    }
    logic = modules.get("logic")
    if logic is None:
        error = dependency_errors.get("logic", {
            "error_type": "ModuleUnavailable",
            "error": "required logic specification module is unavailable",
        })
        summary.update({
            "status": "blocked_missing_logic_module",
            "attempt_count": 0,
            "failure_count": 1,
            "failures": [{
                "stage": "runtime_dependencies",
                "module": "logic",
                "error_type": error["error_type"],
                "error": error["error"],
            }],
        })
        _write_json(summary_path, summary)
        return summary
    specs = logic.cell_specs()

    for cell in selected_cells:
        spec = specs[cell]
        cell_dir = output_root / cell
        cell_dir.mkdir()
        characterize = modules.get("characterize")
        if characterize is None:
            logical_stage = {
                "status": "failed",
                "gate_role": "diagnostic_only",
                "scope": "logical_model_reference; not physical-layout evidence",
                "auxiliary_internal_state_loads": {
                    "scope": "logical_model_reference_only",
                    "resistance_ohm": LOGICAL_REFERENCE_AUX_STATE_RESISTANCE_OHM,
                    "capacitance_ff": LOGICAL_REFERENCE_AUX_STATE_LOAD_FF,
                    "applied_to_postlayout": False,
                },
                "output_load_ff": LOGICAL_OUTPUT_LOAD_FF,
                **_module_failure("characterize", dependency_errors),
                "artifacts": {},
            }
        else:
            logical_stage = _run_logical_state(
                spec, cell_dir, output_root, characterize, logic)
        _write_json(cell_dir / "logical_model_stage.json", logical_stage)

        for index, parameters in enumerate(parameter_sweep(
                spec, architecture=architecture, heights=selected_heights,
                mirror=mirror), start=1):
            candidate_id = _candidate_id(cell, index, parameters)
            candidate_dir = cell_dir / candidate_id
            candidate_dir.mkdir()
            record_path = candidate_dir / "candidate.json"
            record = _new_candidate(cell, candidate_id, parameters)
            if dependency_errors:
                _set_stage(record, "runtime_dependencies", "failed",
                           errors=copy.deepcopy(dependency_errors),
                           error="one or more required modules could not be imported")
            else:
                _set_stage(record, "runtime_dependencies", "passed",
                           loaded_modules=sorted(modules))
            record["stages"]["logical_model_state"] = copy.deepcopy(logical_stage)
            _checkpoint(record, record_path)

            build_spec = copy.deepcopy(spec)
            build_spec["name"] = candidate_id
            geometry = modules.get("geometry")

            try:
                if geometry is None:
                    raise ImportError(dependency_errors.get("geometry", {}).get(
                        "error", "geometry module is unavailable"))
                layout, top, manifest = geometry.build_cell(
                    build_spec, parameters["height_um"],
                    fingers=parameters["fingers"],
                    pin_order=parameters["pin_order"],
                    mirror=parameters["mirror"],
                    route_channel_um=parameters["route_channel_um"],
                    architecture=parameters["architecture"])
                layout.rename_cell(top.cell_index(), candidate_id)
                record["metrics"] = _area_metrics(manifest)
                record["geometry"] = {
                    "architecture": parameters["architecture"],
                    "width_um": float(manifest["width_um"]),
                    "height_um": float(manifest["height_um"]),
                    "contacts_count": int(manifest["contacts_count"]),
                    "route_channel_um": float(manifest["route_channel_um"]),
                    "routing": copy.deepcopy(manifest["routing"]),
                    "top_cell": candidate_id,
                }
                _set_stage(record, "geometry", "passed")
            except Exception as error:
                _set_stage(record, "geometry", "failed", **_exception_details(error))
                for name in ("geometry_manifest", "physical_reference", "gds_write",
                             "strict_physical_verification", "postlayout_state"):
                    _set_stage(record, name, "skipped", reason="geometry generation failed")
                _checkpoint(record, record_path)
                _record_candidate(summary, record, record_path, output_root, summary_path)
                continue
            _checkpoint(record, record_path)

            manifest_path = candidate_dir / "geometry.json"
            try:
                _write_json(manifest_path, manifest)
                record["artifacts"] = {
                    "geometry_manifest": _path_record(manifest_path, output_root)}
                _set_stage(record, "geometry_manifest", "passed")
            except Exception as error:
                _set_stage(record, "geometry_manifest", "failed", **_exception_details(error))
            _checkpoint(record, record_path)

            reference_path = candidate_dir / "physical_reference.spice"
            try:
                physical_spec = copy.deepcopy(build_spec)
                physical_spec["mos"] = [
                    {key: device[key] for key in
                     ("name", "polarity", "d", "g", "s", "w_um", "l_um")}
                    for device in manifest["devices"]]
                logic.write_reference(physical_spec, reference_path,
                                      top_name=candidate_id, library=True)
                record.setdefault("artifacts", {})["physical_reference"] = _path_record(
                    reference_path, output_root)
                _set_stage(record, "physical_reference", "passed")
            except Exception as error:
                _set_stage(record, "physical_reference", "failed", **_exception_details(error))
            _checkpoint(record, record_path)

            gds_path = candidate_dir / "candidate.gds"
            try:
                layout.write(str(gds_path))
                record.setdefault("artifacts", {})["gds"] = _path_record(gds_path, output_root)
                _set_stage(record, "gds_write", "passed")
            except Exception as error:
                _set_stage(record, "gds_write", "failed", **_exception_details(error))
            _checkpoint(record, record_path)

            physical_evidence = None
            if record["stages"]["gds_write"]["status"] != "passed":
                _set_stage(record, "strict_physical_verification", "skipped",
                           reason="candidate GDS generation failed")
            elif record["stages"]["physical_reference"]["status"] != "passed":
                _set_stage(record, "strict_physical_verification", "skipped",
                           reason="physical reference generation failed")
            elif "verify" not in modules:
                _set_stage(record, "strict_physical_verification", "failed",
                           **_module_failure("verify", dependency_errors))
            else:
                verification_dir = candidate_dir / "verification"
                try:
                    physical_evidence = modules["verify"].verify_physical(
                        gds_path, reference_path, candidate_id, verification_dir)
                    required = ("strict_drc", "strict_lvs", "pex", "assembly")
                    if (not physical_evidence.get("passed") or
                            not all(physical_evidence.get(key) is True for key in required)):
                        raise RuntimeError(
                            "native verifier returned without all strict DRC/LVS/PEX/assembly gates passing")
                    _set_stage(record, "strict_physical_verification", "passed",
                               evidence=physical_evidence,
                               artifacts=_existing_artifacts(
                                   output_root,
                                   verification_dir=verification_dir,
                                   physical_json=verification_dir / "physical.json",
                                   drc_log=verification_dir / "drc.log",
                                   drc_report=verification_dir / "strict.drc.lyrdb",
                                   lvs_log=verification_dir / "lvs.log",
                                   pex_log=verification_dir / "pex.log",
                                   extracted=verification_dir / "extracted.cir",
                                   parasitics=verification_dir / "parasitics.spice",
                                   postlayout=verification_dir / "postlayout.spice",
                               ))
                except Exception as error:
                    details = _exception_details(error)
                    evidence = getattr(error, "evidence", None)
                    if evidence is not None:
                        details["evidence"] = evidence
                    details["artifacts"] = _existing_artifacts(
                        output_root,
                        verification_dir=verification_dir,
                        physical_json=verification_dir / "physical.json",
                        drc_log=verification_dir / "drc.log",
                        drc_report=verification_dir / "strict.drc.lyrdb",
                        lvs_log=verification_dir / "lvs.log",
                        pex_log=verification_dir / "pex.log",
                        extracted=verification_dir / "extracted.cir",
                        parasitics=verification_dir / "parasitics.spice",
                        postlayout=verification_dir / "postlayout.spice",
                    )
                    _set_stage(record, "strict_physical_verification", "failed", **details)
            _checkpoint(record, record_path)

            if record["stages"]["strict_physical_verification"]["status"] != "passed":
                _set_stage(record, "postlayout_state", "skipped",
                           reason="postlayout storage requires successful strict physical verification")
            elif characterize is None:
                _set_stage(record, "postlayout_state", "failed",
                           **_storage_characterization_details(integration_method),
                           **_module_failure("characterize", dependency_errors),
                           artifacts={})
            else:
                state_dir = candidate_dir / "postlayout_state"
                postlayout_value = physical_evidence.get("postlayout_spice")
                if not postlayout_value:
                    storage_stage = {
                        "status": "failed",
                        **_storage_characterization_details(integration_method),
                        "error_type": "MissingArtifact",
                        "error": "strict physical verification did not provide postlayout_spice",
                        "artifacts": {},
                    }
                else:
                    storage_stage = _run_postlayout_storage(
                        spec, candidate_id, Path(postlayout_value), state_dir,
                        output_root, characterize, integration_method=integration_method)
                _set_stage(
                    record, "postlayout_state", storage_stage.pop("status"),
                    **storage_stage)
            _checkpoint(record, record_path)
            _record_candidate(summary, record, record_path, output_root, summary_path)

    summary["status"] = ("complete_with_verified_unpublished_candidates"
                          if summary["verified_unpublished_candidates"]
                          else "complete_no_verified_unpublished_candidates")
    summary["attempt_count"] = len(summary["candidates"])
    summary["failure_count"] = len(summary["failures"])
    _write_json(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="new or empty directory outside libs.tech and libs.ref")
    parser.add_argument("--cell", action="append", choices=TARGET_CELLS,
                        help="cell to attempt; repeat to select several (default: all four)")
    parser.add_argument("--architecture", choices=ARCHITECTURES,
                        default=DEFAULT_ARCHITECTURE,
                        help="geometry architecture (default: channel)")
    parser.add_argument("--height", type=float,
                        help="one explicit candidate height; default channel runs template minimum and +50um")
    parser.add_argument("--mirror", action="store_true",
                        help="mirror the selected candidate; default is unmirrored")
    parser.add_argument("--integration-method", choices=("trap", "gear"), default="trap",
                        help="explicit transient integration method, recorded with every storage experiment")
    parser.add_argument("--bench-engine", choices=("ngspice", "xyce"), default="ngspice",
                        help="bench engine; xyce requires the configured FD card and a local Xyce")
    parser.add_argument("--bench-binding", choices=("nominal", "configured"), default="nominal",
                        help="nominal stage-1 card, or the config's model binding (FD card)")
    args = parser.parse_args(argv)
    try:
        if "bench_engine" not in modules or "model_binding" not in modules:
            raise RuntimeError("bench engine modules are unavailable: %s" % sorted(dependency_errors))
        binding_mod, bench_mod = modules["model_binding"], modules["bench_engine"]
        binding = (binding_mod.load() if args.bench_binding == "configured"
                   else binding_mod.nominal())
        set_bench(bench_mod.bench_for(
            binding=binding, engine=args.bench_engine,
            work=Path(args.output_dir).expanduser().resolve() / "bench",
            method=args.integration_method))
        selected_heights = (args.height,) if args.height is not None else None
        summary = run_closure(
            args.output_dir, cells=args.cell or TARGET_CELLS,
            architecture=args.architecture, heights=selected_heights,
            mirror=args.mirror, integration_method=args.integration_method)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"status": summary["status"],
                      "attempt_count": summary["attempt_count"],
                      "failure_count": summary["failure_count"],
                      "summary": str((args.output_dir.expanduser().resolve() /
                                      "sequential_closure.json"))},
                     sort_keys=True))
    return 0 if summary["verified_unpublished_candidates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
