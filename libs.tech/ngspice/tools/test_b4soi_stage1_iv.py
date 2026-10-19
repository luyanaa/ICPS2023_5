#!/usr/bin/env python3
"""Tests for the fixed BSIMSOI-versus-digitized-data regression."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "libs.tech/ngspice/tools/regress_b4soi_stage1_iv.py"
BASELINE = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_baseline.json"
MODEL_CARD = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
MEASUREMENTS = ROOT / "libs.tech/ngspice/fit/b4soi_stage1_iv_digitized.csv"
SPEC = importlib.util.spec_from_file_location("b4soi_iv_regression", SCRIPT)
REGRESSION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGRESSION)


class FrozenIVRegressionTests(unittest.TestCase):
    def test_model_and_digitized_dataset_match_frozen_manifest(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        REGRESSION.verify_frozen_inputs(baseline, MODEL_CARD, MEASUREMENTS)
        curves = REGRESSION.read_curves(MEASUREMENTS)
        self.assertEqual(len(curves), baseline["curve_count"])
        self.assertEqual(sum(len(points) for points in curves.values()), baseline["sample_count"])
        self.assertEqual(sum(len(points) for points in curves.values()), 459)

    def test_fitted_parameter_change_is_rejected(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        source = MODEL_CARD.read_text(encoding="utf-8")
        # Mutate the card's *first* numeric parameter rather than a hard-coded
        # value, so this stays valid across nominal revisions.
        changed = re.sub(r"(?m)^\+ (VTH0)=([0-9.eE+-]+)",
                         lambda m: "+ %s=%.12g" % (m.group(1),
                                                   float(m.group(2)) * 1.004),
                         source, count=1)
        self.assertNotEqual(changed, source)
        with tempfile.TemporaryDirectory() as tempdir:
            changed_card = Path(tempdir) / "changed.inc"
            changed_card.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(REGRESSION.RegressionError, "frozen nch.VTH0 changed"):
                REGRESSION.verify_frozen_inputs(baseline, changed_card, MEASUREMENTS)

    def test_selector_change_is_rejected(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        source = MODEL_CARD.read_text(encoding="utf-8")
        expected = float(baseline.get("expected_soimod", 0))
        replacement = 1 if expected != 1 else 2
        # SOIMOD may be written inline on a continuation line, so match it
        # anywhere on the first line that carries it.
        pattern = r"(?m)^(\+[^\n]*?)SOIMOD=%g" % expected
        changed = re.sub(pattern,
                         lambda m: "%sSOIMOD=%d" % (m.group(1), replacement),
                         source, count=1)
        self.assertNotEqual(changed, source)
        with tempfile.TemporaryDirectory() as tempdir:
            changed_card = Path(tempdir) / "changed.inc"
            changed_card.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(REGRESSION.RegressionError, "SOIMOD changed"):
                REGRESSION.verify_frozen_inputs(baseline, changed_card, MEASUREMENTS)

    @unittest.skipUnless(shutil.which("ngspice"), "ngspice is required for IV regression")
    def test_ngspice_errors_match_the_fixed_baseline(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["curve_count"], 12)
        self.assertEqual(report["sample_count"], 459)
        self.assertEqual(report["metric_weighting"], "unweighted digitized sample points")


if __name__ == "__main__":
    unittest.main()
