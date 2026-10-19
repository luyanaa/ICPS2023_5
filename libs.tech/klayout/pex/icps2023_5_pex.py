#!/usr/bin/env python3
# =============================================================================
# icps2023_5_pex.py -- KLayout parasitic extraction for the ICPS2023_5
#                      (minimal Fab SOI-CMOS) process.
#
# Supersedes nothing: the legacy PDK had no PEX at all.
#
#   * resistance network -- KLayout's native R extractor (klayout.pex,
#     RNetExtractor / RExtractorTech).  Only the qualified layers and
#     contacts are modelled.  The direct measurements are in
#     PDK_minimalFabSOICMOS.pdf p.8 (Table of Sheet & Contact Resistance):
#         ML1 (Al)       0.20 ohm/sq
#         TiN            20 ohm/sq
#         N+ diffusion   180 ohm/sq
#         P+ diffusion   1.2 kohm/sq
#         Al-N+ contact  150 ohm
#         Al-P+ contact  200 ohm
#         Al-TiN contact 0.9 ohm
#   * capacitance -- only the reported 0.086 fF/um^2 passive-diffusion
#     density, mapped to global SUB as an explicit return contract.  BOX is the
#     strongest analytical candidate (ideal 400 nm BOX parallel-plate density
#     eps0*epsr/tBOX = 0.086327 fF/um^2 matches the measured 0.086 fF/um^2
#     within displayed rounding), but the measured return electrode is
#     UNCONFIRMED; see libs.tech/klayout/pex/audit_box_capacitance.md, status
#     BOX_RETURN_ANALYTICALLY_SUPPORTED / MEASUREMENT_UNCONFIRMED.  Area
#     term only; no perimeter rule is characterized.  The 2.3 fF/um^2 CAP
#     windows are LVS-owned devices (different dielectric) and are not
#     emitted here.
#
# Ownership: LVS owns MOS devices, RES bodies, CAP devices, and diode I/V.
# PEX owns TiN/ML1 wiring and eligible passive/diode diffusion access. MOS
# source/drain access through the outer edge of the nearest CNT cut is excluded
# locally because fitted B4SOI RDSW cannot be separated from physical access.
# Diffusion beyond that cut remains PEX-owned. RES-marker contacts are also
# excluded; neither model supplies independent contact characterization there.
#
# The reference manual p.3 marks Via, M2(Al), Via and M3(Al) T.B.D.; they are
# therefore omitted rather than assigned an inferred resistance/capacitance.
# The un-doped SOI film has no direct sheet-resistance value in either source
# PDF, so it is not silently assigned the legacy LVS R_SOI value.
# Model limits (read the README): eligible passive-diffusion C is assigned
# by bounded Euclidean Voronoi area to located nodes on the same diffusion
# layer. Total area capacitance is conserved; this remains nodal lumping, not
# continuous distributed RC. No qualified interconnect coupling is emitted.
#
# Usage (inside the LibreLane nix-shell, which provides this KLayout):
#   python3 libs.tech/klayout/pex/icps2023_5_pex.py \
#       --input <layout.gds> [--top CELL] [--output <parasitics.spice>] \
#       [--layer-mode design|fab] [--no-capacitance]
#
# Copyright 2023 minimalFab Promoting Organization
# Licensed under the Apache License, Version 2.0
# =============================================================================
import argparse
import json
import os
import sys

import klayout.db as pya
import klayout.pex as pex

# --------------------------------------------------------------------------
# Layer tables
#
# FAB numbers are the mask numbers in PDK_minimalFabSOICMOS.pdf p.19,
# "Table of Drawn Layers for Each Device".  DESIGN numbers are this
# repository's designer-facing aliases; libs.tech/klayout/doc/layers.md maps
# them to those same qualified mask layers.  No T.B.D. upper-metal layers are
# accepted by this PEX deck.
# --------------------------------------------------------------------------
DESIGN = {
    "DIFF": (3, 0), "TIN": (5, 0), "TINBLK": (5, 1), "CNT": (7, 0),
    "ML1": (8, 0), "TEXT": (13, 0), "RES": (15, 0), "CAP": (16, 0),
    "DIO": (17, 0), "PAREA": (18, 0), "NAREA": (19, 0), "PWL": (21, 0),
}
FAB = {
    "DIFF": (4, 0), "TIN": (5, 0), "TINBLK": (5, 1), "CNT": (6, 0),
    "ML1": (7, 0), "PAREA": (2, 0), "NAREA": (3, 0), "CAP": (16, 0),
    "RES": (15, 0), "DIO": (17, 0), "TEXT": (13, 1), "PWL": (13, 0),
}
PROFILE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "process_profiles.json")
)
with open(PROFILE_PATH, encoding="utf-8") as profile_file:
    PROFILE_DATA = json.load(profile_file)


def resolve_process_profile(name):
    name = name or os.environ.get("ICPS2023_5_PROCESS_PROFILE") or PROFILE_DATA["default_profile"]
    if name not in PROFILE_DATA["profiles"]:
        choices = ", ".join(PROFILE_DATA["profiles"])
        sys.exit("unknown ICPS2023_5 process profile '%s' (choose %s)" % (name, choices))
    return name, PROFILE_DATA["profiles"][name]

# --------------------------------------------------------------------------
# Technology data.  Values are direct measurements from the source PDFs;
# unsupported terms are deliberately absent rather than inferred.
# --------------------------------------------------------------------------
F_PER_UM2 = 1e-15          # 1 fF/um^2
OHM_PER_SQ = {
    "ML1": 0.20,           # p.8 Table of Sheet & Contact Resistance, Al
    "TIN": 20.0,            # p.8 Table of Sheet & Contact Resistance, TiN
    "NAREA": 180.0,         # p.8 Table of Sheet & Contact Resistance, N+
    "PAREA": 1200.0,        # p.8 Table of Sheet & Contact Resistance, P+
}

# GDS uses one untyped CNT layer. Each cut must have one physical stack.
# On SOI, an ambiguous or incompletely implanted landing is rejected before
# classification; TiN/ML1 contacts outside SOI remain insulated from diffusion.
CUT_LAYERS = {
    "CNT_TIN": (1000, 0),
    "CNT_NAREA": (1001, 0),
    "CNT_PAREA": (1002, 0),
}

# The CNT remains MOS-owned; the extra one-DBU overlap only locates ideal
# graph anchors on external diffusion and ML1, without assigning contact R.
MOS_TERMINAL_TIE_EXTENSION_DBU = 1

# The PDK reports each contact resistance for a 5 x 5 um contact. KLayout's
# via model takes area resistance (ohm*um^2), so R_A = R_5x5 * 25 um^2;
# actual R scales inversely with the extracted contact area.
CONTACT_REFERENCE_AREA_UM2 = 25.0
VIAS = [
    ("CNT_TIN", "TIN", "ML1", 0.9 * CONTACT_REFERENCE_AREA_UM2),
    ("CNT_NAREA", "NAREA", "ML1", 150.0 * CONTACT_REFERENCE_AREA_UM2),
    ("CNT_PAREA", "PAREA", "ML1", 200.0 * CONTACT_REFERENCE_AREA_UM2),
]

# PEX does not emit device capacitance. LVS owns explicit CAP windows, and the
# MOS model owns intrinsic gate/channel charge.

