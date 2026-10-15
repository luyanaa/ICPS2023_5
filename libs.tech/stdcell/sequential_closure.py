#!/usr/bin/env python3
"""Deterministic, unpublished sequential-cell closure experiments.

This runner uses only the native ICPS2023_5_1m physical verifier. It records
logical-model and postlayout capture/hold evidence as separate stages, never
publishes a cell, and never ranks candidates. Cell geometry area is not routed
core area or placement qualification.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import itertools
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
STD_CELL_SOURCE = Path(__file__).resolve().parent
TARGET_CELLS = ("LATCH_TG_X1", "LATCH_GATE_X1", "DFF_TG_X1", "DFF_GATE_X1")
SWEEP_HEIGHTS_UM = (210.0, 230.0, 250.0)
SWEEP_ROUTE_CHANNELS_UM = (40.0, 0.0, 80.0)
SWEEP_PIN_ORDER_VARIANTS = ("declared", "reverse_signals")
SWEEP_MIRRORS = (False, True)
STATE_LOAD_FF = 10.0
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
PROCESS_PROFILE = "icps2023_5_1m"


def _load_modules():
    source = str(STD_CELL_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)
    modules = {}
    errors = {}
    for name in ("logic", "geometry", "verify", "characterize"):
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


def _signal_pin_order(spec: dict[str, Any], variant: str) -> list[str]:
    declared = list(spec["pins"])
    if variant == "declared":
        return declared
    if variant != "reverse_signals":
        raise ValueError(f"unsupported pin-order variant {variant!r}")
    signals = [pin for pin in declared if pin not in {"VDD", "VSS"}]
    supplies = [pin for pin in declared if pin in {"VDD", "VSS"}]
    return list(reversed(signals)) + supplies


def parameter_sweep(spec: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return the fixed, ordered sweep; no device dimensions are altered."""
    parameters = []
    for height, channel, pin_variant, mirror in itertools.product(
            SWEEP_HEIGHTS_UM, SWEEP_ROUTE_CHANNELS_UM,
            SWEEP_PIN_ORDER_VARIANTS, SWEEP_MIRRORS):
        parameters.append({
            "height_um": height,
            "route_channel_um": channel,
            "pin_order_variant": pin_variant,
            "pin_order": _signal_pin_order(spec, pin_variant),
            "mirror": mirror,
            "fingers": 1,
            "mos_dimensions_changed": False,
        })
    return tuple(parameters)


def _candidate_id(cell: str, index: int, parameters: dict[str, Any]) -> str:
    height = f"{parameters['height_um']:g}"
    channel = f"{parameters['route_channel_um']:g}"
    order = "D" if parameters["pin_order_variant"] == "declared" else "R"
    mirror = int(parameters["mirror"])
    return f"{cell}_C{index:03d}_H{height}_CH{channel}_P{order}_M{mirror}"


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
    record["failure_stage"] = failed[0] if failed else None
    record["accepted_for_publication"] = False
    if all(stage["status"] == "passed" for stage in record["stages"].values()):
        record["status"] = "verified_unpublished_candidate"
    elif any(stage["status"] == "pending" for stage in record["stages"].values()):
        record["status"] = "in_progress"
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
    """Write a model-only subcircuit with explicit state-node stabilization."""
    logic.write_reference(spec, path, library=True)
    lines = [f'.lib "{model_library.resolve()}" b4soi']
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
                rewritten.extend((f"RSTATE{index} {net} 0 1e12",
                                  f"CSTATE{index} {net} 0 {STATE_LOAD_FF:g}f"))
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
        "artifacts": {},
    }
    reference = cell_dir / "logical_model_reference.spice"
    state_dir = cell_dir / "logical_model_state"
    try:
        _logical_reference(spec, reference, logic, characterize.MODELS)
        result = characterize.sequential_state(
            spec, reference, state_dir, load_ff=STATE_LOAD_FF)
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


