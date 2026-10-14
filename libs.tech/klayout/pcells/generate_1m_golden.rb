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
MOS_WIDTH = ($mos_width.nil? || $mos_width.to_s.empty?) ? 13.0 : Float($mos_width)

ML1 = 8
TEXT = 13

def dbu(value)
  (value.to_f / DBU).round
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

def instantiate_mos(layout, top, library, name, x, y)
  parameters = { "w" => MOS_WIDTH, "l" => 6.0, "with_via" => false }
  parameters["use_nwell"] = true if name == "Pch"
  pcell_id = library.layout.pcell_id(name)
  raise "missing native PCell #{name}" if pcell_id < 0
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
  sd_y = MOS_WIDTH / 2.0
  gate_y = MOS_WIDTH + 11.5
  new_design(name) do |layout, top|
    instantiate_mos(layout, top, $native_library, pcell, 0.0, 0.0)
    add_label(layout, top, "S", 6.5, sd_y)
    add_label(layout, top, "D", 41.5, sd_y)
    add_label(layout, top, "G", 24.0, gate_y)
  end
end

def make_inverter
  p_y = 45.0
  n_sd_y = MOS_WIDTH / 2.0
  p_sd_y = p_y + n_sd_y
  n_gate_y = MOS_WIDTH + 11.5
  p_gate_y = p_y + n_gate_y
  new_design("inverter_min") do |layout, top|
    instantiate_mos(layout, top, $native_library, "Nch", 0.0, 0.0)
    instantiate_mos(layout, top, $native_library, "Pch", 0.0, p_y)
    add_wire(layout, top, 24.0, n_gate_y, 24.0, p_gate_y)
    add_wire(layout, top, 41.5, n_sd_y, 41.5, p_sd_y)
    add_label(layout, top, "A", 24.0, p_y - 7.5)
    add_label(layout, top, "Y", 41.5, p_y / 2.0 + 4.0)
    add_label(layout, top, "VSS", 6.5, n_sd_y)
    add_label(layout, top, "VDD", 6.5, p_sd_y)
  end
end

def make_nand2
  p_y = 70.0
  n_sd_y = MOS_WIDTH / 2.0
  p_sd_y = p_y + n_sd_y
  n_gate_y = MOS_WIDTH + 11.5
  p_gate_y = p_y + n_gate_y
  new_design("nand2_min") do |layout, top|
    [0.0, 80.0].each do |x|
      instantiate_mos(layout, top, $native_library, "Nch", x, 0.0)
      instantiate_mos(layout, top, $native_library, "Pch", x, p_y)
    end

    # N devices are series-connected; route P drains above the P-gate pads
    # without crossing the P-source terminals.
    output_route_y = p_y + MOS_WIDTH + 32.0
    add_wire(layout, top, 41.5, n_sd_y, 86.5, n_sd_y)
    add_wire(layout, top, 41.5, p_sd_y, 41.5, output_route_y)
    add_wire(layout, top, 41.5, output_route_y, 121.5, output_route_y)
    add_wire(layout, top, 121.5, n_sd_y, 121.5, output_route_y)
    add_wire(layout, top, 24.0, n_gate_y, 24.0, p_gate_y)
    add_wire(layout, top, 104.0, n_gate_y, 104.0, p_gate_y)

    add_label(layout, top, "A", 24.0, p_y - 7.5)
    add_label(layout, top, "B", 104.0, p_y - 7.5)
    add_label(layout, top, "Y", 121.5, output_route_y)
    add_label(layout, top, "VSS", 6.5, n_sd_y)
    add_label(layout, top, "VDD_A", 6.5, p_sd_y)
    add_label(layout, top, "VDD_B", 86.5, p_sd_y)
  end
end

def make_ring(stages)
  name = "ring_oscillator_#{stages}"
  pitch = 100.0
  p_y = 45.0
  n_sd_y = MOS_WIDTH / 2.0
  p_sd_y = p_y + n_sd_y
  n_gate_y = MOS_WIDTH + 11.5
  p_gate_y = p_y + n_gate_y
  input_route_y = p_y + MOS_WIDTH + 32.0
  closure_y = input_route_y + 20.0
  last_x = (stages - 1) * pitch

  new_design(name) do |layout, top|
    stages.times do |stage|
      x = stage * pitch
      instantiate_mos(layout, top, $native_library, "Nch", x, 0.0)
      instantiate_mos(layout, top, $native_library, "Pch", x, p_y)
      add_wire(layout, top, x + 24.0, n_gate_y, x + 24.0, p_gate_y)
      add_wire(layout, top, x + 41.5, n_sd_y, x + 41.5, p_sd_y)
      add_wire(layout, top, x + 24.0, p_gate_y, x + 24.0, input_route_y)
      add_wire(layout, top, x + 41.5, p_sd_y, x + 41.5, input_route_y)
      add_label(layout, top, "OUT", x + 41.5, p_y / 2.0 + 4.0) if stage == 0
      # Stage-local supply pins are bound to common rails by the parent circuit.
      add_label(layout, top, "VDD#{stage}", x + 6.5, p_sd_y)
      add_label(layout, top, "VSS#{stage}", x + 6.5, n_sd_y)
    end

    (stages - 1).times do |stage|
      output_x = stage * pitch + 41.5
      input_x = (stage + 1) * pitch + 24.0
      add_wire(layout, top, output_x, input_route_y, input_x, input_route_y)
    end
    add_wire(layout, top, last_x + 41.5, input_route_y,
             last_x + 41.5, closure_y)
    add_wire(layout, top, 24.0, input_route_y, 24.0, closure_y)
    add_wire(layout, top, last_x + 41.5, closure_y, 24.0, closure_y)
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
make_bridge("bridge_tin_min", "Bridge")
make_bridge("bridge_nplus_min", "Nbridge")
make_ring(3)
make_ring(5)
