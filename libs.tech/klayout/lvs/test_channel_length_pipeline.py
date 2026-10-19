#!/usr/bin/env python3
"""Channel-length integrity: native PCell -> fab prep -> design/fab LVS.

Contract for a rectangle MOS (drawing layers carry the design intent, fab prep
owns the mask bias):

    drawing TIN gate length        = L           (no pre-bias in the PCell)
    fab-prep final TIN length      = L + 4 um    (2 um per end, SMeRL convention)
    fab-prep physical dopant window= L           (the dope mask is cut with the
                                                  drawing TIN, before the bias)
    extracted MOS L, both modes    = L           (the dopant gap is the channel)

The negative control pre-expands the drawing TIN by 2 um per end - the legacy
PCell convention.  fab prep then emits L+8 um TiN with an L+4 um dopant window;
both LVS modes must reject it.  The pre-consolidation deck subtracted a fixed
gate_overlap_um per end and matched such a layout silently.
"""

import math
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
raise "pcell_macro required" unless macro && File.file?(macro)
xml = REXML::Document.new(File.read(macro))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, macro)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
raise "native PCell library did not register" unless lib

kind = ($kind || "n").to_s
length = ($l || "6.0").to_f
width = ($w.nil? || $w.to_s.empty?) ? nil : $w.to_f
layout = RBA::Layout.new
layout.dbu = 0.001
params = {"l" => length}
params["w"] = width if width
pcell_id = lib.layout.pcell_id(kind == "p" ? "Pch" : "Nch")
raise "missing PCell" if pcell_id < 0
variant = layout.cell(layout.add_pcell_variant(lib, pcell_id, params))
top = layout.create_cell("mos_#{kind}_l#{length}")
top.insert(RBA::CellInstArray.new(variant.cell_index, RBA::Trans.new))

w = width ? [width, 13.0].max : (kind == "p" ? 40.0 : 20.0)
contact = 13.0
dgl = [4.0, (22.0 - length) / 2.0, (22.0 - length) / 2.0,
       ICPS2023_5NativeGeometry::MIN_GATE_BAY_UM].max
pitch = contact + length + 2.0 * dgl
gate_center = contact + dgl + length / 2.0

label = layout.layer(RBA::LayerInfo.new(13, 0))
def put(cell, layer, dbu, x, y, text)
  cell.shapes(layer).insert(
    RBA::Text.new(text, RBA::Trans.new(RBA::Point.new((x / dbu).round, (y / dbu).round))))
