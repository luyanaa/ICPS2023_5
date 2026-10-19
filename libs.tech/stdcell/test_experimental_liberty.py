#!/usr/bin/env python3
"""Focused contract tests for the experimental Xyce Liberty serializer."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import experimental_liberty as liberty


MANIFEST = ROOT / "libs.ref/ICPS2023_5_gen_sc/doc/cell_manifest.json"


class ExperimentalLibertyTests(unittest.TestCase):
    def _binding(self):
        return liberty._configured_binding()

    def _evidence(self, directory: Path, *, wrong_engine: bool = False,
                  incomplete: bool = False) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        binding = self._binding()
        for record in manifest["cells"]:
            if record.get("status") != "strict_physical_pass":
                continue
            cell = record["cell"]
            spec = record["source_spec"]
            evidence = {
                "schema": liberty.SCHEMA,
                "cell": cell,
                "binding": dict(binding),
                "conditions": {"voltage_v": 3.0, "temperature_c": 25.0, "mode": "nominal"},
                "scope": {"loads_ff": [5.0], "slews_ns": [2.0]},
                "arcs": [],
            }
            if spec.get("sequential"):
                evidence["sequential_excluded"] = True
            else:
                expected = liberty._expected_arcs(spec)
                for input_pin, output, condition in sorted(expected):
                    sense = expected[(input_pin, output, condition)]
                    evidence["arcs"].append({
                        "input": input_pin, "output": output, "condition": dict(condition),
                        "sense": sense, "load_ff": 5.0, "slew_ns": 2.0,
                        "rise_delay_ns": 1.0, "fall_delay_ns": 1.1,
                        "rise_transition_ns": 0.4, "fall_transition_ns": 0.5,
                    })
            if wrong_engine and cell == "INV_X1":
                evidence["binding"]["engine"] = "ngspice"
            if incomplete and cell == "INV_X1":
                evidence["arcs"].pop()
            (directory / f"{cell}.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_complete_evidence_renders_deterministically_and_excludes_sequential(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            self._evidence(evidence)
            first = liberty.generate_liberty(MANIFEST, evidence)
            second = liberty.generate_liberty(MANIFEST, evidence)
            self.assertEqual(first, second)
            self.assertIn("EXPERIMENTAL ONLY", first)
            self.assertIn("library (ICPS2023_5_experimental_xyce_single_corner)", first)
            self.assertIn("cell (INV_X1)", first)
            self.assertIn('function : "!A";', first)
            self.assertIn('related_pin : "A";', first)
            self.assertNotIn("cell (LATCH_TG_X1)", first)
            self.assertNotIn("timing_type : setup", first)

    def test_nested_cell_evidence_renders(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            self._evidence(evidence)
            flat = {path.name: path.read_text(encoding="utf-8") for path in evidence.glob("*.json")}
            for path in evidence.glob("*.json"):
                path.unlink()
            for name, text in flat.items():
                cell_dir = evidence / name.removesuffix(".json")
                cell_dir.mkdir()
                (cell_dir / name).write_text(text, encoding="utf-8")
            rendered = liberty.generate_liberty(MANIFEST, evidence)
            self.assertIn("cell (INV_X1)", rendered)
            self.assertNotIn("cell (LATCH_TG_X1)", rendered)

    def test_wrong_engine_evidence_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            self._evidence(evidence, wrong_engine=True)
            with self.assertRaisesRegex(liberty.ExperimentalLibertyError, "engine"):
                liberty.validate_evidence(MANIFEST, evidence)

    def test_incomplete_arc_evidence_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            self._evidence(evidence, incomplete=True)
            with self.assertRaisesRegex(liberty.ExperimentalLibertyError, "incomplete arc"):
                liberty.validate_evidence(MANIFEST, evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
