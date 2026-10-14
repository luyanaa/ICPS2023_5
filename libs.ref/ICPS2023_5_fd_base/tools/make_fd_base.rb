# =============================================================================
# make_fd_base.rb -- build libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds
#
# The base library is the chip frame of the minimal Fab SOI-CMOS process:
# the 1600 x 1600 um frame/seal plus the 28 wire-bond pads, WITHOUT the
# pre-placed device arrays (pmos_array / nmos_array / resistor_array /
# tin_m1_cap_array) that the design-contest template carried.
#
# Source: Samples/Semicon2023/base_contest2023.GDS, top cell base_Rev0.93
#         (minimal Fab design contest 2023 template, Rev 0.93).
#
# Usage:
#   cd <repo root>
#   klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_fd_base.rb \
#           [-rd src=Samples/Semicon2023/base_contest2023.GDS] \
#           [-rd out=libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds]
#
# Copyright 2023 minimalFab Promoting Organization
# Licensed under the Apache License, Version 2.0
# =============================================================================

src = ($src && $src.to_s != "") ? $src : "Samples/Semicon2023/base_contest2023.GDS"
out = ($out && $out.to_s != "") ? $out : "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"

# Cells that belong to the template's device arrays, not to the frame.
DEVICE_ARRAYS = %w[base_array pmos_array nmos_array resistor_array tin_m1_cap_array]
PAD_CELLS     = %w[pad_60_m1_tin tin_M1]
FRAME_CELLS   = %w[16_5x5_flame]

ly = RBA::Layout::new
ly.read(src)
top = ly.top_cell
puts "source: #{src}  top cell: #{top.name}"

# 1. pads: count instances of the pad cell at the top level, arrays included
pad_instances = 0
top.each_inst do |i|
  next unless PAD_CELLS.include?(i.cell.name)
  pad_instances += i.is_regular_array? ? i.na * i.nb : 1
end
raise "expected #{PAD_CELLS.join('/')} instances in #{top.name}, found #{pad_instances}" if pad_instances == 0
puts "pads found: #{pad_instances} (#{PAD_CELLS.join(', ')})"
raise "expected 28 pads, found #{pad_instances}" if pad_instances != 28

# 2. drop the pre-placed device arrays and their device cells
DEVICE_ARRAYS.each do |name|
  c = ly.cell(name)
  next unless c
  puts "removing instance(s) of #{name}"
  ly.delete_cell(c.cell_index)
end

# 3. prune every cell that is no longer referenced (device cells, contest
#    leftovers); keep the top cell, pads and frame.
changed = true
while changed
  changed = false
  doomed = []
  ly.each_cell do |c|
    next if c == top
    refs = 0
    ly.each_cell { |other| other.each_inst { |i| refs += 1 if i.cell == c } }
    doomed << c if refs == 0
  end
  doomed.each do |c|
    puts "pruning unreferenced cell #{c.name}"
    ly.delete_cell(c.cell_index)
    changed = true
  end
end

# 4. rename the top cell to the library name
top.name = "ICPS2023_5_fd_base"

# 5. sanity check: pads and frame still present, no device layers left over
ly.top_cells.each do |tc|
  bb = tc.bbox
  puts "top cell: #{tc.name}  #{(bb.width * ly.dbu).round(1)} x #{(bb.height * ly.dbu).round(1)} um"
  cells = Hash.new(0)
  tc.each_inst { |i| cells[i.cell.name] += 1 }
  cells.each { |k, v| puts "  instance #{k} x#{v}" }
end
raise "frame cell missing" unless ly.cell("16_5x5_flame")

ly.write(out)
puts "written #{out}"
