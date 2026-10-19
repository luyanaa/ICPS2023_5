#!/usr/bin/env python3
"""Check generated pad LEF/DEF identities against the unchanged frame GDS."""

import csv
import re
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
GDS = ROOT / "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"
LEF = ROOT / "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef"
DEF = ROOT / "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def"
LIBRELANE_LEF = ROOT / "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base_librelane.lef"
LIBRELANE_DEF = ROOT / "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base_librelane.def"
LIB = ROOT / "libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib"
PAD_MAP = ROOT / "libs.ref/ICPS2023_5_fd_base/doc/pad_map.csv"

def def_pin_records(text, layer="ML1"):
    records = {}
    pin_section = re.search(r"(?ms)^PINS \d+ ;(.*?)^END PINS$", text)
    if not pin_section:
        raise AssertionError("DEF is missing its PINS section")
    blocks = re.findall(r"(?ms)^- (PAD_\d{2})\b(.*?;)", pin_section.group(1))
    for name, body in blocks:
        header = re.search(
            r"\+ NET (\S+) \+ DIRECTION INOUT \+ USE SIGNAL", body
        )
        shape = re.search(
            r"\+ LAYER %s \( 0 0 \) \( (\d+) (\d+) \)" % re.escape(layer), body
        )
        fixed = re.search(r"\+ FIXED \( (-?\d+) (-?\d+) \) N", body)
        if not (header and shape and fixed):
            raise AssertionError("malformed DEF pin %s" % name)
        records[name] = {
            "net": header.group(1),
            "width": int(shape.group(1)),
            "height": int(shape.group(2)),
            "x": int(fixed.group(1)),
            "y": int(fixed.group(2)),
        }
    return records


