# =============================================================================
# make_lef_lib.rb -- generate pad-cell LEF/Liberty views and fixed frame I/O DEF
#                    for libs.ref/ICPS2023_5_fd_base/
#
# The full-frame GDS is a physical overlay, not a placeable block macro. The
# DEF exposes its 28 ML1 pad shapes as independent fixed top-level pins; the
# LEF contains only the reusable 60 um pad-cell macro.
# Liberty is physical-only: pad capacitance has no qualified return-network
# model, and no timing or transition data is characterized.
#
# Optional LibreLane alias views map physical ML1 to the router name M1.
# They do not change the canonical PDK LEF/DEF names or GDS.
# Usage:
#   cd <repo root>
#   klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb \
#           [-rd gds=libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds] \
#           [-rd lef=libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef] \
#           [-rd lib=libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib] \
#           [-rd def=libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def] \
#           [-rd librelane_lef=libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base_librelane.lef] \
#           [-rd librelane_def=libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base_librelane.def]
#
# Copyright 2023 minimalFab Promoting Organization
# Licensed under the Apache License, Version 2.0
# =============================================================================
require "fileutils"

gds_file = ($gds && $gds.to_s != "") ? $gds : "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"
lef_file = ($lef && $lef.to_s != "") ? $lef : "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef"
lib_file = ($lib && $lib.to_s != "") ? $lib : "libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib"
def_file = ($def && $def.to_s != "") ? $def : "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def"
librelane_lef_file = ($librelane_lef && $librelane_lef.to_s != "") ? $librelane_lef : "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base_librelane.lef"
librelane_def_file = ($librelane_def && $librelane_def.to_s != "") ? $librelane_def : "libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base_librelane.def"

FRAME_CELL = "ICPS2023_5_fd_base"
PIN_LAYER = [8, 0]     # ML1: the pad metal that a bond/beam actually touches
MACRO_CLASS = {
  "pad_60_m1_tin" => "PAD",
  "PAD60"         => "PAD",
  "PAD100"        => "PAD",
  "PAD150"        => "PAD",
}
# LEF layer names for the design layers that occur in this library.
LEF_LAYER = {
  [1, 0] => "NWL", [3, 0] => "DIFF", [5, 0] => "TIN", [5, 1] => "TIN_BLK",
  [7, 0] => "CNT", [8, 0] => "ML1", [9, 0] => "VIA1", [10, 0] => "ML2",
  [11, 0] => "VIA2", [12, 0] => "ML3", [13, 0] => "TEXT", [14, 0] => "FRAME",
  [15, 0] => "RES", [16, 0] => "CAP", [17, 0] => "DIO", [18, 0] => "PAREA",
  [19, 0] => "NAREA", [21, 0] => "NCH", [50, 0] => "PAD",
}

ly = RBA::Layout::new
ly.read(gds_file)
dbu = ly.dbu
ip = (1.0 / dbu).round

def cell_region(gds_file, cell_name, lspec)
  # RecursiveShapeIterator does not fold array-instance transforms into the
  # delivered shapes, so read a private copy of the file and flatten the cell
  # before collecting geometry.
  work = RBA::Layout::new
  work.read(gds_file)
  cell = work.cell(cell_name)
  return [RBA::Region::new, nil] unless cell
  bb = cell.bbox
  li = work.find_layer(RBA::LayerInfo::new(lspec[0], lspec[1]))
  region = RBA::Region::new
  if li
    cell.flatten(true)
    cell.shapes(li).each do |s|
      region.insert(s.polygon) if s.is_polygon? || s.is_box?
    end
  end
  [region.merged, bb]
end

# Emit LEF geometry for a region, relative to (x0, y0) in database units.
def lef_geometry(region, x0, y0, dbu)
  out = []
  region.each do |poly|
    if poly.is_box?
      b = poly.bbox
      out << "      RECT #{(b.left*dbu - x0).round(3)} #{(b.bottom*dbu - y0).round(3)}" \
             " #{(b.right*dbu - x0).round(3)} #{(b.top*dbu - y0).round(3)} ;"
    else
      b = poly.bbox
      pts = poly.each_point.map { |p| "#{(p.x*dbu - x0).round(3)} #{(p.y*dbu - y0).round(3)}" }
      out << "      POLYGON #{pts.join(' ')} ;"
    end
  end
  out
