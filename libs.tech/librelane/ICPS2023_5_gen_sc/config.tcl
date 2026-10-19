# LibreLane SCL view configuration for ICPS2023_5_gen_sc.
# Layout follows the per-SCL organization used by icsprout55-pdk; no process
# values, timing data, placement site, or routing data are copied from it.
#
 # This registers only views that exist and are accepted by this PDK. The
 # library has no qualified placement site or row contract. The Liberty below
 # is an explicit, single-corner Xyce evidence artifact; it is not a process
 # signoff timing view and excludes sequential setup/hold arcs.

set scl_dir [file normalize [file join \
    $::env(PDK_ROOT) $::env(PDK) libs.ref $::env(STD_CELL_LIBRARY)]]

set ::env(VDD_PIN) "VDD"
set ::env(GND_PIN) "VSS"
set ::env(SCL_POWER_PINS) [list "VDD"]
set ::env(SCL_GROUND_PINS) [list "VSS"]

# Cell LEFs for the router: the alias views name the physical ML1 layer `M1`,
# which is the layer the experimental technology LEF declares.  The canonical
# `lef/<cell>.lef` views keep the physical process layer name and are not
# loaded here (both sets would define the same MACRO twice).
set ::env(CELL_LEFS) [lsort [glob -nocomplain [file join $scl_dir lef *_librelane.lef]]]
set ::env(CELL_GDS) [lsort [glob -nocomplain [file join $scl_dir gds *.gds]]]
set ::env(CELL_SPICE_MODELS) [list [file join $scl_dir spice ICPS2023_5_gen_sc.spice]]

 # Explicit single-corner Xyce evidence view. This is not a signoff timing
 # library and excludes sequential setup/hold arcs.
 set ::env(LIB) [dict create "*" [list [file normalize [file join \
     $::env(PDK_ROOT) $::env(PDK) libs.ref ICPS2023_5_gen_sc liberty \
     ICPS2023_5_gen_sc_experimental_xyce.lib]]]]
 set ::env(DEFAULT_CORNER) "nom_xyce_3v_25c"
 set ::env(STA_CORNERS) [list "nom_xyce_3v_25c"]
 set ::env(EXPERIMENTAL_TIMING_SCOPE) "Xyce 7.10 / BSIMSOI 4.5.0, nominal 3 V / 25 C, 5 fF load, 2 ns input slew; no signoff claim"
 set ::env(EXPERIMENTAL_TIMING_LIBRARY) "ICPS2023_5_experimental_xyce_single_corner"
# This library publishes no filler or decap cells. No qualified cell is
# available to serve as a tie, buffer, or synthesis driving cell.
set ::env(FILL_CELLS) [list]
set ::env(DECAP_CELLS) [list]
set ::env(CELL_PAD_EXCLUDE) [list]
set ::env(RUN_ANTENNA_REPAIR) false

# No cell exclusion list is defined; /dev/null is an empty list on supported
# Unix hosts, not a cell model or a process assumption.
set ::env(SYNTH_EXCLUDED_CELL_FILE) "/dev/null"
set ::env(PNR_EXCLUDED_CELL_FILE) "/dev/null"

 # No qualified placement site, synthesis utility cells, PDN, RCX, or CTS
 # contract exists. The Liberty above is evidence-scoped and experimental.
