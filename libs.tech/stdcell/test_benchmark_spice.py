#!/usr/bin/env python3
"""Electrical evidence boundaries, independent of simulator startup state."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_spice
import characterize


class ElectricalEvidenceTests(unittest.TestCase):
    def test_unknown_preflush_state_is_not_a_passing_logic_assertion(self):
        samples = benchmark_spice._verify_state_samples(
            "rise_000_q0 = 1.5\n", {"rise_000_q0": (None, "before four real flush edges")}, ["Q0"])
        self.assertFalse(samples[0]["checked"])
        self.assertIsNone(samples[0]["passed"])
        with self.assertRaises(benchmark_spice.BenchmarkCharacterizationError):
            benchmark_spice._verify_state_samples(
                "rise_000_q0 = 1.5\n", {"rise_000_q0": (1, "after known-state capture")}, ["Q0"])

    def test_known_logic_thresholds_are_inclusive_and_missing_values_fail_closed(self):
        for bit, accepted, rejected in ((0, 0.3, 0.30001), (1, 2.7, 2.69999)):
            expected = {"fall_000_q0": (bit, "known-state hold")}
            samples = benchmark_spice._verify_state_samples(
                f"fall_000_q0 = {accepted}\n", expected, ["Q0"])
            self.assertTrue(samples[0]["checked"] and samples[0]["passed"])
            for text in (f"fall_000_q0 = {rejected}\n", "", "fall_000_q0 = 1e309\n"):
                with self.subTest(bit=bit, text=text):
                    with self.assertRaises(characterize.CharacterizationError):
                        benchmark_spice._verify_state_samples(text, expected, ["Q0"])


if __name__ == "__main__":
    unittest.main()
