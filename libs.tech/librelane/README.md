# ICPS2023_5 LibreLane technology bring-up

This directory contains **experimental router overlays**, not a complete or
foundry-qualified LibreLane PDK. The physical-only pad frames remain overlays;
`ICPS2023_5_gen_sc` has accepted GDS/LEF/SPICE, a single-corner Xyce timing
view, and the named synthesis/CTS utility cells, but it is not signoff data.
Profiles now supply explicit placement, synthesis, PDN, and partial RC
contracts so PDK validation reaches design-side inputs. None establishes
placement/routing, electrical closure, or fabrication signoff.

The organization follows the IHP Open-PDK pattern (`libs.tech/librelane/config.tcl`,
per-library `config.tcl`, and library-owned tracks) only. No IHP process values
or layer data are reused.

## Routing assumptions and explicit configurations

The root PDK config is intentionally fixed to the conservative experimental 1M
overlay and the 1x1 `ICPS2023_5_fd_base` pad library. It does not interpret
arbitrary profile/frame environment variables. This preserves a predictable
default and keeps LibreLane selection separate from the canonical KLayout
`icps2023_5_1m` / `icps2023_5_multimetal_experimental` process-profile switch.

Use the supported LibreLane design-config fragments when an experiment needs a
different overlay:

| Fragment | Router bounds | Frame template | Status |
|---|---|---|---|
| `configs/icps2023_5_1m_1x1.json` | `POLY` → `M1` | 1x1, 28 fixed pads | Default-compatible 1M overlay |
| `configs/icps2023_5_2m_post2025_2x2.json` | `POLY` → `M2` | Derived 2x2, 56 fixed pads | Explicit post-2025 geometry experiment |

The fragments use supported `TECH_LEFS`, `FP_TRACKS_INFO`, `RT_MIN_LAYER`,
`RT_MAX_LAYER`, M1-only clock bounds, and `FP_DEF_TEMPLATE`. The matching
`PAD_CELL_LIBRARY` loads only the pad LEF/GDS views; `FP_DEF_TEMPLATE` carries
the die area and fixed top-level pins. The 2x2 frame GDS is not a placeable
macro and the derived frame remains subject to foundry reconciliation.

The isolated post-2025 collateral lives in
`experimental_2m_post2025/`:

* `M1` aliases canonical ML1 8/0; `M2` aliases canonical ML2 10/0;
  `VIA1` aliases canonical VIA1 9/0.
* M2 uses provisional 6 µm width/6 µm spacing and both-axis 12 µm tracks,
  from the repository's provisional upper-metal DRC rules. `VIA_M1_M2` uses a
  provisional 5 × 5 µm VIA1 cut and 1 µm landing enclosure. M2 has no sheet
  resistance in the router tech LEF or the KLayout PEX model and remains
  outside the PEX/RCX resistance model.

The site, utility cells, and profile PDN/RC inputs are intentionally
experimental. The cell LEFs retain canonical `ML1` while the router overlay
uses alias `M1`; that layer-name integration and the absence of qualified
electrical limits still prevent any signoff claim.

| LibreLane layer | Direction | Width | Pitch | R/sq | Provenance |
|---|---|---:|---:|---:|---|
| `POLY` | horizontal | 0.12 µm | 0.32 µm | 25 Ω/sq | User-directed router-only experiment; no POLY layer/mask or PEX model |
| `TIN` | vertical | 0.14 µm | 0.28 µm | 20 Ω/sq | Sheet resistance from the checked-in KLayout PEX model; geometry remains experimental |
| `M1` | horizontal | 6.0 µm | 10.0 µm | 0.20 Ω/sq | `M1` aliases physical `ML1` GDS 8/0; width/space are DRC minima (6/4 µm), pitch is from `tracks.info`, R/sq from PEX |

The TiN/POLY gaps in the LEF are pitch minus width. These assumed submicron
TiN/POLY geometries conflict with the checked-in micrometre-scale DRC limits;
they are not DRC-clean, manufacturable, or signoff collateral. M1's 10 µm pitch
is incompatible with a conventional compact 5-track cell. Both technology
LEFs now declare the shared experimental `ICPS2023_5_ROW500` site (`1.0 ×
500.0 µm`, `CLASS CORE`, `SYMMETRY Y`) to match the tallest published cell
(`MUX4_STATIC_X1.lef`, `SIZE 1797 BY 500`).

