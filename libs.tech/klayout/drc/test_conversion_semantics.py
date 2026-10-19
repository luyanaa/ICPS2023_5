#!/usr/bin/env python3
"""Drawing -> foundry conversion semantics that change manufacturing geometry.

A. Doping masks.  Fab P_DIFF/N_DIFF are not the drawing PAREA/NAREA: the TiN
   subtraction cuts the channel window out of the dope mask, and the fab LVS
   derives the MOS length from that window.
B. TiN bias.  `interacting` selects whole polygons, so the 2 um bias applies to
   every connected TiN conductor that touches the island - the gate window, its
   off-island landing pads and any jumper bar wired to it - clipped only by the
   (DIFF + TiN) extents.  It is not a local TI/TiN-edge property.
C. N-ch mask.  The legacy deck auto-generated a PWell from
   `narea.interacting(tin).sized(2)`; the default conversion emits only the
   explicit drawing 21/0.  `-rd legacy_pwell=true` reproduces the legacy mask.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = os.environ.get("KLAYOUT", "klayout")
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
ONE_METAL = "icps2023_5_1m"
DBU = 0.001


def um(value):
    return int(round(value / DBU))


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return result


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment())


def fab_prep(design, fab, legacy_pwell=False):
    command = [KLAYOUT, "-b", "-r", str(FAB_PREP), "-rd", "input=" + str(design),
               "-rd", "output=" + str(fab), "-rd", "process_profile=" + ONE_METAL]
    if legacy_pwell:
        command += ["-rd", "legacy_pwell=true"]
    return run(command)


def load(path):
    layout = pya.Layout()
    layout.read(str(path))
    return layout


def region(layout, number, datatype=0):
    cell = layout.top_cell()
    cell.flatten(True)
    index = layout.find_layer(pya.LayerInfo(number, datatype))
    if index is None or index < 0:
        return pya.Region()
    return pya.Region(cell.begin_shapes_rec(index)).merged()


def boxes(reg):
    return sorted((p.bbox().left, p.bbox().bottom, p.bbox().right, p.bbox().top)
                  for p in reg.each())


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for fab prep")
class ConversionSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        generated = cls.work / "generated"
        generated.mkdir()
        result = run([KLAYOUT, "-b", "-r", str(GENERATOR),
                      "-rd", "output_dir=" + str(generated),
                      "-rd", "pcell_macro=" + str(PCELL_MACRO)])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.design = generated / "nmos_min.gds"
        cls.multifinger = generated / "edge_protected_multifinger.gds"
        cls.fab = cls.work / "nmos_min_fab.gds"
        cls.fab_legacy = cls.work / "nmos_min_legacy_pwell.gds"
        cls.multi_fab = cls.work / "multifinger_fab.gds"
        for design, fab, legacy in ((cls.design, cls.fab, False),
                                    (cls.design, cls.fab_legacy, True),
                                    (cls.multifinger, cls.multi_fab, False)):
            result = fab_prep(design, fab, legacy_pwell=legacy)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        cls.design_layout = load(cls.design)
        cls.fab_layout = load(cls.fab)
        cls.fab_legacy_layout = load(cls.fab_legacy)
        cls.multi_layout = load(cls.multifinger)
        cls.multi_fab_layout = load(cls.multi_fab)

    def test_doping_masks_are_derived_windows_not_drawing_dope(self):
        design_dope = region(self.design_layout, 19)
        fab_dope = region(self.fab_layout, 3)
        # The drawing enclosure is one polygon; the mask is two windows.
        self.assertEqual(len(boxes(design_dope)), 1, boxes(design_dope))
        self.assertEqual(len(boxes(fab_dope)), 2, boxes(fab_dope))
        window = region(self.fab_layout, 4) - fab_dope
        window_box = window.bbox()
        # The fab LVS derives the channel from this window; it equals the
        # drawing TiN gate width at the channel row.
        self.assertAlmostEqual(window_box.width() * DBU, 6.0, places=3)
        strip = pya.Region(pya.Box(um(0), um(9.95), um(52), um(10.05)))
        tin = region(self.design_layout, 5) & strip
        self.assertAlmostEqual(tin.bbox().width() * DBU, 6.0, places=3)

    def test_tin_bias_applies_to_the_whole_connected_polygon(self):
        design_tin = region(self.multi_layout, 5)
        fab_tin = region(self.multi_fab_layout, 5)
        design_box = design_tin.bbox()
        fab_box = fab_tin.bbox()
        # +2 um per side in x, clipped by (DIFF + TiN) extents in y.
        self.assertAlmostEqual((design_box.left - fab_box.left) * DBU, 2.0, places=3)
        self.assertAlmostEqual((fab_box.right - design_box.right) * DBU, 2.0, places=3)
        self.assertEqual(fab_box.top, design_box.top)
        self.assertEqual(fab_box.bottom, design_box.bottom)
        # The off-island jumper bar is not a gate, but it is part of the same
        # connected TiN polygon and is biased as well.
        probe = pya.Region(pya.Box(um(-20), um(-9.5), um(80), um(-8.5)))
        design_row = (design_tin & probe).bbox()
        fab_row = (fab_tin & probe).bbox()
        self.assertAlmostEqual((design_row.left - fab_row.left) * DBU, 2.0, places=3)
        self.assertAlmostEqual((fab_row.right - design_row.right) * DBU, 2.0, places=3)

    def test_nch_mask_defaults_to_the_explicit_drawing_layer(self):
        design_nch = region(self.design_layout, 21)
        default_nch = region(self.fab_layout, 13)
        legacy_nch = region(self.fab_legacy_layout, 13)
        self.assertEqual(boxes(default_nch), boxes(design_nch))
        expected_legacy = (
            region(self.design_layout, 19).interacting(region(self.design_layout, 5))
            .sized(um(2.0)) + design_nch
        ).merged()
        self.assertEqual(boxes(legacy_nch), boxes(expected_legacy))
        # The two behaviours differ by more than an order of magnitude.
        self.assertGreater(legacy_nch.area(), 10 * default_nch.area())


if __name__ == "__main__":
    unittest.main()
