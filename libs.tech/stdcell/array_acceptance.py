#!/usr/bin/env python3
"""Build and strictly verify deterministic native-1M physical-cell arrays.

This is an experimental layout experiment, not a placement site, signoff flow,
or process qualification.  Instances are flattened from an accepted GDS and all
pin labels are scoped per instance; repeated VDD/VSS text never ties supplies.
"""
from __future__ import annotations

import argparse
import hashlib
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
    from . import library, logic, verify
except ImportError:  # direct invocation from this directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import library  # type: ignore
    import logic  # type: ignore
    import verify  # type: ignore

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))
import characterize  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = library.DEFAULT_MANIFEST
DBU_TOLERANCE = 1e-7
PLACEMENT_GAP_UM = 0.0
BOUNDARY_LAYER = (100, 0)
PHYSICAL_CHECK_LAYERS = ((3, 0), (5, 0), (7, 0), (8, 0), (18, 0), (19, 0))
SUPPLY_PINS = ("VDD", "VSS")
FORBIDDEN_LAYERS = {(9, 0), (10, 0), (11, 0), (12, 0)}


ARRAY_PATTERNS = (
    {"name": "2x2", "code": "2X2", "rows": 2, "columns": 2,
     "description": "R0 cells in row-major placement."},
    {"name": "4x4", "code": "4X4", "rows": 4, "columns": 4,
     "description": "R0 cells in row-major placement."},
    {"name": "4x4_mirrored_rows", "code": "4X4MX", "rows": 4, "columns": 4,
     "description": "Odd rows are mirrored about the horizontal cell axis (MX)."},
    {"name": "4x4_reversed_double_rows", "code": "4X4RD", "rows": 4, "columns": 4,
     "description": "Each odd row in a two-row pair is R180 and column-reversed."},
)


