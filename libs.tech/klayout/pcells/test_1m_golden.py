#!/usr/bin/env python3
"""Native minimum-geometry 1M golden circuits through DRC, LVS, and PEX."""

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from xml.etree import ElementTree

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
DRC_DECK = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX_SCRIPT = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
NGSPICE = shutil.which("ngspice")
ASSEMBLER_SPEC = importlib.util.spec_from_file_location("assemble_postlayout", ASSEMBLER)
ASSEMBLER_MODULE = importlib.util.module_from_spec(ASSEMBLER_SPEC)
ASSEMBLER_SPEC.loader.exec_module(ASSEMBLER_MODULE)
ONE_METAL = "icps2023_5_1m"


def clean_environment():
    environment = os.environ.copy()
    environment["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return environment


def schematic(name):
    fit = name.endswith("_fit")
    design_name = name[:-4] if fit else name
    top_name = name
    width = 24 if fit else 13
    if design_name == "nmos_min":
        pins = ["D", "G", "S"]
        devices = ["MN D G S SUB nch L=6U W=13U"]
    elif design_name == "pmos_min":
        pins = ["D", "G", "S"]
        devices = ["MP D G S SUB pch L=6U W=13U"]
    elif design_name == "inverter_min":
        pins = ["A", "Y", "VDD", "VSS"]
        devices = [
            "MP Y A VDD SUB pch L=6U W=13U",
            "MN Y A VSS SUB nch L=6U W=13U",
        ]
    elif design_name == "nand2_min":
        pins = ["A", "B", "Y", "VDD_A", "VDD_B", "VSS"]
        devices = [
            "MPA Y A VDD_A SUB pch L=6U W=13U",
            "MPB Y B VDD_B SUB pch L=6U W=13U",
            "MNA NINT A VSS SUB nch L=6U W=13U",
            "MNB Y B NINT SUB nch L=6U W=13U",
        ]
    elif design_name == "bridge_tin_min":
        pins = ["START", "END"]
        devices = ["R1 START END 90"]
    elif design_name == "bridge_nplus_min":
        pins = ["START", "END"]
        devices = ["R1 START END 810"]
    elif design_name.startswith("ring_oscillator_"):
        stages = int(design_name.rsplit("_", 1)[1])
        vdd_pins = ["VDD%d" % stage for stage in range(stages)]
        vss_pins = ["VSS%d" % stage for stage in range(stages)]
        pins = ["OUT"] + vdd_pins + vss_pins
        outputs = ["OUT"] + ["N%d" % index for index in range(1, stages)]
        devices = []
        for stage in range(stages):
            input_net = outputs[(stage - 1) % stages]
            output_net = outputs[stage]
            devices.extend(
                [
                    "MP%d %s %s VDD%d SUB pch L=6U W=13U"
                    % (stage, output_net, input_net, stage),
                    "MN%d %s %s VSS%d SUB nch L=6U W=13U"
                    % (stage, output_net, input_net, stage),
                ]
            )
    else:
        raise AssertionError("no schematic for %s" % name)

    devices = [device.replace("W=13U", "W=%dU" % width) for device in devices]
    lines = ["* Native ICPS2023_5 reference", ".GLOBAL SUB"]
    lines.append(".SUBCKT %s %s" % (top_name, " ".join(pins)))
    lines.extend(devices)
    lines.extend(
        [
            ".ENDS %s" % top_name,
            "XTOP %s %s" % (" ".join(pins), top_name),
            ".END",
            "",
        ]
    )
    return "\n".join(lines)


def drc_marker_categories(path):
    items = ElementTree.parse(path).getroot().find("items")
    if items is None:
        raise AssertionError("DRC report has no items element: %s" % path)
    return [item.findtext("category", default="") for item in items.findall("item")]

def rewrite_pex(netlist, update_mos=None, resistor=None):
    lines = []
    updated = False
    injected = False
    for line in netlist.splitlines():
        if line.startswith("* MOS_MAP ") and update_mos and not updated:
            record = json.loads(line[len("* MOS_MAP "):])
            if record["polarity"].upper() == "NMOS":
                update_mos(record)
                line = "* MOS_MAP " + json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                )
                updated = True
        if line.casefold().startswith(".ends") and resistor and not injected:
            lines.append("%s %s %s %s" % resistor)
            injected = True
        lines.append(line)
    if update_mos is not None and not updated:
        raise AssertionError("PEX netlist has no NMOS MOS_MAP record")
    if resistor is not None and not injected:
        raise AssertionError("PEX netlist has no .ENDS line")
    return "\n".join(lines) + "\n"


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for golden circuits")
class NativeOneMetalGoldenTests(unittest.TestCase):
    def generate(self, output_dir, fit=False):
        command = [
            KLAYOUT,
            "-b",
            "-r",
            str(GENERATOR),
            "-rd",
            "output_dir=" + str(output_dir),
            "-rd",
            "pcell_macro=" + str(PCELL_MACRO),
        ]
        if fit:
            command.extend(["-rd", "mos_width=24", "-rd", "design_suffix=fit"])
        result = subprocess.run(
            command, text=True, capture_output=True, env=clean_environment()
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        names = [
            "nmos_min",
            "pmos_min",
            "inverter_min",
            "nand2_min",
            "bridge_tin_min",
            "bridge_nplus_min",
            "ring_oscillator_3",
            "ring_oscillator_5",
        ]
        if fit:
            names = [name + "_fit" for name in names]
        for name in names:
            self.assertTrue((output_dir / (name + ".gds")).is_file(), result.stdout)
        return names

    def assert_one_metal_geometry(self, layout_path, top_name):
        layout = pya.Layout()
        layout.read(str(layout_path))
        top = layout.cell(top_name)
        self.assertIsNotNone(top, "missing top cell %s" % top_name)
        for spec in ((9, 0), (10, 0)):
            layer = layout.find_layer(pya.LayerInfo(*spec))
            if layer is None:
                continue
            iterator = top.begin_shapes_rec(layer)
            self.assertTrue(
                iterator.at_end(),
                "%s contains unqualified upper layer %s" % (top_name, spec),
            )

    def run_drc(self, layout_path, top_name, tempdir):
        report = Path(tempdir) / (top_name + ".drc.lyrdb")
        result = subprocess.run(
            [
                KLAYOUT,
                "-b",
                "-r",
                str(DRC_DECK),
                "-rd",
                "input=" + str(layout_path),
                "-rd",
                "top=" + top_name,
                "-rd",
                "report=" + str(report),
                "-rd",
                "process_profile=" + ONE_METAL,
            ],
            text=True,
            capture_output=True,
            env=clean_environment(),
        )
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, details)
        self.assertIn("profile=" + ONE_METAL, result.stdout)
        self.assertTrue(report.is_file(), details)
        markers = drc_marker_categories(report)
        self.assertEqual(markers, [], details + "\n" + "\n".join(markers))

    def run_lvs(self, layout_path, top_name, tempdir):
        reference = Path(tempdir) / (top_name + ".cir")
        reference.write_text(schematic(top_name))
        report = Path(tempdir) / (top_name + ".lvs.lyrdb")
        extracted = Path(tempdir) / (top_name + ".extracted.cir")
        result = subprocess.run(
            [
                KLAYOUT,
                "-b",
                "-r",
                str(LVS_DECK),
                "-rd",
                "input=" + str(layout_path),
                "-rd",
                "top=" + top_name,
                "-rd",
                "schematic=" + str(reference),
                "-rd",
                "report=" + str(report),
                "-rd",
                "output=" + str(extracted),
                "-rd",
                "process_profile=" + ONE_METAL,
            ],
            text=True,
            capture_output=True,
            env=clean_environment(),
        )
        details = result.stdout + result.stderr
        if extracted.is_file():
            details += "\n" + extracted.read_text()
        if report.is_file():
            details += "\n" + report.read_text()
        self.assertEqual(result.returncode, 0, details)
        self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)
        self.assertTrue(extracted.is_file(), details)

    def run_pex(self, layout_path, top_name, tempdir):
        output = Path(tempdir) / (top_name + ".pex.spice")
        result = subprocess.run(
            [
                sys.executable,
                str(PEX_SCRIPT),
                "--input",
                str(layout_path),
                "--top",
                top_name,
                "--output",
                str(output),
                "--process-profile",
                ONE_METAL,
            ],
            text=True,
            capture_output=True,
            env=clean_environment(),
        )
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, details)
        self.assertIn("profile=" + ONE_METAL, result.stdout)
        netlist = output.read_text()
        self.assertIn(".SUBCKT %s_pex" % top_name, netlist)
        port_maps = [
            json.loads(line.split("PORT_MAP ", 1)[1])
            for line in netlist.splitlines()
            if "PORT_MAP " in line
        ]
        self.assertTrue(port_maps, details + "\n" + netlist)
        return port_maps, netlist

    def run_assembly(self, lvs, pex, reference, top_name, output,
                     expected_error=None):
        command = [
            sys.executable,
            str(ASSEMBLER),
            "--lvs",
            str(lvs),
            "--pex",
            str(pex),
            "--schematic",
            str(reference),
            "--top",
            top_name,
            "--output",
            str(output),
            "--require-multiple-terminals",
        ]
        result = subprocess.run(
            command, text=True, capture_output=True, env=clean_environment()
        )
        details = result.stdout + result.stderr
        if expected_error is None:
            self.assertEqual(result.returncode, 0, details)
        else:
            self.assertNotEqual(result.returncode, 0, details)
            self.assertIn(expected_error, details)
        return details

    def pre_layout_subcircuit(self, top_name, reference, lvs_path):
        pins = ASSEMBLER_MODULE.read_reference(reference, top_name, "SUB")
        lvs = ASSEMBLER_MODULE.read_lvs(lvs_path, top_name, pins)
        pin_by_node = {
            lvs["pin_nodes"][pin.casefold()]: pin for pin in pins
        }
        node_names = {}
        for device in lvs["devices"]:
            for node in device["nets"][:3]:
                node_names[node] = pin_by_node.get(
                    node, "LVSNET_" + re.sub(r"[^A-Za-z0-9_]", "_", node)
                )
        lines = [
            ".GLOBAL SUB",
            ".SUBCKT %s %s" % (top_name, " ".join(pins)),
        ]
        for index, device in enumerate(lvs["devices"], 1):
            nets = [node_names[node] for node in device["nets"][:3]]
            model = "nch" if device["model"] == "NMOS" else "pch"
            lines.append(
                "MBASE%04d %s %s %s SUB %s %s"
                % (
                    index,
                    nets[0],
                    nets[1],
                    nets[2],
                    model,
                    " ".join(device["parameters"]),
                )
            )
        lines.append(".ENDS %s" % top_name)
        return pins, "\n".join(lines)

    def write_transient_deck(self, path, top_name, pins, pre_subcircuit,
                             assembled, post_layout):
        lines = ["* %s %s-layout transient" % (
            top_name, "post" if post_layout else "pre"
        )]
        if post_layout:
            lines.append('.include "%s"' % assembled)
        else:
            lines.extend(
                [
                    '.lib "%s" b4soi' % MODEL_LIBRARY,
                    pre_subcircuit,
                ]
            )
        lines.extend(
            [
                ".GLOBAL SUB",
                ".options temp=25 tnom=25 method=trap reltol=1e-3 itl4=500",
                "VDD VDD 0 3",
                "VSUB SUB 0 0",
            ]
        )
        if top_name.startswith("inverter_"):
            lines.append("VIN A 0 PULSE(0 3 2n 2n 2n 6n 20n)")
            duration = "20n"
            sample_lines = [
                ".measure tran vout_0 FIND v(Y) AT=1n",
                ".measure tran vout_1 FIND v(Y) AT=6n",
                ".measure tran vout_0_again FIND v(Y) AT=15n",
                ".measure tran isupply_peak MAX par('-i(VDD)') FROM=1n TO=15n",
                ".measure tran tp_hl TRIG v(A) VAL=1.5 RISE=1 TARG v(Y) VAL=1.5 FALL=1",
                ".measure tran tp_lh TRIG v(A) VAL=1.5 FALL=1 TARG v(Y) VAL=1.5 RISE=1",
            ]
        else:
            lines.extend(
                [
                    "VA A 0 PULSE(0 3 2n 2n 2n 12n 40n)",
                    "VB B 0 PULSE(0 3 8n 2n 2n 20n 50n)",
                ]
            )
            duration = "40n"
            sample_lines = [
                ".measure tran vout_00_a FIND v(Y) AT=1n",
                ".measure tran vout_10 FIND v(Y) AT=6n",
                ".measure tran vout_11 FIND v(Y) AT=13n",
                ".measure tran vout_01 FIND v(Y) AT=27n",
                ".measure tran vout_00_b FIND v(Y) AT=35n",
                ".measure tran vin_a_01 FIND v(A) AT=27n",
                ".measure tran vin_b_01 FIND v(B) AT=27n",
                ".measure tran isupply_peak MAX par('-i(VDD)') FROM=1n TO=35n",
                ".measure tran tp_hl TRIG v(B) VAL=1.5 RISE=1 TARG v(Y) VAL=1.5 FALL=1",
                ".measure tran tp_lh TRIG v(A) VAL=1.5 FALL=1 TARG v(Y) VAL=1.5 RISE=1",
            ]
        drive = {
            pin.casefold(): (
                "0" if pin.casefold() == "vss"
                else "VDD" if pin.casefold().startswith("vdd")
                else pin
            )
            for pin in pins
        }
        instance_nodes = [drive[pin.casefold()] for pin in pins]
        if post_layout:
            instance_nodes.append("SUB")
        instance = "XPOST" if post_layout else "XPRE"
        lines.extend(
            [
                "%s %s %s" % (instance, " ".join(instance_nodes), top_name),
                "CLOAD Y 0 5f",
                ".tran 10p %s" % duration,
            ]
        )
        lines.extend(sample_lines)
        lines.append(".end")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_transient(self, deck):
        result = subprocess.run(
            [NGSPICE, "-b", str(deck)],
            text=True,
            capture_output=True,
            cwd=deck.parent,
            env=clean_environment(),
        )
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, details)
        measurements = {
            name.casefold(): float(value)
            for name, value in re.findall(
                r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*"
                r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
                details,
                re.MULTILINE,
            )
        }
        self.assertTrue(measurements, details)
        self.assertTrue(all(math.isfinite(value) for value in measurements.values()))
        return measurements

    @unittest.skipUnless(NGSPICE, "ngspice executable is required for transients")
    def test_fit_1m_spice_assembly_and_pre_post_transients(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout_dir = tempdir / "gds_fit"
            layout_dir.mkdir()
            self.generate(layout_dir, fit=True)

            for top_name in ("inverter_min_fit", "nand2_min_fit"):
                with self.subTest(top=top_name):
                    layout = layout_dir / (top_name + ".gds")
                    self.assert_one_metal_geometry(layout, top_name)
                    self.run_drc(layout, top_name, tempdir)
                    self.run_lvs(layout, top_name, tempdir)
                    lvs_path = tempdir / (top_name + ".extracted.cir")
                    reference = tempdir / (top_name + ".cir")
                    pex_maps, pex_text = self.run_pex(layout, top_name, tempdir)
                    pex_path = tempdir / (top_name + ".pex.spice")
                    assembled = tempdir / (top_name + ".assembled.spice")
                    summary = self.run_assembly(
                        lvs_path, pex_path, reference, top_name, assembled
                    )
                    stats = re.search(
                        r"MOS=(\d+) R=(\d+) C=(\d+) MOS_contacts=(\d+) "
                        r"shared_contacts=(\d+) multi_terminal_nets=(\d+)",
                        summary,
                    )
                    self.assertIsNotNone(stats, summary)
                    mos_count, resistor_count, cap_count, contact_count, _, multi_count = (
                        int(value) for value in stats.groups()
                    )
                    self.assertEqual(mos_count, 2 if top_name.startswith("inverter") else 4)
                    self.assertGreater(resistor_count, 0)
                    self.assertGreater(cap_count, 0)
                    self.assertEqual(contact_count, 2 * mos_count)
                    self.assertGreater(multi_count, 0)
                    self.assertIn(
                        ".SUBCKT %s %s E_SUB"
                        % (
                            top_name,
                            "A Y VDD VSS"
                            if top_name.startswith("inverter")
                            else "A B Y VDD_A VDD_B VSS",
                        ),
                        assembled.read_text(encoding="utf-8"),
                    )

                    pins, pre_subcircuit = self.pre_layout_subcircuit(
                        top_name, reference, lvs_path
                    )
                    pre_deck = tempdir / (top_name + ".pre.cir")
                    post_deck = tempdir / (top_name + ".post.cir")
                    self.write_transient_deck(
                        pre_deck, top_name, pins, pre_subcircuit, assembled, False
                    )
                    self.write_transient_deck(
                        post_deck, top_name, pins, pre_subcircuit, assembled, True
                    )
                    pre = self.run_transient(pre_deck)
                    post = self.run_transient(post_deck)

                    if top_name.startswith("inverter"):
                        high_states = ("vout_0", "vout_0_again")
                        low_states = ("vout_1",)
                        state_samples = high_states + low_states
                    else:
                        high_states = (
                            "vout_00_a",
                            "vout_10",
                            "vout_01",
                            "vout_00_b",
                        )
                        low_states = ("vout_11",)
                        state_samples = high_states + low_states
                        self.assertLess(pre["vin_a_01"], 0.1)
                        self.assertGreater(pre["vin_b_01"], 2.9)
                        self.assertLess(post["vin_a_01"], 0.1)
                        self.assertGreater(post["vin_b_01"], 2.9)
                    for result in (pre, post):
                        for name in high_states:
                            self.assertGreater(result[name], 2.0, name)
                        for name in low_states:
                            self.assertLess(result[name], 0.5, name)
                    for name in state_samples:
                        self.assertLess(
                            abs(post[name] - pre[name]),
                            0.3,
                            "%s pre=%g V post=%g V" % (name, pre[name], post[name]),
                        )
                    for name in ("tp_hl", "tp_lh"):
                        self.assertGreater(pre[name], 0.0, name)
                        self.assertGreater(post[name], 0.0, name)
                        self.assertLessEqual(
                            abs(post[name] - pre[name]) / pre[name],
                            0.2,
                            "%s pre=%g s post=%g s" % (name, pre[name], post[name]),
                        )
                    current_ratio = post["isupply_peak"] / pre["isupply_peak"]
                    self.assertGreater(current_ratio, 0.35)
                    self.assertLess(current_ratio, 1.65)
                    print(
                        "%s pre/post: tPHL=%.3f/%.3f ns tPLH=%.3f/%.3f ns "
                        "Ipeak=%.3f/%.3f mA"
                        % (
                            top_name,
                            pre["tp_hl"] * 1e9,
                            post["tp_hl"] * 1e9,
                            pre["tp_lh"] * 1e9,
                            post["tp_lh"] * 1e9,
                            pre["isupply_peak"] * 1e3,
                            post["isupply_peak"] * 1e3,
                        )
                    )

                    if top_name == "nand2_min_fit":
                        a_port = next(
                            record["physical"]
                            for record in pex_maps
                            if record["logical"] == "A" and record["layer"] == "ML1"
                        )
                        vss_port = next(
                            record["physical"]
                            for record in pex_maps
                            if record["logical"] == "VSS" and record["layer"] == "ML1"
                        )
                        corruptions = [
                            (
                                "open",
                                rewrite_pex(
                                    pex_text,
                                    lambda record: record.update(drain="OPEN_DRAIN"),
                                ),
                                "PEX open:",
                            ),
                            (
                                "duplicate_contact",
                                rewrite_pex(
                                    pex_text,
                                    lambda record: record.update(
                                        source_contact=record["drain_contact"]
                                    ),
                                ),
                                "MOS source/drain claim the same contact",
                            ),
                            (
                                "short",
                                rewrite_pex(
                                    pex_text,
                                    resistor=("RSHORT", a_port, vss_port, "1"),
                                ),
                                "PEX short:",
                            ),
                            (
                                "floating",
                                rewrite_pex(
                                    pex_text,
                                    resistor=("RFLOAT", "FLOAT_A", "FLOAT_B", "1"),
                                ),
                                "floating PEX components",
                            ),
                        ]
                        for label, invalid_text, expected_error in corruptions:
                            with self.subTest(structure=label):
                                invalid_pex = tempdir / (
                                    top_name + "." + label + ".pex.spice"
                                )
                                invalid_pex.write_text(invalid_text, encoding="utf-8")
                                self.run_assembly(
                                    lvs_path,
                                    invalid_pex,
                                    reference,
                                    top_name,
                                    tempdir / (top_name + "." + label + ".spice"),
                                    expected_error,
                                )

    def test_native_1m_golden_circuits_pass_drc_lvs_and_pex(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout_dir = tempdir / "gds"
            layout_dir.mkdir()
            names = self.generate(layout_dir)
            for top_name in names:
                with self.subTest(top=top_name):
                    layout_path = layout_dir / (top_name + ".gds")
                    self.assert_one_metal_geometry(layout_path, top_name)
                    self.run_drc(layout_path, top_name, tempdir)
                    self.run_lvs(layout_path, top_name, tempdir)
                    port_maps, netlist = self.run_pex(layout_path, top_name, tempdir)
                    logical_names = {record["logical"] for record in port_maps}
                    if top_name == "nmos_min" or top_name == "pmos_min":
                        self.assertTrue({"D", "G", "S"}.issubset(logical_names))
                    elif top_name == "inverter_min":
                        self.assertTrue({"A", "Y", "VDD", "VSS"}.issubset(logical_names))
                    elif top_name == "nand2_min":
                        self.assertTrue(
                            {"A", "B", "Y", "VDD_A", "VDD_B", "VSS"}.issubset(
                                logical_names
                            )
                        )
                        for supply in ("VDD_A", "VDD_B"):
                            self.assertEqual(
                                sum(record["logical"] == supply for record in port_maps),
                                1,
                                netlist,
                            )
                    elif top_name.startswith("ring_oscillator_"):
                        stages = int(top_name.rsplit("_", 1)[1])
                        supply_pins = {
                            "VDD%d" % stage for stage in range(stages)
                        } | {"VSS%d" % stage for stage in range(stages)}
                        self.assertTrue(
                            ({"OUT"} | supply_pins).issubset(logical_names)
                        )
                        for supply in supply_pins:
                            self.assertEqual(
                                sum(record["logical"] == supply for record in port_maps),
                                1,
                                netlist,
                            )
                    else:
                        self.assertTrue({"START", "END"}.issubset(logical_names))


if __name__ == "__main__":
    unittest.main()
