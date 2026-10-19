#!/usr/bin/env python3
"""Independent structural IO/pad/ESD evidence gate.

This checker reports geometry that is observable in GDS; it does not supply
missing foundry pad, package, current, or ESD requirements.  A structural pass
is deliberately separate from manufacturing acceptance, which remains blocked
until those requirements and their qualification evidence are available.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - supplied by the pinned KLayout toolchain
    try:
        from klayout import db  # type: ignore
    except ImportError:  # Keep imports usable outside the geometry toolchain.
        db = None  # type: ignore[assignment]


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_CONFIG = HERE / "config.json"
PROFILE_PATH = ROOT / "libs.tech/klayout/process_profiles.json"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import frame  # noqa: E402
import pad_mapping as pad_mapping_contract  # noqa: E402


class IoDfmError(ValueError):
    """Invalid checker input or unreadable layout/profile data."""


DESIGN_LAYERS = {
    "pad_opening": (50, 0),
    "ml1": (8, 0),
    "mask": (20, 0),
    "exclude": (63, 63),
}
FAB_LAYERS = {
    "pad_opening": (10, 0),
    "ml1": (7, 0),
    "mask": (20, 0),
    "exclude": (63, 63),
}


_UNKNOWN_MANUFACTURING_REQUIREMENTS = [
    {
        "topic": "pad-opening geometry and ML1 enclosure/margin",
        "status": "UNAVAILABLE",
        "effect": "exact ML1 support is checked structurally; no foundry margin or opening size is asserted",
    },
    {
        "topic": "die-edge and seal-ring clearance",
        "status": "UNAVAILABLE",
        "effect": "FRAME14/0 drawing contours are not treated as a qualified seal ring",
    },
    {
        "topic": "bond/probe interface and current limits",
        "status": "UNAVAILABLE",
        "effect": "pad dimensions and current capacity are not inferred from ML1 geometry",
    },
    {
        "topic": "ESD discharge topology and HBM/CDM/TLP qualification",
        "status": "UNAVAILABLE",
        "effect": "device declarations are evidence of declaration only, never electrical qualification",
    },
]


def _read_profile_data(path: Path = PROFILE_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IoDfmError(f"cannot read process profile data {path}: {error}") from error
    if not isinstance(value, dict):
        raise IoDfmError("process profile data must contain a JSON object")
    return value


def _resolve_profile(name: str | None, data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    selected = name or os.environ.get("ICPS2023_5_PROCESS_PROFILE") or data.get("default_profile")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(selected, str) or selected not in profiles:
        choices = ", ".join(sorted(profiles)) if isinstance(profiles, dict) else "unavailable"
        raise IoDfmError(f"unknown ICPS2023_5 process profile {selected!r} (choose {choices})")
    profile = profiles[selected]
    layers = data.get("upper_metal_layers")
    if not isinstance(profile, dict) or not isinstance(profile.get("permitted_upper_layers"), list):
        raise IoDfmError(f"malformed process profile {selected!r}")
    if not isinstance(layers, dict):
        raise IoDfmError("process profile data has no upper_metal_layers table")
    return selected, profile


def _read_layout(path: str | Path) -> tuple[Any, Any, float, Path]:
    if db is None:
        raise IoDfmError("KLayout Python bindings are required; use the root Nix devShell")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise IoDfmError(f"GDS file does not exist: {source}")
    layout = db.Layout()
    try:
        layout.read(str(source))
    except Exception as error:
        raise IoDfmError(f"cannot read GDS {source}: {error}") from error
    dbu_um = float(layout.dbu)
    if not math.isfinite(dbu_um) or dbu_um <= 0:
        raise IoDfmError("GDS database unit must be finite and positive")
    tops = list(layout.top_cells())
    if len(tops) != 1:
        raise IoDfmError(f"GDS must have exactly one top cell; found {len(tops)}")
    top = tops[0]
    top.flatten(-1)
    return layout, top, dbu_um, source


def _shape_region(shape: Any) -> Any | None:
    if shape.is_box():
        return db.Region(shape.box)
    if shape.is_polygon():
        return db.Region(shape.polygon)
    if shape.is_path():
        return db.Region(shape.path.polygon())
    if shape.is_text():
        return None
    return None


def _layer_region(layout: Any, top: Any, layer: tuple[int, int]) -> tuple[Any, dict[str, int]]:
    region = db.Region()
    details = {"area_shapes": 0, "text_shapes_ignored": 0, "unsupported_shapes": 0}
    layer_index = layout.find_layer(db.LayerInfo(*layer))
    if layer_index is None:
        return region, details
    for shape in top.shapes(layer_index).each():
        shape_region = _shape_region(shape)
        if shape_region is None:
            if shape.is_text():
                details["text_shapes_ignored"] += 1
            else:
                details["unsupported_shapes"] += 1
            continue
        details["area_shapes"] += 1
        region += shape_region
    return region.merged(), details


def _region_payload(region: Any, dbu_um: float) -> dict[str, Any]:
    return frame._region_payload(region, dbu_um)


def _region_signature(region: Any, dbu_um: float) -> tuple[Any, ...]:
    """Return exact physical contours on a rational common unit grid."""
    unit = Fraction(str(dbu_um))

    def canonical_ring(points: Sequence[Any]) -> tuple[Any, ...]:
        ring = [(Fraction(point.x) * unit, Fraction(point.y) * unit) for point in points]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        # Remove redundant collinear vertices so equivalent GDS polygon encodings
        # compare by geometry rather than by their original vertex subdivision.
        changed = True
        while changed and len(ring) > 3:
            changed = False
            kept = []
            for index, current in enumerate(ring):
                previous = ring[index - 1]
                following = ring[(index + 1) % len(ring)]
                cross = ((current[0] - previous[0]) * (following[1] - current[1]) -
                         (current[1] - previous[1]) * (following[0] - current[0]))
                if cross == 0:
                    changed = True
                else:
                    kept.append(current)
            if len(kept) >= 3:
                ring = kept
            else:
                break
        if not ring:
            return ()
        forward = tuple(ring)
        reverse = tuple(reversed(ring))

        def rotations(values: tuple[Any, ...]) -> list[tuple[Any, ...]]:
            smallest = min(values)
            return [values[index:] + values[:index]
                    for index, point in enumerate(values) if point == smallest]

        return min(rotations(forward) + rotations(reverse))

    polygons = []
    for polygon in region.merged().each():
        hull = [polygon.point_hull(index) for index in range(polygon.num_points_hull())]
        holes = []
        for hole_index in range(polygon.holes()):
            points = [polygon.point_hole(hole_index, index)
                      for index in range(polygon.num_points_hole(hole_index))]
            holes.append(canonical_ring(points))
        polygons.append((canonical_ring(hull), tuple(sorted(holes))))
    return tuple(sorted(polygons))


def _region_equality(left: Any, left_dbu: float, right: Any, right_dbu: float) -> bool:
    return _region_signature(left, left_dbu) == _region_signature(right, right_dbu)


def _upper_layer_check(layout: Any, top: Any, layer_mode: str,
                       process_data: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    if layer_mode not in ("design", "fab"):
        raise IoDfmError("layer_mode must be 'design' or 'fab'")
    upper_layers = process_data.get("upper_metal_layers", {}).get(layer_mode)
    if not isinstance(upper_layers, dict):
        raise IoDfmError(f"process profile data has no {layer_mode!r} upper-layer table")
    permitted = set(profile["permitted_upper_layers"])
    unpermitted = sorted(set(upper_layers) - permitted)
    observed = []
    failures = []
    for name, raw_layer in sorted(upper_layers.items()):
        if (not isinstance(raw_layer, list) or len(raw_layer) != 2 or
                any(isinstance(value, bool) or not isinstance(value, int) for value in raw_layer)):
            raise IoDfmError(f"malformed {layer_mode} layer entry for {name!r}")
        layer = (raw_layer[0], raw_layer[1])
        region, details = _layer_region(layout, top, layer)
        if details["unsupported_shapes"]:
            failures.append({"layer_name": name, "layer": list(layer),
                             "code": "unsupported_upper_layer_shape",
                             "count": details["unsupported_shapes"]})
        if not region.is_empty():
            observed.append({"layer_name": name, "layer": list(layer),
                             "geometry": _region_payload(region, float(layout.dbu))})
            if name in unpermitted:
                failures.append({"layer_name": name, "layer": list(layer),
                                 "code": "unpermitted_upper_layer_geometry",
                                 "geometry": _region_payload(region, float(layout.dbu))})
    return {
        "status": "FAIL" if failures else "PASS",
        "layer_mode": layer_mode,
        "permitted_upper_layers": sorted(permitted),
        "unpermitted_upper_layers": unpermitted,
        "observed_geometry": observed,
        "violations": failures,
    }


def _pad_opening_check(layout: Any, top: Any, layer_mode: str, dbu_um: float) -> dict[str, Any]:
    layers = DESIGN_LAYERS if layer_mode == "design" else FAB_LAYERS
    opening, opening_shapes = _layer_region(layout, top, layers["pad_opening"])
    ml1, ml1_shapes = _layer_region(layout, top, layers["ml1"])
    if opening_shapes["unsupported_shapes"]:
        return {
            "status": "FAIL",
            "opening_layer": list(layers["pad_opening"]),
            "ml1_layer": list(layers["ml1"]),
            "opening_count": 0,
            "unsupported_shape_count": opening_shapes["unsupported_shapes"],
            "all_openings_contained_by_ml1": False,
            "violations": [{"code": "unsupported_pad_opening_shape",
                            "count": opening_shapes["unsupported_shapes"]}],
        }
    if ml1_shapes["unsupported_shapes"]:
        return {
            "status": "FAIL",
            "opening_layer": list(layers["pad_opening"]),
            "ml1_layer": list(layers["ml1"]),
            "opening_count": 0,
            "unsupported_shape_count": ml1_shapes["unsupported_shapes"],
            "all_openings_contained_by_ml1": False,
            "violations": [{"code": "unsupported_ml1_shape",
                            "count": ml1_shapes["unsupported_shapes"]}],
        }
    openings = []
    for polygon in opening.each():
        one_polygon = db.Region()
        one_polygon.insert(polygon)
        openings.append(_region_payload(one_polygon, dbu_um))
    if opening.is_empty():
        return {
            "status": "MISSING",
            "opening_layer": list(layers["pad_opening"]),
            "ml1_layer": list(layers["ml1"]),
            "opening_count": 0,
            "openings": [],
            "all_openings_contained_by_ml1": None,
            "ml1_geometry_present": not ml1.is_empty(),
            "finding": {
                "code": "missing_pad_opening_geometry",
                "message": "No PAD opening polygons are present; none were inferred or generated from ML1.",
            },
            "violations": [],
        }
    unsupported = opening - ml1
    if unsupported.is_empty():
        status = "PASS"
        violations = []
    else:
        status = "FAIL"
        violations = [{"code": "pad_opening_not_exactly_contained_by_ml1",
                       "uncovered_by_ml1": _region_payload(unsupported, dbu_um)}]
    return {
        "status": status,
        "opening_layer": list(layers["pad_opening"]),
        "ml1_layer": list(layers["ml1"]),
        "opening_count": len(openings),
        "openings": openings,
        "all_openings_contained_by_ml1": unsupported.is_empty(),
        "containment_rule": "exact_boolean_containment; no enclosure margin is invented",
        "ml1_geometry_present": not ml1.is_empty(),
        "violations": violations,
    }


def _fabprep_comparison(design_layout: Any, design_top: Any, design_dbu: float,
                        design_path: Path, fab_gds_path: str | Path) -> dict[str, Any]:
    fab_layout, fab_top, fab_dbu, fab_path = _read_layout(fab_gds_path)
    comparisons = []
    for name, design_layer, fab_layer in (
        ("pad_opening", DESIGN_LAYERS["pad_opening"], FAB_LAYERS["pad_opening"]),
        ("ml1", DESIGN_LAYERS["ml1"], FAB_LAYERS["ml1"]),
    ):
        source, source_details = _layer_region(design_layout, design_top, design_layer)
        mask, mask_details = _layer_region(design_layout, design_top, DESIGN_LAYERS["mask"])
        exclude, exclude_details = _layer_region(design_layout, design_top, DESIGN_LAYERS["exclude"])
        source -= mask
        source -= exclude
        target, target_details = _layer_region(fab_layout, fab_top, fab_layer)
        unsupported_shape_count = (
            source_details["unsupported_shapes"] + mask_details["unsupported_shapes"] +
            exclude_details["unsupported_shapes"] + target_details["unsupported_shapes"]
        )
        exact = (_region_equality(source, design_dbu, target, fab_dbu) and
                 unsupported_shape_count == 0)
        comparisons.append({
            "geometry": name,
            "design_layer": list(design_layer),
            "fab_layer": list(fab_layer),
            "matches_actual_fab_gds": exact,
            "status": "PASS" if exact else "FAIL",
            "design_after_fabprep_exclusions": _region_payload(source, design_dbu),
            "observed_fab_geometry": _region_payload(target, fab_dbu),
            "ignored_text_shapes": {
                "design": source_details["text_shapes_ignored"] + mask_details["text_shapes_ignored"] +
                          exclude_details["text_shapes_ignored"],
                "fab": target_details["text_shapes_ignored"],
            },
            "unsupported_shape_count": unsupported_shape_count,
            "unsupported_shapes_are_mismatches": True,
        })
    return {
        "status": "PASS" if all(item["matches_actual_fab_gds"] for item in comparisons) else "FAIL",
        "design_gds": str(design_path),
        "fab_gds": str(fab_path),
        "mapping": "design PAD 50/0 -> fab PAD 10/0; design ML1 8/0 -> fab ML1 7/0",
        "comparison_accounts_for_fabprep": "input 63/63 EXCLUDE and 20/0 MASK subtraction",
        "comparisons": comparisons,
    }


def _frame_evidence(gds_path: Path, expected_pad_count: int | None,
                    layer_mode: str) -> tuple[dict[str, Any], list[str] | None]:
    try:
        measured = frame.measure_frame(
            gds_path,
            layer_mode=layer_mode,
            pad_keepout_um=0,
            frame_keepout_um=0,
            edge_keepout_um=0,
            expected_pad_count=expected_pad_count,
            pro_capacity_multiplier=None,
        )
    except frame.FrameError as error:
        return {"status": "UNRESOLVED", "reason": str(error),
                "function_inferred_from_labels": False}, None
    pad_records = measured["pads"]["rectangles"]
    pad_ids = [record["pad_id"] for record in pad_records]
    return {
        "status": "MEASURED",
        "pad_count": measured["pads"]["count"],
        "ml1_pad_rectangles": pad_records,
        "identity_assignment": measured["pads"]["identity_assignment"],
        "function_inferred_from_labels": False,
        "frame_contour_interpretation": measured["frame"]["contour_interpretation"],
        "engineering_only": True,
    }, pad_ids


def _mapping_evidence(mapping: Any, actual_pad_ids: list[str] | None,
                      expected_pad_count: int | None) -> dict[str, Any]:
    if mapping is None or mapping == {}:
        return {
            "status": "UNRESOLVED",
            "assignments_by_physical_pad": {},
            "unassigned_pad_ids": actual_pad_ids,
            "function_inferred_from_labels": False,
            "message": "No explicit pad-to-function/net mapping was supplied; function remains unresolved.",
        }
    def_path = None
    if expected_pad_count == 28:
        def_path = ROOT / "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def"
    elif expected_pad_count == 56:
        def_path = ROOT / "libs.ref/ICPS2023_5_fd_base_2x2/def/ICPS2023_5_fd_base_2x2.def"
    try:
        assignments = pad_mapping_contract.validate_pad_mapping(mapping, def_path=def_path)
    except pad_mapping_contract.PadMappingError as error:
        return {
            "status": "INVALID",
            "assignments_by_physical_pad": {},
            "unassigned_pad_ids": actual_pad_ids,
            "function_inferred_from_labels": False,
            "error": str(error),
        }
    assert assignments is not None
    if actual_pad_ids is None:
        return {
            "status": "VALID_BUT_GEOMETRIC_IDENTITY_UNRESOLVED",
            "assignments_by_physical_pad": assignments,
            "unassigned_pad_ids": None,
            "function_inferred_from_labels": False,
            "message": "Explicit assignments are retained, but this GDS did not satisfy the frame geometry contract needed to bind PAD_XX IDs to ML1 polygons.",
        }
    unknown_ids = sorted(set(assignments) - set(actual_pad_ids))
    if unknown_ids:
        return {
            "status": "INVALID",
            "assignments_by_physical_pad": assignments,
            "unassigned_pad_ids": sorted(set(actual_pad_ids) - set(assignments)),
            "function_inferred_from_labels": False,
            "error": "mapping references pad IDs absent from the measured frame: " + ", ".join(unknown_ids),
        }
    unassigned = sorted(set(actual_pad_ids) - set(assignments))
    return {
        "status": "PARTIAL" if unassigned else "COMPLETE",
        "assignments_by_physical_pad": assignments,
        "unassigned_pad_ids": unassigned,
        "function_inferred_from_labels": False,
        "unmapped_function_resolution": "UNRESOLVED" if unassigned else "none",
    }


def _esd_evidence(esd_devices: Any) -> dict[str, Any]:
    if esd_devices is None:
        return {
            "status": "NOT_DECLARED",
            "declared_devices": None,
            "electrically_qualified": False,
            "reason": "No explicit ESD-device declarations were supplied; ESD topology and qualification remain unavailable.",
        }
    try:
        rendered = json.loads(json.dumps(esd_devices, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise IoDfmError(f"esd_devices must be JSON-serializable: {error}") from error
    return {
        "status": "DECLARED_UNQUALIFIED",
        "declared_devices": rendered,
        "electrically_qualified": False,
        "reason": "Declarations do not establish pad-to-rail/rail-to-return topology or HBM/CDM/TLP validation.",
    }


def check_io_dfm(
    gds_path: str | Path,
    *,
    fab_gds_path: str | Path | None = None,
    pad_mapping: Any = None,
    process_profile: str | None = None,
    layer_mode: str = "design",
    expected_pad_count: int | None = 28,
    esd_devices: Any = None,
    engineering_reservations_um: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check real PAD/ML1/upper-layer geometry, keeping qualification blocked.

    PAD geometry is checked only for exact containment in actual ML1 geometry.
    This is a structural Boolean check, not a foundry-approved pad-opening
    enclosure rule.  If supplied, ``fab_gds_path`` is the output of the actual
    repository fab-prep deck and is compared against its design-layer source.
    """
    if layer_mode not in ("design", "fab"):
        raise IoDfmError("layer_mode must be 'design' or 'fab'")
    if fab_gds_path is not None and layer_mode != "design":
        raise IoDfmError("fab_gds_path comparison requires design-layer input GDS")
    if (expected_pad_count is not None and
            (isinstance(expected_pad_count, bool) or not isinstance(expected_pad_count, int) or
             expected_pad_count < 1)):
        raise IoDfmError("expected_pad_count must be a positive integer or null")
    profile_data = _read_profile_data()
    profile_name, profile = _resolve_profile(process_profile, profile_data)
    layout, top, dbu_um, source_path = _read_layout(gds_path)
    upper = _upper_layer_check(layout, top, layer_mode, profile_data, profile)
    upper["process_profile"] = profile_name
    pad_openings = _pad_opening_check(layout, top, layer_mode, dbu_um)
    frame_info, actual_pad_ids = _frame_evidence(source_path, expected_pad_count, layer_mode)
    mapping_info = _mapping_evidence(pad_mapping, actual_pad_ids, expected_pad_count)
    esd_info = _esd_evidence(esd_devices)
    fabprep = (None if fab_gds_path is None else
               _fabprep_comparison(layout, top, dbu_um, source_path, fab_gds_path))

    structural_failures = []
    structural_incomplete = []
    structural_failures.extend(upper["violations"])
    structural_failures.extend(pad_openings.get("violations", []))
    if mapping_info["status"] == "INVALID":
        structural_failures.append({"code": "invalid_explicit_pad_mapping",
                                    "message": mapping_info.get("error", "invalid pad mapping")})
    if fabprep is not None and fabprep["status"] == "FAIL":
        structural_failures.append({"code": "design_fabprep_geometry_mismatch",
                                    "message": "design PAD/ML1 geometry differs from the supplied fab-prep GDS"})
    if pad_openings["status"] == "MISSING":
        structural_incomplete.append(pad_openings["finding"])
    if frame_info["status"] == "UNRESOLVED":
        structural_incomplete.append({"code": "physical_pad_identity_unresolved",
                                      "message": frame_info["reason"]})
    # Net/function assignment is optional for geometry-only inspection.  Keep
    # unresolved mapping state in the report without changing structural status.
    if structural_failures:
        structural_status = "FAIL"
    elif structural_incomplete:
        structural_status = "INCOMPLETE"
    else:
        structural_status = "PASS"

    reservations = dict(engineering_reservations_um or {
        "pad": 20,
        "frame": 10,
        "edge": 10,
    })
    return {
        "schema_version": 1,
        "tool": "io_dfm",
        "source_gds": str(source_path),
        "source_dbu_um": dbu_um,
        "layer_mode": layer_mode,
        "process_profile": profile_name,
        "structural_status": structural_status,
        "structural_pass": structural_status == "PASS",
        "structural_findings": {
            "failures": structural_failures,
            "incomplete": structural_incomplete,
        },
        "checks": {
            "upper_layer_exclusion": upper,
            "pad_openings": pad_openings,
            "engineering_frame": frame_info,
            "explicit_pad_mapping": mapping_info,
            "explicit_esd_devices": esd_info,
            "design_to_fabprep": fabprep or {
                "status": "NOT_CHECKED",
                "reason": "No actual fab-prep GDS was supplied; pass --fab-gds to compare design PAD/ML1 against fab PAD/ML1.",
            },
        },
        "experimental_defaults": {
            "frame_ml1_pad_size_um": frame.EXPECTED_PAD_SIZE_UM,
            "frame_expected_pad_count": expected_pad_count,
            "frame_reservations_um": reservations,
            "qualification": "engineering measurements only; not official pad, edge-clearance, bond/probe, or ESD requirements",
        },
        "manufacturing_acceptance": {
            "status": "BLOCKED",
            "accepted": False,
            "blocking_unknowns": _UNKNOWN_MANUFACTURING_REQUIREMENTS,
            "structural_pass_is_not_manufacturing_acceptance": True,
        },
        "source_evidence": [
            {
                "source": "ICPS2023_5 PDK Users' Manual v1.05",
                "url": "https://www.dropbox.com/scl/fi/84uzdzux0urhsd05mfxmb/ICPS2023_5-PDK-Users-Manual-v1.05.paper?rlkey=04jeibh2hrzlrlx4x9h5ghp4s&dl=0",
                "section": "Semiconductor manufacturing process used by ICPS2023_5",
                "fact": "The manual says VIA manufacturing is unstable and ML2 wiring is not available, and advises use of ML1 only (version/date context: 1 November 2023).",
            },
            {
                "source": "ICPS2023_5 PDK Users' Manual v1.05",
                "url": "https://www.dropbox.com/scl/fi/84uzdzux0urhsd05mfxmb/ICPS2023_5-PDK-Users-Manual-v1.05.paper?rlkey=04jeibh2hrzlrlx4x9h5ghp4s&dl=0",
                "section": "ICPS2023_5 Basic Cells: Pads (PAD100 and PAD70)",
                "fact": "The manual's PAD100/PAD70 entry is struck through; it does not provide pad-opening, bond/probe-current, or ESD qualification limits.",
            },
            {
                "source": "libs.tech/klayout/doc/layers.md and libs.tech/klayout/drc/icps2023_5_fab_prep.drc",
                "fact": "The repository defines design PAD as 50/0 and fab PAD as 10/0, with design ML1 8/0 mapped to fab ML1 7/0; FRAME14/0 is an outline layer, not a measured physical seal-ring width.",
            },
            {
                "source": "SASIMI 2022 Proceedings, C-4, 'An NDA-free oriented Open PDK technology and EDA for small volume LSI developments', pp. 190–195",
                "url": "https://sasimi.jp/new/sasimi2022/files/archive/pdf/p190_C-4.pdf",
                "fact": "The paper describes open-PDK/Minimal Fab context but does not specify pad-opening or ESD qualification limits used by this checker.",
            },
        ],
    }


