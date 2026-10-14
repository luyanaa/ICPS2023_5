# ICPS2023_5_fd_base — frame and pad ring

Physical-only library: the chip frame and the 28 wire-bond pads of the minimal
Fab SOI-CMOS process. There are no transistors, no logic and no characterised
I/O cell — the pads are pass-through metal (ML1 over TiN over the SOI stack),
which is why the Liberty view carries geometry and capacitance only.

## Contents

| View | File | Notes |
|------|------|-------|
| GDSII | `gds/ICPS2023_5_fd_base.gds` | top cell `ICPS2023_5_fd_base`, 1600 × 1600 µm |
| LEF | `lef/ICPS2023_5_fd_base.lef` | macro outlines, pad pins (ML1), obstructions |
| Liberty | `lib/ICPS2023_5_fd_base.lib` | area and pin capacitance, no timing arcs |

Cells in the GDS:

* `ICPS2023_5_fd_base` — top cell; frame ring (`16_5x5_flame`, FRAME 14/0) plus
  4 pad arrays.
* `pad_60_m1_tin` — 60 × 60 µm pad: one ML1 plate, two TiN shapes and a 4 × 4
  array of `tin_M1` contact/via clusters. **28 instances** (8 + 8 + 6 + 6) sit
  on the top cell.
* `tin_M1` — one ML1/TiN/CNT cluster used inside the pads.

The library descends from the minimal Fab design contest 2023 template
(`Samples/Semicon2023/base_contest2023.GDS`, top cell `base_Rev0.93`). The
template's pre-placed device arrays (`base_array` with `pmos_array`,
`nmos_array`, `resistor_array`, `tin_m1_cap_array`) were removed so that the
frame can be used as the starting point of a new design; **the 28 pads and the
frame were kept unchanged**.

## Regenerating

```bash
cd ~/Documents/librelane && nix-shell --run 'cd <repo> && \
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_fd_base.rb && \
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb'
```

`make_fd_base.rb` asserts that exactly 28 pads are present before writing.
`make_lef_lib.rb` derives both views from the GDS: pins are the merged ML1
geometry of each cell, everything else becomes an obstruction, `area` is the
macro bounding box and the pin capacitance is the drawn pad-metal area times the
documented 0.115 fF/µm² (300 nm TEOS).

## Limits

* No timing, no drive strength, no ESD rating: the foundry does not characterise
  an I/O cell for this process. The only documented limit is the absolute
  maximum rating of ±7 V (VDS/VGD/VGS).
* The LEF references layer names from the PDK design layer table
  (`libs.tech/klayout/doc/layers.md`). A LEF tech file for the process will
  arrive with the LibreLane bring-up; until then the macro LEF cannot be read
  standalone by a router.
* The frame ring is a drawing, not a seal ring with documented widths.

## Use as a design frame

Start a design by instantiating `ICPS2023_5_fd_base` and placing the circuit in
the core area; the pads are the only connections to the outside world. Run
`libs.tech/klayout/drc/icps2023_5.drc` on the assembled layout (the frame itself
is DRC-clean).