`POLY` is assigned local engineering GDS 200/0 and the three synthetic via cuts
201/0–203/0. These IDs are not canonical process layers or foundry mask
assignments. The actual pad `CNT` remains mapped to 7/0; the experimental
0.08 × 0.08 µm cuts are never aliased to CNT. The POLY↔TiN via is an additional
placeholder required to connect the three-layer stack. Its POLY/TiN landings
are 0.12/0.14 µm squares; user-provided M1 via examples are represented with
6 × 6 µm M1 lands to respect the current M1 width minimum. All small cuts and
non-M1 enclosures remain unqualified assumptions.

`icps2023_5_1m.tech.lef` and `tracks.info` expose only POLY, TiN, and M1. The
routing directions follow the proposed 5T topology:

```text
Track 5 (top)     VDD rail on M1
Track 4           PMOS active / PMOS pin channel
Track 3           TiN vertical interconnect / cross-routing zone
Track 2           NMOS active / NMOS pin channel
Track 1 (bottom)  VSS rail on M1

M1:   horizontal primary signal routing; clocks constrained to M1
TiN:  vertical cell-pin access and row-crossing jumper
POLY: horizontal last-resort jumper
```

The LibreLane layer bounds are `RT_MIN_LAYER=POLY` and `RT_MAX_LAYER=M1`.
`flow_defaults.json` is a **design-config fragment** to merge into a future
LibreLane design config; LibreLane does not load that JSON automatically. It
sets 39% placement density, M1-only clock routing, congestion disallowed, and
antenna repair disabled because no qualified diode cell or antenna model exists.
The router's min/max layer bounds do not themselves enforce “90% of signal
trunks on M1” or “POLY only when M1 is blocked”; those policies need route-level
guides/costs and validation against actual cells.
The checked-in explicit profile JSONs intentionally keep the same fail-closed
antenna setting and are the supported examples for profile selection.

## Experimental profile values and provenance

The root `config.tcl` supplies the 1M defaults required during LibreLane PDK
validation; each JSON profile repeats its resolved values and overrides the
2M-specific layer choices. These are experiment inputs, not qualified process
contracts. Source paths are repository-relative; geometry values are in µm
unless stated otherwise.

