# Physical-only derived 2x2 ICPS2023_5 frame/pad views for LibreLane.
# The 3200 x 3200 um frame GDS is an origin-fixed overlay; the matching DEF
# template must be selected explicitly by the design configuration.
# The pad macro has no characterized timing, SPICE, or Verilog model.
set pad_dir "$::env(PDK_ROOT)/$::env(PDK)/libs.ref/ICPS2023_5_fd_base_2x2"
set ::env(PAD_LEFS) [list "$pad_dir/lef/ICPS2023_5_fd_base_2x2_librelane.lef"]
set ::env(PAD_GDS) [list "$pad_dir/gds/ICPS2023_5_fd_base_2x2.gds"]
