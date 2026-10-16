#!/usr/bin/env python3
"""Area-first RTL-to-extracted-core experiments inside the measured 1M frame.

A geometric fit is not frame integration: pads have no inferred net assignment,
IO/ESD is unqualified, and supplies are proven only within the experimental core.
"""
from __future__ import annotations

import argparse
from functools import partial
import json
import math
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import benchmarks
import core_router
import feasibility
import frame
import library
import mapping
import metrics
import verify


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _square_width(measurement: dict[str, Any]) -> dict[str, Any]:
    """Choose a proven square envelope, on a 10um engineering search grid."""
    bbox = measurement["available_region"]["bbox_um"]
    high = math.floor(min(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 10.0)
    low = 0
    best = None
    while low < high:
        middle = (low + high + 1) // 2
        fit = frame.find_rectangular_core_fit(measurement, middle * 10.0, middle * 10.0)
        if fit["fits"]:
            low, best = middle, fit
        else:
            high = middle - 1
    if low == 0:
        raise ValueError("measured frame has no 10um-square routing envelope")
    if best is None or best["requested_size_um"] != [low * 10.0, low * 10.0]:
        best = frame.find_rectangular_core_fit(measurement, low * 10.0, low * 10.0)
    return {"width_um": low * 10.0, "search_quantum_um": 10.0,
            "scope": "square routing envelope, not an optimal core shape or qualified row/site",
            "fit": best}


def _failed_record(candidate: str, storage_present: bool, failure: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": candidate, "storage_present": storage_present,
        **{key: False for key in (
            "process_legal", "functional_correctness", "strict_drc", "strict_lvs",
            "pex", "postlayout", "route_connectivity", "power_connectivity",
            "pin_access", "routed_core_area_complete")},
        "routed_core_area_um2": None, "overflow_count": None, "short_count": None,
        "require_frame_polygon_fit": True, "frame_polygon_fit": False,
        "frame_power_interface_qualified": False, "failure": failure,
    }


def _covers(name: str, directory: Path, manifest: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retain both compound ABC and primitive covers; only routed area ranks them."""
    result = benchmarks.map_benchmark(
        name, directory, manifest, library_view="accepted_physical")
    if result["status"] != "mapped":
        return result, []
    covers = [{"method": "area_abc", "netlist": result["canonical"],
               "path": result["mapped_path"], "evidence": result["mapping_evidence_path"]}]
    # Area-oriented ABC is heuristic: even A&B can be covered by NOR+two INV
    # rather than NAND+INV. Keep the accepted primitive baseline as a physical
    # experiment, not an A_cell-only winner or a compatibility mapping fallback.
    try:
        primitive = mapping.map_yosys_netlist(
            result["raw_yosys_json_path"], top=result["top"],
            library_view="physical_qualified", manifest_path=manifest)
    except ValueError as error:
        if not benchmarks._is_unavailable_cell_error(error):
            raise
        _save(directory / "primitive_unavailable.json", {"error": str(error)})
        return result, covers
    signature = lambda netlist: sorted(
        (instance["cell"], sorted(instance["connections"].items())) for instance in netlist["instances"])
    if signature(primitive) != signature(result["canonical"]):
        path = directory / "mapped.accepted_primitive.json"
        mapping.export_netlist(primitive, path)
        semantic = benchmarks.check_mapped_semantics(name, primitive)
        evidence = directory / "semantic_check.accepted_primitive.json"
        _save(evidence, semantic)
        covers.append({"method": "primitive", "netlist": primitive,
                       "path": str(path), "evidence": str(evidence)})
    return result, covers


def run_benchmarks(output_dir: str | Path, *, manifest_path: str | Path,
                   names: list[str] | None = None,
                   power_topologies: list[str] | None = None,
                   max_core_width_um: float | None = None,
                   config_path: str | Path = HERE / "config.json") -> dict[str, Any]:
    """Map, route, extract and electrically check each selected real RTL fixture."""
    import benchmark_spice

    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["process_profile"] != "icps2023_5_1m":
        raise ValueError("routed benchmarks require canonical icps2023_5_1m")
    selected = benchmarks.list_benchmarks() if names is None else list(dict.fromkeys(names))
    if not selected or set(selected) - set(benchmarks.list_benchmarks()):
        raise ValueError("select at least one known RTL benchmark")
    topologies = (config["row_candidates"]["compare_pdn_topologies"]
                  if power_topologies is None else list(dict.fromkeys(power_topologies)))
    if not topologies or set(topologies) - {"edge_rails", "segmented_rails", "local_clusters"}:
        raise ValueError("select at least one implemented physical PDN topology")
    manifest = Path(manifest_path).resolve()
    accepted = library.physical_manifests(manifest)
    if not accepted:
        raise ValueError("manifest has no strict-pass physical cells")
    cells = [{"cell": name, "contacts_count": record["generator_manifest"]["contacts_count"],
              "tin_wire_length_um": record["generator_manifest"]["tin_length_um"]}
             for name, record in accepted.items()]
    frame_gds, frame_args = frame._config_frame_arguments(config_path)
    measurement = frame.measure_frame(frame_gds, **frame_args)
    expected_pads = config["frame"].get("expected_pad_count")
    if expected_pads is not None and measurement["pads"]["count"] != expected_pads:
        raise ValueError("actual frame pad count differs from configured frame contract")
    square = _square_width(measurement)
    width_limit = square["width_um"] if max_core_width_um is None else max_core_width_um
    if (isinstance(width_limit, bool) or not isinstance(width_limit, (int, float)) or
            not math.isfinite(width_limit) or width_limit <= 0):
        raise ValueError("max_core_width_um must be finite and positive")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    _save(out / "frame.json", measurement)
    _save(out / "routing_envelope.json", square)
    capacity = measurement["available_region"]["area_um2"]
    target = config["objective"].get("target_clock_period_ns")
    records = []
    report = {
        "schema_version": 1, "process_profile": "icps2023_5_1m",
        "manifest_path": str(manifest), "manifest_sha256": verify.sha256(manifest),
        "config_path": str(config_path), "config_sha256": verify.sha256(config_path),
        "frame_evidence": str(out / "frame.json"),
        "core_available_area_um2": capacity, "max_core_width_um": width_limit,
        "timing_objective": "feasibility_only", "target_clock_period_ns": target,
        "area_domain": "completed experimental logic core with actual signal/PDN routes and routing whitespace",
        "frame_integration": False, "frame_power_interface_qualified": False,
        "qualification": "nominal model and repository-rule experiments only; no IO/ESD/PVT/silicon claim",
        "records": records, "winners_by_benchmark": {},
    }
    for name in selected:
        mapping_result, covers = _covers(name, out / name / "rtl", manifest)
        reference = json.loads(Path(mapping_result["source_evidence"]["reference_path"]).read_text())
        storage = reference["kind"] == "sequential"
        if not covers:
            record = _failed_record(name, storage, {
                "stage": "accepted_physical_mapping", "error": mapping_result.get("limitation")})
            record["mapping_evidence"] = mapping_result["mapping_evidence_path"]
            record["assessment"] = metrics.assess_candidate(record, target, capacity)
            records.append(record)
        for cover in covers:
            area_lower_bound = mapping.area(cover["netlist"], manifests=accepted)
            for topology in topologies:
                candidate = "/".join((name, cover["method"], topology))
                directory = out / candidate
                if area_lower_bound > capacity:
                    record = _failed_record(candidate, storage, {
                        "stage": "frame_area_lower_bound",
                        "error": "this accepted-cell cover exceeds actual available frame area before routing",
                        "cell_area_lower_bound_um2": area_lower_bound,
                        "not_a_process_impossibility_claim": True,
                    })
                else:
                    route = core_router.route_core(
                        cover["path"], directory, manifest_path=manifest,
                        power_topology=topology, max_core_width_um=width_limit)
                    evaluator = lambda spec, inputs, benchmark=name: benchmarks.evaluate_combinational(benchmark, inputs)
                    record = feasibility._routed_evidence(
                        route, directory, cells, expected_outputs=reference["expected_outputs"],
                        evaluate=evaluator,
                        postlayout_check=partial(benchmark_spice.characterize_benchmark, name))
                    size = (route.get("placement") or {}).get("allocated_bbox_um")
                    if size is not None:
                        fit = frame.find_rectangular_core_fit(
                            measurement, size[2] - size[0], size[3] - size[1])
                        _save(directory / "frame_fit.json", fit)
                        record["frame_polygon_fit"] = fit["fits"]
                        record["frame_fit_evidence"] = str(directory / "frame_fit.json")
                        record["frame_placement_bbox_um"] = fit["placement_bbox_um"]
                    else:
                        record["frame_polygon_fit"] = False
                    record["require_frame_polygon_fit"] = True
                    record["candidate"] = candidate
                record.update({"benchmark": name, "mapping_method": cover["method"],
                               "mapped_netlist": cover["path"], "mapping_evidence": cover["evidence"],
                               "power_topology": topology, "cell_area_lower_bound_um2": area_lower_bound})
                record["assessment"] = metrics.assess_candidate(record, target, capacity)
                _save(directory / "closure.json", record)
                records.append(record)
                _save(out / "comparison.json", report)
        ranked = metrics.rank_candidates(
            [record for record in records if record.get("benchmark", record["candidate"]) == name],
            target, capacity)
        report["winners_by_benchmark"][name] = ranked[0]["candidate"] if ranked else None
        _save(out / "comparison.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--manifest", required=True, help="explicit strictly accepted physical candidate bank")
    parser.add_argument("--benchmark", action="append", choices=benchmarks.list_benchmarks())
    parser.add_argument("--power-topology", action="append", choices=("edge_rails", "segmented_rails", "local_clusters"))
    parser.add_argument("--max-core-width-um", type=float)
    parser.add_argument("--config", default=str(HERE / "config.json"))
    args = parser.parse_args()
    report = run_benchmarks(
        args.out, manifest_path=args.manifest, names=args.benchmark,
        power_topologies=args.power_topology, max_core_width_um=args.max_core_width_um,
        config_path=args.config)
    print(json.dumps({"comparison": str(Path(args.out).resolve() / "comparison.json"),
                      "winners_by_benchmark": report["winners_by_benchmark"]}, indent=2))
    return 0 if all(report["winners_by_benchmark"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