| Variable | 1M profile | 2M profile | Source and basis |
|---|---|---|---|
| `PLACE_SITE` | `ICPS2023_5_ROW500` | `ICPS2023_5_ROW500` | Both technology LEFs declare `CLASS CORE`, `SYMMETRY Y`, and `SIZE 1.0 BY 500.0` (`libs.tech/librelane/experimental_1m/icps2023_5_1m.tech.lef`, `libs.tech/librelane/experimental_2m_post2025/icps2023_5_2m.tech.lef`); `libs.ref/ICPS2023_5_gen_sc/lef/MUX4_STATIC_X1.lef` is `1797 × 500` and uses the same site. |
| `SYNTH_DRIVING_CELL` | `BUF_X1/Y` | `BUF_X1/Y` | `libs.tech/stdcell/logic.py` defines `BUF_X1` with input `A` and output `Y`; LibreLane's driver cell is the output pin. |
| `SYNTH_BUFFER_CELL` | `BUF_X1/A/Y` | `BUF_X1/A/Y` | `libs.tech/stdcell/logic.py` defines `A → Y`; LibreLane requires `{cell}/{input}/{output}` (`../librelane/librelane/config/flow.py`). |
| `SYNTH_TIEHI_CELL` | `TIEHI_X1/Y` | `TIEHI_X1/Y` | `libs.tech/stdcell/logic.py`: `TIEHI_X1`, output `Y=1`. |
| `SYNTH_TIELO_CELL` | `TIELO_X1/Y` | `TIELO_X1/Y` | `libs.tech/stdcell/logic.py`: `TIELO_X1`, output `Y=0`. |
| `CTS_ROOT_BUFFER` | `BUF_X1` | `BUF_X1` | `libs.tech/stdcell/logic.py`: published buffer cell. |
| `CTS_CLK_BUFFERS` | `["BUF_X1"]` | `["BUF_X1"]` | `libs.tech/stdcell/logic.py`: published buffer cell. |
| `IO_PIN_H_LAYER` | `M1` | `M1` | `libs.tech/librelane/experimental_1m/icps2023_5_1m.tech.lef` and `libs.tech/librelane/experimental_2m_post2025/icps2023_5_2m.tech.lef`: M1 is horizontal; the die-frame fixed pins are on ML1/M1. |
| `IO_PIN_V_LAYER` | `TIN` | `M2` | OpenROAD's pin placer rejects a vertical pin on a horizontal layer (`[PPL-0046] Layer M1 preferred direction is not vertical`), so the 1M profile uses the overlay's vertical TiN jumper and the 2M profile uses vertical M2. The frame's own fixed pins are ML1/M1 and stay available for a later pad-to-net assignment. |
| `OUTPUT_CAP_LOAD` | `5.0 fF` | `5.0 fF` | `libs.tech/librelane/ICPS2023_5_gen_sc/config.tcl`: Xyce/BSIMSOI characterization scope at 3 V / 25 °C, 5 fF load, 2 ns input slew. |
| `CLOCK_TRANSITION_CONSTRAINT` | `2.0 ns` | `2.0 ns` | Same documented 2 ns input-slew scope; not signoff timing. |
| `IO_DELAY_CONSTRAINT` | `0.0%` | `0.0%` | No system I/O budget is characterized or selected; explicit no-budget policy, not a process number. |
| `MAX_FANOUT_CONSTRAINT` | `1 cell` | `1 cell` | One-sink experiment guard; the local SCL only publishes X1 strengths and has no characterized max-fanout number. |
| `CLOCK_UNCERTAINTY_CONSTRAINT` | `0.0 ns` | `0.0 ns` | No jitter/uncertainty datum is supplied; explicit no-data policy, not signoff. |
| `TIME_DERATING_CONSTRAINT` | `0.0%` | `0.0%` | Only the nominal 3 V / 25 °C single-corner evidence is present; no PVT derating is characterized. |
| `TIMING_VIOLATION_CORNERS` | `["nom_xyce_3v_25c"]` | `["nom_xyce_3v_25c"]` | `libs.tech/librelane/ICPS2023_5_gen_sc/config.tcl` sets this sole STA corner. |
| `GRT_LAYER_ADJUSTMENTS` | `[0,0,0]` | `[0,0,0,0]` | Zero capacity reductions for 3/4 routing layers in `libs.tech/librelane/experimental_1m/icps2023_5_1m.tech.lef` / `libs.tech/librelane/experimental_2m_post2025/icps2023_5_2m.tech.lef`; no layer-capacity reductions are characterized. |
| `RCX_RULESETS` | `pdk_dir::libs.tech/librelane/rcx/icps2023_5_1m.rules` | `pdk_dir::libs.tech/librelane/rcx/icps2023_5_2m.rules` | Each profile points its nominal corner at a local partial OpenRCX model; terms and omissions are documented below. |
| `PDN_RAIL_LAYER` | `M1` | `M1` | Router alias for physical ML1 8/0 in `libs.tech/librelane/experimental_1m/icps2023_5_1m.tech.lef` and `libs.tech/librelane/experimental_2m_post2025/icps2023_5_2m.tech.lef`; standard-cell LEFs retain canonical `ML1`. |
| `PDN_RAIL_WIDTH` | `12.0 µm` | `12.0 µm` | Full-width cell power-pin bands are 12 µm high in the published cell LEFs; ML1 DRC minimum is 6 µm. |
| `PDN_RAIL_OFFSET` | `0.0 µm` | `0.0 µm` | `libs.tech/librelane/config.tcl` supplies the required legacy value; `../librelane/librelane/scripts/openroad/common/pdn_cfg.tcl` creates `-followpins` rails and does not use this offset for them. |
| `PDN_VERTICAL_LAYER` | `M1` | `M2` | The 1M stack has a single low-resistance conductor, so a strap set on M1 would cross the M1 rails of the opposite polarity and short them; the layer is still declared as M1 and the pitch below is larger than the die, so no strap stripe lands inside the core and the profile is rails-only. The 2M profile runs its straps on vertical M2. |
| `PDN_HORIZONTAL_LAYER` | `M1` | `M1` | The same profile technology LEFs define horizontal M1/ML1 in both stacks. |
| `PDN_VWIDTH`, `PDN_HWIDTH` | `6.0`, `6.0 µm` | `6.0`, `6.0 µm` | DRC minima: TiN width 6 µm, M1 width 6 µm, and provisional ML2 width 6 µm (`libs.tech/klayout/drc/icps2023_5.drc`). |
| `PDN_VSPACING`, `PDN_HSPACING` | `6.0`, `4.0 µm` | `6.0`, `4.0 µm` | Strap/TiN and M2 space 6 µm, M1 space 4 µm, from `libs.tech/klayout/drc/icps2023_5.drc`. |
| `PDN_VPITCH`, `PDN_HPITCH` | `4000.0`, `4000.0 µm` | `24.0`, `20.0 µm` | OpenROAD refuses a stripe pitch below twice width+spacing (`[PDN-0175] Pitch 12.0000 is too small ... must be atleast 24.0000`), so the 2M strap pitch is `2 × (6+6) = 24` and the M1 value is 20. The 1M pitch exceeds the 3200 µm die so the declared strap set produces no stripe inside the 2M/1M core. |
| `PDN_VOFFSET`, `PDN_HOFFSET` | `0.0`, `0.0 µm` | `0.0`, `0.0 µm` | Both `libs.tech/librelane/experimental_1m/tracks.info` and `libs.tech/librelane/experimental_2m_post2025/tracks.info` specify 0.0 µm origins for TIN/M2 and M1. |
| `PDN_CORE_RING_VWIDTH`, `PDN_CORE_RING_HWIDTH` | `6.0`, `6.0 µm` | `6.0`, `6.0 µm` | Dormant ring dimensions use the corresponding DRC minimum widths in `libs.tech/klayout/drc/icps2023_5.drc`. |
| `PDN_CORE_RING_VSPACING`, `PDN_CORE_RING_HSPACING` | `6.0`, `4.0 µm` | `6.0`, `4.0 µm` | Dormant ring spacings use TiN/M2 6 µm and M1 4 µm minima in the same local DRC deck. |
| `PDN_CORE_RING_VOFFSET`, `PDN_CORE_RING_HOFFSET` | `0.0`, `0.0 µm` | `0.0`, `0.0 µm` | Dormant ring offsets align to the 0.0 µm track origins. |

