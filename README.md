# ICPS2023_5

PDK for the **minimal Fab SOI-CMOS** process (AIST ACPS) — a 1-metal,
gate-last SOI CMOS process with 3 V MOSFETs, diffusion resistors/capacitors,
TiN capacitors, diodes and a 1600 × 1600 µm frame with 28 pads.

The PDK is organised the way open-source PDKs are: tool-facing configuration in
`libs.tech/`, circuit IP in `libs.ref/`.

```
ICPS2023_5/
├── libs.tech/
│   ├── klayout/    tech.lyt, layer properties, DRC / LVS / PEX decks, PCells, macros, cross sections
│   ├── ngspice/    BSIMSOI v4.4 (LEVEL=58) fit, device subcircuits and fit data
│   ├── xschem/     symbol library + xschemrc
│   ├── librelane/  experimental 1M/2M LibreLane technology, PDK/SCL contracts and design fragments
│   ├── kicad/      legacy EEschema symbol library (unmaintained)
│   └── ...
├── libs.ref/
│   ├── ICPS2023_5_fd_base/      frame overlay + pad views: gds/, lef/, def/, lib/
│   ├── ICPS2023_5_fd_base_2x2/  same views for the derived 3200 × 3200 µm 2x2 die option
│   ├── ICPS2023_5_fd_pr/        contacts, vias and pad primitives: gds/
│   └── doc/                     PDK documents (PDF)
├── Samples/        design contest template and the NAND tutorial sample
├── grain.xml       KLayout (salt) package descriptor
└── SOURCES         provenance of every artefact in this repository
```

The LibreLane overlay is user-authorized experimental collateral only. It does
not qualify POLY-Si or the specified narrow TiN routes, and does not override
the DRC/LVS/PEX layer contracts. The generated standard-cell output directories
were cleared for redesign: there is no currently published standard-cell
GDS/LEF/SPICE/Liberty/characterization package. The retained generator and
development sources do not make a qualified library or full PnR flow; see
`libs.tech/librelane/README.md` for the overlay boundary.

## Documentation

* `libs.ref/doc/PDK_minimalFabSOICMOS.pdf` — device/process explanation, device
  characteristics, design manual, SPICE model, PCell and DRC/LVS rules.
* `libs.ref/doc/PDK_reference_manual.pdf` — Japanese reference manual (process,
  device table, layout rules, electrical data).
* `libs.tech/klayout/doc/layers.md` — the canonical layer table (design and
  fab numbering) and the history of the numbering mess this PDK used to have.
* `libs.tech/ngspice/README.md`, `libs.tech/xschem/README.md`,
  `libs.tech/klayout/{drc,lvs,pex}/README.md`, and
  `libs.tech/librelane/README.md` — how to run each tool flow and the boundaries
  of the experimental LibreLane overlay.
* PDK user manual (Dropbox, maintained by the foundry):
  <https://www.dropbox.com/scl/fi/84uzdzux0urhsd05mfxmb/ICPS2023_5-PDK-Users-Manual-v1.05.paper?rlkey=04jeibh2hrzlrlx4x9h5ghp4s&dl=0>

## Installing

The KLayout PCells use native RBA APIs and do not require AnagixLoader. Clone
this repository below KLayout's salt directory:

```bash
mkdir -p ~/.klayout/salt && cd ~/.klayout/salt
git clone https://github.com/luyanaa/ICPS2023_5.git
```

Point the standard command-line tools at the repository root:

```bash
export PDK_ROOT=$(pwd)         # directory containing ICPS2023_5/
export PDK=ICPS2023_5
```

## Process profiles

`libs.tech/klayout/process_profiles.json` defines the process profiles. The
default `icps2023_5_1m` keeps the flow within the qualified single-metal
process: MOS and resistor PCells default `with_via` to false, reject an explicit
VIA1/ML2 request, and DRC, fab-prep, and LVS reject unmasked upper-layer
geometry. Default DRC omits provisional upper-metal rules.

`icps2023_5_multimetal_experimental` permits VIA1/ML2 PCell geometry, enables
provisional DRC rules for those layers, and maps only VIA1/ML2 in fab prep.
Unmasked VIA2/ML3 remains rejected because no LVS connectivity is defined and
fab prep does not emit them. This profile does not confer foundry qualification.
LVS has VIA1/ML2 connectivity; PEX rejects all upper-metal geometry in either
profile because no qualified upper-metal resistance or capacitance model is
available.

