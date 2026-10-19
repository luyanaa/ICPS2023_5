require "fileutils"
require "rexml/document"

output_dir = File.expand_path($output_dir.to_s)
pcell_macro = File.expand_path($pcell_macro.to_s)
raise "pass -rd output_dir=DIR" if $output_dir.to_s.empty?
raise "pass -rd pcell_macro=PATH" if $pcell_macro.to_s.empty?
FileUtils.mkdir_p(output_dir)

xml = REXML::Document.new(File.read(pcell_macro))
source = xml.elements["klayout-macro/text"].text
eval(source, TOPLEVEL_BINDING, pcell_macro)
library = RBA::Library.library_by_name("PCells_ICPS2023_5")
raise "native ICPS2023_5 PCells did not register" unless library

DBU = 0.001
MOS_WIDTH = ($mos_width || 20.0).to_f

MOS_LENGTH = 6.0
CONTACT_SIZE = 13.0
GATE_BAY = 8.0

ML1 = 8
TEXT = 13

def dbu(value)
  (value.to_f / DBU).round
end

def mos_pitch
  CONTACT_SIZE + MOS_LENGTH + 2.0 * GATE_BAY
end

def mos_gate_x(origin_x, finger = 0)
  origin_x + CONTACT_SIZE + GATE_BAY + MOS_LENGTH / 2.0 + finger * mos_pitch
end

def mos_terminal_x(origin_x, terminal = 0)
  origin_x + CONTACT_SIZE / 2.0 + terminal * mos_pitch
end

def mos_gate_y(origin_y)
  origin_y + MOS_WIDTH + 11.5
end

def mos_sd_y(origin_y)
  origin_y + MOS_WIDTH / 2.0
end


def add_box(layout, cell, layer, x1, y1, x2, y2)
  box = RBA::Box.new(*[x1, y1, x2, y2].map { |value| dbu(value) })
  cell.shapes(layout.layer(RBA::LayerInfo.new(layer, 0))).insert(box)
end

def add_wire(layout, cell, x1, y1, x2, y2, width = 6.0)
  half = width / 2.0
  if x1 == x2
    add_box(layout, cell, ML1, x1 - half, [y1, y2].min - half,
            x1 + half, [y1, y2].max + half)
  elsif y1 == y2
    add_box(layout, cell, ML1, [x1, x2].min - half, y1 - half,
            [x1, x2].max + half, y1 + half)
  else
    raise "wire must be Manhattan: (#{x1},#{y1}) to (#{x2},#{y2})"
  end
end

def add_label(layout, cell, name, x, y)
  point = RBA::Point.new(dbu(x), dbu(y))
  text = RBA::Text.new(name, RBA::Trans.new(point))
  cell.shapes(layout.layer(RBA::LayerInfo.new(TEXT, 0))).insert(text)
end

def instantiate_mos(layout, top, library, name, x, y, fingers = 1, options = {})
  parameters = {
    "w" => MOS_WIDTH, "l" => MOS_LENGTH, "n" => fingers,
    "with_via" => false
  }.merge(options)
  parameters["use_nwell"] = true if name == "Pch"
  pcell_id = library.layout.pcell_id(name)
  raise "missing native PCell #{name}" if pcell_id < 0
  # KLayout's PCell engine swallows coerce/produce errors into log lines, so
  # the generator validates the supported parameter combinations up front.
  ICPS2023_5NativeGeometry.validate_supported_parameters!(
    name, parameters["body_tie"] || false)
  variant = layout.add_pcell_variant(library, pcell_id, parameters)
  cell = layout.cell(variant)
  trans = RBA::Trans.new(RBA::Trans::R0, dbu(x), dbu(y))
  top.insert(RBA::CellInstArray.new(cell.cell_index, trans))
end

def design_name(name)
  suffix = $design_suffix.to_s
  suffix.empty? ? name : "#{name}_#{suffix}"
end

def new_design(name)
  layout = RBA::Layout.new
  layout.dbu = DBU
  actual_name = design_name(name)
  top = layout.create_cell(actual_name)
  yield layout, top
  path = File.join(File.expand_path($output_dir.to_s), "#{actual_name}.gds")
  layout.write(path)
  puts "wrote #{path}"
end


