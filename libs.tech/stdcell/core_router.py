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
    ml1_components = [db.Region(poly) for poly in ml1_region.merged().each()]
    if not ml1_components:
        raise CoreRouterError(f"{cell_name} has no connected ML1 components")

    pin_components: dict[str, int] = {}
    pin_windows: dict[str, list[Any]] = {}
    for pin in physical_spec["pins"]:
        label_point = labels[pin].disp
        label_components = _component_ids_at(ml1_components, label_point)
        if len(label_components) != 1:
            raise CoreRouterError(
                f"{cell_name}/{pin} text label is not on exactly one real ML1 component")
        pin_components[pin] = label_components[0]
        windows = []
        raw_windows = record["pins"][pin]["windows_um"]
        for window_index, values in enumerate(raw_windows):
            ticks = tuple(_um_to_dbu(value, dbu_um, f"{cell_name}/{pin} access window")
                          for value in values)
            window = db.Box(*ticks)
            if (db.Region(window) - ml1_region).is_empty():
                windows.append({"index": window_index, "box": window, "fully_on_ml1": True})
            else:
                windows.append({"index": window_index, "box": window, "fully_on_ml1": False})
        if not windows or any(not window["fully_on_ml1"] for window in windows):
            raise CoreRouterError(f"{cell_name}/{pin} access window is not fully covered by real ML1")
        if not any(window["box"].left <= label_point.x <= window["box"].right and
                   window["box"].bottom <= label_point.y <= window["box"].top
                   for window in windows):
            raise CoreRouterError(f"{cell_name}/{pin} text label is outside every accepted ML1 access window")
        if pin in ("VDD", "VSS"):
            for window in windows:
                window_region = db.Region(window["box"])
                component_ids = [
                    index for index, component in enumerate(ml1_components)
                    if not (window_region & component).is_empty()
                ]
                if component_ids != [label_components[0]]:
                    raise CoreRouterError(
                        f"{cell_name}/{pin} access windows do not share one local real-ML1 supply component")
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
        "ml1_box_component_ids": box_component_ids,
        "labels": labels,
        "pin_components": pin_components,
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
    owners: dict[int, str] = {}
    for component_id, nets in component_nets.items():
        if len(nets) != 1:
            raise CoreRouterError(
                f"{instance}/{cell['name']} has distinct mapped nets on one real ML1 component: {sorted(nets)}")
        owners[component_id] = next(iter(nets))
    box_owners: list[str | None] = []
    for component_ids in cell["ml1_box_component_ids"]:
        known_owners = {owners[index] for index in component_ids if index in owners}
        if len(known_owners) > 1:
            raise CoreRouterError(f"{instance}/{cell['name']} ML1 geometry joins different canonical nets")
        box_owners.append(next(iter(known_owners)) if known_owners else None)
    return owners, box_owners


