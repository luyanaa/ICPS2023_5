#!/usr/bin/env python3
"""Deterministic flat ML1 core routing for accepted ICPS2023_5 cells.

This is a routing-evidence generator, not a placement-site, I/O, PDN, timing,
DRC/LVS/PEX, or foundry-signoff flow.  Cell pin access is taken from the
accepted GDS/manifest; external wires use only native ML1 geometry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import heapq
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - supplied by the pinned KLayout toolchain
    from klayout import db  # type: ignore

try:
    from . import library, logic, mapping, verify
except ImportError:  # direct invocation from libs.tech/stdcell
    _TOOL_DIR = Path(__file__).resolve().parent
    if str(_TOOL_DIR) not in sys.path:
        sys.path.insert(0, str(_TOOL_DIR))
    import library  # type: ignore
    import logic  # type: ignore
    import mapping  # type: ignore
    import verify  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_LAYER = (100, 0)
TEXT_LAYER = (13, 0)
ML1_LAYER = (8, 0)
NATIVE_LAYERS = {
    (3, 0), (5, 0), (7, 0), ML1_LAYER, TEXT_LAYER, (18, 0), (19, 0),
    BOUNDARY_LAYER,
}
PITCH_UM = 0.5
WIRE_WIDTH_UM = 6.0
ML1_SPACE_UM = 4.0
ROUTE_CLEARANCE_UM = WIRE_WIDTH_UM / 2 + ML1_SPACE_UM
PLACEMENT_GAP_UM = 32.0
MULTIROW_GAP_UM = 40.0  # Conservative engineering channel, not a qualified row pitch.
CORE_MARGIN_UM = 24.0
ROUTE_CHANNEL_UM = 24.0
PORT_SPACING_UM = 16.0
MAX_SEARCH_NODES = 1_000_000
DBU_TOLERANCE_UM = 1e-7
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class CoreRouterError(ValueError):
    """A canonical input or accepted physical view cannot be routed safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _um_to_dbu(value: float, dbu_um: float, field: str) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise CoreRouterError(f"{field} must be finite")
    ticks = round(number / dbu_um)
    if abs(ticks * dbu_um - number) > DBU_TOLERANCE_UM:
        raise CoreRouterError(f"{field} is not representable at source DBU {dbu_um:g} um")
    return int(ticks)


def _ceil_div(value: int, divisor: int) -> int:
    return -((-value) // divisor)


def _ceil_multiple(value: int, quantum: int) -> int:
    return _ceil_div(value, quantum) * quantum


def _point_box(point: Any) -> Any:
    return db.Box(point.x - 1, point.y - 1, point.x + 1, point.y + 1)


def _region_has_point(region: Any, point: Any) -> bool:
    return not region.is_empty() and region.interacting(db.Region(_point_box(point))).count() > 0


def _bbox_um(box: Any, dbu_um: float) -> list[float]:
    return [float(value * dbu_um) for value in
            (box.left, box.bottom, box.right, box.top)]


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_$]+", "_", value).strip("_").upper()
    return (slug or "CORE")[:32]


def _top_cell_name(design_name: str, requested: str | None) -> str:
    if requested is not None:
        if not _NAME_RE.fullmatch(requested) or len(requested) > 32:
            raise CoreRouterError("top_name must be a SPICE/GDS-safe identifier of at most 32 characters")
        return requested
    digest = hashlib.sha256(design_name.encode("utf-8")).hexdigest()[:6].upper()
    stem = _safe_slug(design_name)[:20]
    return f"CORE_{stem}_{digest}"


def _valid_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise CoreRouterError(f"{field} must be a SPICE-safe identifier: {value!r}")
    return value


def _component_ids_at(components: list[Any], point: Any) -> list[int]:
    return [index for index, component in enumerate(components)
            if _region_has_point(component, point)]

def _electrical_components(boxes_by_layer: dict[tuple[int, int], list[Any]],
                           dbu_um: float) -> dict[str, Any]:
    """Merge native metals only through legal 5um CNT with 2.5um enclosure off DIFF."""
    metal_layers = (ML1_LAYER, (5, 0))
    components_by_layer: dict[tuple[int, int], list[Any]] = {}
    for layer in metal_layers:
        region = db.Region()
        for box in boxes_by_layer.get(layer, []):
            region.insert(box)
        components_by_layer[layer] = [db.Region(poly) for poly in region.merged().each()]

    active = db.Region()
    for box in boxes_by_layer.get((3, 0), []):
        active.insert(box)
    parents = {
        (layer, component_id): (layer, component_id)
        for layer in metal_layers
        for component_id in range(len(components_by_layer[layer]))
    }

    def find(node: tuple[tuple[int, int], int]) -> tuple[tuple[int, int], int]:
        parent = parents[node]
        while parent != parents[parent]:
            parents[node] = parents[parents[parent]]
            parent = parents[node]
        parents[node] = parent
        return parent

    def union(first: tuple[tuple[int, int], int],
              second: tuple[tuple[int, int], int]) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    cut_width = _um_to_dbu(5.0, dbu_um, "native CNT width")
    enclosure = _um_to_dbu(2.5, dbu_um, "native CNT enclosure")
    for via_box in boxes_by_layer.get((7, 0), []):
        if via_box.width() != cut_width or via_box.height() != cut_width:
            continue
        via = db.Region(via_box)
        if not (via & active).is_empty():
            continue
        landing = db.Region(via_box.enlarged(enclosure))
        ml1_ids = [index for index, component in enumerate(components_by_layer[ML1_LAYER])
                   if (landing - component).is_empty()]
        tin_ids = [index for index, component in enumerate(components_by_layer[(5, 0)])
                   if (landing - component).is_empty()]
        if not ml1_ids or not tin_ids:
            continue
        contacted = ([(ML1_LAYER, index) for index in ml1_ids] +
                     [((5, 0), index) for index in tin_ids])
        for node in contacted[1:]:
            union(contacted[0], node)

    roots = sorted({find(node) for node in parents})
    root_ids = {root: index for index, root in enumerate(roots)}
    electrical_ids = {
        layer: [root_ids[find((layer, index))]
                for index in range(len(components_by_layer[layer]))]
        for layer in metal_layers
    }
    return {
        "components_by_layer": components_by_layer,
        "electrical_ids_by_layer": electrical_ids,
        "electrical_component_count": len(roots),
        "method": ("same-layer merged ML1/TiN regions plus native 5x5um CNT fully enclosed by "
                   "both metals by 2.5um outside DIFF; labels and MOS channel conduction excluded"),
    }


def _electrical_ids_at_window(window: Any, components: list[Any],
                              component_electrical_ids: list[int]) -> tuple[list[int], list[int]]:
    component_ids = [index for index, component in enumerate(components)
                     if not (db.Region(window) & component).is_empty()]
    return component_ids, sorted({component_electrical_ids[index] for index in component_ids})


def _compressed_path(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(path) < 3:
        return path
    compressed = [path[0]]
    for index in range(1, len(path) - 1):
        previous = compressed[-1]
        current = path[index]
        following = path[index + 1]
        if ((previous[0] == current[0] == following[0]) or
                (previous[1] == current[1] == following[1])):
            continue
        compressed.append(current)
    compressed.append(path[-1])
    return compressed


def _port_endpoints(port_names: list[str], core_width: int, core_height: int,
                    dbu_um: float, pitch_dbu: int,
                    power_points: dict[str, tuple[int, int]],
                    preferred_points_by_name: dict[str, tuple[int, int]] | None = None,
                    directions_by_name: dict[str, str] | None = None) -> dict[str, tuple[int, int]]:
    half_wire = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "ML1 route half-width")
    top_y = core_height - half_wire
    endpoints = {name: point for name, point in power_points.items() if name in port_names}
    signal_ports = [name for name in port_names if name not in endpoints]
    margin = _um_to_dbu(CORE_MARGIN_UM, dbu_um, "port endpoint margin")
    min_y = _ceil_multiple(margin, pitch_dbu)
    max_y = (core_height - margin) // pitch_dbu * pitch_dbu
    left_x = _ceil_multiple(margin, pitch_dbu)
    right_x = (core_width - margin) // pitch_dbu * pitch_dbu
    separation = _um_to_dbu(WIRE_WIDTH_UM + ML1_SPACE_UM, dbu_um,
                            "port endpoint separation")
    for index, name in enumerate(signal_ports):
        preferred = (preferred_points_by_name or {}).get(name)
        direction = (directions_by_name or {}).get(name, "").lower()
        if direction == "input":
            x = half_wire
            target_y = preferred[1] if preferred else min_y + (index + 1) * (
                max_y - min_y) // (len(signal_ports) + 1)
            candidates = [(x, y) for y in range(min_y, max_y + 1, pitch_dbu)]
        elif direction == "output":
            x = core_width - half_wire
            target_y = preferred[1] if preferred else min_y + (index + 1) * (
                max_y - min_y) // (len(signal_ports) + 1)
            candidates = [(x, y) for y in range(min_y, max_y + 1, pitch_dbu)]
        else:
            target_x = preferred[0] if preferred else left_x + (index + 1) * (
                right_x - left_x) // (len(signal_ports) + 1)
            candidates = [(x, top_y) for x in range(left_x, right_x + 1, pitch_dbu)]
            target_y = top_y
        available = [
            (x, y) for x, y in candidates
            if all(abs(x - px) >= separation or abs(y - py) >= separation
                   for px, py in endpoints.values())
        ]
        if not available:
            raise CoreRouterError(
                f"no legal ML1 perimeter endpoint remains for signal port {name}")
        if direction in ("input", "output"):
            point = min(available, key=lambda candidate: (
                abs(candidate[1] - target_y), candidate[1]))
        else:
            point = min(available, key=lambda candidate: (
                abs(candidate[0] - target_x), candidate[0]))
        endpoints[name] = point
    return endpoints


