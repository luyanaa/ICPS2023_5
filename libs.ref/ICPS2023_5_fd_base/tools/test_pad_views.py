#!/usr/bin/env python3
"""Check generated pad LEF/DEF identities against the unchanged frame GDS."""

import re
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
GDS = ROOT / "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"
LEF = ROOT / "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef"
DEF = ROOT / "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def"
LIB = ROOT / "libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib"


def def_pin_records(text):
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
            r"\+ LAYER ML1 \( 0 0 \) \( (\d+) (\d+) \)", body
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
        ml1 = layout.find_layer(pya.LayerInfo(8, 0))
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
