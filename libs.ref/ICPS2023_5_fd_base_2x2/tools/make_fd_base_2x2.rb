# =============================================================================
# make_fd_base_2x2.rb -- build
#   libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds
# and
#   libs.ref/ICPS2023_5_fd_base_2x2/doc/pad_map.csv
#
# The 2x2 frame is the 3200 x 3200 um die-size option of the minimal Fab
# SOI-CMOS platform: four times the area of the 1600 x 1600 um 1x1 frame
# (libs.ref/ICPS2023_5_fd_base) and twice as many wire-bond pads, 56 instead
# of 28. The foundry published the 1x1 template only
# (Samples/Semicon2023/base_contest2023.GDS, top cell base_Rev0.93); it ships
# no 2x2 frame GDS. This script derives the 2x2 frame from that same template
# with the convention documented in libs.ref/ICPS2023_5_fd_base_2x2/doc/README.md:
#
#   * frame ring: two boxes on FRAME 14/0, die boundary +/-1600 um and inner
#     boundary +/-1550 um; the ring width stays the foundry 1x1 value, 50 um
#   * pads: the unchanged foundry pad cell pad_60_m1_tin (60 x 60 um), outer
#     edge flush with the inner ring boundary, 200 um pitch, rows centred on
#     the die; north/south 8 -> 16 pads, west/east 6 -> 12 pads
#   * labels pin01..pin56 continue the 1x1 counter-clockwise ring numbering:
#     south left-to-right, east bottom-to-top, north right-to-left, west
#     top-to-bottom
#
# Like the 1x1 library this is a physical-only frame + pad overlay: no
# transistors, logic, or characterized I/O cells, and no pre-placed device
# arrays. The pre-placed base_array of the template is not instantiated.
#
# Usage:
#   cd <repo root>
#   klayout -b -r libs.ref/ICPS2023_5_fd_base_2x2/tools/make_fd_base_2x2.rb \
#           [-rd src=Samples/Semicon2023/base_contest2023.GDS] \
#           [-rd out=libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds] \
#           [-rd pad_map=libs.ref/ICPS2023_5_fd_base_2x2/doc/pad_map.csv]
#
# Copyright 2023 minimalFab Promoting Organization
# Licensed under the Apache License, Version 2.0
# =============================================================================
require "fileutils"

src = ($src && $src.to_s != "") ? $src : "Samples/Semicon2023/base_contest2023.GDS"
out = ($out && $out.to_s != "") ? $out : "libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds"
map_out = ($pad_map && $pad_map.to_s != "") ? $pad_map : "libs.ref/ICPS2023_5_fd_base_2x2/doc/pad_map.csv"

TOP_CELL   = "ICPS2023_5_fd_base_2x2"
FRAME_CELL = "frame_3200"
PAD_CELL   = "pad_60_m1_tin"
PAD_CHILD  = "tin_M1"
ML1_LAYER  = [8, 0]    # drawing ML1: the pad metal a bond/beam actually touches;
                       # the shipped GDS is converted to foundry M1(Al) 7/0 by the
                       # fab-prep step in the regeneration pipeline
FRAME_LAYER = [14, 0]  # chip frame and seal, same layer as the 1x1 frame ring

# ---------------------------------------------------------------- geometry ---
DIE_HALF   = 1600      # um, die boundary: 3200 x 3200 um (4x the 1x1 die)
RING_WIDTH = 50        # um, ring width, unchanged from the foundry 1x1 frame
RING_INNER = DIE_HALF - RING_WIDTH   # um, 1550
PAD_SIZE   = 60        # um, foundry pad cell size
PAD_PITCH  = 200       # um, pad pitch, unchanged from the foundry 1x1 frame
LABEL_SIZE = 100000    # dbu = 100 um, the foundry template's label height

