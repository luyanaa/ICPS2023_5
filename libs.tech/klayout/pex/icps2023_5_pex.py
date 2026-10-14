#!/usr/bin/env python3
# =============================================================================
# icps2023_5_pex.py -- KLayout parasitic extraction for the ICPS2023_5
#                      (minimal Fab SOI-CMOS) process.
#
# Supersedes nothing: the legacy PDK had no PEX at all.
#
# What it produces, per conductor:
#   * resistance network  -- KLayout's native R extractor (klayout.pex,
#     RNetExtractor / RExtractorTech). Conductors and vias are taken from the
#     PDK documents:
#         ML1/ML2/ML3 (Al)   0.2   ohm/sq   (PDK sheet resistance table)
#         TiN                20    ohm/sq
#         N+ diffusion       180   ohm/sq
#         P+ diffusion       1200  ohm/sq
#         undoped SOI film   250e6 ohm/sq  (LVS deck R_SOI)
#         CNT                150 ohm (Al-N+), 200 ohm (Al-P+), 0.9 ohm (Al-TiN)
#         VIA1, VIA2         0.9 ohm (same Al/TiN contact stack)
#   * capacitance -- lumped per conductor component to the substrate and
#     coupled between overlapping conductors, using the documented areal
#     capacitances:
#         TiN   <-> DIFF (15 nm gate oxide)      2.3   fF/um^2
#         TiN   <-> ML1  (300 nm TEOS)           0.115 fF/um^2
#         ML1   <-> ML2  (300 nm TEOS)           0.115 fF/um^2
#         DIFF  <-> substrate (400 nm BOX)       0.086 fF/um^2
#
# Model limits (read the README): C is lumped per conductor component, there is
# no field solver, no fringing capacitance, and a component that carries several
# extracted R nodes gets its capacitance attached to one of them.
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
# Layer tables (see libs.tech/klayout/doc/layers.md)
# --------------------------------------------------------------------------
DESIGN = {
    "DIFF": (3, 0), "TIN": (5, 0), "CNT": (7, 0), "ML1": (8, 0),
    "VIA1": (9, 0), "ML2": (10, 0), "VIA2": (11, 0), "ML3": (12, 0),
    "TEXT": (13, 0), "PAREA": (18, 0), "NAREA": (19, 0),
}
FAB = {
    "DIFF": (4, 0), "TIN": (5, 0), "CNT": (6, 0), "ML1": (7, 0),
    "VIA1": (8, 0), "ML2": (9, 0), "VIA2": (11, 0), "ML3": (12, 0),
    "TEXT": (13, 0), "PAREA": (2, 0), "NAREA": (3, 0),
}

# --------------------------------------------------------------------------
# Technology data (PDK documents; see libs.tech/ngspice/README.md for the same
# numbers used by the SPICE passives)
# --------------------------------------------------------------------------
F_PER_UM2 = 1e-15          # 1 fF/um^2
OHM_PER_SQ = {
    "ML1": 0.2, "ML2": 0.2, "ML3": 0.2,
    "TIN": 20.0,
    "NAREA": 180.0, "PAREA": 1200.0,
    "DIFF": 250e6,          # undoped SOI film (LVS deck R_SOI)
}
# via: (cut layer, bottom conductor, top conductor, resistance in ohm)
VIAS = [
    ("CNT", "DIFF", "ML1", 150.0),   # Al-N+ contact
    ("CNT", "TIN", "ML1", 0.9),      # Al-TiN contact
    ("VIA1", "ML1", "ML2", 0.9),
    ("VIA2", "ML2", "ML3", 0.9),
]
# areal capacitances: (layer A, layer B, fF/um^2)
CAP_PAIRS = [
    ("TIN", "DIFF", 2.3 * F_PER_UM2),
    ("TIN", "ML1", 0.115 * F_PER_UM2),
    ("ML1", "ML2", 0.115 * F_PER_UM2),
]
CAP_TO_SUB = {
    "DIFF": 0.086 * F_PER_UM2,       # BOX, 400 nm
    "NAREA": 0.086 * F_PER_UM2,
    "PAREA": 0.086 * F_PER_UM2,
}

