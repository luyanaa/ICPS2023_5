#!/usr/bin/env python3
"""Report-only N_CH mask geometry and provenance audit.

This tool inventories GDS geometry and reports recipe-derived comparisons. It
is not a DRC rule, process model, or manufacturing qualification check.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import klayout.db as pya

DESIGN_LAYERS = {
    "nch": (21, 0),
    "soi": (3, 0),
    "tin": (5, 0),
    "p_area": (18, 0),
    "n_area": (19, 0),
    "cap": (16, 0),
    "dio": (17, 0),
    "mask": (20, 0),
    "exclude": (63, 63),
}
FAB_LAYERS = {
    "nch": (13, 0),
    "soi": (4, 0),
    "tin": (5, 0),
    "p_plus": (2, 0),
    "n_plus": (3, 0),
}
DESIGN_LAYER_MAPPING = {
    "nch": {"design": [21, 0], "fab": [13, 0]},
    "soi": {"design": [3, 0], "fab": [4, 0]},
    "tin": {"design": [5, 0], "fab": [5, 0]},
    "p_plus": {"design_source": [18, 0], "fab": [2, 0]},
    "n_plus": {"design_source": [19, 0], "fab": [3, 0]},
    "mask": {"design_only": [20, 0]},
    "exclude": {"design_only": [63, 63]},
}
FAB_LAYER_MAPPING = {
    "nch": {"fab": [13, 0]},
    "soi": {"fab": [4, 0]},
    "tin": {"fab": [5, 0]},
    "p_plus": {"fab": [2, 0]},
    "n_plus": {"fab": [3, 0]},
}
PROCESS_QUESTIONS = [
    {"question": "N_CH minimum feature size", "status": "UNCONFIRMED"},
    {"question": "N_CH spacing", "status": "UNCONFIRMED"},
    {"question": "N_CH exposure and polarity", "status": "UNCONFIRMED"},
    {"question": "N_CH overlay tolerance to SOI, gates, and dopant masks",
     "status": "UNCONFIRMED"},
    {"question": "Allowed N_CH regions and whether off-SOI geometry is permitted",
     "status": "UNCONFIRMED"},
    {"question": "Whether explicit 21/0 or legacy NAREA/TiN expansion is the "
     "manufacturing recipe", "status": "UNCONFIRMED"},
]


def layer_region(layout, cell, layer_pair):
    """Return the hierarchical merged region and whether the layer exists."""
    index = layout.find_layer(pya.LayerInfo(*layer_pair))
    if index is None or index < 0:
        return pya.Region(), False
    return pya.Region(cell.begin_shapes_rec(index)).merged(), True


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_top(layout, requested, path):
    if requested:
        cell = layout.cell(requested)
        if cell is None:
            raise ValueError("top cell %r was not found in %s" % (requested, path))
        return cell
    tops = list(layout.top_cells())
    if len(tops) != 1:
        names = ", ".join(cell.name for cell in tops) or "none"
        raise ValueError("%s has %d top cells (%s); specify --top" %
                         (path, len(tops), names))
    return tops[0]


def read_layout(path, requested_top):
    layout = pya.Layout()
    layout.read(str(path))
    top = select_top(layout, requested_top, path)
    return layout, top


def dbu_um(layout):
    value = float(layout.dbu)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("layout has invalid database unit %r" % layout.dbu)
    return value


def area_um2(region, dbu):
    return round(region.area() * dbu * dbu, 6)


def bbox_um(box, dbu):
    return [round(value * dbu, 6) for value in
            (box.left, box.bottom, box.right, box.top)]


def component_records(region, dbu):
    records = []
    for polygon in region.merged().each():
        bounds = polygon.bbox()
        records.append({
            "bbox_um": bbox_um(bounds, dbu),
            "area_um2": round(polygon.area() * dbu * dbu, 6),
        })
    return sorted(records, key=lambda item: (item["bbox_um"], item["area_um2"]))


def region_summary(region, dbu, layer_present=True):
    components = component_records(region, dbu)
    bounds = region.bbox()
    return {
        "layer_present": bool(layer_present),
        "geometry_presence": ("OBSERVED" if components else
                              "OBSERVED_EMPTY" if layer_present else "UNCONFIRMED"),
        "component_count": len(components),
        "area_um2": area_um2(region, dbu),
        "bbox_um": bbox_um(bounds, dbu) if components else None,
        "components": components,
    }


def raw_degenerate_shapes(layout, top, layer_index):
    """Count degenerate raw shapes in reachable cell definitions, when present.

    KLayout regions intentionally discard zero-area geometry. This separate
    inventory keeps representable raw degeneracies visible instead of losing
    them during region merging. Reused cell definitions are counted once.
    """
    if layer_index is None or layer_index < 0:
        return []
    pending = [top]
    visited = set()
    degenerates = []
    while pending:
        cell = pending.pop()
        if cell.cell_index() in visited:
            continue
        visited.add(cell.cell_index())
        for shape in cell.shapes(layer_index).each():
            if shape.is_text():
                continue
            bounds = shape.bbox()
            degenerate = bounds.width() <= 0 or bounds.height() <= 0
            if not degenerate and shape.is_polygon():
                degenerate = shape.polygon.area() == 0
            elif not degenerate and shape.is_path():
                degenerate = shape.path.polygon().area() == 0
            if degenerate:
                degenerates.append({
                    "cell": cell.name,
                    "bbox_dbu": [bounds.left, bounds.bottom,
                                  bounds.right, bounds.top],
                    "shape_type": ("box" if shape.is_box() else
                                   "polygon" if shape.is_polygon() else
                                   "path" if shape.is_path() else "other"),
                })
        for instance in cell.each_inst():
            pending.append(layout.cell(instance.cell_index))
    return sorted(degenerates, key=lambda item: (item["cell"], item["bbox_dbu"],
                                                  item["shape_type"]))


def design_masks(layout, top):
    dbu = dbu_um(layout)
    regions = {}
    present = {}
    for name, pair in DESIGN_LAYERS.items():
        regions[name], present[name] = layer_region(layout, top, pair)
    design_clip = regions["mask"] + regions["exclude"]
    soi = regions["soi"] - design_clip
    tin = regions["tin"] - design_clip
    nch = regions["nch"] - design_clip
    p_area = regions["p_area"] - design_clip
    n_area = regions["n_area"] - design_clip
    cap = regions["cap"] - design_clip
    dio = regions["dio"] - design_clip
    expansion = int(round(2.0 / dbu))
    dio2 = dio.sized(expansion) - (p_area + n_area)
    effective_p = (p_area & soi).sized(expansion) - (tin - cap + dio2)
    effective_n = (n_area & soi).sized(expansion) - (tin - cap + dio2)
    legacy_nch = n_area.interacting(tin).sized(expansion) + nch
    source_layer_presence = {
        "nch": present["nch"], "soi": present["soi"],
        "tin": present["tin"], "p_area": present["p_area"],
        "n_area": present["n_area"], "cap": present["cap"],
        "dio": present["dio"], "mask": present["mask"],
        "exclude": present["exclude"],
    }
    return {
        "dbu_um": dbu,
        "regions": regions,
        "present": present,
        "source_layer_presence": source_layer_presence,
        "nch_explicit": nch,
        "nch_legacy": legacy_nch,
        "soi": soi,
        "tin": tin,
        "effective_p_plus": effective_p,
        "effective_n_plus": effective_n,
        "nch_layer_index": layout.find_layer(pya.LayerInfo(*DESIGN_LAYERS["nch"])),
    }


def fab_masks(layout, top):
    dbu = dbu_um(layout)
    regions = {}
    present = {}
    for name, pair in FAB_LAYERS.items():
        regions[name], present[name] = layer_region(layout, top, pair)
    return {
        "dbu_um": dbu,
        "regions": regions,
        "present": present,
        "nch": regions["nch"],
        "soi": regions["soi"],
        "tin": regions["tin"],
        "effective_p_plus": regions["p_plus"],
        "effective_n_plus": regions["n_plus"],
        "nch_layer_index": layout.find_layer(pya.LayerInfo(*FAB_LAYERS["nch"])),
    }


def relation_observation(nch, soi, tin, effective_p, effective_n, dbu,
                        layer_presence):
    on_soi = nch & soi
    off_soi = nch - soi
    fully_off_soi = []
    partially_off_soi = []
    for component in nch.merged().each():
        one = pya.Region()
        one.insert(component)
        component_area = one.area()
        on_area = (one & soi).area()
        record = component_records(one, dbu)[0]
        if on_area == 0:
            fully_off_soi.append(record)
        elif on_area < component_area:
            partially_off_soi.append(record)
    channel = (soi & tin) - (effective_p + effective_n)
    nch_channel = channel & nch
    uncovered_channel = channel - nch
    channel_area = channel.area()
    coverage = (nch_channel.area() / channel_area) if channel_area else None
    p_overlap = nch & effective_p
    n_overlap = nch & effective_n
    return {
        "soi_relation": {
            "status": "OBSERVED",
            "layer_presence": {
                "nch": layer_presence["nch"],
                "soi": layer_presence["soi"],
            },
            "on_soi_area_um2": area_um2(on_soi, dbu),
            "off_soi_area_um2": area_um2(off_soi, dbu),
            "off_soi_components": component_records(off_soi, dbu),
            "fully_off_soi_component_count": len(fully_off_soi),
            "fully_off_soi_components": fully_off_soi,
            "partially_off_soi_component_count": len(partially_off_soi),
            "partially_off_soi_components": partially_off_soi,
            "interpretation": "geometric observation only; allowed regions are unconfirmed",
        },
        "channel_coverage": {
            "status": "UNCONFIRMED",
            "definition": "geometric channel candidate: (SOI intersect TiN) minus effective P+/N+ masks",
            "layer_presence": {
                "nch": layer_presence["nch"],
                "soi": layer_presence["soi"],
                "tin": layer_presence["tin"],
            },
            "channel_region_area_um2": area_um2(channel, dbu),
            "nch_overlap_area_um2": area_um2(nch_channel, dbu),
            "uncovered_channel_area_um2": area_um2(uncovered_channel, dbu),
            "coverage_fraction": round(coverage, 8) if coverage is not None else None,
            "reason": "no accepted N_CH coverage or allowed-region limit is available",
        },
        "effective_dopant_overlap": {
            "status": "OBSERVED",
            "layer_presence": {
                "nch": layer_presence["nch"],
                "p_plus": layer_presence["p_plus"],
                "n_plus": layer_presence["n_plus"],
            },
            "interpretation": "geometric overlap only; not a process violation",
            "p_plus": {
                "layer": [2, 0],
                "overlap_area_um2": area_um2(p_overlap, dbu),
                "overlap_components": component_records(p_overlap, dbu),
            },
            "n_plus": {
                "layer": [3, 0],
                "overlap_area_um2": area_um2(n_overlap, dbu),
                "overlap_components": component_records(n_overlap, dbu),
            },
        },
    }


def source_candidate_report(candidate, required_layers, mask_data):
    complete = all(mask_data["source_layer_presence"].get(name, False)
                   for name in required_layers)
    return {
        "status": "OBSERVED" if complete else "UNCONFIRMED",
        "required_source_layers_present": {
            name: mask_data["source_layer_presence"].get(name, False)
            for name in required_layers
        },
        "inventory": region_summary(candidate, mask_data["dbu_um"], complete),
    }


def xor_record(candidate, actual, dbu):
    difference = candidate ^ actual
    return {
        "xor_area_um2": area_um2(difference, dbu),
        "xor_inventory": region_summary(difference, dbu, True),
        "matches_geometry": difference.is_empty(),
    }


def audit(input_path, mode, top_name=None, source_path=None,
          source_top_name=None, source_provenance=None):
    layout, top = read_layout(input_path, top_name)
    dbu = dbu_um(layout)
    input_metadata = {
        "mode": mode,
        "path": str(Path(input_path).resolve()),
        "top": top.name,
        "sha256": file_sha256(input_path),
        "dbu_um": dbu,
        "layer_mapping": DESIGN_LAYER_MAPPING if mode == "design" else FAB_LAYER_MAPPING,
    }
    if mode == "design":
        masks = design_masks(layout, top)
        nch = masks["nch_explicit"]
        input_mask_layer = DESIGN_LAYERS["nch"]
        input_mask_present = masks["present"]["nch"]
        soi = masks["soi"]
        tin = masks["tin"]
        effective_p = masks["effective_p_plus"]
        effective_n = masks["effective_n_plus"]
        nch_degenerates = raw_degenerate_shapes(layout, top, masks["nch_layer_index"])
        explicit_summary = source_candidate_report(
            masks["nch_explicit"], ["nch"], masks)
        legacy_summary = source_candidate_report(
            masks["nch_legacy"], ["nch", "n_area", "tin"], masks)
        candidates = {
            "explicit": explicit_summary,
            "legacy": legacy_summary,
            "delta": xor_record(masks["nch_explicit"], masks["nch_legacy"], dbu),
            "formula": {
                "explicit": "NCH (21/0) after fab-prep EXCLUDE/MASK subtraction",
                "legacy": "NAREA.interacting(TIN).sized(2.0 um) + NCH",
            },
        }
        source_data = None
    else:
        masks = fab_masks(layout, top)
        nch = masks["nch"]
        input_mask_layer = FAB_LAYERS["nch"]
        input_mask_present = masks["present"]["nch"]
        soi = masks["soi"]
        tin = masks["tin"]
        effective_p = masks["effective_p_plus"]
        effective_n = masks["effective_n_plus"]
        nch_degenerates = raw_degenerate_shapes(layout, top, masks["nch_layer_index"])
        candidates = None
        actual_fab_nch = nch
        actual_fab_present = input_mask_present
        source_data = None
        if source_path is not None:
            source_layout, source_top = read_layout(source_path, source_top_name)
            source_masks = design_masks(source_layout, source_top)
            source_data = {
                "path": str(Path(source_path).resolve()),
                "top": source_top.name,
                "sha256": file_sha256(source_path),
                "dbu_um": source_masks["dbu_um"],
                "provenance": source_provenance,
                "layer_mapping": DESIGN_LAYER_MAPPING,
                "source_layer_presence": source_masks["source_layer_presence"],
                "candidates": {
                    "explicit": region_summary(source_masks["nch_explicit"],
                                                source_masks["dbu_um"],
                                                source_masks["present"]["nch"]),
                    "legacy": region_summary(source_masks["nch_legacy"],
                                              source_masks["dbu_um"],
                                              all(source_masks["source_layer_presence"].get(name, False)
                                                  for name in ("nch", "n_area", "tin"))),
                },
            }
            # GDS design and fab inputs may use different DBUs. Normalize the
            # source candidate regions into the actual fab layout's DBU before
            # computing the XOR, while retaining source units in metadata.
            if abs(source_masks["dbu_um"] - dbu) > 1e-15:
                scale = source_masks["dbu_um"] / dbu
                explicit_candidate = source_masks["nch_explicit"].transformed(
                    pya.ICplxTrans(scale, 0.0, False, 0.0, 0.0))
                legacy_candidate = source_masks["nch_legacy"].transformed(
                    pya.ICplxTrans(scale, 0.0, False, 0.0, 0.0))
            else:
                explicit_candidate = source_masks["nch_explicit"]
                legacy_candidate = source_masks["nch_legacy"]
            source_complete = all(source_masks["source_layer_presence"].get(name, False)
                                  for name in ("nch", "n_area", "tin"))
            xor_by_candidate = {
                "explicit": xor_record(explicit_candidate, actual_fab_nch, dbu),
                "legacy": xor_record(legacy_candidate, actual_fab_nch, dbu),
            }
            if not actual_fab_present:
                comparison_status = "UNCONFIRMED"
                reason = "actual fab N_CH layer is absent from the fab input"
            elif not source_complete:
                comparison_status = "UNCONFIRMED"
                reason = "one or more required design source layers are absent"
            elif source_provenance is None:
                comparison_status = "UNCONFIRMED"
                reason = "source drawing is present but provenance was not declared"
            elif xor_by_candidate[source_provenance]["matches_geometry"]:
                comparison_status = "PROVENANCE_MATCH"
                reason = "actual fab mask matches the declared source recipe candidate"
            else:
                comparison_status = "PROVENANCE_MISMATCH"
                reason = "actual fab mask differs from the declared source recipe candidate"
            source_data["comparison"] = {
                "status": comparison_status,
                "reason": reason,
                "actual_fab_mask_layer": [13, 0],
                "actual_fab_mask_present": actual_fab_present,
                "declared_provenance": source_provenance,
                "candidate_xors": xor_by_candidate,
                "coordinate_treatment": "direct coordinates; only DBU scale is normalized",
                "process_validity": "UNQUALIFIED; provenance comparison is not process validation",
            }

    if mode == "design":
        relation_presence = {
            "nch": masks["present"]["nch"],
            "soi": masks["present"]["soi"],
            "tin": masks["present"]["tin"],
            "p_plus": True,
            "n_plus": True,
        }
    else:
        relation_presence = {
            "nch": masks["present"]["nch"],
            "soi": masks["present"]["soi"],
            "tin": masks["present"]["tin"],
            "p_plus": masks["present"]["p_plus"],
            "n_plus": masks["present"]["n_plus"],
        }
    relations = relation_observation(nch, soi, tin, effective_p, effective_n, dbu,
                                     relation_presence)
    mask_inventory = region_summary(nch, dbu, input_mask_present)
    mask_inventory["layer"] = list(input_mask_layer)
    mask_inventory["raw_degenerate_shapes"] = nch_degenerates
    mask_inventory["raw_degenerate_shape_count"] = len(nch_degenerates)
    categories = {
        "NCH_MASK_INVENTORY": {
            "status": mask_inventory["geometry_presence"],
            "evidence_grade": "GEOMETRIC_OBSERVATION",
            "details": mask_inventory,
        },
        "NCH_SOI_RELATION": {
            "status": relations["soi_relation"]["status"],
            "evidence_grade": "GEOMETRIC_OBSERVATION",
            "details": relations["soi_relation"],
        },
        "NCH_CHANNEL_COVERAGE": {
            "status": relations["channel_coverage"]["status"],
            "evidence_grade": "UNCONFIRMED",
            "details": relations["channel_coverage"],
        },
        "NCH_EFFECTIVE_P_PLUS_OVERLAP": {
            "status": relations["effective_dopant_overlap"]["status"],
            "evidence_grade": "GEOMETRIC_OBSERVATION",
            "details": relations["effective_dopant_overlap"]["p_plus"],
        },
        "NCH_EFFECTIVE_N_PLUS_OVERLAP": {
            "status": relations["effective_dopant_overlap"]["status"],
            "evidence_grade": "GEOMETRIC_OBSERVATION",
            "details": relations["effective_dopant_overlap"]["n_plus"],
        },
        "PROVENANCE_COMPARISON": {
            "status": (source_data["comparison"]["status"] if source_data else
                       "UNCONFIRMED"),
            "evidence_grade": "GEOMETRY_XOR_ONLY",
            "details": (source_data["comparison"] if source_data else {
                "status": "UNCONFIRMED",
                "reason": ("no source drawing supplied" if mode == "fab" else
                           "input is a design drawing; no actual fab mask supplied"),
                "source": "UNKNOWN",
            }),
        },
        "PROCESS_LIMITS": {
            "status": "UNCONFIRMED",
            "evidence_grade": "UNQUALIFIED",
            "details": PROCESS_QUESTIONS,
        },
    }
    provenance_comparison = categories["PROVENANCE_COMPARISON"]["details"]
    return {
        "schema_version": "1.0",
        "tool": "audit_nch_mask.py",
        "assessment": {
            "status": "REPORT_ONLY",
            "qualification": "UNQUALIFIED",
            "can_sign_off": False,
            "manufacturing_validity": "UNCONFIRMED",
        },
        "input": input_metadata,
        "mask_inventory": mask_inventory,
        "observations": relations,
        "candidates": candidates,
        "source": source_data,
        "source_comparison": provenance_comparison,
        "categories": categories,
        "foundry_questions": PROCESS_QUESTIONS,
    }


def make_parser():
    parser = argparse.ArgumentParser(
        description="Report-only N_CH geometry audit; never a manufacturing sign-off.")
    parser.add_argument("--input", required=True,
                        help="design drawing or fab-mask GDS/OASIS")
    parser.add_argument("--mode", required=True, choices=("design", "fab"),
                        help="interpret input layers as design geometry or fab masks")
    parser.add_argument("--top", help="selected input top cell; required for multi-top files")
    parser.add_argument("--source",
                        help="optional source design drawing for a fab-input XOR comparison")
    parser.add_argument("--source-top",
                        help="selected source drawing top cell; required for multi-top files")
    parser.add_argument("--source-provenance", choices=("explicit", "legacy"),
                        help="declared source recipe; comparison remains report-only")
    parser.add_argument("--output", help="write JSON here instead of standard output")
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.mode != "fab" and (args.source or args.source_top or args.source_provenance):
        parser.error("--source and its options are only meaningful with --mode fab")
    if args.source_top and not args.source:
        parser.error("--source-top requires --source")
    if args.source_provenance and not args.source:
        parser.error("--source-provenance requires --source")
    try:
        report = audit(args.input, args.mode, args.top, args.source,
                       args.source_top, args.source_provenance)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
