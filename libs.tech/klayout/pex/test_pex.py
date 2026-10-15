#!/usr/bin/env python3
"""Geometry-to-netlist checks for the qualified ICPS2023_5 PEX paths."""

import json
import shutil
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
PEX_SCRIPT = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
KLAYOUT = shutil.which("klayout")


def add_box(layout, cell, layer, x1, y1, x2, y2):
    dbu = layout.dbu
    box = pya.Box(*(int(round(v / dbu)) for v in (x1, y1, x2, y2)))
    cell.shapes(layout.layer(*layer)).insert(box)


def add_text(layout, cell, layer, name, x, y):
    point = pya.Point(int(round(x / layout.dbu)), int(round(y / layout.dbu)))
    cell.shapes(layout.layer(*layer)).insert(pya.Text(name, pya.Trans(point)))


def run_checked(command):
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(
            "command failed (%d): %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, " ".join(map(str, command)), result.stdout, result.stderr)
        )
    return result.stdout


def json_records(text, prefix):
    return [
        json.loads(line[len(prefix):])
        for line in text.splitlines()
        if line.startswith(prefix)
    ]


def port_mappings(netlist):
    return json_records(netlist, "* PORT_MAP ")


def physical_ports(netlist, logical):
    return [
        item["physical"]
        for item in port_mappings(netlist)
        if item["logical"] == logical
    ]


def capacitance_elements(netlist):
    elements = []
    for line in netlist.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[0].startswith("C"):
            elements.append((fields[1], fields[2], float(fields[3])))
    return elements


def capacitance_to_substrate_at_port(netlist, logical):
    ports = set(physical_ports(netlist, logical))
    return sum(
        value
        for a, b, value in capacitance_elements(netlist)
        if (a in ports and b == "SUB") or (b in ports and a == "SUB")
    )

def dc_ir_voltage_drop(netlist, cell_name, source, sink, current_a):
    ngspice = shutil.which("ngspice")
    if ngspice is None:
        raise RuntimeError("ngspice is required for the DC IR regression")
    physical = {
        item["logical"]: item["physical"]
        for item in port_mappings(netlist)
    }
    if source not in physical or sink not in physical:
        raise AssertionError("DC IR endpoints are missing from PORT_MAP")
    subckt_name = cell_name + "_pex"
    subckt = next(
        (
            line.split()[2:]
            for line in netlist.splitlines()
            if line.upper().startswith(".SUBCKT " + subckt_name.upper() + " ")
        ),
        None,
    )
    if subckt is None:
        raise AssertionError("PEX subcircuit is missing: " + subckt_name)
    instance_nodes = []
    for port in subckt:
        if port == physical[source] or port == "SUB":
            instance_nodes.append("0")
        elif port == physical[sink]:
            instance_nodes.append("DRN")
        else:
            raise AssertionError("unexpected DC IR subcircuit port: " + port)

    with tempfile.TemporaryDirectory() as tempdir:
        work = Path(tempdir)
        pex_path = work / "parasitics.spice"
        deck_path = work / "dc_ir.spice"
        pex_path.write_text(netlist, encoding="utf-8")
        deck_path.write_text(
            '.include "%s"\n'
            "Iload 0 DRN %.12g\n"
            "XPEX %s %s\n"
            ".control\n"
            "op\n"
            "print v(DRN)\n"
            "quit\n"
            ".endc\n"
            ".end\n"
            % (
                pex_path,
                current_a,
                " ".join(instance_nodes),
                subckt_name,
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [ngspice, "-b", str(deck_path)],
            text=True,
            capture_output=True,
        )
        output = result.stdout + "\n" + result.stderr
        if result.returncode:
            raise AssertionError("ngspice DC IR run failed:\n" + output)
        match = re.search(
            r"(?im)^\s*v\(drn\)\s*=\s*"
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)",
            output,
        )
        if match is None:
            raise AssertionError("ngspice DC IR voltage is missing:\n" + output)
        return float(match.group(1))