CONDUCTORS = ["DIFF", "TIN", "ML1", "VIA1", "ML2", "VIA2", "ML3"]
PAD_OR_MASK = [(20, 0), (63, 63)]


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

    def region(name):
        li = layout.find_layer(pya.LayerInfo(*layers[name]))
        if li is None:
            return pya.Region()
        return pya.Region(cell.begin_shapes_rec(li))

    # input geometry ------------------------------------------------------
    diff = region("DIFF")
    tin = region("TIN")
    cnt = region("CNT")
    ml1 = region("ML1")
    via1 = region("VIA1")
    ml2 = region("ML2")

    # Text labels give the net names (TEXT 13/0)
    labels = []
    li_text = layout.find_layer(pya.LayerInfo(*layers["TEXT"]))
    if li_text is not None:
        it = cell.begin_shapes_rec(li_text)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                t = sh.text
                labels.append((t.string, it.trans() * t.position))
            it.next()

    conductor_region = {
        "DIFF": diff,
        "TIN": tin,
        "ML1": ml1,
        "VIA1": via1,
        "ML2": ml2,
    }
    for opt in ("ML3", "VIA2"):
        r = region(opt)
        if not r.is_empty():
            conductor_region[opt] = r

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

    # contacts: a CNT connects DIFF (or TiN) to ML1
    for cut, bottom, top, _r in VIAS:
        cut_region = conductor_region.get(cut)
        if cut_region is None:
            cut_region = region(cut)
        if cut_region.is_empty():
            continue
        bottom_region = conductor_region.get(bottom)
        top_region = conductor_region.get(top)
        if bottom_region is None or top_region is None:
            continue
        # one port per contact, placed on the lowest conductor it touches, so
        # the network is broken at the device terminal without duplicating ports
        cut_bottom = cut_region & bottom_region
        cut_top = cut_region & top_region
        for poly in region_polygons(cut_bottom | cut_top):
            pt = poly_centroid(poly)
            if contains_point(cut_bottom, pt):
                add_port("c_%d_%d" % (pt.x, pt.y), bottom, pt)
            else:
                add_port("c_%d_%d" % (pt.x, pt.y), top, pt)
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
        cond.resistance = OHM_PER_SQ.get(name, 0.2)
        cond.algorithm = pex.Algorithm.SquareCounting
        tech.add_conductor(cond)
    for cut, bottom, top, res in VIAS:
        if bottom not in conductor_region or top not in conductor_region:
            continue
        via = pex.RExtractorTechVia()
        via.cut_layer = layer_id(layers[cut])
        via.bottom_conductor = layer_id(layers[bottom])
        via.top_conductor = layer_id(layers[top])
        via.resistance = res
        tech.add_via(via)

    geo = {layer_id(layers[n]): r for n, r in conductor_region.items()}
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
    caps = []          # (name, node_a, node_b, farad)
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
            cut_region = conductor_region.get(cut)
            if cut_region is None:
                cut_region = region(cut)
            if cut_region.is_empty():
                continue
            if bottom not in comps or top not in comps:
                continue
            for poly in region_polygons(cut_region & (conductor_region[bottom] | conductor_region[top])):
                pt = poly_centroid(poly)
                kb = component_at(bottom, pt)
                kt = component_at(top, pt)
                if kb is not None and kt is not None:
                    union(kb, kt)

        # 3. map every R network node to the merged component it sits in, so
        #    capacitance is attached to the same nodes as the resistance network
        node_component = {}
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
            node_component[node.object_id()] = root
            if root not in component_node:
                component_node[root] = node.object_id()

        merged_area = {}
        root_layer = {}
        for key, area in comp_area.items():
            root = find(key)
            merged_area[root] = merged_area.get(root, 0.0) + area
            root_layer.setdefault(root, key[0])

        # 4. capacitance to the substrate
        for root, area in merged_area.items():
            cpa = CAP_TO_SUB.get(root_layer.get(root))
            if not cpa or root not in component_node:
                continue
            caps.append(("CS_%s_%s" % (root_layer[root], root[1]),
                         node_name[component_node[root]], "SUB", area * cpa))

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
                caps.append(("CC_%s%s_%s%s" % (ra[0], ra[1], rb[0], rb[1]),
                             node_name[component_node[ra]], node_name[component_node[rb]],
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
        if pinned:
            f.write(".SUBCKT %s_pex %s SUB\n" % (cell.name, " ".join(pinned)))
        else:
            f.write("* no TEXT labelled nets found: the parasitic network is written\n")
            f.write("* without top level pins, internal node names only\n")
            f.write(".SUBCKT %s_pex\n" % cell.name)
        f.write("* ---- resistors ----\n")
        for i, el in enumerate(elements, 1):
            aid, bid = el.a().object_id(), el.b().object_id()
            a = node_name.get(aid, "n%d" % aid)
            b = node_name.get(bid, "n%d" % bid)
            f.write("R%-5d %s %s %.6g\n" % (i, a, b, el.resistance()))
        if caps:
            f.write("* ---- capacitors (to SUB and coupling) ----\n")
            for i, (nm, a, b, val) in enumerate(caps, 1):
                f.write("C%-5d %s %s %.6g\n" % (i, a, b, val))
        f.write(".ENDS %s_pex\n" % cell.name)

    print("wrote %s" % output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
