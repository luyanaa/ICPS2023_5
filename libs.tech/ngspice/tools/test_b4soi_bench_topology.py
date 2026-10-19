#!/usr/bin/env python3
"""Regressions for the bench topology gate.

The gate exists because a fanout load written as `(OUT FL ...)` - i.e. with D and
G swapped - silently produced a 1.58 V high level instead of 3.0 V, and cost a
full diagnostic cycle to find. A gate that cannot fail is worthless, so these
tests include deliberately broken fixtures.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "timing", TOOLS / "audit_b4soi_bridge_timing.py")
TIMING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TIMING)


def deck(fanout_lines):
    base = TIMING.inverter_deck("dummy.inc", 3.0, 2.0, 0, "TRAP", 20e-12)
    return base + fanout_lines


CORRECT_LOAD = ["MFN0 F0 OUT 0 SUB nch W=24u L=6u",
                "MFP0 F0 OUT VDD SUB pch W=48u L=6u",
                "CF0 F0 0 1e-15"]
SWAPPED_LOAD = ["MFN0 OUT F0 0 SUB nch W=24u L=6u",
                "MFP0 OUT F0 VDD SUB pch W=48u L=6u",
                "CF0 F0 0 1e-15"]
TIED_DRAIN_LOAD = ["MFN0 OUT OUT 0 SUB nch W=24u L=6u",
                   "MFP0 OUT OUT VDD SUB pch W=48u L=6u"]
WRONG_SUBSTRATE = ["MFN0 F0 OUT 0 VDD nch W=24u L=6u",
                   "MFP0 F0 OUT VDD SUB pch W=48u L=6u"]
WRONG_SOURCE = ["MFN0 F0 OUT VDD SUB nch W=24u L=6u",
                "MFP0 F0 OUT VDD SUB pch W=48u L=6u"]


class TopologyGateTests(unittest.TestCase):
    def check(self, lines):
        return TIMING.check_bench_topology(lines, "VDD", "0", "SUB", "OUT", "IN")

    def test_correct_gate_load_passes(self):
        self.assertTrue(self.check(deck(CORRECT_LOAD)))

    def test_swapped_drain_gate_is_rejected(self):
        # The exact defect that produced the bogus 1.58 V level.
        with self.assertRaisesRegex(ValueError, "undriven node"):
            self.check(deck(SWAPPED_LOAD))

    def test_error_names_the_offending_node(self):
        try:
            self.check(deck(SWAPPED_LOAD))
        except ValueError as error:
            self.assertIn("F0", str(error))
        else:
            self.fail("swapped load was accepted")

    def test_load_drains_tied_to_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "load drain is tied"):
            self.check(deck(TIED_DRAIN_LOAD))

    def test_non_common_substrate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "E terminal"):
            self.check(deck(WRONG_SUBSTRATE))

    def test_wrong_load_source_rail_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "load NMOS"):
            self.check(deck(WRONG_SOURCE))

    def test_fanout_of_four_still_passes(self):
        lines = []
        for index in range(4):
            lines += ["MFN%d F%d OUT 0 SUB nch W=24u L=6u" % (index, index),
                      "MFP%d F%d OUT VDD SUB pch W=48u L=6u" % (index, index),
                      "CF%d F%d 0 1e-15" % (index, index)]
        self.assertTrue(self.check(deck(lines)))


if __name__ == "__main__":
    unittest.main()