def _new_candidate(cell: str, candidate_id: str,
                   parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ICPS2023_5_sequential_closure_candidate_v1",
        "cell": cell,
        "candidate_id": candidate_id,
        "parameters": copy.deepcopy(parameters),
        "status": "in_progress",
        "accepted_for_publication": False,
        "qualification": "experimental unpublished candidate; no process, placement-site, or timing qualification",
        "stages": {name: {"status": "pending"} for name in STAGE_NAMES},
        "failed_stages": [],
        "failure_stage": None,
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


def run_closure(output_dir: str | Path, *, cells: Iterable[str] = TARGET_CELLS) -> dict[str, Any]:
    """Run all selected deterministic attempts and persist every stage result."""
    output_root = Path(output_dir).expanduser().resolve()
    selected_cells = tuple(cells)
    if not selected_cells or len(set(selected_cells)) != len(selected_cells):
        raise ValueError("cells must be a nonempty sequence without duplicates")
    unknown = [cell for cell in selected_cells if cell not in TARGET_CELLS]
    if unknown:
        raise ValueError(f"unsupported sequential cells: {unknown}")
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema": "ICPS2023_5_sequential_closure_v1",
        "process_profile": PROCESS_PROFILE,
        "status": "running",
        "scope": "experimental native-1M sequential closure; no publication or process qualification",
        "limits": [
            "no process, timing, placement-site, or row qualification",
            "IO/ESD and SRAM remain deferred",
            "no combinational geometry or behavior is modified",
        ],
        "logical_and_physical_state_evidence_are_separate": True,
        "area_semantics": {
            "cell_geometry_area_um2": "candidate bounding-box area including configured route-channel whitespace",
            "array_geometry_area_um2": None,
            "routed_core_area_um2": None,
            "note": "array and routed-core area are not evaluated by this single-cell runner",
        },
        "selection_policy": "fixed sweep order only; candidates are never ranked",
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
                "scope": "logical_model_reference; not physical-layout evidence",
                **_module_failure("characterize", dependency_errors),
                "artifacts": {},
            }
        else:
            logical_stage = _run_logical_state(
                spec, cell_dir, output_root, characterize, logic)
        _write_json(cell_dir / "logical_model_stage.json", logical_stage)

        for index, parameters in enumerate(parameter_sweep(spec), start=1):
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
                    route_channel_um=parameters["route_channel_um"])
                layout.rename_cell(top.cell_index(), candidate_id)
                record["metrics"] = _area_metrics(manifest)
                record["geometry"] = {
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
                           reason="postlayout state requires successful strict physical verification")
            elif characterize is None:
                _set_stage(record, "postlayout_state", "failed",
                           scope="strictly verified extracted-and-assembled layout; model-based capture/hold only",
                           **_module_failure("characterize", dependency_errors))
            else:
                state_dir = candidate_dir / "postlayout_state"
                characterization_spec = copy.deepcopy(spec)
                characterization_spec["name"] = candidate_id
                try:
                    postlayout_path = Path(physical_evidence["postlayout_spice"])
                    state_result = characterize.sequential_state(
                        characterization_spec, postlayout_path, state_dir,
                        load_ff=STATE_LOAD_FF)
                    state_path = state_dir / "state.json"
                    _set_stage(record, "postlayout_state", "passed",
                               scope="strictly verified extracted-and-assembled layout; model-based capture/hold only",
                               result=state_result,
                               artifacts=_existing_artifacts(
                                   output_root,
                                   state_dir=state_dir,
                                   bench=state_dir / "bench.cir",
                                   simulator_log=state_dir / "ngspice.log",
                                   state=state_path,
                               ))
                except Exception as error:
                    details = _exception_details(error)
                    details["artifacts"] = _existing_artifacts(
                        output_root,
                        state_dir=state_dir,
                        bench=state_dir / "bench.cir",
                        simulator_log=state_dir / "ngspice.log",
                        state=state_dir / "state.json",
                    )
                    _set_stage(record, "postlayout_state", "failed",
                               scope="strictly verified extracted-and-assembled layout; model-based capture/hold only",
                               **details)
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
    args = parser.parse_args(argv)
    try:
        summary = run_closure(args.output_dir, cells=args.cell or TARGET_CELLS)
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