def make_single_mos(name, pcell)
  sd_y = mos_sd_y(0.0)
  gate_y = mos_gate_y(0.0)
  new_design(name) do |layout, top|
    instantiate_mos(layout, top, $native_library, pcell, 0.0, 0.0)
    add_label(layout, top, "S", mos_terminal_x(0.0, 0), sd_y)
    add_label(layout, top, "D", mos_terminal_x(0.0, 1), sd_y)
    add_label(layout, top, "G", mos_gate_x(0.0), gate_y)
  end
end

def make_inverter
  # Opposing 5 um gate cuts need 4 um separation: centres differ by 9 um.
  p_y = MOS_WIDTH + 32.0
  n_sd_y = mos_sd_y(0.0)
  p_sd_y = mos_sd_y(p_y)
  n_gate_y = mos_gate_y(0.0)
  p_gate_y = mos_gate_y(p_y)
  new_design("inverter_min") do |layout, top|
    instantiate_mos(layout, top, $native_library, "Nch", 0.0, 0.0)
    instantiate_mos(layout, top, $native_library, "Pch", 0.0, p_y)
    gate_x = mos_gate_x(0.0)
    drain_x = mos_terminal_x(0.0, 1)
    source_x = mos_terminal_x(0.0, 0)
    add_wire(layout, top, gate_x, n_gate_y, gate_x, p_gate_y)
    add_wire(layout, top, drain_x, n_sd_y, drain_x, p_sd_y)
    add_label(layout, top, "A", gate_x, p_y - 7.5)
    add_label(layout, top, "Y", drain_x, p_y / 2.0 + 4.0)
    add_label(layout, top, "VSS", source_x, n_sd_y)
    add_label(layout, top, "VDD", source_x, p_sd_y)
  end
end

def make_nand2
  # Keep a >=4 um opening between the same-net edge straps, not a 2 um notch.
  p_y = [70.0, MOS_WIDTH + 48.0].max
  n_sd_y = mos_sd_y(0.0)
  p_sd_y = mos_sd_y(p_y)
  n_gate_y = mos_gate_y(0.0)
  p_gate_y = mos_gate_y(p_y)
  second_x = 80.0
  new_design("nand2_min") do |layout, top|
    [0.0, second_x].each do |x|
      instantiate_mos(layout, top, $native_library, "Nch", x, 0.0)
      instantiate_mos(layout, top, $native_library, "Pch", x, p_y)
    end

    # N devices are series-connected; route P drains above the P-gate pads
    # without crossing the P-source terminals.
    output_route_y = p_y + MOS_WIDTH + 32.0
    n_drain_0 = mos_terminal_x(0.0, 1)
    n_source_1 = mos_terminal_x(second_x, 0)
    p_drain_0 = mos_terminal_x(0.0, 1)
    p_drain_1 = mos_terminal_x(second_x, 1)
    add_wire(layout, top, n_drain_0, n_sd_y, n_source_1, n_sd_y)
    add_wire(layout, top, p_drain_0, p_sd_y, p_drain_0, output_route_y)
    add_wire(layout, top, p_drain_0, output_route_y, p_drain_1, output_route_y)
    add_wire(layout, top, p_drain_1, n_sd_y, p_drain_1, output_route_y)
    [0.0, second_x].each do |x|
      gate_x = mos_gate_x(x)
      add_wire(layout, top, gate_x, n_gate_y, gate_x, p_gate_y)
      add_label(layout, top, x.zero? ? "A" : "B", gate_x, p_y - 7.5)
    end

    add_label(layout, top, "Y", p_drain_1, output_route_y)
    add_label(layout, top, "VSS", mos_terminal_x(0.0, 0), n_sd_y)
    add_label(layout, top, "VDD_A", mos_terminal_x(0.0, 0), p_sd_y)
    add_label(layout, top, "VDD_B", mos_terminal_x(second_x, 0), p_sd_y)
  end
end

def make_multifinger_edge
  new_design("edge_protected_multifinger") do |layout, top|
    instantiate_mos(layout, top, $native_library, "Nch", 0.0, 0.0, 2)
  end
end

def make_edge_without_legacy_switches
  new_design("edge_protected_no_legacy_switches") do |layout, top|
    instantiate_mos(
      layout, top, $native_library, "Nch", 0.0, 0.0, 1,
      { "with_pcont" => false, "soi_bridge" => false }
    )
  end
