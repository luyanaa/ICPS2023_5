#!/usr/bin/env python3
"""Regression tests for native flat-core routing evidence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core_router


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")


class CoreRouterTests(unittest.TestCase):
    def test_tiny_accepted_cell_benchmark_emits_rankable_native_route(self):
        with tempfile.TemporaryDirectory() as directory:
            report = core_router.route_core(
                None, directory, manifest_path=PUBLISHED_MANIFEST)
            self.assertTrue(report["rankable"], report)
            self.assertEqual(report["status"], "route_proof_passed")
            self.assertEqual(report["overflow_count"], 0)
            self.assertTrue(report["native_layers_only"])
            self.assertEqual(report["routing_layers_used"], ["ML1"])
            self.assertGreater(report["cell_geometry_area_um2"], 0)
            self.assertGreater(report["routed_core_area_um2"],
                               report["cell_geometry_area_um2"])
            self.assertFalse(report["verification"]["frame_capacity_claimed"])
            for key in ("mapped_netlist", "gds", "reference_spice", "route_evidence"):
                self.assertTrue(Path(report["artifacts"][key]).is_file(), key)
            with Path(report["artifacts"]["route_evidence"]).open(encoding="utf-8") as stream:
                persisted = json.load(stream)
            self.assertTrue(persisted["rankable"])
            self.assertFalse(persisted["verification"]["foundry_signoff_claimed"])

    def test_invalid_candidate_keeps_nonrankable_machine_evidence(self):
        design = core_router.build_tiny_benchmark(PUBLISHED_MANIFEST)
        design["ports"]["UNCONNECTED"] = "input"
        with tempfile.TemporaryDirectory() as directory:
            report = core_router.route_core(
                design, directory, manifest_path=PUBLISHED_MANIFEST)
            self.assertFalse(report["rankable"])
            self.assertTrue(report["failed"])
            self.assertEqual(report["status"], "failed")
            self.assertIsNotNone(report["failure"])
            self.assertGreater(report["overflow_count"], 0)
            evidence_path = Path(report["artifacts"]["route_evidence"])
            self.assertTrue(evidence_path.is_file())
            with evidence_path.open(encoding="utf-8") as stream:
                persisted = json.load(stream)
            self.assertFalse(persisted["rankable"])
            self.assertTrue(persisted["failure"]["error"])


if __name__ == "__main__":  # pragma: no cover - direct test invocation
    unittest.main()
