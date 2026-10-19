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


BUS_PITCH_UM = 14.0
# A 10um CNT_TIN_ML1 landing is 10um tall and sits on a 6um bus, so two
# landings on adjacent buses need 5+4+5 = 14um centre-to-centre: the pads, not
# the bus bodies, set the pitch.  The supply buses additionally clear the
# full-width rail bands (applied below).
POWER_BUS_GAP_UM = 0.0
# Space the topmost bus must leave under the VDD rail band: the rail tie's 10um
# CNT_TIN_ML1 landing (half on each side) plus one ML1 space.
VIA_TAIL_UM = 12.0


def _bus_positions(bus_order, bus_floor, pitch=BUS_PITCH_UM,
                   power_gap=POWER_BUS_GAP_UM):
    """Return each net's bus y, with extra spacing around the supply buses.

    A supply bus carries a 10um CNT_TIN_ML1 landing at every island column, so
    its *neighbours* must stand off far enough for that pad to clear them as
    well as the full-width rail band.
    """
    positions = {}
    y = bus_floor
    for index, net in enumerate(bus_order):
        previous = bus_order[index - 1] if index else None
        if net in {"VDD", "VSS"} or previous in {"VDD", "VSS"}:
            y += power_gap
        positions[net] = y
        y += pitch
    return positions


def _bus_order(nets, pins):
    order = (["VSS"] if "VSS" in nets else [])
    order += [pin for pin in pins if pin not in {"VSS", "VDD"}]
    order += sorted(set(nets) - set(order) - {"VDD"})
    if "VDD" in nets:
        order.append("VDD")
    return order


def _channel_height(devices, net_count, bus_order=None, *, active_y):
    escape_y = active_y + max(device["w_um"] for device in devices) + 26.0
    bus_floor = escape_y + 24.0
    if bus_order is None:
        span = BUS_PITCH_UM * (net_count - 1)
    else:
        positions = _bus_positions(bus_order, bus_floor)
        span = max(positions.values()) - bus_floor
    # The topmost bus has to stay clear of the VDD rail band by the rail tie's
    # 10um landing plus ML1 spacing, so the template reserves that tail.
    tail = 10.0 if not bus_order else max(10.0, VIA_TAIL_UM)
    minimum = math.ceil((bus_floor + span + tail) / 10.0) * 10.0
    return escape_y, bus_floor, minimum


