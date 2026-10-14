#!/usr/bin/env python3
"""Geometry-to-netlist checks for the qualified ICPS2023_5 PEX paths."""

import shutil
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


def equivalent_resistance(netlist, source, sink):
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

    def test_mos_channel_capacitance_requires_an_explicit_cap_window(self):
        cases = [
            (False, 0.0, 8.6e-15),
            (True, 230.0e-15, 17.2e-15),
        ]
        for explicit_cap, expected_cox, expected_substrate in cases:
            with self.subTest(explicit_cap=explicit_cap), tempfile.TemporaryDirectory() as tempdir:
                layout, cell = self.make_layout("CAP_WINDOW_%s" % explicit_cap)
                add_box(layout, cell, (3, 0), 0, 0, 20, 10)
                add_box(layout, cell, (19, 0), 0, 0, 20, 10)
                add_box(layout, cell, (5, 0), -10, 0, 10, 10)
                if explicit_cap:
                    add_box(layout, cell, (16, 0), 0, 0, 10, 10)
                add_box(layout, cell, (8, 0), 12, 0, 18, 50)
                add_box(layout, cell, (7, 0), 12.5, 2.5, 17.5, 7.5)
                add_text(layout, cell, (13, 0), "GATE", -5, 5)
                add_text(layout, cell, (13, 0), "PLATE", 15, 50)
                spice = self.extract(layout, cell, tempdir, capacitance=True)
                capacitors = []
                for line in spice.splitlines():
                    fields = line.split()
                    if len(fields) == 4 and fields[0].startswith("C"):
                        capacitors.append((frozenset(fields[1:3]), float(fields[3])))

                substrate_caps = [(nodes, value) for nodes, value in capacitors if "SUB" in nodes]
                gate_caps = [(nodes, value) for nodes, value in capacitors if "GATE" in nodes]
                self.assertEqual(len(substrate_caps), 1, spice + "\n" + self.last_pex_log)
                self.assertEqual(len(gate_caps), 1 if explicit_cap else 0)
                csub = substrate_caps[0][1]
                cox = gate_caps[0][1] if gate_caps else 0.0
                self.assertAlmostEqual(
                    cox,
                    expected_cox,
                    delta=max(expected_cox * 1e-6, 1e-20),
                    msg=spice + "\n" + self.last_pex_log,
                )
                self.assertAlmostEqual(
                    csub, expected_substrate, delta=expected_substrate * 1e-6
                )
                self.assertEqual(len(capacitors), 2 if explicit_cap else 1)
                for nodes, _value in substrate_caps + gate_caps:
                    other = next(node for node in nodes if node not in {"SUB", "GATE"})
                    if other != "PLATE":
                        self.assertIsNotNone(
                            equivalent_resistance(spice, "PLATE", other),
                            spice + "\n" + self.last_pex_log,
                        )

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
            self.assertIn(".SUBCKT FAB_LABELS_pex LEFT RIGHT SUB", spice)
            measured = equivalent_resistance(spice, "LEFT", "RIGHT")
            self.assertAlmostEqual(measured, 2.0, delta=0.02)


if __name__ == "__main__":
    unittest.main()
