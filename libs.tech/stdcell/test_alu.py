#!/usr/bin/env python3
"""Functional and physical-mapping checks for the scalar 4-bit ALU."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alu
import library
import logic
import mapping


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")
RTL_SOURCE = HERE / "alu4.v"

def _ports(a: int, b: int, s1: int, s0: int, cin: int) -> dict[str, int]:
    return {
        **{f"A{bit}": (a >> bit) & 1 for bit in range(4)},
        **{f"B{bit}": (b >> bit) & 1 for bit in range(4)},
        "S1": s1,
        "S0": s0,
        "CIN": cin,
    }


class AluTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.netlist = alu.build_alu(manifest_path=PUBLISHED_MANIFEST)
        cls.accepted = library.physical_specs(PUBLISHED_MANIFEST)
        cls.cell_specs = logic.cell_specs()

    def _simulate(self, inputs: dict[str, int],
                  netlist: dict | None = None) -> dict[str, int]:
        values = {"VDD": 1, "VSS": 0, **inputs}
        selected = netlist if netlist is not None else self.netlist
        for instance in selected["instances"]:
            spec = self.cell_specs[instance["cell"]]
            connections = instance["connections"]
            pin_values = {pin: values[connections[pin]] for pin in spec["inputs"]}
            result = logic.evaluate(spec, pin_values)
            for pin in spec["outputs"]:
                values[connections[pin]] = result[pin]
        return {port: values[port] for port in alu.OUTPUT_PORTS}

    def test_scalar_canonical_netlist_is_deterministic_and_physically_mappable(self) -> None:
        expected_ports = dict(alu.PORT_DIRECTIONS)
        self.assertEqual(self.netlist["ports"], expected_ports)
        self.assertTrue(alu.validate_alu_netlist(
            self.netlist, manifest_path=PUBLISHED_MANIFEST))
        self.assertEqual(self.netlist, alu.build_alu(manifest_path=PUBLISHED_MANIFEST))

        used_cells = {instance["cell"] for instance in self.netlist["instances"]}
        self.assertTrue(used_cells <= set(self.accepted), used_cells - set(self.accepted))
        self.assertTrue(used_cells)
        prohibited = {
            "XOR2_X1", "XNOR2_X1", "MUX2_TG6T_X1", "MUX2_STATIC_X1",
            "MUX4_TG_X1", "MUX4_STATIC_X1", "HA_X1", "FA_X1",
        }
        self.assertTrue(used_cells.isdisjoint(prohibited), used_cells & prohibited)
        self.assertEqual(
            len(self.netlist["instances"]),
            len({instance["name"] for instance in self.netlist["instances"]}),
        )
        self.assertEqual(len(self.netlist["nets"]), len(set(self.netlist["nets"])))

        mapped = mapping.map_yosys_netlist(
            self.netlist,
            library_view="physical_qualified",
            manifest_path=PUBLISHED_MANIFEST,
        )
        self.assertEqual(mapped, self.netlist)
        self.assertTrue(
            {instance["cell"] for instance in mapped["instances"]} <= set(self.accepted))
    def test_scalar_rtl_maps_to_accepted_cells_and_matches_exhaustive_vectors(self) -> None:
        if shutil.which("yosys") is None:
            self.skipTest("Yosys is required to synthesize the checked-in ALU RTL")
        with tempfile.TemporaryDirectory(prefix="icps_alu_yosys_") as temporary:
            rtl_netlist = mapping.synthesize_rtl(
                RTL_SOURCE,
                top="ALU4",
                library_view="physical_qualified",
                manifest_path=PUBLISHED_MANIFEST,
                workdir=Path(temporary) / "synthesis",
            )
        self.assertTrue(alu.validate_alu_netlist(
            rtl_netlist, manifest_path=PUBLISHED_MANIFEST))
        self.assertLessEqual(
            {instance["cell"] for instance in rtl_netlist["instances"]},
            set(self.accepted),
        )
        for operation, a, b, cin in product((0, 1, 2, 3), range(16), range(16), range(2)):
            with self.subTest(operation=operation, A=a, B=b, CIN=cin):
                actual = self._simulate(
                    _ports(a, b, operation >> 1, operation & 1, cin),
                    netlist=rtl_netlist,
                )
                expected = alu.reference_alu(
                    a, b, operation >> 1, operation & 1, cin)
                self.assertEqual(actual, expected)

    def test_exhaustive_logic_truth_tables_and_zero_carry(self) -> None:
        for a, b, operation, cin in product(range(16), range(16), range(2), range(2)):
            with self.subTest(A=a, B=b, operation=operation, CIN=cin):
                actual = self._simulate(_ports(a, b, 0, operation, cin))
                expected = alu.reference_alu(a, b, 0, operation, cin)
                self.assertEqual(actual, expected)
                self.assertEqual(actual["COUT"], 0)

    def test_exhaustive_four_bit_addition_and_subtraction_vectors(self) -> None:
        for operation, a, b, cin in product((2, 3), range(16), range(16), range(2)):
            with self.subTest(operation=operation, A=a, B=b, CIN=cin):
                actual = self._simulate(_ports(a, b, operation >> 1, operation & 1, cin))
                expected = alu.reference_alu(a, b, operation >> 1, operation & 1, cin)
                self.assertEqual(actual, expected)

    def test_exhaustive_single_bit_operation_and_carry_truth_table(self) -> None:
        for s1, s0, a, b, cin in product(range(2), range(2), range(2), range(2), range(2)):
            packed_a, packed_b = a, b
            actual = self._simulate(_ports(packed_a, packed_b, s1, s0, cin))
            expected = alu.reference_alu(packed_a, packed_b, s1, s0, cin)
            self.assertEqual(actual["Y0"], expected["Y0"])
            if not s1:
                self.assertEqual(actual["COUT"], 0)

    def test_reference_input_validation_and_subtraction_carry_semantics(self) -> None:
        self.assertEqual(alu.reference_alu(15, 1, 1, 0, 0),
                         {"Y0": 0, "Y1": 0, "Y2": 0, "Y3": 0, "COUT": 1})
        self.assertEqual(alu.reference_alu(0, 0, 1, 1, 0),
                         {"Y0": 0, "Y1": 0, "Y2": 0, "Y3": 0, "COUT": 1})
        self.assertEqual(alu.reference_alu(0, 0, 1, 1, 1),
                         {"Y0": 1, "Y1": 1, "Y2": 1, "Y3": 1, "COUT": 0})
        self.assertEqual(alu.evaluate_alu(_ports(9, 6, 1, 0, 1)),
                         alu.reference_alu(9, 6, 1, 0, 1))
        with self.assertRaises(ValueError):
            alu.reference_alu(16, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            alu.evaluate_alu({"A0": 1})

    def test_validator_rejects_unaccepted_cells(self) -> None:
        malformed = dict(self.netlist)
        malformed["instances"] = [
            *self.netlist["instances"],
            {"name": "U_UNACCEPTED", "cell": "XOR2_X1", "connections": {}},
        ]
        with self.assertRaisesRegex(ValueError, "XOR2_X1.*accepted physical specs"):
            alu.validate_alu_netlist(malformed, manifest_path=PUBLISHED_MANIFEST)


if __name__ == "__main__":
    unittest.main()
