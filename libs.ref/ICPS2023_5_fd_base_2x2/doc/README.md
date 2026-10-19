# ICPS2023_5_fd_base_2x2 — derived 3200 × 3200 µm frame and 56-pad ring

Physical-only library: the **2x2 die-size option** of the minimal Fab SOI-CMOS
process — a 3200 × 3200 µm frame ring and 56 wire-bond pads, four times the
area of the 1600 × 1600 µm 1x1 frame and twice its pad count. The GDS is a
physical overlay; use the fixed top-level I/O ports in DEF instead of placing
the full frame as a blocking macro.

**The foundry did not publish a 2x2 frame GDS.** Only the 1x1 template exists
(`Samples/Semicon2023/base_contest2023.GDS`, top cell `base_Rev0.93`). This
library is therefore *derived* from that template under the convention below,
from the same foundry source cell as `libs.ref/ICPS2023_5_fd_base`. It is not
foundry-published geometry: reconcile it against the foundry's own 2x2 frame
before taping out, and do not treat the pad-to-net assignment or the wafer map
as foundry data.

## Convention

The 1x1 frame is a 50 µm two-box ring drawing (`16_5x5_flame`, FRAME 14/0:
die boundary at ±800 µm, inner boundary at ±750 µm), 28 instances of the
foundry pad cell `pad_60_m1_tin` (60 × 60 µm) at 200 µm pitch, pad outer edge
flush with the ring inner boundary, and `pin01`–`pin28` labels running
counter-clockwise around the ring (south left→right, east bottom→top, north
right→left, west top→bottom). The 2x2 option changes only die area and pad
count, so every other 1x1 measure is preserved and the ring is continued:

| Item | 1x1 `ICPS2023_5_fd_base` | 2x2 `ICPS2023_5_fd_base_2x2` |
|------|--------------------------|------------------------------|
| Die boundary | 1600 × 1600 µm | 3200 × 3200 µm |
| Frame ring (`FRAME` 14/0) | ±800/±750 µm, 50 µm wide | ±1600/±1550 µm, 50 µm wide |
| Pad cell / size | `pad_60_m1_tin`, 60 × 60 µm | unchanged cell, 60 × 60 µm |
| Pad pitch | 200 µm | 200 µm |
| Pads north / south | 8 + 8 | 16 + 16 |
| Pads west / east | 6 + 6 | 12 + 12 |
| Total pads | 28 | 56 |
| Pad outer edge | flush with ±750 µm | flush with ±1550 µm |
| N/S row end margin | 20 µm to the ring inner corner | 20 µm (same rule) |
| Row placement | N/S centred on the die; W/E at y ≈ −12 µm in the template | all four rows centred on the die |
| Labels | `pin01`–`pin28` | `pin01`–`pin56`, same CCW ring |

The 16 north/south pads span 3060 µm of the 3100 µm inner edge — the same
20 µm end margin the 1x1 template gives its 8 pads on the 1500 µm edge. The
west/east rows keep the 1x1 count ratio (6 → 12, centred). The 1x1 template's
12 µm downward offset of its west/east rows is *not* carried over: it has no
documented purpose and a centred ring is the simpler derived convention.

Cell naming: the ring cell is `frame_3200`; the 1x1 counterpart is the
foundry's `16_5x5_flame` (name most likely encoding the 1600 µm die and a 5×5
wafer map — an inference from the name and the published contest wafer
layout, not documented by the foundry). No wafer map is claimed for the 2x2
option; only the die is defined here.

## Contents

| View | File | Notes |
|------|------|-------|
| GDSII | `gds/ICPS2023_5_fd_base_2x2.gds` | 3200 × 3200 µm frame and 56 pad overlay, **foundry mask numbering** |
| LEF | `lef/ICPS2023_5_fd_base_2x2.lef` | canonical reusable 60 × 60 µm pad-cell macro; layer is named ML1 |
| DEF | `def/ICPS2023_5_fd_base_2x2.def` | 56 independent fixed `PAD_00`–`PAD_55` ML1 top-level pins |
| LibreLane LEF | `lef/ICPS2023_5_fd_base_2x2_librelane.lef` | router alias `M1` for physical M1(Al) mask 7/0; geometry unchanged |
| LibreLane DEF | `def/ICPS2023_5_fd_base_2x2_librelane.def` | same 56 fixed pins with router layer name M1 |
| Liberty | `lib/ICPS2023_5_fd_base_2x2.lib` | physical pin identity only; no pad capacitance or timing constraint |
| Pad crosswalk | `doc/pad_map.csv` | source GDS pin labels, array side, and ML1 centers for all 56 DEF pins |

Cells in the GDS:

* `ICPS2023_5_fd_base_2x2` — top cell; frame ring (`frame_3200`, FRAME 14/0)
  plus 4 pad arrays.
* `frame_3200` — die boundary and inner boundary as two filled boxes on
  FRAME 14/0, i.e. the same drawing style as the foundry 1x1 ring.
* `pad_60_m1_tin` — foundry pad cell; the mask view carries its M1(Al) plate
  (7/0), TiN shapes (5/0) and CNT cuts (6/0). **56 instances** (16 + 16 +
  12 + 12) sit on the top cell as four unnamed regular arrays.
* `tin_M1` — foundry M1/TiN/CNT cluster used inside the pads.

The foundry template's pre-placed device arrays (`base_array` with
`pmos_array`, `nmos_array`, `resistor_array`, `tin_m1_cap_array`) are not
instantiated, exactly as in the 1x1 library: the frame is the starting point
of a new design, not a pre-populated one.

