#!/usr/bin/env python3
"""Executable standard-cell checks; model results are not process qualification."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import characterize
import geometry
import logic
import mapping
import verify


class StandardCellTests(unittest.TestCase):
    def _reference(self, specs, path, *, design=None, state_load_ff=None):
        source = {"specs": specs, **(design or {})}
        top_name = (design or {}).get("name") or next(iter(specs))
        logic.write_reference(source, path, top_name=top_name)
        output = []
        active = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(".SUBCKT "):
                active = line.split()[1]
                if state_load_ff is None:
                    # characterize._header supplies this formal substrate port.
                    line += " E_SUB"
            if line.startswith(".ENDS ") and state_load_ff is not None:
                spec = specs.get(active)
                if spec is not None:
                    internals = sorted({mos[terminal] for mos in spec["mos"]
                                        for terminal in ("d", "g", "s")
                                        if mos[terminal] not in spec["pins"]})
                    for index, net in enumerate(internals):
                        output.extend((f"RSTATE{index} {net} 0 1e12",
                                       f"CSTATE{index} {net} 0 {state_load_ff}f"))
            if line.startswith("XTOP ") or line == ".END":
                continue
            output.append(line)
            if line.startswith(".ENDS "):
                active = None
        if state_load_ff is None:
            output.insert(0, f'.lib "{characterize.MODELS.resolve()}" b4soi')
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
        return path

    def _assert_pin_windows_are_bounded(self, spec, manifest):
        self.assertEqual(set(manifest["pins"]), set(spec["pins"]))
        width, height = manifest["bbox_um"][2:]
        for pin in spec["pins"]:
            windows = manifest["pins"][pin]
            self.assertTrue(windows, pin)
            for left, bottom, right, top in windows:
                self.assertGreaterEqual(left, 0.0, pin)
                self.assertGreaterEqual(bottom, 0.0, pin)
                self.assertLessEqual(left, right, pin)
                self.assertLessEqual(bottom, top, pin)
                self.assertLessEqual(right, width, pin)
                self.assertLessEqual(top, height, pin)

    def test_pin_window_manifests_cover_pins_within_cell_bounds(self):
        specs = logic.cell_specs()
        for name in ("INV_X1", "NAND2_X1"):
            with self.subTest(cell=name):
                spec = specs[name]
                _layout, _top, manifest = geometry.build_cell(
                    spec, 160, route_channel_um=40)
                self._assert_pin_windows_are_bounded(spec, manifest)


    def test_all_combinational_reference_truth_tables(self):
        specs = logic.cell_specs()
        combinational = {name: spec for name, spec in specs.items()
                         if not spec.get("sequential")}
        self.assertEqual(len(specs), 26)
        self.assertEqual(len(combinational), 22)
        self.assertEqual(sum(bool(spec.get("sequential")) for spec in specs.values()), 4)
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_truth_") as temporary:
            root = Path(temporary)
            for name, spec in combinational.items():
                reference = self._reference({name: spec}, root / (name + ".spice"))
                rows = characterize.truth_table(spec, reference, root / name,
                                                logic.evaluate)
                self.assertEqual(len(rows), 1 << len(spec["inputs"]), name)


    def test_spice_library_can_be_included_for_cell_simulation(self):
        specs = logic.cell_specs()
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_library_") as temporary:
            root = Path(temporary)
            generated = root / "generated.spice"
            logic.write_reference({"specs": specs}, generated, library=True)
            published = (HERE.parents[1] / "libs.ref/ICPS2023_5_gen_sc/"
                         "spice/ICPS2023_5_gen_sc.spice")
            for index, library in enumerate((generated, published)):
                deck = [
                    f'.lib "{characterize.MODELS.resolve()}" b4soi',
                    f'.include "{library.resolve()}"',
                    ".GLOBAL SUB", "VPOWER VDD 0 3", "VVSS VSS 0 0", "VSUB SUB 0 0",
                    "VA A 0 0", "XINV A Y VDD VSS INV_X1",
                    ".control", "set noaskquit", "op", "echo LOW_INPUT", "print v(Y)",
                    "alter VA 3", "op", "echo HIGH_INPUT", "print v(Y)",
                    "quit", ".endc", ".end"]
                text = characterize._run(deck, root / f"simulation_{index}")

                def output_after(marker):
                    match = re.search(
                        rf"{marker}\s*\n\s*v\(y\)\s*=\s*({characterize.NUMBER})",
                        text, re.I)
                    self.assertIsNotNone(match, marker)
                    return float(match.group(1))

                self.assertGreater(output_after("LOW_INPUT"), 2.7)
                self.assertLess(output_after("HIGH_INPUT"), 0.3)

    def test_sequential_reference_capture_and_hold(self):
        specs = logic.cell_specs()
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_seq_") as temporary:
            root = Path(temporary)
            # Explicit large node/output loads stabilize the state checks; not a timing test.
            for name in ("LATCH_TG_X1", "LATCH_GATE_X1"):
                spec = specs[name]
                reference = self._reference({name: spec}, root / (name + ".spice"),
                                            state_load_ff=10.0)
                deck = [
                    f'.lib "{characterize.MODELS.resolve()}" b4soi',
                    ".options temp=25 tnom=25 method=trap reltol=1e-3 gmin=1e-9 itl4=1000",
                    ".GLOBAL SUB", "VPOWER VDD 0 3", "VSUB SUB 0 0",
                    '.include "%s"' % reference.resolve(),
                    "VCLK CLK 0 PULSE(0 3 10n 5n 5n 40n 100n)",
                    "VD D 0 PWL(0n 3 20n 3 25n 0 70n 0 75n 3 200n 3)",
                    "XDUT D CLK Q VDD 0 " + name, "CLOAD Q 0 10f",
                    ".control", "set noaskquit", "tran 0.5n 180n",
                    "meas tran q20 find v(Q) at=20n", "meas tran q50 find v(Q) at=50n",
                    "meas tran q90 find v(Q) at=90n", "meas tran q130 find v(Q) at=130n",
                    "quit", ".endc", ".end"]
                values = characterize._measurements(characterize._run(deck, root / name))
                self.assertGreater(values["q20"], 2.5, name)
                self.assertLess(values["q50"], 0.5, name)
                self.assertLess(values["q90"], 0.5, name)
                self.assertGreater(values["q130"], 2.5, name)

            for name in ("DFF_TG_X1", "DFF_GATE_X1"):
                spec = specs[name]
                reference = self._reference({name: spec}, root / (name + ".spice"),
                                            state_load_ff=10.0)
                deck = [
                    f'.lib "{characterize.MODELS.resolve()}" b4soi',
                    ".options temp=25 tnom=25 method=trap reltol=1e-3 gmin=1e-9 itl4=1000",
                    ".GLOBAL SUB", "VPOWER VDD 0 3", "VSUB SUB 0 0",
                    '.include "%s"' % reference.resolve(),
                    "VCLK CLK 0 PULSE(0 3 10n 5n 5n 40n 100n)",
                    "VD D 0 PWL(0n 3 35n 3 36n 0 200n 0)",
                    "XDUT D CLK Q VDD 0 " + name, "CLOAD Q 0 10f",
                    ".control", "set noaskquit", "tran 0.5n 180n",
                    "meas tran q40 find v(Q) at=40n", "meas tran q80 find v(Q) at=80n",
                    "meas tran q140 find v(Q) at=140n", "quit", ".endc", ".end"]
                values = characterize._measurements(characterize._run(deck, root / name))
                self.assertGreater(values["q40"], 2.5, name)
                self.assertGreater(values["q80"], 2.5, name)
                self.assertLess(values["q140"], 0.5, name)

    def test_yosys_mapping_has_consumer_visible_state_behavior(self):
        source = """module mapper(input A, input B, input S, input CLK,
                       output Y, output reg Q);
            assign Y = S ? A : ~B;
            always @(posedge CLK) Q <= Y;
        endmodule
        """
        specs = logic.cell_specs()
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_map_") as temporary:
            root = Path(temporary)
            rtl = root / "mapper.v"
            rtl.write_text(source, encoding="utf-8")
            netlist = mapping.synthesize_rtl(rtl, workdir=root / "yosys")
            cells = {instance["cell"] for instance in netlist["instances"]}
            self.assertIn("MUX2_TG6T_X1", cells)
            self.assertIn("DFF_TG_X1", cells)
            self.assertTrue(cells <= set(specs), cells)

            reference = self._reference(specs, root / "mapped.spice",
                                        design={"name": netlist["name"],
                                                "ports": netlist["ports"],
                                                "instances": netlist["instances"]},
                                        state_load_ff=10.0)
            ports = list(netlist["ports"])
            deck = [
                f'.lib "{characterize.MODELS.resolve()}" b4soi',
                ".options temp=25 tnom=25 method=trap reltol=1e-3 gmin=1e-9 itl4=1000",
                ".GLOBAL SUB", "VPOWER VDD 0 3", "VVSS VSS 0 0", "VSUB SUB 0 0",
                '.include "%s"' % reference.resolve(),
                "VA A 0 3", "VB B 0 PWL(0n 0 35n 0 40n 3 200n 3)",
                "VS S 0 PWL(0n 3 35n 3 40n 0 200n 0)",
                "VCLK CLK 0 PULSE(0 3 10n 5n 5n 40n 100n)",
                "XDUT %s mapper" % " ".join(ports), "CLOAD Y 0 10f", "CQ Q 0 10f",
                ".control", "set noaskquit", "tran 0.5n 180n",
                "meas tran y20 find v(Y) at=20n", "meas tran y50 find v(Y) at=50n",
                "meas tran q40 find v(Q) at=40n", "meas tran q80 find v(Q) at=80n",
                "meas tran q140 find v(Q) at=140n", "quit", ".endc", ".end"]
            values = characterize._measurements(characterize._run(deck, root / "mapped_sim"))
            self.assertGreater(values["y20"], 2.5)
            self.assertLess(values["y50"], 0.5)
            self.assertGreater(values["q40"], 2.5)
            self.assertGreater(values["q80"], 2.5)
            self.assertLess(values["q140"], 0.5)

    def test_boolean_fallback_matches_transistor_truth_table(self):
        specs = logic.cell_specs()
        netlist = mapping.map_boolean_design(
            "fallback", {"A": "input", "B": "input", "Y": "output"},
            {"Y": "A & !B"}, specs=specs)
        self.assertFalse(any("$" in net for net in netlist["nets"]))
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_fallback_") as temporary:
            root = Path(temporary)
            reference = root / "fallback.spice"
            logic.write_reference({"specs": specs, "name": netlist["name"],
                                   "ports": netlist["ports"],
                                   "instances": netlist["instances"]},
                                  reference, top_name=netlist["name"])
            lines = [line for line in reference.read_text(encoding="utf-8").splitlines()
                     if not line.startswith("XTOP ") and line != ".END"]
            reference.write_text("\n".join(lines) + "\n", encoding="utf-8")
            deck = [
                f'.lib "{characterize.MODELS.resolve()}" b4soi',
                ".options temp=25 tnom=25",
                ".GLOBAL SUB", "VPOWER VDD 0 3", "VVSS VSS 0 0", "VSUB SUB 0 0",
                '.include "%s"' % reference.resolve(),
                "VA A 0 0", "VB B 0 0",
                "XDUT %s fallback" % " ".join(netlist["ports"]),
                ".control", "set noaskquit"]
            rows = [(0, 0), (0, 1), (1, 0), (1, 1)]
            for index, (a, b) in enumerate(rows):
                deck.extend((f"alter VA {3*a}", f"alter VB {3*b}",
                             "op", f"echo BOOL_ROW_{index}", "print v(Y)"))
            deck.extend(("quit", ".endc", ".end"))
            text = characterize._run(deck, root / "simulation")
            for index, (a, b) in enumerate(rows):
                match = re.search(r"BOOL_ROW_%d\s*\n(.*?)(?=BOOL_ROW_|\Z)" % index,
                                  text, re.S)
                self.assertIsNotNone(match, f"missing truth row {index}")
                voltage = re.search(
                    r"^\s*v\(y\)\s*=\s*(%s)" % characterize.NUMBER,
                    match.group(1), re.M)
                self.assertIsNotNone(voltage, f"missing output voltage for row {index}")
                expected = 3.0 if a and not b else 0.0
                actual = float(voltage.group(1))
                if expected:
                    self.assertGreater(actual, 2.7)
                else:
                    self.assertLess(actual, 0.3)

    def test_aoi21_strict_physical_pex_and_characterization(self):
        spec = logic.cell_specs()["AOI21_X1"]
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_physical_") as temporary:
            root = Path(temporary)
            layout, top, manifest = geometry.build_cell(spec, 160)
            layout.rename_cell(top.cell_index(), spec["name"])
            gds = root / (spec["name"] + ".gds")
            reference = root / (spec["name"] + ".spice")
            layout.write(str(gds))
            logic.write_reference(spec, reference)
            evidence = verify.verify_physical(gds, reference, spec["name"], root / "physical")
            self.assertTrue(evidence["strict_drc"])
            self.assertTrue(evidence["strict_lvs"])
            self.assertTrue(evidence["pex"])
            self.assertTrue(evidence["assembly"])
            self.assertEqual(manifest["area_um2"], manifest["width_um"] * manifest["height_um"])
            self._assert_pin_windows_are_bounded(spec, manifest)
            self.assertFalse(manifest["routing"]["direct_tin_diff_contact"])
            truth = characterize.truth_table(spec, evidence["postlayout_spice"],
                                            root / "truth", logic.evaluate)
            arcs = characterize.characterize_arcs(spec, evidence["postlayout_spice"],
                                                   root / "arcs", logic.evaluate)
            self.assertEqual(len(truth), 8)
            self.assertEqual(len(arcs), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
