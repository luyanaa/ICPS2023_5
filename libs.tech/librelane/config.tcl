# ICPS2023_5 LibreLane technology configuration.
# This is an experimental routing overlay, not a qualified process setup.
# Deliberately do not choose a default STD_CELL_LIBRARY: no ICPS standard-cell
# library/configuration exists yet. LibreLane still requires the future SCL
# through --scl and libs.tech/librelane/<SCL>/config.tcl before a flow can load.

set ::env(DEF_UNITS_PER_MICRON) 1000
set ::env(PRIMARY_GDSII_STREAMOUT_TOOL) "klayout"

if { ![info exist ::env(PAD_CELL_LIBRARY)] } {
    set ::env(PAD_CELL_LIBRARY) "ICPS2023_5_fd_base"
}

set tech_dir "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/librelane/experimental_1m"
set ::env(TECH_LEFS) [dict create]
dict set ::env(TECH_LEFS) "*" "$tech_dir/icps2023_5_1m.tech.lef"
set ::env(FP_TRACKS_INFO) "$tech_dir/tracks.info"
set ::env(RT_MIN_LAYER) "POLY"
set ::env(RT_MAX_LAYER) "M1"

# Use the physical PDK display technology, with the isolated experimental
# OpenROAD layer map for the ML1->M1 router alias and synthetic POLY/via IDs.
set ::env(KLAYOUT_TECH) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/klayout/tech.lyt"
set ::env(KLAYOUT_PROPERTIES) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/klayout/icps2023_5.lyp"
set ::env(KLAYOUT_DEF_LAYER_MAP) "$tech_dir/icps2023_5_1m.map"

# LibreLane splits this value across both sides: 8 sites => 4 sites per side.
set ::env(GPL_CELL_PADDING) 8
set ::env(DPL_CELL_PADDING) 8

# No STD_CELL_LIBRARY, power pins, site, antenna model, RCX rules, or timing
# corners are fabricated here. The SCL config must supply its own cell views.