Batch resolution precedence is explicit `-rd process_profile=NAME` (KLayout)
or `--process-profile NAME` (PEX), then
`ICPS2023_5_PROCESS_PROFILE`, then the JSON default. KLayout PCells use the
environment variable or JSON default because they load outside a batch deck.
Thus an exported experimental profile also affects batch tools when no
command-line profile is supplied. To select the experimental PCell defaults
before starting KLayout:

```bash
export ICPS2023_5_PROCESS_PROFILE=icps2023_5_multimetal_experimental
```

For reproducible batch runs, pass the profile explicitly; `-rd` and
`--process-profile` override an inherited environment setting. DRC, fab-prep,
LVS, and PEX report the resolved profile. `MASK` and `EXCLUDE` geometry retains
its existing exclusion behavior.

## Flows

All KLayout decks are plain, headless DRC/LVS DSL — no external Ruby gems, no
`MinedaCommon` runtime. The root Nix flake pins the non-LibreLane verification
toolchain (Nix-EDA 6.11.0; KLayout 0.30.7, Python 3.13.9, ngspice 44, and Ruby
3.3.10); enter it with `nix develop`. CI runs the non-LibreLane regressions.
The LibreLane overlay is not a qualified integration flow; standard-cell
collateral is being redesigned, as documented in
`libs.tech/librelane/README.md`.

```bash
# Native PCell/designer GDS: run design-numbered decks explicitly.
klayout -b -r libs.tech/klayout/drc/icps2023_5.drc \
        -rd input=design.gds -rd layer_mode=design -rd report=drc.lyrdb \
        -rd process_profile=icps2023_5_1m

# Fab-prep is a one-way conversion for a native design-numbered input.
klayout -b -r libs.tech/klayout/drc/icps2023_5_fab_prep.drc \
        -rd input=design.gds -rd output=fab.gds \
        -rd process_profile=icps2023_5_1m

# Already fab-numbered GDS: check it directly; do not run fab-prep again.
klayout -b -r libs.tech/klayout/drc/icps2023_5.drc \
        -rd input=fab.gds -rd layer_mode=fab -rd report=fab_drc.lyrdb \
        -rd process_profile=icps2023_5_1m

# LVS against a schematic: select the mode that matches the input GDS.
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
        -rd input=design.gds -rd layer_mode=design \
        -rd schematic=design.cir -rd output=design_extracted.cir \
        -rd process_profile=icps2023_5_1m
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
        -rd input=fab.gds -rd layer_mode=fab \
        -rd schematic=design.cir -rd output=fab_extracted.cir \
        -rd process_profile=icps2023_5_1m

# PEX: select the mode that matches the input GDS.
nix develop --command python3 \
  libs.tech/klayout/pex/icps2023_5_pex.py \
  --input design.gds --top CELL --layer-mode design \
  --process-profile icps2023_5_1m
nix develop --command python3 \
  libs.tech/klayout/pex/icps2023_5_pex.py \
  --input fab.gds --top CELL --layer-mode fab \
  --process-profile icps2023_5_1m
```

The `layer_mode`/`--layer-mode` setting selects how a deck interprets layer
numbers; it does not convert the GDS. DRC, LVS, and PEX retain their design-mode
defaults for compatibility, but examples select the mode explicitly. Fab-prep
takes a native design-numbered layout and writes a foundry-mask-numbered view.
The committed frame, primitive, and NAND sample GDS files listed below are
already fab-numbered; use `fab` mode directly and never reconvert them.

| Input GDS | Numbering and use |
|-----------|-------------------|
| Native layouts authored with the KLayout PCells (including a fresh PCell GDS) | `design`; use design-mode DRC/LVS/PEX, then run fab-prep once if producing a mask view |
| `libs.ref/ICPS2023_5_fd_base/gds/ICPS2023_5_fd_base.gds` and `libs.ref/ICPS2023_5_fd_base_2x2/gds/ICPS2023_5_fd_base_2x2.gds` | `fab`; run fab-mode decks directly; do not reconvert |
| `libs.ref/ICPS2023_5_fd_pr/gds/ICPS2023_5_fd_pr.gds` | `fab`; flattened primitive mask view; run fab-mode decks directly; do not reconvert |
| `Samples/Semicon2023/NAND/nand_sample.GDS` | `fab`; run fab-mode decks directly; do not reconvert |
| `Samples/Semicon2023/base_contest2023.GDS` | Drawing-numbered authoring source; use design mode when checking the source, not as an already prepared mask view |

