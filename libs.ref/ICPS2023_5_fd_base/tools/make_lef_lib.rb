# =============================================================================
# make_lef_lib.rb -- generate the LEF and Liberty views of
#                    libs.ref/ICPS2023_5_fd_base/
#
# The base library is a metal-only pad ring: geometry but no transistors, so
# both views are derived directly from the GDS:
#   * LEF   : macro outlines, pad pins (the ML1 shape of each pad cell) and the
#             remaining geometry as obstructions, on the PDK's design layers.
#   * Liberty: one cell per pad macro, area and pin capacitance computed from
#             the drawn ML1 area and the documented TEOS areal capacitance
#             (0.115 fF/um^2 = eps_ox/300 nm, PDK reference manual "TiN_cap").
#             There are NO timing arcs: the foundry does not characterise an
#             I/O cell for this process, the pads are pass-through metal.
#             Absolute maximum ratings: VDS/VGD/VGS = +/-7 V (PDK documents).
#
# Usage:
#   cd <repo root>
#   klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb \
#           [-rd gds=libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds] \
#           [-rd lef=libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef] \
#           [-rd lib=libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib]
#
# Copyright 2023 minimalFab Promoting Organization
# Licensed under the Apache License, Version 2.0
# =============================================================================

gds_file = ($gds && $gds.to_s != "") ? $gds : "libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds"
lef_file = ($lef && $lef.to_s != "") ? $lef : "libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base.lef"
lib_file = ($lib && $lib.to_s != "") ? $lib : "libs.ref/ICPS2023_5_fd_base/lib/ICPS2023_5_fd_base.lib"

TEOS_CAP = 0.115e-15   # F/um^2, 300 nm TEOS between ML1 and lower plates
PIN_LAYER = [8, 0]     # ML1: the pad metal that a bond/beam actually touches
MACRO_CLASS = {
  "ICPS2023_5_fd_base" => "BLOCK",
  "pad_60_m1_tin"      => "PAD",
  "PAD60"              => "PAD",
  "PAD100"             => "PAD",
  "PAD150"             => "PAD",
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

  # pad capacitance estimate from the merged pad-metal area
  area_um2 = pin_region ? pin_region.area * dbu * dbu : 0.0
  cap_ff = (area_um2 * TEOS_CAP * 1e15).round(4)

  macros << { name: cell.name, klass: klass, w: w, h: h,
              pins: pin_rects, obs: obs, area: (w * h).round(3),
              pin_area: area_um2.round(3), cap_ff: cap_ff }
end

macros.sort_by! { |m| m[:name] }
raise "no macros found in #{gds_file}" if macros.empty?

# ---------------------------------------------------------------- LEF --------
lef = []
lef << "# ICPS2023_5_fd_base -- LEF macro views (generated from the GDS)"
lef << "# Layer names follow the PDK design layers (see libs.tech/klayout/doc/layers.md)."
lef << "# Geometry is metal-only pad ring hardware; no LEF tech file is shipped yet"
lef << "# (that arrives with the LibreLane/OpenROAD bring-up)."
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

# ------------------------------------------------------------- Liberty -------
tl = []
tl << "/* ICPS2023_5_fd_base -- Liberty view of the minimal Fab SOI-CMOS pad ring"
tl << " *"
tl << " * Generated from the GDS by libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb."
tl << " * area        : macro bounding box in um^2"
tl << " * capacitance : drawn pad-metal (ML1) area x #{TEOS_CAP * 1e15} fF/um^2"
tl << " *               (300 nm TEOS over the SOI stack)."
tl << " *"
tl << " * THIS LIBRARY HAS NO TIMING ARCS. The foundry does not characterise an"
tl << " * I/O or pad cell for this process: the pads are pass-through metal, and"
tl << " * the only documented electrical limit is the absolute maximum rating of"
tl << " * +/-7 V (VDS/VGD/VGS, PDK reference manual). Do not use this file for"
tl << " * timing-driven optimisation; it exists so that physical flows can"
tl << " * instantiate the frame and pads."
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
  tl << "      capacitance : #{m[:cap_ff].to_s};   /* #{m[:pin_area].to_s} um^2 pad metal */"
  tl << "      max_transition : 0.0;   /* not characterised */"
  tl << "    }"
  tl << "  }"
  tl << ""
end
tl << "}"
File.write(lib_file, tl.join("\n"))

puts "wrote #{lef_file} and #{lib_file}"
macros.each { |m| puts "  #{m[:name]}: #{m[:w]} x #{m[:h]} um, #{m[:pins].size} pin rects, #{m[:obs].values.map(&:size).inject(0, :+)} obs rects, cap #{m[:cap_ff]} fF" }
