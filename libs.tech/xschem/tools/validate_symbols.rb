#!/usr/bin/env ruby
# Structural validator for the xschem library.  xschem is intentionally not
# required: this checks the file grammar and the PDK netlist contract.
require 'set'

ROOT = File.expand_path('..', __dir__)
SYMBOL_DIR = File.join(ROOT, 'symbols')
NGSPICE_DIR = File.expand_path('../ngspice', ROOT)

AGREED_MODELS = Set.new(%w[nch pch])
AGREED_SUBCKTS = Set.new(%w[
  r_ndiff r_pdiff r_soi r_tin r_ml1 cap_ndiff cap_pdiff cap_tin mim_cap
  dio_ndio dio_pdio bridge nbridge nmos_esd pmos_esd cap_min res_min
  r_poly hr_poly
])
DEVICE_TYPES = Set.new(%w[nmos4 pmos4 subcircuit])

def block(text, tag)
  # Metadata may contain a multiline format string; xschem closes such a
  # block with either `}` or `"}` on its own line.
  m = text.match(/^#{tag} \{(.*?)(?:^"?\}\s*$|\}\n(?:V|S|E|L|B|T))/m)
  m && m[1]
end

def attrs(text)
  out = {}
  text.to_s.scan(/(?:^|\n)([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"])*)"/) do |k, v|
    out[k] = v
  end
  text.to_s.scan(/(?:^|\n)([A-Za-z_][A-Za-z0-9_]*)=([^\s\n}]+)/) do |k, v|
    out[k] ||= v
  end
  out
end

def pins(text)
  text.scan(/^B\s+5\s+[^\n]*\{name=([^\s}]+)/).flatten.to_set
end

def ngspice_names
  models = Set.new
  subckts = Set.new
  Dir.glob(File.join(NGSPICE_DIR, '**', '*')).select { |p| File.file?(p) }.each do |path|
    File.foreach(path) do |line|
      models << $1.downcase if line =~ /^\s*\.model\s+([^\s(]+)/i
      subckts << $1.downcase if line =~ /^\s*\.subckt\s+([^\s]+)/i
    end
  end
  [models, subckts]
end

errors = []
files = Dir.glob(File.join(SYMBOL_DIR, '*.sym')).sort
abort "no symbols found in #{SYMBOL_DIR}" if files.empty?
models, subckts = ngspice_names
using_agreed = models.empty? || subckts.empty?
models = AGREED_MODELS if models.empty?
subckts = AGREED_SUBCKTS if subckts.empty?

files.each do |path|
  name = File.basename(path, '.sym')
  text = File.read(path)
  errors << "#{name}: malformed xschem header" unless text.match?(/^v \{xschem version=[^}]+\}/)
  g = block(text, 'G')
  metadata = attrs(g.to_s)
  type = metadata['type']
  errors << "#{name}: missing G type" if type.nil? || type.empty?
  format = metadata['format']
  template = metadata['template']
  errors << "#{name}: missing format" unless format
  errors << "#{name}: missing template" unless template
  next unless DEVICE_TYPES.include?(type)

  pad_names = pins(text)
  if format&.include?('@pinlist')
    errors << "#{name}: @pinlist has no B 5 pads" if pad_names.empty?
  else
    format.scan(/@([A-Za-z_][A-Za-z0-9_]*)/).flatten.each do |pin|
      next if %w[name spiceprefix pinlist model symname value w l as ps ad pd m area].include?(pin)
      errors << "#{name}: format pin #{pin} lacks B 5 pad" unless pad_names.include?(pin)
    end
  end
  model = template[/\bmodel=([^\s"]+)/, 1]
  subckt = template[/\bsymname=([^\s"]+)/, 1]
  if %w[nmos4 pmos4].include?(type)
    errors << "#{name}: model #{model.inspect} absent from ngspice models" unless model && models.include?(model.downcase)
  elsif type == 'subcircuit'
    errors << "#{name}: subcircuit #{subckt.inspect} absent from ngspice library" unless subckt && subckts.include?(subckt.downcase)
  end
end

if errors.empty?
  source = using_agreed ? 'agreed device names (ngspice library not yet populated)' : 'ngspice files'
  puts "validated #{files.length} symbols; device names checked against #{source}"
  exit 0
end
warn errors.join("\n")
warn "validated #{files.length} symbols with #{errors.length} error(s)"
exit 1
