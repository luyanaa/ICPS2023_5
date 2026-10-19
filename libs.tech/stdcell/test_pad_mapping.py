#!/usr/bin/env python3
"""Focused validation tests for explicit frame pad/net assignments."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pad_mapping


BASE = HERE.parents[1]
DEF_1X1 = BASE / "libs.ref" / "ICPS2023_5_fd_base" / "def" / "ICPS2023_5_fd_base.def"
DEF_2X2 = BASE / "libs.ref" / "ICPS2023_5_fd_base_2x2" / "def" / "ICPS2023_5_fd_base_2x2.def"


class PadMappingValidationTests(unittest.TestCase):
    def assertMappingError(self, mapping, **kwargs) -> None:
        with self.assertRaises(pad_mapping.PadMappingError):
            pad_mapping.validate_pad_mapping(mapping, **kwargs)

    def test_absent_mapping_is_distinguishable_for_non_flow_callers(self):
        self.assertIsNone(pad_mapping.validate_pad_mapping(None))

    def test_actual_flow_rejects_absent_mapping(self):
        self.assertMappingError(None, actual_flow=True)

    def test_empty_mapping_and_invalid_values_fail_closed(self):
        self.assertMappingError({})
        self.assertMappingError({"": "VDD"})
        self.assertMappingError({"PAD_56": "VDD"})
        self.assertMappingError({"PAD_00": 3})
        self.assertMappingError({"PAD_00": "  "})

    def test_mapping_is_sorted_and_does_not_infer_supply_names(self):
        mapping = {"PAD_02": "signal_b", "PAD_00": "VSS", "PAD_01": "signal_a"}
        self.assertEqual(
            pad_mapping.validate_pad_mapping(mapping, def_path=DEF_1X1),
            {"PAD_00": "VSS", "PAD_01": "signal_a", "PAD_02": "signal_b"},
        )

    def test_one_by_one_def_rejects_unknown_pad(self):
        self.assertMappingError(
            {"PAD_28": "signal"},
            def_path=DEF_1X1,
        )

    def test_two_by_two_def_accepts_highest_explicit_pad(self):
        self.assertEqual(
            pad_mapping.validate_pad_mapping(
                {"PAD_55": "explicit_net"}, def_path=DEF_2X2
            ),
            {"PAD_55": "explicit_net"},
        )

    def test_def_contract_must_be_28_or_56_pad_template(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "not_a_frame.def"
            invalid.write_text("VERSION 5.8 ;\nPINS 1 ;\nEND PINS\n", encoding="utf-8")
            self.assertMappingError(
                {"PAD_00": "N"}, def_path=invalid)


if __name__ == "__main__":
    unittest.main()
