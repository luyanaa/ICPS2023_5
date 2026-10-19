#!/usr/bin/env python3
"""Measure geometric frame reservations from a frame GDS.

The canonical input is the 1x1 frame library
(libs.ref/ICPS2023_5_fd_base, 1600 x 1600 um, 28 pads).  A config may select
any frame library that follows the same two-contour FRAME14/0 drawing and
60 x 60 um ML1 pad convention, including the derived 2x2 frame library
(libs.ref/ICPS2023_5_fd_base_2x2, 3200 x 3200 um, 56 pads) via
``expected_pad_count``.

This is an engineering geometry estimate, not a manufacturing or floorplan
qualification.  FRAME14/0's two nested rectangles are contour marks: the inner
rectangle bounds the central design region; the outer-minus-inner area is not
interpreted as a filled, physical seal-ring polygon.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.json")
FRAME_LAYER = (14, 0)
ML1_LAYER = (8, 0)
EXPECTED_PAD_COUNT = 28
EXPECTED_PAD_SIZE_UM = 60.0


class FrameError(ValueError):
    """Malformed frame geometry or invalid frame measurement input."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise FrameError(f"{field} must be a finite number")
    return number


def _ticks(value: Any, dbu_um: float, field: str) -> int:
    number = _finite_number(value, field)
    try:
        ticks = round(number / dbu_um)
    except (OverflowError, ZeroDivisionError) as error:
        raise FrameError(f"{field} cannot be represented at source DBU {dbu_um:g} um") from error
    if abs(ticks * dbu_um - number) > max(1e-9, abs(number) * 1e-12):
        raise FrameError(f"{field} is not representable at source DBU {dbu_um:g} um")
    return int(ticks)


def _keepout_ticks(value: Any, dbu_um: float, field: str) -> tuple[float, int]:
    number = _finite_number(value, field)
    if number < 0:
        raise FrameError(f"{field} must be nonnegative")
    return number, _ticks(number, dbu_um, field)


def _box_from_um(values: Sequence[Any], dbu_um: float, field: str) -> Any:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or len(values) != 4:
        raise FrameError(f"{field} must be [left, bottom, right, top]")
    coordinates = [_ticks(value, dbu_um, f"{field}[{index}]")
                   for index, value in enumerate(values)]
    left, bottom, right, top = coordinates
    if left >= right or bottom >= top:
        raise FrameError(f"{field} must have positive width and height")
    try:
        return db.Box(left, bottom, right, top)
    except Exception as error:
        raise FrameError(f"{field} is outside KLayout's supported coordinate range") from error


def _box_coordinates(box: Any) -> tuple[int, int, int, int]:
    return (box.left, box.bottom, box.right, box.top)


def _um(value: int, dbu_um: float) -> float:
    # GDS stores DBU as a binary real; remove serialization residue, not DBU geometry.
    result = float(f"{value * dbu_um:.12g}")
    return 0.0 if result == 0 else result


def _box_um(box: Any, dbu_um: float) -> list[float]:
    return [_um(value, dbu_um) for value in _box_coordinates(box)]


def _box_region(box: Any) -> Any:
    return db.Region(box)


def _inset_box(box: Any, distance: int) -> Any | None:
    if distance == 0:
        return box
    left, bottom, right, top = _box_coordinates(box)
    if left + distance >= right - distance or bottom + distance >= top - distance:
        return None
    return db.Box(left + distance, bottom + distance, right - distance, top - distance)


def _polygon_payload(polygon: Any, dbu_um: float) -> dict[str, Any]:
    hull = [polygon.point_hull(index) for index in range(polygon.num_points_hull())]
    holes = []
    for hole_index in range(polygon.holes()):
        holes.append([polygon.point_hole(hole_index, point_index)
                      for point_index in range(polygon.num_points_hole(hole_index))])

    def points_um(points: Sequence[Any]) -> list[list[float]]:
        return [[_um(point.x, dbu_um), _um(point.y, dbu_um)] for point in points]

    box = polygon.bbox()
    return {
        "bbox_um": _box_um(box, dbu_um),
        "area_um2": _um(abs(polygon.area()), dbu_um * dbu_um),
        "outer_ring_um": points_um(hull),
        "holes_um": [points_um(points) for points in holes],
    }