class PadViewsTests(unittest.TestCase):
    def test_def_pins_match_all_gds_pad_rectangles(self):
        layout = pya.Layout()
        layout.read(str(GDS))
        top = layout.cell("ICPS2023_5_fd_base")
        self.assertIsNotNone(top)
        die = top.bbox()
        ml1 = layout.find_layer(pya.LayerInfo(7, 0))
        self.assertIsNotNone(ml1)
        top.flatten(True)
        region = pya.Region(top.shapes(ml1)).merged()
        boxes = []
        for polygon in region.each():
            self.assertTrue(polygon.is_box())
            box = polygon.bbox()
            self.assertEqual(box.width(), int(round(60.0 / layout.dbu)))
            self.assertEqual(box.height(), int(round(60.0 / layout.dbu)))
            boxes.append(box)
        self.assertEqual(len(boxes), 28)

        expected = sorted(boxes, key=lambda box: (-(2 * box.bottom + box.height()), box.left))
        text = DEF.read_text()
        units = re.search(r"(?m)^UNITS DISTANCE MICRONS (\d+) ;$", text)
        diearea = re.search(
            r"(?m)^DIEAREA \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \) ;$",
            text,
        )
        pin_count = re.search(r"(?m)^PINS (\d+) ;$", text)
        self.assertIsNotNone(units)
        self.assertIsNotNone(diearea)
        self.assertIsNotNone(pin_count)
        units_per_micron = int(units.group(1))
        self.assertEqual(
            tuple(map(int, diearea.groups())),
            tuple(int(round(v * layout.dbu * units_per_micron)) for v in
                  (die.left, die.bottom, die.right, die.top)),
        )
        self.assertEqual(int(pin_count.group(1)), 28)
        records = def_pin_records(text)
        self.assertEqual(len(records), 28)
        for index, box in enumerate(expected):
            name = "PAD_%02d" % index
            record = records[name]
            self.assertEqual(record["net"], name)
            self.assertEqual(record["width"], int(round(box.width() * layout.dbu * units_per_micron)))
            self.assertEqual(record["height"], int(round(box.height() * layout.dbu * units_per_micron)))
            self.assertEqual(record["x"], int(round(box.left * layout.dbu * units_per_micron)))
            self.assertEqual(record["y"], int(round(box.bottom * layout.dbu * units_per_micron)))

    def test_source_pin_labels_map_to_def_pads_and_gds_arrays(self):
        layout = pya.Layout()
        layout.read(str(GDS))
        top = layout.cell("ICPS2023_5_fd_base")
        self.assertIsNotNone(top)

        pad_arrays = [
            instance for instance in top.each_inst()
            if instance.cell.name == "pad_60_m1_tin"
        ]
        self.assertEqual(len(pad_arrays), 4)
        self.assertTrue(all(instance.is_regular_array() for instance in pad_arrays))
        self.assertEqual(
            sorted(instance.na * instance.nb for instance in pad_arrays),
            [6, 6, 8, 8],
        )

        top.flatten(True)
        ml1 = layout.find_layer(pya.LayerInfo(7, 0))
        self.assertIsNotNone(ml1)
        boxes = [
            polygon.bbox()
            for polygon in pya.Region(top.shapes(ml1)).merged().each()
        ]
        boxes.sort(key=lambda box: (-(2 * box.bottom + box.height()), box.left))

        labels = {}
        for layer_index in range(layout.layers()):
            for shape in top.shapes(layer_index).each():
                if not shape.is_text():
                    continue
                text = shape.text
                if not re.fullmatch(r"pin\d{2}", text.string):
                    continue
                point = text.trans.disp
                self.assertNotIn(text.string, labels)
                labels[text.string] = (point.x, point.y)

        self.assertEqual(
            set(labels), {"pin%02d" % index for index in range(1, 29)}
        )
        with PAD_MAP.open(newline="") as source:
            reader = csv.DictReader(source)
            self.assertEqual(
                reader.fieldnames,
                [
                    "def_pin",
                    "source_pin_label",
                    "gds_cell",
                    "array_side",
                    "center_x_um",
                    "center_y_um",
                ],
            )
            mappings = list(reader)
        self.assertEqual(len(mappings), 28)
        by_pin = {row["def_pin"]: row for row in mappings}
        self.assertEqual(len(by_pin), 28)

        for index, box in enumerate(boxes):
            def_pin = "PAD_%02d" % index
            row = by_pin[def_pin]
            containing_labels = [
                label
                for label, (x, y) in labels.items()
                if box.left <= x <= box.right and box.bottom <= y <= box.top
            ]
            self.assertEqual(len(containing_labels), 1, def_pin)
            self.assertEqual(row["source_pin_label"], containing_labels[0])
            self.assertEqual(row["gds_cell"], "pad_60_m1_tin")

            center_x = (box.left + box.right) * layout.dbu / 2.0
            center_y = (box.bottom + box.top) * layout.dbu / 2.0
            self.assertAlmostEqual(float(row["center_x_um"]), center_x, places=3)
            self.assertAlmostEqual(float(row["center_y_um"]), center_y, places=3)
            if abs(center_y - 720.0) < 1.0:
                array_side = "north"
            elif abs(center_y + 720.0) < 1.0:
                array_side = "south"
            elif abs(center_x - 720.0) < 1.0:
                array_side = "east"
            elif abs(center_x + 720.0) < 1.0:
                array_side = "west"
            else:
                self.fail("pad center is not on a source GDS edge: %s" % def_pin)
            self.assertEqual(row["array_side"], array_side)

    def test_librelane_alias_keeps_fixed_pad_geometry(self):
        canonical = def_pin_records(DEF.read_text(), "ML1")
        librelane = def_pin_records(LIBRELANE_DEF.read_text(), "M1")
        self.assertEqual(librelane, canonical)
        self.assertEqual(set(librelane), {"PAD_%02d" % i for i in range(28)})

        def pin_geometry(path):
            match = re.search(
                r"(?ms)^  PIN PAD\s+DIRECTION INOUT ;\s+USE ANALOG ;\s+PORT"
                r"\s+LAYER (\S+) ;\s+RECT ([^;]+) ;",
                path.read_text(),
            )
            self.assertIsNotNone(match)
            return match.groups()

        canonical_pin = pin_geometry(LEF)
        librelane_pin = pin_geometry(LIBRELANE_LEF)
        self.assertEqual(canonical_pin, ("ML1", "0.0 0.0 60.0 60.0"))
        self.assertEqual(librelane_pin, ("M1", canonical_pin[1]))

    def test_lef_and_liberty_are_physical_only_views(self):
        lef = LEF.read_text()
        macros = re.findall(r"(?m)^MACRO (\S+)$", lef)
        self.assertEqual(macros, ["pad_60_m1_tin"])
        self.assertIn("PIN PAD", lef)
        self.assertNotIn("MACRO ICPS2023_5_fd_base", lef)

        liberty = LIB.read_text()
        cells = re.findall(r"(?m)^  cell \(([^)]+)\)", liberty)
        self.assertEqual(cells, ["pad_60_m1_tin"])
        self.assertNotRegex(liberty, r"(?m)^\s*capacitance\s*:")
        self.assertNotRegex(liberty, r"(?m)^\s*max_transition\s*:")


if __name__ == "__main__":
    unittest.main()
