#!/usr/bin/env python3
"""Export all canonical MOS references and verify selected cell GDS views."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
STD_CELL_SOURCE = ROOT / "libs.tech/stdcell"
sys.path.insert(0, str(STD_CELL_SOURCE))

import geometry
import logic
import verify

LIBRARY = ROOT / "libs.ref/ICPS2023_5_gen_sc"
GDS_DIR = LIBRARY / "gds"
SPICE_DIR = LIBRARY / "spice"
DOC_DIR = LIBRARY / "doc"


def _physical_summary(evidence: dict) -> dict:
    keys = ("process_profile", "strict_drc", "strict_lvs", "pex", "assembly",
            "passed", "geometry", "drc_markers", "gds_sha256", "deck_sha256")
    return {key: evidence[key] for key in keys if key in evidence}


def _build_one(name: str, spec: dict) -> dict:
    sequential = bool(spec.get("sequential"))
    height_um = 210 if sequential else 160
    route_channel_um = 40 if sequential else 0
    record = {"cell": name, "height_um": height_um,
              "route_channel_um": route_channel_um}
    with tempfile.TemporaryDirectory(prefix="icps_gen_sc_") as temporary:
        work = Path(temporary)
        try:
            layout, top, manifest = geometry.build_cell(
                spec, height_um, route_channel_um=route_channel_um)
        except geometry.GeometryError as error:
            record.update(status="geometry_failed", error=str(error))
            return record

        layout.rename_cell(top.cell_index(), name)
        gds_path = work / f"{name}.gds"
        reference_path = work / f"{name}.spice"
        layout.write(str(gds_path))
        logic.write_reference(spec, reference_path, top_name=name)
        try:
            evidence = verify.verify_physical(
                gds_path, reference_path, name, work / "physical")
        except verify.VerificationError as error:
            record.update(status="strict_physical_failed", error=str(error),
                          physical=_physical_summary(error.evidence),
                          bbox_um=manifest.get("bbox_um"),
                          area_um2=manifest.get("area_um2"))
            return record

        published_gds = GDS_DIR / gds_path.name
        published_gds.write_bytes(gds_path.read_bytes())
        record.update(status="strict_physical_pass", gds=published_gds.name,
                      bbox_um=manifest.get("bbox_um"),
                      area_um2=manifest.get("area_um2"),
                      route_order=manifest.get("routing", {}).get("route_order_variant"),
                      physical=_physical_summary(evidence))
        return record


def _verify_published(name: str, spec: dict, gds_path: Path) -> dict:
    record = {"cell": name, "gds": gds_path.name}
    with tempfile.TemporaryDirectory(prefix="icps_gen_sc_verify_") as temporary:
        work = Path(temporary)
        reference_path = work / f"{name}.spice"
        logic.write_reference(spec, reference_path, top_name=name)
        try:
            evidence = verify.verify_physical(
                gds_path, reference_path, name, work / "physical")
        except verify.VerificationError as error:
            record.update(status="strict_physical_failed", error=str(error),
                          physical=_physical_summary(error.evidence))
            return record
        record.update(status="strict_physical_pass",
                      physical=_physical_summary(evidence))
        return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-cell", action="append", default=[], metavar="CELL",
        help="build and strictly verify one cell; repeat to select multiple cells")
    args = parser.parse_args()

    for directory in (GDS_DIR, SPICE_DIR, DOC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    specs = logic.cell_specs()
    unknown = sorted(set(args.build_cell) - set(specs))
    if unknown:
        parser.error("unknown cell name(s): " + ", ".join(unknown))

    spice_path = SPICE_DIR / "ICPS2023_5_gen_sc.spice"
    logic.write_reference({"specs": specs}, spice_path, library=True)
    build_attempts = {name: _build_one(name, specs[name]) for name in dict.fromkeys(args.build_cell)}

    records = []
    published_names = {path.stem for path in GDS_DIR.glob("*.gds")}
    unknown_gds = sorted(published_names - set(specs))
    if unknown_gds:
        raise RuntimeError("GDS file names have no canonical cell specification: " +
                           ", ".join(unknown_gds))
    for name, spec in specs.items():
        gds_path = GDS_DIR / f"{name}.gds"
        if gds_path.exists():
            record = _verify_published(name, spec, gds_path)
        else:
            record = {
                "cell": name,
                "status": "not_published",
                "reason": "No strict-verified GDS is included for this cell.",
            }
        if name in build_attempts:
            record["build_attempt"] = build_attempts[name]
        records.append(record)

    source_hash = hashlib.sha256(
        json.dumps(specs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "library": "ICPS2023_5_gen_sc",
        "status": "experimental_partial",
        "source_specs_sha256": source_hash,
        "spice_file": spice_path.relative_to(LIBRARY).as_posix(),
        "cells": records,
    }
    manifest_path = DOC_DIR / "cell_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    print(json.dumps({"manifest": str(manifest_path.relative_to(ROOT)),
                      "statuses": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
