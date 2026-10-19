#!/usr/bin/env python3
"""Foundry-mask migration: the shipped samples and frames are mask-numbered.

The frame libraries and the NAND sample were converted in place, so the flow no
longer converts them: DRC runs `layer_mode=fab` directly on the committed GDS.
The frames keep their cell hierarchy (the LEF/DEF views are emitted per macro);
the sample is a single flat design cell.  A previously converted artifact is
detected by the mask-numbering fingerprint (SOI 4/0, CONTACT 6/0, TEXT 13/1).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
DRC_DECK = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
ONE_METAL = "icps2023_5_1m"
EXPERIMENTAL = "icps2023_5_multimetal_experimental"
EDGE_SCREEN = "TIN.SOIEDGE"
FRAMES = [ROOT / "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds",
          ROOT / "libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds"]
SAMPLE = ROOT / "Samples/Semicon2023/NAND/nand_sample.GDS"
PRIMITIVES = ROOT / "libs.ref/ICPS2023_5_fd_pr/gds/ICPS2023_5_fd_pr.gds"
FRAME_MASK_LAYERS = {(5, 0), (6, 0), (7, 0), (14, 0)}
PRIMITIVE_MASK_LAYERS = {(2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0),
                         (8, 0), (9, 0), (10, 0), (13, 1)}
MASK_MARKERS = {(4, 0), (6, 0), (13, 1)}
DRAWING_ONLY = {(1, 0), (18, 0), (19, 0), (20, 0), (21, 0), (50, 0), (63, 63)}


def environment(profile):
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = profile
    return result


def run(command, profile):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment(profile))


def layers(path):
    layout = pya.Layout()
    layout.read(str(path))
    return ({(info.layer, info.datatype) for info in layout.layer_infos()},
            layout.cells())


def marker_categories(report):
    root = ElementTree.parse(report).getroot()
    items = root.find("items")
    counts = {}
    for item in (items.findall("item") if items is not None else []):
        category = (item.findtext("category", default="") or "").strip().strip("'")
        counts[category] = counts.get(category, 0) + 1
    return counts


def fab_drc(path, profile, report):
    return run([KLAYOUT, "-b", "-r", str(DRC_DECK), "-rd", "input=" + str(path),
                "-rd", "layer_mode=fab", "-rd", "report=" + str(report),
                "-rd", "process_profile=" + profile], profile)


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for DRC")
class FoundryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)

    def test_frame_gds_carry_mask_layers_and_keep_their_cells(self):
        for frame in FRAMES:
            with self.subTest(frame=frame.parent.parent.name):
                used, cells = layers(frame)
                self.assertEqual(used, FRAME_MASK_LAYERS, used)
                self.assertEqual(used & DRAWING_ONLY, set(), used)
                self.assertGreater(cells, 1, "frame hierarchy must be preserved")

    def test_frame_mask_views_are_clean_on_fab_drc(self):
        for frame in FRAMES:
            with self.subTest(frame=frame.parent.parent.name):
                report = self.work / (frame.stem + "_fab.lyrdb")
                result = fab_drc(frame, ONE_METAL, report)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertEqual(marker_categories(report), {})

    def test_sample_is_mask_numbered_and_only_trips_the_edge_screen(self):
        used, _ = layers(SAMPLE)
        self.assertTrue(MASK_MARKERS <= used, used)
        self.assertEqual(used & DRAWING_ONLY, set(), used)
        report = self.work / "nand_sample_fab.lyrdb"
        result = fab_drc(SAMPLE, EXPERIMENTAL, report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        markers = marker_categories(report)
        self.assertTrue(markers, "the legacy sample predates the edge screen")
        unexpected = [category for category in markers
                      if not category.startswith(EDGE_SCREEN)]
        self.assertEqual(unexpected, [], markers)

    def test_primitive_library_is_mask_numbered_and_flat(self):
        used, cells = layers(PRIMITIVES)
        self.assertEqual(used, PRIMITIVE_MASK_LAYERS, used)
        self.assertEqual(used & DRAWING_ONLY, set(), used)
        layout = pya.Layout()
        layout.read(str(PRIMITIVES))
        self.assertEqual(cells, 10)
        self.assertEqual(
            sorted(cell.name for cell in layout.top_cells()),
            sorted(["DCNT2", "PAD100", "PAD150", "PAD60", "Via", "dcont",
                    "nsubcont", "pcont", "pcont_min", "psubcont"]))

    def test_primitive_cells_keep_the_documented_marker_profile(self):
        layout = pya.Layout()
        layout.read(str(PRIMITIVES))
        for cell in sorted(layout.top_cells(), key=lambda item: item.name):
            with self.subTest(cell=cell.name):
                report = self.work / (cell.name + "_fab.lyrdb")
                result = run([KLAYOUT, "-b", "-r", str(DRC_DECK),
                              "-rd", "input=" + str(PRIMITIVES),
                              "-rd", "top=" + cell.name,
                              "-rd", "layer_mode=fab",
                              "-rd", "report=" + str(report),
                              "-rd", "process_profile=" + EXPERIMENTAL],
                             EXPERIMENTAL)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                markers = marker_categories(report)
                if cell.name in ("PAD60", "PAD100", "PAD150"):
                    self.assertEqual(len(markers), 1, markers)
                    category = next(iter(markers))
                    self.assertTrue(category.startswith("VIA1.G"), markers)
                    self.assertEqual(markers[category], 1, markers)
                else:
                    self.assertEqual(markers, {}, markers)


if __name__ == "__main__":
    unittest.main()
