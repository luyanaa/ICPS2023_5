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
│   ├── kicad/      legacy EEschema symbol library (unmaintained)
│   └── librelane/  (placeholder - no standard cell library yet)
├── libs.ref/
│   ├── ICPS2023_5_fd_base/   frame overlay + pad views: gds/, lef/, def/, lib/
│   ├── ICPS2023_5_fd_pr/     contacts, vias and pad primitives: gds/
│   └── doc/                  PDK documents (PDF)
├── Samples/        design contest template and the NAND tutorial sample
├── grain.xml       KLayout (salt) package descriptor
└── SOURCES         provenance of every artefact in this repository
```

## Documentation

* `libs.ref/doc/PDK_minimalFabSOICMOS.pdf` — device/process explanation, device
  characteristics, design manual, SPICE model, PCell and DRC/LVS rules.
* `libs.ref/doc/PDK_reference_manual.pdf` — Japanese reference manual (process,
  device table, layout rules, electrical data).
* `libs.tech/klayout/doc/layers.md` — the canonical layer table (design and
  fab numbering) and the history of the numbering mess this PDK used to have.
* `libs.tech/ngspice/README.md`, `libs.tech/xschem/README.md`,
  `libs.tech/klayout/{drc,lvs,pex}/README.md` — how to run each tool flow.
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
`MinedaCommon` runtime. KLayout and LibreLane come from the LibreLane nix shell
(`~/Documents/librelane`); ngspice is used directly.

```bash
# DRC (design numbering; add -rd layer_mode=fab for foundry-numbered data)
klayout -b -r libs.tech/klayout/drc/icps2023_5.drc \
        -rd input=design.gds -rd report=drc.lyrdb \
        -rd process_profile=icps2023_5_1m

# design -> foundry mask numbering
klayout -b -r libs.tech/klayout/drc/icps2023_5_fab_prep.drc \
        -rd input=design.gds -rd output=fab.gds \
        -rd process_profile=icps2023_5_1m

# LVS against a schematic netlist
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
        -rd input=design.gds -rd schematic=design.cir -rd output=extracted.cir \
        -rd process_profile=icps2023_5_1m

# parasitic extraction (R network + lumped C)
nix-shell ~/Documents/librelane --run \
  'cd "$PDK_ROOT/ICPS2023_5" && python3 libs.tech/klayout/pex/icps2023_5_pex.py --input design.gds --top CELL --process-profile icps2023_5_1m'
```

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

## Verification status

| Check | Result |
|-------|--------|
| Default 1M DRC on shipped NAND sample | Rejected at preflight: unmasked VIA1/ML2 are outside the default profile |
| Experimental DRC on shipped NAND GDS | 0 markers in design mode with `strict=false`; compatibility smoke only |
| Strict 1M DRC on shipped samples | Same preflight rejection; earlier marker counts are historical, not current default-profile evidence |
| Profile-specific DRC/PCell checks | `libs.tech/klayout/test_process_profiles.py` exercises the 1M and experimental gates |
| Experimental fab prep + fab-mode DRC on NAND GDS | 0 markers with explicit experimental profile and `strict=false`; compatibility smoke only |
| NAND legacy fab-prep/LVS compatibility | Requires explicit experimental profile; compatibility does not qualify VIA1/ML2 |
| LVS `nand_sample_b4soi_reference.cir` compatibility smoke | MATCH, 0 mismatches with `filters=legacy` and explicit experimental profile in design and fab modes |
| LVS strict compare on NAND reference | NO MATCH under explicit experimental process profile; unmodelled pad-array parasitics remain in scope |
| PEX on shipped NAND GDS | Rejected in either profile for unmasked upper-metal geometry; no extraction output |
| PEX regression fixtures | 12 tests cover sheet/contact R, MOS/RES/CAP/DIO ownership, physical-port mapping, distributed passive C, masks, fab labels, and unqualified-coupling omission |
| Layered FEM matrix fixtures | `libs.tech/klayout/pex/test_fieldsolve_box.py` checks matrix symmetry, passivity, charge conservation, material sensitivity, and layer-boundary validation; test permittivities are synthetic |
| Pad LEF/DEF consistency | 28 fixed independent DEF pins match the frame GDS; LEF/Liberty remain physical-only |
| ngspice 47 ESD wrapper smoke | Both wrappers run with common `SUB`; expected zero-perimeter and RDSW warnings are nonfatal |
| ngspice 47 two-input NAND transient | Finite-slew states `00/10/01/11` at 1/6/14/21 ns: 3.000/3.019/3.012/0.121 V (the low state was still falling) |
| xschem symbol validator | 45 symbols validated; ESD pin order checked against ngspice `.subckt` definitions |

## Known limits

* **Model accuracy.** The active cards are a stage-1 BSIMSOI v4.4 fit to the
  local PDK PDF, not a bulk-card conversion or a silicon-calibrated model.
  Output-curve relative RMS residuals are 1.8–14.5%; transfer-curve
  log-current RMS is 0.17–0.69 decades. The PMOS transfer fit is poor. The
  fit covers only L=6 µm, W=24 µm at 25 °C and does not characterize body,
  back-gate, C–V, temperature, or geometry scaling. See
  `libs.tech/ngspice/README.md` and `libs.tech/ngspice/fit/`.
* **No automated CI.** The repository has no workflow configuration; PEX, LVS,
  pad-view, and xschem-contract checks are documented local commands, not PR
  gates.
* **ML2 / VIA2 / ML3 are not qualified.** The reference manual marks them
  T.B.D. Experimental DRC and fab prep permit only VIA1/ML2; VIA2/ML3 are
  rejected because their connectivity and mask handling are undefined. PEX
  rejects all upper-metal geometry.
* **DRC document limits.** Strict checks remain opt-in. The shipped NAND sample
  is rejected before rule evaluation by the default 1M profile due to unmasked
  VIA1/ML2, so earlier strict-marker counts are not current default-profile
  evidence. See `libs.tech/klayout/drc/README.md` for per-rule limits.
* **LVS scope.** Strict resistor/capacitor comparison is the default. Explicit
  `-rd filters=legacy` enables the old `max_res`/`min_caps` compatibility
  behavior; even strict LVS cannot check layout substrate/backgate connectivity
  because this PDK has no substrate/BOX geometry. See
  `libs.tech/klayout/lvs/README.md`.
* **PEX limits.** Routine PEX distributes each connected component's existing
  qualified diffusion capacitance evenly over its resistance-network nodes;
  it does not consume field-solver results or model layout-driven fringing,
  sidewall capacitance, or full distributed RC. Research-only FEM studies include
  neither qualifies the 2023 stack or provides PEX coefficients. The layered
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
