#!/usr/bin/env python3
"""Body-tie extraction for both polarities, design and fab modes.

NMOS: design TIN_BLK selects the tie; fab mode identifies it physically - the
channel lies in an N-ch (body dope) component that reaches a contacted P+ dot.
PMOS: there is no P-ch mask and NWL has no fab mask, so the body path is the
PMOS SOI island itself.  A contacted N+ dot inside a PMOS island is the fourth
terminal; this rule is physical in both modes.  The dot is kept out of the
source/drain region, so the body net never merges into the source.

Uncontacted tie dots fail closed for both polarities.
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
  params["with_nsubcont"] = true if tied
  pcell = "Pch"
end
layout = RBA::Layout.new
layout.dbu = 0.001
id = lib.layout.pcell_id(pcell)
variant = layout.cell(layout.add_pcell_variant(lib, id, params))
top = layout.create_cell("#{kind == 'p' ? 'pch' : 'nch'}_body_tie_check")
top.insert(RBA::CellInstArray.new(variant.cell_index, RBA::Trans.new))
label = layout.layer(RBA::LayerInfo.new(13, 0))
dgl = [4.0, (22.0 - length) / 2.0, (22.0 - length) / 2.0,
       ICPS2023_5NativeGeometry::MIN_GATE_BAY_UM].max
pitch = 13.0 + length + 2.0 * dgl
gate_center = 13.0 + dgl + length / 2.0
points = [["S", 6.5, width / 2.0], ["D", 6.5 + pitch, width / 2.0],
          ["G", gate_center, -11.5]]
if tied
  tie_y = kind == "n" ? width + 13.0 + 6.5 :
          [width + 18.0, [25.0, 22.0, 11.0].max + length / 2.0 + 4.0].max
  tie_x = kind == "n" ? 2.0 * 13.0 + length + 2.0 * dgl - 6.5 : 0.0
  points << ["B", tie_x, tie_y]
end
points.each do |name, x, y|
  top.shapes(label).insert(RBA::Text.new(name, RBA::Trans.new(
    RBA::Point.new((x / 0.001).round, (y / 0.001).round))))
end
layout.write($output)
'''
TIE_CNT_CENTER = {"n": (45.5, 39.5), "p": (0.0, 58.0)}


def reference(kind, tied):
    name = "nch_body_tie_check" if kind == "n" else "pch_body_tie_check"
    if kind == "n":
        model, length, width = "NCH", 10, 20
    else:
        model, length, width = "PCH", 6, 40
    if tied:
        return (".GLOBAL SUB\n"
                ".SUBCKT %s D G S B\n"
                "M1 D G S SUB B %s L=%dU W=%dU\n"
                ".ENDS\n"
                "XTOP D G S B %s\n.END\n"
                % (name, model, length, width, name))
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
    """Add the only mask-visible PMOS tie signature: an N+ dot inside the island."""
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
            for tied, suffix in ((True, "tied"), (False, "plain")):
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
            name = "%s_missing_cnt" % kind
            broken = cls.work / (name + "_design.gds")
            delete_tie_contact(cls.layouts["%s_tied" % kind]["design"], broken, kind)
            broken_fab = cls.work / (name + "_fab.gds")
            result = fab_prepare(broken, broken_fab)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.layouts[name] = {"design": broken, "fab": broken_fab}
            cls.references[name] = cls.references["%s_tied" % kind]
        # In-island N+ dot on a plain PMOS: the mask-visible tie signature.
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

    def test_tied_pmos_is_not_mask_visible_and_must_not_match(self):
        """The PCell's off-island n-substrate contact relies on NWL, which has no
        fab mask; the LVS cannot see a body tie there, so a four-terminal
        reference must not match (documented limitation, not a silent pass)."""
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs_details("p_tied", mode)
                self.assertIn("ICPS2023_5 LVS NO MATCH", details)
                devices = extracted_devices(output)
                self.assertEqual(len(devices), 1, details)
                self.assertEqual(devices[0][1], "PMOS", details)

    def test_in_island_pmos_tie_fails_closed(self):
        """An N+ dot inside a PMOS island is the only mask-visible tie
        signature; PMOS4 extraction is experimental and must abort."""
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, _ = self.lvs_details("p_in_island_dot", mode)
                self.assertNotEqual(result.returncode, 0, details)
                self.assertIn("PMOS body-tie extraction is not supported yet",
                              details)

    def test_plain_devices_are_not_mistaken_for_ties(self):
        for kind, expected in (("n", "NMOS"), ("p", "PMOS")):
            for mode in ("design", "fab"):
                with self.subTest(kind=kind, mode=mode):
                    result, details, output = self.lvs_details(
                        "%s_plain" % kind, mode,
                        reference_name="%s_tied" % kind)
                    self.assertIn("ICPS2023_5 LVS NO MATCH", details)
                    devices = extracted_devices(output)
                    self.assertEqual(len(devices), 1, details)
                    self.assertEqual(devices[0][1], expected, details)

    def test_uncontacted_tie_dots_fail_closed(self):
        cases = (("n", "fab", "uncontacted P+ tie dot"),
                 ("n", "design", "P+ dot has no contact"))
        for kind, mode, message in cases:
            with self.subTest(kind=kind, mode=mode):
                result, details, _ = self.lvs_details(
                    "%s_missing_cnt" % kind, mode)
                self.assertNotEqual(result.returncode, 0, details)
                self.assertIn(message, details)


if __name__ == "__main__":
    unittest.main()
