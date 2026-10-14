#!/usr/bin/env python3
"""Golden geometry checks for the native ICPS2023_5 RBA PCells."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
ONE_METAL = "icps2023_5_1m"


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for PCell checks")
class NativePCellGoldenTests(unittest.TestCase):
    def test_registered_cells_and_representative_mask_geometry(self):
        ruby = r'''require "rexml/document"
path = @PCELL_MACRO@
xml = REXML::Document.new(File.read(path))
eval(xml.elements["klayout-macro/text"].text, TOPLEVEL_BINDING, path)
lib = RBA::Library.library_by_name("PCells_ICPS2023_5")
raise "native PCell library did not register" unless lib
layout = RBA::Layout.new
layout.dbu = 0.001

EXPECTED_CELLS = %w[Nch Pch R_ndiff R_pdiff Ndiff_cap Pdiff_cap TIN_cap Bridge Nbridge]
actual_cells = lib.layout.pcell_names.sort
raise "registered PCells: #{actual_cells.inspect}" unless actual_cells == EXPECTED_CELLS.sort

def variant(layout, lib, name, parameters = {})
  id = lib.layout.pcell_id(name)
  raise "missing PCell #{name}" if id < 0
  layout.cell(layout.add_pcell_variant(lib, id, parameters))
end

def bbox_um(layout, cell, layer_number, datatype = 0)
  layer = layout.layer(RBA::LayerInfo.new(layer_number, datatype))
  boxes = []
  cell.shapes(layer).each { |shape| boxes << shape.bbox }
  raise "missing mask #{layer_number}/#{datatype} in #{cell.name}" if boxes.empty?
  [boxes.map(&:left).min, boxes.map(&:bottom).min,
   boxes.map(&:right).max, boxes.map(&:top).max].map do |value|
    (value * layout.dbu).round(3)
  end
end

def expect_bbox(layout, cell, layer_number, expected, datatype = 0)
  actual = bbox_um(layout, cell, layer_number, datatype)
  raise "#{cell.name} layer #{layer_number}/#{datatype}: expected #{expected.inspect}, got #{actual.inspect}" unless actual == expected
end

def expect_absent(layout, cell, layer_number, datatype = 0)
  layer = layout.layer(RBA::LayerInfo.new(layer_number, datatype))
  raise "#{cell.name} unexpectedly emitted mask #{layer_number}/#{datatype}" unless cell.shapes(layer).size == 0
end

nch = variant(layout, lib, "Nch")
expect_bbox(layout, nch, 3, [0.0, 0.0, 48.0, 20.0])
expect_bbox(layout, nch, 19, [-2.0, -2.0, 50.0, 22.0])
expect_bbox(layout, nch, 21, [19.0, 0.0, 29.0, 20.0])
expect_bbox(layout, nch, 5, [17.0, -6.0, 31.0, 38.0])
expect_bbox(layout, nch, 7, [4.0, 7.5, 44.0, 34.0])
expect_bbox(layout, nch, 8, [0.0, 3.5, 48.0, 38.0])

pch = variant(layout, lib, "Pch", {"use_nwell" => true})
expect_bbox(layout, pch, 3, [0.0, 0.0, 48.0, 40.0])
expect_bbox(layout, pch, 18, [-2.0, -2.0, 50.0, 42.0])
expect_bbox(layout, pch, 1, [-2.0, -2.0, 50.0, 42.0])
expect_bbox(layout, pch, 5, [17.0, -6.0, 31.0, 58.0])
expect_absent(layout, pch, 19)

psub_nch = variant(layout, lib, "Nch", {"with_psubcont" => true})
expect_bbox(layout, psub_nch, 3, [-6.5, 0.0, 48.0, 44.5])
expect_bbox(layout, psub_nch, 18, [-8.5, 29.5, 8.5, 46.5])
expect_absent(layout, psub_nch, 1)

pch_nsub = variant(layout, lib, "Pch", {"with_nsubcont" => true})
expect_bbox(layout, pch_nsub, 19, [-8.5, 49.5, 8.5, 66.5])
expect_bbox(layout, pch_nsub, 1, [-8.5, -2.0, 50.0, 66.5])


# The finger interconnect option changes ML1 routing, not channel geometry.
nch_no_bus = variant(layout, lib, "Nch", {"n" => 2, "no_finger_conn" => true})
nch_bus = variant(layout, lib, "Nch", {"n" => 2, "no_finger_conn" => false})
expect_bbox(layout, nch_no_bus, 21, [19.0, 0.0, 64.0, 20.0])
expect_bbox(layout, nch_no_bus, 8, [0.0, 3.5, 83.0, 38.0])
expect_bbox(layout, nch_bus, 8, [0.0, -33.0, 83.0, 53.0])
expect_absent(layout, nch, 5, 1)
tied_nch = variant(layout, lib, "Nch", {"body_tie" => true})
expect_bbox(layout, tied_nch, 5, [17.0, -6.0, 31.0, 26.0], 1)

r_ndiff = variant(layout, lib, "R_ndiff")
expect_bbox(layout, r_ndiff, 3, [0.0, 6.5, 13.0, 59.5])
expect_bbox(layout, r_ndiff, 15, [1.5, 6.5, 11.5, 59.5])
expect_bbox(layout, r_ndiff, 19, [-2.0, 4.5, 15.0, 61.5])

r_pdiff = variant(layout, lib, "R_pdiff", {"use_nwell" => true})
expect_bbox(layout, r_pdiff, 3, [0.0, 6.5, 13.0, 59.5])
expect_bbox(layout, r_pdiff, 15, [1.5, 6.5, 11.5, 59.5])
expect_bbox(layout, r_pdiff, 18, [-2.0, 4.5, 15.0, 61.5])
expect_bbox(layout, r_pdiff, 1, [-4.0, -4.0, 15.5, 67.0])
expect_absent(layout, r_pdiff, 19)

ncap = variant(layout, lib, "Ndiff_cap")
expect_bbox(layout, ncap, 3, [0.0, -4.0, 100.0, 117.0])
expect_bbox(layout, ncap, 16, [0.0, 0.0, 100.0, 100.0])
expect_bbox(layout, ncap, 19, [-2.0, -6.0, 102.0, 119.0])
expect_bbox(layout, ncap, 5, [-4.0, 0.0, 104.0, 100.0])

pcap = variant(layout, lib, "Pdiff_cap")
expect_bbox(layout, pcap, 18, [-2.0, -6.0, 102.0, 119.0])
expect_absent(layout, pcap, 19)

# TiN capacitor plates and overlap are distinct design masks.
tin_cap = variant(layout, lib, "TIN_cap")
expect_bbox(layout, tin_cap, 8, [0.0, 0.0, 127.5, 121.0])
expect_bbox(layout, tin_cap, 16, [23.5, 0.0, 123.5, 100.0])
expect_bbox(layout, tin_cap, 5, [23.5, -4.0, 123.5, 121.0])

bridge = variant(layout, lib, "Bridge")
expect_bbox(layout, bridge, 5, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, bridge, 15, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, bridge, 8, [0.0, 0.0, 33.0, 53.0])

nbridge = variant(layout, lib, "Nbridge")
expect_bbox(layout, nbridge, 3, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, nbridge, 15, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, nbridge, 19, [-0.5, -0.5, 33.5, 53.5])
expect_absent(layout, nbridge, 5)

# Default-profile corpus must not leak unqualified upper-metal masks.
[nch, pch, nch_no_bus, nch_bus, tied_nch, psub_nch, pch_nsub,
 r_ndiff, r_pdiff, ncap, pcap, tin_cap, bridge, nbridge].each do |cell|
  expect_absent(layout, cell, 9)
  expect_absent(layout, cell, 10)
end
'''.replace("@PCELL_MACRO@", repr(str(PCELL_MACRO)))
        environment = os.environ.copy()
        environment.pop("ICPS2023_5_PROCESS_PROFILE", None)
        environment["ICPS2023_5_PROCESS_PROFILE"] = ONE_METAL
        with tempfile.TemporaryDirectory() as tempdir:
            runner = Path(tempdir) / "native_pcell_golden.rb"
            runner.write_text(ruby)
            result = subprocess.run(
                [KLAYOUT, "-b", "-r", str(runner)],
                text=True,
                capture_output=True,
                env=environment,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
