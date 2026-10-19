#!/usr/bin/env python3
"""Foundry-mask reference MOS: independently specified masks vs the PCell.

The reference cell below is drawn directly in foundry mask numbering, with the
device parameters chosen from the process convention instead of copied from
fab-prep output:

    drawing channel L        = 10 um
    final TiN gate           = L + 4 um          (2 um per end)
    physical dopant window   = L                  (dope mask cut with drawing TiN)
    TiN - source/drain CNT   = 10 um              (drawing floor 12 um minus bias)

The tests pin the mask geometry, run fab-mode LVS and fab-mode PEX on it, and
prove that fab-prep of the native PCell produces the same device core: same SOI,
N-dope, N-ch and CNT polygons, and the same TiN window at the channel row.
"""

import importlib.util
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
PEX_DECK = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
ONE_METAL = "icps2023_5_1m"
DBU = 0.001


def _load_module(name, path):
    loader = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


model_binding = _load_module(
    "icps_model_binding", ROOT / "libs.tech/stdcell/model_binding.py")
xyce_b4soi = _load_module(
    "icps_xyce_b4soi", ROOT / "libs.tech/ngspice/tools/xyce_b4soi.py")

LENGTH = 10.0
WIDTH = 20.0
CONTACT = 13.0
BAY = 8.0
PITCH = CONTACT + LENGTH + 2.0 * BAY
ISLAND = (0.0, 0.0, 52.0, 20.0)
DOPE_MASK = [(-2.0, -2.0, 21.0, 22.0), (31.0, -2.0, 54.0, 22.0)]
NCH_MASK = (21.0, 0.0, 31.0, 20.0)
FINAL_TIN = (19.0, -6.0, 33.0, 26.0)
GATE_PADS = [(19.5, -18.0, 32.5, -5.0), (19.5, 25.0, 32.5, 38.0)]
CNT_CUTS = {"S": (4.0, 7.5, 9.0, 12.5), "D": (43.0, 7.5, 48.0, 12.5),
            "GL": (23.5, -14.0, 28.5, -9.0), "GU": (23.5, 29.0, 28.5, 34.0)}
ML1_PADS = {"S": (0.0, 3.5, 13.0, 16.5), "D": (39.0, 3.5, 52.0, 16.5),
            "GL": (17.0, -22.0, 35.0, -5.0), "GU": (17.0, 25.0, 35.0, 42.0)}
LABELS = {"S": (6.5, 10.0), "D": (45.5, 10.0), "G": (26.0, -11.5)}
REFERENCE_NETLIST = """.GLOBAL SUB
.SUBCKT fab_reference D G S
M1 D G S SUB NCH L=10U W=20U
.ENDS
XTOP D G S fab_reference
.END
"""
PCell_RUBY = r'''require "rexml/document"
macro = $pcell_macro
xml = REXML::Document.new(File.read(macro))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, macro)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
layout = RBA::Layout.new
layout.dbu = 0.001
id = lib.layout.pcell_id("Nch")
variant = layout.cell(layout.add_pcell_variant(lib, id, {"l" => 10.0, "w" => 20.0}))
top = layout.create_cell("nch_reference_source")
top.insert(RBA::CellInstArray.new(variant.cell_index, RBA::Trans.new))
label = layout.layer(RBA::LayerInfo.new(13, 0))
[["S", 6.5, 10.0], ["D", 45.5, 10.0], ["G", 26.0, -11.5]].each do |name, x, y|
  top.shapes(label).insert(RBA::Text.new(name, RBA::Trans.new(
    RBA::Point.new((x / 0.001).round, (y / 0.001).round))))
end
layout.write($output)
'''


def um(value):
    return int(round(value / DBU))


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return result


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment())