def _choose_pin_access(cell: dict[str, Any], pin: str, pitch_dbu: int) -> tuple[int, int, int]:
    label_point = cell["labels"][pin].disp
    component_id = cell["pin_components"][pin]
    best: tuple[int, int, int, int] | None = None
    for window in cell["pin_windows"][pin]:
        box = window["box"]
        x_start = _ceil_multiple(box.left, pitch_dbu)
        y_start = _ceil_multiple(box.bottom, pitch_dbu)
        x_end = (box.right // pitch_dbu) * pitch_dbu
        y_end = (box.top // pitch_dbu) * pitch_dbu
        if x_start > x_end or y_start > y_end:
            continue
        for x in range(x_start, x_end + 1, pitch_dbu):
            for y in range(y_start, y_end + 1, pitch_dbu):
                point = db.Point(x, y)
                if component_id not in _component_ids_at(cell["ml1_components"], point):
                    continue
                candidate = (abs(x - label_point.x) + abs(y - label_point.y),
                             window["index"], x, y)
                if best is None or candidate < best:
                    best = candidate
    if best is None:
        raise CoreRouterError(
            f"{cell['name']}/{pin} has no ML1 access point on the deterministic {PITCH_UM:g}um route grid")
    return best[2], best[3], best[1]


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

    def route(self, start_dbu: tuple[int, int], goal_dbu: tuple[int, int],
              net: str) -> tuple[list[tuple[int, int]] | None, str, int]:
        step = self.pitch_dbu
        if any(value % step for value in (*start_dbu, *goal_dbu)):
            return None, "endpoint_not_on_route_grid", 0
        start = (start_dbu[0] // step, start_dbu[1] // step)
        goal = (goal_dbu[0] // step, goal_dbu[1] // step)
        if start == goal:
            return [start_dbu], "connected_at_existing_access", 0

        def in_bounds(node: tuple[int, int]) -> bool:
            return (self.min_ix <= node[0] <= self.max_ix and
                    self.min_iy <= node[1] <= self.max_iy)

        for node, label in ((start, "start"), (goal, "goal")):
            if not in_bounds(node):
                return None, f"{label}_outside_route_core", 0
            if self.blocked(node, net):
                return None, f"{label}_blocked_by_foreign_ml1", 0

        queue: list[tuple[int, int, tuple[int, int]]] = []
        serial = 0
        start_h = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
        heapq.heappush(queue, (start_h, serial, start))
        previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        costs = {start: 0}
        expanded = 0
        directions = ((1, 0), (0, 1), (-1, 0), (0, -1))
        while queue:
            _estimate, _serial, current = heapq.heappop(queue)
            if current == goal:
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
                serial += 1
                heuristic = abs(neighbor[0] - goal[0]) + abs(neighbor[1] - goal[1])
                heapq.heappush(queue, (new_cost + heuristic, serial, neighbor))
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


def _connect_net(grid: _ObstacleGrid, anchors: list[dict[str, Any]],
                 net: str) -> dict[str, Any]:
    unique: list[tuple[int, int]] = []
    for anchor in anchors:
        point = tuple(anchor["point_dbu"])
        if point not in unique:
            unique.append(point)
    if not unique:
        return {"passed": False, "error": "net has no physical terminals", "paths": []}
    connected = [unique[0]]
    remaining = unique[1:]
    paths = []
    expanded = 0
    last_failures = []
    while remaining:
        candidates = sorted((_manhattan(source, target), source, target, target)
                            for source in connected for target in remaining)
        selected = None
        for _length, source, target, target_key in candidates:
            path, reason, seen = grid.route(source, target, net)
            expanded += seen
            if path is not None:
                selected = (source, target, target_key, path)
                break
            last_failures.append({"source_dbu": list(source), "target_dbu": list(target),
                                  "reason": reason, "expanded_nodes": seen})
        if selected is None:
            return {"passed": False,
                    "error": "no legal ML1 connection could be proven for every terminal",
                    "paths": [], "attempted_paths": paths, "expanded_nodes": expanded,
                    "failed_pairs": last_failures[-32:]}
        source, target, target_key, path = selected
        paths.append({"from_dbu": list(source), "to_dbu": list(target),
                      "target_point_dbu": list(target_key), "points_dbu": [list(p) for p in path]})
        connected.append(target)
        remaining.remove(target_key)
    return {"passed": True, "paths": paths, "expanded_nodes": expanded}


def _segments_cross(first: tuple[tuple[int, int], tuple[int, int]],
                    second: tuple[tuple[int, int], tuple[int, int]]) -> tuple[int, int] | None:
    (x1, y1), (x2, y2) = first
    (x3, y3), (x4, y4) = second
    first_horizontal = y1 == y2
    second_horizontal = y3 == y4
    if first_horizontal == second_horizontal:
        return None
    if first_horizontal:
        horizontal, vertical = first, second
    else:
        horizontal, vertical = second, first
    (hx1, hy), (hx2, _hy2) = horizontal
    (vx, vy1), (_vx2, vy2) = vertical
    if min(hx1, hx2) <= vx <= max(hx1, hx2) and min(vy1, vy2) <= hy <= max(vy1, vy2):
        return vx, hy
    return None


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


def _prove_connectivity(top: Any, ml1_layer_index: int,
                        anchors_by_net: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    metal = db.Region()
    for shape in top.shapes(ml1_layer_index).each():
        if shape.is_box():
            metal.insert(shape.box)
    components = [db.Region(poly) for poly in metal.merged().each()]
    component_to_nets: dict[int, set[str]] = defaultdict(set)
    nets = {}
    for net, anchors in sorted(anchors_by_net.items()):
        anchor_rows = []
        ids = []
        for anchor in anchors:
            point = db.Point(*anchor["point_dbu"])
            point_ids = _component_ids_at(components, point)
            anchor_rows.append({"kind": anchor["kind"], "instance": anchor.get("instance"),
                                "pin": anchor.get("pin"), "point_dbu": list(anchor["point_dbu"]),
                                "component_ids": point_ids, "on_real_ml1": bool(point_ids)})
            ids.extend(point_ids)
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
    return {
        "passed": bool(nets) and all(row["connected"] for row in nets.values()) and not shorts,
        "method": "merged connected components of actual ML1 shapes; labels are excluded",
        "text_labels_used_for_connectivity": False,
        "component_count": len(components),
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
        "rankable_scope": "native_ml1_route_geometry_only; no process-signoff or frame-capacity claim",
        "failure": None,
        "failure_reasons": [],
        "area_domain": ("flat core-router sandbox including accepted cell placement, whitespace, "
                        "and signal/power route channels; excludes I/O, ESD, SRAM, PDN, clock, "
                        "repair, and physical-only-cell integration"),
        "cell_geometry_area_um2": None,
        "routed_core_area_um2": None,
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


def route_core(netlist: dict[str, Any] | str | Path | None,
               output_dir: str | Path, *,
               manifest_path: str | Path | None = None,
               top_name: str | None = None) -> dict[str, Any]:
    """Place and route a canonical mapped netlist, or the default tiny benchmark.

    The function always attempts to retain JSON route evidence.  A routing or
    proof failure returns ``status == 'failed'`` and ``rankable == False``; it
    never converts an incomplete candidate into a pass.
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
        cursor_x = _ceil_multiple(margin_dbu, pitch_dbu)
        placements = []
        for instance in design["instances"]:
            cell = cells[instance["cell"]]
            x = _ceil_multiple(cursor_x, pitch_dbu)
            y = base_y
            _, box_owners = _source_component_owners(
                cell, instance["connections"], instance["name"])
            pin_anchors = {}
            for pin in cell["spec"]["pins"]:
                ax, ay, window_index = _choose_pin_access(cell, pin, pitch_dbu)
                pin_anchors[pin] = {"local_dbu": [ax, ay], "window_index": window_index,
                                    "component_id": cell["pin_components"][pin]}
            placements.append({"instance": instance["name"], "cell": instance["cell"],
                               "connections": instance["connections"], "x_dbu": x, "y_dbu": y,
                               "width_dbu": cell["width_dbu"], "height_dbu": cell["height_dbu"],
                               "box_owners": box_owners, "pin_anchors": pin_anchors})
            cursor_x = x + cell["width_dbu"] + gap_dbu
        placement_left = min(item["x_dbu"] for item in placements)
        placement_bottom = min(item["y_dbu"] for item in placements)
        placement_right = max(item["x_dbu"] + item["width_dbu"] for item in placements)
        placement_top = max(item["y_dbu"] + item["height_dbu"] for item in placements)
        port_names = list(design["ports"])
        port_xs = {name: margin_dbu + (index + 1) * port_spacing_dbu
                   for index, name in enumerate(port_names)}
        track_y = _ceil_multiple(placement_top + route_channel_dbu, pitch_dbu)
        core_width = _ceil_multiple(max(placement_right + margin_dbu,
                                      margin_dbu + (len(port_names) + 1) * port_spacing_dbu + margin_dbu),
                                    pitch_dbu)
        core_height = _ceil_multiple(track_y + margin_dbu, pitch_dbu)
        evidence["placement_bbox"] = {
            "bbox_um": [placement_left * dbu_um, placement_bottom * dbu_um,
                        placement_right * dbu_um, placement_top * dbu_um],
            "placement_bbox_area_um2": ((placement_right - placement_left) *
                                         (placement_top - placement_bottom) * dbu_um * dbu_um),
            "cell_geometry_area_um2": sum(cells[item["cell"]]["area_um2"] for item in placements),
            "cell_count": len(placements),
            "site_or_row_qualification": None,
        }
        evidence["cell_geometry_area_um2"] = sum(
            cells[item["cell"]]["area_um2"] for item in placements)
        evidence["routed_core_area_um2"] = float(core_width * core_height * dbu_um * dbu_um)
        evidence["placement"] = {
            "orientation": "R0",
            "gap_um": gap_dbu * dbu_um,
            "site_qualified": False,
            "rows_qualified": False,
            "instances": [
                {"instance": item["instance"], "cell": item["cell"],
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
                    output_cell.shapes(output_layers[layer]).insert(transformed)
                    copied_shape_counts[f"{layer[0]}/{layer[1]}"] += 1
                    if layer == ML1_LAYER:
                        owner = placed["box_owners"][box_index]
                        bounds = (transformed.left, transformed.bottom,
                                  transformed.right, transformed.top)
                        ml1_obstacles.append((bounds, owner))
            for pin in cell["spec"]["pins"]:
                anchor = placed["pin_anchors"][pin]
                point = (placed["x_dbu"] + anchor["local_dbu"][0],
                         placed["y_dbu"] + anchor["local_dbu"][1])
                chosen_window = cell["pin_windows"][pin][anchor["window_index"]]["box"]
                placed_window = chosen_window.transformed(transform)
                label_point = transform * cell["labels"][pin].disp
                net = placed["connections"][pin]
                access = {
                    "instance": placed["instance"], "cell": placed["cell"], "pin": pin,
                    "net": net, "layer": "ML1", "point_dbu": list(point),
                    "point_um": [point[0] * dbu_um, point[1] * dbu_um],
                    "source_text_point_um": [label_point.x * dbu_um, label_point.y * dbu_um],
                    "selected_window_index": anchor["window_index"],
                    "selected_window_um": _bbox_um(placed_window, dbu_um),
                    "all_source_windows_fully_on_real_ml1": True,
                    "source_ml1_component_id": anchor["component_id"],
                    "label_used_as_connectivity_evidence": False,
                }
                pin_access_rows.append(access)
                anchors_by_net[net].append({"kind": "cell_pin", "instance": placed["instance"],
                                            "pin": pin, "point_dbu": list(point)})

        port_anchors = {}
        for name in port_names:
            point = (port_xs[name], track_y)
            port_anchors[name] = point
            anchors_by_net[name].append({"kind": "top_level_net_endpoint", "point_dbu": list(point)})

        grid = _ObstacleGrid(core_width, core_height, dbu_um, pitch_dbu)
        for bounds, owner in ml1_obstacles:
            grid.add_box(bounds, owner)
        grid.sort_rows()

        stage = "route_ml1_nets"
        ordered_nets = sorted(
            anchors_by_net,
            key=lambda net: (0 if net in ("VDD", "VSS") else 1,
                             -len(anchors_by_net[net]), net))
        route_records = {}
        route_edges: dict[str, set[tuple[tuple[int, int], tuple[int, int]]]] = {}
        routed_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
        route_failures = []
        half_wire_dbu = _um_to_dbu(WIRE_WIDTH_UM / 2, dbu_um, "ML1 route half-width")
        for net in ordered_nets:
            route = _connect_net(grid, anchors_by_net[net], net)
            route_records[net] = route
            if not route["passed"]:
                route_failures.append({"net": net, "stage": "route_ml1_nets",
                                       "error": route["error"],
                                       "failed_pairs": route.get("failed_pairs", [])})
                continue
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
                output_cell.shapes(output_layers[ML1_LAYER]).insert(db.Box(*bounds))
                routed_boxes.append((net, bounds))
                grid.add_box(bounds, net)
            grid.sort_rows()

        for net in ordered_nets:
            route = route_records[net]
            route["wire_length_um"] = (sum(_manhattan(*edge) for edge in route_edges.get(net, set())) *
                                       dbu_um)
            route["wire_layer"] = "ML1"
            route["terminal_count"] = len(anchors_by_net[net])
        evidence["net_routes"] = route_records
        evidence["overflow_count"] = len(route_failures)
        evidence["failure_reasons"].extend(route_failures)

        stage = "prove_ml1_connectivity"
        ml1_layer_index = output_layers[ML1_LAYER]
        connectivity = _prove_connectivity(output_cell, ml1_layer_index, anchors_by_net)
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
                {"stage": "prove_ml1_connectivity", "error": "distinct nets share one ML1 component",
                 **short} for short in connectivity["shorts"])
        for net, row in component_nets.items():
            if not row["connected"] and route_records[net]["passed"]:
                evidence["failure_reasons"].append(
                    {"stage": "prove_ml1_connectivity", "net": net,
                     "error": "all ML1 terminal components could not be proven connected"})

        stage = "write_gds"
        text_labels = []
        for placed in placements:
            cell = cells[placed["cell"]]
            transform = db.Trans(db.Trans.R0, db.Point(placed["x_dbu"], placed["y_dbu"]))
            for pin in cell["spec"]["pins"]:
                net = placed["connections"][pin]
                if net in proven_nets:
                    label_name = net
                else:
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
                "error": "one or more routes or native-geometry proofs failed",
                "failed_nets": sorted({row["net"] for row in route_failures} |
                                      {net for net, result in connectivity["nets"].items()
                                       if not result["connected"]}),
            }
            if not evidence["failure_reasons"]:
                evidence["failure_reasons"].append(evidence["failure"])
        evidence["route_proof_passed"] = bool(proof)
        evidence["failed"] = not bool(proof)
        evidence["rankable"] = bool(proof)
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
            "routed_core_area_um2": evidence["routed_core_area_um2"],
            "routed_core_area_domain": "router sandbox only; not frame capacity or floorplan feasibility",
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
    args = parser.parse_args(argv)
    evidence = route_core(args.netlist, args.out, manifest_path=args.manifest,
                          top_name=args.top_name)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence.get("rankable") else 2


if __name__ == "__main__":  # pragma: no cover - command-line entry point
    raise SystemExit(main())
