#!/usr/bin/env python3
"""Measure the real frame and compare private row/PDN experiments.

This is the M0 feasibility gate, not a qualified placement site or a foundry
signoff flow. Failed geometry and routes are retained, never area-ranked.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import characterize
import core_router
import frame
import library
import logic
import metrics
import verify

M0_CELLS = ["INV_X1", "NAND2_X1", "AOI21_X1"]


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _generator() -> Any:
    path = ROOT / "libs.ref/ICPS2023_5_gen_sc/tools/make_gen_sc.py"
    loader = importlib.util.spec_from_file_location("icps_candidate_library", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


def _cell_evidence(record: dict[str, Any], directory: Path) -> dict[str, Any]:
    result = {key: record[key] for key in
              ("cell", "status", "error", "cell_width_um", "cell_height_um", "area_um2")
              if key in record}
    result["external_pin_escape_proven"] = False
    result["row_qualified"] = False
    if record["status"] != "strict_physical_pass":
        return result
    physical_path = directory / "verification" / record["cell"] / "physical.json"
    physical = json.loads(physical_path.read_text(encoding="utf-8"))
    result["physical_evidence"] = str(physical_path)
    generator = record["generator_manifest"]
    result["contacts_count"] = generator["contacts_count"]
    result["tin_wire_length_um"] = generator["tin_length_um"]
    try:
        electrical = characterize.characterize_cell(
            record["physical_spec"], physical["postlayout_spice"],
            directory / "characterization" / record["cell"], logic.evaluate)
        result["postlayout_logic"] = True
        result["worst_cell_arc_delay_ns"] = electrical["worst_delay_ns"]
        result["timing_objective"] = "feasibility_only; cell arc is not a clock period or fMAX"
    except (characterize.CharacterizationError, RuntimeError, ValueError, OSError,
            subprocess.SubprocessError) as error:
        result["postlayout_logic"] = False
        result["electrical_error"] = str(error)
    return result


def _routed_evidence(route: dict[str, Any], directory: Path,
                     cells: list[dict[str, Any]], *,
                     expected_outputs: dict[str, str] | None = None,
                     evaluate: Any = logic.evaluate,
                     postlayout_check: Any = None) -> dict[str, Any]:
    """Translate observed physical results, never router status names, into gates."""
    record: dict[str, Any] = {
        "candidate": str(directory.parent.name + "/" + directory.name),
        "process_legal": route.get("native_layers_only") is True,
        "functional_correctness": False,
        "strict_drc": False, "strict_lvs": False, "pex": False, "postlayout": False,
        "route_connectivity": False, "power_connectivity": False, "pin_access": False,
        "routed_core_area_um2": None,
        "routed_core_area_complete": False,
        "storage_present": None,
        "allocated_core_area_um2": route.get("allocated_core_area_um2"),
        "cell_geometry_area_um2": route.get("cell_geometry_area_um2"),
        "overflow_count": route.get("overflow_count"),
        "route_evidence": route["artifacts"]["route_evidence"],
        "area_domain": "experimental routed logic core including actual signal and supply routes; excludes unintegrated frame/IO",
        "frame_power_interface_qualified": False,
        "clock_period_ns": None,
    }
    mapped_path = Path(route["artifacts"]["mapped_netlist"])
    mapped = json.loads(mapped_path.read_text(encoding="utf-8")) if mapped_path.is_file() else None
    if mapped is not None:
        canonical = logic.cell_specs()
        record["storage_present"] = any(
            canonical[instance["cell"]].get("sequential") for instance in mapped["instances"])
    connectivity = route.get("connectivity") or {}
    record["short_count"] = len(connectivity["shorts"]) if "shorts" in connectivity else None
    record["route_connectivity"] = connectivity.get("passed") is True
    record["power_connectivity"] = all(
        connectivity.get("nets", {}).get(net, {}).get("connected") is True
        for net in ("VDD", "VSS"))
    access = route.get("pin_access") or {}
    record["pin_access"] = (access.get("passed") is True and
                            access.get("routed_to_net_components_passed") is True)
    crossings = route.get("crossing_demand") or {}
    record["crossing_count"] = crossings.get("actual_centerline_crossings")
    record["crossing_count_scope"] = "observed external ML1 centerlines; not a topology lower bound"
    record["contact_count"] = None
    record["local_resistance_ohm_proxy"] = None
    record["power_cost"] = None
    record["longest_detour"] = None
    record["pin_conflicts"] = None
    record["search_difficulty"] = None
    routes = list((route.get("net_routes") or {}).values())
    paths = [path for net in routes for path in net.get("paths", [])]
    if routes and all("expanded_nodes" in net for net in routes):
        record["search_difficulty"] = sum(net["expanded_nodes"] for net in routes)
        record["search_difficulty_scope"] = "actual A* expanded nodes, including failed attempts"
    if routes and all("detour_um" in path for path in paths):
        record["longest_detour"] = max((path["detour_um"] for path in paths), default=0.0)
        record["longest_detour_scope"] = "maximum added path length beyond its endpoint Manhattan distance, um"
    pins = access.get("pins", [])
    if pins and all("blocked_access_candidate_count" in pin for pin in pins):
        record["pin_conflicts"] = sum(pin["blocked_access_candidate_count"] for pin in pins)
        record["pin_conflicts_scope"] = "foreign-metal-blocked full-width landing points on the stated route lattice"
    record["power_cost"] = (route.get("power_topology_plan") or {}).get("added_ml1_area_um2")
    record["power_cost_scope"] = "actual added ML1 supply conductor area, um2; not IR-drop qualification"
    if route.get("route_proof_passed") is not True:
        record["failure"] = route.get("failure")
        return record
    try:
        physical = verify.verify_physical(
            route["artifacts"]["gds"], route["artifacts"]["reference_spice"],
            route["top_cell"], directory / "verification")
    except verify.VerificationError as error:
        physical = error.evidence
        record["failure"] = {"stage": "strict_physical", "error": str(error)}
    record.update({key: physical.get(key) is True for key in ("strict_drc", "strict_lvs", "pex")})
    record["physical_evidence"] = str(directory / "verification/physical.json")
    if physical.get("passed") is not True or physical.get("assembly") is not True:
        return record
    by_cell = {cell["cell"]: cell for cell in cells}
    record["contact_count"] = sum(by_cell[instance["cell"]]["contacts_count"]
                                  for instance in mapped["instances"])
    record["contact_count"] += route["route_geometry"]["native_cnt_tin_ml1_contacts_added"]
    record["local_resistance_ohm_proxy"] = sum(
        by_cell[instance["cell"]]["tin_wire_length_um"] * 20.0 / 6.0
        for instance in mapped["instances"])
    record["local_resistance_proxy_scope"] = "TiN total length at 6um reference width; not effective network R"
    spec = {"name": route["top_cell"], "pins": physical["assembly_stats"]["pins"],
            "inputs": [pin for pin, direction in mapped["ports"].items() if direction == "input"],
            "outputs": [pin for pin, direction in mapped["ports"].items() if direction == "output"],
            "function": {"Y": "A & B"} if expected_outputs is None else expected_outputs}
    try:
        if record["storage_present"] and postlayout_check is None:
            raise characterize.CharacterizationError("stateful core requires a stateful postlayout bench")
        checker = characterize.characterize_cell if postlayout_check is None else postlayout_check
        electrical = checker(
            spec, physical["postlayout_spice"], directory / "characterization", evaluate)
        record["functional_correctness"] = electrical["logic_levels_passed"] is True
        record["postlayout"] = True
        record["worst_path_arc_delay_ns"] = electrical.get("worst_delay_ns")
        record["timing_scope"] = electrical.get(
            "timing_scope", "sensitized combinational paths at stated model/load/slew; no clock target")
        record["clock_period_ns"] = electrical.get("clock_period_ns")
        if record["storage_present"]:
            for key in ("preservation_correctness", "hold_correctness", "pulse_correctness"):
                record[key] = electrical.get(key) is True
        record["characterization"] = str(directory / "characterization/characterization.json")
    except (characterize.CharacterizationError, RuntimeError, ValueError, OSError,
            subprocess.SubprocessError) as error:
        record["failure"] = {"stage": "postlayout_spice", "error": str(error)}
        return record
    gates = ["process_legal", "functional_correctness", "strict_drc", "strict_lvs", "pex",
             "postlayout", "route_connectivity", "power_connectivity", "pin_access"]
    if record["storage_present"]:
        gates += ["preservation_correctness", "hold_correctness", "pulse_correctness"]
    if all(record[key] is True for key in gates):
        record["routed_core_area_um2"] = record["allocated_core_area_um2"]
        record["routed_core_area_complete"] = True
    return record


def run_feasibility(output_dir: str | Path, *, config_path: str | Path = HERE / "config.json",
                    heights_um: list[float] | None = None,
                    widths_um: list[float] | None = None,
                    fingers: list[int] | None = None,
                    architectures: list[str] | None = None,
                    pin_orders: list[str] | None = None,
                    mirrors: list[bool] | None = None) -> dict[str, Any]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config["process_profile"] != "icps2023_5_1m":
        raise ValueError("M0 requires the canonical icps2023_5_1m process profile")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    row_config = config["row_candidates"]
    nominal = {float(count * row_config["pitch_lower_bound_um"]): count
               for count in row_config["nominal_track_counts"]}
    heights = sorted(set(heights_um if heights_um is not None else
                         [*nominal, *row_config["additional_height_um"]]))
    widths = widths_um if widths_um is not None else [logic.NOMINAL_W_UM]
    folds = fingers if fingers is not None else [1]
    architectures = architectures if architectures is not None else ["compact_rows", "channel"]
    pin_orders = pin_orders if pin_orders is not None else ["declared"]
    mirrors = mirrors if mirrors is not None else [False]
    if not architectures or set(architectures) - {"compact_rows", "channel"}:
        raise ValueError("architectures must select compact_rows and/or channel")
    if not pin_orders or set(pin_orders) - {"declared", "reverse_signals"}:
        raise ValueError("pin_orders must select declared and/or reverse_signals")
    if not mirrors or any(not isinstance(value, bool) for value in mirrors):
        raise ValueError("mirrors must be a nonempty list of booleans")
    if any(not math.isfinite(value) or value <= 0 for value in [*heights, *widths]):
        raise ValueError("candidate heights and widths must be positive and finite")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in folds):
        raise ValueError("finger counts must be positive integers")
    frame_config = config["frame"]
    keepout = frame_config["keepout_um"]
    measured_frame = frame.measure_frame(
        ROOT / frame_config["gds"], die_bbox=frame_config["die_bbox_um"],
        pad_keepout_um=keepout["pad"], frame_keepout_um=keepout["frame"],
        edge_keepout_um=keepout["edge"], power_interface=frame_config["power_interface"])
    _save(output_dir / "frame.json", measured_frame)
    generator = _generator()
    report: dict[str, Any] = {
        "schema": "icps2023_5_m0_feasibility_v1", "process_profile": config["process_profile"],
        "frame_report": str(output_dir / "frame.json"),
        "status": "running", "cells": M0_CELLS, "row_candidates": [], "routes": [],
        "rfc_ranked_candidates": [], "objective": config["objective"],
        "coverage": {"heights_um": heights, "device_widths_um": widths, "fingers": folds,
                     "architectures": architectures, "pin_orders": pin_orders,
                     "mirrors": mirrors, "route_channel_um": 0.0,
                     "power_topologies": row_config["compare_pdn_topologies"],
                     "benchmark": "same NAND plus inverter Boolean A&B for every row/PDN variant"},
        "limits": ["Geometric track-count assumption is not a qualified row or placement site.",
                   "Below-template-minimum rejection is not a process-wide impossibility proof.",
                   "No abutment or placement-site qualification follows from isolated or routed results.",
                   "No frame bondout/power/IO/ESD integration or manufacturing qualification.",
                   "No measured geometry-scaled compact model or routed wire-coupling calibration.",
                   "No clock target: delay is reported, never an area ranking objective."],
    }
    report_path = output_dir / "feasibility.json"
    for height, width, fold, architecture, pin_order, mirror in itertools.product(
            heights, widths, folds, architectures, pin_orders, mirrors):
        name = (f"{architecture}_h{height:g}_w{width:g}_f{fold}_{pin_order}_m{int(mirror)}").replace(".", "p")
        directory = output_dir / name
        candidate = generator.build_candidate_library(
            directory / "library", M0_CELLS, height_um=height, width_um=width, fingers=fold,
            architecture=architecture, pin_order_mode=pin_order, mirror=mirror)
        cells = [_cell_evidence(record, directory / "library") for record in candidate["attempts"]]
        row = {"candidate": name, "height_um": height, "device_width_um": width,
               "fingers": fold, "nominal_track_count": nominal.get(height),
               "architecture": architecture, "pin_order": pin_order, "mirror": mirror,
               "track_count_is_assumption": True, "cells": cells,
               "manifest": candidate["manifest_path"], "row_qualified": False}
        report["row_candidates"].append(row)
        accepted = {cell["cell"] for cell in cells
                    if cell["status"] == "strict_physical_pass" and cell.get("postlayout_logic") is True}
        for topology in row_config["compare_pdn_topologies"]:
            route_dir = directory / topology
            if not set(M0_CELLS) <= accepted:
                record = {"candidate": name + "/" + topology, "routed_core_area_um2": None,
                          "failure": {"stage": "row_cells", "error": "Required M0 cell geometry/physical/logic gate failed."}}
            else:
                route = core_router.route_core(None, route_dir, manifest_path=candidate["manifest_path"],
                                               power_topology=topology)
                record = _routed_evidence(route, route_dir, cells)
                bbox = (route.get("placement") or {}).get("allocated_bbox_um")
                if bbox is not None:
                    fit = frame.find_rectangular_core_fit(
                        measured_frame, bbox[2] - bbox[0], bbox[3] - bbox[1])
                    _save(route_dir / "frame_fit.json", fit)
                    record["frame_polygon_fit"] = fit["fits"]
                    record["frame_fit_evidence"] = str(route_dir / "frame_fit.json")
                    record["frame_placement_bbox_um"] = fit["placement_bbox_um"]
            record["require_frame_polygon_fit"] = True
            record.setdefault("frame_polygon_fit", False)
            record["frame_power_interface_qualified"] = False
            record["assessment"] = metrics.assess_candidate(
                record, target_clock_period_ns=config["objective"]["target_clock_period_ns"],
                core_available_area_um2=measured_frame["core_available_area_um2"])
            report["routes"].append(record)
        _save(report_path, report)
    report["rfc_ranked_candidates"] = metrics.rank_candidates(
        report["routes"], target_clock_period_ns=config["objective"]["target_clock_period_ns"],
        core_available_area_um2=measured_frame["core_available_area_um2"])
    report["status"] = "routed_experimental_candidates" if report["rfc_ranked_candidates"] else "no_routed_candidate"
    report["report_path"] = str(report_path)
    _save(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--height", type=float, action="append")
    parser.add_argument("--width", type=float, action="append")
    parser.add_argument("--fingers", type=int, action="append")
    parser.add_argument("--architecture", choices=("compact_rows", "channel"), action="append")
    parser.add_argument("--pin-order", choices=("declared", "reverse_signals"), action="append")
    mirror_group = parser.add_mutually_exclusive_group()
    mirror_group.add_argument("--mirror", action="store_true")
    mirror_group.add_argument("--compare-mirrors", action="store_true")
    args = parser.parse_args(argv)
    report = run_feasibility(args.out, config_path=args.config, heights_um=args.height,
                             widths_um=args.width, fingers=args.fingers,
                             architectures=args.architecture, pin_orders=args.pin_order,
                             mirrors=[False, True] if args.compare_mirrors else [args.mirror])
    print(json.dumps({"report": report["report_path"], "status": report["status"],
                      "row_candidates": len(report["row_candidates"]),
                      "routed_candidates": len(report["rfc_ranked_candidates"])}))
    return 0 if report["rfc_ranked_candidates"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