end

def make_edge_unprotected_diagnostic
  new_design("edge_unprotected_diagnostic") do |layout, top|
    instantiate_mos(
      layout, top, $native_library, "Nch", 0.0, 0.0, 1,
      { "edge_metal_strap" => false }
    )
  end
end

def make_diff_cap(name, pcell)
  new_design(name) do |layout, top|
    parameters = { "w" => 100.0, "l" => 100.0 }
    variant = layout.add_pcell_variant(
      $native_library, $native_library.layout.pcell_id(pcell), parameters
    )
    cell = layout.cell(variant)
    top.insert(
      RBA::CellInstArray.new(
        cell.cell_index,
        RBA::Trans.new(RBA::Trans::R0, dbu(0.0), dbu(0.0))
      )
    )
  end
end


def make_ring(stages)
  name = "ring_oscillator_#{stages}"
  pitch = 100.0
  p_y = MOS_WIDTH + 32.0
  n_sd_y = mos_sd_y(0.0)
  p_sd_y = mos_sd_y(p_y)
  n_gate_y = mos_gate_y(0.0)
  p_gate_y = mos_gate_y(p_y)
  input_route_y = p_y + MOS_WIDTH + 32.0
  closure_y = input_route_y + 20.0
  last_x = (stages - 1) * pitch

  new_design(name) do |layout, top|
    stages.times do |stage|
      x = stage * pitch
      instantiate_mos(layout, top, $native_library, "Nch", x, 0.0)
      instantiate_mos(layout, top, $native_library, "Pch", x, p_y)
      gate_x = mos_gate_x(x)
      drain_x = mos_terminal_x(x, 1)
      source_x = mos_terminal_x(x, 0)
      add_wire(layout, top, gate_x, n_gate_y, gate_x, p_gate_y)
      add_wire(layout, top, drain_x, n_sd_y, drain_x, p_sd_y)
      add_wire(layout, top, gate_x, p_gate_y, gate_x, input_route_y)
      add_wire(layout, top, drain_x, p_sd_y, drain_x, input_route_y)
      add_label(layout, top, "OUT", drain_x, p_y / 2.0 + 4.0) if stage == 0
      # Stage-local supply pins are bound to common rails by the parent circuit.
      add_label(layout, top, "VDD#{stage}", source_x, p_sd_y)
      add_label(layout, top, "VSS#{stage}", source_x, n_sd_y)
    end

    (stages - 1).times do |stage|
      output_x = stage * pitch + mos_terminal_x(0.0, 1)
      input_x = (stage + 1) * pitch + mos_gate_x(0.0)
      add_wire(layout, top, output_x, input_route_y, input_x, input_route_y)
    end
    last_drain = last_x + mos_terminal_x(0.0, 1)
    first_gate = mos_gate_x(0.0)
    add_wire(layout, top, last_drain, input_route_y, last_drain, closure_y)
    add_wire(layout, top, first_gate, input_route_y, first_gate, closure_y)
    add_wire(layout, top, last_drain, closure_y, first_gate, closure_y)
  end
end

def make_bridge(name, pcell)
  new_design(name) do |layout, top|
    parameters = { "l" => 25.0, "w" => 10.0, "s" => "20",
                   "mb" => false }
    variant = layout.add_pcell_variant(
      $native_library, $native_library.layout.pcell_id(pcell), parameters
    )
    cell = layout.cell(variant)
    trans = RBA::Trans.new(RBA::Trans::R0, dbu(0.0), dbu(0.0))
    top.insert(RBA::CellInstArray.new(cell.cell_index, trans))
    add_label(layout, top, "START", 6.5, 6.5)
    add_label(layout, top, "END", 26.5, 31.5)
  end
end

$native_library = library
make_single_mos("nmos_min", "Nch")
make_single_mos("pmos_min", "Pch")
make_inverter
make_nand2
make_multifinger_edge
make_bridge("bridge_tin_min", "Bridge")
make_edge_without_legacy_switches
make_edge_unprotected_diagnostic
make_diff_cap("edge_protected_ncap", "Ndiff_cap")
make_diff_cap("edge_protected_pcap", "Pdiff_cap")
make_bridge("bridge_nplus_min", "Nbridge")
make_ring(3)
make_ring(5)
