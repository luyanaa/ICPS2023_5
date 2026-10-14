# Physical-only ICPS2023_5 frame/pad views for LibreLane.
# The pad macro has no characterized timing, SPICE, or Verilog model.
set pad_dir "$::env(PDK_ROOT)/$::env(PDK)/libs.ref/ICPS2023_5_fd_base"
set ::env(PAD_LEFS) [list "$pad_dir/lef/ICPS2023_5_fd_base_librelane.lef"]
set ::env(PAD_GDS) [list "$pad_dir/gds/ICPS2023_5_fd_base.gds"]