Fab-prep changes geometry as well as layer numbering; its derivations and
regressions are described in `libs.tech/klayout/drc/README.md` and
`libs.tech/klayout/drc/test_conversion_semantics.py`.

SPICE:

```spice
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b4soi
.options temp=25 tnom=25
M1 d g s SUB nch w=24u l=6u
```

Schematic capture: add `libs.tech/xschem/symbols` to the xschem symbol path (or
source `libs.tech/xschem/xschemrc`).

## Devices

| Device | SPICE | Notes |
|--------|-------|-------|
| NMOS / PMOS | `nch`, `pch` | BSIMSOI v4.4, LEVEL=58, D/G/S/E; stage-1 fit at L=6 µm, W=24 µm |
| N+/P+ diffusion resistors | `r_ndiff` (180 Ω/sq), `r_pdiff` (1.2 kΩ/sq) | |
| SOI film resistor | `r_soi` (250 MΩ/sq) | legacy LVS approximation, not PEX data |
| TiN / Al resistors | `r_tin` (20 Ω/sq), `r_ml1` (0.2 Ω/sq) | local interconnect |
| Diffusion capacitors | `cap_ndiff`, `cap_pdiff` (2.3 fF/µm²) | gate oxide over diffusion |
| TiN / MIM capacitors | `cap_tin`, `mim_cap` (0.115 fF/µm²) | legacy LVS assumption; not a qualified PDK measurement |
| Diodes, ESD devices | `dio_ndio`, `dio_pdio`, `nmos_esd`, `pmos_esd` | ESD pins: `PAD VSS SUB` / `PAD VDD SUB`; common `SUB` |
| Local wiring | `bridge` (TiN), `nbridge` (N+ diffusion) | |
| Pads / frame | `libs.ref/ICPS2023_5_fd_base` | 1600 × 1600 µm frame, 28 × 60 µm pads |
| Pads / frame (2x2) | `libs.ref/ICPS2023_5_fd_base_2x2` | derived 3200 × 3200 µm frame, 56 × 60 µm pads; the foundry ships no 2x2 GDS |

Ordinary PMOS3 remains supported. The `Pch` `experimental_nplus_island` PCell
option is retained only as experimental separate N+ island geometry; it does
not establish a PMOS body tie or a fourth electrical terminal. PMOS4 is excluded from the
LVS/PEX device sets. NMOS4 extraction remains experimental and LVS-only.

## Standard-cell redesign status and historical evidence

The published output directories under `libs.ref/ICPS2023_5_gen_sc/`
(`gds/`, `lef/`, `liberty/`, `spice/`, `characterization/`, and `doc/`) now
contain only `.gitkeep`; `tools/make_gen_sc.py` remains as source. There is no
current standard-cell GDS, LEF, SPICE, Liberty, manifest, or characterization
package. Prior generation, characterization, ALU, routing, and cell-area results
describe historical experiments only; they are not current published views or
qualification evidence. The verification table below documents the guarded
publication checks and their NOT RUN status when the manifest is absent.

The nominal PDK model remains the stage-1 `SOIMOD=0` BSIMSOI v4.4 card
(`LEVEL=58`); the bridge candidates did not replace it. The separate
`SOIMOD=2` ideal-FD and `SOIMOD=1` unified bridge candidates are experimental
and version-scoped to Xyce 7.10 / BSIMSOI 4.5.0. The candidate fit evidence
uses W=24 µm, L=6 µm, 3 V, 25 °C, four-terminal D/G/S/E; individual transient
audits have their own conditions. They do not establish the physical depletion
mode of either polarity, constitute silicon calibration, or provide PVT or
timing signoff evidence.

Historical FD-bridge simulation measurements and their definitions remain in
the model audit reports:

* [`b4soi_fd_bridge_timing.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_timing.md)
  records FO1/FO4 transients at 2 ns and 10 ns input slew across the report's
  2.5/3.0/3.5 V cases; unresolved loaded cases are not timing data.
* [`b4soi_fd_bridge_sweep_ro.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_sweep_ro.md)
  records 5/11/21-stage ring-oscillator simulations (about 11.7/5.3/2.8 MHz)
  and about 8.6 ns/stage for the recorded closed-loop case.