def _placement_rows(placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in placements:
        by_index[int(item.get("row_index", 0))].append(item)
    rows = []
    for index, members in sorted(by_index.items()):
        members.sort(key=lambda item: (item["x_dbu"], item["instance"]))
        rows.append({
            "row_index": index,
            "placements": members,
            "bottom_dbu": min(item["y_dbu"] for item in members),
            "top_dbu": max(item["y_dbu"] + item["height_dbu"] for item in members),
            "left_dbu": min(item["x_dbu"] for item in members),
            "right_dbu": max(item["x_dbu"] + item["width_dbu"] for item in members),
        })
    return rows


def _adjacent_row_clusters(placements: list[dict[str, Any]],
                           target_size: int = 4, maximum_size: int = 8
                           ) -> list[list[dict[str, Any]]]:
    ordered = sorted(placements, key=lambda item: (
        int(item.get("row_index", 0)), item["x_dbu"], item["instance"]))
    index_by_instance = {item["instance"]: index for index, item in enumerate(ordered)}
    row_neighbors: dict[int, set[int]] = {index: set() for index in range(len(ordered))}
    by_row: dict[int, list[int]] = defaultdict(list)
    for index, item in enumerate(ordered):
        by_row[int(item.get("row_index", 0))].append(index)
    for members in by_row.values():
        for first, second in zip(members, members[1:]):
            row_neighbors[first].add(second)
            row_neighbors[second].add(first)
    sorted_rows = sorted(by_row)
    for lower_index, upper_index in zip(sorted_rows, sorted_rows[1:]):
        if upper_index != lower_index + 1:
            continue
        for first in by_row[lower_index]:
            first_right = ordered[first]["x_dbu"] + ordered[first]["width_dbu"]
            for second in by_row[upper_index]:
                second_right = ordered[second]["x_dbu"] + ordered[second]["width_dbu"]
                if max(ordered[first]["x_dbu"], ordered[second]["x_dbu"]) < min(
                        first_right, second_right):
                    row_neighbors[first].add(second)
                    row_neighbors[second].add(first)

    remaining = set(range(len(ordered)))
    groups = []
    while remaining:
        seed = min(remaining)
        queue = [seed]
        group_indices = []
        queued = {seed}
        while queue and len(group_indices) < target_size:
            current = queue.pop(0)
            if current not in remaining:
                continue
            remaining.remove(current)
            group_indices.append(current)
            for neighbor in sorted(row_neighbors[current]):
                if neighbor in remaining and neighbor not in queued:
                    queue.append(neighbor)
                    queued.add(neighbor)
        groups.append([ordered[index] for index in group_indices])
    if len(groups) > 1 and len(groups[-1]) == 1 and len(groups[-2]) < maximum_size:
        final_member = groups[-1][0]
        previous_indices = {
            index_by_instance[item["instance"]] for item in groups[-2]}
        final_index = index_by_instance[final_member["instance"]]
        if any(final_index in row_neighbors[index] for index in previous_indices):
            groups[-2].append(final_member)
            groups.pop()
    return groups


def _power_topology_plan(topology: str, placements: list[dict[str, Any]],
                         core_width: int, core_height: int, dbu_um: float,
                         pitch_dbu: int) -> dict[str, Any]:
    half_wire = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "ML1 route half-width")
    margin = _um_to_dbu(CORE_MARGIN_UM, dbu_um, "core edge rail offset")
    vdd_offset = _um_to_dbu(12.0, dbu_um, "VDD row rail offset")
    vss_offset = _um_to_dbu(11.0, dbu_um, "VSS row rail offset")
    left_x = _ceil_multiple(margin // 2, pitch_dbu)
    right_x = core_width - left_x
    top_y = core_height - half_wire
    boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    power_points: dict[str, tuple[int, int]] = {}
    clusters: list[list[str]] = []
    row_buses = []
    routing_anchors = []

    def add_routing_anchor(net: str, bounds: tuple[int, int, int, int],
                           members: list[str] | None = None) -> None:
        routing_anchors.append({
            "net": net, "box_dbu": list(bounds),
            "trunk_instances": list(members or []),
        })
    rows = _placement_rows(placements)
    if len(rows) > 1:
        required_row_gap = _um_to_dbu(
            MULTIROW_GAP_UM, dbu_um, "minimum multirow placement gap")
        for lower, upper in zip(rows, rows[1:]):
            actual_gap = upper["bottom_dbu"] - lower["top_dbu"]
            if actual_gap < required_row_gap:
                raise CoreRouterError(
                    f"row {upper['row_index']} begins {actual_gap * dbu_um:g}um after "
                    f"row {lower['row_index']}; at least {MULTIROW_GAP_UM:g}um is required "
                    "for opposing native ML1 supply bars")

    def append_row_supply(row: dict[str, Any], members: list[dict[str, Any]],
                          vdd_left: int, vdd_right: int, vss_left: int, vss_right: int,
                          cluster_index: int | None = None) -> None:
        row_top = row["top_dbu"]
        row_bottom = row["bottom_dbu"]
        vdd_bus_y = row_top + vdd_offset
        vss_bus_y = row_bottom - vss_offset
        if len(members) == 1:
            item = members[0]
            vdd_candidates = item.get("pin_anchors", {}).get("VDD", {}).get(
                "candidate_accesses", [])
            vss_candidates = item.get("pin_anchors", {}).get("VSS", {}).get(
                "candidate_accesses", [])
            vdd_access = next((candidate for candidate in vdd_candidates
                               if candidate["window_index"] == 0), None)
            vss_access = next((candidate for candidate in vss_candidates
                               if candidate["window_index"] == 1), None)
            if vdd_access is not None and vss_access is not None:
                vdd_local = vdd_access["point_dbu"]
                vss_local = vss_access["point_dbu"]
                vdd_point = (item["x_dbu"] + vdd_local[0],
                             item["y_dbu"] + vdd_local[1])
                vss_point = (item["x_dbu"] + vss_local[0],
                             item["y_dbu"] + vss_local[1])
                vdd_bus = _wire_box((vdd_left, vdd_bus_y),
                                    (vdd_point[0], vdd_bus_y), half_wire)
                vdd_drop = _wire_box((vdd_point[0], vdd_bus_y),
                                     vdd_point, half_wire)
                vss_bus = _wire_box((vss_point[0], vss_bus_y),
                                    (vss_right, vss_bus_y), half_wire)
                vss_drop = _wire_box(vss_point,
                                     (vss_point[0], vss_bus_y), half_wire)
                boxes.extend((("VDD", vdd_bus), ("VDD", vdd_drop),
                              ("VSS", vss_bus), ("VSS", vss_drop)))
                row_buses.append({
                    "row_index": row["row_index"],
                    "cluster_index": cluster_index,
                    "member_instances": [item["instance"]],
                    "vdd_center_y_dbu": vdd_bus_y,
                    "vss_center_y_dbu": vss_bus_y,
                    "vdd_bbox_dbu": list(vdd_bus),
                    "vss_bbox_dbu": list(vss_bus),
                })
                return

        first_x = min(item["x_dbu"] for item in members)
        last_x = max(item["x_dbu"] + item["width_dbu"] for item in members)
        for item in members:
            cell_left = item["x_dbu"]
            cell_right = cell_left + item["width_dbu"]
            cell_top = item["y_dbu"] + item["height_dbu"]
            cell_vdd_y = cell_top + vdd_offset
            cell_vss_y = item["y_dbu"] - vss_offset
            center_x = _ceil_multiple(
                cell_left + item["width_dbu"] // 2, pitch_dbu)
            boxes.append(("VDD", (cell_left, cell_vdd_y - half_wire,
                                  cell_right, cell_vdd_y + half_wire)))
            if cell_vdd_y != vdd_bus_y:
                boxes.append(("VDD", (center_x - half_wire,
                                      min(cell_vdd_y, vdd_bus_y) - half_wire,
                                      center_x + half_wire,
                                      max(cell_vdd_y, vdd_bus_y) + half_wire)))
            boxes.append(("VSS", (cell_left, cell_vss_y - half_wire,
                                  cell_right, cell_vss_y + half_wire)))
        boxes.append(("VDD", (vdd_left, vdd_bus_y - half_wire,
                              vdd_right, vdd_bus_y + half_wire)))
        boxes.append(("VSS", (vss_left, vss_bus_y - half_wire,
                              vss_right, vss_bus_y + half_wire)))
        row_buses.append({
            "row_index": row["row_index"],
            "cluster_index": cluster_index,
            "member_instances": [item["instance"] for item in members],
            "vdd_center_y_dbu": vdd_bus_y,
            "vss_center_y_dbu": vss_bus_y,
            "vdd_bbox_dbu": [vdd_left, vdd_bus_y - half_wire,
                             vdd_right, vdd_bus_y + half_wire],
            "vss_bbox_dbu": [vss_left, vss_bus_y - half_wire,
                             vss_right, vss_bus_y + half_wire],
        })

    if topology == "edge_rails":
        members = [item["instance"] for item in placements]
        for net, y in (("VDD", top_y), ("VSS", half_wire)):
            rail = (0, y - half_wire, core_width, y + half_wire)
            boxes.append((net, rail))
            add_routing_anchor(net, rail, members)
            power_points[net] = (left_x if net == "VDD" else right_x, y)
        description = (
            "continuous top/bottom ML1 edge rails; each cell supply pin connects "
            "to its designated rail through independently proven ML1 paths")
    elif topology == "segmented_rails":
        if not rows:
            raise CoreRouterError("segmented rails require at least one placed cell")
        # A 40um gap leaves 17um between opposing +12/-11um supply centers
        # (11um edge-to-edge for 6um bars). That spaces the two supply nets,
        # but not a third 6um signal at 4um spacing, which needs 20um between
        # the supply centers; signal escape therefore uses perimeter channels.
        for row in rows:
            append_row_supply(row, row["placements"], left_x, row["right_dbu"],
                              row["left_dbu"], right_x)
        lowest_vdd = min(row["top_dbu"] + vdd_offset for row in rows)
        highest_vss = max(row["bottom_dbu"] - vss_offset for row in rows)
        vdd_spine = (left_x - half_wire, lowest_vdd - half_wire,
                     left_x + half_wire, top_y + half_wire)
        vss_spine = (right_x - half_wire, half_wire - half_wire,
                     right_x + half_wire, highest_vss + half_wire)
        vdd_rail = (0, top_y - half_wire, core_width, top_y + half_wire)
        vss_rail = (0, 0, core_width, 2 * half_wire)
        boxes.extend((("VDD", vdd_spine), ("VSS", vss_spine),
                      ("VDD", vdd_rail), ("VSS", vss_rail)))
        for row_bus in row_buses:
            add_routing_anchor("VDD", tuple(row_bus["vdd_bbox_dbu"]),
                               row_bus["member_instances"])
            add_routing_anchor("VSS", tuple(row_bus["vss_bbox_dbu"]),
                               row_bus["member_instances"])
        add_routing_anchor("VDD", vdd_rail)
        add_routing_anchor("VSS", vss_rail)
        power_points = {"VDD": (left_x, top_y), "VSS": (right_x, half_wire)}
        description = (
            "row-band supply buses join per-cell segments; single-member rows use short "
            "cell-to-band taps, and off-cell side spines connect row buses to the perimeter rails")
    elif topology == "local_clusters":
        for cluster_index, start in enumerate(range(0, len(placements), 4)):
            members = placements[start:start + 4]
            clusters.append([item["instance"] for item in members])
            cluster_first_bus = len(row_buses)
            by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for item in members:
                by_row[int(item.get("row_index", 0))].append(item)
            cluster_vdd_y = []
            cluster_vss_y = []
            for row_index, row_members in sorted(by_row.items()):
                row = next(row for row in rows if row["row_index"] == row_index)
                row_left = min(item["x_dbu"] for item in row_members)
                row_right = max(item["x_dbu"] + item["width_dbu"] for item in row_members)
                if len(row_members) == 1:
                    vdd_left, vdd_right = left_x, row_right
                    vss_left, vss_right = row_left, right_x
                else:
                    vdd_left, vdd_right = row_left, row_right
                    vss_left, vss_right = row_left, row_right
                append_row_supply(row, row_members, vdd_left, vdd_right,
                                  vss_left, vss_right, cluster_index)
                vdd_y = row["top_dbu"] + vdd_offset
                vss_y = row["bottom_dbu"] - vss_offset
                if len(row_members) > 1:
                    boxes.append(("VDD", _wire_box(
                        (left_x, vdd_y), (row_left, vdd_y), half_wire)))
                    boxes.append(("VSS", _wire_box(
                        (row_right, vss_y), (right_x, vss_y), half_wire)))
                cluster_vdd_y.append(vdd_y)
                cluster_vss_y.append(vss_y)
            cluster_instances = [item["instance"] for item in members]
            if cluster_vdd_y and min(cluster_vdd_y) < max(cluster_vdd_y):
                vdd_trunk = _wire_box(
                    (left_x, min(cluster_vdd_y)), (left_x, max(cluster_vdd_y)), half_wire)
                boxes.append(("VDD", vdd_trunk))
                add_routing_anchor("VDD", vdd_trunk, cluster_instances)
            else:
                bus = row_buses[cluster_first_bus]
                add_routing_anchor("VDD", tuple(bus["vdd_bbox_dbu"]),
                                   bus["member_instances"])
            if cluster_vss_y and min(cluster_vss_y) < max(cluster_vss_y):
                vss_trunk = _wire_box(
                    (right_x, min(cluster_vss_y)), (right_x, max(cluster_vss_y)), half_wire)
                boxes.append(("VSS", vss_trunk))
                add_routing_anchor("VSS", vss_trunk, cluster_instances)
            else:
                bus = row_buses[cluster_first_bus]
                add_routing_anchor("VSS", tuple(bus["vss_bbox_dbu"]),
                                   bus["member_instances"])
        power_points = {"VDD": (left_x, top_y), "VSS": (right_x, half_wire)}
        description = (
            "adjacent row-member clusters use local supply trunks and off-cell side feeds; "
            "each disconnected cluster is tied to the perimeter by proven ML1 routes")
    else:
        raise CoreRouterError(
            f"power_topology must be one of edge_rails, segmented_rails, local_clusters; got {topology!r}")
    return {
        "topology": topology,
        "description": description,
        "boxes": boxes,
        "physical_shape_count": len(boxes),
        "power_port_points_dbu": {name: list(point) for name, point in power_points.items()},
        "clusters": clusters,
        "row_buses": row_buses,
        "routing_anchors": routing_anchors,
    }




def _load_accepted_cell(cell_name: str, manifest_path: Path,
                        record: dict[str, Any], physical_spec: dict[str, Any]) -> dict[str, Any]:
    library_root = (manifest_path.parent.parent if manifest_path.parent.name == "doc"
                    else manifest_path.parent).resolve()
    gds_path = (library_root / record["views"]["gds"]).resolve()
    try:
        gds_path.relative_to(library_root)
    except ValueError as error:
        raise CoreRouterError(f"accepted GDS path for {cell_name} escapes its library") from error
    if not gds_path.is_file():
        raise CoreRouterError(f"accepted GDS is missing for {cell_name}: {gds_path}")

    source_layout = db.Layout()
    source_layout.read(str(gds_path))
    top_cells = list(source_layout.top_cells())
    if len(top_cells) != 1 or top_cells[0].name != cell_name:
        raise CoreRouterError(f"{cell_name} GDS must have one flat top named {cell_name}")
    source_cell = top_cells[0]
    if list(source_cell.each_inst()):
        raise CoreRouterError(f"accepted cell GDS for {cell_name} is not flat")
    dbu_um = float(source_layout.dbu)
    if not math.isfinite(dbu_um) or dbu_um <= 0:
        raise CoreRouterError(f"accepted cell GDS for {cell_name} has an invalid DBU")

    boxes_by_layer: dict[tuple[int, int], list[Any]] = defaultdict(list)
    labels: dict[str, Any] = {}
    boundaries = []
    observed_layers = set()
    for layer_index in source_layout.layer_indices():
        info = source_layout.get_info(layer_index)
        key = (int(info.layer), int(info.datatype))
        shapes = list(source_cell.shapes(layer_index).each())
        if not shapes:
            continue
        observed_layers.add(key)
        if key not in NATIVE_LAYERS:
            raise CoreRouterError(
                f"accepted {cell_name} GDS contains non-permitted native layer {key[0]}/{key[1]}")
        for shape in shapes:
            if key == TEXT_LAYER:
                if not shape.is_text():
                    raise CoreRouterError(f"{cell_name} native text layer contains non-text geometry")
                label = shape.text.string
                if label not in physical_spec["pins"] or label in labels:
                    raise CoreRouterError(f"{cell_name} has an unexpected or duplicate pin label {label!r}")
                labels[label] = shape.text.trans
            elif key == BOUNDARY_LAYER:
                if not shape.is_box():
                    raise CoreRouterError(f"{cell_name} 100/0 boundary is not rectangular")
                boundaries.append(shape.box)
            else:
                if not shape.is_box():
                    raise CoreRouterError(
                        f"{cell_name} has non-rectangular geometry on native layer {key[0]}/{key[1]}")
                boxes_by_layer[key].append(shape.box)

    if set(labels) != set(physical_spec["pins"]):
        missing = sorted(set(physical_spec["pins"]) - set(labels))
        raise CoreRouterError(f"{cell_name} GDS pin labels are incomplete: {missing}")
    if len(boundaries) != 1:
        raise CoreRouterError(f"{cell_name} GDS must have one 100/0 boundary")
    width_dbu = _um_to_dbu(record["cell_width_um"], dbu_um, f"{cell_name} width")
    height_dbu = _um_to_dbu(record["cell_height_um"], dbu_um, f"{cell_name} height")
    boundary = boundaries[0]
    if (boundary.left, boundary.bottom, boundary.right, boundary.top) != (0, 0, width_dbu, height_dbu):
        raise CoreRouterError(f"{cell_name} GDS boundary disagrees with its accepted manifest dimensions")

    ml1_boxes = boxes_by_layer.get(ML1_LAYER, [])
    if not ml1_boxes:
        raise CoreRouterError(f"{cell_name} has no real ML1 pin geometry")
    ml1_region = db.Region()
    for box in ml1_boxes:
        ml1_region.insert(box)
    conductors = _electrical_components(boxes_by_layer, dbu_um)
    ml1_components = conductors["components_by_layer"][ML1_LAYER]
    ml1_electrical_ids = conductors["electrical_ids_by_layer"][ML1_LAYER]
    if not ml1_components:
        raise CoreRouterError(f"{cell_name} has no connected ML1 components")

    pin_components: dict[str, int] = {}
    pin_ml1_components: dict[str, int] = {}
    pin_windows: dict[str, list[Any]] = {}
    for pin in physical_spec["pins"]:
        label_point = labels[pin].disp
        label_components = _component_ids_at(ml1_components, label_point)
        if len(label_components) != 1:
            raise CoreRouterError(
                f"{cell_name}/{pin} text label is not on exactly one real ML1 component")
        label_component = label_components[0]
        electrical_component = ml1_electrical_ids[label_component]
        pin_components[pin] = electrical_component
        pin_ml1_components[pin] = label_component
        windows = []
        raw_windows = record["pins"][pin]["windows_um"]
        for window_index, values in enumerate(raw_windows):
            ticks = tuple(_um_to_dbu(value, dbu_um, f"{cell_name}/{pin} access window")
                          for value in values)
            window = db.Box(*ticks)
            fully_on_ml1 = (db.Region(window) - ml1_region).is_empty()
            component_ids, window_electrical_ids = _electrical_ids_at_window(
                window, ml1_components, ml1_electrical_ids)
            windows.append({
                "index": window_index,
                "box": window,
                "fully_on_ml1": fully_on_ml1,
                "ml1_component_ids": component_ids,
                "electrical_component_ids": window_electrical_ids,
                "equivalent_to_label": electrical_component in window_electrical_ids,
            })
        if not windows or any(not window["fully_on_ml1"] for window in windows):
            raise CoreRouterError(f"{cell_name}/{pin} access window is not fully covered by real ML1")
        if not any(window["box"].left <= label_point.x <= window["box"].right and
                   window["box"].bottom <= label_point.y <= window["box"].top
                   for window in windows):
            raise CoreRouterError(f"{cell_name}/{pin} text label is outside every accepted ML1 access window")
        if not any(window["equivalent_to_label"] for window in windows):
            raise CoreRouterError(
                f"{cell_name}/{pin} has no access window electrically equivalent to its source label")
        if pin in ("VDD", "VSS"):
            for window in windows:
                if window["electrical_component_ids"] != [electrical_component]:
                    raise CoreRouterError(
                        f"{cell_name}/{pin} access windows do not share one local real-conductor supply component")
        pin_windows[pin] = windows

    regions_by_layer = {}
    for key in ((7, 0), (5, 0), (3, 0)):
        region = db.Region()
        for box in boxes_by_layer.get(key, []):
            region.insert(box)
        regions_by_layer[key] = region
    direct_contact = (regions_by_layer[(7, 0)] &
                      regions_by_layer[(5, 0)] &
                      regions_by_layer[(3, 0)])
    if not direct_contact.is_empty():
        raise CoreRouterError(f"{cell_name} contains an unqualified CNT/TiN/diffusion contact")

    box_component_ids: list[list[int]] = []
    for box in ml1_boxes:
        box_region = db.Region(box)
        box_component_ids.append([
            index for index, component in enumerate(ml1_components)
            if not (box_region & component).is_empty()
        ])
    return {
        "name": cell_name,
        "record": record,
        "spec": physical_spec,
        "gds_path": gds_path,
        "gds_sha256": _sha256(gds_path),
        "dbu_um": dbu_um,
        "width_dbu": width_dbu,
        "height_dbu": height_dbu,
        "width_um": float(record["cell_width_um"]),
        "height_um": float(record["cell_height_um"]),
        "area_um2": float(record["area_um2"]),
        "boundary": boundary,
        "boxes_by_layer": dict(boxes_by_layer),
        "ml1_boxes": ml1_boxes,
        "ml1_components": ml1_components,
        "ml1_component_electrical_ids": ml1_electrical_ids,
        "ml1_box_component_ids": box_component_ids,
        "conductors": conductors,
        "labels": labels,
        "pin_components": pin_components,
        "pin_ml1_components": pin_ml1_components,
        "pin_windows": pin_windows,
        "observed_layers": sorted(observed_layers),
    }


def build_tiny_benchmark(manifest_path: str | Path | None = None) -> dict[str, Any]:
    """Build a deterministic NAND2-to-INV benchmark using accepted physical cells."""
    accepted = library.physical_specs(manifest_path)
    required = {name: accepted[name] for name in ("INV_X1", "NAND2_X1") if name in accepted}
    if set(required) != {"INV_X1", "NAND2_X1"}:
        raise CoreRouterError("tiny benchmark requires accepted INV_X1 and NAND2_X1 physical views")
    design = mapping.map_boolean_design(
        "TINY_INV_NAND", {"A": "input", "B": "input", "Y": "output"},
        {"N1": "!(A & B)", "Y": "!N1"},
        library_view="physical_qualified", manifest_path=manifest_path, specs=required)
    if {instance["cell"] for instance in design["instances"]} != {"INV_X1", "NAND2_X1"}:
        raise CoreRouterError("tiny benchmark mapping did not select exactly accepted INV_X1/NAND2_X1 cells")
    return design


def _load_design(value: Any, manifest_path: Path, output_netlist: Path) -> tuple[dict[str, Any], str | None]:
    source_hash = None
    if value is None:
        raw = build_tiny_benchmark(manifest_path)
    elif isinstance(value, (str, Path)):
        source_path = Path(value).resolve()
        if not source_path.is_file():
            raise CoreRouterError(f"canonical mapped netlist does not exist: {source_path}")
        source_hash = _sha256(source_path)
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CoreRouterError(f"cannot read canonical mapped netlist: {error}") from error
    else:
        raw = value
    if not isinstance(raw, dict):
        raise CoreRouterError("input must be a canonical mapped-netlist object or JSON path")
    try:
        canonical = mapping.export_netlist(raw, output_netlist)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise CoreRouterError(f"cannot normalize canonical mapped netlist: {error}") from error
    return canonical, source_hash


def _validate_design(design: dict[str, Any], physical_specs: dict[str, dict[str, Any]]) -> None:
    name = _valid_identifier(design.get("name"), "design name")
    ports = design.get("ports")
    instances = design.get("instances")
    if not isinstance(ports, dict) or not isinstance(instances, list) or not instances:
        raise CoreRouterError("canonical design requires a ports mapping and at least one mapped instance")
    net_names: dict[str, str] = {}

    def register_net(value: Any, field: str) -> str:
        net = _valid_identifier(value, field)
        key = net.casefold()
        previous = net_names.get(key)
        if previous is not None and previous != net:
            raise CoreRouterError(f"case-insensitive SPICE net-name collision: {previous!r} and {net!r}")
        net_names[key] = net
        return net

    for port, direction in ports.items():
        register_net(port, "top-level port/net")
        if not isinstance(direction, str) or direction.lower() not in ("input", "output", "inout"):
            raise CoreRouterError(f"invalid direction for top-level port {port!r}: {direction!r}")
    if ports.get("VDD", "").lower() != "inout" or ports.get("VSS", "").lower() != "inout":
        raise CoreRouterError("canonical design must expose VDD and VSS as inout top-level ports")

    instance_names = set()
    terminal_counts: dict[str, int] = defaultdict(int)
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict):
            raise CoreRouterError(f"instance {index} is not an object")
        inst_name = _valid_identifier(instance.get("name"), f"instance {index} name")
        if inst_name.casefold() in instance_names:
            raise CoreRouterError(f"duplicate instance name {inst_name!r}")
        instance_names.add(inst_name.casefold())
        cell = instance.get("cell")
        if cell not in physical_specs:
            raise CoreRouterError(f"instance {inst_name} uses a cell without accepted physical evidence: {cell!r}")
        connections = instance.get("connections")
        expected_pins = set(physical_specs[cell]["pins"])
        if not isinstance(connections, dict) or set(connections) != expected_pins:
            raise CoreRouterError(
                f"instance {inst_name}/{cell} connections must match pins {sorted(expected_pins)}")
        for pin in physical_specs[cell]["pins"]:
            net = register_net(connections[pin], f"{inst_name}/{pin} net")
            if net in ("0", "1"):
                raise CoreRouterError(f"{inst_name}/{pin} has an unmapped constant; use VDD/VSS")
            terminal_counts[net] += 1
        if connections.get("VDD") != "VDD" or connections.get("VSS") != "VSS":
            raise CoreRouterError(f"{inst_name} supply pins must connect to native VDD and VSS nets")
    for port in ports:
        if terminal_counts.get(port, 0) == 0:
            raise CoreRouterError(f"top-level port {port!r} has no physical cell terminal")
    if terminal_counts.get("VDD", 0) == 0 or terminal_counts.get("VSS", 0) == 0:
        raise CoreRouterError("the routed design has no cell terminals on both VDD and VSS")
    design["name"] = name


def _source_component_owners(cell: dict[str, Any], connections: dict[str, str],
                              instance: str) -> tuple[dict[int, str], list[str | None]]:
    component_nets: dict[int, set[str]] = defaultdict(set)
    for pin, component_id in cell["pin_components"].items():
        component_nets[component_id].add(connections[pin])
    electrical_owners: dict[int, str] = {}
    for component_id, nets in component_nets.items():
        if len(nets) != 1:
            raise CoreRouterError(
                f"{instance}/{cell['name']} has distinct mapped nets on one real ML1/TiN/CNT component: "
                f"{sorted(nets)}")
        electrical_owners[component_id] = next(iter(nets))
    ml1_component_owners = {
        index: electrical_owners[electrical_id]
        for index, electrical_id in enumerate(cell["ml1_component_electrical_ids"])
        if electrical_id in electrical_owners
    }
    box_owners: list[str | None] = []
    for component_ids in cell["ml1_box_component_ids"]:
        known_owners = {ml1_component_owners[index]
                        for index in component_ids if index in ml1_component_owners}
        if len(known_owners) > 1:
            raise CoreRouterError(f"{instance}/{cell['name']} ML1 geometry joins different canonical nets")
        box_owners.append(next(iter(known_owners)) if known_owners else None)
    return electrical_owners, box_owners


def _pin_access_candidates(cell: dict[str, Any], pin: str,
                           pitch_dbu: int) -> list[dict[str, Any]]:
    label_point = cell["labels"][pin].disp
    electrical_component = cell["pin_components"][pin]
    candidates = []
    seen = set()
    half_wire = _um_to_dbu(WIRE_WIDTH_UM / 2, cell["dbu_um"], "pin landing half-width")
    # A corner overlap is electrically connected but can leave a sub-6um ML1 neck.
    for window in cell["pin_windows"][pin]:
        if not window["equivalent_to_label"]:
            continue
        box = window["box"]
        x_start = _ceil_multiple(box.left + half_wire, pitch_dbu)
        y_start = _ceil_multiple(box.bottom + half_wire, pitch_dbu)
        x_end = ((box.right - half_wire) // pitch_dbu) * pitch_dbu
        y_end = ((box.top - half_wire) // pitch_dbu) * pitch_dbu
        if x_start > x_end or y_start > y_end:
            continue
        for x in range(x_start, x_end + 1, pitch_dbu):
            for y in range(y_start, y_end + 1, pitch_dbu):
                point = db.Point(x, y)
                component_ids = _component_ids_at(cell["ml1_components"], point)
                equivalent_components = [
                    index for index in component_ids
                    if cell["ml1_component_electrical_ids"][index] == electrical_component
                ]
                if not equivalent_components:
                    continue
                key = (x, y)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "point_dbu": [x, y],
                    "window_index": window["index"],
                    "component_id": equivalent_components[0],
                    "electrical_component_id": electrical_component,
                    "distance_from_label_dbu": abs(x - label_point.x) + abs(y - label_point.y),
                })
    candidates.sort(key=lambda row: (row["window_index"], row["distance_from_label_dbu"],
                                     row["point_dbu"][0], row["point_dbu"][1]))
    if not candidates:
        raise CoreRouterError(
            f"{cell['name']}/{pin} has no electrically equivalent ML1 access point on the deterministic "
            f"{PITCH_UM:g}um route grid")
    return candidates


def _choose_pin_access(cell: dict[str, Any], pin: str,
                       pitch_dbu: int) -> tuple[int, int, int]:
    candidate = _pin_access_candidates(cell, pin, pitch_dbu)[0]
    return (candidate["point_dbu"][0], candidate["point_dbu"][1],
            candidate["window_index"])


class _ObstacleGrid:
    """A* lattice over ML1 keepouts with per-net same-conductor access."""

    def __init__(self, width_dbu: int, height_dbu: int, dbu_um: float,
                 pitch_dbu: int, max_nodes: int = MAX_SEARCH_NODES) -> None:
        self.width_dbu = width_dbu
        self.height_dbu = height_dbu
        self.dbu_um = dbu_um
        self.pitch_dbu = pitch_dbu
        self.max_nodes = max_nodes
        self.half_wire_dbu = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "ML1 route half-width")
        self.clearance_dbu = _um_to_dbu(ROUTE_CLEARANCE_UM, dbu_um, "ML1 route clearance")
        self.min_ix = _ceil_div(self.half_wire_dbu, pitch_dbu)
        self.min_iy = _ceil_div(self.half_wire_dbu, pitch_dbu)
        self.max_ix = (width_dbu - self.half_wire_dbu) // pitch_dbu
        self.max_iy = (height_dbu - self.half_wire_dbu) // pitch_dbu
        self.rows: dict[int, list[tuple[int, int, str | None]]] = defaultdict(list)

    def add_box(self, bounds: tuple[int, int, int, int], owner: str | None) -> None:
        left, bottom, right, top = bounds
        xlo = _ceil_div(left - self.clearance_dbu, self.pitch_dbu)
        xhi = (right + self.clearance_dbu) // self.pitch_dbu
        ylo = _ceil_div(bottom - self.clearance_dbu, self.pitch_dbu)
        yhi = (top + self.clearance_dbu) // self.pitch_dbu
        for iy in range(ylo, yhi + 1):
            self.rows[iy].append((xlo, xhi, owner))

    def sort_rows(self) -> None:
        for intervals in self.rows.values():
            intervals.sort(key=lambda row: (row[0], row[1], "" if row[2] is None else row[2]))

    def blocked(self, node: tuple[int, int], net: str) -> bool:
        x, y = node
        for xlo, xhi, owner in self.rows.get(y, ()):
            if xlo > x:
                break
            if owner != net and x <= xhi:
                return True
        return False

    def route(self, start_dbu: tuple[int, int] | list[tuple[int, int]],
              goal_dbu: tuple[int, int] | list[tuple[int, int]],
              net: str) -> tuple[list[tuple[int, int]] | None, str, int]:
        step = self.pitch_dbu
        starts = ([start_dbu] if isinstance(start_dbu, tuple) and len(start_dbu) == 2 and
                  isinstance(start_dbu[0], int) else list(start_dbu))
        goals = ([goal_dbu] if isinstance(goal_dbu, tuple) and len(goal_dbu) == 2 and
                 isinstance(goal_dbu[0], int) else list(goal_dbu))
        if not starts or not goals:
            return None, "no_route_access_candidates", 0
        if any(value % step for point in starts + goals for value in point):
            return None, "endpoint_not_on_route_grid", 0
        start_nodes = sorted({(point[0] // step, point[1] // step) for point in starts})
        goal_nodes = {(point[0] // step, point[1] // step) for point in goals}

        def in_bounds(node: tuple[int, int]) -> bool:
            return (self.min_ix <= node[0] <= self.max_ix and
                    self.min_iy <= node[1] <= self.max_iy)

        legal_starts = [node for node in start_nodes if in_bounds(node) and not self.blocked(node, net)]
        legal_goals = {node for node in goal_nodes if in_bounds(node) and not self.blocked(node, net)}
        if not legal_starts:
            reason = "start_outside_route_core" if not any(in_bounds(node) for node in start_nodes) else \
                "start_blocked_by_foreign_ml1"
            return None, reason, 0
        if not legal_goals:
            reason = "goal_outside_route_core" if not any(in_bounds(node) for node in goal_nodes) else \
                "goal_blocked_by_foreign_ml1"
            return None, reason, 0
        overlap = sorted(set(legal_starts) & legal_goals)
        if overlap:
            point = overlap[0]
            return [(point[0] * step, point[1] * step)], "connected_at_existing_access", 0

        min_gx = min(node[0] for node in legal_goals)
        max_gx = max(node[0] for node in legal_goals)
        min_gy = min(node[1] for node in legal_goals)
        max_gy = max(node[1] for node in legal_goals)

        def heuristic(node: tuple[int, int]) -> int:
            # Distance to the target-set bounding box is admissible. Pin windows
            # contain hundreds of lattice points; scanning all of them per node
            # makes a bounded geometric search needlessly quadratic.
            return (max(min_gx - node[0], 0, node[0] - max_gx) +
                    max(min_gy - node[1], 0, node[1] - max_gy))

        queue: list[tuple[int, int, tuple[int, int]]] = []
        previous: dict[tuple[int, int], tuple[int, int] | None] = {}
        costs: dict[tuple[int, int], int] = {}
        serial = 0
        for start in legal_starts:
            previous[start] = None
            costs[start] = 0
            heapq.heappush(queue, (heuristic(start), serial, start))
            serial += 1
        expanded = 0
        directions = ((1, 0), (0, 1), (-1, 0), (0, -1))
        while queue:
            _estimate, _serial, current = heapq.heappop(queue)
            if current in legal_goals:
                path = []
                cursor: tuple[int, int] | None = current
                while cursor is not None:
                    path.append((cursor[0] * step, cursor[1] * step))
                    cursor = previous[cursor]
                return list(reversed(path)), "connected", expanded
            expanded += 1
            if expanded > self.max_nodes:
                return None, "a_star_expansion_limit", expanded
            current_cost = costs[current]
            for dx, dy in directions:
                neighbor = (current[0] + dx, current[1] + dy)
                if not in_bounds(neighbor) or self.blocked(neighbor, net):
                    continue
                new_cost = current_cost + 1
                if neighbor in costs and costs[neighbor] <= new_cost:
                    continue
                costs[neighbor] = new_cost
                previous[neighbor] = current
                heapq.heappush(queue, (new_cost + heuristic(neighbor), serial, neighbor))
                serial += 1
        return None, "no_legal_ml1_path", expanded


def _wire_box(a: tuple[int, int], b: tuple[int, int], half_width: int) -> tuple[int, int, int, int]:
    if a[0] != b[0] and a[1] != b[1]:
        raise CoreRouterError("route search emitted a non-Manhattan segment")
    return (min(a[0], b[0]) - half_width, min(a[1], b[1]) - half_width,
            max(a[0], b[0]) + half_width, max(a[1], b[1]) + half_width)


def _path_edges(path: list[tuple[int, int]]) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    return {tuple(sorted((a, b))) for a, b in zip(path, path[1:]) if a != b}


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _centerline_length_dbu(edges: set[tuple[tuple[int, int], tuple[int, int]]]) -> int:
    intervals: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for first, second in edges:
        if first[0] == second[0]:
            key = (0, first[0])
            interval = sorted((first[1], second[1]))
        elif first[1] == second[1]:
            key = (1, first[1])
            interval = sorted((first[0], second[0]))
        else:
            raise CoreRouterError("centerline contains a non-Manhattan segment")
        intervals[key].append((interval[0], interval[1]))
    total = 0
    for spans in intervals.values():
        spans.sort()
        start, end = spans[0]
        for next_start, next_end in spans[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                total += end - start
                start, end = next_start, next_end
        total += end - start
    return total


def _connect_net(grid: _ObstacleGrid, anchors: list[dict[str, Any]],
                 net: str) -> dict[str, Any]:
    terminals = []
    for index, anchor in enumerate(anchors):
        candidates = anchor.get("candidate_accesses")
        if candidates is None:
            candidates = [{"point_dbu": list(anchor["point_dbu"])}]
        unique_candidates = []
        seen = set()
        for candidate in candidates:
            point = tuple(candidate["point_dbu"])
            if point in seen:
                continue
            seen.add(point)
            unique_candidates.append({**candidate, "point_dbu": list(point)})
        if unique_candidates:
            terminals.append({"anchor_index": index, "candidates": unique_candidates,
                              "equivalent_accesses": anchor.get("kind") == "cell_pin"})
    if not terminals:
        return {"passed": False, "error": "net has no physical terminals", "paths": [],
                "terminal_accesses": [None] * len(anchors)}

    selected_accesses: list[dict[str, Any] | None] = [None] * len(anchors)
    root = terminals.pop(0)
    selected_accesses[root["anchor_index"]] = root["candidates"][0]
    if not root["equivalent_accesses"]:
        root["candidates"] = root["candidates"][:1]
    connected_terminals = [root]
    connected_points = sorted({tuple(row["point_dbu"]) for row in root["candidates"]})
    paths = []
    expanded = 0
    last_failures = []
    while terminals:
        terminals.sort(key=lambda terminal: min(
            _manhattan(source, tuple(candidate["point_dbu"]))
            for source in connected_points for candidate in terminal["candidates"]))
        selected = None
        for terminal in terminals:
            targets = [tuple(candidate["point_dbu"]) for candidate in terminal["candidates"]]
            path, reason, seen = grid.route(connected_points, targets, net)
            expanded += seen
            if path is not None:
                target_point = tuple(path[-1])
                access = next(row for row in terminal["candidates"]
                              if tuple(row["point_dbu"]) == target_point)
                selected = (terminal, access, path)
                break
            last_failures.append({
                "anchor_index": terminal["anchor_index"],
                "candidate_count": len(targets),
                "reason": reason,
                "expanded_nodes": seen,
            })
        if selected is None:
            return {
                "passed": False,
                "error": "no legal ML1 connection could be proven for every terminal",
                "paths": paths,
                "attempted_paths": paths,
                "expanded_nodes": expanded,
                "failed_terminals": last_failures[-32:],
                "terminal_accesses": selected_accesses,
            }
        terminal, access, path = selected
        source_point = tuple(path[0])
        for source_terminal in connected_terminals:
            source_access = next(
                (candidate for candidate in source_terminal["candidates"]
                 if tuple(candidate["point_dbu"]) == source_point), None)
            if source_access is not None:
                selected_accesses[source_terminal["anchor_index"]] = source_access
        anchor_index = terminal["anchor_index"]
        selected_accesses[anchor_index] = access
        if not terminal["equivalent_accesses"]:
            terminal["candidates"] = [access]
        compressed = _compressed_path(path)
        paths.append({
            "target_anchor_index": anchor_index,
            "from_dbu": list(path[0]),
            "to_dbu": list(path[-1]),
            "target_point_dbu": list(path[-1]),
            "target_access": access,
            "points_dbu": [list(point) for point in compressed],
        })
        connected_points.extend(path)
        connected_points.extend(tuple(row["point_dbu"]) for row in terminal["candidates"])
        connected_points = sorted(set(connected_points))
        connected_terminals.append(terminal)
        terminals.remove(terminal)
    return {
        "passed": True,
        "paths": paths,
        "expanded_nodes": expanded,
        "terminal_accesses": selected_accesses,
    }


def _box_centerline_points(bounds: tuple[int, int, int, int],
                           pitch_dbu: int, half_wire_dbu: int) -> set[tuple[int, int]]:
    left, bottom, right, top = bounds
    if right - left >= top - bottom:
        y = _ceil_multiple((bottom + top) // 2, pitch_dbu)
        first = _ceil_multiple(left + half_wire_dbu, pitch_dbu)
        last = ((right - half_wire_dbu) // pitch_dbu) * pitch_dbu
        return {(x, y) for x in range(first, last + 1, pitch_dbu)}
    x = _ceil_multiple((left + right) // 2, pitch_dbu)
    first = _ceil_multiple(bottom + half_wire_dbu, pitch_dbu)
    last = ((top - half_wire_dbu) // pitch_dbu) * pitch_dbu
    return {(x, y) for y in range(first, last + 1, pitch_dbu)}


def _connect_power_net(grid: _ObstacleGrid, anchors: list[dict[str, Any]],
                       net: str, pitch_dbu: int,
                       half_wire_dbu: int) -> dict[str, Any]:
    backbone_count = next((index for index, anchor in enumerate(anchors)
                           if anchor["kind"] == "cell_pin"), len(anchors))
    backbone = anchors[:backbone_count]
    cell_pins = anchors[backbone_count:]
    if not backbone:
        return {"passed": False, "error": "power net has no planned perimeter or feedthrough",
                "paths": [], "attempted_paths": [], "expanded_nodes": 0,
                "failed_terminals": [], "terminal_accesses": [None] * len(anchors)}

    trunk = _connect_net(grid, backbone, net)
    trunk_accesses = list(trunk.get("terminal_accesses", []))
    if len(trunk_accesses) < backbone_count:
        trunk_accesses.extend([None] * (backbone_count - len(trunk_accesses)))
    terminal_accesses = [*trunk_accesses, *([None] * len(cell_pins))]
    paths = list(trunk.get("paths", []))
    for path in paths:
        points = path["points_dbu"]
        for first, second in zip(points, points[1:]):
            if first != second:
                grid.add_box(_wire_box(tuple(first), tuple(second), half_wire_dbu), net)
    grid.sort_rows()
    if not trunk["passed"]:
        return {
            **trunk,
            "paths": paths,
            "terminal_accesses": terminal_accesses,
            "failed_terminals": trunk.get("failed_terminals", []),
            "power_terminal_policy": "planned_perimeter_and_trunks_before_cell_pins",
        }

    trunk_points_by_instance: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for anchor in backbone:
        members = anchor.get("trunk_instances", [])
        if not members:
            continue
        points = set()
        bounds = anchor.get("box_dbu")
        if bounds is not None:
            points.update(_box_centerline_points(
                tuple(bounds), pitch_dbu, half_wire_dbu))
        candidates = anchor.get("candidate_accesses")
        if candidates is None:
            candidates = [{"point_dbu": anchor["point_dbu"]}]
        points.update(tuple(candidate["point_dbu"]) for candidate in candidates)
        for instance in members:
            trunk_points_by_instance[instance].update(points)

    expanded = int(trunk.get("expanded_nodes", 0))
    failures = []
    for index, anchor in enumerate(cell_pins, start=backbone_count):
        designated_trunk = sorted(trunk_points_by_instance.get(anchor["instance"], ()))
        if not designated_trunk:
            failures.append({
                "anchor_index": index,
                "candidate_count": len(anchor.get("candidate_accesses", [])),
                "reason": "no_designated_power_trunk",
                "expanded_nodes": 0,
            })
            continue
        candidates = anchor.get("candidate_accesses")
        if candidates is None:
            candidates = [{"point_dbu": anchor["point_dbu"]}]
        targets = [tuple(candidate["point_dbu"]) for candidate in candidates]
        path, reason, seen = grid.route(designated_trunk, targets, net)
        expanded += seen
        if path is None:
            failures.append({
                "anchor_index": index,
                "candidate_count": len(targets),
                "reason": reason,
                "expanded_nodes": seen,
            })
            continue
        target_point = tuple(path[-1])
        access = next(candidate for candidate in candidates
                      if tuple(candidate["point_dbu"]) == target_point)
        terminal_accesses[index] = access
        points = _compressed_path(path)
        paths.append({
            "target_anchor_index": index,
            "from_dbu": list(path[0]),
            "to_dbu": list(path[-1]),
            "target_point_dbu": list(path[-1]),
            "target_access": access,
            "points_dbu": [list(point) for point in points],
        })
        for first, second in zip(points, points[1:]):
            if first != second:
                grid.add_box(_wire_box(first, second, half_wire_dbu), net)
        grid.sort_rows()

    result = {
        "passed": not failures,
        "paths": paths,
        "attempted_paths": paths,
        "expanded_nodes": expanded,
        "terminal_accesses": terminal_accesses,
        "power_terminal_policy": "planned_perimeter_then_designated_row_trunk",
    }
    if failures:
        result["error"] = "no legal ML1 connection could be proven from designated power trunks"
        result["failed_terminals"] = failures
    return result


def _segments_cross(first: tuple[tuple[int, int], tuple[int, int]],
                    second: tuple[tuple[int, int], tuple[int, int]]) -> tuple[int, int] | None:
    """Return an intersection of closed orthogonal segments, including overlaps."""
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second
    if (ax != bx and ay != by) or (cx != dx and cy != dy):
        raise CoreRouterError("crossing evidence requires orthogonal centerlines")
    left = max(min(ax, bx), min(cx, dx))
    right = min(max(ax, bx), max(cx, dx))
    bottom = max(min(ay, by), min(cy, dy))
    top = min(max(ay, by), max(cy, dy))
    return (left, bottom) if left <= right and bottom <= top else None


def _crossing_report(net_anchors: dict[str, list[dict[str, Any]]],
                     route_edges: dict[str, set[tuple[tuple[int, int], tuple[int, int]]]]) -> dict[str, Any]:
    baseline_segments: dict[str, list[tuple[tuple[int, int], tuple[int, int]]]] = {}
    for net, anchors in net_anchors.items():
        points = []
        for anchor in anchors:
            point = tuple(anchor["point_dbu"])
            if point not in points:
                points.append(point)
        if len(points) < 2:
            continue
        root = points[0]
        segments = []
        for point in points[1:]:
            bend = (point[0], root[1])
            if root != bend:
                segments.append((root, bend))
            if bend != point:
                segments.append((bend, point))
        baseline_segments[net] = segments
    baseline_crossings = set()
    names = sorted(baseline_segments)
    for index, first_net in enumerate(names):
        for second_net in names[index + 1:]:
            for first in baseline_segments[first_net]:
                for second in baseline_segments[second_net]:
                    point = _segments_cross(first, second)
                    if point is not None:
                        baseline_crossings.add((first_net, second_net, point))
    actual_crossings = set()
    names = sorted(route_edges)
    for index, first_net in enumerate(names):
        first_segments = sorted(route_edges[first_net])
        for second_net in names[index + 1:]:
            for first in first_segments:
                for second in route_edges[second_net]:
                    point = _segments_cross(first, second)
                    if point is not None:
                        actual_crossings.add((first_net, second_net, point))
    return {
        "baseline_pairwise_crossings": len(baseline_crossings),
        "actual_centerline_crossings": len(actual_crossings),
        "actual_crossing_points_dbu": [
            {"nets": [first, second], "point_dbu": list(point)}
            for first, second, point in sorted(actual_crossings)
        ],
        "method": ("baseline is the count of pairwise intersections in deterministic one-bend "
                   "x-then-y direct connections; actual is counted from routed ML1 centerlines"),
        "capacity_claim": False,
    }


def _prove_connectivity(top: Any, layer_indices: dict[tuple[int, int], int],
                        anchors_by_net: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    boxes_by_layer: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for layer in (ML1_LAYER, (5, 0), (7, 0), (3, 0)):
        layer_index = layer_indices[layer]
        for shape in top.shapes(layer_index).each():
            if shape.is_box():
                boxes_by_layer[layer].append(shape.box)
    conductors = _electrical_components(boxes_by_layer, float(top.layout().dbu))
    components = conductors["components_by_layer"][ML1_LAYER]
    ml1_electrical_ids = conductors["electrical_ids_by_layer"][ML1_LAYER]
    component_to_nets: dict[int, set[str]] = defaultdict(set)
    nets = {}
    for net, anchors in sorted(anchors_by_net.items()):
        anchor_rows = []
        ids = []
        for anchor in anchors:
            point = db.Point(*anchor["point_dbu"])
            point_ids = _component_ids_at(components, point)
            electrical_ids = sorted({ml1_electrical_ids[index] for index in point_ids})
            anchor_rows.append({
                "kind": anchor["kind"],
                "instance": anchor.get("instance"),
                "pin": anchor.get("pin"),
                "point_dbu": list(anchor["point_dbu"]),
                "ml1_component_ids": point_ids,
                "electrical_component_ids": electrical_ids,
                "on_real_ml1": bool(point_ids),
            })
            ids.extend(electrical_ids)
        unique_ids = sorted(set(ids))
        for component_id in unique_ids:
            component_to_nets[component_id].add(net)
        nets[net] = {
            "connected": bool(anchors) and all(row["on_real_ml1"] for row in anchor_rows) and
                         len(unique_ids) == 1,
            "component_ids": unique_ids,
            "anchors": anchor_rows,
        }
    shorts = [{"component_id": component_id, "nets": sorted(net_names)}
              for component_id, net_names in sorted(component_to_nets.items())
              if len(net_names) > 1]
    shorted_nets = {net for short in shorts for net in short["nets"]}
    for net in shorted_nets:
        nets[net]["connected"] = False
        nets[net]["shorted_to_other_net"] = True
    layer_component_counts = {
        f"{layer[0]}/{layer[1]}": len(conductors["components_by_layer"][layer])
        for layer in ((5, 0), ML1_LAYER)
    }
    for layer in ((3, 0), (7, 0)):
        region = db.Region()
        for box in boxes_by_layer.get(layer, []):
            region.insert(box)
        layer_component_counts[f"{layer[0]}/{layer[1]}"] = region.merged().count()
    return {
        "passed": bool(nets) and all(row["connected"] for row in nets.values()) and not shorts,
        "method": conductors["method"],
        "text_labels_used_for_connectivity": False,
        "channel_or_diffusion_conduction_used": False,
        "component_count": conductors["electrical_component_count"],
        "layer_component_counts": layer_component_counts,
        "nets": nets,
        "shorts": shorts,
    }


def _initial_evidence(output_dir: Path, base: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "design_name": None,
        "top_cell": None,
        "process_profile": library.PROFILE,
        "status": "failed",
        "failed": True,
        "rankable": False,
        "rankable_scope": "strict_physical_and_electrical_acceptance_only; route proof is not RFC rankability",
        "failure": None,
        "failure_reasons": [],
        "area_domain": ("flat core-router sandbox including accepted cell placement, whitespace, "
                        "and signal/power route channels; excludes I/O, ESD, SRAM, PDN, clock, "
                        "repair, and physical-only-cell integration"),
        "cell_geometry_area_um2": None,
        "placement_width_limit_um": None,
        "allocated_core_area_um2": None,
        "allocated_bbox_area_um2": None,
        "sandbox_route_area_um2": None,
        "routed_core_area_um2": None,
        "route_proof_passed": False,
        "power_topology": None,
        "power_topology_plan": None,
        "placement_constraints": None,
        "placement_bbox": None,
        "route_bbox": None,
        "overflow_count": 0,
        "pin_access": {"passed": False, "pins": []},
        "crossing_demand": None,
        "wire_lengths": {"units": "um", "by_layer": {}, "by_net": {}},
        "native_layers_only": None,
        "native_layers_used": [],
        "routing_layers_used": ["ML1"],
        "provenance_hashes": {},
        "qualification_limits": {
            "accepted_cells": "standalone strict physical cell views are experimental publication evidence only",
            "placement": "deterministic R0 placement; no qualified site, row, pitch, or placement flow",
            "routing": "external signals and VDD/VSS use native ML1 only; no upper-metal or synthetic layers",
            "io_esd": "deferred; top boundary text labels are abstract net endpoints, not qualified I/O pads",
            "sram": "deferred",
            "power": "signal-level VDD/VSS geometry only; no PDN, IR-drop, or EM claim",
            "pex": "not run; coupling and routed interconnect extraction remain unqualified",
            "foundry_signoff": "not claimed; route evidence and verify preflight do not replace strict DRC/LVS/PEX/assembly signoff",
            "frame_capacity": "not integrated with a frame; no frame capacity or routed-core feasibility claim",
        },
        "verification": {
            "verify_contract_preflight": False,
            "strict_drc": None,
            "strict_lvs": None,
            "pex": None,
            "assembly": None,
            "foundry_signoff_claimed": False,
            "frame_capacity_claimed": False,
        },
        "artifacts": {
            "mapped_netlist": str(output_dir / f"{base}.mapped.json"),
            "gds": str(output_dir / f"{base}.gds"),
            "reference_spice": str(output_dir / f"{base}.spice"),
            "route_evidence": str(output_dir / f"{base}.route.json"),
        },
    }


def _persist_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _place_instances(instances: list[dict[str, Any]], cells: dict[str, dict[str, Any]],
                     base_y: int, pitch_dbu: int, gap_dbu: int, margin_dbu: int,
                     max_core_width_dbu: int | None) -> list[dict[str, Any]]:
    placements = []
    cursor_x = _ceil_multiple(margin_dbu, pitch_dbu)
    row_y = base_y
    row_height = 0
    row_index = 0
    row_has_cells = False
    dbu_um = next(iter(cells.values()))["dbu_um"]
    row_gap_dbu = (0 if max_core_width_dbu is None else
                   _um_to_dbu(MULTIROW_GAP_UM, dbu_um, "multirow placement gap"))
    if max_core_width_dbu is not None and max_core_width_dbu <= 2 * margin_dbu:
        raise CoreRouterError(
            f"max_core_width_um leaves no placement width after "
            f"{2 * margin_dbu * dbu_um:g}um of total core margins")

    for instance in instances:
        cell = cells[instance["cell"]]
        width_dbu = cell["width_dbu"]
        if max_core_width_dbu is not None and width_dbu > max_core_width_dbu - 2 * margin_dbu:
            minimum = (width_dbu + 2 * margin_dbu) * cell["dbu_um"]
            raise CoreRouterError(
                f"{instance['cell']} width {width_dbu * cell['dbu_um']:g}um cannot fit "
                f"max_core_width_um={max_core_width_dbu * cell['dbu_um']:g}um after "
                f"{2 * margin_dbu * cell['dbu_um']:g}um of total core margins; "
                f"minimum core width is {minimum:g}um")

        candidate_x = _ceil_multiple(
            cursor_x + (gap_dbu if row_has_cells else 0), pitch_dbu)
        if (max_core_width_dbu is not None and row_has_cells and
                candidate_x + width_dbu > max_core_width_dbu - margin_dbu):
            row_index += 1
            row_y = _ceil_multiple(row_y + row_height + row_gap_dbu, pitch_dbu)
            cursor_x = _ceil_multiple(margin_dbu, pitch_dbu)
            row_height = 0
            row_has_cells = False
            candidate_x = cursor_x

        x = candidate_x
        y = row_y
        _, box_owners = _source_component_owners(
            cell, instance["connections"], instance["name"])
        pin_anchors = {}
        for pin in cell["spec"]["pins"]:
            candidates = _pin_access_candidates(cell, pin, pitch_dbu)
            first = candidates[0]
            pin_anchors[pin] = {
                "local_dbu": first["point_dbu"],
                "window_index": first["window_index"],
                "component_id": first["component_id"],
                "candidate_accesses": candidates,
            }
        placements.append({
            "instance": instance["name"],
            "cell": instance["cell"],
            "connections": instance["connections"],
            "x_dbu": x,
            "y_dbu": y,
            "width_dbu": width_dbu,
            "height_dbu": cell["height_dbu"],
            "row_index": row_index,
            "box_owners": box_owners,
            "pin_anchors": pin_anchors,
        })
        cursor_x = x + width_dbu
        row_height = max(row_height, cell["height_dbu"])
        row_has_cells = True
    return placements


def route_core(netlist: dict[str, Any] | str | Path | None,
               output_dir: str | Path, *,
               manifest_path: str | Path | None = None,
               top_name: str | None = None,
               power_topology: str = "edge_rails",
               max_core_width_um: float | None = None) -> dict[str, Any]:
    """Place and route a canonical mapped netlist, or the default tiny benchmark.

    ``max_core_width_um`` enables deterministic row packing within a geometric
    core-width cap; ``None`` preserves the original single horizontal row.
    """
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    requested_design = (str(netlist.get("name", "CORE")) if isinstance(netlist, dict)
                        else Path(netlist).stem if isinstance(netlist, (str, Path))
                        else "TINY_INV_NAND")
    base = _safe_slug(requested_design).lower()
    evidence_path = out / f"{base}.route.json"
    evidence = _initial_evidence(out, base)
    stage = "load_library"
    try:
        evidence["power_topology"] = power_topology
        if max_core_width_um is not None:
            if (isinstance(max_core_width_um, bool) or
                    not isinstance(max_core_width_um, (int, float)) or
                    not math.isfinite(float(max_core_width_um)) or max_core_width_um <= 0):
                raise CoreRouterError("max_core_width_um must be a finite positive number")
            evidence["placement_width_limit_um"] = float(max_core_width_um)
        else:
            evidence["placement_width_limit_um"] = None
        if power_topology not in ("edge_rails", "segmented_rails", "local_clusters"):
            raise CoreRouterError(
                "power_topology must be one of edge_rails, segmented_rails, local_clusters")
        selected_manifest = Path(manifest_path).resolve() if manifest_path is not None else library.DEFAULT_MANIFEST.resolve()
        manifest = library.load_library(selected_manifest, check=True)
        accepted_specs = library.physical_specs(selected_manifest)
        accepted_records = library.physical_manifests(selected_manifest)
        manifest_records = {
            record["cell"]: record for record in manifest["cells"]
            if record.get("status") == "strict_physical_pass"
        }
        if not accepted_specs or set(accepted_specs) != set(accepted_records):
            raise CoreRouterError("accepted physical specs and manifests are inconsistent")
        evidence["provenance_hashes"]["manifest_sha256"] = _sha256(selected_manifest)

        stage = "normalize_netlist"
        design, source_netlist_hash = _load_design(
            netlist, selected_manifest, Path(evidence["artifacts"]["mapped_netlist"]))
        evidence["provenance_hashes"]["canonical_netlist_sha256"] = _sha256(
            Path(evidence["artifacts"]["mapped_netlist"]))
        if source_netlist_hash:
            evidence["provenance_hashes"]["input_netlist_sha256"] = source_netlist_hash
        _validate_design(design, accepted_specs)
        design_name = design["name"]
        output_top = _top_cell_name(design_name, top_name)
        evidence["design_name"] = design_name
        evidence["top_cell"] = output_top
        used_cells = sorted({instance["cell"] for instance in design["instances"]})
        stage = "write_reference_spice"
        reference_design = {
            "name": output_top,
            "specs": {name: accepted_specs[name] for name in used_cells},
            "ports": design["ports"],
            "instances": design["instances"],
        }
        logic.write_reference(reference_design, evidence["artifacts"]["reference_spice"],
                              top_name=output_top)
        evidence["artifacts"]["reference_spice_sha256"] = _sha256(
            Path(evidence["artifacts"]["reference_spice"]))

        stage = "load_accepted_cells"
        cells = {}
        for cell_name in used_cells:
            if cell_name not in accepted_records or cell_name not in manifest_records:
                raise CoreRouterError(f"{cell_name} has no unique strict-physical-pass manifest record")
            cells[cell_name] = _load_accepted_cell(
                cell_name, selected_manifest, accepted_records[cell_name], accepted_specs[cell_name])
        dbu_um = next(iter(cells.values()))["dbu_um"]
        if any(not math.isclose(cell["dbu_um"], dbu_um, rel_tol=0, abs_tol=1e-12)
               for cell in cells.values()):
            raise CoreRouterError("accepted cells have different database units")
        pitch_dbu = _um_to_dbu(PITCH_UM, dbu_um, "route pitch")
        gap_dbu = _um_to_dbu(PLACEMENT_GAP_UM, dbu_um, "placement gap")
        margin_dbu = _um_to_dbu(CORE_MARGIN_UM, dbu_um, "core margin")
        route_channel_dbu = _um_to_dbu(ROUTE_CHANNEL_UM, dbu_um, "route channel")
        port_spacing_dbu = _um_to_dbu(PORT_SPACING_UM, dbu_um, "port spacing")
        if pitch_dbu <= 0:
            raise CoreRouterError("route pitch is below the accepted GDS database resolution")

        stage = "place_cells"
        base_y = _ceil_multiple(margin_dbu, pitch_dbu)
        max_core_width_dbu = (
            None if max_core_width_um is None else
            _um_to_dbu(float(max_core_width_um), dbu_um, "max_core_width_um"))
        row_gap_dbu = (0 if max_core_width_dbu is None else
                       _um_to_dbu(MULTIROW_GAP_UM, dbu_um, "multirow placement gap"))
        if max_core_width_dbu is not None and max_core_width_dbu <= 0:
            raise CoreRouterError("max_core_width_um must be at least one source DBU")
        placements = _place_instances(
            design["instances"], cells, base_y, pitch_dbu, gap_dbu, margin_dbu,
            max_core_width_dbu)
        placement_left = min(item["x_dbu"] for item in placements)
        placement_bottom = min(item["y_dbu"] for item in placements)
        placement_right = max(item["x_dbu"] + item["width_dbu"] for item in placements)
        placement_top = max(item["y_dbu"] + item["height_dbu"] for item in placements)
        port_names = list(design["ports"])
        track_y = _ceil_multiple(placement_top + route_channel_dbu, pitch_dbu)
        minimum_port_width_dbu = (
            margin_dbu + (len(port_names) + 1) * port_spacing_dbu + margin_dbu)
        required_width_dbu = max(placement_right + margin_dbu, minimum_port_width_dbu)
        core_width = _ceil_multiple(required_width_dbu, pitch_dbu)
        if max_core_width_dbu is not None and core_width > max_core_width_dbu:
            raise CoreRouterError(
                f"max_core_width_um={max_core_width_dbu * dbu_um:g}um cannot accommodate "
                f"the placed cells and {len(port_names)} perimeter ports; at least "
                f"{core_width * dbu_um:g}um is required")
        core_height = _ceil_multiple(track_y + margin_dbu, pitch_dbu)
        evidence["placement_constraints"] = {
            "strategy": ("bounded_width_first_fit_rows" if max_core_width_dbu is not None
                         else "single_horizontal_row"),
            "max_core_width_um": (None if max_core_width_dbu is None
                                  else max_core_width_dbu * dbu_um),
            "width_units": "um",
            "row_gap_um": (None if max_core_width_dbu is None else row_gap_dbu * dbu_um),
            "row_gap_basis": ("40um conservative engineering routing channel; not a qualified row pitch"
                              if max_core_width_dbu is not None else None),
            "placement_gap_um": gap_dbu * dbu_um,
            "site_qualified": False,
            "rows_qualified": False,
        }
        power_plan = _power_topology_plan(
            power_topology, placements, core_width, core_height, dbu_um, pitch_dbu)
        core_bounds = (0, 0, core_width, core_height)
        for net, bounds in power_plan["boxes"]:
            if (bounds[0] < core_bounds[0] or bounds[1] < core_bounds[1] or
                    bounds[2] > core_bounds[2] or bounds[3] > core_bounds[3]):
                raise CoreRouterError(
                    f"{net} power topology shape {bounds} exceeds actual core bounds {core_bounds}")
        preferred_points = {}
        for port in port_names:
            pin_points = sorted(
                (item["x_dbu"] + item["pin_anchors"][pin]["candidate_accesses"][0]["point_dbu"][0],
                 item["y_dbu"] + item["pin_anchors"][pin]["candidate_accesses"][0]["point_dbu"][1])
                for item in placements for pin, net in item["connections"].items()
                if net == port)
            if pin_points:
                preferred_points[port] = pin_points[(len(pin_points) - 1) // 2]
        port_anchors = _port_endpoints(
            port_names, core_width, core_height, dbu_um, pitch_dbu,
            {name: tuple(point)
             for name, point in power_plan["power_port_points_dbu"].items()},
            preferred_points, design["ports"])
        vdd_offset_dbu = _um_to_dbu(12.0, dbu_um, "VDD row rail offset")
        vss_offset_dbu = _um_to_dbu(11.0, dbu_um, "VSS row rail offset")
        rows = _placement_rows(placements)
        row_gaps_dbu = [
            rows[index + 1]["bottom_dbu"] - rows[index]["top_dbu"]
            for index in range(len(rows) - 1)
        ]
        evidence["power_topology_plan"] = {
            "topology": power_plan["topology"],
            "description": power_plan["description"],
            "coordinate_units": "dbu",
            "dbu_um": dbu_um,
            "physical_shape_count": power_plan["physical_shape_count"],
            "power_port_points_dbu": power_plan["power_port_points_dbu"],
            "physical_boxes_dbu": [
                {"net": net, "box_dbu": list(bounds)}
                for net, bounds in power_plan["boxes"]
            ],
            "row_buses": power_plan["row_buses"],
            "row_gap_um": [value * dbu_um for value in row_gaps_dbu],
            "opposing_row_rail_center_spacing_um": (
                [
                    ((rows[index + 1]["bottom_dbu"] - vss_offset_dbu) -
                     (rows[index]["top_dbu"] + vdd_offset_dbu)) * dbu_um
                    for index in range(len(rows) - 1)
                ] if power_plan["topology"] in ("segmented_rails", "local_clusters")
                else []),
            "minimum_ml1_center_spacing_um": WIRE_WIDTH_UM + ML1_SPACE_UM,
            "physical_shapes_inside_actual_core_boundary": True,
            "pdn_claimed": False,
            "clusters": power_plan["clusters"],
        }
        evidence["placement_bbox"] = {
            "bbox_um": [placement_left * dbu_um, placement_bottom * dbu_um,
                        placement_right * dbu_um, placement_top * dbu_um],
            "placement_bbox_area_um2": ((placement_right - placement_left) *
                                         (placement_top - placement_bottom) * dbu_um * dbu_um),
            "cell_geometry_area_um2": sum(cells[item["cell"]]["area_um2"] for item in placements),
            "cell_count": len(placements),
            "row_count": len(rows),
            "row_gap_um": (row_gaps_dbu[0] * dbu_um if row_gaps_dbu else None),
            "site_or_row_qualification": None,
        }
        evidence["cell_geometry_area_um2"] = sum(
            cells[item["cell"]]["area_um2"] for item in placements)
        allocated_area_um2 = float(core_width * core_height * dbu_um * dbu_um)
        evidence["allocated_core_area_um2"] = allocated_area_um2
        evidence["allocated_bbox_area_um2"] = allocated_area_um2
        evidence["sandbox_route_area_um2"] = allocated_area_um2
        evidence["routed_core_area_um2"] = None
        row_bands = [
            {
                "row_index": row["row_index"],
                "bbox_um": [row["left_dbu"] * dbu_um, row["bottom_dbu"] * dbu_um,
                            row["right_dbu"] * dbu_um, row["top_dbu"] * dbu_um],
                "height_um": (row["top_dbu"] - row["bottom_dbu"]) * dbu_um,
                "instances": [item["instance"] for item in row["placements"]],
            }
            for row in rows
        ]
        evidence["placement"] = {
            "orientation": "R0",
            "strategy": evidence["placement_constraints"]["strategy"],
            "max_core_width_um": evidence["placement_constraints"]["max_core_width_um"],
            "gap_um": gap_dbu * dbu_um,
            "row_gap_um": evidence["placement_constraints"]["row_gap_um"],
            "row_gap_basis": evidence["placement_constraints"]["row_gap_basis"],
            "row_bands": row_bands,
            "site_qualified": False,
            "rows_qualified": False,
            "allocated_bbox_um": [0.0, 0.0, core_width * dbu_um, core_height * dbu_um],
            "allocated_bbox_area_um2": allocated_area_um2,
            "instances": [
                {"instance": item["instance"], "cell": item["cell"],
                 "row_index": item["row_index"],
                 "origin_um": [item["x_dbu"] * dbu_um, item["y_dbu"] * dbu_um],
                 "bbox_um": [item["x_dbu"] * dbu_um, item["y_dbu"] * dbu_um,
                             (item["x_dbu"] + item["width_dbu"]) * dbu_um,
                             (item["y_dbu"] + item["height_dbu"]) * dbu_um]}
                for item in placements
            ],
        }

        stage = "build_flat_layout"
        layout = db.Layout()
        layout.dbu = dbu_um
        output_layers = {layer: layout.layer(db.LayerInfo(*layer))
                         for layer in sorted(NATIVE_LAYERS)}
        output_cell = layout.create_cell(output_top)
        output_cell.shapes(output_layers[BOUNDARY_LAYER]).insert(db.Box(0, 0, core_width, core_height))
        ml1_obstacles = []
        copied_shape_counts: dict[str, int] = defaultdict(int)
        pin_access_rows = []
        anchors_by_net: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for placed in placements:
            cell = cells[placed["cell"]]
            transform = db.Trans(db.Trans.R0, db.Point(placed["x_dbu"], placed["y_dbu"]))
            for layer, boxes in cell["boxes_by_layer"].items():
                for box_index, box in enumerate(boxes):
                    transformed = box.transformed(transform)
                    if (transformed.left < 0 or transformed.bottom < 0 or
                            transformed.right > core_width or transformed.top > core_height):
                        raise CoreRouterError(
                            f"{placed['instance']} source geometry exceeds actual core bounds")
                    output_cell.shapes(output_layers[layer]).insert(transformed)
                    copied_shape_counts[f"{layer[0]}/{layer[1]}"] += 1
                    if layer == ML1_LAYER:
                        owner = placed["box_owners"][box_index]
                        bounds = (transformed.left, transformed.bottom,
                                  transformed.right, transformed.top)
                        ml1_obstacles.append((bounds, owner))
            for pin in cell["spec"]["pins"]:
                anchor = placed["pin_anchors"][pin]
                candidate_accesses = [
                    {**candidate,
                     "point_dbu": [placed["x_dbu"] + candidate["point_dbu"][0],
                                   placed["y_dbu"] + candidate["point_dbu"][1]]}
                    for candidate in anchor["candidate_accesses"]
                ]
                point = tuple(candidate_accesses[0]["point_dbu"])
                label_point = transform * cell["labels"][pin].disp
                net = placed["connections"][pin]
                access = {
                    "instance": placed["instance"], "cell": placed["cell"], "pin": pin,
                    "net": net, "layer": "ML1", "point_dbu": list(point),
                    "point_um": [point[0] * dbu_um, point[1] * dbu_um],
                    "source_text_point_um": [label_point.x * dbu_um, label_point.y * dbu_um],
                    "selected_window_index": candidate_accesses[0]["window_index"],
                    "selected_window_um": _bbox_um(
                        cell["pin_windows"][pin][candidate_accesses[0]["window_index"]]["box"].transformed(transform),
                        dbu_um),
                    "electrically_equivalent_window_indices": [
                        window["index"] for window in cell["pin_windows"][pin]
                        if window["equivalent_to_label"]],
                    "candidate_access_count": len(candidate_accesses),
                    "all_source_windows_fully_on_real_ml1": True,
                    "source_ml1_component_id": anchor["component_id"],
                    "source_electrical_component_id": cell["pin_components"][pin],
                    "label_used_as_connectivity_evidence": False,
                }
                pin_access_rows.append(access)
                anchors_by_net[net].append({
                    "kind": "cell_pin",
                    "instance": placed["instance"],
                    "pin": pin,
                    "row_index": placed["row_index"],
                    "point_dbu": list(point),
                    "candidate_accesses": candidate_accesses,
                })

        native_supply_ml1 = {net: db.Region() for net in ("VDD", "VSS")}
        for bounds, owner in ml1_obstacles:
            if owner in native_supply_ml1:
                native_supply_ml1[owner].insert(db.Box(*bounds))
        for net, bounds in power_plan["boxes"]:
            output_cell.shapes(output_layers[ML1_LAYER]).insert(db.Box(*bounds))
            ml1_obstacles.append((bounds, net))
        for route_anchor in power_plan["routing_anchors"]:
            net = route_anchor["net"]
            bounds = tuple(route_anchor["box_dbu"])
            pdn_anchor = {
                "kind": "pdn_geometry",
                "point_dbu": [((bounds[0] + bounds[2]) // 2) // pitch_dbu * pitch_dbu,
                              ((bounds[1] + bounds[3]) // 2) // pitch_dbu * pitch_dbu],
                "box_dbu": list(bounds),
                "trunk_instances": route_anchor["trunk_instances"],
            }
            anchors_by_net[net].append(pdn_anchor)
        for name, point in port_anchors.items():
            candidates = [list(point)]
            if name not in ("VDD", "VSS"):
                half = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "port wire half-width")
                direction = design["ports"][name].lower()
                sides = ([half] if direction == "input" else
                         [core_width - half] if direction == "output" else
                         [half, core_width - half])
                candidates = [[x, y] for x in sides
                              for y in range(half, core_height - half + 1, pitch_dbu)]
            anchors_by_net[name].append({
                "kind": "top_level_net_endpoint",
                "point_dbu": list(point),
                "candidate_accesses": [{"point_dbu": candidate} for candidate in candidates],
            })
        for net in ("VDD", "VSS"):
            anchors_by_net[net].sort(
                key=lambda anchor: (
                    0 if anchor["kind"] == "top_level_net_endpoint" else
                    1 if anchor["kind"] == "pdn_geometry" else 2))

        grid = _ObstacleGrid(core_width, core_height, dbu_um, pitch_dbu)
        for bounds, owner in ml1_obstacles:
            grid.add_box(bounds, owner)
        grid.sort_rows()

        stage = "route_ml1_nets"
        ordered_nets = sorted(
            anchors_by_net,
            key=lambda net: (0 if net == "VSS" else 1 if net == "VDD" else 2,
                             -len(anchors_by_net[net]), net))
        route_records = {}
        route_edges: dict[str, set[tuple[tuple[int, int], tuple[int, int]]]] = {}
        routed_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
        route_failures = []
        half_wire_dbu = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "ML1 route half-width")
        for net in ordered_nets:
            if net in ("VDD", "VSS"):
                route = _connect_power_net(
                    grid, anchors_by_net[net], net, pitch_dbu, half_wire_dbu)
            else:
                route = _connect_net(grid, anchors_by_net[net], net)
            route_records[net] = route
            if not route["passed"]:
                route_failures.append({"net": net, "stage": "route_ml1_nets",
                                       "error": route["error"],
                                       "failed_terminals": route.get("failed_terminals", [])})
            for anchor, selected_access in zip(
                    anchors_by_net[net], route.get("terminal_accesses", [])):
                if selected_access is not None:
                    anchor["point_dbu"] = list(selected_access["point_dbu"])
                    anchor["selected_access"] = selected_access
                    if anchor["kind"] == "top_level_net_endpoint":
                        port_anchors[net] = tuple(selected_access["point_dbu"])
            edges = set()
            boxes = set()
            for path_record in route["paths"]:
                path = [tuple(point) for point in path_record["points_dbu"]]
                edges.update(_path_edges(path))
                for first, second in zip(path, path[1:]):
                    if first != second:
                        boxes.add(_wire_box(first, second, half_wire_dbu))
            route_edges[net] = edges
            for bounds in sorted(boxes):
                if (bounds[0] < 0 or bounds[1] < 0 or
                        bounds[2] > core_width or bounds[3] > core_height):
                    raise CoreRouterError(
                        f"{net} router-generated ML1 shape {bounds} exceeds actual core bounds "
                        f"{core_bounds}")
                output_cell.shapes(output_layers[ML1_LAYER]).insert(db.Box(*bounds))
                routed_boxes.append((net, bounds))
                if net not in ("VDD", "VSS"):
                    grid.add_box(bounds, net)
            grid.sort_rows()

        added_supply_ml1 = {net: db.Region() for net in ("VDD", "VSS")}
        for net, bounds in [*power_plan["boxes"], *routed_boxes]:
            if net in added_supply_ml1:
                added_supply_ml1[net].insert(db.Box(*bounds))
        supply_area = {
            net: float((region.merged() - native_supply_ml1[net]).area()) * dbu_um * dbu_um
            for net, region in added_supply_ml1.items()}
        evidence["power_topology_plan"]["added_ml1_area_um2_by_net"] = supply_area
        evidence["power_topology_plan"]["added_ml1_area_um2"] = sum(supply_area.values())
        evidence["power_topology_plan"]["cost_scope"] = (
            "actual union of added supply ML1 minus pre-existing supply ML1; not IR-drop qualification")

        for net in ordered_nets:
            route = route_records[net]
            route["wire_length_um"] = _centerline_length_dbu(route_edges.get(net, set())) * dbu_um
            for path in route["paths"]:
                points = [tuple(point) for point in path["points_dbu"]]
                length = sum(_manhattan(first, second) for first, second in zip(points, points[1:]))
                path["wire_length_um"] = length * dbu_um
                path["detour_um"] = (length - _manhattan(points[0], points[-1])) * dbu_um
            route["wire_layer"] = "ML1"
            route["terminal_count"] = len(anchors_by_net[net])
        evidence["net_routes"] = route_records
        evidence["overflow_count"] = len(route_failures)
        evidence["failure_reasons"].extend(route_failures)

        for access in pin_access_rows:
            anchor = next(row for row in anchors_by_net[access["net"]]
                          if row["kind"] == "cell_pin" and
                          row["instance"] == access["instance"] and row["pin"] == access["pin"])
            candidates = anchor["candidate_accesses"]
            blocked = sum(grid.blocked((row["point_dbu"][0] // pitch_dbu,
                                        row["point_dbu"][1] // pitch_dbu), access["net"])
                          for row in candidates)
            access["blocked_access_candidate_count"] = blocked
            access["legal_access_candidate_count"] = len(candidates) - blocked
            access["landing_width_um"] = WIRE_WIDTH_UM
            selected = anchor.get("selected_access")
            if selected is None:
                access["selected_anchor_on_real_ml1"] = False
                continue
            access["point_dbu"] = list(anchor["point_dbu"])
            access["point_um"] = [coordinate * dbu_um for coordinate in anchor["point_dbu"]]
            access["selected_window_index"] = selected.get("window_index")
            access["selected_component_id"] = selected.get("component_id")
            placed = next(item for item in placements if item["instance"] == access["instance"])
            cell = cells[placed["cell"]]
            transform = db.Trans(db.Trans.R0, db.Point(placed["x_dbu"], placed["y_dbu"]))
            selected_window = cell["pin_windows"][access["pin"]][
                access["selected_window_index"]]["box"].transformed(transform)
            access["selected_window_um"] = _bbox_um(selected_window, dbu_um)

        stage = "prove_physical_connectivity"
        connectivity = _prove_connectivity(output_cell, output_layers, anchors_by_net)
        evidence["connectivity"] = connectivity
        for access in pin_access_rows:
            net_result = connectivity["nets"][access["net"]]
            access["selected_anchor_on_real_ml1"] = any(
                anchor["kind"] == "cell_pin" and
                anchor.get("instance") == access["instance"] and
                anchor.get("pin") == access["pin"] and anchor["on_real_ml1"]
                for anchor in net_result["anchors"])
            access["routed_to_net_component"] = bool(net_result["connected"])
        component_nets = connectivity["nets"]
        proven_nets = {net for net, row in component_nets.items()
                       if row["connected"] and route_records[net]["passed"]}
        if connectivity["shorts"]:
            evidence["failure_reasons"].extend(
                {"stage": "prove_physical_connectivity",
                 "error": "distinct nets share one actual ML1/TiN/CNT electrical component",
                 **short} for short in connectivity["shorts"])
        for net, row in component_nets.items():
            if not row["connected"] and route_records[net]["passed"]:
                evidence["failure_reasons"].append(
                    {"stage": "prove_physical_connectivity", "net": net,
                     "error": "all actual-conductor terminal components could not be proven connected"})

        stage = "write_gds"
        text_labels = []
        for placed in placements:
            cell = cells[placed["cell"]]
            transform = db.Trans(db.Trans.R0, db.Point(placed["x_dbu"], placed["y_dbu"]))
            for pin in cell["spec"]["pins"]:
                net = placed["connections"][pin]
                if net in proven_nets:
                    continue
                label_name = f"UNROUTED_{_safe_slug(placed['instance'])}_{pin}_{hashlib.sha256((placed['instance'] + '/' + pin).encode()).hexdigest()[:8].upper()}"
                label_trans = transform * cell["labels"][pin]
                output_cell.shapes(output_layers[TEXT_LAYER]).insert(db.Text(label_name, label_trans))
                text_labels.append({"instance": placed["instance"], "pin": pin,
                                    "net": net, "text": label_name,
                                    "scoped_to_instance": net not in proven_nets})
        for name, point in port_anchors.items():
            if name in proven_nets:
                output_cell.shapes(output_layers[TEXT_LAYER]).insert(
                    db.Text(name, db.Trans(db.Trans.R0, db.Point(*point)))
                )
        gds_path = Path(evidence["artifacts"]["gds"])
        layout.write(str(gds_path))
        evidence["artifacts"]["gds_sha256"] = _sha256(gds_path)
        evidence["native_text_labels"] = {
            "labels": text_labels,
            "top_level_ports_emitted": sorted(proven_nets & set(port_names)),
            "text_layer": list(TEXT_LAYER),
            "labels_used_for_connectivity_proof": False,
            "failed_or_unproven_pin_labels_are_instance_scoped": True,
        }
        evidence["native_shape_counts"] = {
            "copied_cell_geometry_by_layer": dict(sorted(copied_shape_counts.items())),
            "added_ml1_route_rectangles": len(routed_boxes),
            "added_ml1_power_topology_shapes": power_plan["physical_shape_count"],
            "accepted_source_layers_by_cell": {
                name: [list(layer) for layer in cells[name]["observed_layers"]]
                for name in used_cells
            },
            "native_text_labels": len(text_labels) + len(proven_nets & set(port_names)),
            "top_boundary_rectangles": 1,
        }

        stage = "verify_native_layers"
        populated_layers = set()
        final_layout = db.Layout()
        final_layout.read(str(gds_path))
        final_top = final_layout.cell(output_top)
        if final_top is None:
            raise CoreRouterError("written GDS does not contain its selected flat top cell")
        for layer_index in final_layout.layer_indices():
            info = final_layout.get_info(layer_index)
            if list(final_top.shapes(layer_index).each()):
                populated_layers.add((int(info.layer), int(info.datatype)))
        native_layers_only = populated_layers <= NATIVE_LAYERS
        evidence["native_layers_used"] = [list(layer) for layer in sorted(populated_layers)]
        evidence["native_layers_only"] = bool(native_layers_only)
        if not native_layers_only:
            evidence["failure_reasons"].append({
                "stage": "verify_native_layers",
                "error": f"written GDS uses non-permitted layers {sorted(populated_layers - NATIVE_LAYERS)}"})

        stage = "verify_contract_preflight"
        try:
            evidence["verification"]["preflight_geometry"] = verify._preflight(gds_path, output_top)
            evidence["verification"]["verify_contract_preflight"] = True
        except (RuntimeError, ValueError, OSError) as error:
            evidence["failure_reasons"].append({"stage": stage, "error": str(error)})
            evidence["verification"]["preflight_error"] = str(error)

        stage = "finalize_route_evidence"
        proof = connectivity["passed"] and not route_failures and native_layers_only
        proof = proof and evidence["verification"]["verify_contract_preflight"]
        if not proof:
            if not route_failures:
                evidence["overflow_count"] += max(1, len(evidence["failure_reasons"]))
            evidence["failure"] = {
                "stage": stage,
                "error": "one or more routes or physical-conductor proofs failed",
                "failed_nets": sorted({row["net"] for row in route_failures} |
                                      {net for net, result in connectivity["nets"].items()
                                       if not result["connected"]}),
            }
            if not evidence["failure_reasons"]:
                evidence["failure_reasons"].append(evidence["failure"])
        evidence["route_proof_passed"] = bool(proof)
        evidence["failed"] = not bool(proof)
        evidence["rankable"] = False
        evidence["status"] = "route_proof_passed" if proof else "failed"
        evidence["route_geometry"] = {
            "grid_pitch_um": PITCH_UM,
            "ml1_wire_width_um": WIRE_WIDTH_UM,
            "minimum_ml1_spacing_um": ML1_SPACE_UM,
            "centerline_keepout_um": ROUTE_CLEARANCE_UM,
            "all_routes_manhattan": True,
            "upper_metal_layers_used": [],
            "synthetic_layers_used": [],
            "native_cnt_tin_ml1_contacts_added": 0,
        }
        evidence["pin_access"] = {
            "passed": bool(pin_access_rows) and all(
                row["all_source_windows_fully_on_real_ml1"] and
                row["selected_anchor_on_real_ml1"] for row in pin_access_rows),
            "routed_to_net_components_passed": bool(pin_access_rows) and all(
                row["routed_to_net_component"] for row in pin_access_rows),
            "source": "accepted manifest ML1 access windows and real accepted GDS ML1 geometry",
            "text_labels_used_for_connectivity": False,
            "pins": pin_access_rows,
        }
        evidence["crossing_demand"] = _crossing_report(anchors_by_net, route_edges)
        net_lengths = {net: float(route_records[net]["wire_length_um"])
                       for net in ordered_nets}
        evidence["wire_lengths"] = {
            "units": "um",
            "by_layer": {"ML1": float(sum(net_lengths.values()))},
            "by_net": net_lengths,
            "measurement": "unique routed ML1 centerline edges; source-cell metal excluded",
        }
        if routed_boxes:
            route_box = [min(box[0] for _net, box in routed_boxes),
                         min(box[1] for _net, box in routed_boxes),
                         max(box[2] for _net, box in routed_boxes),
                         max(box[3] for _net, box in routed_boxes)]
            evidence["route_bbox"] = {
                "bbox_um": [coordinate * dbu_um for coordinate in route_box],
                "bounds_derived_from": "router-generated ML1 wire rectangles only",
                "layer": "ML1",
            }
        else:
            evidence["route_bbox"] = None
        evidence["area_metrics"] = {
            "cell_geometry_area_um2": evidence["cell_geometry_area_um2"],
            "placement_bbox_area_um2": evidence["placement_bbox"]["placement_bbox_area_um2"],
            "allocated_core_area_um2": evidence["allocated_core_area_um2"],
            "allocated_bbox_area_um2": evidence["allocated_bbox_area_um2"],
            "sandbox_route_area_um2": evidence["sandbox_route_area_um2"],
            "routed_core_area_um2": None,
            "routed_core_area_domain": (
                "reserved for strict physical/electrical acceptance plus integrated PDN; "
                "this route sandbox does not establish it"),
            "core_bbox_um": [0.0, 0.0, core_width * dbu_um, core_height * dbu_um],
        }
        evidence["provenance_hashes"]["accepted_gds_sha256_by_cell"] = {
            name: cells[name]["gds_sha256"] for name in used_cells}
        contract_hashes = {}
        for module in (library, mapping, logic, verify):
            module_path = Path(module.__file__).resolve()
            contract_hashes[module_path.name] = _sha256(module_path)
        contract_hashes[Path(__file__).name] = _sha256(Path(__file__).resolve())
        evidence["provenance_hashes"]["contract_files_sha256"] = contract_hashes
        deck_hashes = {}
        for key, path in (("drc", verify.DRC), ("lvs", verify.LVS), ("pex", verify.PEX)):
            if Path(path).is_file():
                deck_hashes[key] = _sha256(Path(path))
        evidence["provenance_hashes"]["verification_decks_sha256"] = deck_hashes
        evidence["artifacts"]["reference_spice_sha256"] = _sha256(Path(evidence["artifacts"]["reference_spice"]))
        evidence["artifacts"]["mapped_netlist_sha256"] = _sha256(Path(evidence["artifacts"]["mapped_netlist"]))
        evidence["qualification_limits"]["placement"] = (
            "deterministic R0 placement; no qualified site, row, pitch, or placement flow")
        _persist_evidence(evidence_path, evidence)
        return evidence
    except Exception as error:
        evidence["status"] = "failed"
        evidence["failed"] = True
        evidence["rankable"] = False
        evidence["route_proof_passed"] = False
        evidence["failure"] = {"stage": stage, "error": str(error)}
        evidence["failure_reasons"].append({"stage": stage, "error": str(error)})
        if evidence.get("overflow_count", 0) == 0:
            evidence["overflow_count"] = 1
        try:
            _persist_evidence(evidence_path, evidence)
        except OSError:
            pass
        return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", help="canonical mapped netlist JSON; omit to build the tiny accepted-cell benchmark")
    parser.add_argument("--out", required=True, help="output directory for flat GDS, reference SPICE, and route JSON")
    parser.add_argument("--manifest", help="accepted physical-cell manifest override")
    parser.add_argument("--top-name", help="optional safe top-cell/SPICE subcircuit name")
    parser.add_argument("--power-topology", choices=("edge_rails", "segmented_rails", "local_clusters"),
                        default="edge_rails",
                        help="physical ML1 supply layout to compare in the sandbox")
    args = parser.parse_args(argv)
    evidence = route_core(args.netlist, args.out, manifest_path=args.manifest,
                          top_name=args.top_name, power_topology=args.power_topology)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence.get("route_proof_passed") else 2


if __name__ == "__main__":  # pragma: no cover - command-line entry point
    raise SystemExit(main())
