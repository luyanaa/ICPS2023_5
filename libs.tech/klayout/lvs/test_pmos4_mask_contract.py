#!/usr/bin/env python3
"""PMOS4 manufacturable-geometry contract (PMOS4_EXPERIMENTAL).

The reference is drawn directly as fabrication masks (no drawing->fab
conversion): SOI, P_DIFF, N_DIFF, TiN, CNT, ML1, TEXT.  It establishes that a
valid physical topology exists whose body path is identifiable on the final
mask:

  C1  the N- neck leaves the channel at the island edge, OUTSIDE the TiN gate;
  C2  the P_DIFF mask stops at the channel, so the neck corridor stays undoped
      (the remaining corridor width is measured on the final masks);
  C3  the main channel is TiN over the dopant gap and the protrusion is not
      under the gate, so a normal rectangular L/W extraction is admissible;
  C5  the N+ dot has a legal CNT and ML1 terminal.

Two failure layouts are frozen as permanent negative regressions:

  F1  P_DIFF intrudes into the neck -> the body path is cut;
  F2  the neck passes under the gate -> the protrusion corrupts the rectangular
      gate geometry (the documented L_E = L(1 + L_V/W) mechanism), so a normal
      rectangular PMOS4 extraction must be refused, not silently corrected.

The current deck refuses every PMOS tie (extraction is experimental), so these
tests pin the refusal now and the required outcomes for the future extractor.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
ONE_METAL = "icps2023_5_1m"
DBU = 0.001

ISLAND = (0.0, 0.0, 48.0, 40.0)
NECK = (21.0, 40.0, 27.0, 60.0)
TIE = (17.5, 53.5, 30.5, 66.5)
CHANNEL = (21.0, 0.0, 27.0, 40.0)
P_DIFF = [(0.0, 0.0, 21.0, 40.0), (27.0, 0.0, 48.0, 40.0)]
N_DIFF = (18.0, 57.0, 30.0, 66.0)
TIN_REFERENCE = [(21.0, 0.0, 27.0, 40.0), (21.0, -6.0, 27.0, 0.0),
                 (17.5, -18.0, 30.5, -5.0)]
TIN_NECK_UNDER_GATE = [(21.0, -6.0, 27.0, 60.0), (17.5, -18.0, 30.5, -5.0)]
P_DIFF_INTRUSION = (21.0, 40.0, 27.0, 50.0)
CNT = {"S": (4.0, 17.5, 9.0, 22.5), "D": (39.0, 17.5, 44.0, 22.5),
       "G": (21.5, -14.0, 26.5, -9.0), "B": (21.5, 59.0, 26.5, 64.0)}
ML1 = {"S": (0.0, 13.5, 13.0, 26.5), "D": (35.0, 13.5, 48.0, 26.5),
       "G": (17.0, -22.0, 31.0, -5.0), "B": (17.5, 55.0, 30.5, 68.0)}
LABELS = {"S": (6.5, 20.0), "D": (41.5, 20.0), "G": (24.0, -11.5),
          "B": (24.0, 61.5)}
REFERENCE = """.GLOBAL SUB
.SUBCKT pch_body_tie D G S B
M1 D G S SUB B PCH L=6U W=40U
.ENDS
XTOP D G S B pch_body_tie
.END
"""


def um(value):
    return int(round(value / DBU))


def build(path, tin, extra_p_diff=()):
    layout = pya.Layout()
    layout.dbu = DBU
    cell = layout.create_cell("pmos4_reference")

    def rect(layer, coords, datatype=0):
        cell.shapes(layout.layer(pya.LayerInfo(layer, datatype))).insert(
            pya.Box(*(um(v) for v in coords)))

    rect(4, ISLAND)
    rect(4, NECK)
    rect(4, TIE)
    for window in P_DIFF + list(extra_p_diff):
        rect(2, window)
    rect(3, N_DIFF)
    for shape in tin:
        rect(5, shape)
    for cut in CNT.values():
        rect(6, cut)
    for pad in ML1.values():
        rect(7, pad)
    for name, (x, y) in LABELS.items():
        cell.shapes(layout.layer(pya.LayerInfo(13, 1))).insert(
            pya.Text(name, pya.Trans(pya.Point(um(x), um(y)))))
    layout.write(str(path))
    return path


def load(path):
    layout = pya.Layout()
    layout.read(str(path))
    layout.top_cell().flatten(True)
    return layout


def region(layout, number, datatype=0):
    index = layout.find_layer(pya.LayerInfo(number, datatype))
    if index is None or index < 0:
        return pya.Region()
    return pya.Region(layout.top_cell().begin_shapes_rec(index)).merged()


def width_at(reg, y):
    strip = pya.Region(pya.Box(um(-10), um(y - 0.05), um(80), um(y + 0.05)))
    clipped = reg & strip
    if clipped.is_empty():
        return 0.0
    box = clipped.bbox()
    return round(box.width() * DBU, 3)


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for LVS")
def build_drawing(path):
    """Drawing candidate whose fab-prep output targets the reference masks.

    DIFF = the reference SOI (mapped 1:1).  PAREA/NAREA are the reference
    windows inset by 2 um, so the fab-prep 2 um dilation reproduces them
    exactly.  TIN is the 6 um channel gate; the current isotropic gate bias
    then grows it 2 um in every direction, including into the neck.
    """
    layout = pya.Layout()
    layout.dbu = DBU
    cell = layout.create_cell("pmos4_drawing_candidate")

    def rect(layer, coords, datatype=0):
        cell.shapes(layout.layer(pya.LayerInfo(layer, datatype))).insert(
            pya.Box(*(um(v) for v in coords)))

    rect(3, ISLAND)
    rect(3, NECK)
    rect(3, TIE)
    rect(18, (2.0, 2.0, 19.0, 38.0))
    rect(18, (29.0, 2.0, 46.0, 38.0))
    rect(19, (20.0, 59.0, 28.0, 64.0))
    for shape in TIN_REFERENCE:
        rect(5, shape)
    for cut in CNT.values():
        rect(7, cut)
    for pad in ML1.values():
        rect(8, pad)
    for name, (x, y) in LABELS.items():
        cell.shapes(layout.layer(pya.LayerInfo(13, 0))).insert(
            pya.Text(name, pya.Trans(pya.Point(um(x), um(y)))))
    layout.write(str(path))
    return path


def fab_prep(design, fab):
    environment = os.environ.copy()
    environment["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    result = subprocess.run(
        [KLAYOUT, "-b", "-r",
         str(ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"),
         "-rd", "input=" + str(design), "-rd", "output=" + str(fab),
         "-rd", "process_profile=" + ONE_METAL],
        text=True, capture_output=True, env=environment)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return fab


class Pmos4GeometryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        cls.reference = build(cls.work / "pmos4_reference.gds",
                              TIN_REFERENCE)
        cls.p_intrusion = build(cls.work / "pmos4_p_intrusion.gds",
                                TIN_REFERENCE, [P_DIFF_INTRUSION])
        cls.neck_under_gate = build(cls.work / "pmos4_neck_under_gate.gds",
                                    TIN_NECK_UNDER_GATE)
        cls.layouts = {name: load(path) for name, path in (
            ("reference", cls.reference), ("p_intrusion", cls.p_intrusion),
            ("neck_under_gate", cls.neck_under_gate))}

    def test_c1_body_path_is_continuous_outside_p_diff(self):
        layout = self.layouts["reference"]
        body = region(layout, 4) - region(layout, 2)
        channel = pya.Region(pya.Box(*(um(v) for v in CHANNEL)))
        dot = pya.Region(pya.Box(*(um(v) for v in N_DIFF)))
        self.assertEqual(len(list(body.each())), 1, list(body.each()))
        self.assertFalse(body.interacting(channel).is_empty())
        self.assertFalse(body.interacting(dot).is_empty())

    def test_c2_body_corridor_keeps_width_outside_p_diff(self):
        layout = self.layouts["reference"]
        body = region(layout, 4) - region(layout, 2)
        # The corridor between the channel edge and the N+ dot keeps its drawn
        # 6 um width; the P+ mask does not reach into it.
        self.assertAlmostEqual(width_at(body, 50.0), 6.0, places=3)
        broken = self.layouts["p_intrusion"]
        broken_body = region(broken, 4) - region(broken, 2)
        self.assertAlmostEqual(width_at(broken_body, 45.0), 0.0, places=3)
        self.assertEqual(len(list(broken_body.each())), 2, list(broken_body.each()))

    def test_c3_main_channel_is_not_under_the_neck(self):
        layout = self.layouts["reference"]
        tin = region(layout, 5)
        body = region(layout, 4) - region(layout, 2)
        channel = pya.Region(pya.Box(*(um(v) for v in CHANNEL)))
        gate = tin & channel
        self.assertAlmostEqual(gate.bbox().width() * DBU, 6.0, places=3)
        self.assertAlmostEqual(gate.bbox().height() * DBU, 40.0, places=3)
        neck = pya.Region(pya.Box(*(um(v) for v in NECK)))
        self.assertTrue((tin & neck).is_empty(), "reference neck must be outside TiN")
        under = self.layouts["neck_under_gate"]
        self.assertFalse((region(under, 5) & neck).is_empty(),
                         "the failure fixture must have TiN over the neck")

    def test_c5_body_contact_has_cnt_and_ml1(self):
        layout = self.layouts["reference"]
        cnt = region(layout, 6)
        n_diff = region(layout, 3)
        ml1 = region(layout, 7)
        body_cut = pya.Region(pya.Box(*(um(v) for v in CNT["B"])))
        self.assertFalse(n_diff.interacting(body_cut).is_empty())
        self.assertFalse(ml1.interacting(body_cut).is_empty())
        # Enclosure: N_DIFF reaches at least 2 um beyond the cut on every side.
        n_box = n_diff.bbox()
        cut_box = body_cut.bbox()
        self.assertGreaterEqual((cut_box.left - n_box.left) * DBU, 2.0)
        self.assertGreaterEqual((n_box.right - cut_box.right) * DBU, 2.0)
        self.assertGreaterEqual((cut_box.bottom - n_box.bottom) * DBU, 2.0)
        self.assertGreaterEqual((n_box.top - cut_box.top) * DBU, 2.0)

    def test_current_deck_refuses_every_pmos_tie(self):
        """Transition gate: the experimental extractor is not implemented, so
        the reference and both frozen failure layouts must be refused."""
        reference = self.work / "reference.cir"
        reference.write_text(REFERENCE, encoding="utf-8")
        for name, gds in (("reference", self.reference),
                          ("p_intrusion", self.p_intrusion),
                          ("neck_under_gate", self.neck_under_gate)):
            with self.subTest(fixture=name):
                report = self.work / (name + ".lyrdb")
                output = self.work / (name + "_extracted.cir")
                environment = os.environ.copy()
                environment["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
                result = subprocess.run(
                    [KLAYOUT, "-b", "-r", str(LVS_DECK),
                     "-rd", "input=" + str(gds),
                     "-rd", "schematic=" + str(reference),
                     "-rd", "layer_mode=fab",
                     "-rd", "report=" + str(report),
                     "-rd", "output=" + str(output),
                     "-rd", "process_profile=" + ONE_METAL],
                    text=True, capture_output=True, env=environment)
                details = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, details)
                self.assertIn("PMOS body-tie extraction is not supported yet",
                              details)


    def test_drawing_prep_realizes_every_mask_except_the_gate_bias(self):
        """Is there a legal drawing input whose fab-prep output equals the
        hand-built reference?  SOI, P_DIFF, N_DIFF, CNT and ML1 are exactly
        reproducible; the isotropic 2 um gate bias is not: it grows the final
        TiN into the neck."""
        drawing = build_drawing(self.work / "pmos4_drawing.gds")
        converted = self.work / "pmos4_drawing_fab.gds"
        fab_prep(drawing, converted)
        produced = load(converted)
        reference = self.layouts["reference"]
        for name, number in (("SOI", 4), ("P_DIFF", 2), ("N_DIFF", 3),
                             ("CNT", 6), ("ML1", 7)):
            with self.subTest(mask=name):
                expected = region(reference, number)
                actual = region(produced, number)
                self.assertTrue((actual - expected).is_empty(), name)
                self.assertTrue((expected - actual).is_empty(), name)
        expected_tin = region(reference, 5)
        actual_tin = region(produced, 5)
        self.assertTrue((expected_tin - actual_tin).is_empty(),
                        "hand reference TiN must be a subset of the mask")
        extra = (actual_tin - expected_tin)
        self.assertFalse(extra.is_empty(),
                         "the current isotropic gate bias must add area")
        neck = pya.Region(pya.Box(*(um(v) for v in NECK)))
        gated_neck = actual_tin & neck
        self.assertAlmostEqual(gated_neck.area() * DBU * DBU, 12.0, places=3)

    def test_fab_realizability_gap_is_the_isotropic_gate_bias(self):
        """C3 (neck outside TiN) is NOT realizable under the current rules:
        the 2 um isotropic bias covers the first 2 um of the neck.  Keeping the
        neck un-gated requires an anisotropic/axis-aware bias or a PCell
        topology where the neck leaves where the gate does not cross."""
        drawing = build_drawing(self.work / "pmos4_drawing2.gds")
        converted = self.work / "pmos4_drawing2_fab.gds"
        fab_prep(drawing, converted)
        produced = load(converted)
        body = region(produced, 4) - region(produced, 2)
        neck = pya.Region(pya.Box(*(um(v) for v in NECK)))
        self.assertAlmostEqual(width_at(body, 50.0), 6.0, places=3)
        gated_neck = region(produced, 5) & neck
        self.assertAlmostEqual(gated_neck.area() * DBU * DBU, 12.0, places=3)
        self.assertEqual((region(self.layouts["reference"], 5) & neck).area(), 0,
                         "the hand reference keeps the neck outside TiN")


if __name__ == "__main__":
    unittest.main()
