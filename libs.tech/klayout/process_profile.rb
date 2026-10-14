# Shared process-profile policy for the ICPS2023_5 KLayout tools.
require "json"

module ICPS2023_5ProcessProfile
  DATA = JSON.parse(File.read(File.expand_path("process_profiles.json", __dir__)))

  def self.resolve(name = nil)
    name = ENV.fetch("ICPS2023_5_PROCESS_PROFILE", DATA.fetch("default_profile")) if name.nil? || name.to_s.empty?
    return name if DATA.fetch("profiles").key?(name)

    raise "Unknown ICPS2023_5 process profile '#{name}' (choose #{DATA.fetch('profiles').keys.join(', ')})"
  end

  def self.profile(name)
    DATA.fetch("profiles").fetch(name) do
      raise "Unknown ICPS2023_5 process profile '#{name}' (choose #{DATA.fetch('profiles').keys.join(', ')})"
    end
  end

  def self.region(layout, cell, spec)
    layer_index = layout.find_layer(RBA::LayerInfo.new(*spec))
    return RBA::Region::new if layer_index.nil?

    RBA::Region::new(cell.begin_shapes_rec(layer_index))
  end

  def self.present_layers(layout, cell, layer_mode, names = nil)
    layer_map = DATA.fetch("upper_metal_layers").fetch(layer_mode)
    names ||= layer_map.keys
    mask = region(layout, cell, [20, 0])
    exclude = region(layout, cell, [63, 63])
    names.select do |name|
      geometry = region(layout, cell, layer_map.fetch(name))
      geometry -= mask
      geometry -= exclude
      !geometry.is_empty?
    end
  end

  def self.reject_layers!(layout, cell, layer_mode, names, tool, reason)
    present = present_layers(layout, cell, layer_mode, names)
    return if present.empty?

    raise "#{tool}: #{reason}: #{present.join(', ')}"
  end

  def self.supports_via_pcells?(profile_name)
    permitted = profile(profile_name).fetch("permitted_upper_layers")
    ["VIA1", "ML2"].all? { |name| permitted.include?(name) }
  end

  def self.reject_unpermitted_layers!(layout, cell, layer_mode, profile_name, tool)
    permitted = profile(profile_name).fetch("permitted_upper_layers")
    unpermitted = DATA.fetch("upper_metal_layers").fetch(layer_mode).keys - permitted
    reject_layers!(
      layout, cell, layer_mode, unpermitted,
      tool, "process profile #{profile_name} does not permit upper-metal geometry"
    )
  end
end
