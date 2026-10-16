#!/usr/bin/env python3
"""Contract tests for the sequential closure experiment runner."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import logic
import sequential_closure


class SequentialClosureTests(unittest.TestCase):
    def test_compact_selection_requires_explicit_height(self):
        with self.assertRaises(ValueError):
            sequential_closure.parameter_sweep(
                logic.cell_specs()["LATCH_TG_X1"], architecture="compact_rows")


    def test_postlayout_stage_rejects_incomplete_storage_evidence(self):
        class IncompleteCharacterizer:
            def characterize_storage(self, spec, postlayout, output_dir, **kwargs):
                return {"passed": True}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = sequential_closure._run_postlayout_storage(
                logic.cell_specs()["LATCH_TG_X1"], "LATCH_TG_X1_TEST",
                root / "postlayout.spice", root / "state", root,
                IncompleteCharacterizer())
        self.assertEqual(stage["status"], "failed")


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

    def test_logical_model_failure_is_diagnostic_not_a_physical_gate(self):
        parameters = sequential_closure.parameter_sweep(
            logic.cell_specs()["DFF_TG_X1"])[0]
        candidate = sequential_closure._new_candidate(
            "DFF_TG_X1", "DFF_TG_X1_DIAGNOSTIC_TEST", parameters)
        for stage in sequential_closure.STAGE_NAMES:
            if stage == "logical_model_state":
                sequential_closure._set_stage(
                    candidate, stage, "failed",
                    error="ideal logical-model convergence failure")
            else:
                sequential_closure._set_stage(candidate, stage, "passed")

        self.assertEqual(candidate["status"], "verified_unpublished_candidate")
        self.assertEqual(candidate["failed_stages"], ["logical_model_state"])
        self.assertEqual(candidate["diagnostic_failures"], ["logical_model_state"])
        self.assertEqual(candidate["gate_failures"], [])
        self.assertEqual(candidate["incomplete_gate_stages"], [])
        self.assertIsNone(candidate["failure_stage"])
        self.assertFalse(candidate["accepted_for_publication"])
        self.assertNotIn("rank", candidate)

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