# The return terminal is an explicit modeling contract, not a measured
# terminal identification (audit_box_capacitance.md): ideal BOX parallel plate
# eps0*epsr/tBOX = 0.086327 fF/um^2 matches the measured 0.086 fF/um^2 within
# displayed rounding, so BOX->SUB is analytically supported but
# MEASUREMENT_UNCONFIRMED.  For eligible passive diffusion only; the area term
# is attributed, no perimeter rule exists.
CAP_TO_SUB = {
    "NAREA": 0.086 * F_PER_UM2,
    "PAREA": 0.086 * F_PER_UM2,
}

CONDUCTORS = ["TIN", "ML1", "NAREA", "PAREA"]


def layer_id(spec):
    """Generic conductor id used inside the extractor: layer<<8 | datatype."""
    return (spec[0] << 8) | spec[1]


def point_box(pt):
    """Small, non-degenerate box around a point (database units)."""
    return pya.Box(pt.x - 1, pt.y - 1, pt.x + 1, pt.y + 1)


def contains_point(region, pt):
    return not region.is_empty() and region.interacting(pya.Region(point_box(pt))).count() > 0


def dbox_to_point(loc, dbu):
    """RNode#location is a DBox in micron; convert to a database-unit Point."""
    c = loc.center()
    return pya.Point(int(round(c.x / dbu)), int(round(c.y / dbu)))


