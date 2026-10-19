#!/usr/bin/env python3
"""Report-only N_CH audit tests against real native PCell conversions."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[3]
DRC_DIR = ROOT / "libs.tech/klayout/drc"
AUDIT = DRC_DIR / "audit_nch_mask.py"
FAB_PREP = DRC_DIR / "icps2023_5_fab_prep.drc"
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
KLAYOUT = os.environ.get("KLAYOUT") or shutil.which("klayout")
ONE_METAL = "icps2023_5_1m"
DBU = 0.001


def environment():
    env = os.environ.copy()
    env["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
    return env


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment())


def layer_region(layout, cell, layer):
    index = layout.find_layer(pya.LayerInfo(*layer))
    if index is None or index < 0:
        return pya.Region()
    return pya.Region(cell.begin_shapes_rec(index)).merged()


def summary(region, dbu):
    components = []
    for polygon in region.merged().each():
        box = polygon.bbox()
        components.append({
            "bbox_um": [round(value * dbu, 6) for value in
                        (box.left, box.bottom, box.right, box.top)],
            "area_um2": round(polygon.area() * dbu * dbu, 6),
        })
    components.sort(key=lambda item: (item["bbox_um"], item["area_um2"]))
    return {
        "component_count": len(components),
        "area_um2": round(region.area() * dbu * dbu, 6),
        "bbox_um": ([round(value * dbu, 6) for value in
                     (region.bbox().left, region.bbox().bottom,
                      region.bbox().right, region.bbox().top)]
                    if components else None),
        "components": components,
    }


def read(path):
    layout = pya.Layout()
    layout.read(str(path))
    return layout, layout.top_cell()


def make_nmos4_fixture(work):
    """Generate a body-tied four-terminal Nch native PCell fixture."""
    output = work / "nmos4_min.gds"
    script = work / "generate_nmos4.rb"
    script.write_text(textwrap.dedent("""
        require "rexml/document"
        xml = REXML::Document.new(File.read($pcell_macro.to_s))
        eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING,
             $pcell_macro.to_s)
        library = RBA::Library.library_by_name("PCells_ICPS2023_5")
        raise "native ICPS2023_5 PCells did not register" unless library
        layout = RBA::Layout.new
        layout.dbu = 0.001
        top = layout.create_cell("nmos4_min")
        pcell_id = library.layout.pcell_id("Nch")
        raise "missing native Nch PCell" if pcell_id < 0
        parameters = {"w" => 20.0, "l" => 6.0, "n" => 1,
                      "with_via" => false, "body_tie" => true}
        ICPS2023_5NativeGeometry.validate_supported_parameters!("Nch", true)
        variant = layout.add_pcell_variant(library, pcell_id, parameters)
        top.insert(RBA::CellInstArray.new(
          variant, RBA::Trans.new(RBA::Trans::R0, 0, 0)))
        layout.write($output.to_s)
    """).lstrip(), encoding="utf-8")
    result = run([KLAYOUT, "-b", "-r", str(script),
                  "-rd", "output=" + str(output),
                  "-rd", "pcell_macro=" + str(PCELL_MACRO)])
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return output


def fab_prep(design, destination, legacy=False):
    command = [KLAYOUT, "-b", "-r", str(FAB_PREP),
               "-rd", "input=" + str(design),
               "-rd", "output=" + str(destination),
               "-rd", "process_profile=" + ONE_METAL]
    if legacy:
        command += ["-rd", "legacy_pwell=true"]
    result = run(command)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return destination


@unittest.skipUnless(KLAYOUT, "KLayout is required for native PCell conversions")
class NchMaskAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tempdir.name)
        generated = cls.work / "generated"
        generated.mkdir()
        result = run([KLAYOUT, "-b", "-r", str(GENERATOR),
                      "-rd", "output_dir=" + str(generated),
                      "-rd", "pcell_macro=" + str(PCELL_MACRO)])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.designs = {
            "nmos_min": generated / "nmos_min.gds",
            "nmos4_min": make_nmos4_fixture(cls.work),
            "pmos_min": generated / "pmos_min.gds",
        }
        cls.fabs = {}
        for name, design in cls.designs.items():
            cls.fabs[name] = {
                "explicit": fab_prep(design, cls.work / (name + "_explicit.gds")),
                "legacy": fab_prep(design, cls.work / (name + "_legacy.gds"),
                                   legacy=True),
            }

    @classmethod
    def tearDownClass(cls):
        cls._tempdir.cleanup()

    def run_audit(self, input_path, mode, *arguments):
        result = subprocess.run(
            [sys.executable, str(AUDIT), "--input", str(input_path),
             "--mode", mode, *map(str, arguments)],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_nmos_nmos4_and_plain_pmos_match_fabprep_candidates(self):
        explicit_areas = {}
        for name, design in self.designs.items():
            with self.subTest(device=name):
                report = self.run_audit(design, "design")
                self.assertEqual(report["assessment"]["status"], "REPORT_ONLY")
                self.assertEqual(report["assessment"]["qualification"], "UNQUALIFIED")
                self.assertEqual(report["input"]["mode"], "design")
                self.assertEqual(report["input"]["top"], name)
                self.assertEqual(report["input"]["layer_mapping"]["nch"],
                                 {"design": [21, 0], "fab": [13, 0]})
                self.assertEqual(report["input"]["path"], str(design.resolve()))
                self.assertEqual(len(report["input"]["sha256"]), 64)
                self.assertEqual(report["observations"]["channel_coverage"]["status"],
                                 "UNCONFIRMED")
                self.assertEqual(report["categories"]["PROCESS_LIMITS"]["status"],
                                 "UNCONFIRMED")
                self.assertNotIn('"PASS"', json.dumps(report))
                self.assertIsNone(report["source"])
                self.assertEqual(report["categories"]["PROVENANCE_COMPARISON"]["status"],
                                 "UNCONFIRMED")
                for recipe in ("explicit", "legacy"):
                    fab_layout, fab_top = read(self.fabs[name][recipe])
                    actual = layer_region(fab_layout, fab_top, (13, 0))
                    expected = summary(actual, fab_layout.dbu)
                    reported = report["candidates"][recipe]["inventory"]
                    for key in expected:
                        self.assertEqual(reported[key], expected[key],
                                         "%s/%s %s" % (name, recipe, key))
                    self.assertEqual(report["candidates"][recipe]["status"],
                                     "OBSERVED" if reported["layer_present"] else
                                     "UNCONFIRMED")
                    explicit_areas.setdefault(name, {})[recipe] = (
                        actual.area() * fab_layout.dbu ** 2)

                _, source_top = read(design)
                self.assertTrue(list(source_top.each_inst()),
                                "native PCell geometry should remain hierarchical")
                if name == "pmos_min":
                    self.assertEqual(explicit_areas[name]["explicit"], 0.0)
                    self.assertEqual(explicit_areas[name]["legacy"], 0.0)
                elif name == "nmos4_min":
                    self.assertGreater(explicit_areas[name]["explicit"],
                                       explicit_areas["nmos_min"]["explicit"])

        self.assertGreater(explicit_areas["nmos_min"]["legacy"],
                           explicit_areas["nmos_min"]["explicit"])
        self.assertGreater(
            self.run_audit(self.designs["nmos_min"], "design")["candidates"]["delta"]["xor_area_um2"],
            0.0,
        )

    def test_candidates_reproduce_recipe_formula_on_real_hierarchical_pcells(self):
        for name, design in self.designs.items():
            with self.subTest(device=name):
                layout, top = read(design)
                clip = layer_region(layout, top, (20, 0)) + layer_region(layout, top, (63, 63))
                nch = layer_region(layout, top, (21, 0)) - clip
                narea = layer_region(layout, top, (19, 0)) - clip
                tin = layer_region(layout, top, (5, 0)) - clip
                expected = {
                    "explicit": nch,
                    "legacy": narea.interacting(tin).sized(round(2.0 / layout.dbu)) + nch,
                }
                for recipe, fab_path in self.fabs[name].items():
                    fab_layout, fab_top = read(fab_path)
                    actual = layer_region(fab_layout, fab_top, (13, 0))
                    self.assertTrue((expected[recipe] ^ actual).is_empty(),
                                    "%s %s mask differs from fab-prep formula" %
                                    (name, recipe))

    def test_spatial_observations_match_design_and_fab_geometry(self):
        for name, design in self.designs.items():
            with self.subTest(device=name):
                design_report = self.run_audit(design, "design")
                design_layout, design_top = read(design)
                clip = (layer_region(design_layout, design_top, (20, 0)) +
                        layer_region(design_layout, design_top, (63, 63)))
                nch = layer_region(design_layout, design_top, (21, 0)) - clip
                soi = layer_region(design_layout, design_top, (3, 0)) - clip
                tin = layer_region(design_layout, design_top, (5, 0)) - clip
                fab_layout, fab_top = read(self.fabs[name]["explicit"])
                fab_nch = layer_region(fab_layout, fab_top, (13, 0))
                fab_soi = layer_region(fab_layout, fab_top, (4, 0))
                fab_tin = layer_region(fab_layout, fab_top, (5, 0))
                fab_p_plus = layer_region(fab_layout, fab_top, (2, 0))
                fab_n_plus = layer_region(fab_layout, fab_top, (3, 0))
                channel = (soi & tin) - (fab_p_plus + fab_n_plus)
                covered = channel & nch
                uncovered = channel - nch
                observations = design_report["observations"]
                coverage = observations["channel_coverage"]
                self.assertEqual(coverage["channel_region_area_um2"],
                                 round(channel.area() * design_layout.dbu ** 2, 6))
                self.assertEqual(coverage["nch_overlap_area_um2"],
                                 round(covered.area() * design_layout.dbu ** 2, 6))
                self.assertEqual(coverage["uncovered_channel_area_um2"],
                                 round(uncovered.area() * design_layout.dbu ** 2, 6))
                expected_fraction = (round(covered.area() / channel.area(), 8)
                                     if channel.area() else None)
                self.assertEqual(coverage["coverage_fraction"], expected_fraction)

                off_soi = nch - soi
                soi_relation = observations["soi_relation"]
                self.assertEqual(soi_relation["off_soi_area_um2"],
                                 round(off_soi.area() * design_layout.dbu ** 2, 6))
                self.assertEqual(
                    sum(component["area_um2"]
                        for component in soi_relation["off_soi_components"]),
                    soi_relation["off_soi_area_um2"])
                fully_off = 0
                partially_off = 0
                for polygon in nch.merged().each():
                    component = pya.Region()
                    component.insert(polygon)
                    on_area = (component & soi).area()
                    if on_area == 0:
                        fully_off += 1
                    elif on_area < component.area():
                        partially_off += 1
                self.assertEqual(soi_relation["fully_off_soi_component_count"],
                                 fully_off)
                self.assertEqual(soi_relation["partially_off_soi_component_count"],
                                 partially_off)
                if name == "nmos4_min":
                    self.assertGreater(soi_relation["off_soi_area_um2"], 0.0)

                design_dopants = observations["effective_dopant_overlap"]
                self.assertEqual(
                    design_dopants["p_plus"]["overlap_area_um2"],
                    round((nch & fab_p_plus).area() * fab_layout.dbu ** 2, 6))
                self.assertEqual(
                    design_dopants["n_plus"]["overlap_area_um2"],
                    round((nch & fab_n_plus).area() * fab_layout.dbu ** 2, 6))

                fab_report = self.run_audit(self.fabs[name]["explicit"], "fab")
                fab_observations = fab_report["observations"]
                fab_channel = (fab_soi & fab_tin) - (fab_p_plus + fab_n_plus)
                fab_coverage = fab_observations["channel_coverage"]
                self.assertEqual(fab_coverage["channel_region_area_um2"],
                                 round(fab_channel.area() * fab_layout.dbu ** 2, 6))
                fab_dopants = fab_observations["effective_dopant_overlap"]
                self.assertEqual(
                    fab_dopants["p_plus"]["overlap_area_um2"],
                    round((fab_nch & fab_p_plus).area() * fab_layout.dbu ** 2, 6))
                self.assertEqual(
                    fab_dopants["n_plus"]["overlap_area_um2"],
                    round((fab_nch & fab_n_plus).area() * fab_layout.dbu ** 2, 6))
                self.assertEqual(fab_dopants["status"], "OBSERVED")
                self.assertEqual(fab_coverage["status"], "UNCONFIRMED")

    def test_fab_provenance_xor_is_separate_from_process_qualification(self):
        design = self.designs["nmos_min"]
        explicit = self.fabs["nmos_min"]["explicit"]
        legacy = self.fabs["nmos_min"]["legacy"]
        matched_explicit = self.run_audit(
            explicit, "fab", "--source", design,
            "--source-provenance", "explicit")
        self.assertEqual(matched_explicit["source"]["comparison"]["status"],
                         "PROVENANCE_MATCH")
        self.assertEqual(matched_explicit["source"]["comparison"]["candidate_xors"]
                         ["explicit"]["xor_area_um2"], 0.0)
        self.assertEqual(matched_explicit["assessment"]["qualification"], "UNQUALIFIED")
        self.assertEqual(matched_explicit["source"]["path"], str(design.resolve()))
        self.assertEqual(len(matched_explicit["source"]["sha256"]), 64)
        self.assertEqual(matched_explicit["source"]["top"], "nmos_min")
        self.assertEqual(matched_explicit["source"]["provenance"], "explicit")
        self.assertEqual(matched_explicit["source"]["layer_mapping"]["nch"],
                         {"design": [21, 0], "fab": [13, 0]})
        self.assertEqual(matched_explicit["source_comparison"]["status"],
                         "PROVENANCE_MATCH")

        matched_legacy = self.run_audit(
            legacy, "fab", "--source", design,
            "--source-provenance", "legacy")
        self.assertEqual(matched_legacy["source"]["comparison"]["status"],
                         "PROVENANCE_MATCH")

        mismatch = self.run_audit(
            explicit, "fab", "--source", design,
            "--source-provenance", "legacy")
        self.assertEqual(mismatch["source"]["comparison"]["status"],
                         "PROVENANCE_MISMATCH")
        self.assertGreater(
            mismatch["source"]["comparison"]["candidate_xors"]["legacy"]["xor_area_um2"],
            0.0)
        self.assertEqual(mismatch["assessment"]["qualification"], "UNQUALIFIED")
        self.assertEqual(mismatch["assessment"]["manufacturing_validity"],
                         "UNCONFIRMED")
        self.assertEqual(mismatch["categories"]["PROVENANCE_COMPARISON"]["status"],
                         "PROVENANCE_MISMATCH")

        unknown = self.run_audit(explicit, "fab")
        self.assertIsNone(unknown["source"])
        self.assertEqual(unknown["categories"]["PROVENANCE_COMPARISON"]["status"],
                         "UNCONFIRMED")
        self.assertEqual(unknown["categories"]["PROVENANCE_COMPARISON"]
                         ["details"]["source"], "UNKNOWN")
        undeclared = self.run_audit(explicit, "fab", "--source", design)
        self.assertIsNone(undeclared["source"]["provenance"])
        self.assertEqual(undeclared["source_comparison"]["status"], "UNCONFIRMED")

    def test_design_masks_and_excludes_are_applied_before_both_candidates(self):
        design = self.designs["nmos_min"]
        layout, top = read(design)
        original_nch = layer_region(layout, top, (21, 0))
        bounds = original_nch.bbox()
        middle_x = (bounds.left + bounds.right) // 2
        top.shapes(layout.layer(pya.LayerInfo(20, 0))).insert(
            pya.Box(bounds.left, bounds.bottom, middle_x, bounds.top))
        top.shapes(layout.layer(pya.LayerInfo(63, 63))).insert(
            pya.Box(middle_x, bounds.bottom, bounds.right, bounds.top))
        masked_design = self.work / "nmos_masks_excluded.gds"
        layout.write(str(masked_design))

        report = self.run_audit(masked_design, "design")
        self.assertLess(report["mask_inventory"]["area_um2"],
                        summary(original_nch, layout.dbu)["area_um2"])
        for recipe in ("explicit", "legacy"):
            fab = fab_prep(masked_design, self.work / ("excluded_" + recipe + ".gds"),
                           legacy=(recipe == "legacy"))
            fab_layout, fab_top = read(fab)
            actual = layer_region(fab_layout, fab_top, (13, 0))
            reported = report["candidates"][recipe]["inventory"]
            expected = summary(actual, fab_layout.dbu)
            for key in expected:
                self.assertEqual(reported[key], expected[key],
                                 "excluded geometry %s %s" % (recipe, key))

    def test_fab_report_keeps_empty_and_unknown_geometry_distinct(self):
        absent = self.run_audit(self.fabs["pmos_min"]["explicit"], "fab")
        self.assertEqual(absent["mask_inventory"]["component_count"], 0)
        self.assertEqual(absent["mask_inventory"]["area_um2"], 0.0)
        self.assertIn(absent["mask_inventory"]["geometry_presence"],
                      ("OBSERVED_EMPTY", "UNCONFIRMED"))
        self.assertEqual(absent["assessment"]["qualification"], "UNQUALIFIED")
        self.assertTrue(all(item["status"] == "UNCONFIRMED"
                            for item in absent["foundry_questions"]))

        layout = pya.Layout()
        layout.dbu = DBU
        top = layout.create_cell("empty_nch")
        nch_layer = layout.layer(pya.LayerInfo(13, 0))
        top.shapes(nch_layer).insert(
            pya.Text("label-not-mask-area", pya.Trans(pya.Trans.R0, 0, 0)))
        empty_path = self.work / "text-only-nch.gds"
        layout.write(str(empty_path))
        empty = self.run_audit(empty_path, "fab")
        self.assertTrue(empty["mask_inventory"]["layer_present"])
        self.assertEqual(empty["mask_inventory"]["component_count"], 0)
        self.assertEqual(empty["mask_inventory"]["geometry_presence"],
                         "OBSERVED_EMPTY")
        self.assertEqual(empty["mask_inventory"]["raw_degenerate_shape_count"], 0)
        self.assertEqual(empty["assessment"]["qualification"], "UNQUALIFIED")


if __name__ == "__main__":
    unittest.main()
