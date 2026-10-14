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
#     density, mapped to global SUB as an explicit assumption because the PDK
#     does not identify its return terminal. The 2.3 fF/um^2 CAP windows are
#     LVS-owned devices and are not emitted here.
#
# Ownership: LVS owns MOS devices, RES bodies, CAP devices, and diode I/V.
# PEX owns TiN/ML1 wiring and eligible passive/diode diffusion access. MOS
# terminal access is excluded from additive PEX because fitted B4SOI RDSW
# cannot be separated from physical access resistance. RES-marker contacts
# are also excluded; neither model supplies independent contact characterization
# at those device pins.
#
# The reference manual p.3 marks Via, M2(Al), Via and M3(Al) T.B.D.; they are
# therefore omitted rather than assigned an inferred resistance/capacitance.
# The un-doped SOI film has no direct sheet-resistance value in either source
# PDF, so it is not silently assigned the legacy LVS R_SOI value.
# Model limits (read the README): eligible passive-diffusion C is split evenly
# over the located resistance-network nodes in each electrical component; it
# is not local-area distributed RC. No qualified interconnect-coupling value
# is emitted.
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
    "DIFF": (3, 0), "TIN": (5, 0), "CNT": (7, 0), "ML1": (8, 0),
    "TEXT": (13, 0), "RES": (15, 0), "CAP": (16, 0), "DIO": (17, 0),
    "PAREA": (18, 0), "NAREA": (19, 0),
}
FAB = {
    "DIFF": (4, 0), "TIN": (5, 0), "CNT": (6, 0), "ML1": (7, 0),
    "PAREA": (2, 0), "NAREA": (3, 0), "CAP": (16, 0), "RES": (15, 0),
    "DIO": (17, 0), "TEXT": (13, 1),
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

# GDS uses one untyped CNT layer. Partition each cut to one internal via
# layer based on the conductor pair it touches. TiN wins when a cut also
# overlaps an implant mask, so gate contacts cannot short to diffusion.
CUT_LAYERS = {
    "CNT_TIN": (1000, 0),
    "CNT_NAREA": (1001, 0),
    "CNT_PAREA": (1002, 0),
}

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

# The reported density has no identified return terminal. For eligible
# passive diffusion only, this deck maps it to global SUB as an explicit
# single-node modeling assumption.
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
    # DIFF clips doped regions to SOI. MOS terminal access is omitted from additive
    # PEX because the fitted RDSW is inseparable from physical access; RES bodies
    # are omitted because LVS already owns their sheet resistance.
    diff = region("DIFF")
    tin = region("TIN")
    ml1 = region("ML1")
    cap = region("CAP")
    res = region("RES")
    dio = region("DIO")
    mos_gate = (tin & diff) - cap
    raw_narea = region("NAREA") & diff
    raw_parea = region("PAREA") & diff
    narea = raw_narea - mos_gate
    parea = raw_parea - mos_gate
    mos_halo = mos_gate.sized(1)
    mos_access = {
        "NAREA": narea.interacting(mos_halo),
        "PAREA": parea.interacting(mos_halo),
    }
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
    narea = narea - mos_access["NAREA"] - res_active["NAREA"]
    parea = parea - mos_access["PAREA"] - res_active["PAREA"]
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
    # N/P diffusion remains PEX-owned only outside MOS access and explicit
    # RES bodies. DIO access remains PEX-owned because the diode model has no
    # characterized series resistance.
    conductor_region = {
        "TIN": tin,
        "ML1": ml1,
        "NAREA": narea,
        "PAREA": parea,
    }

    raw_contacts = region("CNT")
    via_regions = {cut: pya.Region() for cut, _bottom, _top, _res in VIAS}
    for poly in region_polygons(raw_contacts):
        cut_poly = pya.Region(poly)
        for cut, bottom, top, _res in VIAS:
            contact = cut_poly & conductor_region[bottom] & conductor_region[top]
            if contact.is_empty():
                continue
            for contact_poly in region_polygons(contact):
                via_regions[cut].insert(contact_poly)
            break

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
    def polygon_key(poly):
        box = poly.bbox()
        return box.left, box.bottom, box.right, box.top

    for cut, bottom, _top, _r in VIAS:
        for index, poly in enumerate(sorted(region_polygons(via_regions[cut]), key=polygon_key), 1):
            add_port("C_%s_%04d" % (bottom, index), bottom, poly_centroid(poly))
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
    for node in net.each_node():
        if str(node.type()) == "VertexPort":
            lid, idx = node.layer(), node.port_index()
            nname = port_names.get((lid, idx))
            if nname is None:
                pt = dbox_to_point(node.location(), layout.dbu)
                nname = "x_%d_%d_%d_%d" % (lid, idx, pt.x, pt.y)
        else:
            nname = "n%d" % node.object_id()
        node_name[node.object_id()] = nname

    elements = list(net.each_element())
    print("R network: %d nodes, %d resistors" % (net.num_nodes(), len(elements)))

    # ------------------------------------------------------------ C extract
    caps = []          # (node_a, node_b, farad)
    if not args.no_capacitance:
        # 1. conductor components per layer. CAP/RES/DIO device regions are
        #    excluded from this assumed substrate-capacitance area below.
        comps = {}          # layer name -> list of (key, polygon)
        comp_area = {}
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
                eligible = substrate_eligible.get(lname)
                if eligible is None:
                    area = 0.0
                else:
                    area = (pya.Region(poly) & eligible).area() * layout.dbu * layout.dbu
                comp_area[key] = area
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
        parent = {k: k for k in comp_area}

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

        # 3. map every located R-network node to the merged component it
        #    occupies. Internal nodes and explicit vertex ports both receive C.
        component_nodes = {}
        conductor_by_id = {
            layer_id(spec): name for name, spec in layers.items() if name in comps
        }
        for node in net.each_node():
            if node.location() is None:
                continue
            lname = conductor_by_id.get(node.layer())
            if lname is None:
                continue
            key = component_at(lname, dbox_to_point(node.location(), layout.dbu))
            if key is None:
                continue
            root = find(key)
            component_nodes.setdefault(root, set()).add(node.object_id())

        substrate_cap = {}
        for key, area in comp_area.items():
            cpa = CAP_TO_SUB.get(key[0])
            if cpa:
                root = find(key)
                substrate_cap[root] = substrate_cap.get(root, 0.0) + area * cpa

        # 4. Preserve total density*area and split it evenly across the
        #    resistance-network nodes in the merged passive-diffusion component.
        cap_node_count = 0
        for root in sorted(substrate_cap):
            capacitance = substrate_cap[root]
            node_ids = sorted(
                component_nodes.get(root, ()),
                key=lambda object_id: node_name[object_id],
            )
            if capacitance <= 0.0 or not node_ids:
                continue
            share = capacitance / len(node_ids)
            cap_node_count += len(node_ids)
            for object_id in node_ids:
                caps.append((node_name[object_id], "SUB", share))
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
        f.write("* R model: KLayout RNetExtractor, square counting, per-conductor\n")
        f.write("* C model: equal node shares of eligible passive diffusion density to assumed global SUB\n")
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
        subckt_ports = [record["physical"] for record in physical_labels] + ["SUB"]
        f.write(".SUBCKT %s_pex %s\n" % (cell.name, " ".join(subckt_ports)))
        if not physical_labels:
            f.write("* no TEXT-labelled physical terminals found\n")
            f.write("* conductor nodes use generated internal names\n")
        f.write("* ---- resistors ----\n")
        for i, el in enumerate(elements, 1):
            aid, bid = el.a().object_id(), el.b().object_id()
            a = node_name.get(aid, "n%d" % aid)
            b = node_name.get(bid, "n%d" % bid)
            f.write("R%-5d %s %s %.6g\n" % (i, a, b, el.resistance()))
        if caps:
            f.write("* ---- capacitors (eligible diffusion to SUB) ----\n")
            for i, (a, b, val) in enumerate(caps, 1):
                f.write("C%-5d %s %s %.6g\n" % (i, a, b, val))
        f.write(".ENDS %s_pex\n" % cell.name)

    print("wrote %s" % output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