# Pad rows. na pad centres per side, first centre (x0, y0), step (ax, ay) um,
# instance rotation of the foundry template, and the label numbers along the
# row (label0 at k = 0, step dl).
ROWS = [
  { side: "north", na: 16, x0:  1500, y0:  1520, ax: -200, ay:  0, rot: 1, label0: 29, dl:  1 },
  { side: "south", na: 16, x0:  1500, y0: -1520, ax: -200, ay:  0, rot: 1, label0: 16, dl: -1 },
  { side: "west",  na: 12, x0: -1520, y0:  1100, ax:    0, ay: -200, rot: 2, label0: 45, dl:  1 },
  { side: "east",  na: 12, x0:  1520, y0:  1100, ax:    0, ay: -200, rot: 2, label0: 28, dl: -1 },
]
PAD_COUNT = ROWS.map { |r| r[:na] }.inject(0, :+)
raise "expected 56 pads, geometry table has #{PAD_COUNT}" if PAD_COUNT != 56

ly = RBA::Layout::new
ly.read(src)
raise "expected exactly one top cell in #{src}" if ly.top_cells.size != 1
src_top = ly.top_cell
puts "source: #{src}  top cell: #{src_top.name}  dbu=#{ly.dbu}"
ip = (1.0 / ly.dbu).round

pad_cell = ly.cell(PAD_CELL)
raise "#{PAD_CELL} is missing from #{src}" unless pad_cell
raise "#{PAD_CHILD} is missing from #{src}" unless ly.cell(PAD_CHILD)
pb = pad_cell.bbox
unless pb.width == PAD_SIZE * ip && pb.height == PAD_SIZE * ip
  raise "#{PAD_CELL} is #{pb.width * ly.dbu} x #{pb.height * ly.dbu} um," \
        " expected #{PAD_SIZE} x #{PAD_SIZE} um"
end

# 1. ring cell: die boundary and inner boundary of the frame drawing. The
#    foundry 1x1 ring (16_5x5_flame) is the same two-box drawing at 800/750 um;
#    its "5x5" wafer-map suffix is dropped because the foundry has not
#    specified the 2x2 wafer map.
frame_cell = ly.create_cell(FRAME_CELL)
frame_li = ly.layer(RBA::LayerInfo::new(FRAME_LAYER[0], FRAME_LAYER[1]))
frame_cell.shapes(frame_li).insert(
  RBA::Box::new(-DIE_HALF * ip, -DIE_HALF * ip, DIE_HALF * ip, DIE_HALF * ip))
frame_cell.shapes(frame_li).insert(
  RBA::Box::new(-RING_INNER * ip, -RING_INNER * ip, RING_INNER * ip, RING_INNER * ip))

# 2. top cell: ring instance, 4 pad arrays, 56 ML1 pin labels
top = ly.create_cell(TOP_CELL)
top.insert(RBA::CellInstArray::new(frame_cell.cell_index, RBA::Trans::new(0, false, 0, 0)))
ml1_li = ly.layer(RBA::LayerInfo::new(ML1_LAYER[0], ML1_LAYER[1]))

pads = []
ROWS.each do |row|
  top.insert(RBA::CellInstArray::new(
    pad_cell.cell_index,
    RBA::Trans::new(row[:rot], false, row[:x0] * ip, row[:y0] * ip),
    RBA::Vector::new(row[:ax] * ip, row[:ay] * ip), RBA::Vector::new(0, 0), row[:na], 1))
  row[:na].times do |k|
    x = row[:x0] + k * row[:ax]
    y = row[:y0] + k * row[:ay]
    label = "pin%02d" % (row[:label0] + k * row[:dl])
    top.shapes(ml1_li).insert(RBA::Text::new(
      label, RBA::Trans::new(0, false, x * ip, y * ip), LABEL_SIZE, 0))
    pads << { x: x, y: y, side: row[:side], label: label }
  end
end