def poly_centroid(poly):
    b = poly.bbox()
    return pya.Point((b.left + b.right) // 2, (b.bottom + b.top) // 2)


def region_polygons(region):
    """Connected parts of a region as a list of polygons."""
    out = []
    for poly in region.merged().each():
        out.append(poly)
    return out

def voronoi_area_weights(region, node_points):
    """Partition region area among located nodes using bounded Euclidean cells."""
    if region.is_empty() or not node_points:
        return {}

    sites = {}
    for object_id, point in node_points:
        sites.setdefault((point.x, point.y), []).append(object_id)
    bounds = region.bbox()
    locations = sorted(sites)

    def clip_half_plane(vertices, site, other):
        nx = 2.0 * (other[0] - site[0])
        ny = 2.0 * (other[1] - site[1])
        limit = other[0] ** 2 + other[1] ** 2 - site[0] ** 2 - site[1] ** 2
        clipped = []
        for start, end in zip(vertices, vertices[1:] + vertices[:1]):
            start_value = nx * start[0] + ny * start[1] - limit
            end_value = nx * end[0] + ny * end[1] - limit
            start_inside = start_value <= 0.0
            end_inside = end_value <= 0.0
            if start_inside and end_inside:
                clipped.append(end)
            elif start_inside != end_inside:
                fraction = start_value / (start_value - end_value)
                intersection = (
                    start[0] + fraction * (end[0] - start[0]),
                    start[1] + fraction * (end[1] - start[1]),
                )
                clipped.append(intersection)
                if end_inside:
                    clipped.append(end)
        return clipped

    raw_area_by_site = {}
    if len(locations) == 1:
        raw_area_by_site[locations[0]] = region.area()
    else:
        rectangle = [
            (bounds.left, bounds.bottom),
            (bounds.right, bounds.bottom),
            (bounds.right, bounds.top),
            (bounds.left, bounds.top),
        ]
        for site in locations:
            vertices = rectangle
            for other in locations:
                if other != site:
                    vertices = clip_half_plane(vertices, site, other)
                    if len(vertices) < 3:
                        break
            points = []
            for x, y in vertices:
                point = (int(round(x)), int(round(y)))
                if not points or point != points[-1]:
                    points.append(point)
            if len(points) > 1 and points[0] == points[-1]:
                points.pop()
            if len(set(points)) < 3:
                raw_area_by_site[site] = 0
                continue
            cell = pya.Region(
                pya.Polygon([pya.Point(x, y) for x, y in points])
            )
            raw_area_by_site[site] = (region & cell).area()

    positive_sites = [
        (site, raw_area)
        for site, raw_area in sorted(raw_area_by_site.items())
        if raw_area > 0
    ]
    raw_total = sum(raw_area for _site, raw_area in positive_sites)
    if raw_total <= 0:
        raise RuntimeError("failed to partition eligible diffusion area among PEX nodes")

    region_area = region.area()
    entries = [
        (object_id, raw_area / len(sites[site]))
        for site, raw_area in positive_sites
        for object_id in sorted(sites[site])
    ]
    weights = {}
    assigned_area = 0.0
    for index, (object_id, raw_area) in enumerate(entries):
        area = (
            region_area - assigned_area
            if index == len(entries) - 1
            else region_area * raw_area / raw_total
        )
        weights[object_id] = area
        assigned_area += area
    return weights


def carve_mos_terminal_access(
    gate_region, active_region, contact_region, dbu, layer_name,
    other_active_region=None, unclassified_gate_region=None,
):
    """Carve bounded MOS access and fail closed on every unresolved gate."""
    gate_polygons = region_polygons(gate_region)
    unclassified_gate_polygons = region_polygons(
        unclassified_gate_region if unclassified_gate_region is not None
        else pya.Region()
    )
    active_polygons = region_polygons(active_region)
    active_components = [pya.Region(poly) for poly in active_polygons]
    contacts = []
    for poly in contact_region.each():
        cut_region = pya.Region(poly)
        component_ids = tuple(
            index
            for index, component in enumerate(active_components)
            if not (cut_region & component).is_empty()
        )
        if component_ids:
            contacts.append((poly, component_ids))

    access_region = pya.Region()
    mos_contact_region = pya.Region()
    bounded = 0
    ambiguous = 0
    orphaned = 0
    considered = 0
    failures = []
    associated_contacts = 0
    terminal_records = []
    for gate in gate_polygons:
        gate_box = gate.bbox()
        gate_bounds = [gate_box.left * dbu, gate_box.bottom * dbu,
                       gate_box.right * dbu, gate_box.top * dbu]
        gate_halo = pya.Region(gate).sized(1)
        local_component_ids = {
            index
            for index, component in enumerate(active_components)
            if not (component & gate_halo).is_empty()
        }
        if not local_component_ids:
            if (other_active_region is not None
                    and not (other_active_region & gate_halo).is_empty()):
                continue
            considered += 1
            orphaned += 1
            failures.append({"gate_bbox_um": gate_bounds,
                             "reason": "no_associated_active_component"})
            continue
        considered += 1

        local_active = pya.Region()
        for index in local_component_ids:
            local_active.insert(active_polygons[index])
        local_contacts = [
            poly
            for poly, component_ids in contacts
            if not local_component_ids.isdisjoint(component_ids)
        ]
        associated_contacts += len(local_contacts)

        options = []
        for axis in ("x", "y"):
            negative = []
            positive = []
            for contact in local_contacts:
                contact_box = contact.bbox()
                if axis == "x":
                    overlap = min(gate_box.top, contact_box.top) - max(
                        gate_box.bottom, contact_box.bottom
                    )
                    if overlap <= 0:
                        continue
                    if contact_box.right <= gate_box.left:
                        negative.append((gate_box.left - contact_box.right, contact))
                    elif contact_box.left >= gate_box.right:
                        positive.append((contact_box.left - gate_box.right, contact))
                else:
                    overlap = min(gate_box.right, contact_box.right) - max(
                        gate_box.left, contact_box.left
                    )
                    if overlap <= 0:
                        continue
                    if contact_box.top <= gate_box.bottom:
                        negative.append((gate_box.bottom - contact_box.top, contact))
                    elif contact_box.bottom >= gate_box.top:
                        positive.append((contact_box.bottom - gate_box.top, contact))

            if negative and positive:
                near_negative = min(negative, key=lambda item: item[0])
                near_positive = min(positive, key=lambda item: item[0])
                unique = (sum(gap == near_negative[0] for gap, _ in negative) == 1
                          and sum(gap == near_positive[0] for gap, _ in positive) == 1)
                # Parallel CNT cut counts per terminal side: every cut on the
                # same side of the gate over the same active component is a
                # parallel access path.  The assembler uses them as N_cnt in
                # R_cnt = R_unit / N_cnt (AGENTS.md §4.2 contact decoupling).
                options.append((near_negative[0] + near_positive[0], axis,
                                near_negative[1], near_positive[1], unique,
                                len(negative), len(positive)))

        if not options:
            failures.append({"gate_bbox_um": gate_bounds, "reason": "unbounded"})
            continue
        options.sort(key=lambda item: item[0])
        if not options[0][4] or (len(options) > 1 and options[0][0] == options[1][0]):
            ambiguous += 1
            failures.append({"gate_bbox_um": gate_bounds, "reason": "ambiguous"})
            continue

        (_gap, axis, negative_contact, positive_contact, _unique,
         negative_cuts, positive_cuts) = options[0]
        negative_box = negative_contact.bbox()
        positive_box = positive_contact.bbox()
        corridor = pya.Region()
        if axis == "x":
            if negative_box.left < gate_box.left:
                corridor.insert(
                    pya.Box(
                        negative_box.left,
                        gate_box.bottom,
                        gate_box.left,
                        gate_box.top,
                    )
                )
            if positive_box.right > gate_box.right:
                corridor.insert(
                    pya.Box(
                        gate_box.right,
                        gate_box.bottom,
                        positive_box.right,
                        gate_box.top,
                    )
                )
        else:
            if negative_box.bottom < gate_box.bottom:
                corridor.insert(
                    pya.Box(
                        gate_box.left,
                        negative_box.bottom,
                        gate_box.right,
                        gate_box.bottom,
                    )
                )
            if positive_box.top > gate_box.top:
                corridor.insert(
                    pya.Box(
                        gate_box.left,
                        gate_box.top,
                        gate_box.right,
                        positive_box.top,
                    )
                )
        other_gates = gate_region - pya.Region(gate)
        if not (corridor & other_gates).is_empty():
            failures.append({"gate_bbox_um": gate_bounds,
                             "reason": "terminal_corridor_crosses_another_gate"})
            continue
        mos_contact_region.insert(negative_contact)
        mos_contact_region.insert(positive_contact)
        active_box = local_active.bbox()
        gate_center = pya.Point(
            (gate_box.left + gate_box.right) // 2,
            (gate_box.bottom + gate_box.top) // 2,
        )
        terminal_records.append(
            {
                "axis": axis,
                "device_x": gate_center.x,
                "device_y": (active_box.bottom + active_box.top) // 2,
                "gate_center": gate_center,
                "negative_contact": negative_contact,
                "positive_contact": positive_contact,
                "negative_cuts": negative_cuts,
                "positive_cuts": positive_cuts,
            }
        )
        for poly in region_polygons(corridor & local_active):
            access_region.insert(poly)
        bounded += 1

    for gate in unclassified_gate_polygons:
        box = gate.bbox()
        considered += 1
        orphaned += 1
        failures.append(
            {
                "gate_bbox_um": [
                    box.left * dbu, box.bottom * dbu,
                    box.right * dbu, box.top * dbu,
                ],
                "reason": "no_associated_active_component",
            }
        )

    access_region = access_region & active_region
    print(
        "MOS access %s: gates=%d bounded=%d unbounded=%d ambiguous=%d "
        "orphaned=%d contacts=%d"
        % (
            layer_name,
            considered,
            bounded,
            considered - bounded - ambiguous - orphaned,
            ambiguous,
            orphaned,
            associated_contacts,
        )
    )
    summary = {"layer": layer_name, "gates": considered, "bounded": bounded,
               "unbounded": considered - bounded - ambiguous - orphaned,
               "ambiguous": ambiguous, "orphaned": orphaned,
               "failures": failures,
               "passed": not failures}
    print("MOS_ACCESS_RESULT " + json.dumps(summary, sort_keys=True))
    print(
        "MOS terminal contact cuts %s: selected=%d"
        % (layer_name, len(region_polygons(mos_contact_region)))
    )
    remaining = active_region - access_region
    for index, poly in enumerate(region_polygons(active_region), 1):
        component = pya.Region(poly)
        removed = component & access_region
        if removed.is_empty():
            continue
        component_remaining = component - access_region
        print(
            "MOS access %s component=%d area_before=%.6f um2 "
            "area_removed=%.6f um2 area_remaining=%.6f um2 "
            "components_before=1 components_after=%d"
            % (
                layer_name,
                index,
                component.area() * dbu * dbu,
                removed.area() * dbu * dbu,
                component_remaining.area() * dbu * dbu,
                len(region_polygons(component_remaining)),
            )
        )
    return remaining, mos_contact_region, terminal_records, summary


def main():
    ap = argparse.ArgumentParser(description="ICPS2023_5 KLayout PEX")
    ap.add_argument("--input", required=True)
    ap.add_argument("--top")
    ap.add_argument("--output")
    ap.add_argument("--layer-mode", default="design", choices=["design", "fab"])
    ap.add_argument("--no-capacitance", action="store_true")
    ap.add_argument("--process-profile", help="process profile (default: configured 1M profile)")
    ap.add_argument("--simplify", action="store_true", default=True)
    args = ap.parse_args()
    profile_name, process_profile = resolve_process_profile(args.process_profile)

    layers = DESIGN if args.layer_mode == "design" else FAB
    output = args.output or os.path.basename(args.input).rsplit(".", 1)[0] + "_pex.spice"

    layout = pya.Layout()
    layout.read(args.input)
    if args.top:
        cell = layout.cell(args.top)
        if cell is None:
            sys.exit("cell '%s' not found in %s" % (args.top, args.input))
    else:
        tops = layout.top_cells()
        if len(tops) != 1:
            sys.exit("%s has %d top cells: pass --top CELL" % (args.input, len(tops)))
        cell = tops[0]
    print("ICPS2023_5 PEX  layer_mode=%s  profile=%s  top=%s  dbu=%g" %
          (args.layer_mode, profile_name, cell.name, layout.dbu))

    def raw_region(spec):
        li = layout.find_layer(pya.LayerInfo(*spec))
        if li is None:
            return pya.Region()
        return pya.Region(cell.begin_shapes_rec(li))

    exclude = raw_region((63, 63))
    mask = raw_region((20, 0)) if args.layer_mode == "design" else pya.Region()

    def region(name):
        return raw_region(layers[name]) - exclude - mask

    upper_layers = PROFILE_DATA["upper_metal_layers"][args.layer_mode]
    unsupported = [
        name for name, spec in upper_layers.items()
        if not (raw_region(spec) - exclude - mask).is_empty()
    ]
    if unsupported:
        if process_profile["permitted_upper_layers"]:
            reason = "has no qualified extraction model for upper-metal geometry"
        else:
            reason = "process profile %s forbids upper-metal geometry" % profile_name
        sys.exit("ICPS2023_5 PEX %s: %s" % (reason, ", ".join(unsupported)))

    # Geometry ownership --------------------------------------------------
    # MOS owns each gate-to-contact diffusion corridor, including the CNT cut;
    # PEX owns diffusion beyond the outer edge of that terminal contact. This
    # keeps fitted RDSW/access out of the additive network without discarding
    # an entire connected external diffusion wire.
    diff = region("DIFF")
    design_tin = region("TIN")
    tin_blk = region("TINBLK")
    ml1 = region("ML1")
    cap = region("CAP")
    res = region("RES")
    dio = region("DIO")
    pwl = region("PWL")
    raw_contacts = region("CNT")
    base_narea = region("NAREA")
    base_parea = region("PAREA")
    raw_narea = base_narea & diff
    raw_parea = base_parea & diff

    # Match fab prep exactly in design mode: only gate TiN is expanded, while
    # the original design TiN determines where the dopant masks were cut back.
    if args.layer_mode == "design":
        um2dbu = int(round(2.0 / layout.dbu))
        um4dbu = int(round(4.0 / layout.dbu))
        prep_gate = design_tin.interacting(diff) - (design_tin & cap.sized(um4dbu))
        process_bounds = (diff + prep_gate).extents()
        tin = design_tin + (prep_gate.sized(um2dbu) & process_bounds) + tin_blk
        dio2 = dio.sized(um2dbu) - (base_parea + base_narea)
        design_n_dope = (base_narea & diff).sized(um2dbu) - (
            design_tin - cap + dio2
        )
        design_p_dope = (base_parea & diff).sized(um2dbu) - (
            design_tin - cap + dio2
        )
        mos_active = diff - res - cap - dio
        n_channel_gap = (raw_narea & mos_active) - design_n_dope
        p_channel_gap = (raw_parea & mos_active) - design_p_dope
        # Drawing implants are intent regions, not conductors through a gate or
        # undoped neck. Use the same final dopant domains as fab-prep and LVS.
        raw_narea = design_n_dope & diff
        raw_parea = design_p_dope & diff
    else:
        tin = design_tin
        mos_active = diff - res - cap - dio

        def active_components_overlapping(marker):
            selected = pya.Region()
            for poly in region_polygons(mos_active):
                component = pya.Region(poly)
                if not (component & marker).is_empty():
                    selected.insert(poly)
            return selected

        n_channel_gap = active_components_overlapping(raw_narea) - raw_narea
        p_channel_gap = active_components_overlapping(raw_parea) - raw_parea
    # A CNT cannot select one side of a PN boundary by extractor priority.
    # Reject ambiguous/undoped SOI landings before they create graph edges.
    for cut_poly in raw_contacts.each():
        cut = pya.Region(cut_poly)
        if (cut & diff).is_empty():
            continue
        domains = [active for active in (raw_narea, raw_parea)
                   if not (cut & active).is_empty()]
        if (not (cut & tin).is_empty() or len(domains) != 1 or
                not (cut - diff).is_empty() or
                not (cut - domains[0]).is_empty()):
            sys.exit("ICPS2023_5 PEX: CNT has no unique fully implanted SOI landing")

    for polarity, gap in (
        ("NMOS", n_channel_gap),
        ("PMOS", p_channel_gap),
    ):
        for poly in region_polygons(gap):
            channel_piece = pya.Region(poly)
            if (channel_piece & tin).is_empty():
                continue
            if not (channel_piece - tin).is_empty():
                sys.exit(
                    "ICPS2023_5 PEX: %s dopant gap is not fully covered by final TiN"
                    % polarity
                )
    mos_n_gate = n_channel_gap & tin
    mos_p_gate = p_channel_gap & tin

    # NMOS4 body ties: split the tie gate out of the ordinary NMOS set and
    # require one contacted P+ dot. Bounded support: exactly one tie gate and
    # one contacted dot per layout; anything else fails closed.
    tie_dots = ((pwl & raw_parea & diff) - tin).merged()
    contacted_dots = tie_dots.interacting(raw_contacts)
    if args.layer_mode == "design":
        bt_gate = mos_n_gate & design_tin.interacting(tin_blk)
    else:
        contacted_paths = pwl.interacting(contacted_dots)
        bt_gate = mos_n_gate.interacting(contacted_paths)
        if not (tie_dots - contacted_dots).is_empty():
            sys.exit("ICPS2023_5 PEX: N-ch body path carries an uncontacted P+ tie dot")
    tie_route = pwl - (design_tin & diff) if args.layer_mode == "design" else pwl - (tin & diff)
    if not tie_route.is_empty() and (bt_gate.is_empty() or
                                    len(region_polygons(contacted_dots)) != 1 or
                                    len(region_polygons(bt_gate)) != 1):
        sys.exit("ICPS2023_5 PEX: N-channel body-tie route is not a bounded single NMOS4 tie")
    if bt_gate.is_empty() and not contacted_dots.is_empty():
        sys.exit("ICPS2023_5 PEX: contacted P+ tie dot has no NMOS4 gate")
    if not bt_gate.is_empty() and len(region_polygons(contacted_dots)) != 1:
        sys.exit("ICPS2023_5 PEX: NMOS4 body-tie gate has no unique contacted P+ tie dot")
    body_dot = None
    if not bt_gate.is_empty():
        body_dot = pya.Region(region_polygons(contacted_dots)[0])
    mos_n_gate_ordinary = mos_n_gate - bt_gate

    # PMOS body ties share the LVS physical signature: a contacted N+ dot inside
    # a PMOS island. PMOS4 remains excluded; only NMOS4 gains a P terminal.
    if not (tin_blk & raw_parea).is_empty():
        sys.exit("ICPS2023_5 PEX: PMOS body-tie geometry is unsupported")
    pmos_islands = mos_active.interacting(raw_parea)
    if not ((raw_narea & mos_active) & pmos_islands).is_empty():
        sys.exit("ICPS2023_5 PEX: PMOS body-tie geometry is unsupported")

    tin_res_body = (tin & res) - cap - diff
    tin_res_anchors = tin_res_body & raw_contacts
    tin_wire = (tin - tin_res_body) + tin_res_anchors
    unclassified_gate = (mos_active & tin) - (
        raw_narea + raw_parea + mos_n_gate + mos_p_gate
    )
    narea, mos_n_contacts, n_mos_devices, n_access = carve_mos_terminal_access(
        mos_n_gate_ordinary, raw_narea - mos_n_gate, raw_contacts, layout.dbu, "NAREA",
        other_active_region=raw_parea - mos_n_gate,
        unclassified_gate_region=unclassified_gate,
    )
    bt_narea, bt_contacts, bt_devices, bt_access = carve_mos_terminal_access(
        bt_gate, raw_narea - bt_gate, raw_contacts, layout.dbu, "NAREA",
        other_active_region=raw_parea - bt_gate,
        unclassified_gate_region=pya.Region(),
    ) if not bt_gate.is_empty() else (pya.Region(), pya.Region(), [], None)
    parea, mos_p_contacts, p_mos_devices, p_access = carve_mos_terminal_access(
        mos_p_gate, raw_parea - mos_p_gate, raw_contacts, layout.dbu, "PAREA",
        other_active_region=raw_narea - mos_p_gate,
        unclassified_gate_region=unclassified_gate,
    )
    access_reports = [n_access, p_access] + ([bt_access] if bt_access else [])
    failed_access = [summary["layer"] for summary in access_reports
                     if not summary["passed"]]
    if failed_access:
        sys.exit("ICPS2023_5 PEX: MOS terminal access is not bounded for %s"
                 % ", ".join(failed_access))
    mos_devices = []
    for polarity, bottom, records in (
        ("NMOS", "NAREA", n_mos_devices),
        ("PMOS", "PAREA", p_mos_devices),
        ("NMOS4", "NAREA", bt_devices),
    ):
        for record in records:
            record["polarity"] = polarity
            record["bottom"] = bottom
            if polarity == "NMOS4":
                body_center = body_dot.bbox().center()
                record["body_center"] = body_center
            record["device"] = "%s@%.6f,%.6f" % (
                polarity,
                record["device_x"] * layout.dbu,
                record["device_y"] * layout.dbu,
            )
            mos_devices.append(record)
    mos_devices.sort(key=lambda record: record["device"])
    if len({record["device"] for record in mos_devices}) != len(mos_devices):
        sys.exit("duplicate MOS device geometry keys in PEX terminal map")
    for index, record in enumerate(mos_devices, 1):
        record["gate_port"] = "MOS_GATE_%04d" % index
        if record["polarity"] == "NMOS4":
            record["body_port"] = "MOS_BODY_%04d" % index
    narea = narea + bt_narea
    res_active = {
        "NAREA": raw_narea & res,
        "PAREA": raw_parea & res,
    }
    cap_active = {
        "NAREA": raw_narea & cap & tin,
        "PAREA": raw_parea & cap & tin,
    }
    diode_active = {
        "NAREA": raw_narea & dio,
        "PAREA": raw_parea & dio,
    }
    narea = narea - res_active["NAREA"]
    parea = parea - res_active["PAREA"]

    def polygon_key(poly):
        box = poly.bbox()
        return box.left, box.bottom, box.right, box.top

    # R_TIN is LVS-owned. Keep only the real CNT-backed end footprints in the
    # PEX TIN network so its measured contacts remain represented, while the
    # marked sheet-resistance body cannot appear in parallel.
    tin_res_device_candidates = []
    for body_index, body_poly in enumerate(region_polygons(tin_res_body), 1):
        body_region = pya.Region(body_poly)
        terminal_cuts = []
        for cut_poly in raw_contacts.each():
            cut_region = pya.Region(cut_poly)
            if (cut_region & body_region & ml1).is_empty():
                continue
            terminal_cuts.append(
                {"polygon": cut_poly, "key": polygon_key(cut_poly),
                 "point": poly_centroid(cut_poly)}
            )
        if len(terminal_cuts) != 2:
            box = body_poly.bbox()
            sys.exit(
                "ICPS2023_5 PEX: R_TIN body %d needs exactly two CNT/ML1 "
                "terminal heads; found %d at [%g,%g,%g,%g] um"
                % (body_index, len(terminal_cuts), box.left * layout.dbu,
                   box.bottom * layout.dbu, box.right * layout.dbu,
                   box.top * layout.dbu)
            )
        terminal_cuts.sort(key=lambda item: item["key"])
        box = body_poly.bbox()
        tin_res_device_candidates.append(
            {
                "device": "R_TIN@%.6f,%.6f" % (
                    ((box.left + box.right) / 2.0) * layout.dbu,
                    ((box.bottom + box.top) / 2.0) * layout.dbu,
                ),
                "center_x": (box.left + box.right) // 2,
                "center_y": (box.bottom + box.top) // 2,
                "terminals": terminal_cuts,
            }
        )

    mos_terminal_contacts = pya.Region()
    for terminal_contacts in (mos_n_contacts, mos_p_contacts):
        for poly in terminal_contacts.each():
            mos_terminal_contacts.insert(poly)
    # Design TEXT is 13/0. Fab prep preserves labels as 13/1 so they remain
    # distinct from the N-channel mask on 13/0.
    labels = []
    if "TEXT" in layers:
        li_text = layout.find_layer(pya.LayerInfo(*layers["TEXT"]))
        if li_text is not None:
            it = cell.begin_shapes_rec(li_text)
            while not it.at_end():
                sh = it.shape()
                if sh.is_text():
                    t = sh.text
                    labels.append((t.string, it.trans() * t.position()))
                it.next()
    # N/P diffusion outside uniquely bounded MOS access and explicit RES bodies
    # remains PEX-owned. DIO access remains PEX-owned because diode series
    # resistance is not characterized.
    conductor_region = {
        "TIN": tin_wire,
        "ML1": ml1,
        "NAREA": narea,
        "PAREA": parea,
    }

    via_regions = {cut: pya.Region() for cut, _bottom, _top, _res in VIAS}
    for poly in raw_contacts.each():
        cut_poly = pya.Region(poly)
        if not (cut_poly & mos_terminal_contacts).is_empty():
            continue
        for cut, bottom, top, _res in VIAS:
            contact = cut_poly & conductor_region[bottom] & conductor_region[top]
            if contact.is_empty():
                continue
            for contact_poly in region_polygons(contact):
                via_regions[cut].insert(contact_poly)
            break

    mos_terminal_ties = []
    mos_tie_counts = {"NAREA": 0, "PAREA": 0}
    seen_mos_tie_contacts = set()
    for bottom, terminal_contacts in (
        ("NAREA", mos_n_contacts + bt_contacts),
        ("PAREA", mos_p_contacts),
    ):
        for poly in terminal_contacts.each():
            box=poly.bbox()
            contact_key=(bottom,box.left,box.bottom,box.right,box.top)
            if contact_key in seen_mos_tie_contacts:
                # One physical active contact can be discovered from multiple
                # MOS terminal regions in a shared diffusion island.
                continue
            seen_mos_tie_contacts.add(contact_key)
            expanded_cut = pya.Region(poly).sized(
                MOS_TERMINAL_TIE_EXTENSION_DBU
            )
            bottom_anchors = region_polygons(
                expanded_cut & conductor_region[bottom]
            )
            top_anchors = region_polygons(
                expanded_cut & conductor_region["ML1"]
            )
            if not bottom_anchors and not top_anchors:
                sys.exit("MOS terminal contact has no available conductor anchor")
            mos_terminal_ties.append(
                {
                    "bottom": bottom,
                    "contact": poly,
                    "bottom_anchors": bottom_anchors,
                    "top_anchors": top_anchors,
                }
            )
            mos_tie_counts[bottom] += 1
    mos_terminal_ties.sort(
        key=lambda tie: (
            tie["bottom"],
            tie["contact"].bbox().left,
            tie["contact"].bbox().bottom,
            tie["contact"].bbox().right,
            tie["contact"].bbox().top,
        )
    )
    print(
        "MOS terminal ideal ties: NAREA=%d PAREA=%d"
        % (mos_tie_counts["NAREA"], mos_tie_counts["PAREA"])
    )

    # A physical text terminal is a (label, conductor, anchor) tuple. Keep
    # separate anchors separate even when their logical label is identical.
    label_keys = set()
    label_records = []
    for logical, point in labels:
        if not logical:
            continue
        for layer_name in sorted(conductor_region):
            if not contains_point(conductor_region[layer_name], point):
                continue
            key = (logical, layer_name, point.x, point.y)
            if key in label_keys:
                continue
            label_keys.add(key)
            label_records.append(key)
    label_records.sort()
    physical_labels = [
        {
            "physical": "PORT%04d" % (index + 1),
            "logical": logical,
            "layer": layer_name,
            "point": pya.Point(x, y),
        }
        for index, (logical, layer_name, x, y) in enumerate(label_records)
    ]

    # ---------------------------------------------------------------- ports
    # Ports are points where external text terminals or typed CNT contacts
    # meet an extracted conductor.
    ports = {layer_id(layers[n]): [] for n in conductor_region}
    port_names = {}

    def add_port(name, layer_name, point):
        lid = layer_id(layers[layer_name])
        lst = ports.setdefault(lid, [])
        lst.append(point)
        port_names[(lid, len(lst) - 1)] = name

    # Each CNT cut is assigned to one conductor pair. Stable names follow
    # sorted contact geometry within each lower conductor.

    via_port_name_by_key = {}
    for cut, bottom, _top, _r in VIAS:
        for index, poly in enumerate(
            sorted(region_polygons(via_regions[cut]), key=polygon_key), 1
        ):
            name = "C_%s_%04d" % (bottom, index)
            add_port(name, bottom, poly_centroid(poly))
            via_port_name_by_key[(cut, polygon_key(poly))] = name
    for candidate in tin_res_device_candidates:
        terminal_ports = []
        for terminal in candidate["terminals"]:
            contact = (
                pya.Region(terminal["polygon"])
                & conductor_region["TIN"]
                & conductor_region["ML1"]
            )
            contact_polygons = region_polygons(contact)
            names = {
                via_port_name_by_key.get(("CNT_TIN", polygon_key(poly)))
                for poly in contact_polygons
            }
            names.discard(None)
            if len(contact_polygons) != 1 or len(names) != 1:
                sys.exit(
                    "ICPS2023_5 PEX: R_TIN terminal head is not a unique CNT_TIN "
                    "network anchor for %s" % candidate["device"]
                )
            terminal_ports.append(next(iter(names)))
        if len(set(terminal_ports)) != 2:
            sys.exit(
                "ICPS2023_5 PEX: R_TIN terminal heads collapse to one PEX node for %s"
                % candidate["device"]
            )
        candidate["terminal_ports"] = terminal_ports
    mos_tie_port_groups = []
    mos_tie_terminal_ports = {}
    mos_tie_ids = {}
    for tie_index, tie in enumerate(mos_terminal_ties, 1):
        bottom = tie["bottom"]
        group = []
        bottom_ports = []
        for side, layer_name, anchors in (
            ("BOTTOM", bottom, tie["bottom_anchors"]),
            ("TOP", "ML1", tie["top_anchors"]),
        ):
            for anchor_index, anchor in enumerate(anchors, 1):
                name = "MOS_TIE_%s_%04d_%s_%04d" % (
                    bottom,
                    tie_index,
                    side,
                    anchor_index,
                )
                add_port(name, layer_name, poly_centroid(anchor))
                group.append(name)
                if side == "BOTTOM":
                    bottom_ports.append(name)
        if not group:
            sys.exit("MOS terminal tie has no physical conductor anchor")
        tie_id = "MOS_TIE_%s_%04d" % (bottom, tie_index)
        contact_key = (bottom, polygon_key(tie["contact"]))
        if contact_key in mos_tie_ids:
            sys.exit("duplicate MOS contact ownership for %s" % (contact_key,))
        mos_tie_ids[contact_key] = tie_id
        mos_tie_terminal_ports[tie_id] = bottom_ports[0] if bottom_ports else group[0]
        mos_tie_port_groups.append(group)
    for record in mos_devices:
        if not contains_point(tin, record["gate_center"]):
            sys.exit("MOS gate center is outside TIN for %s" % record["device"])
        add_port(record["gate_port"], "TIN", record["gate_center"])
        if record["polarity"] == "NMOS4":
            add_port(record["body_port"], "PAREA", record["body_center"])
    for record in physical_labels:
        add_port(record["physical"], record["layer"], record["point"])

    n_ports = sum(len(v) for v in ports.values())
    print("ports: %d (%d named physical terminals)" % (n_ports, len(physical_labels)))
    if n_ports == 0:
        sys.exit("no ports found - nothing to extract")

    # ------------------------------------------------------------ R extract
    tech = pex.RExtractorTech()
    for name in CONDUCTORS:
        if name not in conductor_region:
            continue
        cond = pex.RExtractorTechConductor()
        cond.layer = layer_id(layers[name])
        cond.resistance = OHM_PER_SQ[name]
        cond.algorithm = pex.Algorithm.SquareCounting
        tech.add_conductor(cond)
    for cut, bottom, top, area_resistance in VIAS:
        if bottom not in conductor_region or top not in conductor_region:
            continue
        via = pex.RExtractorTechVia()
        via.cut_layer = layer_id(CUT_LAYERS[cut])
        via.bottom_conductor = layer_id(layers[bottom])
        via.top_conductor = layer_id(layers[top])
        via.resistance = area_resistance
        tech.add_via(via)

    geo = {layer_id(layers[n]): r for n, r in conductor_region.items()}
    geo.update({layer_id(CUT_LAYERS[cut]): r for cut, r in via_regions.items()})
    vertex_ports = {k: v for k, v in ports.items() if v}
    net = pex.RNetExtractor(layout.dbu).extract(tech, geo, vertex_ports, {})
    if net is None:
        sys.exit("R extraction failed")
    net.simplify()

    node_name = {}
    port_node = {}
    for node in net.each_node():
        if str(node.type()) == "VertexPort":
            lid, idx = node.layer(), node.port_index()
            nname = port_names.get((lid, idx))
            if nname is None:
                pt = dbox_to_point(node.location(), layout.dbu)
                nname = "x_%d_%d_%d_%d" % (lid, idx, pt.x, pt.y)
            else:
                port_node[nname] = node.object_id()
        else:
            nname = "n%d" % node.object_id()
        node_name[node.object_id()] = nname

    ideal_parent = {object_id: object_id for object_id in node_name}

    def ideal_find(object_id):
        while ideal_parent[object_id] != object_id:
            ideal_parent[object_id] = ideal_parent[ideal_parent[object_id]]
            object_id = ideal_parent[object_id]
        return object_id

    def ideal_union(left, right):
        left_root = ideal_find(left)
        right_root = ideal_find(right)
        if left_root != right_root:
            ideal_parent[right_root] = left_root

    for group in mos_tie_port_groups:
        missing = [name for name in group if name not in port_node]
        if missing:
            sys.exit("MOS terminal tie ports missing from R network: %s" % missing)
        object_ids = [port_node[name] for name in group]
        for object_id in object_ids[1:]:
            ideal_union(object_ids[0], object_id)
    all_elements = list(net.each_element())
    zero_resistor_count = 0
    for element in all_elements:
        if element.resistance() == 0.0:
            ideal_union(
                element.a().object_id(),
                element.b().object_id(),
            )
            zero_resistor_count += 1

    canonical_names = {}
    for object_id, name in node_name.items():
        root = ideal_find(object_id)
        priority = 0 if name.startswith("PORT") else 1 if name.startswith("C_") else 2
        candidate = (priority, name)
        if root not in canonical_names or candidate < canonical_names[root]:
            canonical_names[root] = candidate
    for object_id in node_name:
        node_name[object_id] = canonical_names[ideal_find(object_id)][1]
    for record in physical_labels:
        object_id = port_node.get(record["physical"])
        if object_id is None:
            sys.exit(
                "physical terminal missing from R network: %s" % record["physical"]
            )
        record["physical"] = node_name[object_id]

    def mapped_port_node(port_name):
        object_id = port_node.get(port_name)
        if object_id is None:
            sys.exit("MOS_MAP port missing from R network: %s" % port_name)
        return node_name[object_id]
    tin_res_map_records = []
    for candidate in tin_res_device_candidates:
        endpoints = []
        for terminal, port_name in zip(candidate["terminals"], candidate["terminal_ports"]):
            point = terminal["point"]
            endpoints.append(
                {
                    "node": mapped_port_node(port_name),
                    "x_um": round(point.x * layout.dbu, 6),
                    "y_um": round(point.y * layout.dbu, 6),
                }
            )
        tin_res_map_records.append(
            {
                "device": candidate["device"],
                "x_um": round(candidate["center_x"] * layout.dbu, 6),
                "y_um": round(candidate["center_y"] * layout.dbu, 6),
                "endpoints": endpoints,
            }
        )

    mos_map_records = []
    mapped_contact_ids = set()
    for record in mos_devices:
        bottom = record["bottom"]
        source_key = (bottom, polygon_key(record["negative_contact"]))
        drain_key = (bottom, polygon_key(record["positive_contact"]))
        source_contact = mos_tie_ids.get(source_key)
        drain_contact = mos_tie_ids.get(drain_key)
        if source_contact is None or drain_contact is None:
            sys.exit("MOS terminal contact is missing a PEX tie for %s" % record["device"])
        mapped_contact_ids.update((source_contact, drain_contact))
        mapping_record = {
            "device": record["device"],
            "polarity": record["polarity"],
            "axis": record["axis"],
            "drain_side": "positive",
            "drain": mapped_port_node(mos_tie_terminal_ports[drain_contact]),
            "drain_contact": drain_contact,
            "drain_contact_cuts": record["positive_cuts"],
            "gate": mapped_port_node(record["gate_port"]),
            "gate_x_um": round(record["gate_center"].x * layout.dbu, 6),
            "gate_y_um": round(record["gate_center"].y * layout.dbu, 6),
            "source_side": "negative",
            "source": mapped_port_node(mos_tie_terminal_ports[source_contact]),
            "source_contact": source_contact,
            "source_contact_cuts": record["negative_cuts"],
            "source_x_um": round(poly_centroid(record["negative_contact"]).x * layout.dbu, 6),
            "source_y_um": round(poly_centroid(record["negative_contact"]).y * layout.dbu, 6),
            "drain_x_um": round(poly_centroid(record["positive_contact"]).x * layout.dbu, 6),
            "drain_y_um": round(poly_centroid(record["positive_contact"]).y * layout.dbu, 6),
            "e": "SUB",
            "x_um": round(record["device_x"] * layout.dbu, 6),
            "y_um": round(record["device_y"] * layout.dbu, 6),
        }
        if record["polarity"] == "NMOS4":
            mapping_record["body"] = mapped_port_node(record["body_port"])
        mos_map_records.append(mapping_record)
    unowned_contacts = set(mos_tie_ids.values()) - mapped_contact_ids
    if unowned_contacts:
        sys.exit("MOS terminal ties have no MOS_MAP owner: %s" % sorted(unowned_contacts))


    elements = [element for element in all_elements if element.resistance() != 0.0]
    physical_layer_by_id = {
        layer_id(layers[name]): name for name in ("TIN", "ML1", "NAREA", "PAREA")
    }
    contact_material_by_pair = {}
    for cut, bottom, top, _resistance in VIAS:
        contact_material_by_pair[
            frozenset((layer_id(layers[bottom]), layer_id(layers[top])))
        ] = cut

    def node_location_um(node):
        location = node.location()
        if location is None:
            return None
        center = location.center()
        return [round(center.x, 6), round(center.y, 6)]

    resistor_map_records = []
    for index, element in enumerate(elements, 1):
        a_node, b_node = element.a(), element.b()
        a_layer, b_layer = a_node.layer(), b_node.layer()
        if a_layer == b_layer:
            material = physical_layer_by_id.get(a_layer, "UNKNOWN/MIXED")
        else:
            material = contact_material_by_pair.get(
                frozenset((a_layer, b_layer)), "UNKNOWN/MIXED"
            )
        resistor_map_records.append(
            {
                "name": "RPEX%04d" % index,
                "a": node_name.get(a_node.object_id(), "n%d" % a_node.object_id()),
                "b": node_name.get(b_node.object_id(), "n%d" % b_node.object_id()),
                "resistance_ohm": element.resistance(),
                "material": material,
                "ownership": "pex_network",
                "a_location_um": node_location_um(a_node),
                "b_location_um": node_location_um(b_node),
                "geometry_qualification": (
                    "bounded_sheet_or_contact_edge"
                    if material != "UNKNOWN/MIXED"
                    else "unknown_or_mixed_after_network_simplification"
                ),
            }
        )
    print(
        "MOS terminal graph contractions: %d ties, %d zero-ohm edges"
        % (len(mos_tie_port_groups), zero_resistor_count)
    )
    print("R network: %d nodes, %d resistors" % (net.num_nodes(), len(elements)))

    # ------------------------------------------------------------ C extract
    caps = []          # (node_a, node_b, farad)
    if not args.no_capacitance:
        # 1. conductor components per layer. CAP/RES/DIO device regions are
        #    excluded from this assumed substrate-capacitance area below.
        comps = {}          # layer name -> list of (key, polygon)
        comp_of_cell = {}   # grid index: (layer, cx, cy) -> [key, ...]
        CELL = 10000        # 10 um grid in database units

        substrate_eligible = {
            "NAREA": conductor_region["NAREA"] - diode_active["NAREA"] - cap_active["NAREA"],
            "PAREA": conductor_region["PAREA"] - diode_active["PAREA"] - cap_active["PAREA"],
        }
        for lname, rgn in conductor_region.items():
            comps[lname] = []
            for i, poly in enumerate(region_polygons(rgn)):
                key = (lname, i)
                comps[lname].append((key, poly))
                b = poly.bbox()
                for cx in range(b.left // CELL, b.right // CELL + 1):
                    for cy in range(b.bottom // CELL, b.top // CELL + 1):
                        comp_of_cell.setdefault((lname, cx, cy), []).append(key)
        def component_at(lname, pt):
            """Key of the component of `lname` containing point `pt`."""
            for key in comp_of_cell.get((lname, pt.x // CELL, pt.y // CELL), []):
                poly = dict(comps[lname])[key]
                if pya.Region(poly).interacting(pya.Region(point_box(pt))).count() > 0:
                    return key
            return None

        # 2. merge components that meet through a cut layer (they are the same
        #    electrical node)
        parent = {
            key: key
            for layer_components in comps.values()
            for key, _poly in layer_components
        }

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for cut, bottom, top, _r in VIAS:
            if bottom not in comps or top not in comps:
                continue
            for poly in region_polygons(via_regions[cut]):
                pt = poly_centroid(poly)
                kb = component_at(bottom, pt)
                kt = component_at(top, pt)
                if kb is not None and kt is not None:
                    union(kb, kt)
        for tie in mos_terminal_ties:
            bottom = tie["bottom"]
            tie_components = []
            for lname, anchors in (
                (bottom, tie["bottom_anchors"]),
                ("ML1", tie["top_anchors"]),
            ):
                for anchor in anchors:
                    key = component_at(lname, poly_centroid(anchor))
                    if key is not None:
                        tie_components.append(key)
            if tie_components:
                for key in tie_components[1:]:
                    union(tie_components[0], key)

        # 3. Map located R-network nodes to their merged conductor component.
        component_layer_nodes = {}
        component_node_points = {}
        conductor_by_id = {
            layer_id(spec): name for name, spec in layers.items() if name in comps
        }
        for node in net.each_node():
            if node.location() is None:
                continue
            lname = conductor_by_id.get(node.layer())
            if lname is None:
                continue
            point = dbox_to_point(node.location(), layout.dbu)
            key = component_at(lname, point)
            if key is None:
                continue
            root = find(key)
            object_id = node.object_id()
            component_layer_nodes.setdefault((root, lname), set()).add(object_id)
            component_node_points[object_id] = point

        # 4. Keep each qualified diffusion-area region with its conductor layer.
        substrate_regions = {}
        for lname, layer_components in comps.items():
            cpa = CAP_TO_SUB.get(lname)
            eligible = substrate_eligible.get(lname)
            if cpa is None or eligible is None:
                continue
            for key, poly in layer_components:
                cap_region = pya.Region(poly) & eligible
                if cap_region.is_empty():
                    continue
                region_key = (find(key), lname)
                target = substrate_regions.setdefault(region_key, pya.Region())
                for cap_poly in cap_region.each():
                    target.insert(cap_poly)

        # 5. Preserve density*area while assigning each area element to its
        #    nearest located node on the same diffusion layer.
        cap_node_count = 0
        for region_key in sorted(substrate_regions):
            lname = region_key[1]
            region = substrate_regions[region_key]
            capacitance = region.area() * layout.dbu * layout.dbu * CAP_TO_SUB[lname]
            node_ids = component_layer_nodes.get(region_key, ())
            if capacitance <= 0.0 or not node_ids:
                continue
            node_points = [
                (object_id, component_node_points[object_id])
                for object_id in node_ids
            ]
            area_weights = voronoi_area_weights(region, node_points)
            weight_by_node = {}
            for object_id, area in area_weights.items():
                name = node_name[object_id]
                weight_by_node[name] = weight_by_node.get(name, 0.0) + area
            total_weight = sum(weight_by_node.values())
            if total_weight <= 0.0:
                raise RuntimeError(
                    "no located R-network node owns eligible %s capacitance" % lname
                )
            entries = sorted(
                (name, weight)
                for name, weight in weight_by_node.items()
                if weight > 0.0
            )
            assigned = 0.0
            for index, (name, weight) in enumerate(entries):
                share = (
                    capacitance - assigned
                    if index == len(entries) - 1
                    else capacitance * weight / total_weight
                )
                caps.append((name, "SUB", share))
                assigned += share
                cap_node_count += 1
        print(
            "capacitors: %d (%d R-network nodes carry eligible capacitance)"
            % (len(caps), cap_node_count)
        )
    # ---------------------------------------------------------------- write
    with open(output, "w") as f:
        f.write("* ICPS2023_5 (minimal Fab SOI-CMOS) parasitics\n")
        f.write("* extracted by libs.tech/klayout/pex/icps2023_5_pex.py\n")
        f.write("* layout: %s  top cell: %s  layer_mode: %s\n" % (args.input, cell.name, args.layer_mode))
        f.write("* process profile: %s\n" % profile_name)
        f.write("* upper-metal layers: %s\n" % json.dumps([], separators=(",", ":")))
        f.write("* R model: KLayout RNetExtractor, square counting, per-conductor\n")
        f.write("* C model: diffusion-area Voronoi shares to located same-layer nodes; return assumed SUB\n")
        f.write(
            "* MOS source/drain diffusion access is omitted from additive PEX, not "
            "assumed 0 ohm; RDSW versus physical access is uncharacterized\n"
        )
        f.write("* MOS terminal ties contract graph anchors only; they are not total-access models\n")
        f.write(
            "* MOS CNT contact terms are omitted from PEX; assembly adds measured "
            "R_unit/N_cnt contact resistors\n"
        )
        f.write("* MOS terminal mapping binds LVS D/G/S to physical PEX nodes\n")
        f.write("* R_TIN bodies are LVS-owned; bounded CNT_TIN endpoint anchors are mapped separately\n")
        for summary in access_reports:
            f.write("* MOS_ACCESS_RESULT " + json.dumps(summary, sort_keys=True) + "\n")
        for record in physical_labels:
            mapping = {
                "physical": record["physical"],
                "logical": record["logical"],
                "layer": record["layer"],
                "x_um": round(record["point"].x * layout.dbu, 6),
                "y_um": round(record["point"].y * layout.dbu, 6),
            }
            f.write("* PORT_MAP %s\n" %
                    json.dumps(mapping, sort_keys=True, separators=(",", ":")))
        for mapping in mos_map_records:
            f.write("* MOS_MAP %s\n" %
                    json.dumps(mapping, sort_keys=True, separators=(",", ":")))
        for mapping in tin_res_map_records:
            f.write("* R_TIN_MAP %s\n" %
                    json.dumps(mapping, sort_keys=True, separators=(",", ":")))
        for mapping in resistor_map_records:
            f.write("* RESISTOR_MAP %s\n" %
                    json.dumps(mapping, sort_keys=True, separators=(",", ":")))
        subckt_ports = list(dict.fromkeys(record["physical"] for record in physical_labels))
        subckt_ports.append("SUB")
        f.write(".SUBCKT %s_pex %s\n" % (cell.name, " ".join(subckt_ports)))
        if not physical_labels:
            f.write("* no TEXT-labelled physical terminals found\n")
            f.write("* conductor nodes use generated internal names\n")
        f.write("* ---- resistors ----\n")
        for record in resistor_map_records:
            # %.9g keeps the element within the assembler's 1e-6 relative
            # agreement check against the full-precision RESISTOR_MAP record,
            # which %.6g output (up to 5e-6 relative rounding) violates.
            f.write(
                "%s %s %s %.9g\n"
                % (
                    record["name"],
                    record["a"],
                    record["b"],
                    record["resistance_ohm"],
                )
            )
        if caps:
            f.write("* ---- capacitors (eligible diffusion to SUB) ----\n")
            for i, (a, b, val) in enumerate(caps, 1):
                f.write("C%-5d %s %s %.9g\n" % (i, a, b, val))
        f.write(".ENDS %s_pex\n" % cell.name)

    print("wrote %s" % output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
