# ICPS2023_5 layer table

This table defines both KLayout layer-number spaces; it does not imply that
every GDS in the repository uses the same one. Newly authored native layouts
and KLayout PCells use **design** numbers. Some committed reference GDS files
are already **fab** (foundry-mask) numbered.

KLayout DRC/LVS select the numbering space with `-rd layer_mode=design|fab`;
PEX uses `--layer-mode design|fab`. These tools retain their `design` defaults
for compatibility. Selecting a mode interprets GDS layer numbers; it does not
convert a file.

* **design** — native drawing numbers used by PCells and designer-authored
  layouts.
* **fab** — mask numbering of a fab-prepared layout. The conversion is performed
  by `libs.tech/klayout/drc/icps2023_5_fab_prep.drc`, which takes design-numbered
  input and writes fab-numbered output.

## Input GDS mode

| Input | Mode and handling |
|-------|-------------------|
| Native layout authored with KLayout PCells | `design`; run design-mode DRC/LVS/PEX. Apply fab-prep once only when making a mask view. |
| Output of `icps2023_5_fab_prep.drc` | `fab`; run fab-mode DRC/LVS/PEX directly. Do not reconvert. |
| `libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds` and `libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds` | Already `fab`; run fab-mode decks directly, never fab-prep again. The 2x2 frame is derived, not a foundry-published frame. |
| `libs.ref/ICPS2023_5_fd_pr/gds/ICPS2023_5_fd_pr.gds` | Already `fab`; flattened primitive mask view; run fab-mode decks directly, never reconvert. |
| `Samples/Semicon2023/NAND/nand_sample.GDS` | Already `fab`; run fab-mode decks directly, never reconvert. |
| `Samples/Semicon2023/base_contest2023.GDS` | Drawing-numbered authoring source; if checked directly, use design mode. It is not a prepared mask view. |

`drc/test_foundry_migration.py` pins the checked-in frame, primitive, and NAND
mask-numbering states and runs DRC on those views in fab mode. The distinct
native-design-to-fab geometry transformations are pinned by
`drc/test_conversion_semantics.py`.

`libs.tech/klayout/icps2023_5.lyp` holds the design layers,
`libs.tech/klayout/icps2023_5_fab.lyp` the fab (mask) layers.

## Design layers

| # | Name | Purpose | Fab mask / conversion |
|---|------|---------|----------|
| 1/0 | `NWL` | n- body / isolation region (used by `R_pdiff` `use_nwell`) | – (n- film is the default body) |
| 3/0 | `DIFF` | SOI island, active area, diffusion resistor/capacitor bodies | `SOI` 4/0 |
| 5/0 | `TIN` | TiN gate electrode and TiN local interconnect ("bridge") | `TIN_GATE` 5/0; interacting connected TiN polygons are biased during fab-prep |
| 5/1 | `TIN_BLK` | TiN block – splits a TiN polygon into two gates (experimental NMOS4 selector in design-mode LVS) | drawn, no own mask; merged during fab-prep |
| 7/0 | `CNT` | contact hole (source, drain, gate, bridge) | `CONTACT` 6/0 |
| 8/0 | `ML1` | metal 1 (Al) | `M1_AL` 7/0 |
| 9/0 | `VIA1` | via, ML1 → ML2 | `VIA1` 8/0 |
| 10/0 | `ML2` | metal 2 (Al) — **option, see below** | `M2_AL` 9/0 |
| 11/0 | `VIA2` | via, ML2 → ML3 — **option, see below** | `VIA2` 11/0 |
| 12/0 | `ML3` | metal 3 (Al) — **option, see below** | `M3_AL` 12/0 |
| 13/0 | `TEXT` | labels / net names | – |
| 14/0 | `FRAME` | chip frame and seal | `FRAME` 14/0 |
| 15/0 | `RES` | resistor block (body of diffusion resistors) | `RES` 15/0 |
| 16/0 | `CAP` | capacitor block (TiN/ML1 plate definition) | `CAP` 16/0 |
| 17/0 | `DIO` | diode block (anode/cathode definition) | `DIO` 17/0 |
| 18/0 | `PAREA` | P+ source/drain drawing dope area | `P_DIFF` 2/0, derived mask window |
| 19/0 | `NAREA` | N+ source/drain drawing dope area | `N_DIFF` 3/0, derived mask window |
| 20/0 | `MASK` | DRC/LVS exclusion zone: geometry under this layer is ignored | – |
| 21/0 | `NCH` | NMOS channel doping (`N-ch` mask), also the experimental NMOS4 body-tie path (`pwl` in LVS). The default conversion emits only this explicit layer; `-rd legacy_pwell=true` reproduces the old auto-generated PWell (`narea.interacting(tin).sized(2)`), see `drc/test_conversion_semantics.py` | `N_CH` 13/0 |
| 50/0 | `PAD` | pad opening | `PAD` 10/0 |
| 63/63 | `EXCLUDE` | hard keep-out (imported/foreign geometry is ignored) | – |
| 100/0 | `CELL_BOUNDARY` | Experimental/private standard-cell candidate marker; not a published placement contract; drawing only, removed by fab-prep | – |
| 101/0–105/0 | `DM_dcn`, `DM_pcn`, `DM_nscn`, `DM_pscn`, `DM_via1` | dummy (DFM) fill marks in candidate layouts; no mask output | – |

