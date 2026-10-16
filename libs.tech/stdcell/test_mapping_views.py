#!/usr/bin/env python3
"""Mapping-view eligibility and Boolean-preservation regression tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import logic
import mapping


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")

class MappingViewTests(unittest.TestCase):
    _channel_manifest_path = None

    @classmethod
    def _channel_manifest(cls):
        if cls._channel_manifest_path is None:
            import feasibility
            scratch = tempfile.TemporaryDirectory(prefix="icps_mapping_cells_")
            cls.addClassCleanup(scratch.cleanup)
            root = Path(scratch.name) / "cells"
            feasibility._generator().build_candidate_library(
                root, ["INV_X1", "NAND2_X1", "NOR2_X1",
                       "AOI21_X1", "MUX2_TG6T_X1", "XOR2_X1"],
                height_um=None, architecture="channel",
                pin_order_mode="reverse_signals")
            cls._channel_manifest_path = root / "doc" / "cell_manifest.json"
        return cls._channel_manifest_path

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
        basic = {"INV_X1", "NAND2_X1", "NOR2_X1"}
        accepted = {name: spec for name, spec in
                    self._library().physical_specs(PUBLISHED_MANIFEST).items() if name in basic}
        netlist = mapping.map_boolean_design(
            "physical_fallback",
            {"A": "input", "B": "input", "S": "input",
             "MUX_Y": "output", "XOR_Y": "output", "XNOR_Y": "output"},
            {"MUX_Y": "(S & B) | (!S & A)",
             "XOR_Y": "A ^ B",
             "XNOR_Y": "!(A ^ B)"},
            library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST, specs=accepted)

        emitted = {instance["cell"] for instance in netlist["instances"]}
        self.assertTrue(emitted <= set(accepted), emitted - set(accepted))
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
        accepted = {name: spec for name, spec in
                    self._library().physical_specs(PUBLISHED_MANIFEST).items()
                    if name in {"INV_X1", "NAND2_X1", "NOR2_X1"}}
        netlist = mapping.map_yosys_netlist(
            raw, library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST,
            specs=accepted)
        emitted = {instance["cell"] for instance in netlist["instances"]}
        self.assertTrue(emitted <= set(accepted), emitted - set(accepted))
        for a in (0, 1):
            for b in (0, 1):
                for select in (0, 1):
                    with self.subTest(A=a, B=b, S=select):
                        values = self._evaluate(netlist, {"A": a, "B": b, "S": select})
                        expected = b if select else a
                        self.assertEqual(values["Y"], expected)
                        self.assertEqual(values["YN"], 1 - expected)

    def test_unaccepted_canonical_cell_is_rejected_in_physical_view(self):
        accepted = self._library().physical_specs(PUBLISHED_MANIFEST)
        raw = {
            "name": "unaccepted_xor",
            "ports": {"A": "input", "B": "input", "Y": "output"},
            "instances": [{
                "name": "U_XOR", "cell": "XOR2_X1",
                "connections": {"A": "A", "B": "B", "Y": "Y"},
            }],
        }
        with self.assertRaises(ValueError):
            mapping.map_yosys_netlist(
                raw, library_view="physical_qualified", manifest_path=PUBLISHED_MANIFEST,
                specs={"INV_X1": accepted["INV_X1"]})

    def test_storage_is_rejected_when_excluded_from_selected_physical_specs(self):
        accepted = self._library().physical_specs(PUBLISHED_MANIFEST)
        for cell, connections in (
                ("$_DFF_P_", {"D": "D", "C": "CLK", "Q": "Q"}),
                ("$_DLATCH_P_", {"D": "D", "E": "EN", "Q": "Q"})):
            raw = {
                "name": "storage",
                "ports": {"D": "input", "CLK": "input", "EN": "input", "Q": "output"},
                "instances": [{"name": "U_STORAGE", "cell": cell,
                               "connections": connections}],
            }
            with self.subTest(cell=cell):
                with self.assertRaises(ValueError):
                    mapping.map_yosys_netlist(
                        raw, library_view="physical_qualified",
                        manifest_path=PUBLISHED_MANIFEST,
                        specs={"INV_X1": accepted["INV_X1"]})


    def test_empty_explicit_specs_do_not_enable_unlisted_boolean_cells(self):
        with self.assertRaises(ValueError):
            mapping.map_boolean_design(
                "empty_specs", {"A": "input", "Y": "output"}, {"Y": "A"},
                specs={})
        with self.assertRaises(ValueError):
            mapping.map_boolean_design(
                "empty_constant_specs", {"Y": "output"}, {"Y": "1"}, specs={})
        self._library().physical_specs(PUBLISHED_MANIFEST)
        with self.assertRaises(ValueError):
            mapping.map_boolean_design(
                "empty_physical_specs", {"A": "input", "Y": "output"},
                {"Y": "A"}, library_view="physical_qualified",
                manifest_path=PUBLISHED_MANIFEST, specs={})

    def test_yosys_names_remain_router_safe_without_late_source_collision(self):
        import core_router
        source = {
            "name": "escaped_names",
            "ports": {"A": "input", "Y": "output",
                      "VDD": "inout", "VSS": "inout"},
            "instances": [
                {"name": "$abc$1$auto$parse.cc:397", "cell": "INV_X1",
                 "connections": {"A": "A", "Y": "N0",
                                 "VDD": "VDD", "VSS": "VSS"}},
                {"name": "u_map0", "cell": "INV_X1",
                 "connections": {"A": "N0", "Y": "Y",
                                 "VDD": "VDD", "VSS": "VSS"}},
            ],
        }
        mapped = mapping.map_yosys_netlist(
            source, library_view="physical_qualified",
            manifest_path=PUBLISHED_MANIFEST)
        names = [core_router._valid_identifier(inst["name"], "instance")
                 for inst in mapped["instances"]]
        self.assertEqual(len({name.casefold() for name in names}), len(names))
        for a in (0, 1):
            self.assertEqual(self._evaluate(mapped, {"A": a})["Y"], a)

    def test_abc_area_mapping_uses_compound_gate_below_primitive_area(self):
        manifest = self._channel_manifest()
        library = self._library()
        accepted = library.physical_specs(manifest)
        manifests = library.physical_manifests(manifest)
        source = (
            "module area_aoi(input A, input B, input C, output Y); "
            "assign Y = !((A & B) | C); endmodule")
        with tempfile.TemporaryDirectory(prefix="icps_area_aoi_") as directory:
            mapped = mapping.synthesize_rtl(
                source, top="area_aoi", library_view="physical_qualified",
                manifest_path=manifest,
                workdir=directory, area_only_abc=True)
        primitive = mapping.map_boolean_design(
            "area_aoi_primitive",
            {"A": "input", "B": "input", "C": "input", "Y": "output"},
            {"Y": "!((A & B) | C)"},
            library_view="physical_qualified",
            manifest_path=manifest)
        cells = {instance["cell"] for instance in mapped["instances"]}
        self.assertTrue(cells <= set(accepted), cells - set(accepted))
        self.assertIn("AOI21_X1", cells)
        self.assertLess(
            mapping.area(mapped, manifests=manifests),
            mapping.area(primitive, manifests=manifests))
        for a in (0, 1):
            for b in (0, 1):
                for c in (0, 1):
                    values = self._evaluate(mapped, {"A": a, "B": b, "C": c})
                    self.assertEqual(values["Y"], 1 - ((a & b) | c))

    def test_abc_area_mapping_selects_accepted_mux_and_xor_costs(self):
        manifest = self._channel_manifest()
        library = self._library()
        accepted = library.physical_specs(manifest)
        manifests = library.physical_manifests(manifest)
        source = (
            "module area_mux_xor(input A, input B, input S, "
            "output MUX_Y, output XOR_Y); "
            "assign MUX_Y = S ? A : B; assign XOR_Y = A ^ B; endmodule")
        with tempfile.TemporaryDirectory(prefix="icps_area_mux_xor_") as directory:
            mapped = mapping.synthesize_rtl(
                source, top="area_mux_xor",
                library_view="physical_qualified",
                manifest_path=manifest,
                workdir=directory, area_only_abc=True)
        cells = {instance["cell"] for instance in mapped["instances"]}
        self.assertTrue(cells <= set(accepted), cells - set(accepted))
        self.assertIn("MUX2_TG6T_X1", cells)
        self.assertIn("XOR2_X1", cells)
        area_proxy = mapping.area(mapped, manifests=manifests)
        self.assertLessEqual(
            area_proxy,
            manifests["MUX2_TG6T_X1"]["area_um2"]
            + manifests["XOR2_X1"]["area_um2"])
        for a in (0, 1):
            for b in (0, 1):
                for select in (0, 1):
                    values = self._evaluate(
                        mapped, {"A": a, "B": b, "S": select})
                    self.assertEqual(values["MUX_Y"], a if select else b)
                    self.assertEqual(values["XOR_Y"], a ^ b)

    def test_negative_gate_area_budget_preserves_polarity(self):
        library = self._library()
        manifests = library.physical_manifests(PUBLISHED_MANIFEST)
        accepted = library.physical_specs(PUBLISHED_MANIFEST)
        for expression, gate, expected in (
                ("!(A & B)", "NAND2_X1", lambda a, b: 1 - (a & b)),
                ("!(A | B)", "NOR2_X1", lambda a, b: 1 - (a | b))):
            with self.subTest(expression=expression):
                netlist = mapping.map_boolean_design(
                    "negative_gate", {"A": "input", "B": "input", "Y": "output"},
                    {"Y": expression}, library_view="physical_qualified",
                    manifest_path=PUBLISHED_MANIFEST, specs=accepted)
                self.assertLessEqual(
                    mapping.area(netlist, manifests=manifests),
                    manifests[gate]["area_um2"])
                for a in (0, 1):
                    for b in (0, 1):
                        values = self._evaluate(netlist, {"A": a, "B": b})
                        self.assertEqual(values["Y"], expected(a, b))

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