end
frame_pin_region, frame_bbox = cell_region(gds_file, FRAME_CELL, PIN_LAYER)
raise "frame cell #{FRAME_CELL} is missing from #{gds_file}" unless frame_bbox
frame_pins = frame_pin_region.each.map do |poly|
  raise "frame pad pin is not rectangular" unless poly.is_box?
  box = poly.bbox
  expected_size = (60.0 / dbu).round
  unless box.width == expected_size && box.height == expected_size
    raise "frame pad pin is #{box.width * dbu} x #{box.height * dbu} um, expected 60 x 60 um"
  end
  { x: box.left, y: box.bottom, width: box.width, height: box.height }
end
raise "expected 28 separate frame pad pins, found #{frame_pins.size}" unless frame_pins.size == 28
frame_pins.sort_by! { |pin| [-(2 * pin[:y] + pin[:height]), pin[:x]] }
frame_pins.each_with_index { |pin, i| pin[:name] = "PAD_%02d" % i }

frame_width = (frame_bbox.width * dbu * ip).round
frame_height = (frame_bbox.height * dbu * ip).round

macros = []

ly.each_cell do |cell|
  klass = MACRO_CLASS[cell.name] or next
  bb = cell.bbox
  next if bb.empty?
  w = (bb.width * dbu).round(3)
  h = (bb.height * dbu).round(3)
  x0 = bb.left * dbu
  y0 = bb.bottom * dbu

  # Pin geometry: the ML1 (pad metal) region, merged so overlaps are counted
  # once. Everything else becomes an obstruction.
  pin_region = nil
  obs = {}
  LEF_LAYER.keys.sort.each do |lspec|
    next if lspec == [13, 0]   # TEXT carries no geometry
    name = LEF_LAYER[lspec]
    region, = cell_region(gds_file, cell.name, lspec)
    next if region.count == 0
    if lspec == PIN_LAYER
      pin_region = region
    else
      obs[name] = lef_geometry(region, x0, y0, dbu)
    end
  end
  pin_rects = pin_region ? lef_geometry(pin_region, x0, y0, dbu) : []

  macros << { name: cell.name, klass: klass, w: w, h: h,
              pins: pin_rects, obs: obs, area: (w * h).round(3) }
end

macros.sort_by! { |m| m[:name] }
raise "no macros found in #{gds_file}" if macros.empty?

# ---------------------------------------------------------------- LEF --------
lef = []
lef << "# Reusable pad-cell LEF only; the complete frame remains a GDS overlay."
lef << "# Fixed top-level pins are in libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base.def."
lef << "# Layer names follow libs.tech/klayout/doc/layers.md."
lef << "# A process LEF tech file is not shipped."
lef << "VERSION 5.7 ;"
lef << "BUSBITCHARS \"[]\" ;"
lef << "DIVIDERCHAR \"/\" ;"
lef << "UNITS"
lef << "  DATABASE MICRONS 1000 ;   # PDK database unit is 0.001 um"
lef << "END UNITS"
lef << "MANUFACTURINGGRID 0.001 ;"
lef << ""
macros.each do |m|
  lef << "MACRO #{m[:name]}"
  lef << "  CLASS #{m[:klass]} ;"
  lef << "  ORIGIN 0 0 ;"
  lef << "  FOREIGN #{m[:name]} 0 0 ;"
  lef << "  SIZE #{m[:w].to_s} BY #{m[:h].to_s} ;"
  lef << "  SYMMETRY X Y ;"
  unless m[:pins].empty?
    layer = LEF_LAYER[PIN_LAYER]
    lef << "  PIN PAD"
    lef << "    DIRECTION INOUT ;"
    lef << "    USE ANALOG ;"
    lef << "    PORT"
    lef << "      LAYER #{layer} ;"
    m[:pins].each { |line| lef << line }
    lef << "    END"
    lef << "  END PAD"
  end
  unless m[:obs].empty?
    lef << "  OBS"
    m[:obs].keys.sort.each do |layer|
      lef << "    LAYER #{layer} ;"
      m[:obs][layer].each { |line| lef << line }
    end
    lef << "  END"
  end
  lef << "END #{m[:name]}"
  lef << ""
end
File.write(lef_file, lef.join("\n"))
def_lines = []
def_lines << "VERSION 5.8 ;"
def_lines << "DIVIDERCHAR \"/\" ;"
def_lines << "BUSBITCHARS \"[]\" ;"
def_lines << "DESIGN #{FRAME_CELL} ;"
def_lines << "UNITS DISTANCE MICRONS #{ip} ;"
def_lines << "DIEAREA ( #{(frame_bbox.left * dbu * ip).round} #{(frame_bbox.bottom * dbu * ip).round} )" \
             " ( #{(frame_bbox.right * dbu * ip).round} #{(frame_bbox.top * dbu * ip).round} ) ;"
