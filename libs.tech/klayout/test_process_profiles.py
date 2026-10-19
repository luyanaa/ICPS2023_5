#!/usr/bin/env python3
"""Integration checks for the 1M and experimental process profiles."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[2]
KLAYOUT = shutil.which("klayout")
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
DRC_DECK = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX_SCRIPT = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ONE_METAL = "icps2023_5_1m"
EXPERIMENTAL = "icps2023_5_multimetal_experimental"


def add_box(layout, cell, layer, x1, y1, x2, y2):
    scale = 1.0 / layout.dbu
    box = pya.Box(*(int(round(value * scale)) for value in (x1, y1, x2, y2)))
    cell.shapes(layout.layer(*layer)).insert(box)


def clean_environment(profile=None):
    environment = os.environ.copy()
    environment.pop("ICPS2023_5_PROCESS_PROFILE", None)
    if profile:
        environment["ICPS2023_5_PROCESS_PROFILE"] = profile
    return environment


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for profile checks")
class ProcessProfileTests(unittest.TestCase):
    def make_layout(self, path, mask=False, extra_layers=()):
        layout = pya.Layout()
        layout.dbu = 0.001
        cell = layout.create_cell("TOP")
        add_box(layout, cell, (9, 0), 0, 0, 5, 5)       # VIA1, design numbering
        add_box(layout, cell, (10, 0), 0, 0, 4, 4)    # ML2, design numbering
        if mask:
            add_box(layout, cell, (20, 0), -1, -1, 13, 13)
        for layer in extra_layers:
            add_box(layout, cell, layer, 0, 0, 4, 4)
        layout.write(str(path))
        return path

    def run_drc(self, layout_path, tempdir, profile=None, environment_profile=None):
        report = Path(tempdir) / "drc.lyrdb"
        command = [
            KLAYOUT, "-b", "-r", str(DRC_DECK), "-rd", "input=" + str(layout_path),
            "-rd", "report=" + str(report),
        ]
        if profile:
            command.extend(["-rd", "process_profile=" + profile])
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=clean_environment(environment_profile),
        ), report

    def run_fab_prep(self, layout_path, output_path, profile=None):
        command = [
            KLAYOUT, "-b", "-r", str(FAB_PREP), "-rd", "input=" + str(layout_path),
            "-rd", "output=" + str(output_path),
        ]
        if profile:
            command.extend(["-rd", "process_profile=" + profile])
        return subprocess.run(
            command, text=True, capture_output=True, env=clean_environment()
        )

    def test_default_profile_rejects_unqualified_layers_in_drc_fabprep_and_lvs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "upper.gds")
            drc, _ = self.run_drc(layout, tempdir)
            self.assertNotEqual(drc.returncode, 0)
            self.assertIn("process profile %s does not permit upper-metal geometry" % ONE_METAL,
                          drc.stdout + drc.stderr)

            fab_output = tempdir / "upper_fab.gds"
            fab = self.run_fab_prep(layout, fab_output)
            self.assertNotEqual(fab.returncode, 0)
            self.assertIn("process profile %s does not permit upper-metal geometry" % ONE_METAL,
                          fab.stdout + fab.stderr)
            self.assertFalse(fab_output.exists())

            schematic = tempdir / "empty.cir"
            schematic.write_text(".SUBCKT TOP\n.ENDS TOP\n")
            lvs_output = tempdir / "extracted.cir"
            lvs = subprocess.run(
                [KLAYOUT, "-b", "-r", str(LVS_DECK), "-rd", "input=" + str(layout),
                 "-rd", "schematic=" + str(schematic), "-rd", "output=" + str(lvs_output)],
                text=True, capture_output=True, env=clean_environment(),
            )
            self.assertNotEqual(lvs.returncode, 0)
            self.assertIn("process profile %s does not permit upper-metal geometry" % ONE_METAL,
                          lvs.stdout + lvs.stderr)

    def test_cli_profile_overrides_environment_and_banner_reports_selection(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "upper.gds")
            inherited, _ = self.run_drc(
                layout, tempdir, environment_profile=EXPERIMENTAL
            )
            self.assertEqual(inherited.returncode, 0, inherited.stdout + inherited.stderr)
            self.assertIn("profile=%s" % EXPERIMENTAL, inherited.stdout)

            overridden, _ = self.run_drc(
                layout, tempdir, profile=ONE_METAL, environment_profile=EXPERIMENTAL
            )
            self.assertNotEqual(overridden.returncode, 0)
            self.assertIn(
                "process profile %s does not permit upper-metal geometry" % ONE_METAL,
                overridden.stdout + overridden.stderr,
            )

    def test_masked_upper_layers_are_ignored_by_default_preflight(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "masked.gds", mask=True)
            drc, _ = self.run_drc(layout, tempdir)
            self.assertEqual(drc.returncode, 0, drc.stdout + drc.stderr)
            fab = self.run_fab_prep(layout, tempdir / "masked_fab.gds")
            self.assertEqual(fab.returncode, 0, fab.stdout + fab.stderr)

    def test_experimental_profile_runs_provisional_drc_and_maps_upper_layers(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "upper.gds")
            drc, report = self.run_drc(layout, tempdir, EXPERIMENTAL)
            self.assertEqual(drc.returncode, 0, drc.stdout + drc.stderr)
            self.assertIn("DRC finished", drc.stdout)
            self.assertIn("ML2.H", report.read_text())

            fab_path = tempdir / "upper_fab.gds"
            fab = self.run_fab_prep(layout, fab_path, EXPERIMENTAL)
            self.assertEqual(fab.returncode, 0, fab.stdout + fab.stderr)
            converted = pya.Layout()
            converted.read(str(fab_path))
            via1 = converted.find_layer(pya.LayerInfo(8, 0))
            ml2 = converted.find_layer(pya.LayerInfo(9, 0))
            self.assertIsNotNone(via1)
            self.assertIsNotNone(ml2)
            self.assertGreater(converted.top_cell().shapes(via1).size(), 0)
            self.assertGreater(converted.top_cell().shapes(ml2).size(), 0)

    def test_experimental_drc_and_fabprep_reject_via2_and_ml3(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(
                tempdir / "unmapped.gds", extra_layers=((11, 0), (12, 0))
            )
            drc, _ = self.run_drc(layout, tempdir, EXPERIMENTAL)
            self.assertNotEqual(drc.returncode, 0)
            self.assertIn(
                "does not permit upper-metal geometry: VIA2, ML3",
                drc.stdout + drc.stderr,
            )

            fab_output = tempdir / "unmapped_fab.gds"
            fab = self.run_fab_prep(layout, fab_output, EXPERIMENTAL)
            self.assertNotEqual(fab.returncode, 0)
            self.assertIn(
                "does not permit upper-metal geometry: VIA2, ML3",
                fab.stdout + fab.stderr,
            )
            self.assertFalse(fab_output.exists())

    def test_masked_via2_ml3_are_not_emitted_by_experimental_fabprep(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(
                tempdir / "masked_unmapped.gds",
                mask=True,
                extra_layers=((11, 0), (12, 0)),
            )
            fab_output = tempdir / "masked_unmapped_fab.gds"
            fab = self.run_fab_prep(layout, fab_output, EXPERIMENTAL)
            self.assertEqual(fab.returncode, 0, fab.stdout + fab.stderr)
            converted = pya.Layout()
            converted.read(str(fab_output))
            for layer_info in (pya.LayerInfo(11, 0), pya.LayerInfo(12, 0)):
                layer = converted.find_layer(layer_info)
                if layer is not None:
                    self.assertEqual(converted.top_cell().shapes(layer).size(), 0)

    def test_experimental_lvs_accepts_supported_via1_ml2_geometry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "upper.gds")
            schematic = tempdir / "empty.cir"
            schematic.write_text(".SUBCKT TOP\n.ENDS TOP\n")
            result = subprocess.run(
                [KLAYOUT, "-b", "-r", str(LVS_DECK), "-rd", "input=" + str(layout),
                 "-rd", "schematic=" + str(schematic), "-rd", "process_profile=" + EXPERIMENTAL,
                 "-rd", "output=" + str(tempdir / "extracted.cir")],
                text=True, capture_output=True, env=clean_environment(),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_experimental_lvs_rejects_unpermitted_via2_and_ml3(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(
                tempdir / "unmapped.gds", extra_layers=((11, 0), (12, 0))
            )
            schematic = tempdir / "empty.cir"
            schematic.write_text(".SUBCKT TOP\n.ENDS TOP\n")
            result = subprocess.run(
                [KLAYOUT, "-b", "-r", str(LVS_DECK), "-rd", "input=" + str(layout),
                 "-rd", "schematic=" + str(schematic), "-rd", "process_profile=" + EXPERIMENTAL],
                text=True, capture_output=True, env=clean_environment(),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not permit upper-metal geometry: VIA2, ML3",
                          result.stdout + result.stderr)

    def test_pex_rejects_unmodelled_upper_layers_even_in_experimental_profile(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = self.make_layout(tempdir / "upper.gds")
            for profile in (ONE_METAL, EXPERIMENTAL):
                output = tempdir / (profile + ".spice")
                result = subprocess.run(
                    [sys.executable, str(PEX_SCRIPT), "--input", str(layout), "--top", "TOP",
                     "--output", str(output), "--process-profile", profile, "--no-capacitance"],
                    text=True, capture_output=True, env=clean_environment(),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("upper-metal geometry", result.stdout + result.stderr)
                self.assertFalse(output.exists())

    def test_pcell_defaults_and_override_follow_selected_profile(self):
        ruby = r'''require "rexml/document"
path = @PCELL_MACRO@
xml = REXML::Document.new(File.read(path))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, path)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
layout = RBA::Layout::new
layout.dbu = 0.001

def via_shapes(layout, cell)
  cell.shapes(layout.layer(RBA::LayerInfo.new(9, 0))).size +
    cell.shapes(layout.layer(RBA::LayerInfo.new(10, 0))).size
end

["Nch", "R_ndiff"].each do |name|
  pcell_id = lib.layout.pcell_id(name)
  variant = layout.cell(layout.add_pcell_variant(lib, pcell_id, {}))
  count = via_shapes(layout, variant)
  if ICPS2023_5NativeGeometry::PROFILE_NAME == "icps2023_5_1m"
    raise "#{name} default emitted unqualified via geometry" unless count == 0
  else
    raise "#{name} experimental default omitted via geometry" unless count > 0
  end
end

if ICPS2023_5NativeGeometry::PROFILE_NAME == "icps2023_5_1m"
  rejected = layout.cell(layout.add_pcell_variant(
    lib, lib.layout.pcell_id("Nch"), {"with_via" => true}
  ))
  raise "1M profile emitted unqualified via geometry" if via_shapes(layout, rejected) > 0
end

'''.replace("@PCELL_MACRO@", json.dumps(str(PCELL_MACRO)))
        with tempfile.TemporaryDirectory() as tempdir:
            runner = Path(tempdir) / "pcell_profile_check.rb"
            runner.write_text(ruby)
            for profile in (ONE_METAL, EXPERIMENTAL):
                result = subprocess.run(
                    [KLAYOUT, "-b", "-r", str(runner)], text=True, capture_output=True,
                    env=clean_environment(profile),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bridge_metal_option_is_profile_gated(self):
        ruby = r'''require "rexml/document"
path = @PCELL_MACRO@
xml = REXML::Document.new(File.read(path))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, path)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
layout = RBA::Layout::new
layout.dbu = 0.001
bridge = layout.cell(layout.add_pcell_variant(
  lib, lib.layout.pcell_id("Bridge"), {"mb" => true}
))
counts = [9, 10].map do |number|
  bridge.shapes(layout.layer(RBA::LayerInfo.new(number, 0))).size
end
gate = Object.new.extend(ICPS2023_5NativeGeometry)
if ICPS2023_5NativeGeometry::PROFILE_NAME == "icps2023_5_1m"
  begin
    gate.validate_via_profile!(true)
    raise "1M profile accepted a VIA1/ML2 request"
  rescue ArgumentError
    # The invalid parameter combination must be rejected before production.
  end
  raise "1M profile emitted Bridge.mb upper-metal geometry" unless counts.all?(&:zero?)
else
  gate.validate_via_profile!(true)
  raise "experimental Bridge.mb omitted VIA1 or ML2" unless counts.all?{|count| count > 0}
end
plain = layout.cell(layout.add_pcell_variant(
  lib, lib.layout.pcell_id("Bridge"), {"mb" => false}
))
raise "TiN Bridge omitted its conductor" if plain.shapes(
  layout.layer(RBA::LayerInfo.new(5, 0))).is_empty?
raise "TiN Bridge emitted upper metal" unless [9, 10].all? do |number|
  plain.shapes(layout.layer(RBA::LayerInfo.new(number, 0))).is_empty?
end
'''.replace("@PCELL_MACRO@", json.dumps(str(PCELL_MACRO)))
        with tempfile.TemporaryDirectory() as tempdir:
            runner = Path(tempdir) / "bridge_profile_check.rb"
            runner.write_text(ruby)
            for profile in (ONE_METAL, EXPERIMENTAL):
                result = subprocess.run(
                    [KLAYOUT, "-b", "-r", str(runner)], text=True, capture_output=True,
                    env=clean_environment(profile),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
