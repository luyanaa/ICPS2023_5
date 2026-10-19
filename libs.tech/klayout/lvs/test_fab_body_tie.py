#!/usr/bin/env python3
"""Nch body-tie extraction and fail-closed PMOS island checks.

Nch: design TIN_BLK selects the tie; fab mode identifies it physically - the
channel lies in an N-ch (body dope) component that reaches a contacted P+ dot.
PMOS4 is unsupported. A separate experimental N+ SOI island is not a PMOS body
contact; an N+ dot mixed into the PMOS active island must fail closed.

Uncontacted Nch tie dots fail closed.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
ONE_METAL = "icps2023_5_1m"
DBU = 0.001
GENERATOR_RUBY = r'''require "rexml/document"
macro = $pcell_macro
xml = REXML::Document.new(File.read(macro))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, macro)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
kind = $kind.to_s == "p" ? "p" : "n"
tied = $tied.to_s == "1"
length = kind == "p" ? 6.0 : 10.0
width = kind == "p" ? 40.0 : 20.0
params = {"l" => length, "w" => width}
if kind == "n"
  params["body_tie"] = true if tied
  pcell = "Nch"
else
  pcell = "Pch"
end
layout = RBA::Layout.new
layout.dbu = 0.001
id = lib.layout.pcell_id(pcell)
variant = layout.cell(layout.add_pcell_variant(lib, id, params))
top = layout.create_cell(kind == "p" ? "pch_plain_check" :
                         "nch_#{tied ? 'body_tie' : 'plain'}_check")
top.insert(RBA::CellInstArray.new(variant.cell_index, RBA::Trans.new))
label = layout.layer(RBA::LayerInfo.new(13, 0))
dgl = [4.0, (22.0 - length) / 2.0, (22.0 - length) / 2.0,
       ICPS2023_5NativeGeometry::MIN_GATE_BAY_UM].max
pitch = 13.0 + length + 2.0 * dgl
gate_center = 13.0 + dgl + length / 2.0
points = [["S", 6.5, width / 2.0], ["D", 6.5 + pitch, width / 2.0],
          ["G", gate_center, -11.5]]
if tied && kind == "n"
  tie_y = width + 13.0 + 6.5
  tie_x = 2.0 * 13.0 + length + 2.0 * dgl - 6.5
  points << ["B", tie_x, tie_y]
end
points.each do |name, x, y|
  top.shapes(label).insert(RBA::Text.new(name, RBA::Trans.new(
    RBA::Point.new((x / 0.001).round, (y / 0.001).round))))
end
layout.write($output)
'''
TIE_CNT_CENTER = {"n": (45.5, 39.5)}


def reference(kind, tied):
    if kind == "p":
        name = "pch_plain_check"
    else:
        name = "nch_%s_check" % ("body_tie" if tied else "plain")
    if kind == "p" and tied:
        raise ValueError("PMOS body-tie reference netlists are unsupported")
    if kind == "n" and tied:
        return (".GLOBAL SUB\n"
                ".SUBCKT %s D G S B\n"
                "M1 D G S SUB B NCH L=10U W=20U\n"
                ".ENDS\n"
                "XTOP D G S B %s\n.END\n"
                % (name, name))
    model, length, width = ("NCH", 10, 20) if kind == "n" else ("PCH", 6, 40)
    return (".GLOBAL SUB\n"
            ".SUBCKT %s D G S\n"
            "M1 D G S SUB %s L=%dU W=%dU\n"
            ".ENDS\n"
            "XTOP D G S %s\n.END\n"
            % (name, model, length, width, name))


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return result


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment())


def generate(generator, output, kind, tied):
    return run([KLAYOUT, "-b", "-r", str(generator),
                "-rd", "pcell_macro=" + str(PCELL_MACRO),
                "-rd", "output=" + str(output),
                "-rd", "kind=" + kind,
                "-rd", "tied=" + ("1" if tied else "0")])


def fab_prepare(design, fab):
    return run([KLAYOUT, "-b", "-r", str(FAB_PREP),
                "-rd", "input=" + str(design), "-rd", "output=" + str(fab),
                "-rd", "process_profile=" + ONE_METAL])


def delete_tie_contact(source, destination, kind):
    layout = pya.Layout()
    layout.read(str(source))
    index = layout.layer(pya.LayerInfo(7, 0))
    target = TIE_CNT_CENTER[kind]
    removed = 0
    for cell in layout.each_cell():
        if cell.child_instances() > 0:
            continue
        shapes = cell.shapes(index)
        for shape in list(shapes.each()):
            box = shape.bbox()
            center = ((box.left + box.right) / 2.0 * DBU,
                      (box.bottom + box.top) / 2.0 * DBU)
            if (abs(center[0] - target[0]) < 0.01 and
                    abs(center[1] - target[1]) < 0.01):
                shapes.erase(shape)
                removed += 1
    if removed != 1:
        raise AssertionError("expected exactly one tie contact cut, removed=%d"
                             % removed)
    layout.write(str(destination))


def add_in_island_dot(source, destination):
    """Add a contacted N+ dot to create a mixed-implant PMOS failure case."""
    layout = pya.Layout()
    layout.read(str(source))
    leaf = next(cell for cell in layout.each_cell()
                if cell.child_instances() == 0)
    leaf.shapes(layout.layer(pya.LayerInfo(19, 0))).insert(
        pya.Box(int(12 / DBU), int(28 / DBU), int(16 / DBU), int(32 / DBU)))
    leaf.shapes(layout.layer(pya.LayerInfo(7, 0))).insert(
        pya.Box(int(13 / DBU), int(29 / DBU), int(15 / DBU), int(31 / DBU)))
    layout.write(str(destination))


def lvs(input_gds, ref, mode, report, output):
    return run([KLAYOUT, "-b", "-r", str(LVS_DECK),
                "-rd", "input=" + str(input_gds),
                "-rd", "schematic=" + str(ref),
                "-rd", "layer_mode=" + mode,
                "-rd", "report=" + str(report),
                "-rd", "output=" + str(output),
                "-rd", "process_profile=" + ONE_METAL])


def extracted_devices(path):
    if not path.exists():
        return []
    return [(m.group(1), m.group(2))
            for m in (re.match(r"(M\S+)\s.*?(\w+) L=", line)
                      for line in path.read_text().splitlines() if line.startswith("M"))
            if m]


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for LVS")
class BodyTiePhysicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        cls.generator = cls.work / "gen.rb"
        cls.generator.write_text(GENERATOR_RUBY, encoding="utf-8")
        cls.layouts = {}
        cls.references = {}
        for kind in ("n", "p"):
            configurations = ((True, "tied"), (False, "plain")) if kind == "n" else ((False, "plain"),)
            for tied, suffix in configurations:
                name = "%s_%s" % (kind, suffix)
                design = cls.work / (name + "_design.gds")
                fab = cls.work / (name + "_fab.gds")
                result = generate(cls.generator, design, kind, tied)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                result = fab_prepare(design, fab)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                cls.layouts[name] = {"design": design, "fab": fab}
                ref = cls.work / (name + ".cir")
                ref.write_text(reference(kind, tied), encoding="utf-8")
                cls.references[name] = ref
            if kind == "n":
                name = "n_missing_cnt"
                broken = cls.work / (name + "_design.gds")
                delete_tie_contact(cls.layouts["n_tied"]["design"], broken, "n")
                broken_fab = cls.work / (name + "_fab.gds")
                result = fab_prepare(broken, broken_fab)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                cls.layouts[name] = {"design": broken, "fab": broken_fab}
                cls.references[name] = cls.references["n_tied"]
        # A mixed N+/P+ PMOS island is a negative, fail-closed layout case.
        dot_design = cls.work / "p_in_island_dot_design.gds"
        add_in_island_dot(cls.layouts["p_plain"]["design"], dot_design)
        dot_fab = cls.work / "p_in_island_dot_fab.gds"
        result = fab_prepare(dot_design, dot_fab)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.layouts["p_in_island_dot"] = {"design": dot_design, "fab": dot_fab}
        cls.references["p_in_island_dot"] = cls.references["p_plain"]


    def lvs_details(self, name, mode, reference_name=None):
        report = self.work / ("%s_%s.lyrdb" % (name, mode))
        output = self.work / ("%s_%s.cir" % (name, mode))
        result = lvs(self.layouts[name][mode], self.references[reference_name or name],
                     mode, report, output)
        return result, result.stdout + result.stderr, output

    def test_tied_nmos_extracts_nmos4_in_both_modes(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs_details("n_tied", mode)
                self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)
                devices = extracted_devices(output)
                self.assertEqual(len(devices), 1, details)
                self.assertEqual(devices[0][1], "NMOS4", details)

    def test_ordinary_pmos_remains_three_terminal(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs_details("p_plain", mode)
                self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)
                devices = extracted_devices(output)
                self.assertEqual(len(devices), 1, details)
                self.assertEqual(devices[0][1], "PMOS", details)

    def test_mixed_pmos_island_fails_closed_without_output(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs_details("p_in_island_dot", mode)
                self.assertNotEqual(result.returncode, 0, details)
                self.assertFalse(output.exists(), details)

    def test_plain_nmos_is_not_mistaken_for_a_body_tie(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs_details(
                    "n_plain", mode, reference_name="n_tied")
                self.assertIn("ICPS2023_5 LVS NO MATCH", details)
                devices = extracted_devices(output)
                self.assertEqual(len(devices), 1, details)
                self.assertEqual(devices[0][1], "NMOS", details)

    def test_uncontacted_nmos_tie_dots_fail_closed(self):
        cases = (("fab", "uncontacted P+ tie dot"),
                 ("design", "P+ dot has no contact"))
        for mode, message in cases:
            with self.subTest(mode=mode):
                result, details, _ = self.lvs_details("n_missing_cnt", mode)
                self.assertNotEqual(result.returncode, 0, details)
                self.assertIn(message, details)


if __name__ == "__main__":
    unittest.main()