DEF pin names follow the 1x1 rule: rows top to bottom, then left to right.
`PAD_00`–`PAD_15` are the north row (left to right, i.e. `pin44`–`pin29`),
`PAD_16`–`PAD_39` are the west/east pairs top to bottom (west before east at
each level), `PAD_40`–`PAD_55` are the south row (`pin01`–`pin16`). The GDS
carries `pin01`–`pin56` labels; the source labels provide no signal function,
bonding schedule, or package-to-design-net assignment. Assign design nets
separately; do not infer functions from labels.

## Regenerating

```bash
nix develop --command bash -euo pipefail -c '
  TMP=$(mktemp -d) &&
  klayout -b -r libs.ref/ICPS2023_5_fd_base_2x2/tools/make_fd_base_2x2.rb \
    -rd out=$TMP/fd_base_2x2_drawing.gds &&
  python3 libs.tech/klayout/drc/make_mask_view.py \
    --input $TMP/fd_base_2x2_drawing.gds \
    --output libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds &&
  klayout -b -r libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb \
    -rd gds=libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds \
    -rd lef=libs.ref/ICPS2023_5_fd_base_2x2/lef/ICPS2023_5_fd_base_2x2.lef \
    -rd lib=libs.ref/ICPS2023_5_fd_base_2x2/lib/ICPS2023_5_fd_base_2x2.lib \
    -rd def=libs.ref/ICPS2023_5_fd_base_2x2/def/ICPS2023_5_fd_base_2x2.def \
    -rd librelane_lef=libs.ref/ICPS2023_5_fd_base_2x2/lef/ICPS2023_5_fd_base_2x2_librelane.lef \
    -rd librelane_def=libs.ref/ICPS2023_5_fd_base_2x2/def/ICPS2023_5_fd_base_2x2_librelane.def \
    -rd frame_cell=ICPS2023_5_fd_base_2x2 -rd expected_pins=56 &&
  python3 libs.ref/ICPS2023_5_fd_base_2x2/tools/test_pad_views_2x2.py
'
```

`make_fd_base_2x2.rb` reads the foundry template, builds the ring cell and the
four pad arrays in a fresh top cell, deletes the template's device arrays, and
writes a drawing layout and `doc/pad_map.csv`. It asserts the whole geometry
contract before and after writing: 3200 × 3200 µm die, the two ring boxes,
56 60 × 60 µm pads at 200 µm pitch with their row margins, pad-to-pad
separation, and the complete `pin01`–`pin56` label set at the pad centers.
`make_mask_view.py` converts that drawing layout to the foundry mask numbering,
keeps the cell hierarchy, and refuses to write a view that differs from a real
fab-prep conversion of the same input. Both the drawing generator and the mask
view suppress GDSII timestamps, so regeneration is byte-identical. The
LEF/DEF/Liberty views come from the shared pad-view generator
in `libs.ref/ICPS2023_5_fd_base/tools/make_lef_lib.rb` with the overrides
shown above (that generator's defaults still reproduce the 1x1 library byte
for byte). `tools/test_pad_views_2x2.py` independently re-checks the written
GDS against the DEF, the LibreLane alias views and `doc/pad_map.csv`.

## Limits

* **Derived, not foundry-shipped geometry.** The 2x2 pad ring above is a
  documented extension of the published 1x1 ring; the foundry has published
  neither a 2x2 frame nor its pad positions. Treat pad coordinates as this
  library's contract, not as a foundry specification.
* No timing, no drive strength, no ESD rating: the foundry does not
  characterise an I/O cell for this process. The only documented limit is the
  absolute maximum rating of ±7 V (VDS/VGD/VGS).
* No **process-qualified** technology LEF, placement site, routing-track
  definitions, or OpenROAD GDS layer map is shipped. The experimental
  LibreLane overlay in `libs.tech/librelane/` does not make this physical-only
  frame routable.
* The frame ring is a drawing, not a seal ring with documented widths.
* `libs.tech/klayout/drc/icps2023_5.drc` reports 0 markers on this GDS in both
  the default and the `strict=true` 1M profile with the pinned KLayout
  version, as it does on the 1x1 frame. The shipped GDS is mask-numbered, so
  the same run is the foundry-mask check (`-rd layer_mode=fab`, no conversion
  step) and also reports 0 markers
  (`libs.tech/klayout/drc/test_foundry_migration.py`). That is a bare-overlay
  result: it does not qualify an assembled 2x2 core, I/O, or power delivery.

## Use as a design frame

Use `gds/ICPS2023_5_fd_base_2x2.gds` as a physical overlay at its original
origin; do not instantiate the full-frame BLOCK in the standard-cell placement
database. Use `def/ICPS2023_5_fd_base_2x2.def` for the 56 independent fixed
I/O ports (`PAD_00`–`PAD_55`) or the LibreLane alias DEF for a router using
layer name `M1`; assign design nets to them, and floorplan the central core
separately. The DEF contains no standard-cell rows or PDN plan. Run
`libs.tech/klayout/drc/icps2023_5.drc` on the assembled layout.

LibreLane registration is limited to the explicit pad views at
`libs.tech/librelane/ICPS2023_5_fd_base_2x2/config.tcl`. Select the matching
`FP_DEF_TEMPLATE` from a design config such as
`libs.tech/librelane/configs/icps2023_5_2m_post2025_2x2.json`; the pad library
config alone does not select the 3200 µm die or the 56 fixed pins.


`libs.tech/stdcell/config_2x2.json` applies the same explicit engineering
reservations to this frame for the experimental RTL routing fixtures
(`routed_benchmarks.py --config ...`); the measured capacity and the recorded
routing outcome are summarized in the root README's closure boundary. That
experiment closed no new fixture, so the 2x2 frame is an area option, not a
routing or authority upgrade.