`PDN_MULTILAYER` is `false` in both profiles. The rails themselves are
`-followpins` stripes on M1, and every published cell now carries a full-width
M1 VSS band at the row bottom (`y = 0..12`) and a full-width M1 VDD band at the
row top (`y = height-12..height`), identical for every cell, so one rail pair
per row reaches every instance. Those bands are also the cells' declared
VDD/VSS pin windows, and each is tied to the supply network inside the cell
through a TiN riser plus a short local M1 contact: M1 is the only signal layer
here, so a full-height *M1* riser would partition the routing region and make
the internal nodes of AOI21/OAI21/AOI22 unreachable. The NMOS row also starts
clear of the VSS band so the rail cannot consume the bottom escape corridor,
which otherwise pushes every NMOS net up over the PMOS row. `PDN_CORE_RING`
remains `false`: the frame pins have no assigned VDD/VSS pad set or pad-to-ring
connection contract. `PDN_RAIL_OFFSET` is required by LibreLane but does not
move pin-following rails.

Placement density and padding follow the process guidance for this stack: a
single-metal + TiN library cannot be placed at a normal target density, so the
1M profile uses `PL_TARGET_DENSITY_PCT 35` (30–40 % range) and the 2M profile
`PL_TARGET_DENSITY_PCT 65` (60–70 % range). Both profiles take the conservative
`GPL_CELL_PADDING 8` / `DPL_CELL_PADDING 8` (4 sites per side) and
`FP_CORE_UTIL 10`, which is the higher-padding end of the documented window.

Do not claim current, IR-drop, EM, or all-cell power-connectivity closure from
these geometry values.

### RCX coverage and limits

The checked-in `libs.tech/klayout/pex/icps2023_5_pex.py` supplies these measured
constants: ML1 `0.20 Ω/sq`, TiN `20 Ω/sq`, N+ `180 Ω/sq`, P+ `1200 Ω/sq`;
Al–TiN `0.9 Ω`, Al–N+ `150 Ω`, and Al–P+ `200 Ω` per 5 × 5 µm contact; and
eligible N/P diffusion-to-SUB capacitance `0.086 fF/µm²`. The corresponding
PEX code uses `CONTACT_REFERENCE_AREA_UM2=25.0` and scales contact resistance
by inverse cut area. The RCX rules retain local POLY's `25 Ω/sq` only from the
experimental technology LEF because POLY is absent from the PEX model.

