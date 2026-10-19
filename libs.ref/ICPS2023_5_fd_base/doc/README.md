# ICPS2023_5_fd_base — frame and pad ring

Physical-only library: the chip frame and the 28 wire-bond pads of the minimal
Fab SOI-CMOS process. There are no transistors, logic, or characterized I/O
cells. The GDS is a physical overlay; use the fixed top-level I/O ports in DEF
instead of placing the full 1600 µm frame as a blocking macro.

## Contents

| View | File | Notes |
|------|------|-------|
| GDSII | `gds/ICPS2023_5_fd_base.gds` | 1600 × 1600 µm frame and 28 pad overlay, **foundry mask numbering** |
| LEF | `lef/ICPS2023_5_fd_base.lef` | canonical reusable 60 × 60 µm pad-cell macro; layer is named ML1 |
| DEF | `def/ICPS2023_5_fd_base.def` | canonical 28 independent fixed `PAD_00`–`PAD_27` ML1 top-level pins |
| LibreLane LEF | `lef/ICPS2023_5_fd_base_librelane.lef` | router alias `M1` for physical M1(Al) mask 7/0; geometry unchanged |
| LibreLane DEF | `def/ICPS2023_5_fd_base_librelane.def` | same 28 fixed pins with router layer name M1 |
| Liberty | `lib/ICPS2023_5_fd_base.lib` | physical pin identity only; no pad capacitance or timing constraint |
| Pad crosswalk | `doc/pad_map.csv` | source GDS pin labels, array side, and ML1 centers for all DEF pins |

Cells in the GDS:

* `ICPS2023_5_fd_base` — top cell; frame ring (`16_5x5_flame`, FRAME 14/0) plus
  4 pad arrays.
* `pad_60_m1_tin` — 60 × 60 µm pad: one M1(Al) plate (7/0), two TiN shapes (5/0)
  and a 4 × 4 array of `tin_M1` contact/via clusters (CNT 6/0). **28 instances**
  (8 + 8 + 6 + 6) sit on the top cell.
* `tin_M1` — one M1/TiN/CNT cluster used inside the pads.

The library descends from the minimal Fab design contest 2023 template
(`Samples/Semicon2023/base_contest2023.GDS`, top cell `base_Rev0.93`). The
template's pre-placed device arrays (`base_array` with `pmos_array`,
`nmos_array`, `resistor_array`, `tin_m1_cap_array`) were removed so that the
frame can be used as the starting point of a new design; **the 28 pads and the
frame were kept unchanged**.

The shipped GDS is the foundry-mask view: the drawing layout is produced from
the vendor template and converted by
`libs.tech/klayout/drc/make_mask_view.py`, which preserves the cell hierarchy
(the LEF/DEF views are emitted per macro) and proves the view against a real
fab-prep conversion of the same input. The vendor template itself stays
drawing-numbered; it is a foundry authoring source, not a flow artifact.

## Regenerating

```bash
nix develop --command bash -euo pipefail -c '
  TMP=$(mktemp -d) &&
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_fd_base.rb \
    -rd out=$TMP/fd_base_drawing.gds &&
  python3 libs.tech/klayout/drc/make_mask_view.py \
    --input $TMP/fd_base_drawing.gds \
    --output libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds &&
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb &&
  python3 libs.ref/ICPS2023_5_fd_base/tools/test_pad_views.py
'
```

`make_fd_base.rb` asserts that exactly 28 pads are present before writing;
`make_mask_view.py` converts the drawing layout to the foundry mask numbering,
keeps the cell hierarchy, and refuses to write a view that differs from a real
fab-prep conversion. `make_lef_lib.rb` asserts 28 separate 60 × 60 µm M1(Al)
shapes, emits canonical ML1 LEF/DEF and a separate LibreLane alias LEF/DEF
(`M1` maps to the same physical mask 7/0), and emits Liberty without unqualified
pad capacitance or timing constraints. The generic `PAD_XX` aliases are assigned
top-to-bottom, then left-to-right. `doc/pad_map.csv` cross-references them to the
source GDS text labels `pin01`–`pin28`, the `pad_60_m1_tin` array side, and each
M1 pad rectangle center. The GDS has four unnamed regular arrays of that child
(8 + 8 + 6 + 6 pads), not named per-pad instances; array side and center identify
each member. The source labels provide
no signal function, bonding schedule, or package-to-design-net assignment.
Assign design nets separately; do not infer functions from labels.

## Limits

* No timing, no drive strength, no ESD rating: the foundry does not characterise
  an I/O cell for this process. The only documented limit is the absolute
  maximum rating of ±7 V (VDS/VGD/VGS).
* No **process-qualified** technology LEF, placement site, routing-track
  definitions, or OpenROAD GDS layer map is shipped. An explicitly experimental
  LibreLane overlay now exists in `libs.tech/librelane/`; it uses user-directed
  submicron TiN/POLY assumptions that conflict with the checked-in DRC and does
  not make this physical-only frame routable. See its README for exact limits.
* `AIST-SOI-CMOS.xs` v0.2 is a 2021 visualization prior (TBOX 400 nm, TiN
  50 nm, first TEOS 250 nm, ML1 300 nm), not a confirmed 2023 production stack.
  The PDK's TiN-cap density of 0.115 fF/µm² corresponds to about 300 nm oxide;
  its difference from the XS 250 nm TEOS value is unresolved.
* The frame ring is a drawing, not a seal ring with documented widths.

## Use as a design frame

Use `gds/ICPS2023_5_fd_base.gds` as a physical overlay at its original origin;
do not instantiate the full-frame BLOCK in the standard-cell placement database.
Use `def/ICPS2023_5_fd_base.def` for the 28 independent fixed I/O ports
(`PAD_00`–`PAD_27`) or the LibreLane alias DEF for a router using layer name
`M1`; assign design nets to them, and floorplan the central core separately.
The DEF contains no standard-cell rows or PDN plan. Run
`libs.tech/klayout/drc/icps2023_5.drc` on the assembled layout.
