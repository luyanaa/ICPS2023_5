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
#   * capacitance -- lumped per conductor component plus coupling between
#     overlapping conductors.  PDK_minimalFabSOICMOS.pdf p.9 (Tables of Gate
#     Oxide and MOSFET Capacitance) reports Cox=2.3 fF/um^2 and N/P diffusion
#     capacitance density=0.086 fF/um^2, but does not identify the diffusion
#     capacitance return conductor.  This deck maps that density to global SUB
#     as an explicit modeling assumption, not as a measured BOX coupling.
#     The same table reports overlap capacitance of 5 fF/um (N) and 6.8 fF/um
#     (P).  It is intentionally not extracted: the documents do not define a
#     validated layout edge/perimeter model for that 1-D value.
#
# The reference manual p.3 marks Via, M2(Al), Via and M3(Al) T.B.D.; they are
# therefore omitted rather than assigned an inferred resistance/capacitance.
# The un-doped SOI film has no direct sheet-resistance value in either source
# PDF, so it is not silently assigned the legacy LVS R_SOI value.
#
# Model limits (read the README): baseline C is lumped per conductor component;
# the deck consumes no field-solver result and has no fringing capacitance. A
# component that carries several extracted R nodes gets its capacitance
# attached to one of them.
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
    "TEXT": (13, 0), "CAP": (16, 0), "PAREA": (18, 0), "NAREA": (19, 0),
}
FAB = {
    "DIFF": (4, 0), "TIN": (5, 0), "CNT": (6, 0), "ML1": (7, 0),
    "PAREA": (2, 0), "NAREA": (3, 0), "CAP": (16, 0), "TEXT": (13, 1),
}

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

# Measured Cox applies to the explicit diffusion-capacitor windows (`CAP`).
# MOS channel overlap is removed from conductor geometry below, so this pair
# does not duplicate intrinsic capacitance from BSIMSOI device models.
CAP_PAIRS = [
    ("TIN", "NAREA", 2.3 * F_PER_UM2),
    ("TIN", "PAREA", 2.3 * F_PER_UM2),
]