def mapped_port(netlist, name):
    matches = physical_ports(netlist, name)
    return matches[0] if len(matches) == 1 else name


def equivalent_resistance(netlist, source, sink):
    source = mapped_port(netlist, source)
    sink = mapped_port(netlist, sink)
    edges = []
    for line in netlist.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].upper().startswith("R"):
            continue
        try:
            resistance = float(fields[3])
        except ValueError:
            continue
        if resistance > 0:
            edges.append((fields[1], fields[2], resistance))

    adjacency = {}
    for a, b, resistance in edges:
        adjacency.setdefault(a, []).append((b, resistance))
        adjacency.setdefault(b, []).append((a, resistance))

    visited = {source}
    frontier = [source]
    while frontier:
        node = frontier.pop()
        for neighbor, _ in adjacency.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)
    if sink not in visited:
        return None

    unknown = sorted(visited - {source, sink})
    index = {node: i for i, node in enumerate(unknown)}
    matrix = [[0.0 for _ in unknown] for _ in unknown]
    rhs = [0.0 for _ in unknown]
    voltage = {source: 1.0, sink: 0.0}
    for a, b, resistance in edges:
        if a not in visited or b not in visited:
            continue
        conductance = 1.0 / resistance
        for node, other in ((a, b), (b, a)):
            if node not in index:
                continue
            i = index[node]
            matrix[i][i] += conductance
            if other in index:
                matrix[i][index[other]] -= conductance
            else:
                rhs[i] += conductance * voltage[other]

    for col in range(len(unknown)):
        pivot = max(range(col, len(unknown)), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-20:
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        rhs[col], rhs[pivot] = rhs[pivot], rhs[col]
        scale = matrix[col][col]
        matrix[col] = [value / scale for value in matrix[col]]
        rhs[col] /= scale
        for row in range(len(unknown)):
            if row == col:
                continue
            scale = matrix[row][col]
            if scale == 0:
                continue
            matrix[row] = [
                value - scale * pivot_value
                for value, pivot_value in zip(matrix[row], matrix[col])
            ]
            rhs[row] -= scale * rhs[col]

    voltage.update({node: rhs[i] for node, i in index.items()})
    current = sum(
        (voltage[source] - voltage[neighbor]) / resistance
        for neighbor, resistance in adjacency.get(source, [])
    )
    return 1.0 / current if current > 0 else None


class PexRegressionTests(unittest.TestCase):
    def make_layout(self, name):
        layout = pya.Layout()
        layout.dbu = 0.001
        return layout, layout.create_cell(name)

    def extract(self, layout, cell, tempdir, mode="design", capacitance=False):
        input_path = Path(tempdir) / (cell.name + ".gds")
        output_path = Path(tempdir) / (cell.name + ".spice")
        layout.write(str(input_path))
        command = [
            sys.executable,
            str(PEX_SCRIPT),
            "--input",
            str(input_path),
            "--top",
            cell.name,
            "--output",
            str(output_path),
            "--layer-mode",
            mode,
        ]
        if not capacitance:
            command.append("--no-capacitance")
        self.last_pex_log = run_checked(command)
        return output_path.read_text()

    def test_analytic_sheet_resistance_by_conductor(self):
        cases = [
            ("NPLUS", (3, 0), (19, 0), 1800.0),
            ("PPLUS", (3, 0), (18, 0), 12000.0),
            ("TIN", (5, 0), None, 200.0),
            ("ML1", (8, 0), None, 2.0),
        ]
        for name, conductor, implant, expected in cases:
            with self.subTest(conductor=name), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout(name)
                add_box(layout, cell, conductor, 0, 0, 100, 10)
                if implant:
                    add_box(layout, cell, implant, 0, 0, 100, 10)
                add_text(layout, cell, (13, 0), "SRC", 0, 5)
                add_text(layout, cell, (13, 0), "DRN", 100, 5)
                spice = self.extract(layout, cell, tempdir)
                measured = equivalent_resistance(spice, "SRC", "DRN")
                self.assertIsNotNone(measured)
                self.assertAlmostEqual(measured, expected, delta=expected * 0.01)


    def test_typed_contact_resistances_connect_only_the_classified_layers(self):
        cases = [
            ("NPLUS_CONTACT", (3, 0), (19, 0), 180.0, 150.0),
            ("PPLUS_CONTACT", (3, 0), (18, 0), 1200.0, 200.0),
            ("TIN_CONTACT", (5, 0), None, 20.0, 0.9),
        ]
        for name, bottom, implant, sheet_resistance, contact_resistance in cases:
            with self.subTest(conductor=name), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout(name)
                add_box(layout, cell, bottom, 0, 45, 100, 55)
                if implant:
                    add_box(layout, cell, implant, 0, 45, 100, 55)
                add_box(layout, cell, (8, 0), 47, 0, 53, 100)
                add_box(layout, cell, (7, 0), 47.5, 47.5, 52.5, 52.5)
                add_text(layout, cell, (13, 0), "SRC", 0, 50)
                add_text(layout, cell, (13, 0), "DRN", 50, 100)
                spice = self.extract(layout, cell, tempdir)
                measured = equivalent_resistance(spice, "SRC", "DRN")
                expected = 5 * sheet_resistance + contact_resistance + (50 / 6) * 0.20
                self.assertIsNotNone(measured, spice + "\n" + self.last_pex_log)
                self.assertAlmostEqual(measured, expected, delta=expected * 0.01)

    @unittest.skipUnless(shutil.which("ngspice"), "ngspice is required for DC IR")
    def test_tin_ml1_contact_dc_ir_drop(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("TIN_ML1_DC_IR")
            add_box(layout, cell, (5, 0), 0, 45, 100, 55)
            add_box(layout, cell, (8, 0), 47, 0, 53, 100)
            add_box(layout, cell, (7, 0), 47.5, 47.5, 52.5, 52.5)
            add_text(layout, cell, (13, 0), "SRC", 0, 50)
            add_text(layout, cell, (13, 0), "DRN", 50, 100)
            spice = self.extract(layout, cell, tempdir)

            expected_resistance = 5 * 20.0 + 0.9 + (50.0 / 6.0) * 0.20
            measured_resistance = equivalent_resistance(spice, "SRC", "DRN")
            self.assertAlmostEqual(
                measured_resistance,
                expected_resistance,
                delta=expected_resistance * 0.01,
            )
            current = 1e-3
            measured_drop = dc_ir_voltage_drop(
                spice, cell.name, "SRC", "DRN", current
            )
            expected_drop = expected_resistance * current
            self.assertAlmostEqual(
                measured_drop,
                expected_drop,
                delta=expected_drop * 0.01,
            )

    def test_duplicate_text_labels_export_distinct_physical_ports(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("DUPLICATE_LABELS")
            for x in (0, 100):
                add_box(layout, cell, (3, 0), x, 0, x + 10, 10)
                add_box(layout, cell, (19, 0), x, 0, x + 10, 10)
                add_text(layout, cell, (13, 0), "VDD", x + 5, 5)

            spice = self.extract(layout, cell, tempdir)
            header = next(
                line for line in spice.splitlines() if line.startswith(".SUBCKT ")
            ).split()
            physical_names = header[2:-1]
            mappings = port_mappings(spice)
            self.assertEqual(len(physical_names), 2, spice)
            self.assertEqual(len(set(physical_names)), 2, spice)
            self.assertEqual(len(mappings), 2, spice)
            self.assertEqual({item["logical"] for item in mappings}, {"VDD"})
            self.assertEqual(
                {item["physical"] for item in mappings}, set(physical_names)
            )
            self.assertEqual({item["layer"] for item in mappings}, {"NAREA"})
            self.assertEqual({item["x_um"] for item in mappings}, {5.0, 105.0})
            self.assertNotIn("VDD#", spice)

            repeated = self.extract(layout, cell, tempdir)
            self.assertEqual(
                [line for line in spice.splitlines() if line.startswith("* PORT_MAP ")],
                [line for line in repeated.splitlines() if line.startswith("* PORT_MAP ")],
            )

    def test_physical_sub_label_is_separate_from_global_return(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("PHYSICAL_SUB")
            add_box(layout, cell, (8, 0), 0, 0, 100, 10)
            add_text(layout, cell, (13, 0), "SUB", 50, 5)

            spice = self.extract(layout, cell, tempdir)
            header = next(
                line for line in spice.splitlines() if line.startswith(".SUBCKT ")
            ).split()
            mappings = port_mappings(spice)
            self.assertEqual(len(mappings), 1, spice)
            self.assertEqual(mappings[0]["logical"], "SUB")
            self.assertNotEqual(mappings[0]["physical"], "SUB")
            self.assertEqual(header[2:], [mappings[0]["physical"], "SUB"])

    def test_unbounded_and_ambiguous_mos_access_are_rejected(self):
        for mode in ("unbounded", "ambiguous"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("MOS_ACCESS_" + mode.upper())
                add_box(layout, cell, (3, 0), 0, 0, 100, 30)
                add_box(layout, cell, (19, 0), 0, 0, 100, 30)
                add_box(layout, cell, (5, 0), 40, 0, 46, 30)
                cuts = [(10, 10)]
                if mode == "ambiguous":
                    cuts += [(10, 20), (80, 15)]
                for x, y in cuts:
                    add_box(layout, cell, (7, 0), x-2.5, y-2.5, x+2.5, y+2.5)
                    add_box(layout, cell, (8, 0), x-6.5, y-6.5, x+6.5, y+6.5)
                input_path = Path(tempdir) / "access.gds"
                output_path = Path(tempdir) / "access.spice"
                layout.write(str(input_path))
                result = subprocess.run(
                    [sys.executable, str(PEX_SCRIPT), "--input", str(input_path),
                     "--top", cell.name, "--output", str(output_path)],
                    text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                reports = json_records(result.stdout, "MOS_ACCESS_RESULT ")
                self.assertEqual(len(reports), 2, result.stdout)
                n_report = next(report for report in reports if report["layer"] == "NAREA")
                self.assertFalse(n_report["passed"])
                self.assertEqual(n_report[mode], 1)
                self.assertEqual(n_report["failures"][0]["reason"], mode)
                self.assertEqual({report["layer"] for report in reports},
                                 {"NAREA", "PAREA"})
                self.assertEqual(n_report["gates"], 1)
                self.assertEqual(n_report["bounded"], 0)
                self.assertEqual(n_report["unbounded"], int(mode == "unbounded"))
                self.assertEqual(n_report["ambiguous"], int(mode == "ambiguous"))
                self.assertEqual(len(n_report["failures"]), 1)
                self.assertEqual(len(n_report["failures"][0]["gate_bbox_um"]), 4)
                self.assertFalse(output_path.exists())


    def test_mos_gate_without_associated_active_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("MOS_ACCESS_ORPHANED_GATE")
            add_box(layout, cell, (3, 0), 0, 0, 100, 30)
            add_box(layout, cell, (5, 0), 40, 0, 46, 30)
            input_path = Path(tempdir) / "orphaned.gds"
            output_path = Path(tempdir) / "orphaned.spice"
            layout.write(str(input_path))
            result = subprocess.run(
                [sys.executable, str(PEX_SCRIPT), "--input", str(input_path),
                 "--top", cell.name, "--output", str(output_path),
                 "--no-capacitance"],
                text=True, capture_output=True)

            self.assertNotEqual(result.returncode, 0, result.stdout)
            reports = json_records(result.stdout, "MOS_ACCESS_RESULT ")
            self.assertEqual({report["layer"] for report in reports},
                             {"NAREA", "PAREA"})
            for report in reports:
                self.assertFalse(report["passed"], report)
                self.assertEqual(report["gates"], 1, report)
                self.assertEqual(report["orphaned"], 1, report)
                self.assertEqual(
                    report["failures"][0]["reason"],
                    "no_associated_active_component",
                    report,
                )
            self.assertFalse(output_path.exists())

    def test_mos_channel_is_not_replaced_with_a_pex_short(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("MOS_ACCESS_OWNER")
            add_box(layout, cell, (3, 0), 0, 0, 100, 10)
            add_box(layout, cell, (19, 0), 0, 0, 100, 10)
            add_box(layout, cell, (5, 0), 40, 0, 60, 10)
            for x, label in ((10, "DRAIN"), (90, "SOURCE")):
                add_box(layout, cell, (8, 0), x - 5, 0, x + 5, 10)
                add_box(layout, cell, (7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
                add_text(layout, cell, (13, 0), label, x, 5)

            spice = self.extract(layout, cell, tempdir)
            self.assertIsNone(equivalent_resistance(spice, "DRAIN", "SOURCE"))


    def test_mos_access_exclusion_preserves_long_n_and_p_diffusion_wires(self):
        cases = [
            ("NPLUS", (19, 0), 180.0),
            ("PPLUS", (18, 0), 1200.0),
        ]
        for name, implant, sheet_resistance in cases:
            for length in (100, 300):
                with self.subTest(conductor=name, length=length), tempfile.TemporaryDirectory() as tempdir:
                    layout, cell = self.make_layout("%s_%dUM" % (name, length))
                    wire_start = 13.1
                    wire_end = wire_start + length
                    add_box(layout, cell, (3, 0), -7, 0, wire_end, 10)
                    add_box(layout, cell, implant, -7, 0, wire_end, 10)
                    add_box(layout, cell, (5, 0), 0, 0, 6, 10)
                    for left, right in ((-7, -2), (8, 13)):
                        add_box(layout, cell, (8, 0), left, 2.5, right, 7.5)
                        add_box(layout, cell, (7, 0), left, 2.5, right, 7.5)
                    add_text(layout, cell, (13, 0), "MOS_PIN", 10.5, 5)
                    add_text(layout, cell, (13, 0), "WIRE_START", wire_start, 5)
                    add_text(layout, cell, (13, 0), "WIRE_END", wire_end, 5)

                    spice = self.extract(layout, cell, tempdir)
                    measured = equivalent_resistance(spice, "WIRE_START", "WIRE_END")
                    terminal_resistance = equivalent_resistance(spice, "MOS_PIN", "WIRE_END")
                    expected = sheet_resistance * length / 10
                    self.assertIsNotNone(measured, spice + "\n" + self.last_pex_log)
                    self.assertAlmostEqual(measured, expected, delta=expected * 0.01)
                    self.assertIsNotNone(terminal_resistance, spice + "\n" + self.last_pex_log)
                    self.assertAlmostEqual(
                        terminal_resistance, expected, delta=expected * 0.01
                    )
                    access_layer = "NAREA" if implant == (19, 0) else "PAREA"
                    access_reports = json_records(spice, "* MOS_ACCESS_RESULT ")
                    self.assertEqual(len(access_reports), 2, spice)
                    self.assertEqual(
                        json_records(self.last_pex_log, "MOS_ACCESS_RESULT "),
                        access_reports,
                    )
                    self.assertEqual(
                        {report["layer"] for report in access_reports},
                        {"NAREA", "PAREA"},
                    )
                    for report in access_reports:
                        self.assertTrue(report["passed"], report)
                        self.assertEqual(report["gates"], report["bounded"], report)
                        self.assertEqual(report["unbounded"], 0, report)
                        self.assertEqual(report["ambiguous"], 0, report)
                        self.assertEqual(report["failures"], [], report)
                    mos_maps = json_records(spice, "* MOS_MAP ")
                    self.assertEqual(len(mos_maps), 1, spice)
                    self.assertEqual(
                        sum(report["bounded"] for report in access_reports),
                        len(mos_maps),
                        spice,
                    )
                    mos_map = mos_maps[0]
                    self.assertEqual(
                        mos_map["polarity"],
                        "NMOS" if access_layer == "NAREA" else "PMOS",
                    )
                    self.assertEqual(mos_map["drain_side"], "positive")
                    self.assertEqual(mos_map["source_side"], "negative")
                    self.assertNotEqual(
                        mos_map["drain_contact"], mos_map["source_contact"]
                    )
                    self.assertNotEqual(mos_map["drain"], mos_map["source"])
                    self.assertEqual(
                        self.last_pex_log.count("area_removed=70.000000 um2"),
                        2,
                        self.last_pex_log,
                    )
                    self.assertIn(
                        "area_remaining=0.000000 um2 components_before=1 components_after=0",
                        self.last_pex_log,
                    )
                    self.assertIn(
                        "area_remaining=%.6f um2 components_before=1 components_after=1"
                        % (length * 10 + 1),
                        self.last_pex_log,
                    )
                    mapped_pin = [
                        item for item in port_mappings(spice)
                        if item["logical"] == "MOS_PIN"
                    ]
                    mapped_wire = [
                        item for item in port_mappings(spice)
                        if item["logical"] == "WIRE_START"
                    ]
                    self.assertEqual(len(mapped_pin), 1, spice)
                    self.assertEqual(mapped_pin[0]["layer"], "ML1")
                    self.assertEqual(mapped_pin[0]["x_um"], 10.5)
                    self.assertEqual(len(mapped_wire), 1, spice)
                    self.assertEqual(mapped_wire[0]["layer"], access_layer)
                    self.assertEqual(mapped_wire[0]["x_um"], wire_start)

    def test_lvs_owns_marked_resistor_body(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("RESISTOR_OWNER")
            add_box(layout, cell, (3, 0), 0, 0, 100, 10)
            add_box(layout, cell, (19, 0), 0, 0, 100, 10)
            add_box(layout, cell, (15, 0), 0, 0, 100, 10)
            for x, label in ((5, "LEFT"), (95, "RIGHT")):
                add_box(layout, cell, (8, 0), x - 2, 0, x + 2, 10)
                add_box(layout, cell, (7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
                add_text(layout, cell, (13, 0), label, x, 5)

            spice = self.extract(layout, cell, tempdir, capacitance=True)
            self.assertIsNone(equivalent_resistance(spice, "LEFT", "RIGHT"))
            self.assertFalse(
                any(line.startswith("C") for line in spice.splitlines()), spice
            )

    def test_diode_model_owns_iv_while_pex_owns_diffusion_access(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("DIODE_ACCESS_OWNER")
            add_box(layout, cell, (3, 0), 0, 0, 100, 10)
            add_box(layout, cell, (19, 0), 0, 0, 100, 10)
            add_box(layout, cell, (17, 0), 0, 0, 100, 10)
            add_text(layout, cell, (13, 0), "ANODE", 5, 5)
            add_text(layout, cell, (13, 0), "CATHODE", 95, 5)

            spice = self.extract(layout, cell, tempdir, capacitance=True)
            measured = equivalent_resistance(spice, "ANODE", "CATHODE")
            self.assertAlmostEqual(measured, 1620.0, delta=16.2)
            self.assertFalse(
                any(line.startswith("C") for line in spice.splitlines()), spice
            )

    def test_lvs_owns_explicit_capacitance_windows(self):
        cases = [(False, 0.0), (True, 12.9e-15)]
        for explicit_cap, expected_substrate in cases:
            with self.subTest(explicit_cap=explicit_cap), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("CAP_WINDOW_%s" % explicit_cap)
                add_box(layout, cell, (3, 0), -5, 0, 20, 10)
                add_box(layout, cell, (19, 0), -5, 0, 20, 10)
                add_box(layout, cell, (5, 0), 0, 0, 10, 20)
                if explicit_cap:
                    add_box(layout, cell, (16, 0), 0, 0, 10, 10)
                add_box(layout, cell, (8, 0), -5, 2.5, 0, 7.5)
                add_box(layout, cell, (7, 0), -5, 2.5, 0, 7.5)
                add_box(layout, cell, (8, 0), 13, 0, 22, 100)
                add_box(layout, cell, (7, 0), 15, 2.5, 20, 7.5)
                add_text(layout, cell, (13, 0), "GATE", 5, 15)
                add_text(layout, cell, (13, 0), "PLATE", 15, 50)
                spice = self.extract(layout, cell, tempdir, capacitance=True)
                capacitors = capacitance_elements(spice)
                substrate_caps = [
                    (a, b, value)
                    for a, b, value in capacitors
                    if "SUB" in {a, b}
                ]
                gate_ports = set(physical_ports(spice, "GATE"))
                self.assertEqual(len(gate_ports), 1, spice)
                gate_caps = [
                    (a, b, value) for a, b, value in capacitors
                    if not {a, b}.isdisjoint(gate_ports)
                ]
                self.assertEqual(gate_caps, [])
                self.assertEqual(len(substrate_caps), len(capacitors))
                csub = sum(value for _a, _b, value in substrate_caps)
                self.assertAlmostEqual(
                    csub,
                    expected_substrate,
                    delta=max(expected_substrate * 1e-6, 1e-20),
                )
                if substrate_caps:
                    plate_ports = set(physical_ports(spice, "PLATE"))
                    self.assertEqual(len(plate_ports), 1, spice)
                    for a, b, _value in substrate_caps:
                        other = b if a == "SUB" else a
                        if other not in plate_ports:
                            self.assertIsNotNone(
                                equivalent_resistance(spice, "PLATE", other),
                                spice + "\n" + self.last_pex_log,
                            )


    def test_passive_diffusion_capacitance_conserves_area_and_resistance(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("DISTRIBUTED_DIFF_CAP")
            add_box(layout, cell, (3, 0), 0, 0, 100, 10)
            add_box(layout, cell, (19, 0), 0, 0, 100, 10)
            add_text(layout, cell, (13, 0), "LEFT", 0, 5)
            add_text(layout, cell, (13, 0), "RIGHT", 100, 5)

            spice = self.extract(layout, cell, tempdir, capacitance=True)
            capacitors = capacitance_elements(spice)
            self.assertGreater(len(capacitors), 1, spice)
            self.assertTrue(
                all("SUB" in {a, b} for a, b, _value in capacitors), spice
            )
            total = sum(value for _a, _b, value in capacitors)
            expected = 0.086e-15 * 1000.0
            self.assertAlmostEqual(total, expected, delta=expected * 1e-6)

            capacitance_nodes = {
                b if a == "SUB" else a for a, b, _value in capacitors
            }
            self.assertTrue(
                set(physical_ports(spice, "LEFT") + physical_ports(spice, "RIGHT"))
                <= capacitance_nodes,
                spice,
            )
            self.assertAlmostEqual(
                equivalent_resistance(spice, "LEFT", "RIGHT"),
                1800.0,
                delta=18.0,
            )

    def test_diffusion_capacitance_tracks_asymmetric_area_location(self):
        def extract_with_branch(side):
            with tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("DIFF_CAP_BRANCH_%s" % side)
                add_box(layout, cell, (3, 0), 0, 0, 100, 10)
                add_box(layout, cell, (19, 0), 0, 0, 100, 10)
                branch_x = 0 if side == "left" else 90
                add_box(layout, cell, (3, 0), branch_x, 10, branch_x + 10, 50)
                add_box(layout, cell, (19, 0), branch_x, 10, branch_x + 10, 50)
                add_text(layout, cell, (13, 0), "LEFT", 0, 5)
                add_text(layout, cell, (13, 0), "RIGHT", 100, 5)

                spice = self.extract(layout, cell, tempdir, capacitance=True)
                capacitors = capacitance_elements(spice)
                total = sum(value for _a, _b, value in capacitors)
                expected = 0.086e-15 * 1400.0
                self.assertAlmostEqual(total, expected, delta=expected * 1e-6)
                return (
                    capacitance_to_substrate_at_port(spice, "LEFT"),
                    capacitance_to_substrate_at_port(spice, "RIGHT"),
                )

        left_branch = extract_with_branch("left")
        right_branch = extract_with_branch("right")
        self.assertGreater(left_branch[0], right_branch[0], (left_branch, right_branch))
        self.assertGreater(right_branch[1], left_branch[1], (left_branch, right_branch))

    def test_unqualified_interconnect_coupling_is_not_emitted(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("NO_UNQUALIFIED_COUPLING")
            add_box(layout, cell, (5, 0), 0, 0, 100, 10)
            add_box(layout, cell, (8, 0), 0, 0, 100, 10)
            add_text(layout, cell, (13, 0), "GATE", 0, 5)
            add_text(layout, cell, (13, 0), "METAL", 100, 5)

            spice = self.extract(layout, cell, tempdir, capacitance=True)
            self.assertEqual(capacitance_elements(spice), [], spice)

    def test_mask_and_exclude_remove_diffusion_paths(self):
        cases = [
            ("design", (20, 0)),
            ("design", (63, 63)),
            ("fab", (63, 63)),
        ]
        for mode, layer in cases:
            with self.subTest(mode=mode, layer=layer), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("FILTER_%s_%d_%d" % (mode, *layer))
                diff_layer, narea_layer = (
                    ((3, 0), (19, 0)) if mode == "design" else ((4, 0), (3, 0))
                )
                add_box(layout, cell, diff_layer, 0, 0, 100, 10)
                add_box(layout, cell, narea_layer, 0, 0, 100, 10)
                add_box(layout, cell, layer, 45, 0, 55, 10)
                text_layer = (13, 0) if mode == "design" else (13, 1)
                add_text(layout, cell, text_layer, "LEFT", 10, 5)
                add_text(layout, cell, text_layer, "RIGHT", 90, 5)
                spice = self.extract(layout, cell, tempdir, mode=mode)
                self.assertIsNone(equivalent_resistance(spice, "LEFT", "RIGHT"))

    @unittest.skipUnless(KLAYOUT, "KLayout executable is required for fab conversion")
    def test_fab_preparation_preserves_text_for_pex(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("FAB_LABELS")
            add_box(layout, cell, (8, 0), 0, 0, 100, 10)
            add_text(layout, cell, (13, 0), "LEFT", 0, 5)
            add_text(layout, cell, (13, 0), "RIGHT", 100, 5)
            design_path = Path(tempdir) / "design.gds"
            fab_path = Path(tempdir) / "fab.gds"
            layout.write(str(design_path))
            run_checked(
                [
                    KLAYOUT,
                    "-b",
                    "-r",
                    str(FAB_PREP),
                    "-rd",
                    "input=" + str(design_path),
                    "-rd",
                    "output=" + str(fab_path),
                ]
            )

            converted = pya.Layout()
            converted.read(str(fab_path))
            text_layer = converted.find_layer(pya.LayerInfo(13, 1))
            self.assertIsNotNone(text_layer)
            labels = {
                shape.text.string
                for shape in converted.top_cell().shapes(text_layer).each()
                if shape.is_text()
            }
            self.assertEqual(labels, {"LEFT", "RIGHT"})
            spice = self.extract(converted, converted.top_cell(), tempdir, mode="fab")


            header = next(
                line for line in spice.splitlines() if line.startswith(".SUBCKT ")
            ).split()
            mappings = port_mappings(spice)
            self.assertEqual([item["logical"] for item in mappings], ["LEFT", "RIGHT"])
            self.assertEqual(
                header[2:-1], [item["physical"] for item in mappings]
            )
            measured = equivalent_resistance(spice, "LEFT", "RIGHT")
            self.assertAlmostEqual(measured, 2.0, delta=0.02)


if __name__ == "__main__":
    unittest.main()
