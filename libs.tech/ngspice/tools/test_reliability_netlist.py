#!/usr/bin/env python3
"""Analytic and unsupported-input boundary tests for reliability_netlist."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import reliability_netlist as netlist


class ReliabilityNetlistTest(unittest.TestCase):
    def read_text(self, text: str, top: str | None = None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "network.spice"
        path.write_text(text, encoding="utf-8")
        return netlist.read_network(path, top)

    def test_preserves_rcm_ports_maps_and_engineering_suffixes(self):
        result = self.read_text(
            """* source model provenance: test model remains external
* selfheat: model ownership not qualified
* access: measured RDSW is not an interconnect substitute
* PORT_MAP {"logical":"VDD","physical":"PORT01","layer":"ML1","x_um":1,"y_um":2}
* RESISTOR_MAP {"name":"R_TIN","a":"n0","b":"n1","resistance_ohm":23.5,"material":"TIN","a_location_um":[1,2]}
* ASSEMBLY_NODE_MAP {"pex":"PORT01","assembled":"VDD"}
* MOS_DEVICE_MAP {"name":"M0","layout_device":"NMOS@1,2","body_state":"floating_body","e":"SUB"}
.GLOBAL SUB
.SUBCKT Core VDD VSS IN
.param RB=10k
R0 VDD n0 1M
R1 n0 n1 {RB * 2}
R_TIN n1 VSS RTIN_MODEL
C0 n1 0 3p
C1 VDD SUB 0
M0 n1 IN VSS SUB nch L=6u W=24u
M1 n1 IN VSS SUB BODY pch L=6u W=48u
.ENDS Core
"""
        )
        self.assertEqual(result["name"], "Core")
        self.assertEqual(result["ports"], ["VDD", "VSS", "IN"])
        self.assertIn("0", result["nodes"])
        self.assertEqual([item["resistance_ohm"] for item in result["resistors"]],
                         [1e-3, 20_000.0, 23.5])
        self.assertEqual(result["resistors"][0]["material"], "UNKNOWN")
        self.assertEqual(result["resistors"][2]["material"], "TIN")
        self.assertEqual([item["capacitance_f"] for item in result["capacitors"]],
                         [3e-12, 0.0])
        self.assertEqual(len(result["mos"]), 2)
        self.assertIsNone(result["mos"][0]["p"])
        self.assertEqual(result["mos"][1]["p"], "BODY")
        self.assertEqual(result["mos"][0]["body_state"], "floating_body")
        self.assertEqual(result["mos"][0]["layout_device"], "NMOS@1,2")
        self.assertEqual(result["port_maps"][0]["physical"], "PORT01")
        self.assertEqual(result["assembly_node_maps"][0],
                         {"pex": "PORT01", "assembled": "VDD"})
        self.assertTrue(result["metadata"]["model_provenance"])
        self.assertTrue(result["metadata"]["selfheat_provenance"])
        self.assertTrue(result["metadata"]["access_provenance"])
        self.assertEqual(len(result["sha256"]), 64)

    def test_native_dollar_identifiers_survive_inline_comments(self):
        result = self.read_text(
            ".SUBCKT Core D$pad G S E\n"
            "R$1 D$pad D 2k $ ignored inline comment\n"
            "C$1 D E 5f ; ignored inline comment\n"
            "M$1 D G S E nch L=6u W=24u $ W=999u\n"
            ".ENDS Core\n"
        )
        resistor = result["resistors"][0]
        capacitor = result["capacitors"][0]
        mos = result["mos"][0]
        self.assertEqual(resistor["name"], "R$1")
        self.assertEqual(resistor["a"], "D$pad")
        self.assertEqual(resistor["resistance_ohm"], 2000.0)
        self.assertEqual(mos["name"], "M$1")
        self.assertEqual(mos["d"], resistor["b"])
        self.assertEqual(capacitor["a"], mos["d"])
        self.assertAlmostEqual(capacitor["capacitance_f"], 5e-15)

    def test_selects_subcircuit_case_insensitively_and_requires_selection_if_ambiguous(self):
        text = ".SUBCKT A a b\nR1 a b 1k\n.ENDS A\n.SUBCKT B c d\nR2 c d 2k\n.ENDS B\n"
        self.assertEqual(self.read_text(text, "a")["name"], "A")
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "top is required"):
            self.read_text(text)

    def test_unresolved_or_nonpositive_resistors_fail_loudly(self):
        for value in ("MISSING", "0", "-1k", "NaN"):
            with self.subTest(value=value):
                with self.assertRaises(netlist.ReliabilityNetlistError):
                    self.read_text(".SUBCKT X a b\nR1 a b %s\n.ENDS X\n" % value)

    def test_unsupported_devices_and_hierarchical_instances_are_rejected(self):
        for element, message in (("X1 a b child", "hierarchical instance"),
                                 ("D1 a b diode", "unsupported D element"),
                                 ("B1 a b V=1", "unsupported B element")):
            with self.subTest(element=element):
                with self.assertRaisesRegex(netlist.ReliabilityNetlistError, message):
                    self.read_text(".SUBCKT X a b\n%s\n.ENDS X\n" % element)

    def test_incomplete_and_mismatched_hierarchy_is_rejected(self):
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "unterminated"):
            self.read_text(".SUBCKT X a b\nR1 a b 1k\n")
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "does not match"):
            self.read_text(".SUBCKT X a b\n.ENDS Y\n")
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "nested .SUBCKT"):
            self.read_text(".SUBCKT X a b\n.SUBCKT Y c d\n.ENDS Y\n.ENDS X\n", "X")

    def test_resistor_map_can_resolve_a_native_model_style_value(self):
        mapped = {"name": "R_TIN", "a": "a", "b": "b", "resistance_ohm": 17.25,
                  "material": "TIN", "geometry_qualification": "native model extraction"}
        result = self.read_text(
            "* RESISTOR_MAP " + json.dumps(mapped) +
            "\n.SUBCKT X a b\nR_TIN a b RTIN_MODEL\n.ENDS X\n"
        )
        self.assertEqual(result["resistors"][0]["resistance_ohm"], 17.25)
        self.assertEqual(result["resistors"][0]["geometry"]["geometry_qualification"],
                         "native model extraction")

    def test_bad_map_json_and_unsupported_resistor_model_fail(self):
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "malformed RESISTOR_MAP JSON"):
            self.read_text("* RESISTOR_MAP {oops}\n.SUBCKT X a b\n.ENDS X\n")
        with self.assertRaisesRegex(netlist.ReliabilityNetlistError, "RESISTOR_MAP resistance_ohm"):
            self.read_text(".SUBCKT X a b\nR1 a b RTIN_MODEL\n.ENDS X\n")


if __name__ == "__main__":
    unittest.main()
