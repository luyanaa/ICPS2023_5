#!/usr/bin/env python3
"""Pure contract tests for the sequential closure attempt recorder."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import logic
import sequential_closure


class SequentialClosureTests(unittest.TestCase):
    def test_parameter_sweep_is_fixed_ordered_and_preserves_native_dimensions(self):
        specs = logic.cell_specs()
        expected_count = (len(sequential_closure.SWEEP_HEIGHTS_UM) *
                          len(sequential_closure.SWEEP_ROUTE_CHANNELS_UM) *
                          len(sequential_closure.SWEEP_PIN_ORDER_VARIANTS) *
                          len(sequential_closure.SWEEP_MIRRORS))
        for name in sequential_closure.TARGET_CELLS:
            with self.subTest(cell=name):
                first = sequential_closure.parameter_sweep(specs[name])
                self.assertEqual(first, sequential_closure.parameter_sweep(specs[name]))
                self.assertEqual(len(first), expected_count)
                self.assertEqual(len({
                    sequential_closure._candidate_id(name, index, parameters)
                    for index, parameters in enumerate(first, start=1)
                }), expected_count)
                self.assertEqual(first[0], {
                    "height_um": sequential_closure.SWEEP_HEIGHTS_UM[0],
                    "route_channel_um": sequential_closure.SWEEP_ROUTE_CHANNELS_UM[0],
                    "pin_order_variant": "declared",
                    "pin_order": list(specs[name]["pins"]),
                    "mirror": False,
                    "fingers": 1,
                    "mos_dimensions_changed": False,
                })
                self.assertEqual({item["height_um"] for item in first},
                                 set(sequential_closure.SWEEP_HEIGHTS_UM))
                self.assertEqual({item["route_channel_um"] for item in first},
                                 set(sequential_closure.SWEEP_ROUTE_CHANNELS_UM))
                self.assertEqual({item["pin_order_variant"] for item in first},
                                 set(sequential_closure.SWEEP_PIN_ORDER_VARIANTS))
                self.assertEqual({item["mirror"] for item in first},
                                 set(sequential_closure.SWEEP_MIRRORS))
                for parameters in first:
                    self.assertEqual(set(parameters["pin_order"]),
                                     set(specs[name]["pins"]))
                    self.assertEqual(len(parameters["pin_order"]),
                                     len(specs[name]["pins"]))
                    self.assertFalse(parameters["mos_dimensions_changed"])

    def test_failed_stages_remain_not_accepted_and_candidates_are_never_ranked(self):
        parameters = sequential_closure.parameter_sweep(
            logic.cell_specs()["LATCH_TG_X1"])[0]
        failed = sequential_closure._new_candidate(
            "LATCH_TG_X1", "LATCH_TG_X1_TEST", parameters)
        failed_geometry_index = sequential_closure.STAGE_NAMES.index("geometry")
        for index, stage in enumerate(sequential_closure.STAGE_NAMES):
            if index < failed_geometry_index:
                sequential_closure._set_stage(failed, stage, "passed")
            elif stage == "geometry":
                sequential_closure._set_stage(
                    failed, stage, "failed", error="no legal native route")
            else:
                sequential_closure._set_stage(failed, stage, "skipped",
                                              reason="dependent stage not run")
        self.assertEqual(failed["status"], "not_accepted")
        self.assertEqual(failed["failure_stage"], "geometry")
        self.assertEqual(failed["failed_stages"], ["geometry"])
        self.assertFalse(failed["accepted_for_publication"])
        self.assertNotIn("rank", failed)

        verified = sequential_closure._new_candidate(
            "LATCH_TG_X1", "LATCH_TG_X1_TEST_PASS", parameters)
        for stage in sequential_closure.STAGE_NAMES:
            sequential_closure._set_stage(verified, stage, "passed")
        self.assertEqual(verified["status"], "verified_unpublished_candidate")
        self.assertFalse(verified["accepted_for_publication"])
        self.assertNotIn("rank", verified)

    def test_output_root_rejects_source_and_published_artifact_trees(self):
        for relative in ("libs.tech/stdcell/sequential-output",
                         "libs.ref/ICPS2023_5_gen_sc/sequential-output"):
            with self.subTest(path=relative):
                with self.assertRaises(ValueError):
                    sequential_closure._validate_output_root(
                        (sequential_closure.ROOT / relative).resolve())

    def test_area_metrics_keep_cell_array_and_routed_core_domains_separate(self):
        metrics = sequential_closure._area_metrics({
            "area_um2": 12345.0,
            "bbox_um": [0.0, 0.0, 55.0, 225.0],
        })
        self.assertEqual(metrics["cell_geometry_area_um2"], 12345.0)
        self.assertEqual(metrics["cell_bbox_um"], [0.0, 0.0, 55.0, 225.0])
        self.assertIsNone(metrics["array_geometry_area_um2"])
        self.assertIsNone(metrics["routed_core_area_um2"])


if __name__ == "__main__":
    unittest.main()
