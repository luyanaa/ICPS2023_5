# ICPS2023_5_fd_base — frame and pad ring

Physical-only library: the chip frame and the 28 wire-bond pads of the minimal
Fab SOI-CMOS process. There are no transistors, logic, or characterized I/O
cells. The GDS is a physical overlay; use the fixed top-level I/O ports in DEF
instead of placing the full 1600 µm frame as a blocking macro.

## Contents

| View | File | Notes |
|------|------|-------|
| GDSII | `gds/ICPS2023_5_fd_base.gds` | unchanged 1600 × 1600 µm frame and 28 pad overlay |
| LEF | `lef/ICPS2023_5_fd_base.lef` | reusable 60 × 60 µm pad-cell macro only |
| DEF | `def/ICPS2023_5_fd_base.def` | 28 independent fixed `PAD_00`–`PAD_27` ML1 top-level pins |
| Liberty | `lib/ICPS2023_5_fd_base.lib` | physical pin identity only; no pad capacitance or timing constraint |

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
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb && \
  python3 libs.ref/ICPS2023_5_fd_base/tools/test_pad_views.py'
```

`make_fd_base.rb` asserts that exactly 28 pads are present before writing.
`make_lef_lib.rb` asserts 28 separate 60 × 60 µm ML1 shapes, keeps the frame
GDS unchanged, emits a pad-cell-only LEF and a DEF with one fixed top-level pin
per physical pad, and emits Liberty without unqualified pad capacitance or
timing constraints.
The generic `PAD_XX` names are assigned top-to-bottom, left-to-right within each
row. Map them to design nets in the floorplan; the DEF pins use the original
GDS coordinate origin.

## Limits

* No timing, no drive strength, no ESD rating: the foundry does not characterise
  an I/O cell for this process. The only documented limit is the absolute
  maximum rating of ±7 V (VDS/VGD/VGS).
* The LEF references layer names from `libs.tech/klayout/doc/layers.md`; no
  process tech LEF, site, or track definition is shipped, so the LEF is not
  independently routable.
* `AIST-SOI-CMOS.xs` v0.2 is a 2021 visualization prior (TBOX 400 nm, TiN
  50 nm, first TEOS 250 nm, ML1 300 nm), not a confirmed 2023 production stack.
  The PDK's TiN-cap density of 0.115 fF/µm² corresponds to about 300 nm oxide;
  its difference from the XS 250 nm TEOS value is unresolved.
* The frame ring is a drawing, not a seal ring with documented widths.

## Use as a design frame

Use `gds/ICPS2023_5_fd_base.gds` as a physical overlay at its original origin;
do not instantiate the full-frame BLOCK in the standard-cell placement database.
Use `def/ICPS2023_5_fd_base.def` for the 28 independent fixed I/O ports
(`PAD_00`–`PAD_27`), assign design nets to them, and floorplan the central core
separately. The DEF contains no standard-cell rows or PDN plan. Run
`libs.tech/klayout/drc/icps2023_5.drc` on the assembled layout.
