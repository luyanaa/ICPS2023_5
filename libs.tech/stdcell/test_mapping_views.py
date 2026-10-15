#!/usr/bin/env python3
"""Mapping-view eligibility and Boolean-preservation regression tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import logic
import mapping


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")


class MappingViewTests(unittest.TestCase):
    def _library(self):
        import library
        return library

    def _evaluate(self, netlist, inputs):
        values = {"VDD": 1, "VSS": 0, **inputs}
        logical_specs = logic.cell_specs()
        for instance in netlist["instances"]:
            spec = logical_specs[instance["cell"]]
            connections = instance["connections"]
            pin_values = {pin: values[connections[pin]] for pin in spec["inputs"]}
            outputs = logic.evaluate(spec, pin_values)
            for pin in spec["outputs"]:
                values[connections[pin]] = outputs[pin]
        return values

    def test_published_physical_fallback_truth_tables(self):
        accepted = self._library().physical_specs(PUBLISHED_MANIFEST)
        unavailable = {
            "BUF_X1", "MUX2_TG6T_X1", "MUX2_STATIC_X1", "XOR2_X1", "XNOR2_X1",
        }
        self.assertTrue({"INV_X1", "NAND2_X1", "NOR2_X1"} <= set(accepted))
        self.assertTrue(unavailable.isdisjoint(accepted))
        netlist = mapping.map_boolean_design(
            "physical_fallback",
            {"A": "input", "B": "input", "S": "input",
             "MUX_Y": "output", "XOR_Y": "output", "XNOR_Y": "output"},
            {"MUX_Y": "(S & B) | (!S & A)",
             "XOR_Y": "A ^ B",
             "XNOR_Y": "!(A ^ B)"},
            library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST)

        emitted = {instance["cell"] for instance in netlist["instances"]}
        basic_network = {
            "INV_X1", "NAND2_X1", "NAND2_BA_X1", "NOR2_X1", "NOR2_BA_X1",
        }
        self.assertTrue(emitted <= set(accepted), emitted - set(accepted))
        self.assertTrue(emitted <= basic_network, emitted - basic_network)
        self.assertTrue(unavailable.isdisjoint(emitted))
        for a in (0, 1):
            for b in (0, 1):
                for select in (0, 1):
                    with self.subTest(A=a, B=b, S=select):
                        values = self._evaluate(
                            netlist, {"A": a, "B": b, "S": select})
                        self.assertEqual(values["MUX_Y"], b if select else a)
                        self.assertEqual(values["XOR_Y"], a ^ b)
                        self.assertEqual(values["XNOR_Y"], 1 ^ (a ^ b))

    def test_physical_boolean_wires_and_constants_use_only_eligible_cells(self):
        accepted = self._library().physical_specs(PUBLISHED_MANIFEST)
        netlist = mapping.map_boolean_design(
            "physical_aliases",
            {"A": "input", "COPY": "output", "ZERO": "output", "ONE": "output"},
            {"COPY": "A", "ZERO": "0", "ONE": "1"},
            library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST)
        emitted = {instance["cell"] for instance in netlist["instances"]}
        self.assertTrue(emitted <= set(accepted), emitted - set(accepted))
        self.assertNotIn("BUF_X1", emitted)
        for a in (0, 1):
            values = self._evaluate(netlist, {"A": a})
            self.assertEqual(values["COPY"], a)
            self.assertEqual(values["ZERO"], 0)
            self.assertEqual(values["ONE"], 1)

    def test_yosys_mux_select_polarity_survives_physical_decomposition(self):
        raw = {
            "name": "yosys_mux",
            "ports": {"A": "input", "B": "input", "S": "input",
                      "Y": "output", "YN": "output"},
            "instances": [
                {"name": "U_MUX", "cell": "$_MUX_",
                 "connections": {"A": "A", "B": "B", "S": "S", "Y": "Y"}},
                {"name": "U_NMUX", "cell": "$_NMUX_",
                 "connections": {"A": "A", "B": "B", "S": "S", "Y": "YN"}},
            ],
        }
        netlist = mapping.map_yosys_netlist(
            raw, library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST)
        accepted = self._library().physical_specs(PUBLISHED_MANIFEST)
        emitted = {instance["cell"] for instance in netlist["instances"]}
        basic_network = {
            "INV_X1", "NAND2_X1", "NAND2_BA_X1", "NOR2_X1", "NOR2_BA_X1",
        }
        self.assertTrue(emitted <= set(accepted), emitted - set(accepted))
        self.assertTrue(emitted <= basic_network, emitted - basic_network)
        for a in (0, 1):
            for b in (0, 1):
                for select in (0, 1):
                    with self.subTest(A=a, B=b, S=select):
                        values = self._evaluate(netlist, {"A": a, "B": b, "S": select})
                        expected = b if select else a
                        self.assertEqual(values["Y"], expected)
                        self.assertEqual(values["YN"], 1 - expected)

    def test_unaccepted_canonical_cell_is_rejected_in_physical_view(self):
        raw = {
            "name": "unaccepted_xor",
            "ports": {"A": "input", "B": "input", "Y": "output"},
            "instances": [{
                "name": "U_XOR", "cell": "XOR2_X1",
                "connections": {"A": "A", "B": "B", "Y": "Y"},
            }],
        }
        with self.assertRaisesRegex(ValueError, "XOR2_X1.*physical_qualified"):
            mapping.map_yosys_netlist(
                raw, library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST)

    def test_unavailable_storage_fails_in_physical_view_and_reference_has_all_26(self):
        for cell, connections, kind in (
                ("$_DFF_P_", {"D": "D", "C": "CLK", "Q": "Q"}, "DFF"),
                ("$_DLATCH_P_", {"D": "D", "E": "EN", "Q": "Q"}, "latch")):
            raw = {
                "name": "storage",
                "ports": {"D": "input", "CLK": "input", "EN": "input", "Q": "output"},
                "instances": [{"name": "U_STORAGE", "cell": cell,
                               "connections": connections}],
            }
            with self.subTest(cell=cell):
                with self.assertRaisesRegex(
                        ValueError, rf"{kind} cell .*physical_qualified"):
                    mapping.map_yosys_netlist(
                        raw, library_view="physical_qualified",
                        manifest_path=PUBLISHED_MANIFEST)

        canonical = logic.cell_specs()
        all_cells = {
            "name": "all_reference_cells", "ports": {},
            "instances": [
                {"name": f"U_{name}", "cell": name,
                 "connections": {pin: pin for pin in spec["pins"]}}
                for name, spec in canonical.items()
            ],
        }
        reference = mapping.map_yosys_netlist(all_cells)
        self.assertEqual({instance["cell"] for instance in reference["instances"]},
                         set(canonical))
        self.assertEqual(len(reference["instances"]), 26)

    def test_empty_explicit_specs_do_not_enable_unlisted_boolean_cells(self):
        with self.assertRaisesRegex(ValueError, "logical_reference"):
            mapping.map_boolean_design(
                "empty_specs", {"A": "input", "Y": "output"}, {"Y": "A"},
                specs={})
        with self.assertRaisesRegex(ValueError, "logical_reference"):
            mapping.map_boolean_design(
                "empty_constant_specs", {"Y": "output"}, {"Y": "1"}, specs={})
        with self.assertRaisesRegex(ValueError, "physical_qualified"):
            mapping.map_boolean_design(
                "empty_physical_specs", {"A": "input", "Y": "output"},
                {"Y": "A"}, library_view="physical_qualified",
                manifest_path=PUBLISHED_MANIFEST, specs={})

    def test_area_uses_accepted_physical_dimensions(self):
        library = self._library()
        manifests = library.physical_manifests(PUBLISHED_MANIFEST)
        netlist = mapping.map_boolean_design(
            "physical_area", {"A": "input", "B": "input", "Y": "output"},
            {"Y": "A & B"}, library_view="physical_qualified",
            manifest_path=PUBLISHED_MANIFEST)
        expected = sum(manifests[instance["cell"]]["width_um"] *
                       manifests[instance["cell"]]["height_um"]
                       for instance in netlist["instances"])
        self.assertAlmostEqual(
            mapping.area(netlist, manifest_path=PUBLISHED_MANIFEST), expected)

        with self.assertRaisesRegex(ValueError, "disagrees with dimensions"):
            mapping.area(
                {"instances": [{"cell": "INV_X1"}]},
                manifests={"INV_X1": {
                    "width_um": 1.0, "height_um": 1.0, "area_um2": 2.0,
                }})


if __name__ == "__main__":
    unittest.main()
