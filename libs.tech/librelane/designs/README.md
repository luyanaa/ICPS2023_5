# Explicit LibreLane design fragments

These JSON files are **design-side** fragments, not PDK contracts. Each one is
loaded *after* a PDK profile from `../configs/`, so the profile keeps owning the
technology, pad library, site and PDN/RC/CTS contracts and the design fragment
only supplies what is specific to one netlist.

```bash
# 1x1 single-metal profile with the small combinational smoke design
nix develop ../librelane --command librelane --manual-pdk \
  --pdk-root /tmp/icps2023_5_pdkroot --pdk ICPS2023_5 --scl ICPS2023_5_gen_sc \
  libs.tech/librelane/configs/icps2023_5_1m_1x1.json \
  libs.tech/librelane/designs/inv_nand2_aoi21.json

# 2x2 experimental two-metal profile with the ALU4 design
nix develop ../librelane --command librelane --manual-pdk \
  --pdk-root /tmp/icps2023_5_pdkroot --pdk ICPS2023_5 --scl ICPS2023_5_gen_sc \
  libs.tech/librelane/configs/icps2023_5_2m_post2025_2x2.json \
  libs.tech/librelane/designs/alu4.json
```

`inv_nand2_aoi21` is the single-row smoke design: three gates on the 1x1
profile, whose core is only two 500 µm rows deep. A one-cell design
(`tiny_and`) degenerates RePlAce's Nesterov solver, so the smoke design keeps a
few instances instead.

Both designs are **combinational**: the experimental Liberty has no sequential
setup/hold arcs, so `CLOCK_PORT` is left unset and `combinational.sdc` supplies a
virtual clock plus input/output delays instead. Neither fragment uses the frame
DEF template: the frame's 28/56 fixed pads carry no net assignment yet, and a
DEF template whose pins do not exist in the design is refused by
`OpenROAD.ApplyDEFTemplate`. The die areas stay inside the measured frame
envelope (1340 × 1340 µm for 1x1, 2940 × 2940 µm for 2x2) with the core rounded
to whole 500 µm rows, so the streamed layout can be translated onto the frame
overlay once a pad-to-net assignment exists.

The RTL lives with the standard-cell tooling (`libs.tech/stdcell/alu4.v`,
`libs.tech/stdcell/benchmarks/inv_nand2_aoi21.v`) so the design and the library
evidence stay in one place.