Layer polarity (the `W/B` column of the PDK documents): `ALIGNMENT`, `P_DIFF`,
`N_DIFF`, `CONTACT` and `N_CH` are clear/white-field masks; `SOI`, `TIN_GATE`,
`M1_AL` are dark/black-field masks. Polarity is mask-shop information; the PDK
draws positive polygons. Fab-prep is not a pure layer rename:

* It derives the `P_DIFF`/`N_DIFF` windows from drawing dope areas, the SOI,
  2 µm mask bias, and device/diode exclusions; they are not 1:1 copies of
  `PAREA`/`NAREA`.
* TiN bias is applied by 2 µm per side to the whole connected TiN polygon
  selected by its interaction with `DIFF`, clipped by the `(DIFF + TiN)` extents.
  It can affect off-island connected bars and landing pads as well as the gate.
* By default, the N-ch mask is the explicit drawing `NCH` layer. The legacy
  auto-generated PWell behavior is opt-in with `-rd legacy_pwell=true`.

These behaviors are pinned by `drc/test_conversion_semantics.py`; they do not
establish process limits or foundry qualification.

## Metal 2 / metal 3 (options)

`ML2`, `VIA1`, `VIA2` and `ML3` are **not qualified** by the foundry. The
reference manual lists `Via` and `M2(Al)`/`M3(Al)` as `T.B.D.`, and the
recommended design flow uses M1 only (`M1` layer, minimum width 5 µm, minimum
space 4 µm, 9 µm line-and-space). These layers remain in the layer table,
properties, DRC/LVS decks and xtor stack because existing `ICPS2023_5_Basic`
cells and `TIN_cap` draw them; PEX intentionally omits them because no
resistance or capacitance values are qualified. Their DRC rules are marked
`[TBD]` and are derived from the M1 rules (mm) or inherited from the
Mineda2022_6 rule deck; treat them as a proposal, not foundry sign-off.

## Numbering history (why the old files disagreed)

The paths in this table are the pre-refactor locations; they no longer exist.

| Legacy file | Space | Fate |
|-------------|-------|------|
| `Technology/tech/ICPS2023_5.lyp` | design | merged into `icps2023_5.lyp` |
| `Technology/tech/ICPS_SOI.lyp` | fab | merged into `icps2023_5_fab.lyp` |
| `Technology/tech/YSS_SOI_2023_1.lyp` | fab (YSS predecessor, 11 layers) | dropped, subset of `icps2023_5_fab.lyp` |
| `drc.lydrc`, `lvs.lylvs` | design, but with a `product == true` branch for fab numbering | one deck each, `layer_mode` switch |
| `lvs_fabrication.lylvs` | fab | merged as `layer_mode=fab` |
| `area_cut.lydrc` | Mineda2022_6 fab numbering (pad on 20/0) | dropped; it was a stale copy whose `input(20,0)` "pad" contradicts `PAD` 50/0 |
| `ICPS2023_5_to_SOI.lydrc` | design → fab conversion | rewritten as `drc/icps2023_5_fab_prep.drc` |
| `AIST-SOI-CMOS.xs`, `YSS-SOI-CMOS.xs` | mixed 2022 numbers (e.g. `cnt` 7/0) | kept as process cross sections; they only feed the 3D viewer |