def minimum_height_um(spec: dict[str, Any], *, fingers: int = 1) -> float:
    """Return this channel template's bound, never a process-wide minimum."""
    import geometry as g
    devices = g._device_records(spec, fingers)
    nets = set(spec["pins"]) | {device[terminal] for device in devices
                               for terminal in ("d", "g", "s")}
    order = _bus_order(nets, spec["pins"])
    return _channel_height(devices, len(nets), order,
                           active_y=g.SOI_EDGE_MIN_ACTIVE_Y_UM)[2]


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
    # The deck reads a whole TiN network that reaches an island gate as gate
    # TiN (enlarged by 2um), so those nets are routed with the wider keepout.
    g._TIN_GATE_NETS = frozenset(device["g"] for device in devices)
    pins = list(spec["pins"])
    ordered = list(pin_order) if pin_order is not None else pins
    if len(ordered) != len(pins) or set(ordered) != set(pins):
        raise g.GeometryError("pin_order must contain every formal pin exactly once")
    nets = set(pins)
    for device in devices:
        nets.update(device[terminal] for terminal in ("d", "g", "s"))
    # VSS and VDD form the outside buses; signal and internal buses are between.
    bus_order = _bus_order(nets, ordered)
    active_y = g.SOI_EDGE_MIN_ACTIVE_Y_UM
    escape_y, bus_floor, minimum_height = _channel_height(
        devices, len(bus_order), bus_order, active_y=active_y)
    if requested < minimum_height:
        raise g.GeometryError(
            f"{spec['name']} channel template needs height >= {minimum_height:g}um; "
            "this is not a process-wide minimum")
    bus_y = _bus_positions(bus_order, bus_floor)
    # The full-width supply rail bands own the row edges.  A rail tie has to
    # land a 10um CNT_TIN_ML1 pad on the rail, which cannot fit in the few um
    # left by the outermost bus, so the supply buses are pulled clear of the
    # bands *before* any island drop is drawn to them.
    rail_vdd_top = requested - g.POWER_RAIL_HEIGHT_UM
    if "VDD" in bus_y:
        # The tie lands a 10um pad on the rail, so the bus it starts from has to
        # sit a full pad half plus ML1 space below the band, not just touch it.
        bus_y["VDD"] = min(bus_y["VDD"],
                           g._snap(rail_vdd_top - g.VIA_CONTACT - g.ML1_SPACE - 3.0))
    if "VSS" in bus_y:
        bus_y["VSS"] = max(bus_y["VSS"],
                           g._snap(g.POWER_RAIL_HEIGHT_UM + g.VIA_CONTACT
                                   + g.ML1_SPACE + 3.0))
    layout = db.Layout()
    layout.dbu = g.DBU
    top = layout.create_cell(f"{spec['name']}_CHANNEL_H{requested:g}_F{fingers}")
    layers = {number: g._layer(layout, number) for number in
              (g.DIFF, g.TIN, g.CNT, g.ML1, g.TEXT, g.NWL, g.PAREA, g.NAREA)}
    labels = defaultdict(list)
    m1_shapes, tin_shapes, contacts, active_regions = [], [], [], []
    device_records, rows, edge_protection = [], [], []
    wire_ml1 = wire_tin = 0.0
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
                                x, active_y, labels)
        m1_shapes.extend(island["pads"])
        tin_shapes.extend(island["tin_shapes"])
        contacts.extend(island["contacts"])
        edge_protection.extend(island["edge_protection"])
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
                                   "bbox_um": bbox,
                                   "soi_edge_protection": gate["soi_edge_protection"]})
        rows.append({"polarity": group[0]["polarity"], "bbox_um": bbox,
                     "gate_order": [gate["device"]["g"] for gate in island["gates"]],
                     "euler_shared_active": shared})
        x = bbox[2] + 20.0
    width = x + 10.0 + extension
    pin_windows = {}
    # The full-width supply rail bands occupy the row edges, so no signal or
    # supply *bus* may sit within ML1 spacing of them: the rail tie has to land
    # a 10um CNT_TIN_ML1 pad on the rail, which cannot fit in a 4um gap.  The
    # supply buses are therefore pulled inside the rail clearance.
    for net, y in bus_y.items():
        bus = g._rect(10.0, y - 3.0, width - 10.0, y + 3.0)
        g._put_box(layout, top, layers[g.ML1], bus)
        m1_shapes.append((bus, net, "horizontal_net_bus"))
        wire_ml1 += width - 20.0
        if net in pins and net not in {"VDD", "VSS"}:
            windows = [g._rect(5.0, y - 5.0, 15.0, y + 5.0),
                       g._rect(width - 15.0, y - 5.0, width - 5.0, y + 5.0)]
            for window in windows:
                g._put_box(layout, top, layers[g.ML1], window)
                m1_shapes.append((window, net, "external_pin"))
            pin_windows[net] = [list(window) for window in windows]
    power_rails = g._add_power_rail_bands(
        layout, top, layers[g.ML1], m1_shapes, width, requested)
    wire_ml1 += sum(rail[2] - rail[0] for rail in power_rails.values())
    for net, rail in power_rails.items():
        source_points = [
            (g._snap((contact["landing_um"][0] + contact["landing_um"][2]) / 2),
             g._snap((contact["landing_um"][1] + contact["landing_um"][3]) / 2))
            for contact in contacts
            if contact.get("kind") == "metal_via" and contact.get("net") == net]
        if not source_points:
            raise g.GeometryError(f"{net} power rail has no device-bus via to connect")
        source = (min(source_points, key=lambda point: point[1]) if net == "VSS"
                  else max(source_points, key=lambda point: point[1]))
        goal = (source[0], g._snap((rail[1] + rail[3]) / 2))
        # Both supplies take a direct ML1 tie.  A channel cell's bus stack
        # leaves no room for a CNT_TIN_ML1 riser, and the tie starts on the
        # supply's own bus, so that bus and the rail are merge targets for it:
        # the deck's ML1 space rule is not net-aware, and a tie that kept clear
        # of its own network would only be a second conductor beside it.
        source_network = tuple(
            rect for rect, owner, kind in m1_shapes
            if owner == net and rect[0] - 8.0 <= source[0] <= rect[2] + 8.0
            and (rect[1] - 8.0 <= source[1] <= rect[3] + 8.0
                 or rect[1] - 8.0 <= goal[1] <= rect[3] + 8.0))
        path = g._route_ml1(source, goal, net, m1_shapes, width, requested,
                            merge_targets=(rail,) + source_network)
        for rectangle in g._path_rectangles(path):
            g._put_box(layout, top, layers[g.ML1], rectangle)
            m1_shapes.append((rectangle, net, "wire"))
        wire_ml1 += sum(abs(a[0] - b[0]) + abs(a[1] - b[1])
                        for a, b in zip(path, path[1:]))
        pin_windows[net] = [list(rail)]
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
        for protection in edge_protection:
            for key in ("gate_bbox_um", "tin_crossing_um", "strap_um"):
                protection[key] = list(mirrored(protection[key]))
            for contact in protection["contacts"]:
                contact["center_um"] = [
                    g._snap(width - contact["center_um"][0]),
                    contact["center_um"][1]]
                for key in ("cut_um", "landing_um"):
                    contact[key] = list(mirrored(contact[key]))
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
    edge_metrics = g._ml1_area_metrics(layout, top, layers[g.ML1], boundary)
    soi_edge_manifest = {
        "experimental_margin_um": g.SOI_EDGE_EXPERIMENTAL_MARGIN_UM,
        "fab_prep_tin_bias_um": g.SOI_EDGE_FAB_TIN_BIAS_UM,
        "design_side_ml1_expansion_um": g.SOI_EDGE_STRAP_DRAW_MARGIN_UM,
        "single_edge_only": True,
        "scope": "one open TiN/SOI edge; not two simultaneous edge breaks",
        "contact_topology": (
            "two off-DIFF 5um CNT ties per gate on opposite TiN crossing edges, "
            "joined by a continuous ML1 strap"),
        "devices": edge_protection,
        **edge_metrics,
    }
    manifest = {
        "name": spec["name"], "process_profile": "icps2023_5_1m", "architecture": "channel",
        "bbox_um": boundary, "boundary_um": boundary, "boundary_layer": [100, 0],
        "width_um": boundary[2], "height_um": boundary[3], "area_um2": width * requested,
        "pins": pin_windows, "power_rails_um": {net: list(rail) for net, rail in power_rails.items()},
        "devices": device_records, "rows": rows,
        "soi_edge_protection": soi_edge_manifest,
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
                    "direct_tin_diff_contact": False, "bus_pitch_um": BUS_PITCH_UM,
                    "power_bus_gap_um": POWER_BUS_GAP_UM,
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