def _region_payload(region: Any, dbu_um: float) -> dict[str, Any]:
    merged = region.merged()
    polygons = [_polygon_payload(polygon, dbu_um) for polygon in merged.each()]
    polygons.sort(key=lambda item: (item["bbox_um"], item["outer_ring_um"]))
    bbox = None if merged.is_empty() else _box_um(merged.bbox(), dbu_um)
    return {
        "area_um2": _um(merged.area(), dbu_um * dbu_um),
        "bbox_um": bbox,
        "polygon_count": len(polygons),
        "polygons": polygons,
    }


def _area_um2(region: Any, dbu_um: float) -> float:
    return _um(region.merged().area(), dbu_um * dbu_um)


def _shape_region(shape: Any) -> Any:
    region = db.Region()
    if shape.is_box():
        region.insert(shape.box)
    elif shape.is_polygon():
        region.insert(shape.polygon)
    elif shape.is_path():
        region.insert(shape.path.polygon())
    elif shape.is_text():
        # Text is not conductor geometry; pin labels never define pad function.
        return region
    else:
        raise FrameError("unsupported non-area shape on frame or ML1 geometry layer")
    return region


def _power_assignments(power_interface: Any, pad_ids: set[str]) -> dict[str, str]:
    if power_interface is None:
        return {}
    if not isinstance(power_interface, Mapping):
        raise FrameError("power_interface must map explicit PAD_XX identifiers to net names")
    assignments: dict[str, str] = {}
    for pad_id, net_name in power_interface.items():
        if not isinstance(pad_id, str) or pad_id not in pad_ids:
            raise FrameError(f"power_interface references unknown pad {pad_id!r}")
        if (not isinstance(net_name, str) or not net_name or
                net_name != net_name.strip()):
            raise FrameError(f"power_interface[{pad_id!r}] must be a nonempty net name")
        assignments[pad_id] = net_name
    return dict(sorted(assignments.items()))