def_lines << ""
def_lines << "PINS #{frame_pins.size} ;"
frame_pins.each do |pin|
  width = (pin[:width] * dbu * ip).round
  height = (pin[:height] * dbu * ip).round
  x = (pin[:x] * dbu * ip).round
  y = (pin[:y] * dbu * ip).round
  def_lines << "- #{pin[:name]} + NET #{pin[:name]} + DIRECTION INOUT + USE SIGNAL"
  def_lines << "  + PORT"
  def_lines << "    + LAYER #{LEF_LAYER[PIN_LAYER]} ( 0 0 ) ( #{width} #{height} )"
  def_lines << "    + FIXED ( #{x} #{y} ) N ;"
end
def_lines << "END PINS"
def_lines << ""
def_lines << "NETS #{frame_pins.size} ;"
frame_pins.each { |pin| def_lines << "- #{pin[:name]} ( PIN #{pin[:name]} ) ;" }
def_lines << "END NETS"
def_lines << "END DESIGN"
FileUtils.mkdir_p(File.dirname(def_file))
File.write(def_file, def_lines.join("\n"))
librelane_lef = lef.map { |line| line.gsub(/\bML1\b/, "M1") }
librelane_lef[0] = "# LibreLane alias view: M1 uses physical design layer ML1 (GDS 8/0)."
librelane_lef[1] = "# Geometry matches the canonical pad LEF; only the router layer name differs."
librelane_lef[2] = "# Fixed top-level pins are in the LibreLane-compatible DEF view."
librelane_lef[3] = "# This is not a process technology LEF."
FileUtils.mkdir_p(File.dirname(librelane_lef_file))
File.write(librelane_lef_file, librelane_lef.join("\n"))

librelane_def = def_lines.map { |line| line.gsub(/\bML1\b/, "M1") }
librelane_def.unshift("# LibreLane alias view: M1 maps to physical design layer ML1 (GDS 8/0).")
FileUtils.mkdir_p(File.dirname(librelane_def_file))
File.write(librelane_def_file, librelane_def.join("\n"))



# ------------------------------------------------------------- Liberty -------
tl = []
tl << "/* ICPS2023_5_fd_base -- physical-only Liberty view of the pad cell"
tl << " *"
tl << " * Generated from libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds."
tl << " * No pad-to-substrate capacitance or timing constraint is characterized."
tl << " * ML1 and TiN are electrically shorted in the drawn pad; a return-net"
tl << " * capacitance cannot be inferred from their intervening dielectric."
tl << " *"
tl << " * Do not use this library for timing-driven optimization or electrical"
tl << " * signoff. It carries physical cell and pin identity only."
tl << " */"
tl << "library (ICPS2023_5_fd_base) {"
tl << "  comment : \"minimal Fab SOI-CMOS 1600x1600 um frame and 60 um pad ring\";"
tl << "  delay_model : generic_cmos ;"
tl << "  time_unit : \"1ns\";"
tl << "  voltage_unit : \"1V\";"
tl << "  current_unit : \"1mA\";"
tl << "  capacitive_load_unit (1, ff);"
tl << "  pulling_resistance_unit : \"1kohm\";"
tl << "  leakage_power_unit : \"1nW\";"
tl << "  nom_process : 1.0;"
tl << "  nom_temperature : 25.0;"
tl << "  nom_voltage : 3.3;"
tl << "  operating_conditions (typical) {"
tl << "    process : 1.0;"
tl << "    temperature : 25.0;"
tl << "    voltage : 3.3;"
tl << "  }"
tl << "  default_operating_conditions : typical;"
tl << "  voltage_map (VDD, 3.3);"
tl << "  voltage_map (VSS, 0.0);"
tl << ""
macros.each do |m|
  tl << "  cell (#{m[:name]}) {"
  tl << "    area : #{m[:area].to_s};"
  tl << "    pad_cell : true;"
  tl << "    cell_footprint : \"#{m[:name]}\";"
  tl << "    pin (PAD) {"
  tl << "      direction : inout;"
  tl << "    }"
  tl << "  }"
  tl << ""
end
tl << "}"
File.write(lib_file, tl.join("\n"))

puts "wrote #{lef_file}, #{lib_file}, and #{def_file}"
puts "wrote #{librelane_lef_file} and #{librelane_def_file} (ML1-to-M1 LibreLane aliases)"
puts "  #{macros.map { |m| m[:name] }.join(', ')}: pad-cell LEF/Liberty"
puts "  #{frame_pins.size} fixed frame pins: #{frame_pins.first[:name]} through #{frame_pins.last[:name]}"
