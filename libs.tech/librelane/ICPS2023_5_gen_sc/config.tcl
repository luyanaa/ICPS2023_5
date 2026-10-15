# LibreLane SCL view configuration for ICPS2023_5_gen_sc.
# Layout follows the per-SCL organization used by icsprout55-pdk; no process
# values, timing data, placement site, or routing data are copied from it.
#
# This registers only views that exist and are accepted by this PDK. It is not
# a usable place-and-route SCL: the library has no Liberty/Verilog/CDL view,
# no placement site, and its canonical LEFs use ML1 while the experimental
# LibreLane technology LEF uses the M1 alias. Do not infer timing or placement
# readiness from this file.

set scl_dir [file normalize [file join \
    $::env(PDK_ROOT) $::env(PDK) libs.ref $::env(STD_CELL_LIBRARY)]]

set ::env(VDD_PIN) "VDD"
set ::env(GND_PIN) "VSS"
set ::env(SCL_POWER_PINS) [list "VDD"]
set ::env(SCL_GROUND_PINS) [list "VSS"]

set ::env(CELL_LEFS) [lsort [glob -nocomplain [file join $scl_dir lef *.lef]]]
set ::env(CELL_GDS) [lsort [glob -nocomplain [file join $scl_dir gds *.gds]]]
set ::env(CELL_SPICE_MODELS) [list [file join $scl_dir spice ICPS2023_5_gen_sc.spice]]

# This library publishes no filler or decap cells. No qualified cell is
# available to serve as a tie, buffer, or synthesis driving cell.
set ::env(FILL_CELLS) [list]
set ::env(DECAP_CELLS) [list]
set ::env(CELL_PAD_EXCLUDE) [list]

# No cell exclusion list is defined; /dev/null is an empty list on supported
# Unix hosts, not a cell model or a process assumption.
set ::env(SYNTH_EXCLUDED_CELL_FILE) "/dev/null"
set ::env(PNR_EXCLUDED_CELL_FILE) "/dev/null"

# Intentionally unset: LIB, DEFAULT_CORNER, STA_CORNERS, PLACE_SITE,
# SYNTH_DRIVING_CELL, SYNTH_BUFFER_CELL, SYNTH_TIEHI_CELL, and SYNTH_TIELO_CELL.
# Their required qualification or cell views do not exist in this PDK.