def measure_frame(gds_path: str | Path, *, die_bbox: Sequence[float] | None = None,
                  pad_keepout_um: float = 20, frame_keepout_um: float = 10,
                  edge_keepout_um: float = 10,
                  expected_pad_count: int | None = None,
                  pro_capacity_multiplier: float | None = 4.0,
                  power_interface: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Measure die/frame/pad geometry and reserved core area from a real GDS.

    ``die_bbox`` is an optional [left, bottom, right, top] die override in um;
    otherwise the outer FRAME14/0 contour is used.  Keepouts are nonnegative
    distances in um.  ``expected_pad_count`` defaults to the canonical 28-pad
    1x1 frame; the derived 3200 x 3200 um 2x2 frame library supplies 56.
    ``pro_capacity_multiplier`` emits the user-supplied nominal scaling
    scenarios of that factor (4 for the historical unverified 4x readings);
    ``None`` omits them, as for the measured 2x2 frame.  ``power_interface`` is
    an explicit mapping from canonical ``PAD_00``.. IDs to net names; GDS text
    labels are never consulted.

    The GDS must contain one top cell, exactly two nested rectangular FRAME14/0
    contours, and exactly ``expected_pad_count`` distinct merged 60 x 60 um ML1
    pad rectangles.
    The available polygon is the die interior clipped to the inner frame contour,
    less the frame/edge reservations and the union of expanded pad rectangles.
    Areas therefore come from Boolean geometry (overlap counted once), not an
    occupancy estimate.  The returned capacity is an engineering reservation
    only and is not a qualified manufacturing core.
    """
    if db is None:
        raise FrameError("KLayout Python bindings are required; use the root Nix devShell")
    required_pads = EXPECTED_PAD_COUNT if expected_pad_count is None else expected_pad_count
    if isinstance(required_pads, bool) or not isinstance(required_pads, int) or required_pads < 1:
        raise FrameError("expected_pad_count must be a positive integer")
    capacity_factor = (None if pro_capacity_multiplier is None else
                       _finite_number(pro_capacity_multiplier, "pro_capacity_multiplier"))
    if capacity_factor is not None and capacity_factor <= 0:
        raise FrameError("pro_capacity_multiplier must be positive")
    source = Path(gds_path).expanduser().resolve()
    if not source.is_file():
        raise FrameError(f"GDS file does not exist: {source}")

    layout = db.Layout()
    try:
        layout.read(str(source))
    except Exception as error:
        raise FrameError(f"cannot read GDS {source}: {error}") from error
    dbu_um = float(layout.dbu)
    if not math.isfinite(dbu_um) or dbu_um <= 0:
        raise FrameError("GDS database unit must be finite and positive")
    top_cells = list(layout.top_cells())
    if len(top_cells) != 1:
        raise FrameError(f"GDS must have exactly one top cell; found {len(top_cells)}")
    top = top_cells[0]

    # Flatten only the in-memory read copy so contours and physical plates can
    # be reconstructed from the complete hierarchy, including array members.
    top.flatten(-1)
    frame_index = layout.find_layer(db.LayerInfo(*FRAME_LAYER))
    ml1_index = layout.find_layer(db.LayerInfo(*ML1_LAYER))
    if frame_index is None:
        raise FrameError("GDS is missing FRAME14/0 geometry")
    if ml1_index is None:
        raise FrameError("GDS is missing ML1 8/0 pad geometry")

    frame_boxes = []
    for shape in top.shapes(frame_index).each():
        if not shape.is_box():
            raise FrameError("FRAME14/0 contours must be rectangular boxes")
        frame_boxes.append(shape.box)
    if len(frame_boxes) != 2:
        raise FrameError(f"FRAME14/0 must contain exactly two nested boxes; found {len(frame_boxes)}")
    frame_boxes.sort(key=lambda box: (box.width() * box.height(), box.width(), box.height()),
                     reverse=True)
    frame_outer, frame_inner = frame_boxes
    outer_left, outer_bottom, outer_right, outer_top = _box_coordinates(frame_outer)
    inner_left, inner_bottom, inner_right, inner_top = _box_coordinates(frame_inner)
    if not (outer_left <= inner_left < inner_right <= outer_right and
            outer_bottom <= inner_bottom < inner_top <= outer_top and
            (outer_left, outer_bottom, outer_right, outer_top) !=
            (inner_left, inner_bottom, inner_right, inner_top)):
        raise FrameError("FRAME14/0 boxes must be strictly nested")

    die_box = frame_outer if die_bbox is None else _box_from_um(die_bbox, dbu_um, "die_bbox")
    if not (outer_left <= die_box.left and die_box.right <= outer_right and
            outer_bottom <= die_box.bottom and die_box.top <= outer_top):
        raise FrameError("die_bbox must lie within the measured outer frame contour")

    pad_keepout, pad_delta = _keepout_ticks(pad_keepout_um, dbu_um, "pad_keepout_um")
    frame_keepout, frame_delta = _keepout_ticks(frame_keepout_um, dbu_um, "frame_keepout_um")
    edge_keepout, edge_delta = _keepout_ticks(edge_keepout_um, dbu_um, "edge_keepout_um")

    expected_pad_ticks = _ticks(EXPECTED_PAD_SIZE_UM, dbu_um, "canonical pad size")
    ml1_region = db.Region()
    for shape in top.shapes(ml1_index).each():
        ml1_region += _shape_region(shape)
    ml1_region = ml1_region.merged()
    pad_polygons = list(ml1_region.each())
    if len(pad_polygons) != required_pads:
        raise FrameError(f"merged ML1 geometry must contain exactly {required_pads} pads; "
                         f"found {len(pad_polygons)}")

    pad_boxes = []
    for polygon in pad_polygons:
        box = polygon.bbox()
        if (polygon.holes() != 0 or polygon.area() != box.width() * box.height() or
                box.width() != expected_pad_ticks or box.height() != expected_pad_ticks):
            raise FrameError("each merged ML1 pad must be a solid 60 x 60 um rectangle")
        pad_boxes.append(box)
    pad_boxes.sort(key=lambda box: (-(box.bottom + box.top), box.left + box.right,
                                    box.bottom, box.left))
    pads = []
    for index, box in enumerate(pad_boxes):
        pad_id = f"PAD_{index:02d}"
        pads.append({"pad_id": pad_id, "bbox_um": _box_um(box, dbu_um),
                     "area_um2": _um(box.width() * box.height(), dbu_um * dbu_um)})
    pad_ids = {pad["pad_id"] for pad in pads}
    assignments = _power_assignments(power_interface, pad_ids)

    die_region = _box_region(die_box)
    frame_inner_region = _box_region(frame_inner)
    die_inset = _inset_box(die_box, edge_delta)
    edge_keepout_region = die_region if die_inset is None else die_region - _box_region(die_inset)
    frame_inset = _inset_box(frame_inner, frame_delta)
    frame_keepout_region = (frame_inner_region if frame_inset is None else
                            frame_inner_region - _box_region(frame_inset)) & die_region

    raw_pad_corridors = db.Region()
    for box in pad_boxes:
        left = max(die_box.left, box.left - pad_delta)
        bottom = max(die_box.bottom, box.bottom - pad_delta)
        right = min(die_box.right, box.right + pad_delta)
        top_edge = min(die_box.top, box.top + pad_delta)
        if left < right and bottom < top_edge:
            raw_pad_corridors.insert(db.Box(left, bottom, right, top_edge))
    pad_keepout_region = raw_pad_corridors.merged() & die_region

    available = db.Region() if die_inset is None else _box_region(die_inset)
    available &= frame_inner_region
    available -= frame_keepout_region
    available -= pad_keepout_region
    available = available.merged()

    die_area_um2 = _um(die_box.width() * die_box.height(), dbu_um * dbu_um)
    core_available_area_um2 = _area_um2(available, dbu_um)
    fixed_overhead_area_um2 = die_area_um2 - core_available_area_um2
    # Suppress harmless floating point residue while retaining DBU-derived area.
    if abs(fixed_overhead_area_um2) < 1e-9:
        fixed_overhead_area_um2 = 0.0
    pad_bbox = [min(pad["bbox_um"][0] for pad in pads),
                min(pad["bbox_um"][1] for pad in pads),
                max(pad["bbox_um"][2] for pad in pads),
                max(pad["bbox_um"][3] for pad in pads)]

    frame_area_um2 = _um(frame_outer.width() * frame_outer.height(), dbu_um * dbu_um)
    frame_width_um = _um(frame_outer.width(), dbu_um)
    frame_height_um = _um(frame_outer.height(), dbu_um)
    if capacity_factor is None:
        # The measured 2x2 frame supersedes the unverified scaling readings;
        # nominal scenarios would describe a die larger than the measured one.
        frame_scenarios = {}
    else:
        scaled_area = capacity_factor * frame_area_um2
        scaled_width = capacity_factor * frame_width_um
        scaled_height = capacity_factor * frame_height_um
        frame_scenarios = {
            f"area_multiplier_{capacity_factor:g}": {
                "user_supplied_unverified": True,
                "nominal_area_um2": scaled_area,
                "nominal_square_side_um": math.sqrt(scaled_area),
                "core_available_area_um2": None,
                "core_capacity_claimed": False,
                "interpretation": f"{capacity_factor:g}x the outer-frame area; not a multiplier "
                                  "on the measured core area",
            },
            f"side_multiplier_{capacity_factor:g}": {
                "user_supplied_unverified": True,
                "nominal_bbox_um": [-scaled_width / 2, -scaled_height / 2,
                                    scaled_width / 2, scaled_height / 2],
                "nominal_area_um2": scaled_width * scaled_height,
                "nominal_square_side_um": scaled_width if scaled_width == scaled_height else None,
                "core_available_area_um2": None,
                "core_capacity_claimed": False,
                "interpretation": f"{capacity_factor:g}x each outer-frame side; not a multiplier "
                                  "on the measured core area",
            },
        }

    keepout_payloads = {
        "pad": {"distance_um": pad_keepout,
                "meaning": "whole 60 x 60 um pad plus its square routing reservation, clipped to die",
                "region": _region_payload(pad_keepout_region, dbu_um)},
        "frame": {"distance_um": frame_keepout,
                  "meaning": "interior band of this width at the inner FRAME14/0 contour",
                  "region": _region_payload(frame_keepout_region, dbu_um)},
        "edge": {"distance_um": edge_keepout,
                 "meaning": "band at the selected die boundary",
                 "region": _region_payload(edge_keepout_region, dbu_um)},
    }
    unassigned_pads = sorted(pad_ids - set(assignments))
    try:
        library_dir = source.relative_to(ROOT).parent.parent
        frame_readme_path = f"{library_dir}/doc/README.md"
        pad_source_path = f"{library_dir}/doc/pad_map.csv"
    except ValueError:  # GDS outside the repository tree
        frame_readme_path = "frame library README (external GDS)"
        pad_source_path = "frame pad map (external GDS)"
    return {
        "schema_version": 1,
        "units": "um",
        "source_gds": str(source),
        "source_sha256": _sha256(source),
        "dbu_um": dbu_um,
        "frame": {
            "layer": [FRAME_LAYER[0], FRAME_LAYER[1]],
            "outer_bbox_um": _box_um(frame_outer, dbu_um),
            "inner_bbox_um": _box_um(frame_inner, dbu_um),
            "contours_bbox_um": [_box_um(frame_outer, dbu_um), _box_um(frame_inner, dbu_um)],
            "contour_interpretation": (
                "FRAME14/0 contains two nested rectangular contour marks, not a filled solid frame ring; "
                "the inner contour bounds the central design region. The source frame README says the "
                "frame is a drawing, not a seal ring with documented widths."),
        },
        "die_outer_bbox_um": _box_um(die_box, dbu_um),
        "die_bbox_source": "FRAME14/0 outer contour" if die_bbox is None else "explicit die_bbox override",
        "die_area_um2": die_area_um2,
        "pads": {
            "layer": [ML1_LAYER[0], ML1_LAYER[1]],
            "count": len(pads),
            "merged_rectangle_count": len(pads),
            "bbox_um": pad_bbox,
            "rectangles": pads,
            "identity_assignment": (
                f"PAD_00..PAD_{len(pads) - 1:02d} assigned by center y descending then x ascending, "
                "matching the frame README's DEF numbering; source pin labels are not read or used "
                "as signal functions."),
        },
        "keepout_regions": keepout_payloads,
        "available_region": _region_payload(available, dbu_um),
        "core_available_area_um2": core_available_area_um2,
        "fixed_overhead_area_um2": fixed_overhead_area_um2,
        "fixed_overhead_definition": (
            "selected die rectangle area minus Boolean-unioned available region; frame, edge, pad, and "
            "measurement reservations are therefore not double counted."),
        "power_interface": {
            "assignments_by_pad": assignments,
            "unassigned_pads": unassigned_pads,
            "signal_or_power_inferred_from_gds_labels": False,
        },
        "pro_capacity_scenarios": frame_scenarios,
        "provenance": {
            "frame_readme": frame_readme_path,
            "pad_source": f"{pad_source_path} and recursive ML1 geometry",
            "keepout_source": "explicit measurement arguments; no manufacturing/package clearance qualification",
            "area_method": "KLayout integer-DBU Boolean regions; merged connected polygons and holes",
        },
        "manufacturing_limits": {
            "qualified_manufacturing_core": False,
            "process_qualification_claimed": False,
            "signoff_claimed": False,
            "frame": "drawing only; no documented seal-ring width or seal-ring qualification",
            "technology": {
                "ML1": "8/0, width 6 um, spacing 4 um",
                "TiN": "5/0, width 6 um, spacing 6 um, sheet resistance 20 ohm/square",
                "CNT": "7/0, 5 x 5 um; ML1/TiN enclosure at least 2.5 um outside active 3/0",
                "source_drain_contact_enclosure": "4 um",
                "unsupported": "no Poly/VIA1/ML2 routing; direct TiN/diffusion CNT is unqualified",
            },
            "capacity": "geometric estimate only; no floorplan, route, PDN, electrical, or foundry signoff claim",
            "power": "pad-to-net assignments must be explicit; no power/current capacity is inferred",
        },
    }


def find_rectangular_core_fit(measurement: Mapping[str, Any], width_um: float,
                              height_um: float) -> dict[str, Any]:
    """Find an exact axis-aligned core rectangle inside measured available geometry.

    ``measurement`` is the report returned by :func:`measure_frame`.  Dimensions
    and returned bboxes are in micrometres; all containment checks use the
    source GDS database unit and the complete available polygon, including
    holes.  ``fits`` is false when no placement is found.  This is geometric
    evidence only, not a qualified placement site or frame integration claim.
    """
    if db is None:
        raise FrameError("KLayout Python bindings are required; use the root Nix devShell")
    if not isinstance(measurement, Mapping) or measurement.get("units") != "um":
        raise FrameError("measurement must be a measure_frame report with units 'um'")
    dbu_um = _finite_number(measurement.get("dbu_um"), "measurement dbu_um")
    if dbu_um <= 0:
        raise FrameError("measurement dbu_um must be positive")
    width_dbu = _ticks(width_um, dbu_um, "core rectangle width_um")
    height_dbu = _ticks(height_um, dbu_um, "core rectangle height_um")
    if width_dbu <= 0 or height_dbu <= 0:
        raise FrameError("core rectangle dimensions must be positive")
    available_payload = measurement.get("available_region")
    if not isinstance(available_payload, Mapping):
        raise FrameError("measurement is missing the exact available_region geometry")
    polygons_payload = available_payload.get("polygons")
    if not isinstance(polygons_payload, Sequence) or isinstance(
            polygons_payload, (str, bytes)):
        raise FrameError("measurement available_region polygons must be a sequence")

    available = db.Region()
    boundary_x: set[int] = set()
    boundary_y: set[int] = set()

    def points_from_ring(value: Any, field: str) -> list[Any]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 4:
            raise FrameError(f"{field} must contain at least four points")
        points = []
        for index, coordinate in enumerate(value):
            if (not isinstance(coordinate, Sequence) or
                    isinstance(coordinate, (str, bytes)) or len(coordinate) != 2):
                raise FrameError(f"{field}[{index}] must be an [x, y] point")
            x = _ticks(coordinate[0], dbu_um, f"{field}[{index}].x")
            y = _ticks(coordinate[1], dbu_um, f"{field}[{index}].y")
            points.append(db.Point(x, y))
            boundary_x.add(x)
            boundary_y.add(y)
        for first, second in zip(points, points[1:] + points[:1]):
            if first.x != second.x and first.y != second.y:
                raise FrameError(
                    "exact rectangular-core lookup requires orthogonal available-region boundaries")
        return points

    for polygon_index, polygon_payload in enumerate(polygons_payload):
        if not isinstance(polygon_payload, Mapping):
            raise FrameError(f"available_region.polygons[{polygon_index}] must be an object")
        hull = points_from_ring(
            polygon_payload.get("outer_ring_um"),
            f"available_region.polygons[{polygon_index}].outer_ring_um")
        polygon_region = db.Region(db.Polygon(hull))
        holes = polygon_payload.get("holes_um", [])
        if not isinstance(holes, Sequence) or isinstance(holes, (str, bytes)):
            raise FrameError(f"available_region.polygons[{polygon_index}].holes_um must be a sequence")
        for hole_index, hole in enumerate(holes):
            hole_points = points_from_ring(
                hole, f"available_region.polygons[{polygon_index}].holes_um[{hole_index}]")
            polygon_region -= db.Region(db.Polygon(hole_points))
        available += polygon_region
    available = available.merged()
    if available.is_empty():
        return {
            "fits": False,
            "units": "um",
            "dbu_um": dbu_um,
            "source_gds": measurement.get("source_gds"),
            "source_sha256": measurement.get("source_sha256"),
            "requested_size_um": [float(width_um), float(height_um)],
            "placement_bbox_um": None,
            "candidate_count": 0,
            "containment_method": "exact KLayout DBU Boolean containment in available_region",
            "qualified_site": False,
        }

    available_box = available.bbox()
    result_base = {
        "units": "um",
        "dbu_um": dbu_um,
        "requested_size_um": [float(width_um), float(height_um)],
        "available_bbox_um": _box_um(available_box, dbu_um),
        "available_area_um2": _area_um2(available, dbu_um),
        "containment_method": "exact KLayout DBU Boolean containment in available_region",
        "qualified_site": False,
        "source_gds": measurement.get("source_gds"),
        "source_sha256": measurement.get("source_sha256"),
    }
    if width_dbu > available_box.width() or height_dbu > available_box.height():
        return {
            **result_base,
            "fits": False,
            "placement_bbox_um": None,
            "candidate_count": 0,
        }

    # For orthogonal geometry, any feasible rectangle can be slid left/down
    # until an edge meets a boundary; the resulting origin is a boundary
    # coordinate or that coordinate minus the requested extent.  Enumerating
    # those finite translations is therefore complete, not an area heuristic.
    center_x2 = available_box.left + available_box.right
    center_y2 = available_box.bottom + available_box.top
    x_candidates = {value for boundary in boundary_x
                    for value in (boundary, boundary - width_dbu)
                    if available_box.left <= value <= available_box.right - width_dbu}
    y_candidates = {value for boundary in boundary_y
                    for value in (boundary, boundary - height_dbu)
                    if available_box.bottom <= value <= available_box.top - height_dbu}
    centered = (
        (available_box.left + available_box.right - width_dbu) // 2,
        (available_box.bottom + available_box.top - height_dbu) // 2,
    )
    ordered_x = sorted(x_candidates, key=lambda value: (abs(2 * value + width_dbu - center_x2),
                                                        value))
    ordered_y = sorted(y_candidates, key=lambda value: (abs(2 * value + height_dbu - center_y2),
                                                        value))
    # A centered rectangle is a useful first candidate, but is accepted only
    # after the same exact Boolean test as every other candidate.
    if (available_box.left <= centered[0] <= available_box.right - width_dbu and
            available_box.bottom <= centered[1] <= available_box.top - height_dbu):
        ordered_x = [centered[0], *(value for value in ordered_x if value != centered[0])]
        ordered_y = [centered[1], *(value for value in ordered_y if value != centered[1])]

    candidate_count = 0
    for bottom in ordered_y:
        for left in ordered_x:
            candidate_count += 1
            rectangle = db.Region(db.Box(left, bottom, left + width_dbu, bottom + height_dbu))
            if (rectangle - available).is_empty():
                return {
                    **result_base,
                    "fits": True,
                    "placement_bbox_um": _box_um(
                        db.Box(left, bottom, left + width_dbu, bottom + height_dbu), dbu_um),
                    "candidate_count": candidate_count,
                }
    return {
        **result_base,
        "fits": False,
        "placement_bbox_um": None,
        "candidate_count": candidate_count,
    }


def _config_frame_arguments(config_path: Path) -> tuple[Path, dict[str, Any]]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrameError(f"cannot read config {config_path}: {error}") from error
    if not isinstance(config, dict) or not isinstance(config.get("frame"), dict):
        raise FrameError("config must contain a frame object")
    frame = config["frame"]
    if not isinstance(frame.get("gds"), str) or not frame["gds"]:
        raise FrameError("config frame.gds must be a nonempty path")
    gds_path = Path(frame["gds"]).expanduser()
    if not gds_path.is_absolute():
        gds_path = ROOT / gds_path
    keepouts = frame.get("keepout_um", {})
    if not isinstance(keepouts, dict):
        raise FrameError("config frame.keepout_um must be an object")
    report_args = {
        "die_bbox": frame.get("die_bbox_um"),
        "pad_keepout_um": keepouts.get("pad", 20),
        "frame_keepout_um": keepouts.get("frame", 10),
        "edge_keepout_um": keepouts.get("edge", 10),
        "expected_pad_count": frame.get("expected_pad_count", EXPECTED_PAD_COUNT),
        "pro_capacity_multiplier": frame.get("pro_capacity_multiplier", 4.0),
        "power_interface": frame.get("power_interface", {}),
    }
    return gds_path, report_args


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON config (default: config.json)")
    parser.add_argument("--out", default="-", help="write JSON report here; '-' writes to stdout")
    args = parser.parse_args(argv)
    try:
        gds_path, report_args = _config_frame_arguments(Path(args.config).expanduser().resolve())
        report = measure_frame(gds_path, **report_args)
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.out == "-":
            sys.stdout.write(rendered)
        else:
            Path(args.out).expanduser().write_text(rendered, encoding="utf-8")
    except (FrameError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