def _read_config(path: Path) -> tuple[dict[str, Any], Path]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IoDfmError(f"cannot read config {path}: {error}") from error
    if not isinstance(config, dict) or not isinstance(config.get("frame"), dict):
        raise IoDfmError("config must contain a frame object")
    frame_config = config["frame"]
    raw_gds = frame_config.get("gds")
    if not isinstance(raw_gds, str) or not raw_gds:
        raise IoDfmError("config frame.gds must be a nonempty path")
    gds_path = Path(raw_gds).expanduser()
    if not gds_path.is_absolute():
        gds_path = ROOT / gds_path
    return config, gds_path


def _read_json_value(value: str, field: str) -> Any:
    path = Path(value).expanduser()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise IoDfmError(f"cannot read {field} JSON {path}: {error}") from error
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise IoDfmError(f"{field} must be JSON text or a JSON file path") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="frame/profile JSON config")
    parser.add_argument("--gds", help="design or fab GDS (overrides config frame.gds)")
    parser.add_argument("--fab-gds", help="actual fab-prep output GDS for design-to-fab comparison")
    parser.add_argument("--layer-mode", choices=("design", "fab"), default=None,
                        help="input numbering; defaults to config frame.layer_mode")
    parser.add_argument("--process-profile", help="shared ICPS2023_5 process profile")
    parser.add_argument("--expected-pad-count", type=int, help="engineering frame measurement contract")
    parser.add_argument("--pad-mapping", help="explicit PAD_XX-to-function/net JSON object or file")
    parser.add_argument("--esd-devices", help="explicit ESD declaration JSON value or file")
    parser.add_argument("--mode", choices=("structural", "manufacturing"), default="structural")
    parser.add_argument("--out", default="-", help="JSON report path; '-' writes to stdout")
    args = parser.parse_args(argv)
    try:
        config_path = Path(args.config).expanduser().resolve()
        config, configured_gds = _read_config(config_path)
        frame_config = config["frame"]
        keepouts = frame_config.get("keepout_um", {})
        if not isinstance(keepouts, dict):
            raise IoDfmError("config frame.keepout_um must be an object")
        mapping = (_read_json_value(args.pad_mapping, "pad mapping") if args.pad_mapping is not None
                   else frame_config.get("power_interface"))
        if mapping == {}:
            mapping = None
        esd_devices = (_read_json_value(args.esd_devices, "ESD devices")
                       if args.esd_devices is not None else None)
        count = (args.expected_pad_count if args.expected_pad_count is not None
                 else frame_config.get("expected_pad_count", frame.EXPECTED_PAD_COUNT))
        profile = (args.process_profile or os.environ.get("ICPS2023_5_PROCESS_PROFILE") or
                   config.get("process_profile"))
        layer_mode = args.layer_mode if args.layer_mode is not None else frame_config.get("layer_mode")
        if layer_mode not in ("design", "fab"):
            raise IoDfmError(
                "input numbering is not declared: pass --layer-mode or set "
                "frame.layer_mode in the config (got %r)" % (layer_mode,))
        report = check_io_dfm(
            args.gds or configured_gds,
            fab_gds_path=args.fab_gds,
            pad_mapping=mapping,
            process_profile=profile,
            layer_mode=layer_mode,
            expected_pad_count=count,
            esd_devices=esd_devices,
            engineering_reservations_um={
                "pad": keepouts.get("pad", 20),
                "frame": keepouts.get("frame", 10),
                "edge": keepouts.get("edge", 10),
            },
        )
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.out == "-":
            sys.stdout.write(rendered)
        else:
            Path(args.out).expanduser().write_text(rendered, encoding="utf-8")
    except (IoDfmError, OSError) as error:
        parser.error(str(error))
    if args.mode == "manufacturing":
        return 3
    return 0 if report["structural_pass"] else 1


if __name__ == "__main__":  # pragma: no cover - command-line entry point
    raise SystemExit(main())
