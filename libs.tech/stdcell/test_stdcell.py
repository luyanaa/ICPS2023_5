#!/usr/bin/env python3
"""Executable standard-cell checks; model results are not process qualification."""
from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_engine
import characterize
import core_router
import geometry
import logic
import mapping
import model_binding
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
                # 500um is the experimental row height the library publishes:
                # the NMOS row starts clear of the full-width VSS rail band, so
                # the pre-rail 160um probe no longer builds.
                _layout, _top, manifest = geometry.build_cell(
                    spec, 500, route_channel_um=40)
                self._assert_pin_windows_are_bounded(spec, manifest)


    def test_soi_edge_straps_are_physical_route_obstacles_in_both_architectures(self):
        spec = logic.cell_specs()["INV_X1"]
        for architecture, mirror in (("compact_rows", False), ("channel", True)):
            with self.subTest(architecture=architecture):
                layout, top, manifest = geometry.build_cell(
                    spec, 500, architecture=architecture, mirror=mirror)
                records = manifest["soi_edge_protection"]["devices"]
                straps = [shape for shape in manifest["routing"]["shapes"]["ML1"]
                          if shape[2] == "soi_edge_strap"]

                actual = geometry.soi_edge_protection(layout, top)
                self.assertTrue(actual["passed"])
                self.assertEqual(actual["gate_count"], len(spec["mos"]))
                self.assertEqual(actual["single_edge_breaks_checked"],
                                 2 * len(spec["mos"]))
                self.assertTrue(actual["single_edge_breaks_passed"])
                self.assertTrue(all(
                    contact["off_diffusion"]
                    for device in actual["devices"]
                    for contact in device["contacts"]))
                self.assertTrue(all(
                    len(device["single_edge_faults"]) == 2
                    and all(fault["tin_continuity_passed"]
                            for fault in device["single_edge_faults"])
                    for device in actual["devices"]))

                rect = tuple(straps[0][0])
                clearance = geometry.ML1_WIDTH/2 + geometry.ML1_SPACE
                y = (rect[1] + rect[3]) / 2
                route = geometry._route_ml1(
                    (rect[0] - clearance - 5.0, y),
                    (rect[2] + clearance + 5.0, y),
                    "__PROTECTION_ROUTE_PROBE__",
                    [(rect, "__PROTECTION_NET__", "soi_edge_strap")],
                    manifest["width_um"], manifest["height_um"])
                for x, y in route:
                    self.assertFalse(
                        rect[0] - clearance <= x <= rect[2] + clearance
                        and rect[1] - clearance <= y <= rect[3] + clearance)

                record = records[0]
                upper_contact = next(
                    contact for contact in record["contacts"]
                    if contact["edge"] == "upper")
                source = tuple(upper_contact["center_um"])
                target = (source[0] + 25.0, source[1])
                target_pad = (
                    target[0] - geometry.GATE_CONTACT / 2.0,
                    target[1] - geometry.GATE_CONTACT / 2.0,
                    target[0] + geometry.GATE_CONTACT / 2.0,
                    target[1] + geometry.GATE_CONTACT / 2.0)
                same_net_shapes = [
                    (tuple(upper_contact["landing_um"]), "__SAME_GATE__", "pad"),
                    (tuple(record["strap_um"]), "__SAME_GATE__",
                     "soi_edge_strap"),
                    (target_pad, "__SAME_GATE__", "pad")]
                path = geometry._route_ml1(
                    source, target, "__SAME_GATE__", same_net_shapes,
                    manifest["width_um"], manifest["height_um"])
                self.assertEqual(path[0], source)
                self.assertEqual(path[-1], target)

    def test_actual_edge_checker_rejects_a_deleted_off_island_endpoint(self):
        spec = logic.cell_specs()["INV_X1"]
        layout, top, manifest = geometry.build_cell(spec, 500)
        cut = manifest["soi_edge_protection"]["devices"][0]["contacts"][0]["cut_um"]
        target = tuple(round(value / layout.dbu) for value in cut)
        layer = layout.find_layer(geometry.db.LayerInfo(geometry.CNT, 0))
        shapes = top.shapes(layer)
        boxes = [tuple(value for value in
                       (shape.box.left, shape.box.bottom,
                        shape.box.right, shape.box.top))
                 for shape in shapes.each() if shape.is_box()]
        self.assertIn(target, boxes)
        shapes.clear()
        for box in boxes:
            if box != target:
                shapes.insert(geometry.db.Box(*box))
        with self.assertRaisesRegex(ValueError, "one off-DIFF CNT endpoint"):
            geometry.soi_edge_protection(layout, top)

    def test_geometry_checker_covers_non_mos_crossings_and_ignores_nearby_tin(self):
        def layout_with_crossing(layer_mode, covered):
            layout = geometry.db.Layout()
            layout.dbu = geometry.DBU
            top = layout.create_cell("non_mos_crossing")
            diff_layer, ml1_layer = (
                (3, 8) if layer_mode == "design" else (4, 7)
            )

            def box(layer_number, bounds):
                layer = layout.layer(geometry.db.LayerInfo(layer_number, 0))
                ticks = [round(value / layout.dbu) for value in bounds]
                top.shapes(layer).insert(geometry.db.Box(*ticks))

            box(diff_layer, (0.0, 0.0, 40.0, 40.0))
            tin_bounds = (
                (10.0, -5.0, 16.0, 5.0) if layer_mode == "design"
                else (8.0, -5.0, 18.0, 7.0)
            )
            box(5, tin_bounds)
            if covered:
                box(ml1_layer, (6.0, -4.0, 20.0, 4.0))
            return layout, top

        for layer_mode in ("design", "fab"):
            with self.subTest(layer_mode=layer_mode):
                layout, top = layout_with_crossing(layer_mode, covered=True)
                result = geometry.soi_edge_protection(
                    layout, top, layer_mode=layer_mode
                )
                self.assertEqual(result["crossing_count"], 1)
                self.assertEqual(result["gate_count"], 0)

                damaged_layout, damaged_top = layout_with_crossing(
                    layer_mode, covered=False
                )
                with self.assertRaisesRegex(ValueError, "TiN crossing"):
                    geometry.soi_edge_protection(
                        damaged_layout, damaged_top, layer_mode=layer_mode
                    )

        for layer_mode in ("design", "fab"):
            layout = geometry.db.Layout()
            layout.dbu = geometry.DBU
            top = layout.create_cell("adjacent_tin_only")
            diff_layer = 3 if layer_mode == "design" else 4
            tin_bounds = (
                ((10.0, 3.0, 36.0, 9.0), (10.0, -9.0, 36.0, -3.0))
                if layer_mode == "design"
                else ((10.0, 1.0, 36.0, 7.0), (10.0, -7.0, 36.0, -1.0))
            )
            for layer_number, bounds in (
                (diff_layer, (0.0, 0.0, 40.0, 40.0)),
                *((5, bounds) for bounds in tin_bounds),
            ):
                layer = layout.layer(geometry.db.LayerInfo(layer_number, 0))
                ticks = [round(value / layout.dbu) for value in bounds]
                top.shapes(layer).insert(geometry.db.Box(*ticks))
            result = geometry.soi_edge_protection(
                layout, top, layer_mode=layer_mode
            )
            self.assertEqual(result["crossing_count"], 0)
            self.assertEqual(result["gate_count"], 0)


    def test_connected_off_island_arm_does_not_expand_local_crossing(self):
        def layout_with_arm(covered):
            layout = geometry.db.Layout()
            layout.dbu = geometry.DBU
            top = layout.create_cell("connected_tin_arm")

            def box(layer_number, bounds):
                layer = layout.layer(geometry.db.LayerInfo(layer_number, 0))
                ticks = [round(value / layout.dbu) for value in bounds]
                top.shapes(layer).insert(geometry.db.Box(*ticks))

            box(4, (0.0, 0.0, 40.0, 40.0))
            box(5, (10.0, -10.0, 16.0, 10.0))
            box(5, (14.0, -7.0, 36.0, -1.0))
            if covered:
                box(7, (8.0, -4.0, 18.0, 4.0))
            return layout, top

        layout, top = layout_with_arm(covered=True)
        result = geometry.soi_edge_protection(layout, top, layer_mode="fab")
        self.assertEqual(result["crossing_count"], 1)
        self.assertEqual(result["gate_count"], 0)

        damaged_layout, damaged_top = layout_with_arm(covered=False)
        with self.assertRaisesRegex(ValueError, "TiN crossing"):
            geometry.soi_edge_protection(
                damaged_layout, damaged_top, layer_mode="fab"
            )

    def test_fab_geometry_checks_gate_bypass_with_fab_dopants(self):
        layout = geometry.db.Layout()
        layout.dbu = geometry.DBU
        top = layout.create_cell("prepared_nmos_gate")

        def box(layer_number, bounds):
            layer = layout.layer(geometry.db.LayerInfo(layer_number, 0))
            ticks = [round(value / layout.dbu) for value in bounds]
            top.shapes(layer).insert(geometry.db.Box(*ticks))

        box(4, (0.0, 0.0, 48.0, 20.0))
        box(3, (-2.0, -2.0, 50.0, 22.0))
        for bounds in (
            (19.0, -8.0, 29.0, 28.0),
            (17.5, -18.0, 30.5, -5.0),
            (17.5, 25.0, 30.5, 38.0),
        ):
            box(5, bounds)
        for y in (-11.5, 31.5):
            box(6, (21.5, y - 2.5, 26.5, y + 2.5))
        box(7, (17.0, -22.0, 31.0, 42.0))

        result = geometry.soi_edge_protection(layout, top, layer_mode="fab")
        self.assertEqual(result["gate_count"], 1)

        cnt_layer = layout.layer(geometry.db.LayerInfo(6, 0))
        cnt_shapes = top.shapes(cnt_layer)
        cut_to_remove = geometry.db.Box(
            round(21.5 / layout.dbu), round(-14.0 / layout.dbu),
            round(26.5 / layout.dbu), round(-9.0 / layout.dbu),
        )
        remaining = [
            shape.box for shape in cnt_shapes.each()
            if shape.is_box() and shape.box != cut_to_remove
        ]
        cnt_shapes.clear()
        for cut in remaining:
            cnt_shapes.insert(cut)
        with self.assertRaisesRegex(ValueError, "one off-DIFF CNT endpoint"):
            geometry.soi_edge_protection(layout, top, layer_mode="fab")

    def test_separate_supply_pads_reach_rails_without_crossing_gate_straps(self):
        for name in ("NAND2_X1", "NOR2_X1"):
            with self.subTest(cell=name):
                layout, top, manifest = geometry.build_cell(
                    logic.cell_specs()[name], 500.0)
                boxes = {
                    (number, 0): [shape.box for shape in
                                  top.shapes(layout.layer(number, 0)).each()]
                    for number in (3, 5, 7, 8)}
                graph = core_router._electrical_components(boxes, layout.dbu)
                components = graph["components_by_layer"][(8, 0)]
                electrical = graph["electrical_ids_by_layer"][(8, 0)]

                def component_at(x, y):
                    point = geometry.db.Point(
                        round(x / layout.dbu), round(y / layout.dbu))
                    ids = core_router._component_ids_at(components, point)
                    self.assertEqual(len(ids), 1)
                    return electrical[ids[0]]

                rail_ids = {}
                for net, rail in manifest["power_rails_um"].items():
                    rail_ids[net] = component_at(
                        (rail[0] + rail[2]) / 2, (rail[1] + rail[3]) / 2)
                self.assertNotEqual(rail_ids["VDD"], rail_ids["VSS"])
                for contact in manifest["contacts"]:
                    if contact["kind"] != "diffusion" or contact["net"] not in rail_ids:
                        continue
                    cut = contact["cut_um"]
                    self.assertEqual(
                        component_at((cut[0] + cut[2]) / 2,
                                     (cut[1] + cut[3]) / 2),
                        rail_ids[contact["net"]])

    def test_l6_edge_geometry_uses_native_channel_formula(self):
        layout, top, _manifest = geometry.build_cell(
            logic.cell_specs()["INV_X1"], 500.0, fingers=1)
        diff = geometry._physical_region(layout, top, geometry.DIFF)
        tin = geometry._physical_region(layout, top, geometry.TIN)
        cnt = geometry._physical_region(layout, top, geometry.CNT)
        ml1 = geometry._physical_region(layout, top, geometry.ML1)
        crossing = next((tin & diff).merged().each())
        box = crossing.bbox()
        active = diff.interacting(geometry.db.Region(crossing)).bbox()
        dbu = layout.dbu
        self.assertAlmostEqual(active.width() * dbu, 48.0)
        self.assertAlmostEqual(
            ((box.left + box.right) / 2.0 - active.left) * dbu, 24.0)
        self.assertAlmostEqual(box.width() * dbu, 6.0)
        edge_strip = geometry.db.Region(geometry.db.Box(
            active.left, box.bottom - 1, active.right, box.bottom + 1))
        gate_metal = ml1.interacting(geometry.db.Region(crossing))
        self.assertAlmostEqual((gate_metal & edge_strip).bbox().width() * dbu, 14.0)
        active_cuts = cnt.interacting(geometry.db.Region(active))
        contact_centers = sorted(
            ((cut.bbox().left + cut.bbox().right) / 2.0 - active.left) * dbu
            for cut in active_cuts.each())
        self.assertEqual(len(contact_centers), 2)
        self.assertAlmostEqual(contact_centers[0], 6.5)
        self.assertAlmostEqual(contact_centers[1], 41.5)

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

    def test_design_fab_pex_resistance_disagreement_is_not_correspondence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summaries = [
                {"layer": layer, "gates": 0, "bounded": 0, "unbounded": 0,
                 "ambiguous": 0, "orphaned": 0, "failures": [], "passed": True}
                for layer in ("NAREA", "PAREA")
            ]
            prefix = "".join("* MOS_ACCESS_RESULT " + json.dumps(row) + "\n"
                             for row in summaries)
            design = root / "design.spice"
            fab = root / "fab.spice"
            design.write_text(prefix + ".SUBCKT TOP_pex A B SUB\nR1 A B 10\n.ENDS TOP_pex\n")
            fab.write_text(prefix + ".SUBCKT TOP_pex A B SUB\nR1 A B 20\n.ENDS TOP_pex\n")
            comparison = verify.compare_pex_modes(design, fab, "TOP")
        self.assertEqual(comparison["status"], "DIFFERENT")
        self.assertNotEqual(comparison["rc_summary"]["design"],
                            comparison["rc_summary"]["fab"])

    def test_aoi21_strict_physical_pex_and_characterization(self):
        spec = logic.cell_specs()["AOI21_X1"]
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_physical_") as temporary:
            root = Path(temporary)
            layout, top, manifest = geometry.build_cell(spec, 500)
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
            # The assembled postlayout netlist is bound to the configured model
            # (the experimental ideal-FD bridge card, Xyce-scoped), so it is
            # characterized on that engine.  The nominal stage-1/ngspice bench
            # covers the logical references in the other tests.
            binding = model_binding.load()
            if not bench_engine.available(binding["engine"]):
                self.skipTest("postlayout evidence is bound to %s, which is unavailable"
                              % binding["engine"])
            bench = bench_engine.bench_for(binding=binding, engine=binding["engine"],
                                           work=root / "bench")
            # Engine-port evidence on the bound FD card: the translated deck must
            # reproduce the same strict truth-table criteria as the nominal leg
            # (0.9/0.1 VDD per row).  CI without Xyce skips this leg (skipTest above).
            truth = characterize.truth_table(spec, evidence["postlayout_spice"],
                                             root / "truth", logic.evaluate,
                                             bench=bench, strict=False)
            arcs = characterize.characterize_arcs(spec, evidence["postlayout_spice"],
                                                  root / "arcs", logic.evaluate, bench=bench)
            self.assertEqual(len(truth), 8)
            self.assertEqual(len(arcs), 5)
            self.assertTrue(all(math.isfinite(row["voltage_v"][pin])
                                for row in truth for pin in row["expected"]))
            mismatches = [{"inputs": row["inputs"], "expected": row["expected"],
                           "voltage_v": row["voltage_v"]}
                          for row in truth if not row["passed"]]
            self.assertEqual(mismatches, [], "FD-card truth-table mismatch: %s"
                             % json.dumps(mismatches[:2]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