end
put(top, label, layout.dbu, contact / 2.0, w / 2.0, "S")
put(top, label, layout.dbu, contact / 2.0 + pitch, w / 2.0, "D")
put(top, label, layout.dbu, gate_center, -11.5, "G")
layout.write($output)
'''

LAYERS = {
    "design": dict(diff=(3, 0), tin=(5, 0), cnt=(7, 0), parea=(18, 0),
                   narea=(19, 0), cap=(16, 0), dio=(17, 0)),
    "fab": dict(diff=(4, 0), tin=(5, 0), cnt=(6, 0), parea=(2, 0),
                narea=(3, 0), cap=(16, 0), dio=(17, 0)),
}
CASES = [("n", 6.0, 20.0), ("n", 10.0, 20.0), ("n", 15.0, 20.0),
         ("p", 10.0, 40.0)]
NEGATIVE = ("n", 10.0, 20.0)


def um(value):
    return int(round(value / DBU))


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return result


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment())


def region(layout, cell, spec):
    index = layout.find_layer(pya.LayerInfo(spec[0], spec[1]))
    if index is None or index < 0:
        return pya.Region()
    return pya.Region(cell.begin_shapes_rec(index)).merged()


def shape_at(reg, x, y):
    probe = pya.Region(pya.Box(um(x - 0.05), um(y - 0.05),
                               um(x + 0.05), um(y + 0.05)))
    hit = reg.interacting(probe)
    if hit.is_empty():
        return None
    return max(hit.each(), key=lambda p: p.bbox().width() * p.bbox().height()).bbox()


def strip_at(reg, y):
    limit = reg.bbox().right * DBU + 5.0
    strip = pya.Region(pya.Box(um(-10.0), um(y - 0.05), um(limit), um(y + 0.05)))
    return reg & strip


def rect_clearance(a, b):
    dx = max(a.left - b.right, b.left - a.right, 0)
    dy = max(a.bottom - b.top, b.bottom - a.top, 0)
    return math.hypot(dx, dy) * DBU


def measure(gds, mode, kind, length, width):
    """Direct mask measurements: TiN gate length, dopant window, CNT clearance."""
    layout = pya.Layout()
    layout.read(str(gds))
    cell = layout.top_cell()
    spec = LAYERS[mode]
    diff = region(layout, cell, spec["diff"])
    tin = region(layout, cell, spec["tin"])
    cnt = region(layout, cell, spec["cnt"])
    cap = region(layout, cell, spec["cap"])
    dio = region(layout, cell, spec["dio"])
    parea = region(layout, cell, spec["parea"])
    narea = region(layout, cell, spec["narea"])

    contact, bay = 13.0, 8.0
    gate_center = contact + bay + length / 2.0

    gate = shape_at(strip_at(tin, width / 2.0), gate_center, width / 2.0)
    result = {"tin_length_um": round((gate.right - gate.left) * DBU, 3)}

    dio2 = dio.sized(um(2.0)) - (parea + narea)
    area = narea if kind == "n" else parea
    if mode == "design":
        dope = (area & diff).sized(um(2.0)) - (tin - cap + dio2)
    else:
        dope = area
    gap = shape_at(diff - dope, gate_center, width / 2.0)
    result["dope_window_um"] = round((gap.right - gap.left) * DBU, 3)

    row_cuts = [p.bbox() for p in cnt.each()
                if 0 < (p.bbox().bottom + p.bbox().top) / 2.0 * DBU < width]
    result["tin_cnt_clearance_um"] = round(
        min(rect_clearance(gate, box) for box in row_cuts), 3)
    return result


def expand_tin(source, destination, amount_um=2.0):
    layout = pya.Layout()
    layout.read(str(source))
    index = layout.layer(pya.LayerInfo(5, 0))
    for cell in layout.each_cell():
        if cell.child_instances() > 0:
            continue
        reg = pya.Region(cell.begin_shapes_rec(index))
        if reg.is_empty():
            continue
        reg.size(um(amount_um))
        cell.shapes(index).clear()
        cell.shapes(index).insert(reg)
    layout.write(str(destination))


def parse_extracted(path):
    values = []
    for line in path.read_text().splitlines():
        if line.startswith("M") and "L=" in line:
            length = re.search(r"L=([0-9.eE+-]+)", line)
            width = re.search(r"W=([0-9.eE+-]+)", line)
            values.append((float(length.group(1)), float(width.group(1))))
    return values


def write_reference(path, kind, length, width):
    name = ("nch" if kind == "n" else "pch") + "_l" + str(int(length))
    model = "NCH" if kind == "n" else "PCH"
    path.write_text(
        ".GLOBAL SUB\n"
        ".SUBCKT %s D G S\n"
        "M1 D G S SUB %s L=%dU W=%dU\n"
        ".ENDS\n"
        "XTOP D G S %s\n"
        ".END\n" % (name, model, length, width, name),
        encoding="utf-8",
    )


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for the pipeline")
class ChannelLengthPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        cls.generator = cls.work / "gen_mos.rb"
        cls.generator.write_text(GENERATOR_RUBY, encoding="utf-8")
        cls.measured = {}
        cls.lvs = {}
        for kind, length, width in CASES:
            cls.prepare(kind, length, width, "fixed", negative=False)
        cls.prepare(*NEGATIVE, "legacy_tin", negative=True)

    @classmethod
    def prepare(cls, kind, length, width, scenario, negative):
        tag = "%s_l%d_%s" % (kind, length, scenario)
        design = cls.work / (tag + "_design.gds")
        fab = cls.work / (tag + "_fab.gds")
        reference = cls.work / (tag + ".cir")
        result = run([KLAYOUT, "-b", "-r", str(cls.generator),
                      "-rd", "pcell_macro=" + str(PCELL_MACRO),
                      "-rd", "output=" + str(design),
                      "-rd", "kind=" + kind, "-rd", "l=" + str(length),
                      "-rd", "w=" + str(width)])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        if negative:
            expanded = cls.work / (tag + "_expanded.gds")
            expand_tin(design, expanded, 2.0)
            design = expanded
        result = run([KLAYOUT, "-b", "-r", str(FAB_PREP),
                      "-rd", "input=" + str(design), "-rd", "output=" + str(fab),
                      "-rd", "process_profile=" + ONE_METAL])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        write_reference(reference, kind, length, int(width))
        cls.measured[(kind, length, scenario)] = {
            "design": measure(design, "design", kind, length, width),
            "fab": measure(fab, "fab", kind, length, width),
        }
        for mode, gds in (("design", design), ("fab", fab)):
            report = cls.work / ("%s_%s.lyrdb" % (tag, mode))
            output = cls.work / ("%s_%s_extracted.cir" % (tag, mode))
            result = run([KLAYOUT, "-b", "-r", str(LVS_DECK),
                          "-rd", "input=" + str(gds),
                          "-rd", "schematic=" + str(reference),
                          "-rd", "layer_mode=" + mode,
                          "-rd", "report=" + str(report),
                          "-rd", "output=" + str(output),
                          "-rd", "process_profile=" + ONE_METAL])
            details = result.stdout + result.stderr
            if output.exists():
                details += "\n" + output.read_text()
            cls.lvs[(kind, length, scenario, mode)] = (result, details, output)

    def test_fab_prep_keeps_drawing_length_and_biases_only_tin(self):
        for kind, length, width in CASES:
            with self.subTest(device="%s_l%d" % (kind, length)):
                measured = self.measured[(kind, length, "fixed")]
                design, fab = measured["design"], measured["fab"]
                self.assertEqual(design["tin_length_um"], length)
                self.assertEqual(fab["tin_length_um"], length + 4.0)
                self.assertEqual(design["dope_window_um"], length)
                self.assertEqual(fab["dope_window_um"], length)
                # Drawing clearance recommendation >= 10 um, final mask >= 8 um.
                self.assertGreaterEqual(design["tin_cnt_clearance_um"], 10.0)
                self.assertGreaterEqual(fab["tin_cnt_clearance_um"], 8.0)
                self.assertAlmostEqual(design["tin_cnt_clearance_um"],
                                       fab["tin_cnt_clearance_um"] + 2.0, places=3)

    def test_both_lvs_modes_extract_the_physical_channel(self):
        for kind, length, width in CASES:
            for mode in ("design", "fab"):
                with self.subTest(device="%s_l%d" % (kind, length), mode=mode):
                    result, details, output = self.lvs[(kind, length, "fixed", mode)]
                    self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)
                    devices = parse_extracted(output)
                    self.assertEqual(len(devices), 1, details)
                    self.assertAlmostEqual(devices[0][0], length, places=3)
                    self.assertAlmostEqual(devices[0][1], width, places=3)

    def test_pre_expanded_tin_geometry_is_rejected_by_both_modes(self):
        kind, length, width = NEGATIVE
        measured = self.measured[(kind, length, "legacy_tin")]
        design, fab = measured["design"], measured["fab"]
        # The legacy convention re-creates the historical double bias: L+4 um
        # drawing TiN becomes an L+8 um final mask over an L+4 um dopant window.
        self.assertEqual(design["tin_length_um"], length + 4.0)
        self.assertEqual(fab["tin_length_um"], length + 8.0)
        self.assertEqual(design["dope_window_um"], length + 4.0)
        self.assertEqual(fab["dope_window_um"], length + 4.0)
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                result, details, output = self.lvs[(kind, length, "legacy_tin", mode)]
                self.assertIn("ICPS2023_5 LVS NO MATCH", details)
                devices = parse_extracted(output)
                self.assertEqual(len(devices), 1, details)
                self.assertAlmostEqual(devices[0][0], length + 4.0, places=3)


if __name__ == "__main__":
    unittest.main()
