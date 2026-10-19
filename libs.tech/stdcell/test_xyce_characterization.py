#!/usr/bin/env python3
"""Focused contract tests for the Xyce characterization evidence producer.

No simulator is required: these tests exercise the evidence mapping, the
physical-evidence gate, the binding rules, and the renderer handshake.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experimental_liberty  # noqa: E402
import xyce_characterization as producer  # noqa: E402


BINDING = {
    "engine": "xyce",
    "engine_version": producer.EXPECTED_ENGINE_VERSION,
    "engine_level": producer.EXPECTED_ENGINE_LEVEL,
    "section": "b4soi",
    "model_sha256": "a" * 64,
    "model_include_sha256": "b" * 64,
}


def _arc(*, input_pin: str = "A", output_pin: str = "Y",
         condition: dict | None = None, sense: str = "negative_unate") -> dict:
    return {
        "input": input_pin, "output": output_pin, "condition": condition or {},
        "sense": sense, "load_ff": 5.0, "input_slew_ns": 2.0,
        "rise_delay_ns": 1.0, "fall_delay_ns": 1.1,
        "rise_transition_ns": 0.4, "fall_transition_ns": 0.5,
        "noise_margin": {"vil_v": 0.5, "vih_v": 1.0, "nml_v": 0.4, "nmh_v": 1.9},
    }


def _synthetic_manifest(root: Path, count: int = 18) -> Path:
    cells = []
    for index in range(count):
        name = f"UNIT{index:02d}_X1"
        cells.append({
            "cell": name, "status": "strict_physical_pass",
            "source_spec": {
                "name": name, "inputs": ["A"], "outputs": ["Y"],
                "pins": ["A", "Y", "VDD", "VSS"], "function": {"Y": "!A"},
                "sequential": None,
            },
        })
    manifest = root / "cell_manifest.json"
    manifest.write_text(json.dumps({"cells": cells}), encoding="utf-8")
    return manifest


def _write_evidence(root: Path, manifest: Path, *,
                    empty_for: str | None = None) -> Path:
    evidence = root / "characterization"
    for record in json.loads(manifest.read_text(encoding="utf-8"))["cells"]:
        cell = record["cell"]
        arcs = [] if cell == empty_for else [_arc()]
        payload = producer.cell_record(
            cell, binding=BINDING, integration_method="gear", arcs=arcs,
            provenance={"postlayout_spice": f"build/physical/{cell}/postlayout.spice"})
        (evidence / cell).mkdir(parents=True, exist_ok=True)
        (evidence / cell / f"{cell}.json").write_text(json.dumps(payload) + "\n",
                                                     encoding="utf-8")
    return evidence


class ArcRecordTests(unittest.TestCase):
    def test_arc_record_narrows_fields_and_renames_slew(self):
        record = producer.arc_record(_arc(condition={"B": 0}))
        self.assertEqual(record["slew_ns"], 2.0)
        self.assertEqual(record["load_ff"], 5.0)
        self.assertEqual(record["condition"], {"B": 0})
        self.assertEqual(sorted(record), sorted([
            "input", "output", "condition", "sense", "load_ff", "slew_ns",
            "rise_delay_ns", "fall_delay_ns", "rise_transition_ns", "fall_transition_ns"]))
        self.assertNotIn("noise_margin", record)

    def test_arc_record_rejects_invalid_measurements_and_sense(self):
        for field in producer.ARC_MEASUREMENT_FIELDS:
            for value in (0.0, -1.0, float("nan"), True, "1"):
                with self.subTest(field=field, value=value), self.assertRaises(
                        producer.XyceCharacterizationError):
                    producer.arc_record({**_arc(), field: value})
        with self.assertRaises(producer.XyceCharacterizationError):
            producer.arc_record({**_arc(), "sense": "non_unate"})
        with self.assertRaises(producer.XyceCharacterizationError):
            producer.arc_record({**_arc(), "condition": {"B": 2}})

    def test_sequential_records_carry_no_arcs(self):
        record = producer.cell_record("DFF_GATE_X1", binding=BINDING,
                                      integration_method="gear", arcs=[],
                                      provenance={}, sequential=True)
        self.assertTrue(record["sequential_excluded"])
        self.assertEqual(record["arcs"], [])
        with self.assertRaises(producer.XyceCharacterizationError):
            producer.cell_record("DFF_GATE_X1", binding=BINDING,
                                 integration_method="gear", arcs=[_arc()],
                                 provenance={}, sequential=True)


class PhysicalEvidenceTests(unittest.TestCase):
    def _cell_dir(self, root: Path, **overrides) -> Path:
        directory = root / "physical" / "INV_X1"
        directory.mkdir(parents=True, exist_ok=True)
        record = {check: True for check in producer.PHYSICAL_CHECKS}
        record.update({"soi_edge_protection": {"passed": True},
                       "fabrication": {"passed": True}})
        record.update(overrides)
        (directory / "physical.json").write_text(json.dumps(record), encoding="utf-8")
        (directory / "postlayout.spice").write_text("* netlist\n", encoding="utf-8")
        return directory

    def test_passing_evidence_records_repository_relative_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._cell_dir(root)
            evidence = producer.physical_evidence(root / "physical", "INV_X1", root)
        self.assertTrue(evidence["record"]["assembly"])
        self.assertEqual(evidence["provenance"]["postlayout_spice"],
                         "physical/INV_X1/postlayout.spice")
        self.assertTrue(evidence["provenance"]["physical_evidence"].endswith("physical.json"))

    def test_failed_check_or_missing_netlist_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._cell_dir(root, strict_lvs=False)
            with self.assertRaisesRegex(producer.XyceCharacterizationError, "strict_lvs"):
                producer.physical_evidence(root / "physical", "INV_X1", root)
            for gate in ("soi_edge_protection", "fabrication"):
                with self.subTest(gate=gate):
                    self._cell_dir(root, **{gate: {"passed": False}})
                    with self.assertRaisesRegex(producer.XyceCharacterizationError, gate):
                        producer.physical_evidence(root / "physical", "INV_X1", root)
            self._cell_dir(root)
            (root / "physical" / "INV_X1" / "postlayout.spice").unlink()
            with self.assertRaisesRegex(producer.XyceCharacterizationError, "assembled netlist"):
                producer.physical_evidence(root / "physical", "INV_X1", root)
            with self.assertRaises(producer.XyceCharacterizationError):
                producer.physical_evidence(root / "physical", "NOPE_X1", root)


class BindingTests(unittest.TestCase):
    def _configured(self, root: Path, engine: str = "xyce") -> dict:
        library = root / "selector.lib.spice"
        include = root / "card.inc"
        library.write_text(".lib b4soi\n.endl b4soi\n", encoding="utf-8")
        include.write_text("NCH=1e15\n", encoding="utf-8")
        return {"engine": engine, "engine_version": producer.EXPECTED_ENGINE_VERSION,
                "engine_level": producer.EXPECTED_ENGINE_LEVEL, "section": "b4soi",
                "library_path": str(library), "include_path": str(include)}

    def test_only_the_xcye_bridge_binding_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = producer.evidence_binding(self._configured(root))
            self.assertEqual(binding["engine"], "xyce")
            self.assertEqual(len(binding["model_sha256"]), 64)
            for engine in ("ngspice", "spectre"):
                with self.subTest(engine=engine), self.assertRaises(
                        producer.XyceCharacterizationError):
                    producer.evidence_binding(self._configured(root, engine))

    def test_missing_configured_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = self._configured(root)
            Path(configured["include_path"]).unlink()
            with self.assertRaisesRegex(producer.XyceCharacterizationError, "missing"):
                producer.evidence_binding(configured)


class RendererHandshakeTests(unittest.TestCase):
    def test_complete_evidence_is_accepted_and_a_missing_arc_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _synthetic_manifest(root)
            evidence = _write_evidence(root, manifest)
            validated = experimental_liberty.validate_evidence(manifest, evidence,
                                                               binding=BINDING)
            self.assertEqual(len(validated["cells"]), 18)
            self.assertEqual(validated["excluded_sequential"], [])

            broken = _write_evidence(root, manifest, empty_for="UNIT09_X1")
            with self.assertRaises(experimental_liberty.ExperimentalLibertyError):
                experimental_liberty.validate_evidence(manifest, broken, binding=BINDING)

    def test_unknown_or_unaccepted_cell_selection_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _synthetic_manifest(root)
            with self.assertRaisesRegex(producer.XyceCharacterizationError,
                                        "not accepted cells"):
                producer.characterize_cells(manifest, root / "physical", root / "out",
                                            cells=["MISSING_X1"])


if __name__ == "__main__":
    unittest.main()
