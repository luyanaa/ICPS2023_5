#!/usr/bin/env python3
"""The forward P sweep stays a permanent PHYSICAL_SANITY_FAIL regression.

The unified card produces implausible currents (>0.1 A for a W20/L6 device)
when P is swept into forward body bias. That result must never be read as a
body effect, and must not silently disappear when the report excludes it.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
NGSPICE = shutil.which("ngspice")
if NGSPICE:
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(
        "audit_nmos4_body_bias", TOOLS / "audit_nmos4_body_bias.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)


@unittest.skipUnless(NGSPICE, "ngspice executable required")
class ForwardSweepSanityTest(unittest.TestCase):
    def test_forward_p_sweep_is_never_a_valid_body_effect(self):
        with tempfile.TemporaryDirectory(prefix="icps-nmos4-sanity-") as temporary:
            result = audit._run_ngspice(
                audit.UNIFIED_SELECTOR, audit.BIAS_POINTS[0],
                Path(temporary), NGSPICE, sweep=(-3.0, 3.0, 0.1))
        self.assertEqual(result["status"], "observed", result)
        self.assertEqual(result["metrics"]["physical_sanity"],
                         "implausible_or_nonconverged")
        self.assertGreater(result["metrics"]["drain_current_max_a"],
                           audit.MAX_PHYSICAL_CURRENT_A)


if __name__ == "__main__":
    unittest.main()
