#!/usr/bin/env python3
"""Guard against treating incomplete/invalid simulator evidence as a successful audit."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import b4soi_depletion as audit
import audit_b4soi_depletion_dynamic as dynamic


class SimulatorEvidenceTests(unittest.TestCase):
    def run_case(self, folder, *, data=None, output="", timeout=False, stop=1.0):
        def simulate(command, **kwargs):
            if data is not None:
                (Path(kwargs["cwd"]) / "case.dat").write_text(data, encoding="utf-8")
            if timeout:
                raise subprocess.TimeoutExpired(command, 1, output=b"partial analysis")
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        with patch.object(audit.subprocess, "run", side_effect=simulate):
            return audit.run_deck("ngspice", folder, "case", ["* evidence regression", ".end"],
                                  vectors=1, stop=stop)

    def test_missing_output_cannot_reuse_previous_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "case"
            stale.mkdir()
            (stale / "case.dat").write_text("0 0\n1 3\n", encoding="utf-8")
            result = self.run_case(tmp)
            self.assertEqual(result["evidence"]["status"], "failed")
            self.assertFalse((stale / "case.dat").exists())
            self.assertEqual(result["vectors"], [])

    def test_zero_exit_after_aborted_analysis_is_not_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_case(tmp, data="0 0\n1 3\n",
                                   output="doAnalyses: TRAN: Timestep too small; simulation aborted")
            self.assertEqual(result["evidence"]["status"], "failed")
            self.assertTrue(Path(result["evidence"]["data"]).is_file())

    def test_truncated_nonfinite_and_nonmonotone_output_is_rejected(self):
        for data in ("0 0\n0.9 3\n", "0 0\n1 nan\n", "0 0\n0.8 2\n0.7 2\n1 3\n"):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                result = self.run_case(tmp, data=data)
                self.assertEqual(result["evidence"]["status"], "failed")
                self.assertEqual(result["vectors"], [])

    def test_zero_ending_sweep_distinguishes_roundoff_from_truncation(self):
        for final, expected in ((-1.967523366452895e-14, "observed"), (-0.01, "failed")):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as tmp:
                result = self.run_case(tmp, data="-3 2\n-1 1\n%.16g 0\n" % final, stop=0.0)
                self.assertEqual(result["evidence"]["status"], expected)

    def test_timeout_preserves_partial_evidence_without_accepting_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_case(tmp, data="0 0\n0.5 2\n", timeout=True)
            self.assertEqual(result["evidence"]["status"], "inconclusive")
            self.assertTrue(Path(result["evidence"]["data"]).is_file())
            self.assertEqual(result["vectors"], [])

    def test_signed_complex_admittance_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ac.dat"
            path.write_text("1000 -2e-9 -3e-12\n", encoding="utf-8")
            freq, currents = audit.read_data(path, 1, complex_data=True)
            self.assertEqual(freq, [1000.0])
            self.assertEqual(currents, [[complex(-2e-9, -3e-12)]])


class ThresholdExtractionTests(unittest.TestCase):
    def test_exact_current_sample_and_endpoint_are_not_reinterpolated(self):
        cases = [
            ([0.0, 1.0, 2.0], [0.0, 1e-6, 3e-6], 1.0),
            ([-2.0, -1.0, 0.0], [3e-6, 1e-6, 0.0], -1.0),
            ([0.0, 1.0], [1e-6, 2e-6], 0.0),
            ([0.0, 1.0], [0.0, 1e-6], 1.0),
        ]
        for axis, current, expected in cases:
            with self.subTest(axis=axis, current=current):
                result = dynamic._constant_current_threshold(axis, current, 1e-6)
                self.assertEqual(result["constant_current_status"], "bracketed_single")
                self.assertEqual(result["constant_current_VGS_V"], expected)

    def test_equality_interval_and_multiple_roots_are_inconclusive(self):
        for current, expected in (
                ([0.0, 1e-6, 1e-6, 3e-6], "nonunique_equality_interval"),
                ([0.0, 2e-6, 0.0], "multiple_crossings")):
            with self.subTest(current=current):
                result = dynamic._constant_current_threshold(list(range(len(current))), current, 1e-6)
                self.assertEqual(result["constant_current_status"], expected)
                self.assertIsNone(result["constant_current_VGS_V"])


if __name__ == "__main__":
    unittest.main()
