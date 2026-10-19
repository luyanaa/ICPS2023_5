#!/usr/bin/env python3
"""Physical native-PCell regressions for TiN/SOI edge protection."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
from xml.etree import ElementTree

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
DRC_DECK = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
ONE_METAL = "icps2023_5_1m"
sys.path.insert(0, str(ROOT / "libs.tech/stdcell"))
import geometry as stdcell_geometry
DBU = 0.001
# Nch PCell defaults (its `w` default; the reference deck clamps W >= 13 um).
MOS_WIDTH = 20.0
MOS_LENGTH = 6.0
CONTACT_SIZE = 13.0
GATE_BAY = 8.0
GATE_PITCH = CONTACT_SIZE + MOS_LENGTH + 2.0 * GATE_BAY
GATE_CENTER_X = CONTACT_SIZE + GATE_BAY + MOS_LENGTH / 2.0


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return result


def um(value):
    return int(round(value / DBU))


def box_um(x1, y1, x2, y2):
    return pya.Box(*(um(value) for value in (x1, y1, x2, y2)))


def drc_categories(report_path):
    items = ElementTree.parse(report_path).getroot().find("items")
    if items is None:
        raise AssertionError("DRC report has no items element: %s" % report_path)
    # The RDB stores the description quoted; strip it so the rule-id prefixes
    # below match.  Emptiness checks do not care, but prefix assertions do.
    return [item.findtext("category", default="").strip().strip("'")
            for item in items.findall("item")]


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for physical edge tests")
class SoiEdgeProtectionPhysicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        cls.generated = cls.work / "generated"
        cls.generated.mkdir()
        result = subprocess.run(
            [
                KLAYOUT,
                "-b",
                "-r",
                str(GENERATOR),
                "-rd",
                "output_dir=" + str(cls.generated),
                "-rd",
                "pcell_macro=" + str(PCELL_MACRO),
            ],
            text=True,
            capture_output=True,
            env=environment(),
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        for name in (
            "nmos_min",
            "pmos_min",
            "edge_protected_multifinger",
            "edge_protected_no_legacy_switches",
            "edge_protected_ncap",
            "edge_protected_pcap",
            "edge_unprotected_diagnostic",
        ):
            if not (cls.generated / (name + ".gds")).is_file():
                raise AssertionError("native generator omitted " + name + ":\n" + result.stdout)

    def test_native_diff_cap_tin_crossings_have_strict_cnt_enclosure(self):
        for name in ("edge_protected_ncap", "edge_protected_pcap"):
            with self.subTest(cell=name):
                markers, details = self.run_drc(
                    self.generated / (name + ".gds"), name,
                    edge_redundancy=True, strict=True,
                )
                bad_enclosure = [
                    item for item in markers if item.startswith("CNT.m5")
                ]
                self.assertEqual(
                    bad_enclosure, [], details + "\n" + "\n".join(bad_enclosure)
                )


    def test_native_diff_cap_tin_crossings_are_covered_not_excluded(self):
        for name in ("edge_protected_ncap", "edge_protected_pcap"):
            with self.subTest(cell=name):
                markers, details = self.run_drc(
                    self.generated / (name + ".gds"), name
                )
                edge_markers = [
                    item for item in markers if item.startswith("TIN.SOIEDGE")
                ]
                self.assertEqual(
                    edge_markers, [], details + "\n" + "\n".join(edge_markers)
                )

    def test_nearby_non_crossing_tin_arms_are_not_edge_coverage_targets(self):
        for layer_mode in ("design", "fab"):
            with self.subTest(layer_mode=layer_mode):
                fixture = self.write_bent_tin_fixture(
                    self.work / ("bent_tin_non_crossing_arm_" + layer_mode + ".gds"),
                    layer_mode,
                )
                markers, details = self.run_drc(
                    fixture, "bent_tin_non_crossing_arm", layer_mode=layer_mode
                )
                false_edge_markers = [
                    item for item in markers if item.startswith("TIN.SOIEDGE")
                ]
                self.assertEqual(
                    false_edge_markers, [], details + "\n" + "\n".join(false_edge_markers)
                )

    def test_non_mos_crossing_coverage_matches_design_and_fab_tin_bias(self):
        for layer_mode in ("design", "fab"):
            with self.subTest(layer_mode=layer_mode):
                covered = self.write_tin_crossing_fixture(
                    self.work / ("non_mos_crossing_" + layer_mode + ".gds"),
                    layer_mode,
                    covered=True,
                )
                markers, details = self.run_drc(
                    covered, "non_mos_tin_crossing", layer_mode=layer_mode
                )
                self.assertFalse(
                    any(item.startswith("TIN.SOIEDGE") for item in markers),
                    details + "\n" + "\n".join(markers),
                )

                uncovered = self.write_tin_crossing_fixture(
                    self.work / ("uncovered_non_mos_crossing_" + layer_mode + ".gds"),
                    layer_mode,
                    covered=False,
                )
                markers, details = self.run_drc(
                    uncovered, "non_mos_tin_crossing", layer_mode=layer_mode
                )
                self.assertTrue(
                    any(item.startswith("TIN.SOIEDGE") for item in markers),
                    details + "\n" + "\n".join(markers),
                )
    def test_connected_off_island_arm_does_not_extend_crossing_target(self):
        def fixture(path, covered):
            layout = pya.Layout()
            layout.dbu = DBU
            top = layout.create_cell("connected_tin_arm")
            self.add_area(layout, top, (4, 0), (0.0, 0.0, 40.0, 40.0))
            self.add_area(layout, top, (5, 0), (10.0, -10.0, 16.0, 10.0))
            self.add_area(layout, top, (5, 0), (14.0, -7.0, 36.0, -1.0))
            if covered:
                self.add_area(layout, top, (7, 0), (8.0, -4.0, 18.0, 4.0))
            layout.write(str(path))
            return path

        covered = fixture(self.work / "connected_arm_covered.gds", True)
        markers, details = self.run_drc(
            covered, "connected_tin_arm", layer_mode="fab"
        )
        self.assertFalse(
            any(item.startswith("TIN.SOIEDGE") for item in markers),
            details + "\n" + "\n".join(markers),
        )

        uncovered = fixture(self.work / "connected_arm_uncovered.gds", False)
        markers, details = self.run_drc(
            uncovered, "connected_tin_arm", layer_mode="fab"
        )
        self.assertTrue(
            any(item.startswith("TIN.SOIEDGE") for item in markers), details
        )


    def test_experimental_margin_rejects_values_below_two_microns(self):
        fixture = self.write_tin_crossing_fixture(
            self.work / "invalid_margin.gds", "fab", covered=True
        )
        for margin in ("1.99", "nan", "not-a-number"):
            with self.subTest(margin=margin):
                _markers, details = self.run_drc(
                    fixture,
                    "non_mos_tin_crossing",
                    layer_mode="fab",
                    soi_edge_margin=margin,
                    expected_success=False,
                )
                self.assertIn("soi_edge_margin", details)

    def test_strict_mode_automatically_checks_dual_edge_bypass(self):
        missing_contact = self.work / "strict_missing_gate_endpoint.gds"
        self.copy_flattened(
            "nmos_min",
            missing_contact,
            lambda layout, top: self.remove_area(
                layout, top, (7, 0),
                (GATE_CENTER_X - 2.5, -14.0, GATE_CENTER_X + 2.5, -9.0),
            ),
        )
        markers, details = self.run_drc(missing_contact, "nmos_min", strict=True)
        self.assertTrue(
            any(item.startswith("TIN.SOIBYPASS") for item in markers), details
        )

    @classmethod
    def tearDownClass(cls):
        cls._tempdir.cleanup()

    def copy_and_flatten(self, source_path, top_name, destination, mutate=None):
        layout = pya.Layout()
        layout.read(str(source_path))
        top = layout.cell(top_name)
        self.assertIsNotNone(top, "missing top cell " + top_name)
        top.flatten(-1)
        if mutate is not None:
            mutate(layout, top)
        layout.write(str(destination))
        return destination

    def copy_flattened(self, source_name, destination, mutate=None):
        return self.copy_and_flatten(
            self.generated / (source_name + ".gds"),
            source_name,
            destination,
            mutate,
        )

    def edit_region(self, layout, top, layer_spec, update):
        layer = layout.layer(pya.LayerInfo(*layer_spec))
        shapes = top.shapes(layer)
        region = pya.Region(shapes)
        update(region)
        shapes.clear()
        for polygon in region.each():
            shapes.insert(polygon)

    def add_area(self, layout, top, layer_spec, bounds):
        layer = layout.layer(pya.LayerInfo(*layer_spec))
        top.shapes(layer).insert(box_um(*bounds))

    def write_bent_tin_fixture(self, destination, layer_mode="fab"):
        layout = pya.Layout()
        layout.dbu = DBU
        top = layout.create_cell("bent_tin_non_crossing_arm")
        diff_layer = (3, 0) if layer_mode == "design" else (4, 0)
        self.add_area(layout, top, diff_layer, (0.0, 0.0, 40.0, 40.0))
        tin_bounds = (
            ((10.0, 3.0, 36.0, 9.0), (10.0, -9.0, 36.0, -3.0))
            if layer_mode == "design"
            else ((10.0, 1.0, 36.0, 7.0), (10.0, -7.0, 36.0, -1.0))
        )
        for bounds in tin_bounds:
            self.add_area(layout, top, (5, 0), bounds)
        layout.write(str(destination))
        return destination

    def write_tin_crossing_fixture(self, destination, layer_mode, covered):
        layout = pya.Layout()
        layout.dbu = DBU
        top = layout.create_cell("non_mos_tin_crossing")
        diff_layer = (3, 0) if layer_mode == "design" else (4, 0)
        ml1_layer = (8, 0) if layer_mode == "design" else (7, 0)
        tin_bounds = (
            (10.0, -5.0, 16.0, 5.0) if layer_mode == "design"
            else (8.0, -5.0, 18.0, 7.0)
        )
        self.add_area(layout, top, diff_layer, (0.0, 0.0, 40.0, 40.0))
        self.add_area(layout, top, (5, 0), tin_bounds)
        if covered:
            self.add_area(layout, top, ml1_layer, (6.0, -4.0, 20.0, 4.0))
        layout.write(str(destination))
        return destination

    def remove_area(self, layout, top, layer_spec, bounds):
        cut = pya.Region(box_um(*bounds))
        self.edit_region(
            layout,
            top,
            layer_spec,
            lambda region: region.__isub__(cut),
        )

    def run_drc(
        self, layout_path, top_name, layer_mode="design",
        edge_redundancy=False, strict=False, soi_edge_margin=None,
        expected_success=True,
    ):
        report = self.work / (layout_path.stem + "_" + layer_mode + ".lyrdb")
        command = [
            KLAYOUT,
            "-b",
            "-r",
            str(DRC_DECK),
            "-rd",
            "input=" + str(layout_path),
            "-rd",
            "top=" + top_name,
            "-rd",
            "layer_mode=" + layer_mode,
            "-rd",
            "report=" + str(report),
            "-rd",
            "process_profile=" + ONE_METAL,
        ]
        if strict:
            command.extend(["-rd", "strict=true"])
        if edge_redundancy:
            command.extend(["-rd", "edge_redundancy=true"])
        if soi_edge_margin is not None:
            command.extend(["-rd", "soi_edge_margin=" + str(soi_edge_margin)])
        result = subprocess.run(
            command, text=True, capture_output=True, env=environment()
        )
        details = result.stdout + result.stderr
        if not expected_success:
            self.assertNotEqual(result.returncode, 0, details)
            return [], details
        self.assertEqual(result.returncode, 0, details)
        self.assertTrue(report.is_file(), details)
        return drc_categories(report), details

    def prepare_fab(self, layout_path, top_name):
        output = self.work / (layout_path.stem + "_fab.gds")
        result = subprocess.run(
            [
                KLAYOUT,
                "-b",
                "-r",
                str(FAB_PREP),
                "-rd",
                "input=" + str(layout_path),
                "-rd",
                "output=" + str(output),
                "-rd",
                "process_profile=" + ONE_METAL,
            ],
            text=True,
            capture_output=True,
            env=environment(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(output.is_file(), result.stdout + result.stderr)
        return output

    def write_bridge_reference(self, path, resistance=None):
        reference = (
            ".GLOBAL SUB\n"
            ".SUBCKT bridge_tin_min START END\n"
        )
        if resistance is not None:
            reference += "R1 START END %g\n" % resistance
        reference += (
            ".ENDS bridge_tin_min\n"
            "XTOP START END bridge_tin_min\n"
            ".END\n"
        )
        path.write_text(reference)

    def run_lvs(
        self, layout_path, top_name, layer_mode="design", schematic=None
    ):
        if schematic is None:
            schematic = self.work / (layout_path.stem + ".cir")
            schematic.write_text(
                "* Native Nch reference\n"
                ".GLOBAL SUB\n"
                ".SUBCKT nmos_min D G S\n"
                "MN D G S SUB nch L=%gU W=%gU\n" % (MOS_LENGTH, MOS_WIDTH)
                + ".ENDS nmos_min\n"
                "XTOP D G S nmos_min\n"
                ".END\n"
            )
        report = self.work / (layout_path.stem + ".lvs.lyrdb")
        extracted = self.work / (layout_path.stem + ".extracted.cir")
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
                "schematic=" + str(schematic),
                "-rd",
                "report=" + str(report),
                "-rd",
                "output=" + str(extracted),
                "-rd",
                "layer_mode=" + layer_mode,
                "-rd",
                "process_profile=" + ONE_METAL,
            ],
            text=True,
            capture_output=True,
            env=environment(),
        )
        details = result.stdout + result.stderr
        if extracted.is_file():
            details += "\n" + extracted.read_text()
        if report.is_file():
            details += "\n" + report.read_text()
        self.assertEqual(result.returncode, 0, details)
        return details

    def test_bent_native_bridge_extracts_and_compares_its_resistance(self):
        bridge = self.generated / "bridge_tin_min.gds"
        empty_reference = self.work / "bridge_empty.cir"
        self.write_bridge_reference(empty_reference)
        empty_details = self.run_lvs(
            bridge, "bridge_tin_min", schematic=empty_reference
        )
        self.assertIn("ICPS2023_5 LVS NO MATCH", empty_details)

        wrong_reference = self.work / "bridge_wrong_resistance.cir"
        self.write_bridge_reference(wrong_reference, resistance=100.0)
        wrong_details = self.run_lvs(
            bridge, "bridge_tin_min", schematic=wrong_reference
        )
        self.assertIn("ICPS2023_5 LVS NO MATCH", wrong_details)

        correct_reference = self.work / "bridge_correct_resistance.cir"
        self.write_bridge_reference(correct_reference, resistance=90.0)
        correct_details = self.run_lvs(
            bridge, "bridge_tin_min", schematic=correct_reference
        )
        self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", correct_details)

        extracted = self.work / "bridge_tin_min.extracted.cir"
        records = [
            json.loads(line.split("ICPS2023_5_R_TIN ", 1)[1])
            for line in extracted.read_text().splitlines()
            if line.startswith("* ICPS2023_5_R_TIN ")
        ]
        self.assertEqual(len(records), 1, correct_details)
        self.assertAlmostEqual(records[0]["r_ohm"], 90.0, delta=0.1)
        names = dict(line.split()[2:4] for line in extracted.read_text().splitlines()
                     if line.startswith("* net "))
        self.assertEqual({names[records[0][terminal]] for terminal in ("a", "b")},
                         {"START", "END"})

    def test_protected_nmos_pmos_multifinger_and_legacy_switch_independence(self):
        for name in (
            "nmos_min",
            "pmos_min",
            "edge_protected_multifinger",
            "edge_protected_no_legacy_switches",
        ):
            with self.subTest(cell=name):
                markers, details = self.run_drc(
                    self.generated / (name + ".gds"), name,
                    edge_redundancy=True,
                )
                self.assertEqual(markers, [], details + "\n" + "\n".join(markers))

    def test_fab_prepared_mos_passes_strict_edge_bypass_rules(self):
        for name in ("nmos_min", "pmos_min"):
            with self.subTest(cell=name):
                prepared = self.prepare_fab(
                    self.generated / (name + ".gds"), name
                )
                if name == "nmos_min":
                    checker_layout = pya.Layout()
                    checker_layout.read(str(prepared))
                    checker_top = checker_layout.cell(name)
                    checker_top.flatten(-1)
                    checked = stdcell_geometry.soi_edge_protection(
                        checker_layout, checker_top, layer_mode="fab"
                    )
                    self.assertEqual(checked["gate_count"], 1)
                    self.remove_area(
                        checker_layout, checker_top, (6, 0),
                        (GATE_CENTER_X - 2.5, -14.0,
                         GATE_CENTER_X + 2.5, -9.0),
                    )
                    with self.assertRaisesRegex(
                        ValueError, "one off-DIFF CNT endpoint"
                    ):
                        stdcell_geometry.soi_edge_protection(
                            checker_layout, checker_top, layer_mode="fab"
                        )
                markers, details = self.run_drc(
                    prepared, name, layer_mode="fab",
                    edge_redundancy=True, strict=True,
                )
                edge_failures = [
                    item for item in markers
                    if item.startswith(("TIN.SOIEDGE", "TIN.SOIBYPASS", "CNT.SOIGATE"))
                ]
                self.assertEqual(
                    edge_failures, [], details + "\n" + "\n".join(edge_failures)
                )

    def test_disabled_strap_is_a_nonaccepting_diagnostic_geometry(self):
        markers, details = self.run_drc(
            self.generated / "edge_unprotected_diagnostic.gds",
            "edge_unprotected_diagnostic",
            edge_redundancy=True,
        )
        self.assertTrue(
            any(item.startswith("TIN.SOIEDGE") for item in markers),
            details + "\n" + "\n".join(markers),
        )

    def test_missing_one_ml1_edge_and_insufficient_margin_fail_coverage(self):
        missing_edge = self.work / "missing_ml1_edge.gds"
        self.copy_flattened(
            "nmos_min",
            missing_edge,
            lambda layout, top: self.remove_area(
                layout, top, (8, 0),
                (GATE_CENTER_X - 12.0, -4.0, GATE_CENTER_X + 12.0, 4.0),
            ),
        )
        markers, details = self.run_drc(missing_edge, "nmos_min")
        self.assertTrue(any(item.startswith("TIN.SOIEDGE") for item in markers), details)

        # The obligation covers the TiN that steps over the edge (the gate), not
        # the off-island landing pad, so the margin case has to trim the strap
        # inside the crossing's margin zone: cutting it back to the gate edge
        # leaves the TiN covered but with no protective margin left.
        insufficient = self.work / "insufficient_ml1_margin.gds"
        self.copy_flattened(
            "nmos_min",
            insufficient,
            lambda layout, top: self.remove_area(
                layout, top, (8, 0),
                (GATE_CENTER_X + MOS_LENGTH / 2.0, -4.0,
                 GATE_CENTER_X + MOS_LENGTH / 2.0 + 0.4, 4.0),
            ),
        )
        markers, details = self.run_drc(insufficient, "nmos_min")
        self.assertTrue(any(item.startswith("TIN.SOIEDGE") for item in markers), details)

    def test_bypass_rejects_one_missing_endpoint_and_disconnected_metal(self):
        missing_contact = self.work / "missing_gate_endpoint.gds"
        self.copy_flattened(
            "nmos_min",
            missing_contact,
            lambda layout, top: self.remove_area(
                layout, top, (7, 0),
                (GATE_CENTER_X - 2.5, -14.0, GATE_CENTER_X + 2.5, -9.0),
            ),
        )
        markers, details = self.run_drc(
            missing_contact, "nmos_min", edge_redundancy=True
        )
        self.assertTrue(any(item.startswith("TIN.SOIBYPASS") for item in markers), details)

        broken_tin_landing = self.work / "disconnected_tin_landing.gds"
        self.copy_flattened(
            "nmos_min",
            broken_tin_landing,
            lambda layout, top: self.remove_area(
                layout, top, (5, 0),
                (GATE_CENTER_X - 12.0, -9.0, GATE_CENTER_X + 12.0, -4.0),
            ),
        )
        markers, details = self.run_drc(
            broken_tin_landing, "nmos_min", edge_redundancy=True
        )
        self.assertTrue(any(item.startswith("TIN.SOIBYPASS") for item in markers), details)

        multi_contact = self.work / "multifinger_missing_gate_endpoint.gds"
        second_gate_center = GATE_CENTER_X + GATE_PITCH
        self.copy_flattened(
            "edge_protected_multifinger",
            multi_contact,
            lambda layout, top: self.remove_area(
                layout, top, (7, 0),
                (second_gate_center - 2.5, -14.0,
                 second_gate_center + 2.5, -9.0),
            ),
        )
        markers, details = self.run_drc(
            multi_contact, "edge_protected_multifinger", edge_redundancy=True
        )
        self.assertTrue(any(item.startswith("TIN.SOIBYPASS") for item in markers), details)

        broken_strap = self.work / "disconnected_gate_strap.gds"
        self.copy_flattened(
            "nmos_min",
            broken_strap,
            lambda layout, top: self.remove_area(
                layout, top, (8, 0),
                (GATE_CENTER_X - 12.0, 6.0, GATE_CENTER_X + 12.0, 7.0),
            ),
        )
        markers, details = self.run_drc(
            broken_strap, "nmos_min", edge_redundancy=True
        )
        self.assertTrue(any(item.startswith("TIN.SOIBYPASS") for item in markers), details)

        split_metal_with_two_contacts_per_edge = self.work / "split_gate_bypass_metal.gds"

        def add_double_edge_contacts_then_cut_strap(layout, top):
            for y in (-24.5, MOS_WIDTH + 24.5):
                self.add_area(
                    layout, top, (5, 0),
                    (GATE_CENTER_X - 6.5, y - 6.5,
                     GATE_CENTER_X + 6.5, y + 6.5),
                )
                self.add_area(
                    layout, top, (7, 0),
                    (GATE_CENTER_X - 2.5, y - 2.5,
                     GATE_CENTER_X + 2.5, y + 2.5),
                )
                self.add_area(
                    layout, top, (8, 0),
                    (GATE_CENTER_X - 6.5, y - 6.5,
                     GATE_CENTER_X + 6.5, y + 6.5),
                )
            self.remove_area(
                layout, top, (8, 0),
                (GATE_CENTER_X - 15.0, 6.0,
                 GATE_CENTER_X + 15.0, 14.0),
            )

        self.copy_flattened(
            "nmos_min", split_metal_with_two_contacts_per_edge,
            add_double_edge_contacts_then_cut_strap,
        )
        markers, details = self.run_drc(
            split_metal_with_two_contacts_per_edge, "nmos_min",
            edge_redundancy=True,
        )
        self.assertTrue(
            any(item.startswith("TIN.SOIBYPASS") for item in markers), details
        )

        channel_contact = self.work / "illegal_channel_gate_contact.gds"
        self.copy_flattened(
            "nmos_min",
            channel_contact,
            lambda layout, top: self.add_area(
                layout, top, (7, 0),
                (GATE_CENTER_X - 2.5, 4.0, GATE_CENTER_X + 2.5, 9.0),
            ),
        )
        markers, details = self.run_drc(
            channel_contact, "nmos_min", edge_redundancy=True
        )
        self.assertTrue(any(item.startswith("CNT.SOIGATE") for item in markers), details)

    def test_post_fab_protection_loss_fails_fab_mode_coverage(self):
        fab_layout = self.prepare_fab(self.generated / "nmos_min.gds", "nmos_min")
        damaged_fab = self.work / "damaged_fab_protection.gds"
        layout = pya.Layout()
        layout.read(str(fab_layout))
        top = layout.cell("nmos_min")
        self.assertIsNotNone(top)
        top.flatten(-1)
        self.remove_area(
            layout, top, (7, 0),
            (GATE_CENTER_X - 12.0, -4.0, GATE_CENTER_X + 12.0, 4.0),
        )
        layout.write(str(damaged_fab))
        markers, details = self.run_drc(
            damaged_fab, "nmos_min", layer_mode="fab"
        )
        self.assertTrue(any(item.startswith("TIN.SOIEDGE") for item in markers), details)

    def test_each_single_tin_edge_cut_independently_keeps_gate_lvs(self):
        # Each single edge fracture is bypassed by the intact opposite edge.
        # Two simultaneous complete crossings are not claimed to remain driven.
        prepared = self.prepare_fab(self.generated / "nmos_min.gds", "nmos_min")
        for label, bounds in (
            ("lower", (GATE_CENTER_X - 10.0, -0.5,
                       GATE_CENTER_X + 10.0, 0.0)),
            ("upper", (GATE_CENTER_X - 10.0, MOS_WIDTH,
                       GATE_CENTER_X + 10.0, MOS_WIDTH + 0.5)),
        ):
            with self.subTest(edge=label):
                damaged = self.work / ("single_" + label + "_tin_cut.gds")
                self.copy_and_flatten(
                    prepared,
                    "nmos_min",
                    damaged,
                    lambda layout, top, bounds=bounds: self.remove_area(
                        layout, top, (5, 0), bounds
                    ),
                )
                details = self.run_lvs(damaged, "nmos_min", layer_mode="fab")
                self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)

    def test_real_adjacent_source_drain_metal_short_fails_lvs(self):
        shorted = self.work / "shorted_nmos.gds"

        def add_short(layout, top):
            self.edit_region(
                layout,
                top,
                (8, 0),
                lambda region: region.__ior__(
                    pya.Region(box_um(3.5, -29.0, 9.5, 9.5))
                    | pya.Region(box_um(42.5, -29.0, 48.5, 9.5))
                    | pya.Region(box_um(6.5, -29.0, 45.5, -23.0))
                ),
            )

        self.copy_flattened("nmos_min", shorted, add_short)
        details = self.run_lvs(shorted, "nmos_min")
        self.assertIn("ICPS2023_5 LVS NO MATCH", details)


if __name__ == "__main__":
    unittest.main()
