#!/usr/bin/env python3
"""Deterministic geometry tests for frame.measure_frame."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import klayout.db as db

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frame


SOURCE_GDS = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_fd_base" / "gds" /
              "ICPS2023_5_fd_base.gds")
FRAME_2X2_GDS = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_fd_base_2x2" / "gds" /
                 "ICPS2023_5_fd_base_2x2.gds")
CONFIG_1X1 = HERE / "config.json"
CONFIG_2X2 = HERE / "config_2x2.json"


def _write_gds(path: Path, *, outer_um: float, inner_um: float,
               pad_centers_um: list[tuple[float, float]]) -> None:
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("FRAME_TEST")
    frame_layer = layout.layer(14, 0)
    ml1_layer = layout.layer(8, 0)
    scale = 1000
    top.shapes(frame_layer).insert(db.Box(-round(outer_um * scale), -round(outer_um * scale),
                                         round(outer_um * scale), round(outer_um * scale)))
    top.shapes(frame_layer).insert(db.Box(-round(inner_um * scale), -round(inner_um * scale),
                                         round(inner_um * scale), round(inner_um * scale)))
    half_pad = 30 * scale
    for center_x_um, center_y_um in pad_centers_um:
        center_x = round(center_x_um * scale)
        center_y = round(center_y_um * scale)
        top.shapes(ml1_layer).insert(db.Box(center_x - half_pad, center_y - half_pad,
                                           center_x + half_pad, center_y + half_pad))
    layout.write(str(path))


def _overlap_test_centers() -> list[tuple[float, float]]:
    centers = [(-40.0, 0.0), (40.0, 0.0)]
    centers.extend((x, y) for y in (-300.0, -180.0, 180.0, 300.0)
                   for x in (-360.0, -240.0, -120.0, 120.0, 240.0, 360.0))
    centers.extend((x, 0.0) for x in (-360.0, 360.0))
    return centers


class FrameMeasurementTests(unittest.TestCase):
    def test_canonical_frame_report_preserves_contours_and_all_pads(self):
        report = frame.measure_frame(SOURCE_GDS, layer_mode="fab")
        self.assertEqual(report["frame"]["outer_bbox_um"], [-800.0, -800.0, 800.0, 800.0])
        self.assertEqual(report["frame"]["inner_bbox_um"], [-750.0, -750.0, 750.0, 750.0])
        self.assertEqual(report["pads"]["count"], 28)
        self.assertEqual(report["pads"]["merged_rectangle_count"], 28)
        self.assertEqual([pad["pad_id"] for pad in report["pads"]["rectangles"]],
                         [f"PAD_{index:02d}" for index in range(28)])
        for pad in report["pads"]["rectangles"]:
            left, bottom, right, top = pad["bbox_um"]
            self.assertEqual((right - left, top - bottom), (60.0, 60.0))
        self.assertEqual(report["power_interface"]["assignments_by_pad"], {})
        self.assertEqual(len(report["power_interface"]["unassigned_pads"]), 28)
        self.assertFalse(report["power_interface"]["signal_or_power_inferred_from_gds_labels"])
        self.assertGreater(report["core_available_area_um2"], 0)
        self.assertEqual(report["fixed_overhead_area_um2"],
                         report["die_area_um2"] - report["core_available_area_um2"])
        self.assertFalse(report["manufacturing_limits"]["qualified_manufacturing_core"])
        self.assertEqual(report["pro_capacity_scenarios"]["area_multiplier_4"]["nominal_square_side_um"],
                         3200.0)
        self.assertEqual(report["pro_capacity_scenarios"]["side_multiplier_4"]["nominal_square_side_um"],
                         6400.0)
        self.assertIsNone(report["pro_capacity_scenarios"]["area_multiplier_4"]["core_available_area_um2"])
        self.assertIsNone(report["pro_capacity_scenarios"]["side_multiplier_4"]["core_available_area_um2"])

    def test_boolean_geometry_unions_overlaps_and_keeps_polygon_holes(self):
        with tempfile.TemporaryDirectory() as temporary:
            gds_path = Path(temporary) / "overlap.gds"
            _write_gds(gds_path, outer_um=700, inner_um=650,
                       pad_centers_um=_overlap_test_centers())
            report = frame.measure_frame(gds_path, layer_mode="design",
                                         die_bbox=[-650, -650, 650, 650],
                                         pad_keepout_um=20, frame_keepout_um=10,
                                         edge_keepout_um=10)

        pad_keepout = report["keepout_regions"]["pad"]["region"]
        frame_keepout = report["keepout_regions"]["frame"]["region"]
        edge_keepout = report["keepout_regions"]["edge"]["region"]
        self.assertEqual(report["pads"]["count"], 28)
        self.assertEqual(pad_keepout["area_um2"], 278000.0)
        self.assertEqual(frame_keepout["area_um2"], 51600.0)
        self.assertEqual(edge_keepout["area_um2"], 51600.0)
        available = report["available_region"]
        self.assertEqual(available["bbox_um"], [-640.0, -640.0, 640.0, 640.0])
        self.assertEqual(available["polygon_count"], 1)
        self.assertEqual(len(available["polygons"][0]["holes_um"]), 27)
        self.assertEqual(available["area_um2"], report["core_available_area_um2"])
        self.assertEqual(report["core_available_area_um2"], 1360400.0)
        self.assertEqual(report["fixed_overhead_area_um2"], 329600.0)
        self.assertLess(report["fixed_overhead_area_um2"],
                        pad_keepout["area_um2"] + frame_keepout["area_um2"] + edge_keepout["area_um2"])

    def test_exact_rectangular_fit_uses_available_geometry_and_holes(self):
        with tempfile.TemporaryDirectory() as temporary:
            gds_path = Path(temporary) / "fit.gds"
            _write_gds(gds_path, outer_um=700, inner_um=650,
                       pad_centers_um=_overlap_test_centers())
            report = frame.measure_frame(
                gds_path, layer_mode="design", die_bbox=[-650, -650, 650, 650],
                pad_keepout_um=20, frame_keepout_um=10, edge_keepout_um=10)

        fit = frame.find_rectangular_core_fit(report, 100, 100)
        self.assertTrue(fit["fits"], fit)
        left, bottom, right, top = fit["placement_bbox_um"]
        self.assertEqual((right - left, top - bottom), (100.0, 100.0))
        self.assertEqual(fit["dbu_um"], report["dbu_um"])
        self.assertEqual(fit["available_area_um2"], report["available_region"]["area_um2"])
        self.assertEqual(fit["source_sha256"], report["source_sha256"])
        self.assertFalse(fit["qualified_site"])

        # Available scalar area exceeds 1,000,000 um2, but every 1000 x 1000
        # rectangle intersects a pad reservation or its boundary.
        no_fit = frame.find_rectangular_core_fit(report, 1000, 1000)
        self.assertGreater(report["available_region"]["area_um2"], 1000 * 1000)
        self.assertFalse(no_fit["fits"], no_fit)
        self.assertIsNone(no_fit["placement_bbox_um"])
        self.assertGreater(no_fit["candidate_count"], 0)

    def test_explicit_power_mapping_is_validated_against_canonical_pad_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            gds_path = Path(temporary) / "power.gds"
            _write_gds(gds_path, outer_um=700, inner_um=650,
                       pad_centers_um=_overlap_test_centers())
            report = frame.measure_frame(gds_path, layer_mode="design", power_interface={"PAD_00": "VDD"})
            self.assertEqual(report["power_interface"]["assignments_by_pad"], {"PAD_00": "VDD"})
            self.assertEqual(len(report["power_interface"]["unassigned_pads"]), 27)
            with self.assertRaisesRegex(frame.FrameError, "unknown pad"):
                frame.measure_frame(gds_path, layer_mode="design", power_interface={"PAD_28": "VSS"})
            with self.assertRaisesRegex(frame.FrameError, "net name"):
                frame.measure_frame(gds_path, layer_mode="design", power_interface={"PAD_00": " "})

    def test_invalid_keepout_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            gds_path = Path(temporary) / "valid.gds"
            _write_gds(gds_path, outer_um=700, inner_um=650,
                       pad_centers_um=_overlap_test_centers())
            for kwargs in ({"pad_keepout_um": -1}, {"frame_keepout_um": float("nan")},
                           {"edge_keepout_um": True}, {"pad_keepout_um": 0.0004}):
                with self.subTest(kwargs=kwargs), self.assertRaises(frame.FrameError):
                    frame.measure_frame(gds_path, layer_mode="design", **kwargs)

    def test_frame_layer_mode_is_explicit_and_fails_closed(self):
        for value in (None, "FAB", "native"):
            with self.subTest(layer_mode=value), self.assertRaisesRegex(
                    frame.FrameError, "layer_mode"):
                frame.measure_frame(SOURCE_GDS, layer_mode=value)
        # The migrated frames are foundry numbered: the drawing view must not
        # silently find or fabricate pads from other 8/0 geometry.
        with self.assertRaisesRegex(frame.FrameError, "ML1 8/0"):
            frame.measure_frame(SOURCE_GDS, layer_mode="design")
        report = frame.measure_frame(SOURCE_GDS, layer_mode="fab")
        self.assertEqual(report["layer_mode"], "fab")
        self.assertEqual(report["pads"]["layer"], [7, 0])


class SecondDieOptionTests(unittest.TestCase):
    """The derived 2x2 frame library measures through the same contract."""

    def test_2x2_frame_measures_56_pads_and_reserved_capacity(self):
        report = frame.measure_frame(FRAME_2X2_GDS, layer_mode="fab", expected_pad_count=56,
                                     pro_capacity_multiplier=None)
        self.assertEqual(report["die_outer_bbox_um"], [-1600.0, -1600.0, 1600.0, 1600.0])
        self.assertEqual(report["frame"]["inner_bbox_um"], [-1550.0, -1550.0, 1550.0, 1550.0])
        self.assertEqual(report["pads"]["count"], 56)
        self.assertEqual(report["pads"]["merged_rectangle_count"], 56)
        self.assertEqual([pad["pad_id"] for pad in report["pads"]["rectangles"]],
                         [f"PAD_{index:02d}" for index in range(56)])
        for pad in report["pads"]["rectangles"]:
            left, bottom, right, top = pad["bbox_um"]
            self.assertEqual((right - left, top - bottom), (60.0, 60.0))
        self.assertEqual(report["die_area_um2"], 10240000.0)
        self.assertEqual(report["core_available_area_um2"], 9097200.0)
        self.assertEqual(report["fixed_overhead_area_um2"],
                         report["die_area_um2"] - report["core_available_area_um2"])
        self.assertEqual(report["pro_capacity_scenarios"], {})
        self.assertEqual(report["provenance"]["frame_readme"],
                         "libs.ref/ICPS2023_5_fd_base_2x2/doc/README.md")
        self.assertEqual(report["provenance"]["pad_source"],
                         "libs.ref/ICPS2023_5_fd_base_2x2/doc/pad_map.csv and recursive ML1 geometry")
        self.assertEqual(len(report["power_interface"]["unassigned_pads"]), 56)

    def test_2x2_square_routing_envelope_uses_the_whole_inner_region(self):
        report = frame.measure_frame(FRAME_2X2_GDS, layer_mode="fab", expected_pad_count=56,
                                     pro_capacity_multiplier=None)
        fit = frame.find_rectangular_core_fit(report, 2940, 2940)
        self.assertTrue(fit["fits"], fit)
        self.assertEqual(fit["placement_bbox_um"], [-1470.0, -1470.0, 1470.0, 1470.0])
        self.assertFalse(frame.find_rectangular_core_fit(report, 2980, 2980)["fits"])

    def test_pad_count_and_capacity_factor_validation(self):
        with self.assertRaisesRegex(frame.FrameError, "exactly 56 pads"):
            frame.measure_frame(SOURCE_GDS, layer_mode="fab", expected_pad_count=56)
        for value in (0, -1, True, "4"):
            with self.subTest(expected_pad_count=value), self.assertRaises(frame.FrameError):
                frame.measure_frame(SOURCE_GDS, layer_mode="fab", expected_pad_count=value)
        for value in (0, -4, float("nan"), True):
            with self.subTest(pro_capacity_multiplier=value), self.assertRaises(frame.FrameError):
                frame.measure_frame(SOURCE_GDS, layer_mode="fab", pro_capacity_multiplier=value)
        self.assertEqual(
            frame.measure_frame(SOURCE_GDS, layer_mode="fab", pro_capacity_multiplier=None)["pro_capacity_scenarios"],
            {})

    def test_config_frame_arguments_drive_the_measurement(self):
        gds_path, arguments = frame._config_frame_arguments(CONFIG_2X2)
        self.assertTrue(str(gds_path).endswith("ICPS2023_5_fd_base_2x2.gds"))
        self.assertEqual(arguments["layer_mode"], "fab")
        self.assertEqual(arguments["expected_pad_count"], 56)
        self.assertIsNone(arguments["pro_capacity_multiplier"])
        report = frame.measure_frame(gds_path, **arguments)
        self.assertEqual(report["pads"]["count"], 56)

        canonical_path, canonical_args = frame._config_frame_arguments(CONFIG_1X1)
        self.assertEqual(canonical_args["expected_pad_count"], 28)
        self.assertEqual(canonical_args["pro_capacity_multiplier"], 4)
        canonical = frame.measure_frame(canonical_path, **canonical_args)
        self.assertEqual(canonical["pads"]["count"], 28)
        self.assertEqual(canonical["core_available_area_um2"], 1997200.0)
        self.assertEqual(sorted(canonical["pro_capacity_scenarios"]),
                         ["area_multiplier_4", "side_multiplier_4"])


if __name__ == "__main__":
    unittest.main()
