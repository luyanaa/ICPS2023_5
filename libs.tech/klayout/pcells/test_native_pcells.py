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
  # Reject stale names before passing the parameter map to KLayout.
  declaration = lib.layout.pcell_declaration(id)
  valid_parameters = declaration.get_parameters.map(&:name)
  unknown = parameters.keys.map(&:to_s) - valid_parameters
  raise "unknown parameters for #{name}: #{unknown.join(', ')}" unless unknown.empty?
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

def boxes_um(layout, cell, layer_number, datatype = 0)
  layer = layout.layer(RBA::LayerInfo.new(layer_number, datatype))
  boxes = []
  RBA::Region.new(cell.begin_shapes_rec(layer)).merged.each do |polygon|
    box = polygon.bbox
    boxes << [box.left, box.bottom, box.right, box.top].map do |value|
      (value * layout.dbu).round(3)
    end
  end
  boxes
end

nch = variant(layout, lib, "Nch")
expect_bbox(layout, nch, 3, [0.0, 0.0, 48.0, 20.0])
expect_bbox(layout, nch, 19, [-2.0, -2.0, 50.0, 22.0])
expect_bbox(layout, nch, 21, [21.0, 0.0, 27.0, 20.0])
tight_nch = variant(
    layout, lib, "Nch",
    {"w" => 20.0, "l" => 6.0, "dg" => 0.0, "sdg" => 0.0, "dsl" => 0.0}
)
tight_tin = []
tight_nch.shapes(layout.layer(RBA::LayerInfo.new(5, 0))).each do |shape|
  tight_tin << shape.bbox
end
tight_cnt = []
tight_nch.shapes(layout.layer(RBA::LayerInfo.new(7, 0))).each do |shape|
  tight_cnt << shape.bbox
end
gate_box = tight_tin.find do |box|
  [box.left, box.bottom, box.right, box.top] == [21000, -6000, 27000, 26000]
end
sd_cut = tight_cnt.find do |box|
  [box.left, box.bottom, box.right, box.top] == [4000, 7500, 9000, 12500]
end
unless gate_box && sd_cut && ((gate_box.left - 2000 - sd_cut.right) * layout.dbu).round(3) >= 8.0
  raise "minimum native gate bay must preserve the final 8um TiN/CNT clearance"
end

pch = variant(layout, lib, "Pch", {"use_nwell" => true})
expect_bbox(layout, pch, 3, [0.0, 0.0, 48.0, 40.0])
expect_bbox(layout, pch, 18, [-2.0, -2.0, 50.0, 42.0])
expect_bbox(layout, pch, 1, [-2.0, -2.0, 50.0, 42.0])
expect_absent(layout, pch, 19)
pmos_diff = boxes_um(layout, pch, 3)
raise "ordinary Pch must have only its channel/S-D diffusion, not a body-contact island" unless pmos_diff == [[0.0, 0.0, 48.0, 40.0]]
pplus_area = boxes_um(layout, pch, 18)
raise "ordinary Pch must retain only its device P+ area" unless pplus_area == [[-2.0, -2.0, 50.0, 42.0]]


psub_nch = variant(layout, lib, "Nch", {"with_psubcont" => true})
expect_bbox(layout, psub_nch, 3, [-6.5, 0.0, 48.0, 44.5])
expect_bbox(layout, psub_nch, 18, [-8.5, 29.5, 8.5, 46.5])
expect_absent(layout, psub_nch, 1)

pch_nplus_island = variant(
    layout, lib, "Pch", {"experimental_nplus_island" => true}
)
expect_bbox(layout, pch_nplus_island, 19, [-8.5, 49.5, 8.5, 66.5])
expect_bbox(layout, pch_nplus_island, 1, [-8.5, -2.0, 50.0, 66.5])
channel_diff = [0.0, 0.0, 48.0, 40.0]
nplus_island_diff = [-6.5, 51.5, 6.5, 64.5]
island_diff = boxes_um(layout, pch_nplus_island, 3)
unless island_diff.length == 2 &&
       island_diff.include?(channel_diff) &&
       island_diff.include?(nplus_island_diff) &&
       nplus_island_diff[1] > channel_diff[3]
  raise "experimental Pch N+ SOI island must be separate from the PMOS channel diffusion"
