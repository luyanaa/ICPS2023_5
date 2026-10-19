#!/usr/bin/env python3
"""Focused contract tests for KLayout PEX artifact evidence."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pex_evidence


ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"


class PexEvidenceTests(unittest.TestCase):
    def _artifact(self, directory: Path, layout: Path, *, profile="icps2023_5_1m",
                  mode="design", upper="[]", tool="libs.tech/klayout/pex/icps2023_5_pex.py"):
        artifact = directory / "parasitics.spice"
        artifact.write_text(
            "* ICPS2023_5 (minimal Fab SOI-CMOS) parasitics\n"
            "* extracted by %s\n"
            "* layout: %s  top cell: INV_X1  layer_mode: %s\n"
            "* process profile: %s\n"
            "* upper-metal layers: %s\n"
            ".SUBCKT INV_X1 PORT0001 SUB\n"
            ".ENDS INV_X1\n" % (tool, layout, mode, profile, upper),
            encoding="utf-8",
        )
        return artifact

    def test_records_artifact_and_explicit_non_timing_flags(self):
        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            layout = directory / "layout.gds"
            layout.write_bytes(b"layout")
            artifact = self._artifact(directory, layout)
            evidence = pex_evidence.collect_pex_evidence(
                artifact, layout, "icps2023_5_1m", TOOL)
            self.assertEqual(evidence["artifact_path"], str(artifact.resolve()))
            self.assertEqual(evidence["source_layout"], str(layout.resolve()))
            self.assertEqual(evidence["process_profile"], "icps2023_5_1m")
            self.assertEqual(evidence["tool"], str(TOOL.resolve()))
            self.assertTrue(evidence["parasitics_only_spice"])
            self.assertFalse(evidence["timing_spef_available"])
            self.assertFalse(evidence["openrcx_qualified"])

    def test_rejects_missing_artifact(self):
        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            layout = directory / "layout.gds"
            layout.write_bytes(b"layout")
            with self.assertRaisesRegex(pex_evidence.PexEvidenceError, "missing PEX artifact"):
                pex_evidence.collect_pex_evidence(
                    directory / "missing.spice", layout, "icps2023_5_1m", TOOL)

    def test_rejects_upper_metal_profile_mismatch(self):
        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            layout = directory / "layout.gds"
            layout.write_bytes(b"layout")
            artifact = self._artifact(directory, layout, upper='["ML2"]')
            with self.assertRaisesRegex(pex_evidence.PexEvidenceError, "upper-metal/profile mismatch"):
                pex_evidence.collect_pex_evidence(
                    artifact, layout, "icps2023_5_1m", TOOL, upper_metal_layers=["ML2"])

    def test_rejects_malformed_provenance(self):
        with tempfile.TemporaryDirectory() as tempdir:
            directory = Path(tempdir)
            layout = directory / "layout.gds"
            layout.write_bytes(b"layout")
            artifact = self._artifact(directory, layout, tool="wrong.py")
            with self.assertRaisesRegex(pex_evidence.PexEvidenceError, "tool provenance"):
                pex_evidence.collect_pex_evidence(
                    artifact, layout, "icps2023_5_1m", TOOL)


if __name__ == "__main__":
    unittest.main()