def build_reference(path):
    layout = pya.Layout()
    layout.dbu = DBU
    cell = layout.create_cell("fab_reference")

    def rect(layer, dt, coords):
        cell.shapes(layout.layer(pya.LayerInfo(layer, dt))).insert(
            pya.Box(*(um(v) for v in coords)))

    rect(4, 0, ISLAND)
    for dope in DOPE_MASK:
        rect(3, 0, dope)
    rect(13, 0, NCH_MASK)
    rect(5, 0, FINAL_TIN)
    for pad in GATE_PADS:
        rect(5, 0, pad)
    for cut in CNT_CUTS.values():
        rect(6, 0, cut)
    for pad in ML1_PADS.values():
        rect(7, 0, pad)
    for name, (x, y) in LABELS.items():
        cell.shapes(layout.layer(pya.LayerInfo(13, 1))).insert(
            pya.Text(name, pya.Trans(pya.Point(um(x), um(y)))))
    layout.write(str(path))
    return path


def region(layout, cell, num, dt=0):
    index = layout.find_layer(pya.LayerInfo(num, dt))
    if index is None or index < 0:
        return pya.Region()
    return pya.Region(cell.begin_shapes_rec(index)).merged()


def polygon_boxes(reg):
    return sorted(tuple(round(v * DBU, 3) for v in
                        (p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top))
                  for p in reg.each())


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for the reference")
class FabReferenceGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        cls.reference = build_reference(cls.work / "fab_reference.gds")
        cls.netlist = cls.work / "fab_reference.cir"
        cls.netlist.write_text(REFERENCE_NETLIST, encoding="utf-8")

        cls.pcell_design = cls.work / "pcell_design.gds"
        cls.pcell_fab = cls.work / "pcell_fab.gds"
        ruby = cls.work / "pcell.rb"
        ruby.write_text(PCell_RUBY, encoding="utf-8")
        result = run([KLAYOUT, "-b", "-r", str(ruby),
                      "-rd", "pcell_macro=" + str(PCELL_MACRO),
                      "-rd", "output=" + str(cls.pcell_design)])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        result = run([KLAYOUT, "-b", "-r", str(FAB_PREP),
                      "-rd", "input=" + str(cls.pcell_design),
                      "-rd", "output=" + str(cls.pcell_fab),
                      "-rd", "process_profile=" + ONE_METAL])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

        cls.extracted = cls.work / "reference_extracted.cir"
        result = run([KLAYOUT, "-b", "-r", str(LVS_DECK),
                      "-rd", "input=" + str(cls.reference),
                      "-rd", "schematic=" + str(cls.netlist),
                      "-rd", "layer_mode=fab",
                      "-rd", "report=" + str(cls.work / "reference.lyrdb"),
                      "-rd", "output=" + str(cls.extracted),
                      "-rd", "process_profile=" + ONE_METAL])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.pex_output = cls.work / "reference_pex.spice"
        result = run(["python3", str(PEX_DECK), "--input", str(cls.reference),
                      "--layer-mode", "fab", "--output", str(cls.pex_output),
                      "--process-profile", ONE_METAL])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_reference_masks_have_the_intended_geometry(self):
        layout = pya.Layout()
        layout.read(str(self.reference))
        cell = layout.top_cell()
        diff = region(layout, cell, 4)
        dope = region(layout, cell, 3)
        tin = region(layout, cell, 5)
        cnt = region(layout, cell, 6)

        window = (diff - dope).bbox()
        self.assertAlmostEqual(window.width() * DBU, LENGTH, places=3)
        strip = pya.Region(pya.Box(um(0), um(9.95), um(52), um(10.05)))
        gate = (tin & strip).bbox()
        self.assertAlmostEqual(gate.width() * DBU, LENGTH + 4.0, places=3)
        self.assertAlmostEqual((gate.left - um(0)) * DBU, 19.0, places=3)

        source_cut = pya.Region(pya.Box(*(um(v) for v in CNT_CUTS["S"])))
        clearance = (gate.left - source_cut.bbox().right) * DBU
        self.assertAlmostEqual(clearance, 10.0, places=3)

    def test_fab_prep_reproduces_the_reference_device_core(self):
        reference = pya.Layout()
        reference.read(str(self.reference))
        reference_cell = reference.top_cell()
        converted = pya.Layout()
        converted.read(str(self.pcell_fab))
        converted_cell = converted.top_cell()
        for layer, datatype, name in ((4, 0, "SOI"), (3, 0, "N-dope"),
                                      (13, 0, "N-ch"), (6, 0, "CNT")):
            with self.subTest(layer=name):
                expected = polygon_boxes(region(reference, reference_cell, layer, datatype))
                actual = polygon_boxes(region(converted, converted_cell, layer, datatype))
                self.assertEqual(actual, expected, name)
        strip = pya.Region(pya.Box(um(0), um(9.95), um(52), um(10.05)))
        expected_gate = polygon_boxes(region(reference, reference_cell, 5) & strip)
        actual_gate = polygon_boxes(region(converted, converted_cell, 5) & strip)
        self.assertEqual(actual_gate, expected_gate, "TiN gate window")

    def test_fab_lvs_extracts_the_reference_device(self):
        text = self.extracted.read_text()
        mos_lines = [line for line in text.splitlines() if line.startswith("M")]
        self.assertEqual(len(mos_lines), 1, text)
        self.assertIn("NMOS L=10U W=20U", mos_lines[0])

    def test_fab_lvs_matches_the_reference_schematic(self):
        report = self.work / "reference.lyrdb"
        result = run([KLAYOUT, "-b", "-r", str(LVS_DECK),
                      "-rd", "input=" + str(self.reference),
                      "-rd", "schematic=" + str(self.netlist),
                      "-rd", "layer_mode=fab",
                      "-rd", "report=" + str(report),
                      "-rd", "output=" + str(self.work / "reference_check.cir"),
                      "-rd", "process_profile=" + ONE_METAL])
        self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)",
                      result.stdout + result.stderr)

    def test_fab_pex_runs_on_the_reference_device(self):
        text = self.pex_output.read_text()
        self.assertIn('"device":"NMOS@26.000000,10.000000"', text)
        self.assertIn('"gate":"MOS_GATE_0001"', text)
        self.assertIn('"drain_contact_cuts":1', text)
        self.assertIn('"source_contact_cuts":1', text)
        self.assertIn('"passed": true', text)

    def assemble(self, model_library, name):
        output = self.work / name
        result = run(["python3", str(ASSEMBLER),
                      "--lvs", str(self.extracted),
                      "--pex", str(self.pex_output),
                      "--schematic", str(self.netlist),
                      "--top", "fab_reference",
                      "--model-library", str(model_library),
                      "--output", str(output)])
        return result, output

    def assert_library_reference(self, text, library):
        line = next((l for l in text.splitlines() if l.startswith(".lib ")), None)
        self.assertIsNotNone(line, text)
        self.assertEqual(Path(line.split('"')[1]).name, Path(library).name, line)

    def test_assembled_flow_smoke_runs_on_the_nominal_card(self):
        library = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
        result, output = self.assemble(library, "assembled_nominal.spice")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = output.read_text()
        self.assert_library_reference(text, library)
        self.assertIn("M0001 M0001_DRAIN MOS_GATE_0001 M0001_SOURCE E_SUB "
                      "nch L=10U W=20U", text)
        self.assertIn('"body_state":"floating_body"', text)
        self.assertIn('"e":"E_SUB"', text)
        self.assertIn("RCNT0001 D M0001_DRAIN 150", text)

        ngspice = shutil.which("ngspice")
        if not ngspice:
            self.skipTest("ngspice is required for the assembled-flow smoke run")
        bench = self.work / "smoke.cir"
        bench.write_text(
            "* migrated-foundry-flow smoke: assembled reference device\n"
            '.include "%s"\n'
            "X1 D G S SUB fab_reference\n"
            "VD D 0 1.5\n"
            "VG G 0 1.5\n"
            "VS S 0 0\n"
            "E1 SUB 0 0 0 0\n"
            ".op\n"
            ".print op v(d) v(g) i(VD)\n"
            ".end\n" % output,
            encoding="utf-8")
        result = run([ngspice, "-b", str(bench)])
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, details)
        self.assertIn("Operating Point", details)
        row = re.search(r"^0\t([^\n]+)$", details, re.MULTILINE)
        self.assertIsNotNone(row, details)
        drain_current = float(row.group(1).split()[2])
        self.assertLess(drain_current, 0.0, details)
        self.assertLess(abs(drain_current), 5e-4, details)
        self.assertGreater(abs(drain_current), 5e-5, details)

    def test_assembled_flow_binds_each_bridge_card(self):
        binding = model_binding.load()
        self.assertEqual(binding["engine"], "xyce")
        self.assertEqual(binding["engine_level"], 70450)
        self.assertIn("b4soi_fd_bridge_experimental", binding["library"])

        for name in ("b4soi_fd_bridge_experimental",
                     "b4soi_unified_bridge_experimental"):
            with self.subTest(card=name):
                library = ROOT / "libs.tech/ngspice" / (name + ".lib.spice")
                include = ROOT / "libs.tech/ngspice/models" / (name + ".inc")
                self.assertTrue(library.is_file(), library)
                self.assertTrue(include.is_file(), include)
                ported = xyce_b4soi.port_card(include.read_text(encoding="utf-8"),
                                              binding["engine_level"])
                self.assertIn("LEVEL=70450", ported)
                card = self.work / (name + "_card_xyce.inc")
                card.write_text(ported, encoding="utf-8")
                selector = self.work / (name + "_selector.lib.spice")
                selector.write_text(
                    "* test selector for the ported %s card; Xyce 7.10 only\n"
                    ".lib b4soi\n.include \"%s\"\n.endl b4soi\n" % (name, card),
                    encoding="utf-8")
                result, output = self.assemble(selector,
                                               "assembled_%s.spice" % name)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                text = output.read_text()
                self.assert_library_reference(text, selector)
                self.assertIn("L=10U W=20U", text)

                # Real Xyce smoke. W20/L10 is outside the card's W24/L6 fit
                # scope: this checks that the assembled foundry-GDS netlist
                # binds and converges, not a calibrated current.
                if not xyce_b4soi.available():
                    self.skipTest("Xyce 7.10 is required for the bridge smoke")
                bench = self.work / ("bench_%s.cir" % name)
                bench.write_text(
                    "* %s smoke; device outside the W24/L6 fit scope\n"
                    '.include "%s"\n'
                    "X1 D G S SUB fab_reference\n"
                    "VD D 0 1.5\nVG G 0 1.5\nVS S 0 0\nE1 SUB 0 0 0 0\n"
                    ".dc VD 1.5 1.5 1\n"
                    ".print dc v(D) i(VD)\n"
                    ".end\n" % (name, output),
                    encoding="utf-8")
                result = subprocess.run([xyce_b4soi.XYCE, bench.name],
                                        cwd=str(self.work), text=True,
                                        capture_output=True,
                                        env=xyce_b4soi.xyce_env())
                details = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, details)
                prn = Path(str(bench) + ".prn")
                self.assertTrue(prn.is_file(), details)
                rows = [line.split() for line in prn.read_text().splitlines()
                        if line.strip() and line.split()[0].isdigit()]
                self.assertTrue(rows, prn.read_text())
                current = float(rows[0][2])
                self.assertLess(current, 0.0, prn.read_text())
                self.assertLess(abs(current), 5e-4, prn.read_text())
                self.assertGreater(abs(current), 5e-5, prn.read_text())


if __name__ == "__main__":
    unittest.main()
