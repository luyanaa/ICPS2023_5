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
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
ASSEMBLER_SCRIPT = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
KLAYOUT = shutil.which("klayout")
PCELL_GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"


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

    def run_lvs(self, layout, cell, tempdir, schematic):
        input_path = Path(tempdir) / (cell.name + "_lvs.gds")
        output_path = Path(tempdir) / (cell.name + "_lvs.spice")
        report_path = Path(tempdir) / (cell.name + "_lvs.lyrdb")
        layout.write(str(input_path))
        result = subprocess.run(
            [
                KLAYOUT, "-b", "-r", str(LVS_DECK),
                "-rd", "input=" + str(input_path),
                "-rd", "top=" + cell.name,
                "-rd", "schematic=" + str(schematic),
                "-rd", "report=" + str(report_path),
                "-rd", "output=" + str(output_path),
                "-rd", "layer_mode=design",
            ],
            text=True, capture_output=True,
        )
        return result, output_path

    def generate_native_bridge(self, tempdir):
        output_dir = Path(tempdir) / "native_bridge"
        output_dir.mkdir()
        run_checked(
            [
                KLAYOUT, "-b", "-r", str(PCELL_GENERATOR),
                "-rd", "output_dir=" + str(output_dir),
                "-rd", "pcell_macro=" + str(PCELL_MACRO),
            ]
        )
        return output_dir / "bridge_tin_min.gds"

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

    def test_raw_tin_ml1_crossing_requires_a_cnt_contact(self):
        for mode in ("design", "fab"):
            tin = (5, 0)
            if mode == "design":
                ml1, cnt, text = (8, 0), (7, 0), (13, 0)
            else:
                ml1, cnt, text = (7, 0), (6, 0), (13, 1)
            for has_contact in (False, True):
                with self.subTest(mode=mode, has_contact=has_contact), tempfile.TemporaryDirectory() as tempdir:
                    layout, cell = self.make_layout(
                        "TIN_ML1_CROSSING_%s_%s" %
                        (mode.upper(), "CNT" if has_contact else "OPEN")
                    )
                    add_box(layout, cell, tin, 0, 40, 100, 50)
                    add_box(layout, cell, ml1, 47, 0, 53, 90)
                    if has_contact:
                        add_box(layout, cell, cnt, 47.5, 42.5, 52.5, 47.5)
                    add_text(layout, cell, text, "TIN_ROUTE", 50, 45)
                    add_text(layout, cell, text, "ML1_ROUTE", 50, 45)

                    spice = self.extract(layout, cell, tempdir, mode=mode)
                    self.assertEqual(capacitance_elements(spice), [])
                    mappings = port_mappings(spice)
                    tin_node = next(row["physical"] for row in mappings
                                    if row["logical"] == "TIN_ROUTE" and row["layer"] == "TIN")
                    ml1_node = next(row["physical"] for row in mappings
                                    if row["logical"] == "ML1_ROUTE" and row["layer"] == "ML1")
                    measured = equivalent_resistance(spice, tin_node, ml1_node)
                    if has_contact:
                        self.assertIsNotNone(measured, spice)
                        self.assertAlmostEqual(measured, 0.9, delta=0.02)
                        maps = json_records(spice, "* RESISTOR_MAP ")
                        self.assertTrue(
                            any(record["material"] == "CNT_TIN" for record in maps),
                            spice,
                        )
                    else:
                        self.assertNotEqual(tin_node, ml1_node, spice)
                        self.assertIsNone(measured, spice)

    def test_four_terminal_body_tie_geometry_fails_closed(self):
        cases = (
            ("TINBLK", (5, 1), "NMOS4 body-tie geometry"),
            ("PWL_OUTSIDE_TIN", (21, 0), "body-tie route"),
        )
        for name, marker_layer, reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout(name)
                add_box(layout, cell, (3, 0), 0, 0, 100, 30)
                add_box(layout, cell, (19, 0), 0, 0, 100, 30)
                add_box(layout, cell, (5, 0), 40, 0, 46, 30)
                if name == "TINBLK":
                    add_box(layout, cell, marker_layer, 40, 0, 46, 30)
                else:
                    add_box(layout, cell, marker_layer, 80, 0, 90, 30)
                input_path = Path(tempdir) / "body_tie.gds"
                output_path = Path(tempdir) / "body_tie.spice"
                layout.write(str(input_path))
                result = subprocess.run(
                    [sys.executable, str(PEX_SCRIPT), "--input", str(input_path),
                     "--top", cell.name, "--output", str(output_path)],
                    text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(reason, result.stdout + result.stderr)
                self.assertFalse(output_path.exists())


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


    @unittest.skipUnless(KLAYOUT, "KLayout executable is required for LVS/PEX")
    def test_lvs_owned_tin_resistor_has_real_pex_anchors_and_one_assembled_body(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("TIN_RESISTOR_CUTOVER")
            add_box(layout, cell, (5, 0), 0, 40, 100, 50)
            add_box(layout, cell, (15, 0), 0, 40, 100, 50)
            for x, name in ((2.5, "A"), (97.5, "B")):
                add_box(layout, cell, (7, 0), x - 2.5, 42.5, x + 2.5, 47.5)
                add_box(layout, cell, (8, 0), x - 7.5, 37.5, x + 7.5, 52.5)
                add_text(layout, cell, (13, 0), name, x, 45)

            reference = Path(tempdir) / "tin_resistor_reference.cir"
            reference.write_text(
                ".GLOBAL SUB\n"
                ".SUBCKT TIN_RESISTOR_CUTOVER A B\n"
                "R1 A B 200\n"
                ".ENDS TIN_RESISTOR_CUTOVER\n"
                "XTOP A B TIN_RESISTOR_CUTOVER\n.END\n",
                encoding="utf-8",
            )
            lvs_result, lvs_path = self.run_lvs(
                layout, cell, tempdir, reference
            )
            self.assertEqual(
                lvs_result.returncode, 0,
                lvs_result.stdout + lvs_result.stderr,
            )
            self.assertIn(
                "ICPS2023_5 LVS MATCH (mismatches=0)",
                lvs_result.stdout + lvs_result.stderr,
            )
            lvs_text = lvs_path.read_text(encoding="utf-8")
            self.assertEqual(len(json_records(lvs_text, "* ICPS2023_5_R_TIN ")), 1)

            pex_text = self.extract(layout, cell, tempdir)
            tin_maps = json_records(pex_text, "* R_TIN_MAP ")
            self.assertEqual(len(tin_maps), 1, pex_text)
            pex_endpoints = [endpoint["node"] for endpoint in tin_maps[0]["endpoints"]]
            self.assertEqual(len(set(pex_endpoints)), 2)
            self.assertIsNone(equivalent_resistance(pex_text, "A", "B"), pex_text)
            self.assertTrue(
                any(record["material"] == "CNT_TIN"
                    for record in json_records(pex_text, "* RESISTOR_MAP ")),
                pex_text,
            )

            pex_path = Path(tempdir) / (cell.name + ".spice")
            assembled_path = Path(tempdir) / "assembled.spice"
            assembled_result = subprocess.run(
                [
                    sys.executable, str(ASSEMBLER_SCRIPT),
                    "--lvs", str(lvs_path), "--pex", str(pex_path),
                    "--schematic", str(reference), "--top", cell.name,
                    "--output", str(assembled_path),
                ],
                text=True, capture_output=True,
            )
            self.assertEqual(
                assembled_result.returncode, 0,
                assembled_result.stdout + assembled_result.stderr,
            )
            assembled = assembled_path.read_text(encoding="utf-8")
            tin_elements = [
                line for line in assembled.splitlines()
                if re.match(r"^RTIN\d+\s", line)
            ]
            self.assertEqual(len(tin_elements), 1, assembled)
            self.assertIsNotNone(equivalent_resistance(assembled, "A", "B"), assembled)
            self.assertGreater(equivalent_resistance(assembled, "A", "B"), 200.0)
            tin_resistor_map = next(
                record for record in json_records(assembled, "* RESISTOR_MAP ")
                if record["name"] == "RTIN0001"
            )
            node_aliases = {
                record["pex"]: record["assembled"]
                for record in json_records(assembled, "* ASSEMBLY_NODE_MAP ")
            }
            self.assertEqual(
                {tin_resistor_map["a"], tin_resistor_map["b"]},
                {node_aliases[node] for node in pex_endpoints},
            )

    @unittest.skipUnless(KLAYOUT, "KLayout executable is required for native Bridge PEX")
    def test_native_bent_bridge_assembles_lvs_body_and_pex_contacts_once(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            native_gds = self.generate_native_bridge(tempdir)
            layout = pya.Layout()
            layout.read(str(native_gds))
            cell = layout.cell("bridge_tin_min")
            self.assertIsNotNone(cell)

            design_dir = tempdir / "design_pex"
            design_dir.mkdir()
            design_spice = self.extract(layout, cell, design_dir)
            design_pex_path = design_dir / (cell.name + ".spice")
            access_reports = json_records(design_spice, "* MOS_ACCESS_RESULT ")
            self.assertEqual(
                json_records(self.last_pex_log, "MOS_ACCESS_RESULT "),
                access_reports,
            )
            self.assertEqual({report["layer"] for report in access_reports},
                             {"NAREA", "PAREA"})
            self.assertTrue(
                all(report["passed"] and report["gates"] == 0
                    and report["bounded"] == 0 for report in access_reports),
                access_reports,
            )
            self.assertEqual(capacitance_elements(design_spice), [])
            self.assertEqual(json_records(design_spice, "* MOS_MAP "), [])
            design_tin_maps = json_records(design_spice, "* R_TIN_MAP ")
            self.assertEqual(len(design_tin_maps), 1, design_spice)
            design_tin_map = design_tin_maps[0]
            self.assertEqual(
                len({endpoint["node"] for endpoint in design_tin_map["endpoints"]}),
                2,
                design_tin_map,
            )
            self.assertEqual(
                {(endpoint["x_um"], endpoint["y_um"])
                 for endpoint in design_tin_map["endpoints"]},
                {(6.5, 6.5), (26.5, 31.5)},
            )
            design_resistors = json_records(design_spice, "* RESISTOR_MAP ")
            design_contacts = [
                record for record in design_resistors
                if record["material"] == "CNT_TIN"
            ]
            self.assertEqual(len(design_contacts), 2, design_resistors)
            for record in design_contacts:
                self.assertAlmostEqual(record["resistance_ohm"], 0.9, delta=0.02)
            self.assertIsNone(
                equivalent_resistance(design_spice, "START", "END"),
                design_spice,
            )

            reference = tempdir / "bridge_reference.cir"
            reference.write_text(
                ".GLOBAL SUB\n"
                ".SUBCKT bridge_tin_min START END\n"
                "R1 START END 90\n"
                ".ENDS bridge_tin_min\n"
                "XTOP START END bridge_tin_min\n.END\n",
                encoding="utf-8",
            )
            lvs_result, lvs_path = self.run_lvs(
                layout, cell, tempdir, reference
            )
            self.assertEqual(
                lvs_result.returncode, 0,
                lvs_result.stdout + lvs_result.stderr,
            )
            self.assertIn(
                "ICPS2023_5 LVS MATCH (mismatches=0)",
                lvs_result.stdout + lvs_result.stderr,
            )
            lvs_tin_maps = json_records(
                lvs_path.read_text(encoding="utf-8"),
                "* ICPS2023_5_R_TIN ",
            )
            self.assertEqual(len(lvs_tin_maps), 1, lvs_tin_maps)
            self.assertAlmostEqual(lvs_tin_maps[0]["r_ohm"], 90.0, delta=0.1)
            names = dict(line.split()[2:4] for line in lvs_path.read_text().splitlines()
                         if line.startswith("* net "))
            self.assertEqual(
                {names[lvs_tin_maps[0][terminal]] for terminal in ("a", "b")},
                {"START", "END"},
            )

            assembled_path = tempdir / "bridge_assembled.spice"
            assembled_result = subprocess.run(
                [
                    sys.executable, str(ASSEMBLER_SCRIPT),
                    "--lvs", str(lvs_path), "--pex", str(design_pex_path),
                    "--schematic", str(reference), "--top", cell.name,
                    "--output", str(assembled_path),
                ],
                text=True, capture_output=True,
            )
            self.assertEqual(
                assembled_result.returncode, 0,
                assembled_result.stdout + assembled_result.stderr,
            )
            assembled = assembled_path.read_text(encoding="utf-8")
            header = next(
                line for line in assembled.splitlines()
                if line.startswith(".SUBCKT ")
            ).split()
            self.assertTrue({"START", "END"} <= set(header[2:]), assembled)
            tin_elements = [
                line for line in assembled.splitlines()
                if re.match(r"^RTIN\d+\s", line)
            ]
            self.assertEqual(len(tin_elements), 1, assembled)
            self.assertGreater(
                equivalent_resistance(assembled, "START", "END"),
                lvs_tin_maps[0]["r_ohm"],
                assembled,
            )
            assembled_resistors = json_records(assembled, "* RESISTOR_MAP ")
            assembled_tin_bodies = [
                record for record in assembled_resistors
                if record.get("ownership") == "lvs_device"
            ]
            assembled_contacts = [
                record for record in assembled_resistors
                if record["material"] == "CNT_TIN"
            ]
            self.assertEqual(len(assembled_tin_bodies), 1, assembled_resistors)
            self.assertEqual(assembled_tin_bodies[0]["name"], "RTIN0001")
            self.assertEqual(len(assembled_contacts), len(design_contacts))
            endpoint_aliases = {
                record["pex"]: record["assembled"]
                for record in json_records(assembled, "* ASSEMBLY_NODE_MAP ")
            }
            mapped_endpoints = {
                endpoint_aliases[endpoint["node"]]
                for endpoint in design_tin_map["endpoints"]
            }
            self.assertEqual(
                set(tin_elements[0].split()[1:3]),
                mapped_endpoints,
                assembled,
            )

            fab_gds = tempdir / "bridge_tin_min_fab.gds"
            run_checked(
                [
                    KLAYOUT, "-b", "-r", str(FAB_PREP),
                    "-rd", "input=" + str(native_gds),
                    "-rd", "output=" + str(fab_gds),
                ]
            )
            fab_layout = pya.Layout()
            fab_layout.read(str(fab_gds))
            fab_cell = fab_layout.cell(cell.name)
            self.assertIsNotNone(fab_cell)
            fab_dir = tempdir / "fab_pex"
            fab_dir.mkdir()
            fab_spice = self.extract(fab_layout, fab_cell, fab_dir, mode="fab")
            fab_access_reports = json_records(fab_spice, "* MOS_ACCESS_RESULT ")
            self.assertEqual(fab_access_reports, access_reports)
            self.assertEqual(
                json_records(self.last_pex_log, "MOS_ACCESS_RESULT "),
                fab_access_reports,
            )
            fab_tin_maps = json_records(fab_spice, "* R_TIN_MAP ")
            self.assertEqual(len(fab_tin_maps), 1, fab_spice)
            self.assertEqual(
                {(endpoint["x_um"], endpoint["y_um"])
                 for endpoint in fab_tin_maps[0]["endpoints"]},
                {(endpoint["x_um"], endpoint["y_um"])
                 for endpoint in design_tin_map["endpoints"]},
            )
            fab_contacts = [
                record for record in json_records(fab_spice, "* RESISTOR_MAP ")
                if record["material"] == "CNT_TIN"
            ]
            self.assertEqual(len(fab_contacts), len(design_contacts))
            self.assertEqual(
                sorted(record["resistance_ohm"] for record in fab_contacts),
                sorted(record["resistance_ohm"] for record in design_contacts),
            )
            self.assertIsNone(
                equivalent_resistance(fab_spice, "START", "END"),
                fab_spice,
            )

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

    def test_mos_map_reports_parallel_contact_cut_counts(self):
        # Two CNT cuts on the drain (positive-x) side of one NMOS terminal:
        # the nearest becomes the MOS-owned tie, the further one stays
        # PEX-owned.  MOS_MAP must report N_cnt = actual openings per side
        # so the assembler can attach R_cnt = R_unit / N_cnt (AGENTS.md 4.2).
        with tempfile.TemporaryDirectory() as tempdir:
            layout, cell = self.make_layout("MOS_PARALLEL_CUTS")
            add_box(layout, cell, (3, 0), -7, 0, 40, 10)
            add_box(layout, cell, (19, 0), -7, 0, 40, 10)
            add_box(layout, cell, (5, 0), 0, 0, 6, 10)
            # source cut (negative side)
            add_box(layout, cell, (8, 0), -7, 2.5, -2, 7.5)
            add_box(layout, cell, (7, 0), -5, 2.5, -4, 7.5)
            add_text(layout, cell, (13, 0), "SOURCE", -4.5, 5)
            # two drain cuts (positive side), nearest first
            add_box(layout, cell, (8, 0), 8, 2.5, 13, 7.5)
            add_box(layout, cell, (7, 0), 10, 2.5, 11, 7.5)
            add_box(layout, cell, (8, 0), 20, 2.5, 25, 7.5)
            add_box(layout, cell, (7, 0), 22, 2.5, 23, 7.5)
            add_text(layout, cell, (13, 0), "DRAIN", 10.5, 5)

            spice = self.extract(layout, cell, tempdir)
            mos_maps = json_records(spice, "* MOS_MAP ")
            self.assertEqual(len(mos_maps), 1, spice)
            mos_map = mos_maps[0]
            self.assertEqual(mos_map["source_contact_cuts"], 1, mos_map)
            self.assertEqual(mos_map["drain_contact_cuts"], 2, mos_map)

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
        cases = [(False, 0.0), (True, (28 * 10 - 10 * 10) * 0.086e-15)]
        for explicit_cap, expected_substrate in cases:
            with self.subTest(explicit_cap=explicit_cap), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("CAP_WINDOW_%s" % explicit_cap)
                add_box(layout, cell, (3, 0), -8, 0, 20, 10)
                add_box(layout, cell, (19, 0), -8, 0, 20, 10)
                add_box(layout, cell, (5, 0), 0, 0, 10, 20)
                if explicit_cap:
                    add_box(layout, cell, (16, 0), 0, 0, 10, 10)
                # Stay outside the final, +2 um-biased TiN gate, not merely
                # outside its drawing outline.
                add_box(layout, cell, (8, 0), -8, 2.5, -3, 7.5)
                add_box(layout, cell, (7, 0), -8, 2.5, -3, 7.5)
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