end
nplus_island = boxes_um(layout, pch_nplus_island, 19)
pmos_parea = boxes_um(layout, pch_nplus_island, 18)
unless nplus_island == [[-8.5, 49.5, 8.5, 66.5]] &&
       pmos_parea == [[-2.0, -2.0, 50.0, 42.0]] &&
       nplus_island[0][1] > pmos_parea[0][3]
  raise "experimental Pch N+ SOI island must not overlap the PMOS P+ area"
end

# Protected gate bays are formula-derived; the minimum bay preserves the
# native source/drain contact clearance while retaining the physical channel L.
nch_no_bus = variant(layout, lib, "Nch", {"n" => 2, "no_finger_conn" => true})
nch_bus = variant(layout, lib, "Nch", {"n" => 2, "no_finger_conn" => false})
expect_bbox(layout, nch_no_bus, 21, [21.0, 0.0, 62.0, 20.0])
expect_absent(layout, nch, 5, 1)
tied_nch = variant(layout, lib, "Nch", {"body_tie" => true})

# The PCell guard reports through KLayout's log, not to the caller: the raw
# add_pcell_variant returns an unnamed, empty cell.  Callers that need a
# handleable rejection run the shared validator first, which raises.
begin
  ICPS2023_5NativeGeometry.validate_supported_parameters!("Pch", true)
  raise "Pch body_tie=true passed the pre-call validation"
rescue => error
  raise unless error.message.include?("PMOS body ties are unsupported")
end
raw_guard = variant(layout, lib, "Pch", {"body_tie" => true})
raise "raw PCell guard must leave an unnamed cell" unless raw_guard.name.empty?
raw_shapes = 0
layout.layer_indices.each { |li| raw_shapes += raw_guard.shapes(li).size }
raise "raw PCell guard must not produce geometry" unless raw_shapes == 0

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
expect_bbox(layout, ncap, 3, [0.0, -4.0, 100.0, 119.5])
expect_bbox(layout, ncap, 16, [0.0, 0.0, 100.0, 100.0])
expect_bbox(layout, ncap, 19, [-2.0, -6.0, 102.0, 121.5])
expect_bbox(layout, ncap, 5, [-4.0, 0.0, 104.0, 100.0])

pcap = variant(layout, lib, "Pdiff_cap")
expect_bbox(layout, pcap, 18, [-2.0, -6.0, 102.0, 121.5])
expect_absent(layout, pcap, 19)

# TiN capacitor plates and overlap are distinct design masks.
tin_cap = variant(layout, lib, "TIN_cap")
expect_bbox(layout, tin_cap, 8, [0.0, 0.0, 127.5, 121.0])
expect_bbox(layout, tin_cap, 16, [23.5, 0.0, 123.5, 100.0])
expect_bbox(layout, tin_cap, 5, [23.5, -4.0, 123.5, 121.0])

bridge = variant(layout, lib, "Bridge")
expect_bbox(layout, bridge, 5, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, bridge, 15, [1.5, 6.5, 26.5, 51.5])
expect_bbox(layout, bridge, 8, [0.0, 0.0, 33.0, 53.0])

bent_bridge = variant(
    layout, lib, "Bridge", {"l" => 25.0, "w" => 10.0, "s" => "20"}
)
expect_bbox(layout, bent_bridge, 5, [1.5, 1.5, 31.5, 36.5])
expect_bbox(layout, bent_bridge, 15, [1.5, 6.5, 26.5, 36.5])
# The stored parameter readback keeps the requested values (coerce corrections
# are not exposed), so the bend is checked on the drawn TiN/RES bodies; the
# intended 90 ohm value (20 ohm/sq x (25+20)/10) is only observable through the
# LVS R_TIN extraction, which currently mis-measures bent shapes (known gap).
cnt_layer = layout.layer(RBA::LayerInfo.new(7, 0))
raise "bent Bridge must expose two physical CNT terminals" unless bent_bridge.shapes(cnt_layer).size == 2

nbridge = variant(layout, lib, "Nbridge")
expect_bbox(layout, nbridge, 3, [1.5, 1.5, 31.5, 51.5])
expect_bbox(layout, nbridge, 15, [1.5, 6.5, 26.5, 51.5])
expect_bbox(layout, nbridge, 19, [-0.5, -0.5, 33.5, 53.5])
expect_absent(layout, nbridge, 5)

# Default-profile corpus must not leak unqualified upper-metal masks.
[
 nch, pch, nch_no_bus, nch_bus, tied_nch, psub_nch, pch_nplus_island,
 r_ndiff, r_pdiff, ncap, pcap, tin_cap, bridge, nbridge
].each do |cell|
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
