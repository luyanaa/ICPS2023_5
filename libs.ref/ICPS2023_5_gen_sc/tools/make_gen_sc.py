#!/usr/bin/env python3
"""Generate and strictly verify canonical ICPS2023_5 1M cell views."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STD_CELL_SOURCE = ROOT / "libs.tech/stdcell"
sys.path.insert(0, str(STD_CELL_SOURCE))

import geometry
import library
import logic
import verify

LIBRARY = ROOT / "libs.ref/ICPS2023_5_gen_sc"
GDS_DIR = LIBRARY / "gds"
LEF_DIR = LIBRARY / "lef"
SPICE_DIR = LIBRARY / "spice"
DOC_DIR = LIBRARY / "doc"
MANIFEST_PATH = DOC_DIR / "cell_manifest.json"
LOGICAL_SPICE = "spice/ICPS2023_5_gen_sc.spice"


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _physical_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    keys = ("process_profile", "strict_drc", "strict_lvs", "pex", "assembly",
            "passed", "geometry", "drc_markers", "mos_access", "gds_sha256",
            "reference_sha256", "deck_sha256", "pex_sha256", "assembly_stats")
    return {key: evidence[key] for key in keys if key in evidence}


def _physical_spec(spec: dict[str, Any], devices: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(spec)
    result["mos"] = [{key: device[key] for key in
                       ("name", "polarity", "d", "g", "s", "w_um", "l_um")}
                      for device in devices]
    return result


def _parameters(spec: dict[str, Any], args: argparse.Namespace,
                previous: dict[str, Any] | None = None) -> dict[str, Any]:
    default_height = 210.0 if spec.get("sequential") else 160.0
    default_channel = 40.0 if spec.get("sequential") else 0.0
    old = (previous or {}).get("parameters", {})
    return {
        "height_um": args.height_um if args.height_um is not None
                     else old.get("height_um", default_height),
        "width_um": args.width_um if args.width_um is not None
                    else old.get("width_um"),
        "fingers": args.fingers if args.fingers is not None
                   else old.get("fingers", 1),
        "pin_order": list(args.pin_order) if args.pin_order is not None
                     else list(old.get("pin_order", spec["pins"])),
        "mirror": args.mirror if args.mirror is not None
                  else old.get("mirror", False),
        "route_channel_um": args.route_channel_um if args.route_channel_um is not None
                            else old.get("route_channel_um", default_channel),
    }


def _write_record_views(record: dict[str, Any], root: Path) -> None:
    paths = record["views"]
    (root / paths["lef"]).parent.mkdir(parents=True, exist_ok=True)
    (root / paths["spice"]).parent.mkdir(parents=True, exist_ok=True)
    (root / paths["lef"]).write_text(library._lef_text(record), encoding="utf-8")
    logic.write_reference(record["physical_spec"], root / paths["spice"],
                          top_name=record["cell"], library=True)


def _fingerprints(record: dict[str, Any], root: Path,
                  provenance: dict[str, Any], logical_spice_sha256: str) -> dict[str, Any]:
    views = {name: _sha256(root / relative)
             for name, relative in record["views"].items()}
    return {
        "source_spec_sha256": _canonical_digest(record["source_spec"]),
        "logic_sha256": provenance["logic_sha256"],
        "generator_sha256": provenance["generator_sha256"],
        "config_sha256": provenance["config_sha256"],
        "model_sha256": provenance["model_sha256"],
        "model_include_sha256": provenance["model_include_sha256"],
        "deck_sha256": provenance["deck_sha256"],
        "parameters_sha256": _canonical_digest(record["parameters"]),
        "physical_spec_sha256": _canonical_digest(record["physical_spec"]),
        "logical_spice_sha256": logical_spice_sha256,
        "gds_sha256": views["gds"],
        "lef_sha256": views["lef"],
        "spice_sha256": views["spice"],
        "views_sha256": _canonical_digest(views),
    }


def _pin_records(spec: dict[str, Any], generator_manifest: dict[str, Any]) -> dict[str, Any]:
    inputs, outputs = set(spec["inputs"]), set(spec["outputs"])
    pins = {}
    for pin, windows in generator_manifest["pins"].items():
        if pin == "VDD":
            direction, use = "INOUT", "POWER"
        elif pin == "VSS":
            direction, use = "INOUT", "GROUND"
        elif pin in inputs and pin in outputs:
            direction, use = "INOUT", "SIGNAL"
        elif pin in outputs:
            direction, use = "OUTPUT", "SIGNAL"
        else:
            direction, use = "INPUT", "SIGNAL"
        pins[pin] = {"direction": direction, "use": use,
                     "windows_um": copy.deepcopy(windows)}
    return pins


def _base_record(name: str, spec: dict[str, Any], parameters: dict[str, Any],
                 generator_manifest: dict[str, Any], physical_spec: dict[str, Any],
                 physical: dict[str, Any]) -> dict[str, Any]:
    width, height = generator_manifest["width_um"], generator_manifest["height_um"]
    return {
        "cell": name,
        "status": "strict_physical_pass",
        "gds": f"{name}.gds",
        "source_spec": copy.deepcopy(spec),
        "physical_spec": physical_spec,
        "parameters": copy.deepcopy(parameters),
        "generator_manifest": generator_manifest,
        "physical": _physical_summary(physical),
        "cell_width_um": width,
        "cell_height_um": height,
        "width_um": width,
        "height_um": height,
        "bbox_um": [0.0, 0.0, width, height],
        "boundary_um": copy.deepcopy(generator_manifest["boundary_um"]),
        "boundary_layer": [100, 0],
        "boundary_source": "generator",
        "area_um2": width * height,
        "site_width_um": None,
        "qualification": "experimental",
        "pins": _pin_records(spec, generator_manifest),
        "obstructions_um": copy.deepcopy(generator_manifest["obstructions_um"]),
        "views": {
            "gds": f"gds/{name}.gds",
            "lef": f"lef/{name}.lef",
            "spice": f"spice/physical/{name}.spice",
        },
    }

def _device_base(name: str) -> str:
    base, separator, suffix = name.rpartition("_f")
    return base if separator and suffix.isdigit() else name

def _build_one(name: str, spec: dict[str, Any], parameters: dict[str, Any],
               stage: Path, provenance: dict[str, Any],
               logical_spice_sha256: str) -> dict[str, Any]:
    build_spec = copy.deepcopy(spec)
    if parameters["width_um"] is not None:
        for mos in build_spec["mos"]:
            mos["w_um"] = parameters["width_um"]
    try:
        layout, top, generator_manifest = geometry.build_cell(
            build_spec, parameters["height_um"], fingers=parameters["fingers"],
            pin_order=parameters["pin_order"], mirror=parameters["mirror"],
            route_channel_um=parameters["route_channel_um"])
    except geometry.GeometryError as error:
        return {"cell": name, "status": "geometry_failed", "error": str(error)}
    for source_mos in spec["mos"]:
        devices = [device for device in generator_manifest["devices"]
                   if _device_base(device["name"]) == source_mos["name"]]
        total_width = parameters["width_um"]
        if total_width is None:
            total_width = source_mos["w_um"]
        if (len(devices) != parameters["fingers"] or
                any(device["w_um"] < 13.0 for device in devices) or
                not math.isclose(sum(device["w_um"] for device in devices), total_width,
                                 rel_tol=0, abs_tol=0.001)):
            return {"cell": name, "status": "geometry_failed",
                    "error": f"folding for {source_mos['name']} does not preserve selected total width"}

    layout.rename_cell(top.cell_index(), name)
    gds_path = stage / "gds" / f"{name}.gds"
    spice_path = stage / "spice/physical" / f"{name}.spice"
    gds_path.parent.mkdir(parents=True, exist_ok=True)
    spice_path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(gds_path))
    physical_spec = _physical_spec(spec, generator_manifest["devices"])
    logic.write_reference(physical_spec, spice_path, top_name=name, library=True)
    try:
        evidence = verify.verify_physical(
            gds_path, spice_path, name, stage / "verification" / name)
    except verify.VerificationError as error:
        return {"cell": name, "status": "strict_physical_failed",
                "error": str(error), "physical": _physical_summary(error.evidence)}

    record = _base_record(name, spec, parameters, generator_manifest,
                          physical_spec, evidence)
    _write_record_views(record, stage)
    record["fingerprints"] = _fingerprints(record, stage, provenance,
                                           logical_spice_sha256)
    return record


def _copy_views(record: dict[str, Any], source_root: Path, stage: Path) -> None:
    for relative in record["views"].values():
        source = source_root / relative
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _verify_existing(record: dict[str, Any], stage: Path) -> dict[str, Any]:
    gds_path = stage / record["views"]["gds"]
    spice_path = stage / record["views"]["spice"]
    try:
        evidence = verify.verify_physical(
            gds_path, spice_path, record["cell"],
            stage / "verification" / record["cell"])
    except verify.VerificationError as error:
        raise RuntimeError(f"strict re-verification failed for {record['cell']}: {error}") from error
    updated = copy.deepcopy(record)
    updated["physical"] = _physical_summary(evidence)
    return updated


def _deck_only_provenance_change(old: dict[str, Any], current: dict[str, Any]) -> bool:
    return ({key: value for key, value in old.items() if key != "deck_sha256"} ==
            {key: value for key, value in current.items() if key != "deck_sha256"})


def _prepare_reverify_records(old_manifest: dict[str, Any], specs: dict[str, dict[str, Any]],
                              current: dict[str, Any], library_root: Path,
                              stage: Path) -> list[dict[str, Any]]:
    if old_manifest.get("status") != "experimental_partial":
        raise RuntimeError("cannot reverify a manifest without experimental_partial status")
    old_provenance = old_manifest.get("provenance")
    if not isinstance(old_provenance, dict) or not _deck_only_provenance_change(
            old_provenance, current):
        raise RuntimeError("source/spec/generator/config/model provenance changed; rebuild the cell")
    if old_manifest.get("source_specs_sha256") != current["source_specs_sha256"]:
        raise RuntimeError("canonical source specifications changed; rebuild affected cells")
    result = []
    old_records = {item["cell"]: item for item in old_manifest["cells"]}
    for name, spec in specs.items():
        previous = old_records[name]
        if previous.get("status") == "not_published":
            result.append({"cell": name, "status": "not_published",
                           "reason": previous.get("reason", "No strict-verified GDS is included for this cell.")})
            continue
        record = copy.deepcopy(previous)
        library._validate_physical_spec(record, spec)
        if record.get("fingerprints", {}).get("source_spec_sha256") != _canonical_digest(spec):
            raise RuntimeError(f"source specification fingerprint is stale for {name}")
        if record.get("fingerprints", {}).get("generator_sha256") != current["generator_sha256"]:
            raise RuntimeError(f"geometry generator changed for {name}; rebuild the cell")
        if record.get("fingerprints", {}).get("config_sha256") != current["config_sha256"]:
            raise RuntimeError(f"configuration changed for {name}; rebuild the cell")
        if record.get("fingerprints", {}).get("model_sha256") != current["model_sha256"]:
            raise RuntimeError(f"model changed for {name}; rebuild the cell")
        if record.get("fingerprints", {}).get("model_include_sha256") != current["model_include_sha256"]:
            raise RuntimeError(f"model include changed for {name}; rebuild the cell")
        for view, key in (("gds", "gds_sha256"), ("lef", "lef_sha256"),
                          ("spice", "spice_sha256")):
            original_path = library._resolve_artifact(library_root, record["views"][view])
            if _sha256(original_path) != record["fingerprints"].get(key):
                raise RuntimeError(f"published {view.upper()} for {name} changed since acceptance")
        gds_source = library._resolve_artifact(library_root, record["views"]["gds"])
        width, height = library._read_gds_boundary(gds_source, name)
        if not math.isclose(width, record["cell_width_um"], abs_tol=0.001) or not math.isclose(
                height, record["cell_height_um"], abs_tol=0.001):
            raise RuntimeError(f"published GDS boundary differs from the manifest for {name}")
        _copy_views(record, library_root, stage)
        record["fingerprints"]["deck_sha256"] = current["deck_sha256"]
        _write_record_views(record, stage)
        result.append(record)
    return result




def _write_logical_spice(specs: dict[str, dict[str, Any]], stage: Path) -> str:
    path = stage / LOGICAL_SPICE
    path.parent.mkdir(parents=True, exist_ok=True)
    logic.write_reference({"specs": specs}, path, library=True)
    return _sha256(path)


def _manifest(records: list[dict[str, Any]], provenance: dict[str, Any],
              logical_spice_sha256: str) -> dict[str, Any]:
    return {
        "library": "ICPS2023_5_gen_sc",
        "status": "experimental_partial",
        "source_specs_sha256": provenance["source_specs_sha256"],
        "provenance": provenance,
        "spice_file": LOGICAL_SPICE,
        "spice_file_sha256": logical_spice_sha256,
        "cells": records,
    }


def _stage_and_publish(records: list[dict[str, Any]], specs: dict[str, dict[str, Any]],
                       provenance: dict[str, Any], logical_spice_sha256: str,
                       stage: Path) -> None:
    manifest = _manifest(records, provenance, logical_spice_sha256)
    candidate_path = stage / "doc/cell_manifest.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # Validate the entire selected library before replacing any advertised view.
    library.load_library(candidate_path, check=True)

    for relative in (LOGICAL_SPICE,):
        destination = LIBRARY / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage / relative, destination)
    for record in records:
        if record.get("status") != "strict_physical_pass":
            continue
        for relative in record["views"].values():
            destination = LIBRARY / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / relative, destination)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(candidate_path, MANIFEST_PATH)


def _check() -> dict[str, int]:
    manifest = library.load_library(check=True)
    counts: dict[str, int] = {}
    for record in manifest["cells"]:
        status = record["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _publish(args: argparse.Namespace, specs: dict[str, dict[str, Any]]) -> dict[str, int]:
    gds_names = {path.stem for path in GDS_DIR.glob("*.gds")} if GDS_DIR.exists() else set()
    unknown_gds = sorted(gds_names - set(specs))
    if unknown_gds:
        raise RuntimeError("GDS file names have no canonical cell specification: " +
                           ", ".join(unknown_gds))
    old_manifest = None
    if MANIFEST_PATH.is_file():
        old_manifest = library.load_library(MANIFEST_PATH, check=False)
    requested = set(args.build_cell)
    if args.rebuild_published:
        requested |= gds_names
    if args.reverify:
        if requested or any(value is not None for value in
                            (args.height_um, args.width_um, args.fingers, args.pin_order,
                             args.route_channel_um)) or args.mirror is not None:
            raise RuntimeError("--reverify cannot be combined with builds or generator parameters")
        if old_manifest is None:
            raise RuntimeError("--reverify requires an existing cell manifest")
    if not requested and not args.reverify and old_manifest is None:
        if gds_names:
            raise RuntimeError("published GDS has no manifest; use --rebuild-published")
        raise RuntimeError("no accepted manifest; select cells with --build-cell")

    # Any unselected publication must already be fully eligible; never bless old
    # GDS merely because strict tools still happen to accept it today.
    if old_manifest is not None and not args.reverify and not args.rebuild_published:
        current_library = library.load_library(MANIFEST_PATH, check=True)
        old_status = {record["cell"]: record["status"] for record in current_library["cells"]}
        pass_names = {name for name, status in old_status.items()
                      if status == "strict_physical_pass"}
        if not requested.issubset(pass_names):
            raise RuntimeError("this phase preserves existing publication statuses; new cells are not selectable")
        if gds_names != pass_names:
            raise RuntimeError("GDS membership differs from accepted records; use --rebuild-published")
        if requested and not requested.issubset(set(specs)):
            raise RuntimeError("invalid cell selection")
    elif old_manifest is not None and args.rebuild_published:
        pass_names = {record["cell"] for record in old_manifest["cells"]
                      if record.get("status") == "strict_physical_pass"}
        if not requested.issubset(pass_names):
            raise RuntimeError("this phase preserves existing publication statuses; new cells are not selectable")
    elif old_manifest is None and not requested:
        raise RuntimeError("no accepted manifest; select cells with --build-cell")

    current = library._current_provenance()
    with tempfile.TemporaryDirectory(prefix="icps_gen_sc_stage_", dir=LIBRARY) as temporary:
        stage = Path(temporary)
        logical_spice_sha256 = _write_logical_spice(specs, stage)
        if args.reverify:
            records = _prepare_reverify_records(old_manifest, specs, current, LIBRARY, stage)
        else:
            old_records = ({record["cell"]: record for record in old_manifest["cells"]}
                           if old_manifest else {})
            records = []
            for name, spec in specs.items():
                if name in requested:
                    record = _build_one(
                        name, spec, _parameters(spec, args, old_records.get(name)),
                        stage, current, logical_spice_sha256)
                    records.append(record)
                else:
                    previous = old_records.get(name)
                    if previous is None or previous.get("status") == "not_published":
                        records.append({"cell": name, "status": "not_published",
                                        "reason": "No strict-verified GDS is included for this cell."})
                        continue
                    _copy_views(previous, LIBRARY, stage)
                    record = copy.deepcopy(previous)
                    _write_record_views(record, stage)
                    record["fingerprints"] = _fingerprints(
                        record, stage, current, logical_spice_sha256)
                    try:
                        records.append(_verify_existing(record, stage))
                    except RuntimeError as error:
                        raise RuntimeError(str(error)) from error

        failed = [record for record in records
                  if record.get("status") != "strict_physical_pass" and
                  record.get("status") != "not_published"]
        if failed:
            details = "; ".join(f"{item['cell']}: {item.get('error', item['status'])}"
                                 for item in failed)
            raise RuntimeError("selected cell verification failed; no publication made: " + details)

        # Attach exact artifact fingerprints after every candidate view exists.
        for record in records:
            if record.get("status") != "strict_physical_pass":
                continue
            record["fingerprints"] = _fingerprints(record, stage, current,
                                                   logical_spice_sha256)
        counts: dict[str, int] = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        _stage_and_publish(records, specs, current, logical_spice_sha256, stage)
        return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-cell", action="append", default=[], metavar="CELL",
                        help="build and strictly verify one cell; repeat to select multiple cells")
    parser.add_argument("--rebuild-published", action="store_true",
                        help="explicitly rebuild every currently published GDS cell")
    parser.add_argument("--reverify", action="store_true",
                        help="re-run strict checks and refresh deck evidence without rebuilding GDS")
    parser.add_argument("--height-um", type=float,
                        help="requested cell height for selected cells")
    parser.add_argument("--width-um", type=float,
                        help="selected total width per source MOS (None preserves the canonical width)")
    parser.add_argument("--fingers", type=int,
                        help="parallel finger count for selected cells")
    parser.add_argument("--pin-order", type=lambda value: value.split(","),
                        help="comma-separated pin permutation for selected cells")
    mirror_group = parser.add_mutually_exclusive_group()
    mirror_group.add_argument("--mirror", dest="mirror", action="store_true",
                              help="mirror selected cell geometry horizontally")
    mirror_group.add_argument("--no-mirror", dest="mirror", action="store_false",
                              help="disable mirroring for selected cells")
    parser.set_defaults(mirror=None)
    parser.add_argument("--route-channel-um", type=float,
                        help="explicit additional routing channel for selected cells")
    parser.add_argument("--check", action="store_true",
                        help="read-only provenance and physical-geometry check")
    args = parser.parse_args()
    specs = logic.cell_specs()
    unknown = sorted(set(args.build_cell) - set(specs))
    if unknown:
        parser.error("unknown cell name(s): " + ", ".join(unknown))
    if args.check and (args.build_cell or args.rebuild_published or args.reverify or
                       args.height_um is not None or args.width_um is not None or
                       args.fingers is not None or args.pin_order is not None or
                       args.mirror is not None or args.route_channel_um is not None):
        parser.error("--check is read-only and cannot be combined with build/reverify options")
    try:
        if args.check:
            counts = _check()
        else:
            counts = _publish(args, specs)
    except (RuntimeError, library.LibraryValidationError, OSError,
            geometry.GeometryError, verify.VerificationError) as error:
        print(f"make_gen_sc: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"manifest": str(MANIFEST_PATH.relative_to(ROOT)),
                      "statuses": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