* [`b4soi_fd_bridge_cin_definitions.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_cin_definitions.md)
  distinguishes the edge-start dynamic input-capacitance result (2258.3 fF)
  from the historical 2636 fF half-crossing pairing. `C_eff_energy` (previously
  reported as 2377 fF) remains frozen pending re-integration.

These are version-limited simulations, not silicon measurements, a nominal
model replacement, a physical FD/PD decision, a qualified standard-cell
library, or a signoff claim.

## Verification status

| Check | Current scope |
|-------|---------------|
| Input numbering | Native PCell/designer GDS is `design`; fab-prep output and the named frame, primitive, and NAND GDS files above are already `fab`. DRC/LVS/PEX mode defaults remain `design` for compatibility. |
| Foundry migration | `libs.tech/klayout/drc/test_foundry_migration.py` checks the 1x1/2x2 frame mask layers and hierarchy, the flattened primitive mask layer set, and the NAND sample's mask-numbering fingerprint. It runs migrated views in fab mode; it does not reconvert them. |
| Fab-prep semantics | `libs.tech/klayout/drc/test_conversion_semantics.py` pins derived doping windows, whole-connected-polygon TiN bias, and explicit NCH output versus opt-in legacy PWell generation. These geometry checks are not foundry qualification. |
| SOI conductor partition | `libs.tech/klayout/lvs/test_soi_partition.py` checks actual LVS/PEX/ERC networks in both input modes: ordinary MOS S/D isolation, nonohmic low-doped necks and PN junctions, rejected ambiguous CNT landings, and preserved same-polarity diffusion. ERC requires explicit `layer_mode`; geometric SOI continuity is not an ideal conductor. |
| NAND sample DRC | `Samples/Semicon2023/NAND/nand_sample.GDS` is fab-numbered. Run `layer_mode=fab`; the experimental-profile migration check expects only the `TIN.SOIEDGE` screen. The sample's unqualified VIA1/ML2 geometry remains outside default 1M acceptance. |
| NAND sample LVS | `libs.tech/klayout/lvs/test_lvs_contract.py` exercises the already-fab-numbered sample in fab mode. The explicit experimental-profile/`filters=legacy` compatibility comparison matches; strict comparison remains NO MATCH because pad-array parasitics are unmodelled. This is compatibility evidence, not foundry qualification. |
| NAND sample PEX | Use `--layer-mode fab`; PEX rejects its unqualified upper-metal geometry in either profile and emits no extraction output. |
| PEX and FEM regressions | `libs.tech/klayout/pex/test_pex.py` covers extraction ownership and supported parasitics. `test_fieldsolve_box.py` covers numerical matrix properties with synthetic permittivities; it supplies no qualified process coefficients. |
| Pad/frame views | `libs.ref/ICPS2023_5_fd_base/tools/test_pad_views.py` and `libs.ref/ICPS2023_5_fd_base_2x2/tools/test_pad_views_2x2.py` cover physical pad views and their frame contracts. The 2x2 frame is derived; no foundry-published 2x2 GDS exists. |
| Frame measurement | `libs.tech/stdcell/test_frame.py` measures the foundry-numbered 1x1 (28-pad) and derived 2x2 (56-pad) frames with an explicit `layer_mode="fab"`; drawing fixtures stay `design`, and the configs declare the mode. Missing or wrong-mode input fails closed instead of inferring a layer. |
| Capacitance ownership | The single-owner matrix (`BSIMSOI` / LVS device / PEX) is in `libs.tech/klayout/pex/README.md`. The BOX diffusion return is `BOX_RETURN_ANALYTICALLY_SUPPORTED / MEASUREMENT_UNCONFIRMED` (`audit_box_capacitance.md`); implementation ownership is not a measured deembedding. |
| Contact `RDSW`/`RCNT` | `libs.tech/ngspice/tools/audit_mos_contact_ownership.py` re-evaluates the frozen digitized curves for bare, explicit-`RCNT`, and full post-layout networks on the nominal card; every report is `RDSW_RCNT_SEPARATION_UNCALIBRATED` (`audit/mos_contact_ownership.md`). Kelvin/TLM data is still required. |
| Standard-cell output state | The six published output directories under `libs.ref/ICPS2023_5_gen_sc/` contain only `.gitkeep`; see the redesign and historical-evidence section above. Source-level geometry regressions do not publish the withdrawn library views. |
| CI and publication boundary | `.github/workflows/ci.yml` runs the six migration/SOI suites unconditionally (`test_fab_body_tie`, `test_pmos4_mask_contract`, `test_channel_length_pipeline`, `test_conversion_semantics`, `test_soi_edge_protection`, and `test_foundry_migration`), plus cross-engine partition, NCH mask audit, ERC, and assembly regressions. `test_stdcell.py` and `test_frame.py` remain unconditional; generated-library inverter simulation runs, while the absent published-library simulation is explicitly skipped. `make_gen_sc.py --check`, `test_library.py`, `test_mapping_views.py`, `test_alu.py`, and `array_acceptance.py` run only when `cell_manifest.json` exists; otherwise publication-dependent checks are NOT RUN. This is not a complete standard-cell publication or CI pass. |
| Runtime boundary | The observed local `nix-shell ../librelane/shell.nix` runtime has KLayout 0.30.7, Python 3.13.9, and ngspice 47; pinned CI uses ngspice 44. Local ngspice evidence is not a pinned-CI result. |
| LibreLane/OpenROAD | The current partial SCL/configuration is not a qualified PnR flow; no placement, routing, or signoff claim follows from the experimental overlay. |

## Known limits

* **Model accuracy.** The nominal stage-1 BSIMSOI v4.4 fit is to the local PDK
  PDF, not a bulk-card conversion or a silicon-calibrated model. Output-curve
  relative RMS residuals are 1.8–14.5%; transfer-curve log-current RMS is
  0.17–0.69 decades, with a poor PMOS transfer fit. The nominal fit covers only
  L=6 µm, W=24 µm at 25 °C and does not characterize body, back-gate, C–V,
  temperature, or geometry scaling. The separate version-scoped bridge
  experiments are described above. See `libs.tech/ngspice/README.md` and
  `libs.tech/ngspice/fit/`.
* **Automated CI.** The workflow and its conditional standard-cell publication
  checks are described in the verification table above. An absent generated
  manifest means publication-dependent checks are not run; this is not a
  complete publication or CI pass.
* **ML2 / VIA2 / ML3 are not qualified.** The reference manual marks them
  T.B.D. Experimental DRC and fab prep permit only VIA1/ML2; VIA2/ML3 are
  rejected because their connectivity and mask handling are undefined. PEX
  rejects all upper-metal geometry.
* **DRC document limits.** Strict checks remain opt-in. The NAND sample GDS is
  already fab-numbered, so check it with `layer_mode=fab`; its unqualified
  VIA1/ML2 geometry is rejected by the default 1M profile before rule
  evaluation. Experimental-profile `TIN.SOIEDGE` markers are a design-practice
  screen, not a process limit. See `libs.tech/klayout/drc/README.md`.
* **LVS scope.** Strict resistor/capacitor comparison is the default. Explicit
  `-rd filters=legacy` enables the old `max_res`/`min_caps` compatibility
  behavior; even strict LVS cannot check layout substrate/backgate connectivity
  because this PDK has no substrate/BOX geometry. See
  `libs.tech/klayout/lvs/README.md`.
* **PEX limits.** Routine PEX allocates reported diffusion capacitance by
  bounded Voronoi area to resistance-network nodes on the same diffusion
  layer; it does not consume field-solver results or model layout-driven fringing,
  sidewall capacitance, or full distributed RC. Research-only FEM studies
  neither qualify the 2023 stack nor provide PEX coefficients. The layered
  solver has no embedded 2023 stack or dielectric constants. MOS channel
  geometry is excluded from interconnect PEX; intrinsic channel charge remains
  with the BSIMSOI device model. The PDK does not identify the return conductor
  for its N/P diffusion-capacitance density; mapping it to `SUB` is an explicit
  assumption, not measured BOX coupling. `CNT` is a generic untyped mask and
  PEX partitions contacts using a documented geometric priority. See
  `libs.tech/klayout/pex/README.md`.

## Tutorials from the community

* Leo Moser, *Design of a NAND gate using the ICPS PDK*:
  <https://mole99.uber.space/2024/NAND_tutorial/Design%20of%20a%20NAND%20gate%20using%20the%20ICPS%20PDK.html>
* Semicon 2023 minimal Fab design contest:
  <https://github.com/mineda-support/Semicon2023-MinimalFab-Design-Contest>

\***************************************************************************<br>
\* Copyright 2023 minimalFab Promoting Organization<br>
\*<br>
\* Licensed under the Apache License, Version 2.0 (the "License");<br>
\* you may not use this file except in compliance with the License.<br>
\* You may obtain a copy of the License at<br>
\*<br>
\*      http://www.apache.org/licenses/LICENSE-2.0<br>
\*<br>
\* Unless required by applicable law or agreed to in writing, software<br>
\* distributed under the License is distributed on an "AS IS" BASIS,<br>
\* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.<br>
\* See the License for the specific language governing permissions and<br>
\* limitations under the License<br>

