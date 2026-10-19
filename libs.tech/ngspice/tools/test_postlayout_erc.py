#!/usr/bin/env python3
"""Focused actual-GDS regressions for post-layout electrical partitioning."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import klayout.db as db

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_postlayout_erc as erc


class PostlayoutERCTopologyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _write_gds(self, layer_mode: str, scenario: str, polarity: str = "NMOS",
                   exclusion: str | None = None, cut_half: float = 2.5,
                   ml1_half: float = 4.5) -> Path:
        layers = erc.DESIGN_LAYERS if layer_mode == "design" else erc.FAB_LAYERS
        layout = db.Layout()
        layout.dbu = 0.001
        for layer, datatype in sorted(set(layers.values())):
            layout.layer(layer, datatype)
        top = layout.create_cell("TOP")

        def add(layer_name: str, x1: float, y1: float, x2: float, y2: float):
            values = [int(round(value / layout.dbu)) for value in (x1, y1, x2, y2)]
            top.shapes(layout.layer(*layers[layer_name])).insert(db.Box(*values))

        def contact(center_x: float):
            add("CNT", center_x - cut_half, 10 - cut_half,
                center_x + cut_half, 10 + cut_half)
            add("ML1", center_x - ml1_half, 10 - ml1_half,
                center_x + ml1_half, 10 + ml1_half)

        top.shapes(layout.layer(*layers["TEXT"])).insert(
            db.Text("ERC", db.Trans(db.Point(100_000, 100_000)))
        )
        add("DIFF", 0, 0, 40, 20)
        implant = "NAREA" if polarity == "NMOS" else "PAREA"
        opposite = "PAREA" if polarity == "NMOS" else "NAREA"
        if scenario == "split":
            if layer_mode == "design":
                add(implant, 0, 0, 40, 20)
                add(opposite, 100, 100, 102, 102)
                add("TIN", 17, 0, 23, 20)
            else:
                add(implant, -2, -2, 17, 22)
                add(implant, 23, -2, 42, 22)
                add(opposite, 100, 100, 102, 102)
                add("TIN", 15, 0, 25, 20)
        elif scenario == "shared":
            if layer_mode == "design":
                add(implant, 0, 0, 40, 20)
                add(opposite, 100, 100, 102, 102)
            else:
                add(implant, -2, -2, 42, 22)
                add(opposite, 100, 100, 102, 102)
            add("TIN", 15, 5, 25, 15)
            add("CAP", 15, 5, 25, 15)
        elif scenario == "res_body":
            if layer_mode == "design":
                add(implant, 0, 0, 40, 20)
            else:
                add(implant, -2, -2, 42, 22)
            add(opposite, 100, 100, 102, 102)
            add("TIN", 100, 100, 120, 120)
            add("RES", 17, 0, 23, 20)
        elif scenario == "missing":
            add("NAREA", 100, 100, 102, 102)
            add("PAREA", 104, 100, 106, 102)
            add("TIN", 100, 100, 120, 120)
        elif scenario == "pn_boundary":
            if layer_mode == "design":
                add("NAREA", 0, 0, 18, 20)
                add("PAREA", 22, 0, 40, 20)
            else:
                add("NAREA", -2, -2, 20, 22)
                add("PAREA", 20, -2, 42, 22)
            add("TIN", 100, 100, 120, 120)
            add("DIO", 0, 0, 40, 20)
        else:
            raise AssertionError("unknown test fixture %r" % scenario)

        for center_x in ((10, 30, 20) if scenario == "pn_boundary" else (10, 30)):
            contact(center_x)
        if exclusion is not None:
            add(exclusion, 5.5, 5.5, 14.5, 14.5)

        path = self.root / ("%s_%s_%s.gds" % (layer_mode, scenario, polarity.lower()))
        layout.write(str(path))
        return path

    def _geometry(self, layer_mode: str, scenario: str, polarity: str = "NMOS",
                  exclusion: str | None = None, cut_half: float = 2.5):
        path = self._write_gds(layer_mode, scenario, polarity, exclusion, cut_half)
        return path, erc._layout_inputs(path, "TOP", None, layer_mode=layer_mode)

    @staticmethod
    def _anchor(geometry, x: float):
        return erc._physical_anchor(
            {"kind": "mos_terminal", "layer": "ML1", "point_um": [x, 10]},
            geometry,
            None,
        )

    def test_contact_policy_matches_the_drc_policy_it_claims(self):
        for layer_mode in ("design", "fab"):
            with self.subTest(layer_mode=layer_mode):
                path = self._write_gds(layer_mode, "split", cut_half=2.0)
                strict = erc._layout_inputs(path, "TOP", None, layer_mode=layer_mode)
                legacy = erc._layout_inputs(path, "TOP", None, layer_mode=layer_mode,
                                            contact_policy="legacy")
                self.assertEqual([c["status"] for c in strict["contacts"]],
                                 ["illegal", "illegal"])
                self.assertTrue(all("exact 5x5" in " ".join(c["reason"])
                                    for c in strict["contacts"]), strict["contacts"])
                self.assertEqual([c["status"] for c in legacy["contacts"]],
                                 ["legal", "legal"], legacy["contacts"])
                self.assertEqual(legacy["contact_policy"], "legacy")
                with self.assertRaisesRegex(erc.PostlayoutERCError, "contact_policy"):
                    erc._layout_inputs(path, "TOP", None, layer_mode=layer_mode,
                                       contact_policy="loose")
                # A cut beyond the DRC perimeter limit stays illegal even in
                # the legacy policy; ML1 is widened so only that limit fires.
                wide = self._write_gds(layer_mode, "split", cut_half=3.0,
                                       ml1_half=6.0)
                wide_legacy = erc._layout_inputs(wide, "TOP", None,
                                                 layer_mode=layer_mode,
                                                 contact_policy="legacy")
                self.assertEqual([c["status"] for c in wide_legacy["contacts"]],
                                 ["illegal", "illegal"], wide_legacy["contacts"])

    def test_nmos_and_pmos_two_contacts_on_one_island_remain_separate(self):
        for layer_mode in ("design", "fab"):
            for polarity in ("NMOS", "PMOS"):
                with self.subTest(layer_mode=layer_mode, polarity=polarity):
                    _, geometry = self._geometry(layer_mode, "split", polarity)
                    self.assertTrue(geometry["required_layers_present"])
                    self.assertEqual([item["status"] for item in geometry["contacts"]], ["legal", "legal"])
                    source = self._anchor(geometry, 10)
                    drain = self._anchor(geometry, 30)
                    self.assertEqual(source["status"], "PASS", source)
                    self.assertEqual(drain["status"], "PASS", drain)
                    self.assertNotEqual(source["component_id"], drain["component_id"])
                    channel = erc._physical_anchor(
                        {"kind": "pin", "layer": "DIFF", "point_um": [20, 10]},
                        geometry,
                        None,
                    )
                    self.assertEqual(channel["status"], "BLOCKED", channel)
                    if polarity == "PMOS":
                        body = erc._physical_anchor(
                            {"kind": "body_contact", "layer": "PPLUS", "point_um": [10, 10]},
                            geometry,
                            None,
                        )
                        self.assertEqual(body["status"], "BLOCKED", body)
                        self.assertTrue(body["unqualified_boundary"])
                    self.assertEqual(geometry["layer_mode"], layer_mode)
                    self.assertEqual(geometry["contact_policy"], "strict")
                    if layer_mode == "design":
                        self.assertEqual(geometry["resolved_layers"]["DIFF"], [3, 0])
                        self.assertEqual(geometry["resolved_layers"]["CNT"], [7, 0])
                    else:
                        self.assertEqual(geometry["resolved_layers"]["DIFF"], [4, 0])
                        self.assertEqual(geometry["resolved_layers"]["CNT"], [6, 0])

    def test_shared_same_polarity_diffusion_stays_connected_through_cap(self):
        for layer_mode in ("design", "fab"):
            for polarity in ("NMOS", "PMOS"):
                with self.subTest(layer_mode=layer_mode, polarity=polarity):
                    _, geometry = self._geometry(layer_mode, "shared", polarity)
                    self.assertEqual([item["status"] for item in geometry["contacts"]], ["legal", "legal"])
                    left = self._anchor(geometry, 10)
                    right = self._anchor(geometry, 30)
                    self.assertEqual(left["status"], "PASS", left)
                    self.assertEqual(right["status"], "PASS", right)
                    self.assertEqual(left["component_id"], right["component_id"])
                    plate = erc._physical_anchor(
                        {"kind": "pin", "layer": "TIN", "point_um": [20, 10]},
                        geometry,
                        None,
                    )
                    self.assertEqual(plate["status"], "PASS", plate)
                    self.assertNotEqual(plate["component_id"], left["component_id"])


    def test_res_marker_breaks_the_ohmic_domain_instead_of_shorting_it(self):
        for layer_mode in ("design", "fab"):
            for polarity in ("NMOS", "PMOS"):
                with self.subTest(layer_mode=layer_mode, polarity=polarity):
                    _, geometry = self._geometry(layer_mode, "res_body", polarity)
                    self.assertEqual([item["status"] for item in geometry["contacts"]], ["legal", "legal"])
                    left = self._anchor(geometry, 10)
                    right = self._anchor(geometry, 30)
                    self.assertEqual(left["status"], "PASS", left)
                    self.assertEqual(right["status"], "PASS", right)
                    self.assertNotEqual(left["component_id"], right["component_id"])

    def test_missing_implant_and_mixed_pn_contact_never_create_edges(self):
        for layer_mode in ("design", "fab"):
            with self.subTest(layer_mode=layer_mode, case="missing-implant"):
                _, geometry = self._geometry(layer_mode, "missing")
                self.assertTrue(all(item["status"] == "illegal" for item in geometry["contacts"]))
                left = self._anchor(geometry, 10)
                right = self._anchor(geometry, 30)
                self.assertEqual(left["status"], "PASS", left)
                self.assertEqual(right["status"], "PASS", right)
                self.assertNotEqual(left["component_id"], right["component_id"])
            with self.subTest(layer_mode=layer_mode, case="pn-boundary"):
                _, geometry = self._geometry(layer_mode, "pn_boundary")
                illegal = [item for item in geometry["contacts"] if item["status"] == "illegal"]
                self.assertEqual(len(illegal), 1, geometry["contacts"])
                self.assertEqual(illegal[0]["effective_ohmic_domains"], ["NPLUS", "PPLUS"])
                left = self._anchor(geometry, 10)
                mixed = self._anchor(geometry, 20)
                right = self._anchor(geometry, 30)
                self.assertEqual(left["status"], "PASS", left)
                self.assertEqual(mixed["status"], "PASS", mixed)
                self.assertEqual(right["status"], "PASS", right)
                self.assertNotEqual(left["component_id"], right["component_id"])
                self.assertNotIn(mixed["component_id"], {left["component_id"], right["component_id"]})

    def test_mask_and_exclude_remove_all_overlaid_contact_geometry(self):
        for exclusion in ("MASK", "EXCLUDE"):
            with self.subTest(exclusion=exclusion):
                _, geometry = self._geometry("design", "shared", exclusion=exclusion)
                self.assertEqual(len(geometry["contacts"]), 1)
                self.assertEqual(geometry["contacts"][0]["status"], "legal")
                self.assertEqual(self._anchor(geometry, 10)["status"], "FAIL")
                self.assertEqual(self._anchor(geometry, 30)["status"], "PASS")

    def test_layer_mode_and_overrides_fail_closed_without_guessing(self):
        path = self._write_gds("design", "split")
        with self.assertRaises(TypeError):
            erc._layout_inputs(path, "TOP", None)
        with self.assertRaises(erc.PostlayoutERCError):
            erc._layout_inputs(path, "TOP", None, layer_mode="unknown")
        with self.assertRaises(erc.PostlayoutERCError):
            erc._layout_inputs(path, "TOP", {"DIFF": [4, 0]}, layer_mode="design")
        with self.assertRaises(erc.PostlayoutERCError):
            erc._layout_inputs(path, "TOP", {"DIFF": [200, 0], "NAREA": [3, 0]}, layer_mode="design")
        with self.assertRaises(erc.PostlayoutERCError):
            erc._layout_inputs(
                path, "TOP", {"DIFF": [200, 0], "NAREA": [200, 0]}, layer_mode="design"
            )
        with self.assertRaises(erc.PostlayoutERCError):
            erc._layout_inputs(path, "TOP", {"NAREA": [21, 0]}, layer_mode="design")
        geometry = erc._layout_inputs(path, "TOP", {"PAD": [200, 0]}, layer_mode="design")
        self.assertEqual(geometry["resolved_layers"]["PAD"], [200, 0])

        blocked = erc.run_erc({"gds": str(path)})
        self.assertIsNone(blocked["layer_mode"])
        self.assertEqual(blocked["checks"]["gds_geometry"]["status"], "BLOCKED")
        self.assertIn("layer_mode", blocked["checks"]["gds_geometry"]["error"])


    def test_unsupported_assembled_pmos_body_terminal_remains_blocked(self):
        assembled = {
            "mos": [{
                "name": "MP1",
                "layout_device": "PMOS@10.000000,10.000000",
                "body_state": "body_tied",
                "e": "SUB",
                "p": "P_BODY",
                "model": "pch",
            }],
            "ports": [],
        }
        body, _ = erc._body_checks({}, {"assembled": assembled}, None, {})
        self.assertEqual(body["status"], "BLOCKED")
        self.assertEqual(body["devices"][0]["status"], "BLOCKED")

    def test_cli_reports_explicit_mapping_and_unqualified_boundary(self):
        path = self._write_gds("fab", "split", "PMOS")
        config = self.root / "erc.json"
        report_path = self.root / "erc-report.json"
        config.write_text(json.dumps({
            "gds": str(path), "layer_mode": "fab", "layers": {"PAD": [200, 0]}
        }), encoding="utf-8")
        status = erc.main(["--config", str(config), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(status, 2)  # netlist inputs are intentionally absent
        self.assertEqual(report["layer_mode"], "fab")
        self.assertEqual(report["inputs"]["gds"]["resolved_layers"]["ML1"], [7, 0])
        self.assertEqual(report["inputs"]["gds"]["resolved_layers"]["PAD"], [200, 0])
        self.assertEqual(report["checks"]["gds_geometry"]["status"], "PASS")
        self.assertTrue(any("local P+ contact alone" in item for item in report["limitations"]))

    def test_unsupported_upper_metal_blocks_anchor_qualification(self):
        path = self._write_gds("design", "shared")
        # Add actual upper-metal geometry to this GDS rather than relying on a layer table entry.
        layout = db.Layout()
        layout.read(str(path))
        top = layout.cell("TOP")
        top.shapes(layout.layer(*erc.DESIGN_LAYERS["ML2"])).insert(db.Box(0, 0, 2000, 2000))
        layout.write(str(path))
        geometry = erc._layout_inputs(path, "TOP", None, layer_mode="design")
        self.assertEqual(geometry["unsupported_metal_layers"], ["ML2"])
        self.assertEqual(erc._contact_check(geometry)["status"], "BLOCKED")
        self.assertEqual(self._anchor(geometry, 10)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