# PDK p.9 reports diffusion capacitance density (reference manual p.4
# `CNdiff`/`CPdiff`) without specifying the return terminal. The deck assigns
# it to global SUB as a single-node modeling assumption.
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
    ap.add_argument("--simplify", action="store_true", default=True)
    args = ap.parse_args()

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
    print("ICPS2023_5 PEX  layer_mode=%s  top=%s  dbu=%g" % (args.layer_mode, cell.name, layout.dbu))

    def raw_region(spec):
        li = layout.find_layer(pya.LayerInfo(*spec))
        if li is None:
            return pya.Region()
        return pya.Region(cell.begin_shapes_rec(li))

    exclude = raw_region((63, 63))
    mask = raw_region((20, 0)) if args.layer_mode == "design" else pya.Region()

    def region(name):
        return raw_region(layers[name]) - exclude - mask

    # input geometry ------------------------------------------------------
    # DIFF is used only to clip the dopant masks to the SOI island.  The
    # undoped SOI film itself has no sheet resistance in either source PDF,
    # so it is deliberately not an R-extractor conductor.
    diff = region("DIFF")
    # Keep P/N regions as diffusion conductors only. Gate TiN removes the SOI
    # channel from R/C-to-SUB geometry; explicit CAP windows remain electrodes.
    tin = region("TIN")
    ml1 = region("ML1")
    cap = region("CAP")
    mos_gate = (tin & diff) - cap
    narea = (region("NAREA") & diff) - mos_gate
    parea = (region("PAREA") & diff) - mos_gate

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
    # NAREA and PAREA are separate conductors so their measured sheet and
    # contact resistances cannot be conflated.
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

    # ---------------------------------------------------------------- ports
    # Ports are the points where a device or an external net meets a
    # conductor: contact/via locations, plus text labels.
    ports = {layer_id(layers[n]): [] for n in conductor_region}
    port_names = {}

    def add_port(name, layer_name, point):
        lid = layer_id(layers[layer_name])
        lst = ports.setdefault(lid, [])
        lst.append(point)
        port_names[(lid, len(lst) - 1)] = name

    # Each CNT cut is assigned to one conductor pair, and the port is placed
    # at the shared via footprint.
    for cut, bottom, _top, _r in VIAS:
        for poly in region_polygons(via_regions[cut]):
            pt = poly_centroid(poly)
            add_port("c_%d_%d" % (pt.x, pt.y), bottom, pt)
    for name, pt in labels:
        for lname, r in conductor_region.items():
            if contains_point(r, pt):
                add_port(name, lname, pt)

    n_ports = sum(len(v) for v in ports.values())
    print("ports: %d (%d named)" % (n_ports, len(labels)))
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
    wifi = {}
    for node in net.each_node():
        if str(node.type()) == "VertexPort":
            lid, idx = node.layer(), node.port_index()
            nname = port_names.get((lid, idx))
            if nname is None:
                pt = dbox_to_point(node.location(), layout.dbu)
                nname = "x_%d_%d" % (pt.x, pt.y)
        else:
            nname = "n%d" % node.object_id()
        # node names must be unique: several ports can share a text label
        if nname in wifi and wifi[nname] != node.object_id():
            nname = "%s#%d" % (nname, node.object_id())
        wifi[nname] = node.object_id()
        node_name[node.object_id()] = nname

    elements = list(net.each_element())
    print("R network: %d nodes, %d resistors" % (net.num_nodes(), len(elements)))

    # ------------------------------------------------------------ C extract
    caps = []          # (node_a, node_b, farad)
    if not args.no_capacitance:
        # 1. conductor components per layer (connected parts of the merged
        #    region) and their area
        comps = {}          # layer name -> list of (key, polygon)
        comp_area = {}
        comp_of_cell = {}   # grid index: (layer, cx, cy) -> [key, ...]
        CELL = 10000        # 10 um grid in database units

        for lname, rgn in conductor_region.items():
            comps[lname] = []
            for i, poly in enumerate(region_polygons(rgn)):
                key = (lname, i)
                comps[lname].append((key, poly))
                comp_area[key] = poly.area() * layout.dbu * layout.dbu
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

        # 3. map every R network node to the merged component it sits in, so
        #    capacitance is attached to the same nodes as the resistance network
        component_node = {}
        for node in net.each_node():
            if node.location() is None or str(node.type()) != "VertexPort":
                continue
            lname = None
            for cand, spec in layers.items():
                if layer_id(spec) == node.layer():
                    lname = cand
                    break
            if lname is None or lname not in comps:
                continue
            key = component_at(lname, dbox_to_point(node.location(), layout.dbu))
            if key is None:
                continue
            root = find(key)
            if root not in component_node:
                component_node[root] = node.object_id()

        substrate_cap = {}
        for key, area in comp_area.items():
            cpa = CAP_TO_SUB.get(key[0])
            if cpa:
                root = find(key)
                substrate_cap[root] = substrate_cap.get(root, 0.0) + area * cpa

        # 4. capacitance to the substrate. Sum only N/P diffusion areas;
        #    wire and gate area share electrical roots but do not use this
        #    diffusion-density parameter.
        for root, capacitance in substrate_cap.items():
            if root not in component_node:
                continue
            caps.append((node_name[component_node[root]], "SUB", capacitance))

        # 5. coupling capacitance between overlapping conductors of different
        #    components (lumped per component pair)
        for la, lb, cpa in CAP_PAIRS:
            if la not in comps or lb not in comps:
                continue
            inter = conductor_region[la] & conductor_region[lb]
            if inter.is_empty():
                continue
            pair_area = {}
            for poly in region_polygons(inter):
                pt = poly_centroid(poly)
                ka = component_at(la, pt)
                kb = component_at(lb, pt)
                if ka is None or kb is None:
                    continue
                ra, rb = find(ka), find(kb)
                if ra == rb or ra not in component_node or rb not in component_node:
                    continue
                key = (ra, rb) if ra <= rb else (rb, ra)
                pair_area[key] = pair_area.get(key, 0.0) + poly.area() * layout.dbu * layout.dbu
            for (ra, rb), area in pair_area.items():
                caps.append((node_name[component_node[ra]], node_name[component_node[rb]],
                             area * cpa))
        print("capacitors: %d (%d nodes carry capacitance)" % (len(caps), len(component_node)))

    # ---------------------------------------------------------------- write
    with open(output, "w") as f:
        f.write("* ICPS2023_5 (minimal Fab SOI-CMOS) parasitics\n")
        f.write("* extracted by libs.tech/klayout/pex/icps2023_5_pex.py\n")
        f.write("* layout: %s  top cell: %s  layer_mode: %s\n" % (args.input, cell.name, args.layer_mode))
        f.write("* R model: KLayout RNetExtractor, square counting, per-conductor\n")
        f.write("* C model: lumped per conductor component, documented areal values\n")
        pinned = sorted({lbl for lbl, _ in labels if lbl})
        subckt_ports = list(pinned)
        if not any(name.upper() == "SUB" for name in subckt_ports):
            subckt_ports.append("SUB")
        f.write(".SUBCKT %s_pex %s\n" % (cell.name, " ".join(subckt_ports)))
        if not pinned:
            f.write("* no TEXT labelled nets found: only the substrate port is external\n")
            f.write("* all conductor nodes use generated internal names\n")
        f.write("* ---- resistors ----\n")
        for i, el in enumerate(elements, 1):
            aid, bid = el.a().object_id(), el.b().object_id()
            a = node_name.get(aid, "n%d" % aid)
            b = node_name.get(bid, "n%d" % bid)
            f.write("R%-5d %s %s %.6g\n" % (i, a, b, el.resistance()))
        if caps:
            f.write("* ---- capacitors (to SUB and coupling) ----\n")
            for i, (a, b, val) in enumerate(caps, 1):
                f.write("C%-5d %s %s %.6g\n" % (i, a, b, val))
        f.write(".ENDS %s_pex\n" % cell.name)

    print("wrote %s" % output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
