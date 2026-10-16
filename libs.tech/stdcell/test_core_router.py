#!/usr/bin/env python3
"""Regression tests for native flat-core routing evidence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core_router
import feasibility


PUBLISHED_MANIFEST = (HERE.parents[1] / "libs.ref" / "ICPS2023_5_gen_sc" /
                      "doc" / "cell_manifest.json")


class CoreRouterTests(unittest.TestCase):
    def test_folded_core_physical_checks_and_spice_timeout_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = feasibility._generator().build_candidate_library(
                directory / "library", ["INV_X1", "NAND2_X1"], height_um=180,
                width_um=26, fingers=2, architecture="channel")
            self.assertTrue(all(record["status"] == "strict_physical_pass"
                                for record in candidate["attempts"]), candidate["attempts"])
            cells = [
                {"cell": record["cell"],
                 "contacts_count": record["generator_manifest"]["contacts_count"],
                 "tin_wire_length_um": record["generator_manifest"]["tin_length_um"]}
                for record in candidate["attempts"]]
            route_dir = directory / "core"
            route = core_router.route_core(
                None, route_dir, manifest_path=candidate["manifest_path"])
            self.assertTrue(route["route_proof_passed"], route.get("failure"))
            self.assertFalse(route["rankable"])
            self.assertIsNone(route["routed_core_area_um2"])
            # W26/fingers2 is a legal geometry boundary, not a calibrated
            # transient operating point. Require real strict extraction and
            # preserve every physical finger before exercising failure gates.
            physical = feasibility.verify.verify_physical(
                route["artifacts"]["gds"], route["artifacts"]["reference_spice"],
                route["top_cell"], route_dir / "verification")
            self.assertTrue(physical["passed"], physical)
            self.assertEqual(physical["assembly_stats"]["mos"], 12)
            with patch.object(feasibility.verify, "verify_physical", return_value=physical), \
                    patch.object(feasibility.characterize, "characterize_cell",
                                 side_effect=TimeoutExpired("ngspice", 300)):
                failed_cell = feasibility._cell_evidence(
                    candidate["attempts"][0], directory / "library")
                failed_core = feasibility._routed_evidence(route, route_dir, cells)
            self.assertFalse(failed_cell["postlayout_logic"])
            self.assertFalse(failed_core["postlayout"])
            self.assertIsNone(failed_core["routed_core_area_um2"])
            self.assertFalse(feasibility.metrics.assess_candidate(failed_core)["eligible"])

    def test_bounded_multrow_placement_keeps_supply_bars_in_row_channels(self):
        dbu_um = 0.001
        cells = {
            "DFF_TG": {
                "name": "DFF_TG",
                "dbu_um": dbu_um,
                "width_dbu": 1_254_000,
                "height_dbu": 240_000,
                "spec": {"pins": []},
                "pin_components": {},
                "ml1_component_electrical_ids": [],
                "ml1_box_component_ids": [],
            }
        }
        instances = [
            {"name": f"U{index}", "cell": "DFF_TG", "connections": {}}
            for index in range(4)
        ]
        horizontal = core_router._place_instances(
            instances, cells, 24_000, 500, 32_000, 24_000, None)
        self.assertEqual({item["row_index"] for item in horizontal}, {0})
        placements = core_router._place_instances(
            instances, cells, 24_000, 500, 32_000, 24_000, 1_302_000)
        self.assertEqual([item["row_index"] for item in placements], [0, 1, 2, 3])
        core_height = max(item["y_dbu"] + cells[item["cell"]]["height_dbu"]
                          for item in placements) + 48_000
        for item in placements:
            self.assertGreaterEqual(item["x_dbu"], 24_000)
            self.assertLessEqual(item["x_dbu"] + cells[item["cell"]]["width_dbu"] +
                                 24_000, 1_302_000)
        with self.assertRaises(core_router.CoreRouterError):
            core_router._place_instances(
                instances, cells, 24_000, 500, 32_000, 24_000, 1_301_000)

        segmented = core_router._power_topology_plan(
            "segmented_rails", placements, 1_302_000, core_height, dbu_um, 500)
        self.assertEqual(len(segmented["row_buses"]), 4)
        centers = [
            row["vdd_center_y_dbu"] for row in segmented["row_buses"][:1]
        ] + [segmented["row_buses"][1]["vss_center_y_dbu"]]
        self.assertGreaterEqual((centers[1] - centers[0]) * dbu_um, 10.0)
        too_close = [
            {**placements[0]},
            {**placements[1], "y_dbu": 296_000},
        ]
        with self.assertRaises(core_router.CoreRouterError):
            core_router._power_topology_plan(
                "segmented_rails", too_close, 1_302_000, core_height, dbu_um, 500)
        self.assertTrue(all(
            0 <= left <= right <= 1_302_000 and 0 <= bottom <= top <= core_height
            for _net, (left, bottom, right, top) in segmented["boxes"]))

        clustered = core_router._power_topology_plan(
            "local_clusters", placements, 1_302_000, core_height, dbu_um, 500)
        self.assertEqual(len(clustered["row_buses"]), 4)
        self.assertTrue(all(
            0 <= left <= right <= 1_302_000 and 0 <= bottom <= top <= core_height
            for _net, (left, bottom, right, top) in clustered["boxes"]))

    def test_opposing_row_spacing_evidence_is_in_micrometres(self):
        dbu_um = 0.001
        cells = {
            "DFF_TG": {
                "name": "DFF_TG",
                "dbu_um": dbu_um,
                "width_dbu": 1_254_000,
                "height_dbu": 240_000,
                "spec": {"pins": []},
                "pin_components": {},
                "ml1_component_electrical_ids": [],
                "ml1_box_component_ids": [],
            }
        }
        instances = [
            {"name": f"U{index}", "cell": "DFF_TG", "connections": {}}
            for index in range(2)
        ]
        placements = core_router._place_instances(
            instances, cells, 24_000, 500, 32_000, 24_000, 1_302_000)
        plan = core_router._power_topology_plan(
            "segmented_rails", placements, 1_302_000, 600_000, dbu_um, 500)
        spacing = ((placements[1]["y_dbu"] - 11_000) -
                   (placements[0]["y_dbu"] + 240_000 + 12_000)) * dbu_um
        self.assertEqual(plan["row_buses"][1]["vss_center_y_dbu"], placements[1]["y_dbu"] - 11_000)
        self.assertEqual(spacing, 17.0)

    def test_wrapped_row_origins_snap_to_route_pitch(self):
        cells = {
            "ODD_HEIGHT": {
                "name": "ODD_HEIGHT",
                "dbu_um": 0.001,
                "width_dbu": 1_000,
                "height_dbu": 1_001,
                "spec": {"pins": []},
                "pin_components": {},
                "ml1_component_electrical_ids": [],
                "ml1_box_component_ids": [],
            }
        }
        instances = [
            {"name": f"U{index}", "cell": "ODD_HEIGHT", "connections": {}}
            for index in range(2)
        ]
        placements = core_router._place_instances(
            instances, cells, 0, 500, 0, 0, 1_000)
        self.assertEqual([item["y_dbu"] for item in placements], [0, 41_500])
        self.assertTrue(all(item["y_dbu"] % 500 == 0 for item in placements))

    def test_single_cell_row_power_uses_short_side_taps(self):
        dbu_um = 0.001
        cells = {
            "DFF_TG": {
                "name": "DFF_TG",
                "dbu_um": dbu_um,
                "width_dbu": 1_254_000,
                "height_dbu": 240_000,
                "spec": {"pins": []},
                "pin_components": {},
                "ml1_component_electrical_ids": [],
                "ml1_box_component_ids": [],
            }
        }
        instances = [
            {"name": f"U{index}", "cell": "DFF_TG", "connections": {}}
            for index in range(4)
        ]
        placements = core_router._place_instances(
            instances, cells, 24_000, 500, 32_000, 24_000, 1_302_000)
        core_height = max(item["y_dbu"] + cells[item["cell"]]["height_dbu"]
                          for item in placements) + 48_000
        for item in placements:
            item["pin_anchors"] = {
                "VDD": {"candidate_accesses": [
                    {"window_index": 0, "point_dbu": [8_000, 231_000]}]},
                "VSS": {"candidate_accesses": [
                    {"window_index": 1, "point_dbu": [1_246_000, 87_000]}]},
            }

        segmented = core_router._power_topology_plan(
            "segmented_rails", placements, 1_302_000, core_height, dbu_um, 500)
        self.assertEqual(len(segmented["row_buses"]), 4)
        for row in segmented["row_buses"]:
            for supply in ("vdd_bbox_dbu", "vss_bbox_dbu"):
                width = row[supply][2] - row[supply][0]
                self.assertGreaterEqual(width * dbu_um, 6.0)
                self.assertLess(width, cells["DFF_TG"]["width_dbu"])

        clustered = core_router._power_topology_plan(
            "local_clusters", placements, 1_302_000, core_height, dbu_um, 500)
        self.assertEqual(len(clustered["row_buses"]), 4)
        self.assertTrue(all(
            6_000 <= row["vdd_bbox_dbu"][2] - row["vdd_bbox_dbu"][0] < cells["DFF_TG"]["width_dbu"] and
            6_000 <= row["vss_bbox_dbu"][2] - row["vss_bbox_dbu"][0] < cells["DFF_TG"]["width_dbu"]
            for row in clustered["row_buses"]))

    def test_supply_pins_attach_to_the_planned_trunk_not_previous_pins(self):
        grid = core_router._ObstacleGrid(300_000, 300_000, 0.001, 500)
        first_trunk = (0, 147_000, 120_000, 153_000)
        second_trunk = (180_000, 247_000, 300_000, 253_000)
        grid.add_box(first_trunk, "VDD")
        grid.add_box(second_trunk, "VDD")
        first_point = [20_000, 100_000]
        second_point = [280_000, 100_000]
        for point in (first_point, second_point):
            grid.add_box((point[0] - 3_000, point[1] - 3_000,
                          point[0] + 3_000, point[1] + 3_000), "VDD")
        grid.sort_rows()
        anchors = [
            {"kind": "top_level_net_endpoint", "point_dbu": [60_000, 150_000],
             "candidate_accesses": [{"point_dbu": [60_000, 150_000]}]},
            {"kind": "pdn_geometry", "point_dbu": [60_000, 150_000],
             "box_dbu": list(first_trunk), "trunk_instances": ["U0"]},
            {"kind": "pdn_geometry", "point_dbu": [240_000, 250_000],
             "box_dbu": list(second_trunk), "trunk_instances": ["U1"]},
            {"kind": "cell_pin", "instance": "U0", "candidate_accesses": [
                {"point_dbu": first_point, "window_index": 0}]},
            {"kind": "cell_pin", "instance": "U1", "candidate_accesses": [
                {"point_dbu": second_point, "window_index": 0}]},
        ]

        route = core_router._connect_power_net(grid, anchors, "VDD", 500, 3_000)
        self.assertTrue(route["passed"], route)
        attachments = [path for path in route["paths"]
                       if path["target_anchor_index"] >= 3]
        for path in attachments:
            bounds = first_trunk if path["target_anchor_index"] == 3 else second_trunk
            x, y = path["from_dbu"]
            self.assertTrue(bounds[0] + 3_000 <= x <= bounds[2] - 3_000)
            self.assertTrue(bounds[1] + 3_000 <= y <= bounds[3] - 3_000)
        metal = core_router.db.Region()
        for bounds in (first_trunk, second_trunk):
            metal.insert(core_router.db.Box(*bounds))
        for x, y in (first_point, second_point):
            metal.insert(core_router.db.Box(x - 3_000, y - 3_000,
                                           x + 3_000, y + 3_000))
        for path in route["paths"]:
            points = [tuple(point) for point in path["points_dbu"]]
            for first, second in zip(points, points[1:]):
                metal.insert(core_router.db.Box(*core_router._wire_box(
                    first, second, 3_000)))
        self.assertEqual(len(list(metal.merged().each())), 1)

    def test_perimeter_alternatives_do_not_disconnect_a_fanout_tree(self):
        grid = core_router._ObstacleGrid(40, 40, 1.0, 1)
        anchors = [
            {"kind": "cell_pin", "point_dbu": [10, 10]},
            {"kind": "top_level_net_endpoint", "point_dbu": [3, 10],
             "candidate_accesses": [{"point_dbu": [3, 10]}, {"point_dbu": [3, 30]}]},
            {"kind": "cell_pin", "point_dbu": [10, 30]},
        ]
        route = core_router._connect_net(grid, anchors, "FANOUT")
        self.assertTrue(route["passed"], route)
        metal = core_router.db.Region()
        for x, y in ((10, 10), (10, 30)):
            metal.insert(core_router.db.Box(x - 3, y - 3, x + 3, y + 3))
        for path in route["paths"]:
            points = [tuple(point) for point in path["points_dbu"]]
            for first, second in zip(points, points[1:]):
                metal.insert(core_router.db.Box(*core_router._wire_box(first, second, 3)))
        self.assertEqual(len(list(metal.merged().each())), 1)

    def test_tin_ml1_connectivity_requires_native_off_active_contact(self):
        db = core_router.db
        metals = {(8, 0): [db.Box(0, 0, 20000, 10000)],
                  (5, 0): [db.Box(0, 0, 10000, 20000)]}
        valid = db.Box(2500, 2500, 7500, 7500)
        cases = [
            ({}, 2),
            ({(7, 0): [db.Box(2500, 2500, 6500, 7500)]}, 2),
            ({(7, 0): [db.Box(0, 0, 5000, 5000)]}, 2),
            ({(7, 0): [valid]}, 1),
            ({(7, 0): [valid], (3, 0): [db.Box(0, 0, 10000, 10000)]}, 2),
        ]
        for additional, expected_components in cases:
            with self.subTest(additional=additional):
                graph = core_router._electrical_components({**metals, **additional}, .001)
                self.assertEqual(graph["electrical_component_count"], expected_components)

    def test_centerline_union_does_not_double_count_partial_overlaps(self):
        edges = {((0, 0), (10, 0)), ((5, 0), (15, 0)),
                 ((15, 0), (15, 5)), ((15, 2), (15, 8)),
                 ((0, 1), (10, 1))}
        self.assertEqual(core_router._centerline_length_dbu(edges), 33)

    def test_crossing_evidence_handles_endpoints_overlaps_and_reversed_segments(self):
        horizontal = ((0, 5), (10, 5))
        for other, expected in (
                (((5, 0), (5, 10)), (5, 5)),
                (((10, 5), (10, 15)), (10, 5)),
                (((12, 5), (7, 5)), (7, 5)),
                (((11, 0), (11, 10)), None),
                (((0, 6), (10, 6)), None)):
            with self.subTest(other=other):
                self.assertEqual(core_router._segments_cross(horizontal, other), expected)
                self.assertEqual(core_router._segments_cross(other, horizontal), expected)
        with self.assertRaises(core_router.CoreRouterError):
            core_router._segments_cross(horizontal, ((0, 0), (10, 10)))

    def test_invalid_candidate_keeps_nonrankable_machine_evidence(self):
        design = core_router.build_tiny_benchmark(PUBLISHED_MANIFEST)
        design["ports"]["UNCONNECTED"] = "input"
        with tempfile.TemporaryDirectory() as directory:
            report = core_router.route_core(
                design, directory, manifest_path=PUBLISHED_MANIFEST)
            self.assertFalse(report["rankable"])
            self.assertTrue(report["failed"])
            self.assertEqual(report["status"], "failed")
            self.assertIsNotNone(report["failure"])
            self.assertGreater(report["overflow_count"], 0)
            record = feasibility._routed_evidence(report, Path(directory), [])
            self.assertIsNone(record["routed_core_area_um2"])
            self.assertFalse(feasibility.metrics.assess_candidate(record)["eligible"])
            evidence_path = Path(report["artifacts"]["route_evidence"])
            self.assertTrue(evidence_path.is_file())
            with evidence_path.open(encoding="utf-8") as stream:
                persisted = json.load(stream)
            self.assertFalse(persisted["rankable"])
            self.assertTrue(persisted["failure"]["error"])


if __name__ == "__main__":  # pragma: no cover - direct test invocation
    unittest.main()