class ArrayAcceptanceError(RuntimeError):
    """An accepted cell cannot be used to construct an auditable array."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not result or not re.match(r"[A-Za-z_]", result):
        result = "N_" + result
    return result


def _um_to_dbu(value: float, dbu_um: float, field: str) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ArrayAcceptanceError(f"{field} must be finite")
    ticks = round(number / dbu_um)
    if abs(ticks * dbu_um - number) > DBU_TOLERANCE:
        raise ArrayAcceptanceError(f"{field} is not representable at source DBU {dbu_um:g} um")
    return int(ticks)


def _bbox_um(box: Any, dbu_um: float) -> list[float]:
    return [float(value * dbu_um) for value in
            (box.left, box.bottom, box.right, box.top)]


def _transformation(orientation: str, x: int, y: int,
                    width: int, height: int) -> Any:
    if orientation == "R0":
        return db.Trans(db.Trans.R0, db.Point(x, y))
    if orientation == "MX":
        return db.Trans(db.Trans.M0, db.Point(x, y + height))
    if orientation == "R180":
        return db.Trans(db.Trans.R180, db.Point(x + width, y + height))
    raise ArrayAcceptanceError(f"unsupported deterministic array orientation {orientation!r}")


def _is_touching(a: Any, b: Any) -> bool:
    return (a.left <= b.right and b.left <= a.right and
            a.bottom <= b.top and b.bottom <= a.top)


def _load_accepted_cell(cell_name: str, manifest_path: Path | None) -> dict[str, Any]:
    selected_manifest = Path(manifest_path).resolve() if manifest_path else DEFAULT_MANIFEST.resolve()
    manifest = library.load_library(selected_manifest, check=True)
    records = [record for record in manifest["cells"]
               if record.get("cell") == cell_name and
               record.get("status") == "strict_physical_pass"]
    if len(records) != 1:
        raise ArrayAcceptanceError(
            f"{cell_name} is not a unique strict_physical_pass cell in the accepted manifest")
    record = records[0]
    library_root = (selected_manifest.parent.parent if selected_manifest.parent.name == "doc"
                    else selected_manifest.parent).resolve()
    gds_path = (library_root / record["views"]["gds"]).resolve()
    try:
        gds_path.relative_to(library_root)
    except ValueError as error:
        raise ArrayAcceptanceError("accepted GDS path escapes the manifest library") from error
    if not gds_path.is_file():
        raise ArrayAcceptanceError(f"accepted GDS is missing: {gds_path}")
    return {"manifest_path": selected_manifest,
            "manifest_sha256": _sha256(selected_manifest), "record": record,
            "gds_path": gds_path, "gds_sha256": _sha256(gds_path)}


def _source_geometry(source_layout: Any, source_cell: Any,
                     record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_layers: dict[tuple[int, int], int] = {}
    layer_shapes: list[dict[str, Any]] = []
    for layer_index in source_layout.layer_indices():
        info = source_layout.get_info(layer_index)
        key = (info.layer, info.datatype)
        source_layers[key] = layer_index
        shapes = list(source_cell.shapes(layer_index).each())
        if not shapes:
            continue
        if key not in verify.ALLOWED_LAYERS:
            raise ArrayAcceptanceError(
                f"accepted GDS has non-native layer {info.layer}/{info.datatype}")
        if key in FORBIDDEN_LAYERS:
            raise ArrayAcceptanceError(
                f"accepted GDS contains forbidden upper-metal layer {info.layer}/{info.datatype}")
        for shape in shapes:
            if key == (13, 0):
                if not shape.is_text():
                    raise ArrayAcceptanceError("pin label layer 13/0 contains non-text geometry")
                text = shape.text
                layer_shapes.append({"kind": "text", "layer": key,
                                     "name": text.string, "trans": text.trans})
            elif key == BOUNDARY_LAYER:
                if not shape.is_box():
                    raise ArrayAcceptanceError("accepted 100/0 boundary is not rectangular")
            else:
                if not shape.is_box():
                    raise ArrayAcceptanceError(
                        f"accepted physical geometry on {info.layer}/{info.datatype} is not rectangular")
                layer_shapes.append({"kind": "box", "layer": key, "box": shape.box})

    boundary_index = source_layers.get(BOUNDARY_LAYER)
    if boundary_index is None:
        raise ArrayAcceptanceError("accepted GDS has no 100/0 core boundary")
    boundaries = [shape.box for shape in source_cell.shapes(boundary_index).each()
                  if shape.is_box()]
    if len(boundaries) != 1:
        raise ArrayAcceptanceError("accepted GDS must have one rectangular 100/0 boundary")
    source_box = boundaries[0]
    dbu_um = float(source_layout.dbu)
    if dbu_um <= 0 or not math.isfinite(dbu_um):
        raise ArrayAcceptanceError("accepted GDS has an invalid DBU")
    width_um = float(record["cell_width_um"])
    height_um = float(record["cell_height_um"])
    width_dbu = _um_to_dbu(width_um, dbu_um, "cell width")
    height_dbu = _um_to_dbu(height_um, dbu_um, "cell height")
    if ((source_box.left, source_box.bottom, source_box.right, source_box.top) !=
            (0, 0, width_dbu, height_dbu)):
        raise ArrayAcceptanceError("accepted GDS boundary disagrees with manifest dimensions")
    expected_pins = list(record["physical_spec"]["pins"])
    labels: dict[str, Any] = {}
    for item in layer_shapes:
        if item["kind"] != "text":
            continue
        name = item["name"]
        if name not in expected_pins:
            raise ArrayAcceptanceError(f"accepted GDS has unexpected text/pin identity {name!r}")
        if name in labels:
            raise ArrayAcceptanceError(f"accepted GDS repeats pin text identity {name!r}")
        labels[name] = item["trans"]
    if set(labels) != set(expected_pins):
        missing = sorted(set(expected_pins) - set(labels))
        raise ArrayAcceptanceError(f"accepted GDS is missing pin text identities: {missing}")
    if list(source_cell.each_inst()):
        raise ArrayAcceptanceError("accepted cell GDS must be a single flat cell before array assembly")
    required = {(3, 0), (5, 0), (7, 0), (8, 0)}
    populated = {item["layer"] for item in layer_shapes if item["kind"] == "box"}
    if not required <= populated:
        raise ArrayAcceptanceError(
            f"accepted GDS lacks native active/contact/conductor layers: {sorted(required - populated)}")
    return {"labels": labels, "width_dbu": width_dbu,
            "height_dbu": height_dbu, "dbu_um": dbu_um,
            "width_um": width_um, "height_um": height_um,
            "source_box": source_box}, layer_shapes


def _pattern_transforms(pattern: dict[str, Any], width: int, height: int,
                         gap: int) -> list[dict[str, Any]]:
    rows, columns = pattern["rows"], pattern["columns"]
    placements = []
    for row in range(rows):
        for logical_column in range(columns):
            orientation = "R0"
            physical_column = logical_column
            if pattern["name"] == "4x4_mirrored_rows" and row % 2:
                orientation = "MX"
            elif pattern["name"] == "4x4_reversed_double_rows" and row % 2:
                orientation = "R180"
                physical_column = columns - 1 - logical_column
            x = physical_column * (width + gap)
            y = row * (height + gap)
            placements.append({"instance": f"I_R{row:02d}_C{logical_column:02d}",
                               "logical_row": row, "logical_column": logical_column,
                               "physical_row": row, "physical_column": physical_column,
                               "orientation": orientation, "x_dbu": x, "y_dbu": y,
                               "translation_um": [x, y]})
    return placements


def _make_array(source_layout: Any, source_cell: Any, geometry: dict[str, Any],
                layer_shapes: list[dict[str, Any]], record: dict[str, Any],
                pattern: dict[str, Any], top_name: str,
                gap_um: float) -> tuple[Any, Any, list[dict[str, Any]], dict[str, Any]]:
    dbu_um = geometry["dbu_um"]
    width, height = geometry["width_dbu"], geometry["height_dbu"]
    gap = _um_to_dbu(gap_um, dbu_um, "placement gap")
    if gap < 0:
        raise ArrayAcceptanceError("placement gap must be nonnegative")
    rows, columns = pattern["rows"], pattern["columns"]
    core_width = columns * width + (columns - 1) * gap
    core_height = rows * height + (rows - 1) * gap

    layout = db.Layout()
    layout.dbu = dbu_um
    output_layers: dict[tuple[int, int], int] = {}
    for layer_index in source_layout.layer_indices():
        info = source_layout.get_info(layer_index)
        output_layers[(info.layer, info.datatype)] = layout.layer(info)
    top = layout.create_cell(top_name)
    placements = _pattern_transforms(pattern, width, height, gap)
    pin_windows: dict[str, dict[str, list[Any]]] = {}
    pin_labels: dict[str, dict[str, Any]] = {}
    physical_boxes: dict[str, dict[tuple[int, int], list[Any]]] = {}
    spec = record["physical_spec"]

    for placement in placements:
        instance = placement["instance"]
        x, y = placement["x_dbu"], placement["y_dbu"]
        transform = _transformation(placement["orientation"], x, y, width, height)
        physical_boxes[instance] = {layer: [] for layer in PHYSICAL_CHECK_LAYERS}
        for item in layer_shapes:
            layer = item["layer"]
            output_layer = output_layers[layer]
            if item["kind"] == "box":
                transformed_box = item["box"].transformed(transform)
                top.shapes(output_layer).insert(transformed_box)
                if layer in physical_boxes[instance]:
                    physical_boxes[instance][layer].append(transformed_box)
            else:
                source_pin = item["name"]
                scoped_name = _safe_name(f"N_{instance}_{source_pin}").upper()
                transformed_text = transform * item["trans"]
                top.shapes(output_layer).insert(db.Text(scoped_name, transformed_text))
                pin_labels.setdefault(instance, {})[source_pin] = {
                    "name": scoped_name, "point": transformed_text.disp,
                    "source_pin": source_pin}

        instance_windows: dict[str, list[Any]] = {}
        for pin in spec["pins"]:
            windows = record["pins"][pin]["windows_um"]
            transformed_windows = []
            for index, values in enumerate(windows):
                source_window = db.Box(*(_um_to_dbu(value, dbu_um,
                                                     f"{record['cell']}/{pin} window")
                                         for value in values))
                transformed_windows.append(source_window.transformed(transform))
            instance_windows[pin] = transformed_windows
        pin_windows[instance] = instance_windows
        placement["translation_um"] = [x * dbu_um, y * dbu_um]
        placement["transform_dbu"] = [transform.disp.x, transform.disp.y]
        placement["transform_um"] = [transform.disp.x * dbu_um,
                                     transform.disp.y * dbu_um]
        placement["bbox_um"] = _bbox_um(
            geometry["source_box"].transformed(transform), dbu_um)
        placement["cell_width_um"] = geometry["width_um"]
        placement["cell_height_um"] = geometry["height_um"]
        placement["text_pin_identity"] = {
            pin: pin_labels[instance][pin]["name"] for pin in spec["pins"]}

    boundary_layer = output_layers.get(BOUNDARY_LAYER)
    if boundary_layer is None:
        boundary_layer = layout.layer(db.LayerInfo(*BOUNDARY_LAYER))
    top.shapes(boundary_layer).insert(db.Box(0, 0, core_width, core_height))
    return layout, top, placements, {
        "gap_dbu": gap, "gap_um": gap * dbu_um,
        "width_dbu": core_width, "height_dbu": core_height,
        "width_um": core_width * dbu_um, "height_um": core_height * dbu_um,
        "pin_windows": pin_windows, "pin_labels": pin_labels,
        "physical_boxes": physical_boxes, "layers": output_layers}


def _pin_access_checks(top: Any, layout: Any, array_data: dict[str, Any],
                       record: dict[str, Any]) -> dict[str, Any]:
    ml1_index = array_data["layers"].get((8, 0))
    text_index = array_data["layers"].get((13, 0))
    if ml1_index is None or text_index is None:
        return {"passed": False, "error": "array is missing ML1 or text/pin layer"}
    metal = db.Region()
    for shape in top.shapes(ml1_index).each():
        if shape.is_box():
            metal.insert(shape.box)
    labels: dict[str, list[Any]] = {}
    for shape in top.shapes(text_index).each():
        if shape.is_text():
            labels.setdefault(shape.text.string, []).append(shape.text.trans.disp)

    instance_results = {}
    all_passed = True
    for instance in sorted(array_data["pin_windows"]):
        pin_results = {}
        for pin in record["physical_spec"]["pins"]:
            transformed_windows = array_data["pin_windows"][instance][pin]
            window_results = []
            for window in transformed_windows:
                window_region = db.Region(window)
                covered = (window_region - metal).is_empty()
                window_results.append({"bbox_um": _bbox_um(window, layout.dbu),
                                       "fully_on_real_ml1": bool(covered)})
            scoped_label = array_data["pin_labels"][instance][pin]["name"]
            label_points = labels.get(scoped_label, [])
            expected_point = array_data["pin_labels"][instance][pin]["point"]
            label_ok = len(label_points) == 1 and label_points[0] == expected_point
            point_ok = (label_ok and any(
                window.left <= expected_point.x <= window.right and
                window.bottom <= expected_point.y <= window.top
                for window in transformed_windows))
            pin_ok = (all(item["fully_on_real_ml1"] for item in window_results) and
                      label_ok and point_ok)
            pin_results[pin] = {"passed": bool(pin_ok), "scoped_text": scoped_label,
                                "source_pin_identity": pin,
                                "label_point_um": [expected_point.x * layout.dbu,
                                                   expected_point.y * layout.dbu],
                                "label_is_unique_and_in_access_window": bool(point_ok),
                                "windows": window_results}
            all_passed &= pin_ok
        instance_results[instance] = {"passed": all(item["passed"] for item in pin_results.values()),
                                      "pins": pin_results}
    return {"passed": bool(all_passed), "method":
            "each manifest ML1 access window is covered by copied ML1 geometry; the exact scoped text label is unique and remains inside an access window",
            "text_labels_used_for_connectivity": False,
            "instances": instance_results}


def _supply_connectivity(top: Any, layout: Any, array_data: dict[str, Any],
                         pin_access: dict[str, Any]) -> dict[str, Any]:
    ml1_index = array_data["layers"].get((8, 0))
    if ml1_index is None:
        return {"passed": False, "shared_pdn_connected": False,
                "error": "array is missing ML1"}
    region = db.Region()
    for shape in top.shapes(ml1_index).each():
        if shape.is_box():
            region.insert(shape.box)
    components = []
    for polygon in region.merged().each():
        component = db.Region()
        component.insert(polygon)
        components.append(component)

    supplies = {}
    for pin in SUPPLY_PINS:
        instance_rows = []
        all_component_ids = set()
        for instance in sorted(array_data["pin_windows"]):
            pin_data = pin_access.get("instances", {}).get(instance, {}).get("pins", {}).get(pin, {})
            window_component_ids = []
            for window in array_data["pin_windows"][instance].get(pin, []):
                window_region = db.Region(window)
                hits = [component_id for component_id, component in enumerate(components)
                        if not (component & window_region).is_empty()]
                window_component_ids.append(sorted(hits))
                all_component_ids.update(hits)
            local_ids = sorted({component_id for hits in window_component_ids
                                for component_id in hits})
            local_connected = (bool(window_component_ids) and
                               all(bool(hits) for hits in window_component_ids) and
                               len(local_ids) == 1 and bool(pin_data.get("passed", False)))
            instance_rows.append({"instance": instance,
                                  "connected_within_instance_by_real_ml1": bool(local_connected),
                                  "component_ids": local_ids,
                                  "window_component_ids": window_component_ids,
                                  "pin_access_passed": bool(pin_data.get("passed", False))})
        per_instance_connected = (bool(instance_rows) and
                                  all(row["connected_within_instance_by_real_ml1"]
                                      for row in instance_rows))
        instance_component_ids = [row["component_ids"][0] for row in instance_rows
                                  if len(row["component_ids"]) == 1]
        distinct_instance_components = (
            per_instance_connected and len(instance_component_ids) == len(instance_rows) and
            len(set(instance_component_ids)) == len(instance_rows))
        supplies[pin] = {"per_instance_internal_connectivity_passed": bool(per_instance_connected),
                         "distinct_instance_components": bool(distinct_instance_components),
                         "all_instance_access_windows_share_component_observed":
                             bool(per_instance_connected and len(all_component_ids) == 1),
                         "component_count_across_all_instance_access_windows": len(all_component_ids),
                         "component_ids": sorted(all_component_ids),
                         "instances": instance_rows}

    vdd_instances = {row["instance"]: set(row["component_ids"])
                     for row in supplies["VDD"]["instances"]}
    vss_instances = {row["instance"]: set(row["component_ids"])
                     for row in supplies["VSS"]["instances"]}
    vdd_vss_isolated = all(
        vdd_instances[instance].isdisjoint(vss_instances[instance])
        for instance in sorted(set(vdd_instances) & set(vss_instances)))
    local_power_connected = all(
        details["per_instance_internal_connectivity_passed"] for details in supplies.values())
    instance_components_isolated = all(
        details["distinct_instance_components"] for details in supplies.values())
    power_isolation_passed = (local_power_connected and
                              instance_components_isolated and vdd_vss_isolated)
    return {"passed": bool(power_isolation_passed),
            "per_instance_power_ports_connected": bool(local_power_connected),
            "instance_supply_components_distinct": bool(instance_components_isolated),
            "vdd_vss_isolated_within_each_instance": bool(vdd_vss_isolated),
            "shared_pdn_connected": False,
            "explicit_pdn_built": False,
            "method": "merged connected components of actual ML1 polygons; each instance's supply access windows must share one local component, VDD and VSS remain separate, and different instances remain on distinct components; labels are excluded",
            "text_labels_used_for_connectivity": False,
            "global_supply_inferred_from_labels": False,
            "supplies": supplies}


def _instance_isolation(array_data: dict[str, Any], layout: Any) -> dict[str, Any]:
    instances = sorted(array_data["physical_boxes"])
    interactions = []
    for left_index, left_name in enumerate(instances):
        left_layers = array_data["physical_boxes"][left_name]
        for right_name in instances[left_index + 1:]:
            right_layers = array_data["physical_boxes"][right_name]
            for layer in PHYSICAL_CHECK_LAYERS:
                left_boxes = left_layers.get(layer, [])
                right_boxes = right_layers.get(layer, [])
                for left_box in left_boxes:
                    for right_box in right_boxes:
                        if _is_touching(left_box, right_box):
                            interactions.append({"instance_a": left_name,
                                                 "instance_b": right_name,
                                                 "layer": f"{layer[0]}/{layer[1]}",
                                                 "bbox_a_um": _bbox_um(left_box, layout.dbu),
                                                 "bbox_b_um": _bbox_um(right_box, layout.dbu)})
    return {"passed": not interactions,
            "method": "pairwise overlap/touch check on copied native geometry, layer by layer; labels and boundaries excluded",
            "layers_checked": [f"{layer[0]}/{layer[1]}" for layer in PHYSICAL_CHECK_LAYERS],
            "cross_instance_touch_count": len(interactions),
            "interactions": interactions}


def _top_name(cell_name: str, pattern: dict[str, Any]) -> str:
    cell_slug = _safe_name(cell_name).upper()[:16]
    return f"ARR_{cell_slug}_{pattern['code']}"


def _build_reference(path: Path, top_name: str, cell_name: str,
                     physical_spec: dict[str, Any], placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ports: dict[str, str] = {}
    instances = []
    port_records = []
    pin_info = {pin: data for pin, data in
                _load_pin_metadata_for_reference(physical_spec).items()}
    for placement in placements:
        instance = placement["instance"]
        connections = {}
        for pin in physical_spec["pins"]:
            net_name = _safe_name(f"N_{instance}_{pin}").upper()
            connections[pin] = net_name
            ports[net_name] = pin_info[pin]["direction"]
            port_records.append({"name": net_name, "instance": instance,
                                 "cell_pin": pin, "direction": pin_info[pin]["direction"],
                                 "use": pin_info[pin]["use"]})
        instances.append({"name": instance, "cell": cell_name,
                          "connections": connections})
    design = {"name": top_name, "specs": {cell_name: physical_spec},
              "ports": ports, "instances": instances}
    logic.write_reference(design, path, top_name=top_name)
    contents = path.read_text(encoding="utf-8")
    if re.search(r"(?im)^\.global\s+.*\b(?:VDD|VSS)\b", contents):
        raise ArrayAcceptanceError("array reference must not declare shared global VDD/VSS")
    return port_records


def _load_pin_metadata_for_reference(physical_spec: dict[str, Any]) -> dict[str, dict[str, str]]:
    inputs = set(physical_spec.get("inputs", []))
    outputs = set(physical_spec.get("outputs", []))
    metadata = {}
    for pin in physical_spec["pins"]:
        if pin == "VDD":
            direction, use = "INOUT", "POWER"
        elif pin == "VSS":
            direction, use = "INOUT", "GROUND"
        elif pin in inputs:
            direction, use = "INPUT", "SIGNAL"
        elif pin in outputs:
            direction, use = "OUTPUT", "SIGNAL"
        else:
            direction, use = "INOUT", "SIGNAL"
        metadata[pin] = {"direction": direction, "use": use}
    return metadata


def _logic_policy() -> tuple[bool, str]:
    config_path = ROOT / "libs.tech/stdcell/config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        required = bool(config["area_search"]["require_postlayout_logic"])
        return required, str(config_path.relative_to(ROOT))
    except (OSError, ValueError, KeyError, TypeError):
        return True, "missing-or-invalid-config-default-requirement"

def _postlayout_logic_check(postlayout_path: str | Path, top_name: str,
                            physical_spec: dict[str, Any],
                            placements: list[dict[str, Any]],
                            port_records: list[dict[str, Any]],
                            output_dir: Path) -> dict[str, Any]:
    postlayout_path = Path(postlayout_path).resolve()
    if not postlayout_path.is_file():
        return {"status": "failed", "failures": [
            {"stage": "postlayout_boolean_logic", "error":
             f"assembled postlayout SPICE is missing: {postlayout_path}"}],
            "instances": []}
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs = set(physical_spec.get("inputs", []))
    outputs = set(physical_spec.get("outputs", []))
    instance_evidence = []
    failures = []
    for placement in placements:
        instance = placement["instance"]
        wrapper_name = _safe_name(f"{top_name}_LOGIC_{instance}").upper()
        instance_dir = output_dir / instance
        instance_dir.mkdir(parents=True, exist_ok=True)
        wrapper_path = instance_dir / "array_logic_wrapper.spice"
        simulation_dir = instance_dir / "simulation"
        terms = []
        for port in port_records:
            net = port["name"]
            pin = port["cell_pin"]
            owner = port["instance"]
            if owner == instance:
                net = pin
            elif pin in inputs:
                net = "0"
            elif pin in outputs:
                net = _safe_name(f"OPEN_{owner}_{pin}").upper()
            elif pin == "VDD":
                net = "VDD"
            elif pin == "VSS":
                net = "VSS"
            terms.append(net)
        wrapper_ports = " ".join(physical_spec["pins"] + ["SUB"])
        include_path = postlayout_path.as_posix().replace('"', '\\"')
        wrapper_path.write_text(
            "\n".join([
                "* Isolated truth-table wrapper; per-instance power is supplied externally.",
                f'.include "{include_path}"',
                f".SUBCKT {wrapper_name} {wrapper_ports}",
                f"XARRAY {' '.join(terms)} SUB {top_name}",
                f".ENDS {wrapper_name}",
                ""]), encoding="utf-8")
        characterize_spec = dict(physical_spec)
        characterize_spec["name"] = wrapper_name
        try:
            truth = characterize.truth_table(
                characterize_spec, wrapper_path, simulation_dir, logic.evaluate)
            instance_evidence.append({
                "instance": instance, "status": "passed",
                "truth_row_count": len(truth), "truth_table": truth,
                "wrapper": str(wrapper_path.relative_to(output_dir)),
                "simulation_dir": str(simulation_dir.relative_to(output_dir))})
        except Exception as error:
            failures.append({"stage": "postlayout_boolean_logic",
                             "instance": instance, "error": str(error),
                             "wrapper": str(wrapper_path.relative_to(output_dir)),
                             "simulation_dir": str(simulation_dir.relative_to(output_dir))})
    return {"status": "passed" if not failures else "failed",
            "method": "existing characterize.truth_table on assembled postlayout SPICE; one instance is sensitized at a time while every explicitly scoped instance supply receives an ideal testbench source",
            "testbench_power_is_not_a_shared_layout_pdn": True,
            "instances": instance_evidence, "failures": failures}


def _failed_candidate(pattern: dict[str, Any], stage: str, error: BaseException) -> dict[str, Any]:
    return {"name": pattern["name"], "rows": pattern["rows"],
            "columns": pattern["columns"], "status": "candidate_failed",
            "rankable": False, "strict_physical": {"status": "not_run"},
            "failure": {"stage": stage, "error": str(error)}}


def _run_pattern(pattern: dict[str, Any], accepted: dict[str, Any],
                 source_layout: Any, source_cell: Any,
                 geometry: dict[str, Any], layer_shapes: list[dict[str, Any]],
                 output_dir: Path, gap_um: float,
                 postlayout_logic_required: bool) -> dict[str, Any]:
    record = accepted["record"]
    cell_name = record["cell"]
    top_name = _top_name(cell_name, pattern)
    candidate_dir = output_dir / pattern["name"]
    candidate_dir.mkdir(parents=True, exist_ok=True)
    gds_path = candidate_dir / "array.gds"
    reference_path = candidate_dir / "array_reference.spice"
    verification_dir = candidate_dir / "verification"
    candidate: dict[str, Any] = {
        "name": pattern["name"], "rows": pattern["rows"],
        "columns": pattern["columns"], "instance_count": pattern["rows"] * pattern["columns"],
        "status": "constructed", "rankable": False, "top_cell": top_name,
        "pattern": {"description": pattern["description"],
                    "orientation_and_column_order": pattern["name"]},
        "strict_physical": {"status": "not_run"},
        "qualification": {"qualification": "experimental_array_experiment",
                           "process_qualification": False,
                           "timing_signoff": False,
                           "publication_candidate": False}}
    try:
        layout, top, placements, array_data = _make_array(
            source_layout, source_cell, geometry, layer_shapes,
            record, pattern, top_name, gap_um)
        pin_access = _pin_access_checks(top, layout, array_data, record)
        isolation = _instance_isolation(array_data, layout)
        supply = _supply_connectivity(top, layout, array_data, pin_access)
        boundary = db.Box(0, 0, array_data["width_dbu"], array_data["height_dbu"])
        if tuple((boundary.left, boundary.bottom, boundary.right, boundary.top)) != (
                0, 0, array_data["width_dbu"], array_data["height_dbu"]):
            raise ArrayAcceptanceError("array boundary/core dimensions are inconsistent")
        layout.write(str(gds_path))
        port_records = _build_reference(reference_path, top_name, cell_name,
                                        record["physical_spec"], placements)
        scoped_labels = [array_data["pin_labels"][item["instance"]][pin]["name"]
                         for item in placements for pin in record["physical_spec"]["pins"]]
        reference_valid = (len(scoped_labels) == len(set(scoped_labels)) and
                           {item["name"] for item in port_records} == set(scoped_labels))
        candidate.update({
            "array_pattern": {"name": pattern["name"], "rows": pattern["rows"],
                              "columns": pattern["columns"],
                              "double_row_pairs": [
                                  {"rows": [row, row + 1],
                                   "orientations": ["R0", "R180"],
                                   "second_row_columns_reversed": True}
                                  for row in range(0, pattern["rows"], 2)]
                                  if pattern["name"] == "4x4_reversed_double_rows" else [],
                              "description": pattern["description"]},
            "transforms": [{key: value for key, value in placement.items()
                             if key != "x_dbu" and key != "y_dbu"}
                            for placement in placements],
            "top_level_ports": port_records,
            "reference": {"path": str(reference_path.relative_to(output_dir)),
                          "top_name": top_name, "valid_for_physical_comparison": bool(reference_valid),
                          "global_vdd_vss": False,
                          "per_instance_power_ports": True,
                          "power_delivery_scope": "independently exposed per-instance VDD/VSS ports; no shared PDN is constructed"},
            "physical_checks": {"pin_access": pin_access,
                                "instance_isolation": isolation,
                                "supply_connectivity": supply},
            "dimensions": {"cell_width_um": geometry["width_um"],
                           "cell_height_um": geometry["height_um"],
                           "rows": pattern["rows"], "columns": pattern["columns"],
                           "placement_gap_um": array_data["gap_um"],
                           "abutment": array_data["gap_um"] == 0.0,
                           "core_width_um": array_data["width_um"],
                           "core_height_um": array_data["height_um"],
                           "core_area_um2": array_data["width_um"] * array_data["height_um"],
                           "occupied_cell_area_um2": pattern["rows"] * pattern["columns"] *
                                                     geometry["width_um"] * geometry["height_um"],
                           "area_domain": "bounding 100/0 array core; includes inter-instance gaps, excludes frame and any unbuilt external routing"},
            "native_geometry": {"layers_present": sorted(
                                    {f"{item['layer'][0]}/{item['layer'][1]}"
                                     for item in layer_shapes} |
                                    {f"{BOUNDARY_LAYER[0]}/{BOUNDARY_LAYER[1]}"}),
                                "routing_conductors": ["ML1", "TiN"],
                                "contact_layer": "CNT", "active_layer": "DIFF",
                                "forbidden_layers": ["Poly", "VIA1", "ML2"],
                                "synthetic_interconnect_added": False,
                                "boundary_layer": [100, 0],
                                "boundary_bbox_um": _bbox_um(boundary, layout.dbu)},
            "area_domain_limits": {"includes_only_copied_cells_and_placement_gaps": True,
                                    "does_not_include_inter_instance_power_routing": True,
                                    "not_a_floorplan_or_signoff_core_area": True,
                                    "library_qualification": "experimental; strict checks are per generated array"},
            "array_contacts_count": (pattern["rows"] * pattern["columns"] *
                                     int(record["generator_manifest"].get("contacts_count", 0))),
            "artifacts": {"gds": str(gds_path.relative_to(output_dir)),
                          "reference": str(reference_path.relative_to(output_dir)),
                          "verification_dir": str(verification_dir.relative_to(output_dir)),
                          "postlayout_logic_dir": str((candidate_dir / "postlayout_logic").relative_to(output_dir))},
            "logic_check": {"status": "not_run",
                            "required_for_rankability": bool(postlayout_logic_required)} })
        if not reference_valid:
            candidate["strict_physical"] = {
                "status": "not_run_invalid_reference",
                "failure": {"stage": "reference_validation",
                            "error": "per-instance port/label mapping is incomplete"}}
        else:
            try:
                evidence = verify.verify_physical(gds_path, reference_path, top_name,
                                                  verification_dir)
                candidate["strict_physical"] = {"status": "passed", "evidence": evidence}
            except verify.VerificationError as error:
                candidate["strict_physical"] = {"status": "strict_physical_failed",
                                                 "evidence": error.evidence,
                                                 "failure": {"stage": "strict_physical_verification",
                                                             "error": str(error)}}
            except Exception as error:
                evidence = getattr(error, "evidence", None)
                candidate["strict_physical"] = {"status": "strict_physical_failed",
                                                 "evidence": evidence,
                                                 "failure": {"stage": "strict_physical_verification",
                                                             "error": str(error)}}
        strict_pass = candidate["strict_physical"]["status"] == "passed"
        if strict_pass and postlayout_logic_required:
            evidence = candidate["strict_physical"].get("evidence", {})
            try:
                logic_check = _postlayout_logic_check(
                    evidence.get("postlayout_spice", ""), top_name,
                    record["physical_spec"], placements, port_records,
                    candidate_dir / "postlayout_logic")
            except Exception as error:
                logic_check = {"status": "failed", "failures": [
                    {"stage": "postlayout_boolean_logic", "error": str(error)}],
                    "instances": []}
        elif postlayout_logic_required:
            logic_check = {"status": "not_run_strict_physical_failed",
                           "failures": [], "instances": []}
        else:
            logic_check = {"status": "not_required", "failures": [], "instances": []}
        candidate["logic_check"] = {
            **logic_check, "required_for_rankability": bool(postlayout_logic_required)}
        supply_pass = supply["passed"]
        logic_pass = (not postlayout_logic_required or
                      candidate["logic_check"]["status"] == "passed")
        candidate["rankable"] = bool(strict_pass and pin_access["passed"] and
                                     isolation["passed"] and supply_pass and logic_pass)
        if candidate["strict_physical"]["status"] == "strict_physical_failed":
            candidate["status"] = "strict_physical_failed"
        elif candidate["strict_physical"]["status"] == "not_run_invalid_reference":
            candidate["status"] = "not_accepted_invalid_reference"
        elif not pin_access["passed"]:
            candidate["status"] = "not_accepted_pin_access_failed"
        elif not isolation["passed"]:
            candidate["status"] = "not_accepted_instance_interaction"
        elif not supply_pass:
            candidate["status"] = "not_accepted_disconnected_supply"
        elif postlayout_logic_required and not logic_pass:
            candidate["status"] = "postlayout_logic_failed"
        else:
            candidate["status"] = "rankable_experimental_candidate"
    except Exception as error:
        candidate["status"] = "candidate_failed"
        candidate["failure"] = {"stage": "array_construction_or_checks", "error": str(error)}
        candidate["strict_physical"] = {"status": "not_run"}
    return candidate


def run_array_acceptance(cell_name: str, output_dir: str | Path, *,
                         manifest_path: str | Path | None = None,
                         placement_gap_um: float = PLACEMENT_GAP_UM) -> dict[str, Any]:
    """Create deterministic 2x2/4x4 array artifacts and their strict evidence."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_manifest = Path(manifest_path).resolve() if manifest_path else None
    logic_required, logic_policy_source = _logic_policy()
    report: dict[str, Any] = {
        "schema": "icps2023_5_native_array_acceptance_v1",
        "cell": cell_name,
        "experiment": "deterministic_native_gds_array_acceptance",
        "status": "completed",
        "rankable_candidate_count": 0,
        "qualification_limits": {"process_qualification": False,
                                  "timing_signoff": False,
                                  "publication": False,
                                  "native_1m_layers_only": True,
                                  "postlayout_boolean_logic_required": logic_required,
                                  "postlayout_logic_policy_source": logic_policy_source,
                                  "no_site_or_synthetic_routing_resource": True,
                                  "array_acceptance_does_not_claim_foundry_signoff": True},
        "candidates": []}
    try:
        accepted = _load_accepted_cell(cell_name, selected_manifest)
        record = accepted["record"]
        source_layout = db.Layout()
        source_layout.read(str(accepted["gds_path"]))
        source_cell = source_layout.cell(cell_name)
        if source_cell is None:
            raise ArrayAcceptanceError(f"accepted GDS has no top cell named {cell_name}")
        geometry, layer_shapes = _source_geometry(source_layout, source_cell, record)
        report["source"] = {"cell": cell_name,
                            "qualification": record.get("qualification"),
                            "manifest": accepted["manifest_path"].name,
                            "manifest_sha256": accepted["manifest_sha256"],
                            "gds_sha256": accepted["gds_sha256"],
                            "accepted_status": record.get("status"),
                            "source_area_um2": record.get("area_um2"),
                            "source_boundary_um": record.get("boundary_um"),
                            "source_physical_evidence": record.get("physical")}
        for pattern in ARRAY_PATTERNS:
            report["candidates"].append(_run_pattern(
                pattern, accepted, source_layout, source_cell,
                geometry, layer_shapes, output_dir, placement_gap_um,
                logic_required))
    except Exception as error:
        report["status"] = "input_failed"
        report["input_failure"] = {"stage": "accepted_cell_manifest_or_gds", "error": str(error)}
        report["candidates"] = [_failed_candidate(
            pattern, "accepted_cell_manifest_or_gds", error) for pattern in ARRAY_PATTERNS]
    report["rankable_candidate_count"] = sum(
        bool(candidate.get("rankable")) for candidate in report["candidates"])
    report_path = output_dir / "array_acceptance.json"
    report["report_path"] = str(report_path.name)
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2,
                                      ensure_ascii=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True, help="accepted physical cell name")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="accepted cell_manifest.json (default: ICPS2023_5_gen_sc publication)")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="directory for deterministic array GDS, references, evidence, and JSON")
    parser.add_argument("--placement-gap-um", type=float, default=PLACEMENT_GAP_UM,
                        help="0 for exact boundary abutment; positive spacing for isolation experiments (default: 0 um)")
    args = parser.parse_args(argv)
    report = run_array_acceptance(args.cell, args.output_dir,
                                  manifest_path=args.manifest,
                                  placement_gap_um=args.placement_gap_um)
    print(json.dumps({"report": report["report_path"],
                      "status": report["status"],
                      "rankable_candidate_count": report["rankable_candidate_count"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
