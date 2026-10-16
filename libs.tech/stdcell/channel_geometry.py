"""Deterministic native-1M channel layout for static networks.

A sparse comparator with shared-diffusion trails for topology-compatible
same-polarity MOS devices, short ML1 source/drain escapes, off-active CNT
transitions, vertical TiN drops, and horizontal ML1 net buses. There are no
same-layer crossings. The explicit TiN length/contact cost must be compared
with compact cells, not hidden.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any


def _channel_height(devices, net_count):
    escape_y = 15.0 + max(device["w_um"] for device in devices) + 26.0
    bus_floor = escape_y + 24.0
    minimum = math.ceil((bus_floor + 14.0 * (net_count - 1) + 10.0) / 10.0) * 10.0
    return escape_y, bus_floor, minimum


def minimum_height_um(spec: dict[str, Any], *, fingers: int = 1) -> float:
    """Return this channel template's bound, never a process-wide minimum."""
    import geometry as g
    devices = g._device_records(spec, fingers)
    nets = set(spec["pins"]) | {device[terminal] for device in devices
                               for terminal in ("d", "g", "s")}
    return _channel_height(devices, len(nets))[2]


def build_channel_cell(spec: dict[str, Any], height_um: float, *, fingers: int = 1,
                       pin_order: list[str] | None = None, mirror: bool = False,
                       route_channel_um: float = 0.0):
    # Import here so the compact backend can select this architecture without
    # a circular module-initialization dependency.
    import geometry as g
    import klayout.db as db

    requested = g._finite(height_um, "height_um")
    extension = g._finite(route_channel_um, "route_channel_um")
    if requested <= 0 or extension < 0:
        raise g.GeometryError("height must be positive and route channel nonnegative")
    devices = g._device_records(spec, fingers)
    pins = list(spec["pins"])
    ordered = list(pin_order) if pin_order is not None else pins
    if len(ordered) != len(pins) or set(ordered) != set(pins):
        raise g.GeometryError("pin_order must contain every formal pin exactly once")
    nets = set(pins)
    for device in devices:
        nets.update(device[terminal] for terminal in ("d", "g", "s"))
    # VSS and VDD form the outside buses; signal and internal buses are between.
    bus_order = (["VSS"] if "VSS" in nets else [])
    bus_order += [pin for pin in ordered if pin not in {"VSS", "VDD"}]
    bus_order += sorted(nets - set(bus_order) - {"VDD"})
    if "VDD" in nets:
        bus_order.append("VDD")
    active_y = 15.0
    escape_y, bus_floor, minimum_height = _channel_height(devices, len(bus_order))
    bus_pitch = 14.0  # 10um CNT landing vs 6um bus leaves >=4um ML1 space.
    if requested < minimum_height:
        raise g.GeometryError(
            f"{spec['name']} channel template needs height >= {minimum_height:g}um; "
            "this is not a process-wide minimum")
    bus_y = {net: bus_floor + index * bus_pitch for index, net in enumerate(bus_order)}
    layout = db.Layout()
    layout.dbu = g.DBU
    top = layout.create_cell(f"{spec['name']}_CHANNEL_H{requested:g}_F{fingers}")
    layers = {number: g._layer(layout, number) for number in
              (g.DIFF, g.TIN, g.CNT, g.ML1, g.TEXT, g.NWL, g.PAREA, g.NAREA)}
    labels = defaultdict(list)
    m1_shapes, tin_shapes, contacts, active_regions = [], [], [], []
    device_records, rows = [], []
    wire_ml1, wire_tin = 0.0, 0.0
    x = 20.0
    islands = []
    for polarity in dict.fromkeys(device["polarity"] for device in devices):
        compatible = defaultdict(list)
        for device in devices:
            if device["polarity"] == polarity:
                compatible[(device["w_um"], device["l_um"])].append(device)
        for group in compatible.values():
            islands.extend(g._euler_islands(group))
    shared_active_rows = 0
    for group, net_walk in islands:
        island = g._draw_island(layout, top, layers, group, net_walk,
                                x, active_y, labels, 11.5)
        m1_shapes.extend(island["pads"])
        tin_shapes.extend(island["tin_shapes"])
        contacts.extend(island["contacts"])
        active_regions.append(island["active_bbox_um"])
        wire_tin += island["tin_length_um"]
        for net, point in island["diffusion_points"]:
            stem = g._rect(point[0] - 3.0, point[1], point[0] + 3.0, escape_y)
            g._put_box(layout, top, layers[g.ML1], stem)
            m1_shapes.append((stem, net, "sd_escape"))
            wire_ml1 += escape_y - point[1]
            g._draw_tin_ml1_via(layout, top, layers, (point[0], escape_y), net,
                                 m1_shapes, tin_shapes, contacts)
            drop = g._rect(point[0] - 3.0, escape_y, point[0] + 3.0, bus_y[net])
            g._put_box(layout, top, layers[g.TIN], drop)
            tin_shapes.append((drop, net, "vertical_drop"))
            wire_tin += bus_y[net] - escape_y
            g._draw_tin_ml1_via(layout, top, layers, (point[0], bus_y[net]), net,
                                 m1_shapes, tin_shapes, contacts)
        for net, point in island["gate_points"]:
            drop = g._rect(point[0] - 3.0, point[1], point[0] + 3.0, bus_y[net])
            g._put_box(layout, top, layers[g.TIN], drop)
            tin_shapes.append((drop, net, "vertical_gate_drop"))
            wire_tin += bus_y[net] - point[1]
            g._draw_tin_ml1_via(layout, top, layers, (point[0], bus_y[net]), net,
                                 m1_shapes, tin_shapes, contacts)
        bbox = list(island["active_bbox_um"])
        shared = len(island["gates"]) > 1
        shared_active_rows += shared
        for gate_order_index, gate in enumerate(island["gates"]):
            device_records.append({**gate["device"],
                                   "active_row": ("shared_euler_island" if shared
                                                  else "isolated_euler_island"),
                                   "gate_order_index": gate_order_index,
                                   "bbox_um": bbox})
        rows.append({"polarity": group[0]["polarity"], "bbox_um": bbox,
                     "gate_order": [gate["device"]["g"] for gate in island["gates"]],
                     "euler_shared_active": shared})
        x = bbox[2] + 20.0
    width = x + 10.0 + extension
    pin_windows = {}
    for net, y in bus_y.items():
        bus = g._rect(10.0, y - 3.0, width - 10.0, y + 3.0)
        g._put_box(layout, top, layers[g.ML1], bus)
        m1_shapes.append((bus, net, "horizontal_net_bus"))
        wire_ml1 += width - 20.0
        if net in pins:
            windows = [g._rect(5.0, y - 5.0, 15.0, y + 5.0),
                       g._rect(width - 15.0, y - 5.0, width - 5.0, y + 5.0)]
            for window in windows:
                g._put_box(layout, top, layers[g.ML1], window)
                m1_shapes.append((window, net, "external_pin"))
            pin_windows[net] = [list(window) for window in windows]
    if mirror:
        def mirrored(rect):
            return g._rect(width - rect[2], rect[1], width - rect[0], rect[3])
        for layer in layers.values():
            boxes = [tuple(value * g.DBU for value in
                           (shape.box.left, shape.box.bottom, shape.box.right, shape.box.top))
                     for shape in top.shapes(layer).each() if shape.is_box()]
            top.shapes(layer).clear()
            for rect in boxes:
                g._put_box(layout, top, layer, mirrored(rect))
        m1_shapes = [(mirrored(rect), net, kind) for rect, net, kind in m1_shapes]
        tin_shapes = [(mirrored(rect), net, kind) for rect, net, kind in tin_shapes]
        active_regions = [mirrored(rect) for rect in active_regions]
        for record in contacts:
            record["cut_um"] = list(mirrored(record["cut_um"]))
            record["landing_um"] = list(mirrored(record["landing_um"]))
        for record in device_records + rows:
            record["bbox_um"] = list(mirrored(record["bbox_um"]))
        pin_windows = {net: [list(mirrored(window)) for window in windows]
                       for net, windows in pin_windows.items()}
    pin_windows = {pin: pin_windows[pin] for pin in ordered}
    for pin in ordered:
        window = pin_windows[pin][0]
        g._put_text(layout, top, layers[g.TEXT], pin,
                    ((window[0] + window[2]) / 2, (window[1] + window[3]) / 2))
    boundary = [0.0, 0.0, g._snap(width), g._snap(requested)]
    g._put_box(layout, top, g._layer(layout, 100), tuple(boundary))
    pin_rects = {tuple(window) for windows in pin_windows.values() for window in windows}
    obstructions = [list(rect) for rect, _net, _kind in m1_shapes if rect not in pin_rects]
    manifest = {
        "name": spec["name"], "process_profile": "icps2023_5_1m", "architecture": "channel",
        "bbox_um": boundary, "boundary_um": boundary, "boundary_layer": [100, 0],
        "width_um": boundary[2], "height_um": boundary[3], "area_um2": width * requested,
        "pins": pin_windows, "devices": device_records, "rows": rows,
        "mos_count": len(devices), "contacts_count": len(contacts), "contacts": contacts,
        "tin_length_um": g._snap(wire_tin), "requested_height_um": requested,
        "actual_height_um": requested, "nominal_track_count_assumption": requested / 10.0,
        "template_minimum_height_um": minimum_height,
        "track_pitch_assumption_um": 10.0, "track_pitch_status": "not a qualified routing/site pitch",
        "route_channel_um": extension,
        "folding": {"requested_parallel_fingers": fingers, "physical_parallel_devices": len(devices),
                    "total_width_preserved": True,
                    "shared_active_euler_rows": shared_active_rows},
        "variant": {"fingers": fingers, "mirror": bool(mirror), "pin_order": ordered,
                    "architecture": "channel"},
        "routing": {"local_conductors": ["ML1", "TiN"], "unrouted_nets": [],
                    "ML1_wire_length_um": g._snap(wire_ml1), "TiN_wire_length_um": g._snap(wire_tin),
                    "additional_CNT_vias": sum(contact["kind"] == "metal_via" for contact in contacts),
                    "direct_tin_diff_contact": False, "bus_pitch_um": bus_pitch,
                    "shapes": {"ML1": [[list(rect), net, kind] for rect, net, kind in m1_shapes],
                               "TIN": [[list(rect), net, kind] for rect, net, kind in tin_shapes]}},
        "obstructions_um": obstructions, "active_regions_um": [list(rect) for rect in active_regions],
        "site_width_um": None,
        "provenance": {"device_geometry": "native same-polarity, same-size Euler-shared diffusion trails where possible",
                       "interconnect": "offactive CNT; vertical TiN and horizontal ML1 channel; no layer crossings within one conductor",
                       "qualification": "strict DRC/LVS/PEX and postlayout state checks required; no signoff",
                       "sheet_resistance_ohm_sq": {"ML1": 0.2, "TIN": 20.0}},
    }
    return layout, top, manifest