Both `.rules` files include the known POLY/TiN/ML1 resistance terms and the
Al–TiN contact extrapolated to the local 0.08 × 0.08 µm `VIA_TIN_M1` cut:
`(0.9 Ω × 25 µm²)/(0.08 µm × 0.08 µm) = 3515.625 Ω`. This is an explicit
inverse-area extrapolation from a 5 × 5 µm reference contact, not a measured
small contact. The 2M file omits M2 and VIA1 resistance because the PDK marks
them T.B.D. and the PEX model does not cover them. The N+/P+ contact terms and
diffusion capacitance are not translated to routing metals: OpenRCX's wire
rules do not model the PEX diffusion layers.

These partial models contain **no metal ground capacitance, coupling
capacitance, fringe term, field-solver table, M2/ML2 resistance/capacitance,
or qualified via model**. PEX's `0.086 fF/µm²` is a diffusion-area-to-SUB term,
not a metal-wire coefficient. The files are not complete OpenRCX calibration
data, do not represent the full RC stack, and must not be used for timing
signoff. `RUN_SPEF_EXTRACTION` stays false; the supported post-layout extractor
is the repository's KLayout PEX script.

### Classic flow substitutions

Both profile `meta` objects select `flow: "Classic"` and remove
`Magic.StreamOut`, `Magic.WriteLEF`, `Magic.DRC`, `Magic.SpiceExtraction`,
`Checker.MagicDRC`, `Netgen.LVS`, and `Checker.LVS` through
`meta.substituting_steps` (`null` substitutions). Synthesis, floorplan, PDN,
placement, CTS, routing, KLayout streamout, and KLayout DRC remain in the
Classic flow. KLayout PEX remains the separate repository extraction path;
no external Magic/Netgen setup is invented.

## LibreLane integration

`config.tcl` provides experimental technology-level defaults and selects the
existing physical-only 1x1 pad library `ICPS2023_5_fd_base`. It deliberately
does **not** set a default `STD_CELL_LIBRARY` or interpret custom profile/frame
selectors. Explicit alternatives are documented in `configs/`.

`ICPS2023_5_gen_sc/config.tcl` follows the per-SCL organization used by
icsprout55-pdk but copies no process values. It lists the accepted local
LEF/GDS/SPICE views and binds the explicit experimental single-corner Liberty
view `libs.ref/ICPS2023_5_gen_sc/liberty/ICPS2023_5_gen_sc_experimental_xyce.lib`.
That view is limited to Xyce 7.10 / BSIMSOI 4.5.0 at nominal 3 V / 25 °C,
5 fF load and 2 ns input slew; it is not signoff timing. The shared experimental
`ICPS2023_5_ROW500` site and utility cells, plus profile-specific PDN/RC/CTS
settings, are now provided by the root config and explicit profiles. The cell
LEFs use canonical `ML1`, while the experimental router technology LEF uses
alias `M1`; that mismatch remains unqualified.
Both profiles set `RUN_MAGIC_*` false and use `meta.flow: "Classic"` with
`meta.substituting_steps` to remove Magic, Netgen, and their dependent Magic/LVS
checkers. KLayout streamout and DRC remain enabled and use
`libs.tech/klayout/drc/icps2023_5.drc`.

The repository's KLayout PEX script is a separate post-layout SPICE extractor,
not LibreLane's OpenRCX/SPEF step. It remains the supported PEX path and is
invoked with `libs.tech/klayout/pex/icps2023_5_pex.py` after layout exists. The
new `rcx/` rulesets are intentionally partial OpenRCX-format rule inputs:
they carry only available resistance terms and do not constitute timing SPEF
or signoff RC.

The native KLayout DRC/LVS/PEX checks and ngspice assembler remain independent
of the removed Magic/Netgen flow steps and must still be run through their
existing verification helpers. Removing `Checker.LVS` from this Classic profile
does not replace or waive the repository's native KLayout LVS path.

From the repository root, LibreLane 3.0.11 was loaded with the exact
commands below. Both profiles pass PDK validation and stop only because
design-side `DESIGN_NAME` and `VERILOG_FILES` were not supplied; each
profile already selects its `FP_DEF_TEMPLATE`. No synthesis, floorplan, PDN
generation, placement, CTS, routing, extraction, or signoff run is claimed.

