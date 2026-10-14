# ICPS2023_5 layer table

Single source of truth for layer numbers used by every file in this PDK
(`libs.tech/klayout`, `libs.tech/xschem`, `libs.ref`, all KLayout decks).

The process has two layer number spaces and the legacy repository mixed them in
three different ways (design decks, "product" decks, and the YSS predecessor
numbering). Both spaces are defined here once, and every deck selects one with
`-rd layer_mode=design|fab`.

* **design** — what designers draw. Numbers match the GDS cells shipped in
  `libs.ref/` and the KLayout PCells. Canonical for everything in this repo.
* **fab** — mask numbering used by the foundry dataprep for the minimal Fab
  mask set. Produced from design data by
  `libs.tech/klayout/drc/icps2023_5_fab_prep.drc`.

`libs.tech/klayout/icps2023_5.lyp` holds the design layers,
`libs.tech/klayout/icps2023_5_fab.lyp` the fab (mask) layers.

## Design layers

| # | Name | Purpose | Fab mask |
|---|------|---------|----------|
| 1/0 | `NWL` | n- body / isolation region (used by `R_pdiff` `use_nwell`) | – (n- film is the default body) |
| 3/0 | `DIFF` | SOI island, active area, diffusion resistor/capacitor bodies | `SOI` 4/0 |
| 5/0 | `TIN` | TiN gate electrode and TiN local interconnect ("bridge") | `TIN_GATE` 5/0 |
| 5/1 | `TIN_BLK` | TiN block – splits a TiN polygon into two gates (body-tie devices) | drawn, no own mask |
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
| 18/0 | `PAREA` | P+ source/drain doping | `P_DIFF` 2/0 |
| 19/0 | `NAREA` | N+ source/drain doping | `N_DIFF` 3/0 |
| 20/0 | `MASK` | DRC/LVS exclusion zone: geometry under this layer is ignored | – |
| 21/0 | `NCH` | NMOS channel doping (`N-ch` mask), also the body-tie path (`pwl` in LVS) | `N_CH` 13/0 |
| 50/0 | `PAD` | pad opening | `PAD` 10/0 |
| 63/63 | `EXCLUDE` | hard keep-out (imported/foreign geometry is ignored) | – |
| 101/0–105/0 | `DM_dcn`, `DM_pcn`, `DM_nscn`, `DM_pscn`, `DM_via1` | dummy (DFM) fill marks inside library cells | – |

Layer polarity (the `W/B` column of the PDK documents): `ALIGNMENT`, `P_DIFF`,
`N_DIFF`, `CONTACT` and `N_CH` are clear/white-field masks; `SOI`, `TIN_GATE`,
`M1_AL` are dark/black-field masks. Polarity is mask-shop information; the PDK
draws all layers positive and `icps2023_5_fab_prep.drc` copies them 1:1.

## Metal 2 / metal 3 (options)

`ML2`, `VIA1`, `VIA2` and `ML3` are **not qualified** by the foundry. The
reference manual lists `Via` and `M2(Al)`/`M3(Al)` as `T.B.D.`, and the
recommended design flow uses M1 only (`M1` layer, minimum width 5 µm, minimum
space 4 µm, 9 µm line-and-space). They are kept in the layer table, in the
PDK's layer properties, in the DRC/LVS/PEX decks and in the xtor stack because
they are drawn by existing `ICPS2023_5_Basic` cells and by `TIN_cap`
(`ML1`/`ML2` plates) — removing them would break those cells. Rules for them
are marked `[TBD]` in `drc/icps2023_5.drc` and are derived from the M1 rules
(mm) or inherited from the Mineda2022_6 rule deck; treat them as a proposal,
not as foundry sign-off.

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
