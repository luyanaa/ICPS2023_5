#!/usr/bin/env python3
"""Contract tests for the explicit contact-resistor insertion in assembly.

AGENTS.md 4.2: the physical Al-diffusion contact resistance (p.8 measured
150 ohm N+ / 200 ohm P+ per opening) must be attached explicitly at
post-layout assembly as R_cnt = R_unit / N_cnt between the PEX node and the
MOS D/S terminal, never merged into the core MOS RDSW.  N_cnt is the
parallel CNT cut count that PEX reports per MOS_MAP terminal.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import assemble_postlayout as asm

REFERENCE = """
* inverter reference
.GLOBAL SUB
.SUBCKT INV A OUT
.ENDS
"""

LVS = """
* net 0 A
* net 1 OUT
.SUBCKT INV 0 1
* device instance MN r0 MOS 5.000000,5.000000 NMOS
MN 1 0 0 0 NMOS L=6u W=24u
* device instance MP r0 MOS 5.000000,5.000000 PMOS
MP 1 0 0 0 PMOS L=6u W=48u
.ENDS
"""


def pex_text(drain_cuts=None, source_cuts=None):
    mos_map = {
        "device": "NMOS@5.000000,5.000000",
        "polarity": "NMOS",
        "axis": "x",
        "drain_side": "positive",
        "drain": "n10",
        "drain_contact": "MOS_TIE_NAREA_0001",
        "drain_contact_cuts": drain_cuts,
        "gate": "n20",
        "gate_x_um": 5.0,
        "gate_y_um": 5.0,
        "source_side": "negative",
        "source": "n11",
        "source_contact": "MOS_TIE_NAREA_0002",
        "source_contact_cuts": source_cuts,
        "e": "SUB",
        "x_um": 5.0,
        "y_um": 5.0,
    }
    mos_map = {k: v for k, v in mos_map.items() if v is not None}
    pmos_map = dict(mos_map)
    pmos_map["device"] = "PMOS@5.000000,5.000000"
    pmos_map["polarity"] = "PMOS"
    lines = [
        "* PEX",
        "* PORT_MAP " + json.dumps({"physical": "PORT0001", "logical": "A",
                                    "layer": "ML1", "x_um": 1.0, "y_um": 1.0}),
        "* PORT_MAP " + json.dumps({"physical": "PORT0002", "logical": "OUT",
                                    "layer": "ML1", "x_um": 2.0, "y_um": 2.0}),
        "* MOS_MAP " + json.dumps(mos_map, sort_keys=True),
        "* MOS_MAP " + json.dumps(pmos_map, sort_keys=True),
        ".SUBCKT INV_pex PORT0001 PORT0002 n10 n11 n20 SUB",
        "R1 PORT0001 n20 10.0",
        "R2 n10 PORT0002 5.0",
        "R3 n11 PORT0001 5.0",
        ".ENDS",
    ]
    resistor_maps = [
        {"name": "R1", "a": "PORT0001", "b": "n20", "resistance_ohm": 10.0,
         "material": "ML1", "a_location_um": [1.0, 1.0],
         "b_location_um": [5.0, 5.0], "geometry_qualification": "fixture sheet edge"},
        {"name": "R2", "a": "n10", "b": "PORT0002", "resistance_ohm": 5.0,
         "material": "NAREA", "a_location_um": [4.0, 5.0],
         "b_location_um": [2.0, 2.0], "geometry_qualification": "fixture sheet edge"},
        {"name": "R3", "a": "n11", "b": "PORT0001", "resistance_ohm": 5.0,
         "material": "PAREA", "a_location_um": [6.0, 5.0],
         "b_location_um": [1.0, 1.0], "geometry_qualification": "fixture sheet edge"},
    ]
    lines[5:5] = [
        "* RESISTOR_MAP " + json.dumps(record, sort_keys=True)
        for record in resistor_maps
    ]
    return "\n".join(lines) + "\n"


TIN_REFERENCE = """
.GLOBAL SUB
.SUBCKT RTIN A B
.ENDS RTIN
"""

TIN_LVS = """
* net 0 A
* net 1 B
.SUBCKT RTIN 0 1
* ICPS2023_5_R_TIN {"instance":"R1","a":"0","b":"1","r_ohm":200.0,"x_um":5.0,"y_um":5.0}
R1 0 1 200
.ENDS
"""


def tin_pex_text():
    tin_map = {
        "device": "R_TIN@5.000000,5.000000",
        "x_um": 5.0,
        "y_um": 5.0,
        "endpoints": [
            {"node": "PORT_A", "x_um": 0.0, "y_um": 5.0},
            {"node": "PORT_B", "x_um": 10.0, "y_um": 5.0},
        ],
    }
    return "\n".join([
        "* PORT_MAP " + json.dumps({"physical": "PORT_A", "logical": "A",
                                    "layer": "ML1", "x_um": 0.0, "y_um": 5.0}),
        "* PORT_MAP " + json.dumps({"physical": "PORT_B", "logical": "B",
                                    "layer": "ML1", "x_um": 10.0, "y_um": 5.0}),
        "* R_TIN_MAP " + json.dumps(tin_map, sort_keys=True),
        ".SUBCKT RTIN_pex PORT_A PORT_B SUB",
        ".ENDS RTIN_pex",
        "",
    ])


class ContactResistorTest(unittest.TestCase):
    def assemble(self, pex, lvs=LVS, reference=REFERENCE, top="INV"):
        tempdir = tempfile.mkdtemp()
        base = Path(tempdir)
        lib = base / "models.lib"
        lib.write_text(".lib b4soi\n", encoding="utf-8")
        output = base / "out.spice"
        summary = asm.assemble(
            self._write(base, "lvs.spice", lvs),
            self._write(base, "pex.spice", pex),
            self._write(base, "ref.spice", reference),
            top, output, model_library=lib,
        )
        return output.read_text(encoding="utf-8"), summary

    @staticmethod
    def _write(base, name, text):
        path = base / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_explicit_cut_counts_scale_resistance(self):
        text, summary = self.assemble(pex_text(drain_cuts=2, source_cuts=4))
        # NMOS: R_unit 150 ohm -> drain 150/2, source 150/4.  Comment
        # provenance sits on its own '*' line (ngspice rejects trailing '*'
        # text on element lines as an unknown model parameter).
        self.assertIn("* NMOS@5.000000,5.000000 DRAIN contact, N_cnt=2, "
                      "R_unit=150 ohm", text)
        self.assertIn("RCNT0001 n10 M0001_DRAIN 75", text)
        self.assertIn("* NMOS@5.000000,5.000000 SOURCE contact, N_cnt=4, "
                      "R_unit=150 ohm", text)
        self.assertIn("RCNT0002 n11 M0001_SOURCE 37.5", text)
        # PMOS: R_unit 200 ohm with the same cut counts -> 100 / 50
        self.assertIn("RCNT0003 n10 M0002_DRAIN 100", text)
        self.assertIn("RCNT0004 n11 M0002_SOURCE 50", text)
        # MOS D/S terminals sit on private internal nodes; gate does not
        self.assertIn("M0001 M0001_DRAIN n20 M0001_SOURCE E_SUB nch ", text)
        self.assertIn("M0002 M0002_DRAIN n20 M0002_SOURCE E_SUB pch ", text)
        self.assertEqual(summary["contact_resistors"], 4)
        resistor_maps = [
            json.loads(line[len("* RESISTOR_MAP "):])
            for line in text.splitlines() if line.startswith("* RESISTOR_MAP ")
        ]
        rcnt = [record for record in resistor_maps if record["name"].startswith("RCNT")]
        self.assertEqual(
            [(record["parallel_cuts"], record["unit_contact_resistance_ohm"],
              record["ownership"]) for record in rcnt],
            [(2, 150.0, "assembled_mos_contact"),
             (4, 150.0, "assembled_mos_contact"),
             (2, 200.0, "assembled_mos_contact"),
             (4, 200.0, "assembled_mos_contact")],
        )
        mos_maps = [
            json.loads(line[len("* MOS_DEVICE_MAP "):])
            for line in text.splitlines() if line.startswith("* MOS_DEVICE_MAP ")
        ]
        self.assertEqual(len(mos_maps), 2)
        self.assertTrue(all(item["body_state"] == "floating_body" for item in mos_maps))
        self.assertTrue(all(item["e"] == "E_SUB" for item in mos_maps))
        # no element line may carry trailing comment text
        for line in text.splitlines():
            if line.startswith(("RCNT", "M00", "RPEX", "CPEX")):
                self.assertNotIn("*", line, line)

    def test_missing_cut_fields_default_to_one_opening(self):
        text, summary = self.assemble(pex_text())
        self.assertIn("N_cnt=1, R_unit=150 ohm", text)
        self.assertIn("N_cnt=1, R_unit=200 ohm", text)
        self.assertEqual(summary["contact_resistors"], 4)

    def test_invalid_cut_counts_are_rejected(self):
        for bad in (0, -1, 1.5, "2", True):
            with self.subTest(bad=bad):
                with self.assertRaises(asm.AssemblyError):
                    self.assemble(pex_text(drain_cuts=bad))

    def test_lvs_owned_tin_resistor_is_added_once_without_a_pex_parallel_body(self):
        text, _summary = self.assemble(
            tin_pex_text(), lvs=TIN_LVS, reference=TIN_REFERENCE, top="RTIN"
        )
        element_lines = [
            line for line in text.splitlines()
            if line.startswith(("RTIN", "RPEX"))
        ]
        self.assertEqual(len(element_lines), 1, text)
        self.assertEqual(element_lines[0], "RTIN0001 A B 200")
        maps = [
            json.loads(line[len("* RESISTOR_MAP "):])
            for line in text.splitlines() if line.startswith("* RESISTOR_MAP ")
        ]
        tin_map = next(record for record in maps if record["name"] == "RTIN0001")
        self.assertEqual((tin_map["a"], tin_map["b"], tin_map["material"]),
                         ("A", "B", "TIN"))
        node_maps = [
            json.loads(line[len("* ASSEMBLY_NODE_MAP "):])
            for line in text.splitlines()
            if line.startswith("* ASSEMBLY_NODE_MAP ")
        ]
        aliases = {record["pex"]: record["assembled"] for record in node_maps}
        self.assertEqual(aliases["PORT_A"], "A")
        self.assertEqual(aliases["PORT_B"], "B")
        self.assertEqual(aliases["SUB"], "E_SUB")
        self.assertFalse(any(line.startswith("* MOS_DEVICE_MAP ") for line in text.splitlines()))

    def test_untagged_lvs_resistor_is_rejected(self):
        lvs_without_tag = TIN_LVS.splitlines()
        lvs_without_tag = "\n".join(
            line for line in lvs_without_tag
            if not line.startswith("* ICPS2023_5_R_TIN ")
        )
        with self.assertRaisesRegex(asm.AssemblyError, "only tagged R_TIN"):
            self.assemble(
                tin_pex_text(), lvs=lvs_without_tag,
                reference=TIN_REFERENCE, top="RTIN"
            )


if __name__ == "__main__":
    unittest.main()