```sh
nix develop ../librelane --command librelane --manual-pdk --pdk-root /tmp/icps2023_5_pdkroot --pdk ICPS2023_5 --scl ICPS2023_5_gen_sc libs.tech/librelane/configs/icps2023_5_1m_1x1.json
nix develop ../librelane --command librelane --manual-pdk --pdk-root /tmp/icps2023_5_pdkroot --pdk ICPS2023_5 --scl ICPS2023_5_gen_sc libs.tech/librelane/configs/icps2023_5_2m_post2025_2x2.json
```

The exact loader error tail for both is:

```text
Required variable 'DESIGN_NAME' did not get a specified value.
Required variable 'VERILOG_FILES' did not get a specified value.
LibreLane will now quit. Please check your configuration.
```

No Magic/Netgen or other PDK-contract error remains. The adjacent
`../librelane` Nix shell supplies OpenROAD, but the profiles are only
loader-valid experimental collateral; no PnR command is claimed.

## Verified bounded checks

The checked-in collateral has been exercised without claiming a complete PnR
flow:

* The 1x1 and derived 2x2 pad-view test suites pass, including GDS/LEF/DEF/
  Liberty cross-checks and the 28/56 fixed-pin contracts.
* The standard-cell, frame, mapping, native PCell, PEX, symbol, ALU, process-
  profile, and LVS-contract regressions pass in the repository test suite.
* Native KLayout DRC runs on both frame overlays and produces zero violation
  items.
* The KLayout PEX regression passes, including sheet/contact resistance,
  diffusion-to-`SUB` capacitance, and the ngspice DC IR smoke check.
* Xyce NF 7.10.0 is installed at `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` and
  runs with the available OpenMPI runtime. Existing bridge verification remains
  version-pinned to Xyce BSIMSOI 4.5.0 and does not promote the experimental
  card to the nominal model.
* LibreLane 3.0.11 validates both explicit profile fragments through the PDK
  contract and stops at the design-only `DESIGN_NAME`/`VERILOG_FILES` inputs.

These checks validate collateral contracts and process geometry only. They do
not establish placement, routing, timing, extraction, LVS, tapeout, or
fabrication signoff. The experimental overlay and derived 2x2 frame remain
research collateral until the missing foundry-qualified inputs and frame
reconciliation are supplied.

Before signoff, replace the experimental closure with qualified evidence:

1. Characterize the full Liberty scope, including sequential setup/hold arcs,
   justified STA corners, fanout/transition limits, and system timing budgets.
2. Reconcile canonical cell `ML1` pins with the router's `M1` alias and verify
   the 500 µm site, row symmetry, and all cell access geometries.
3. Complete the PDN contract: pad-to-core connection, layer/via strategy,
   current/IR/EM limits, straps/ring, and connectivity for every cell family.
   The current mesh settings are geometry experiments only.
4. Obtain full per-corner RC extraction inputs (wire ground/coupling/fringe,
   M2/via models where applicable), clock insertion/uncertainty/skew limits,
   and characterized CTS cells. The checked-in RCX rules do not supply these.
5. Keep KLayout streamout/DRC enabled and invoke the repository KLayout PEX
   wrapper separately. Magic remains disabled; do not replace the existing
   KLayout DRC/LVS/PEX checks or copy values from another PDK.

`ICPS2023_5_fd_base/config.tcl` supplies the pad LEF/GDS. The frame generator
keeps canonical ML1 LEF/DEF intact and emits separate LibreLane alias views
where `M1` names the same physical ML1 layer 8/0:

- `libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base_librelane.lef`
- `libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base_librelane.def`

The DEF carries all 28 fixed pad pins; assign actual design nets separately.
The frame GDS remains an overlay at its original origin, not a 1600 µm placeable
macro. The map is limited to the frame plus experimental routing layers; extend
it from the real standard-cell GDS when that library is created.

The derived 2x2 pad library has a separate config at
`ICPS2023_5_fd_base_2x2/config.tcl`. It registers the 60 × 60 µm pad LEF/GDS
views only; a design must separately select
`libs.ref/ICPS2023_5_fd_base_2x2/def/ICPS2023_5_fd_base_2x2_librelane.def` as
`FP_DEF_TEMPLATE` to carry the 3200 µm die and 56 fixed pins.

The experimental overlay must not be used for fabrication, process DRC/LVS/PEX
claims, timing signoff, or mask generation. The current ICPS2023_5 DRC, LVS,
PEX, and canonical layer table are unchanged.
