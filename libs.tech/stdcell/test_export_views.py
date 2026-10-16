#!/usr/bin/env python3
"""Regression tests for validated structural stdcell exports."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_views


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")


class ExportViewsTests(unittest.TestCase):
    def _design(self, cell="INV_X1"):
        return {
            "name": "export_test",
            "ports": {"A": "input", "Y": "output", "VDD": "inout", "VSS": "inout"},
            "instances": [{"name": "U0", "cell": cell,
                           "connections": {"A": "A", "Y": "Y", "VDD": "VDD", "VSS": "VSS"}}],
            "nets": ["A", "Y", "VDD", "VSS"],
        }

    def test_exports_blackboxes_and_accepted_structural_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = export_views.export_views(self._design(), temporary,
                                               manifest_path=PUBLISHED_MANIFEST)
            self.assertEqual(status["status"], "passed")
            self.assertEqual(status["used_cells"], ["INV_X1"])
            self.assertIsNone(status["metrics"]["A_routed_core_um2"])
            self.assertTrue(status["metrics"]["A_cell_total_um2"] > 0)
            metadata = json.loads((Path(temporary) / "model_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "refused")
            self.assertIn("characterization_evidence_not_supplied", metadata["reason_codes"])
            self.assertFalse((Path(temporary) / "ICPS2023_5_gen_sc.lib").exists())

    def test_unaccepted_cell_is_refused_with_machine_readable_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = export_views.export_views(self._design("NOT_AN_ACCEPTED_CELL"), temporary,
                                               manifest_path=PUBLISHED_MANIFEST)
            self.assertEqual(status["status"], "refused")
            self.assertFalse((Path(temporary) / "mapped_design.v").exists())
            self.assertTrue((Path(temporary) / "export_status.json").is_file())
            metadata = json.loads((Path(temporary) / "model_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "refused")
            self.assertIn("view_validation_or_export_failed", metadata["reason_codes"])

    def test_cell_pin_mismatch_is_rejected_before_rendering(self):
        specs, records, _ = export_views._load_accepted_library(PUBLISHED_MANIFEST)
        design = self._design()
        del design["instances"][0]["connections"]["A"]
        with self.assertRaises(export_views.ExportValidationError):
            export_views._render_mapped_design(design, specs)


if __name__ == "__main__":
    unittest.main()
