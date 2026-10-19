# ICPS2023_5 LibreLane technology configuration.
# It provides unqualified experimental 1M defaults so LibreLane can validate
# the PDK contract; explicit profile fragments override the 2M geometry.
# These values are not process/signoff collateral. Design inputs remain in
# design configs, and this file does not start a flow by itself.

set ::env(DEF_UNITS_PER_MICRON) 1000
set ::env(PRIMARY_GDSII_STREAMOUT_TOOL) "klayout"

# The root PDK configuration is deliberately fixed to the conservative 1M
# overlay. Select the post-2025 2M experiment through an explicit design config
# under configs/; do not switch canonical KLayout DRC/LVS/PEX behavior here.
if { ![info exist ::env(PAD_CELL_LIBRARY)] } {
    set ::env(PAD_CELL_LIBRARY) "ICPS2023_5_fd_base"
}

set tech_dir "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/librelane/experimental_1m"
set ::env(TECH_LEFS) [dict create]
dict set ::env(TECH_LEFS) "*" "$tech_dir/icps2023_5_1m.tech.lef"
set ::env(FP_TRACKS_INFO) "$tech_dir/tracks.info"
set ::env(RT_MIN_LAYER) "POLY"
set ::env(RT_MAX_LAYER) "M1"
set ::env(RT_CLOCK_MIN_LAYER) "M1"
set ::env(RT_CLOCK_MAX_LAYER) "M1"

# Use the physical PDK display technology, with the isolated experimental
# OpenROAD layer map for the ML1->M1 router alias and synthetic POLY/via IDs.
set ::env(KLAYOUT_TECH) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/klayout/tech.lyt"
set ::env(KLAYOUT_PROPERTIES) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/klayout/icps2023_5.lyp"
set ::env(KLAYOUT_DEF_LAYER_MAP) "$tech_dir/icps2023_5_1m.map"

# LibreLane splits this value across both sides: 8 sites => 4 sites per side.
set ::env(FP_CORE_UTIL) 10
set ::env(GPL_CELL_PADDING) 8
set ::env(DPL_CELL_PADDING) 8

# Experimental site/timing/PDN/RCX defaults are supplied below for loader
# compatibility. They are not qualified. Antenna repair is disabled because
# no diode/model is characterized.
# The default physical backend is KLayout. Magic is intentionally not part of
# this PDK flow because no Magic technology/setup is published.
set ::env(RUN_MAGIC_STREAMOUT) false
set ::env(RUN_MAGIC_WRITE_LEF) false
set ::env(RUN_MAGIC_DRC) false
set ::env(RUN_KLAYOUT_STREAMOUT) true
set ::env(RUN_KLAYOUT_XOR) false
set ::env(RUN_KLAYOUT_DRC) true
set ::env(KLAYOUT_DRC_RUNSET) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/klayout/drc/icps2023_5.drc"
set ::env(KLAYOUT_DRC_DEFINES) [dict create layer_mode design strict true edge_redundancy true soi_edge_margin 2.0 process_profile icps2023_5_1m]

# OpenRCX rulesets below carry only available resistance terms, not a full
# interconnect RC model. The supported PEX path remains KLayout/SPICE.
set ::env(RUN_SPEF_EXTRACTION) false
set ::env(RUN_MCSTA) false
set ::env(RUN_IRDROP_REPORT) false

# Required Classic-flow inputs for the local experimental 1M profile. These
# values are explicit and unqualified; the profile fragments override the
# stack-specific layer, PDN, IO-pin, GRT, and RCX choices for the 2M experiment.
set ::env(PLACE_SITE) "ICPS2023_5_ROW500"
set ::env(SYNTH_DRIVING_CELL) "BUF_X1/Y"
set ::env(SYNTH_BUFFER_CELL) "BUF_X1/A/Y"
set ::env(SYNTH_TIEHI_CELL) "TIEHI_X1/Y"
set ::env(SYNTH_TIELO_CELL) "TIELO_X1/Y"
set ::env(CTS_ROOT_BUFFER) "BUF_X1"
set ::env(CTS_CLK_BUFFERS) [list "BUF_X1"]
set ::env(IO_PIN_H_LAYER) "M1"
set ::env(IO_PIN_V_LAYER) "TIN"

# The local Liberty evidence is limited to nominal 3 V / 25 C, 5 fF load and
# 2 ns input slew. Missing system timing budgets are explicit zero policies,
# not signoff uncertainty/derating claims. A one-sink fanout is an experiment
# guard because the library publishes only X1 drive strengths.
set ::env(OUTPUT_CAP_LOAD) 5.0
set ::env(MAX_FANOUT_CONSTRAINT) 1
set ::env(CLOCK_UNCERTAINTY_CONSTRAINT) 0.0
set ::env(CLOCK_TRANSITION_CONSTRAINT) 2.0
set ::env(TIME_DERATING_CONSTRAINT) 0.0
set ::env(IO_DELAY_CONSTRAINT) 0.0
set ::env(TIMING_VIOLATION_CORNERS) [list "nom_xyce_3v_25c"]

# No capacity reduction is characterized. One zero adjustment corresponds to
# each 1M routing level in the technology LEF (POLY, TIN, M1).
set ::env(GRT_LAYER_ADJUSTMENTS) [list 0.0 0.0 0.0]
set ::env(RCX_RULESETS) [list \
    "nom_xyce_3v_25c" "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/librelane/rcx/icps2023_5_1m.rules"]

# 1M geometry-only PDN. Rails follow the cell M1 power pins (-followpins), so
# every row gets a VSS rail at its bottom pin band and a VDD rail at its top
# band. ML1 is the only low-resistance conductor here, so the strap layer is
# declared as M1 too and its pitch is larger than the die: no strap stripe can
# land inside the core, because a strap on M1 would cross a rail of the
# opposite polarity and short. The 2M profile overrides the strap layer to M2.
# PDN_RAIL_OFFSET is required but unused with -followpins. Keep the core ring
# off until VDD/VSS pads and a pad-to-ring connection are assigned.
set ::env(PDN_MULTILAYER) false
set ::env(PDN_CORE_RING) false
set ::env(PDN_RAIL_OFFSET) 0.0
set ::env(PDN_RAIL_LAYER) "M1"
set ::env(PDN_RAIL_WIDTH) 12.0
set ::env(PDN_VERTICAL_LAYER) "M1"
set ::env(PDN_HORIZONTAL_LAYER) "M1"
set ::env(PDN_VWIDTH) 6.0
set ::env(PDN_HWIDTH) 6.0
set ::env(PDN_VSPACING) 6.0
set ::env(PDN_HSPACING) 4.0
set ::env(PDN_VPITCH) 4000.0
set ::env(PDN_HPITCH) 4000.0
set ::env(PDN_VOFFSET) 0.0
set ::env(PDN_HOFFSET) 0.0
set ::env(PDN_CORE_RING_VWIDTH) 6.0
set ::env(PDN_CORE_RING_HWIDTH) 6.0
set ::env(PDN_CORE_RING_VSPACING) 6.0
set ::env(PDN_CORE_RING_HSPACING) 4.0
set ::env(PDN_CORE_RING_VOFFSET) 0.0
set ::env(PDN_CORE_RING_HOFFSET) 0.0