labels_seen = pads.map { |pad| pad[:label] }
expected_labels = (1..PAD_COUNT).map { |index| "pin%02d" % index }
raise "label set mismatch: #{labels_seen.sort}" if labels_seen.sort != expected_labels

# 3. the template's own contents (device arrays, 1x1 ring, 1x1 pad ring,
#    top cell) must not survive in the 2x2 overlay
ly.delete_cell(src_top.cell_index)
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
remaining = []
ly.each_cell { |c| remaining << c.name }
remaining.sort!
expected_cells = [TOP_CELL, FRAME_CELL, PAD_CELL, PAD_CHILD].sort
raise "unexpected cell set #{remaining}" if remaining != expected_cells

# 4. pre-write geometry contract
die = top.bbox
unless die.left == -DIE_HALF * ip && die.bottom == -DIE_HALF * ip &&
       die.right == DIE_HALF * ip && die.top == DIE_HALF * ip
  raise "die bbox is #{(die.width * ly.dbu).round(3)} x #{(die.height * ly.dbu).round(3)} um," \
        " expected #{2 * DIE_HALF} x #{2 * DIE_HALF} um"
end
pads.each do |pad|
  half = PAD_SIZE / 2.0
  flush = case pad[:side]
          when "north" then pad[:y] + half == RING_INNER
          when "south" then pad[:y] - half == -RING_INNER
          when "west"  then pad[:x] - half == -RING_INNER
          when "east"  then pad[:x] + half == RING_INNER
          end
  raise "#{pad[:label]} is not flush with the inner ring boundary" unless flush
end
north = pads.select { |pad| pad[:side] == "north" }.map { |pad| pad[:x] }.sort
south = pads.select { |pad| pad[:side] == "south" }.map { |pad| pad[:x] }.sort
[["north", north], ["south", south]].each do |side, xs|
  span = (xs.last - xs.first) + PAD_SIZE
  unless xs.first - PAD_SIZE / 2 == -RING_INNER + 20 && xs.last + PAD_SIZE / 2 == RING_INNER - 20
    raise "#{side} row does not keep the 1x1 20 um end margin: span #{span} um"
  end
  xs.each_cons(2) { |a, b| raise "#{side} pad pitch is not #{PAD_PITCH} um" if b - a != PAD_PITCH }
end
%w[west east].each do |side|
  ys = pads.select { |pad| pad[:side] == side }.map { |pad| pad[:y] }.sort
  unless ys.first + ys.last == 0
    raise "#{side} row is not centred on the die: #{ys.first}..#{ys.last} um"
  end
  ys.each_cons(2) { |a, b| raise "#{side} pad pitch is not #{PAD_PITCH} um" if b - a != PAD_PITCH }
end
pads.combination(2).each do |a, b|
  unless (a[:x] - b[:x]).abs >= PAD_SIZE || (a[:y] - b[:y]).abs >= PAD_SIZE
    raise "#{a[:label]} and #{b[:label]} overlap"
  end
end
puts "geometry: die #{2 * DIE_HALF} x #{2 * DIE_HALF} um, ring #{RING_WIDTH} um," \
     " #{PAD_COUNT} pads at #{PAD_PITCH} um pitch (north/south 16, west/east 12)"

# 5. write GDS and pad map
FileUtils.mkdir_p(File.dirname(out))
options = RBA::SaveLayoutOptions::new
options.gds2_write_timestamps = false   # keep regeneration byte-reproducible
ly.write(out, options)
puts "written #{out}"

# 6. read-back contract, independent of the in-memory construction
chk = RBA::Layout::new
chk.read(out)
chip = chk.cell(TOP_CELL)
raise "#{TOP_CELL} is missing from #{out}" unless chip
die = chip.bbox
unless die.left == -DIE_HALF * ip && die.bottom == -DIE_HALF * ip &&
       die.right == DIE_HALF * ip && die.top == DIE_HALF * ip
  raise "written die bbox is #{(die.width * chk.dbu).round(3)} x #{(die.height * chk.dbu).round(3)} um"
