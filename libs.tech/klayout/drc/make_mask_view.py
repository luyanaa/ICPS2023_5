#!/usr/bin/env python3
"""Hierarchical foundry mask view for passive frame-like libraries.

The frame libraries must keep their cells: the LEF/DEF views are emitted per
macro (pad cells, ring cell), so the flattened fab-prep output cannot be the
shipped GDS.  Frame content carries no SOI (`DIFF 3/0`), and without DIFF every
fab-prep derivation collapses: the dope masks are empty, the gate-TiN bias is
empty and `TIN_BLK` merges into TiN by renaming.  The conversion is therefore a
pure layer renaming, which can be applied per cell with the hierarchy intact.

This tool asserts that precondition, remaps the layers into a fresh layout
(no in-place aliasing), and then proves the result: it flattens the remapped
view and compares it layer by layer against a real `icps2023_5_fab_prep.drc`
conversion of the same input.  A mismatch aborts instead of shipping an
unverified view.

Usage:
    python3 libs.tech/klayout/drc/make_mask_view.py \
        --input <drawing.gds> --output <mask.gds> [--profile NAME]
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = os.environ.get("KLAYOUT", "klayout")
FAB_PREP = Path(__file__).resolve().with_name("icps2023_5_fab_prep.drc")
DEFAULT_PROFILE = "icps2023_5_1m"

# Drawing layer -> foundry mask layer (mirrors icps2023_5_fab_prep.drc).
REMAP = {
    (3, 0): (4, 0),    # DIFF -> SOI: forbidden input for this passive-view tool
    (5, 0): (5, 0),    # TiN-Gate
    (5, 1): (5, 0),    # TIN_BLK merges into the TiN output
    (7, 0): (6, 0),    # Contact
    (8, 0): (7, 0),    # M1(Al)
    (9, 0): (8, 0),    # Via
    (10, 0): (9, 0),   # M2(Al)
    (13, 0): (13, 1),  # TEXT labels
    (14, 0): (14, 0),  # FRAME
    (15, 0): (15, 0),  # RES
    (16, 0): (16, 0),  # CAP
    (17, 0): (17, 0),  # DIO
    (18, 0): (2, 0),   # P+ dope -> P-diff
    (19, 0): (3, 0),   # N+ dope -> N-diff
    (21, 0): (13, 0),  # N-ch channel dope
    (50, 0): (10, 0),  # PAD opening
}
# Design-only layers with no mask output (dropped, matching the legacy deck).
DESIGN_ONLY = {(1, 0), (20, 0), (63, 63)} | {(layer, 0) for layer in range(101, 106)}


def present_layers(layout, cell):
    used = set()
    for index in layout.layer_indices():
        if not cell.shapes(index).is_empty():
            info = layout.get_info(index)
            used.add((info.layer, info.datatype))
        for instance in cell.each_inst():
            used |= present_layers(layout, instance.cell)
    return used


def guard_precondition(layout, top):
    used = present_layers(layout, top)
    if (3, 0) in used:
        raise SystemExit("mask view refused: SOI (DIFF 3/0) is present, so "
                         "fab-prep derivations are not a pure layer renaming")
    unknown = sorted(used - set(REMAP) - DESIGN_ONLY)
    if unknown:
        raise SystemExit("mask view refused: no mask mapping for %s" % unknown)


def build_mask_view(source):
    view = pya.Layout()
    view.dbu = source.dbu
    cells = {}
    for cell in source.each_cell():
        cells[cell.cell_index()] = view.create_cell(cell.name)
    layers = {}
    for index in source.layer_indices():
        info = source.get_info(index)
        key = (info.layer, info.datatype)
        if key in DESIGN_ONLY:
            layers[index] = None
        elif key in REMAP:
            layers[index] = view.layer(pya.LayerInfo(*REMAP[key]))
        else:
            raise SystemExit("mask view refused: no mask mapping for %s" % (key,))
    for cell in source.each_cell():
        target = cells[cell.cell_index()]
        for instance in cell.each_inst():
            child = cells[instance.cell_index]
            target.insert(pya.CellInstArray(child, instance.trans, instance.a,
                                            instance.b, instance.na, instance.nb))
        for index in source.layer_indices():
            target_index = layers[index]
            if target_index is None:
                continue
            shapes = cell.shapes(index)
            if shapes.is_empty():
                continue
            out_shapes = target.shapes(target_index)
            for shape in shapes.each():
                out_shapes.insert(shape)
    return view


def fab_prep(source, destination, profile, top=None):
    environment = os.environ.copy()
    environment["ICPS2023_5_PROCESS_PROFILE"] = profile
    command = [KLAYOUT, "-b", "-r", str(FAB_PREP), "-rd", "input=" + str(source),
               "-rd", "output=" + str(destination),
               "-rd", "process_profile=" + profile]
    if top:
        command += ["-rd", "top=" + top]
    result = subprocess.run(command, text=True, capture_output=True,
                            env=environment)
    if result.returncode:
        raise SystemExit("fab prep failed for %s%s:\n%s"
                         % (source, " top=" + top if top else "",
                            result.stdout + result.stderr))


def convert_flat_library(source_path, destination, profile):
    """Convert every top cell of a multi-top primitive library with fab prep.

    fab prep writes flattened output; this mode accepts that loss for a
    cell-library input whose parents are itself tops (the primitive cell names
    survive as flattened tops, the internal child hierarchy does not).
    """
    probe = pya.Layout()
    probe.read(str(source_path))
    tops = [cell.name for cell in probe.top_cells()]
    merged = pya.Layout()
    merged.dbu = probe.dbu
    copied = set()
    with tempfile.TemporaryDirectory() as tmp:
        for top in tops:
            part = Path(tmp) / (top + ".gds")
            fab_prep(source_path, part, profile, top=top)
            part_layout = pya.Layout()
            part_layout.read(str(part))
            for cell in part_layout.each_cell():
                if cell.name in copied:
                    continue
                copied.add(cell.name)
                merged.create_cell(cell.name).copy_tree(cell)
    options = pya.SaveLayoutOptions()
    options.gds2_write_timestamps = False
    merged.write(str(destination), options)
    source_layers = {(info.layer, info.datatype) for info in probe.layer_infos()}
    view_layers = {(info.layer, info.datatype) for info in merged.layer_infos()}
    dropped = sorted(entry for entry in source_layers
                     if REMAP.get(entry) not in view_layers)
    unexpected = [entry for entry in dropped if entry not in DESIGN_ONLY]
    if unexpected:
        raise SystemExit("flat library view: unmapped layers with no mask "
                         "output: %s" % unexpected)
    return dropped


def fingerprint(gds_path):
    layout = pya.Layout()
    layout.read(str(gds_path))
    cell = layout.top_cell()
    cell.flatten(True)
    records = []
    for info in sorted(layout.layer_infos(), key=lambda i: (i.layer, i.datatype)):
        index = layout.find_layer(info)
        region = pya.Region(cell.begin_shapes_rec(index)).merged()
        boxes = sorted((p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top)
                       for p in region.each())
        texts = sorted((shape.text.string, shape.text.trans.disp.x,
                        shape.text.trans.disp.y)
                       for shape in cell.shapes(index).each() if shape.is_text())
        records.append(((info.layer, info.datatype), boxes, texts))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--flat-library", action="store_true",
                        help="multi-top primitive library: convert every top "
                             "with fab prep and merge the flattened cells")
    arguments = parser.parse_args()

    if arguments.flat_library:
        dropped = convert_flat_library(arguments.input, arguments.output,
                                       arguments.profile)
        print("flat library mask view written: %s (dropped=%s)"
              % (arguments.output, dropped))
        return 0

    source = pya.Layout()
    source.read(arguments.input)
    guard_precondition(source, source.top_cell())
    view = build_mask_view(source)
    options = pya.SaveLayoutOptions()
    options.gds2_write_timestamps = False
    view.write(arguments.output, options)

    # Equivalence gate: the hierarchical view flattened must equal the real
    # fab-prep conversion of the same input.
    with tempfile.TemporaryDirectory() as tmp:
        flat = Path(tmp) / "flat.gds"
        fab_prep(arguments.input, flat, arguments.profile)
        if fingerprint(arguments.output) != fingerprint(flat):
            Path(arguments.output).unlink()
            raise SystemExit("mask view refused: hierarchical view differs from "
                             "the fab-prep conversion of %s" % arguments.input)

    print("mask view written: %s (verified against fab prep, profile=%s)"
          % (arguments.output, arguments.profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
