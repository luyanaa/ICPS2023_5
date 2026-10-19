#!/usr/bin/env python3
"""Geometry and qualification-boundary tests for the independent IO DFM gate."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - supplied by the pinned KLayout toolchain
    from klayout import db

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import io_dfm


FRAME_GDS = ROOT / "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"


def write_layout(path: Path, *, pad_boxes: list[tuple[int, int, int, int]],
                 openings: list[tuple[int, int, int, int]] | None = None,
                 upper_layer: tuple[int, int] | None = None,
                 layer_mode: str = "design") -> None:
    """Write real GDS geometry using integer DBU ticks (0.001 um per tick)."""
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    frame_index = layout.layer(14, 0)
    ml1_index = layout.layer(8, 0) if layer_mode == "design" else layout.layer(7, 0)
    pad_index = layout.layer(50, 0) if layer_mode == "design" else layout.layer(10, 0)
    top.shapes(frame_index).insert(db.Box(-900_000, -900_000, 900_000, 900_000))
    top.shapes(frame_index).insert(db.Box(-800_000, -800_000, 800_000, 800_000))
    for box in pad_boxes:
        top.shapes(ml1_index).insert(db.Box(*box))
    for box in openings or []:
        top.shapes(pad_index).insert(db.Box(*box))
    if upper_layer is not None:
        top.shapes(layout.layer(*upper_layer)).insert(
            db.Box(-1_000, -1_000, 1_000, 1_000))
    layout.write(str(path))


class IoDfmGeometryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def single_pad(self, *, openings=None, upper_layer=None, path_name="design.gds") -> Path:
        path = self.directory / path_name
        pad = (-130_000, 100_000, -70_000, 160_000)
        write_layout(path, pad_boxes=[pad], openings=openings, upper_layer=upper_layer)
        return path

    def test_existing_bare_frame_reports_missing_openings_and_blocked_acceptance(self):
        # The shipped 1x1 frame GDS is foundry numbered (ML1 7/0, PAD 10/0).
        report = io_dfm.check_io_dfm(FRAME_GDS, layer_mode="fab",
                                     expected_pad_count=28)
        self.assertEqual(report["checks"]["engineering_frame"]["status"], "MEASURED")
        self.assertEqual(report["checks"]["engineering_frame"]["pad_count"], 28)
        self.assertEqual(report["checks"]["pad_openings"]["status"], "MISSING")
        self.assertEqual(
            report["checks"]["pad_openings"]["finding"]["code"],
            "missing_pad_opening_geometry",
        )
        self.assertEqual(report["structural_status"], "INCOMPLETE")
        self.assertEqual(report["checks"]["explicit_pad_mapping"]["status"], "UNRESOLVED")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")
        self.assertFalse(report["manufacturing_acceptance"]["accepted"])

    def test_supported_opening_needs_no_invented_enclosure_margin(self):
        path = self.single_pad(openings=[(-129_000, 101_000, -71_000, 159_000)])
        report = io_dfm.check_io_dfm(path, expected_pad_count=1)
        self.assertEqual(report["checks"]["pad_openings"]["status"], "PASS")
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")

    def test_one_mismatched_opening_shape_fails_exact_containment(self):
        path = self.single_pad(openings=[
            (-120_000, 110_000, -80_000, 150_000),
            (-131_000, 110_000, -130_000, 120_000),
        ])
        report = io_dfm.check_io_dfm(path, expected_pad_count=1)
        self.assertEqual(report["checks"]["pad_openings"]["status"], "FAIL")
        self.assertFalse(report["checks"]["pad_openings"]["all_openings_contained_by_ml1"])
        self.assertEqual(
            report["checks"]["pad_openings"]["violations"][0]["code"],
            "pad_opening_not_exactly_contained_by_ml1",
        )
        self.assertEqual(report["structural_status"], "FAIL")

    def test_1m_profile_rejects_actual_ml2_layer_geometry(self):
        path = self.single_pad(
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            upper_layer=(10, 0),
        )
        report = io_dfm.check_io_dfm(path, expected_pad_count=1,
                                     process_profile="icps2023_5_1m")
        violations = report["checks"]["upper_layer_exclusion"]["violations"]
        self.assertTrue(any(item["code"] == "unpermitted_upper_layer_geometry"
                            for item in violations))
        self.assertEqual(report["structural_status"], "FAIL")

    def test_experimental_profile_allows_its_declared_upper_layers(self):
        path = self.single_pad(
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            upper_layer=(10, 0),
        )
        report = io_dfm.check_io_dfm(
            path, expected_pad_count=1,
            process_profile="icps2023_5_multimetal_experimental",
        )
        self.assertEqual(report["checks"]["upper_layer_exclusion"]["status"], "PASS")
        self.assertEqual(
            [item["layer_name"] for item in
             report["checks"]["upper_layer_exclusion"]["observed_geometry"]],
            ["ML2"],
        )
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")

    def test_fab_pad_layer10_is_not_mistaken_for_ml2(self):
        path = self.directory / "fab_pad10.gds"
        write_layout(
            path,
            pad_boxes=[(-130_000, 100_000, -70_000, 160_000)],
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            layer_mode="fab",
        )
        report = io_dfm.check_io_dfm(path, layer_mode="fab", expected_pad_count=1)
        self.assertEqual(report["checks"]["pad_openings"]["status"], "PASS")
        self.assertEqual(report["checks"]["upper_layer_exclusion"]["observed_geometry"], [])
        self.assertEqual(report["checks"]["upper_layer_exclusion"]["violations"], [])

    def test_explicitly_invalid_mapping_fails_structural_report(self):
        path = self.single_pad(openings=[(-120_000, 110_000, -80_000, 150_000)])
        report = io_dfm.check_io_dfm(path, expected_pad_count=1,
                                     pad_mapping={"PAD_00": " "})
        self.assertEqual(report["checks"]["explicit_pad_mapping"]["status"], "INVALID")
        self.assertEqual(report["structural_status"], "FAIL")


    def test_explicit_mapping_is_honored_and_other_functions_stay_unresolved(self):
        path = self.directory / "two_pads.gds"
        pads = [(-130_000, 100_000, -70_000, 160_000),
                (70_000, -160_000, 130_000, -100_000)]
        openings = [(-120_000, 110_000, -80_000, 150_000)]
        write_layout(path, pad_boxes=pads, openings=openings)
        report = io_dfm.check_io_dfm(path, expected_pad_count=2,
                                     pad_mapping={"PAD_00": "SENSOR_OUT"})
        mapping = report["checks"]["explicit_pad_mapping"]
        self.assertEqual(mapping["unassigned_pad_ids"], ["PAD_01"])
        self.assertEqual(mapping["unmapped_function_resolution"], "UNRESOLVED")
        self.assertFalse(mapping["function_inferred_from_labels"])

    def test_absent_mapping_does_not_block_geometry_only_structural_pass(self):
        path = self.single_pad(openings=[(-120_000, 110_000, -80_000, 150_000)])
        report = io_dfm.check_io_dfm(path, expected_pad_count=1)
        self.assertEqual(report["checks"]["explicit_pad_mapping"]["status"], "UNRESOLVED")
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")

    def test_actual_design_to_fab_geometry_maps_pad10_separately_from_ml2(self):
        design_path = self.single_pad(
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            path_name="design_for_fabprep.gds",
        )
        fab_path = self.directory / "fab.gds"
        write_layout(
            fab_path,
            pad_boxes=[(-130_000, 100_000, -70_000, 160_000)],
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            layer_mode="fab",
        )
        report = io_dfm.check_io_dfm(design_path, expected_pad_count=1,
                                     fab_gds_path=fab_path)
        comparison = report["checks"]["design_to_fabprep"]
        self.assertEqual(comparison["status"], "PASS")
        self.assertTrue(all(item["matches_actual_fab_gds"]
                            for item in comparison["comparisons"]))
        self.assertEqual(report["checks"]["upper_layer_exclusion"]["observed_geometry"], [])

    def test_changed_fab_metal_is_not_blessed_by_matching_opening_bounds(self):
        design = self.single_pad(
            openings=[(-120_000, 110_000, -80_000, 150_000)])
        fab = self.directory / "changed_fab.gds"
        write_layout(
            fab,
            pad_boxes=[(-130_000, 100_000, -100_000, 160_000)],
            openings=[(-120_000, 110_000, -80_000, 150_000)],
            layer_mode="fab",
        )
        report = io_dfm.check_io_dfm(
            design, expected_pad_count=1, fab_gds_path=fab)
        self.assertEqual(report["checks"]["design_to_fabprep"]["status"], "FAIL")
        self.assertFalse(report["structural_pass"])

    def test_esd_declarations_never_assert_electrical_qualification(self):
        path = self.single_pad(openings=[(-120_000, 110_000, -80_000, 150_000)])
        report = io_dfm.check_io_dfm(path, expected_pad_count=1,
                                     esd_devices=[{"name": "clamp"}])
        esd = report["checks"]["explicit_esd_devices"]
        self.assertEqual(esd["status"], "DECLARED_UNQUALIFIED")
        self.assertFalse(esd["electrically_qualified"])

    def test_cli_structural_and_manufacturing_exit_modes_are_distinct(self):
        path = self.single_pad(openings=[(-120_000, 110_000, -80_000, 150_000)])
        structural_json = self.directory / "structural.json"
        manufacturing_json = self.directory / "manufacturing.json"
        structural_exit = io_dfm.main([
            "--gds", str(path), "--layer-mode", "design",
            "--expected-pad-count", "1",
            "--mode", "structural", "--out", str(structural_json),
        ])
        manufacturing_exit = io_dfm.main([
            "--gds", str(path), "--layer-mode", "design",
            "--expected-pad-count", "1",
            "--mode", "manufacturing", "--out", str(manufacturing_json),
        ])
        self.assertEqual(structural_exit, 0)
        self.assertEqual(manufacturing_exit, 3)


if __name__ == "__main__":
    unittest.main()