end
ring = chk.cell(FRAME_CELL)
raise "#{FRAME_CELL} is missing from #{out}" unless ring
ring_li = chk.find_layer(RBA::LayerInfo::new(FRAME_LAYER[0], FRAME_LAYER[1]))
boxes = []
ring.shapes(ring_li).each { |s| boxes << s.box if s.is_box? }
boxes.sort_by! { |b| b.width }
unless boxes.size == 2 && boxes[0].width == 2 * RING_INNER * ip && boxes[0].height == 2 * RING_INNER * ip &&
       boxes[1].width == 2 * DIE_HALF * ip && boxes[1].height == 2 * DIE_HALF * ip
  raise "written ring is not the expected two boxes"
end
arrays = []
chip.each_inst { |i| arrays << i if i.cell.name == PAD_CELL }
unless arrays.size == 4 && arrays.all? { |i| i.is_regular_array? }
  raise "expected 4 regular pad arrays, found #{arrays.size}"
end
counts = arrays.map { |i| i.na * i.nb }.sort
raise "pad arrays hold #{counts.inspect}, expected [12, 12, 16, 16]" unless counts == [12, 12, 16, 16]

chip.flatten(true)
chk_ml1 = chk.find_layer(RBA::LayerInfo::new(ML1_LAYER[0], ML1_LAYER[1]))
all_boxes = []
chk_labels = {}
chip.shapes(chk_ml1).each do |shape|
  if shape.is_text?
    text = shape.text
    chk_labels[text.string] = [(text.trans.disp.x * chk.dbu).round(3), (text.trans.disp.y * chk.dbu).round(3)]
  else
    all_boxes << shape.box
  end
end
plates = all_boxes.select { |b| b.width == PAD_SIZE * ip && b.height == PAD_SIZE * ip }
unless plates.size == PAD_COUNT
  raise "written GDS has #{plates.size} pad plates, expected #{PAD_COUNT}"
end
(all_boxes - plates).each do |b|
  unless plates.any? { |p| p.left <= b.left && p.right >= b.right && p.bottom <= b.bottom && p.top >= b.top }
    raise "ML1 geometry outside the pad plates: #{b.to_s}"
  end
end
centers = plates.map { |b| [((b.left + b.right) / 2.0 * chk.dbu).round(3), ((b.bottom + b.top) / 2.0 * chk.dbu).round(3)] }
pads.each do |pad|
  unless centers.include?([pad[:x].round(3), pad[:y].round(3)])
    raise "#{pad[:label]} has no written pad rectangle at #{pad[:x]},#{pad[:y]}"
  end
  unless chk_labels[pad[:label]] == [pad[:x], pad[:y]]
    raise "#{pad[:label]} label is at #{chk_labels[pad[:label]].inspect}, expected #{[pad[:x], pad[:y]].inspect}"
  end
end
raise "written label set mismatch" if chk_labels.keys.sort != expected_labels

# DEF pin names follow the 1x1 rule: rows top to bottom, then left to right.
sorted = pads.sort_by { |pad| [-pad[:y], pad[:x]] }
rows_out = sorted.each_with_index.map do |pad, index|
  "PAD_%02d,%s,%s,%s,%s,%s" % [
    index, pad[:label], PAD_CELL, pad[:side],
    (pad[:x] == pad[:x].to_i) ? pad[:x].to_i.to_s : "%.3f" % pad[:x],
    (pad[:y] == pad[:y].to_i) ? pad[:y].to_i.to_s : "%.3f" % pad[:y]
  ]
end
header = "def_pin,source_pin_label,gds_cell,array_side,center_x_um,center_y_um"
FileUtils.mkdir_p(File.dirname(map_out))
File.write(map_out, ([header] + rows_out).join("\n") + "\n")
puts "written #{map_out} (#{rows_out.size} pins)"
