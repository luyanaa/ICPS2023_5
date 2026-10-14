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
git clone https://github.com/mineda-support/ICPS2023_5.git
```

Point the standard command-line tools at the repository root:

```bash
export PDK_ROOT=$(pwd)         # directory containing ICPS2023_5/
export PDK=ICPS2023_5
```

## Flows

All KLayout decks are plain, headless DRC/LVS DSL — no external Ruby gems, no
`MinedaCommon` runtime. KLayout and LibreLane come from the LibreLane nix shell
(`~/Documents/librelane`); ngspice is used directly.

```bash
# DRC (design numbering; add -rd layer_mode=fab for foundry-numbered data)
klayout -b -r libs.tech/klayout/drc/icps2023_5.drc \
        -rd input=design.gds -rd report=drc.lyrdb

# design -> foundry mask numbering
klayout -b -r libs.tech/klayout/drc/icps2023_5_fab_prep.drc \
        -rd input=design.gds -rd output=fab.gds

# LVS against a schematic netlist
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
        -rd input=design.gds -rd schematic=design.cir -rd output=extracted.cir

# parasitic extraction (R network + lumped C)
nix-shell ~/Documents/librelane --run \
  'cd "$PDK_ROOT/ICPS2023_5" && python3 libs.tech/klayout/pex/icps2023_5_pex.py --input design.gds --top CELL'
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
| Default DRC on contest and NAND shipped GDS | 0 markers on both |
| Strict DRC on contest / NAND | 160 / 166 contact-enclosure markers; strict remains opt-in |
| Default DRC on 14 generated PCell variants | 0 markers |
| Fab prep + fab-mode DRC on NAND | 0 markers |
| LVS `nand_sample_b4soi_reference.cir` compatibility smoke | MATCH, 0 mismatches with explicit `filters=legacy` in design and fab modes |
| LVS strict default on NAND reference | NO MATCH; unmodelled pad-array parasitics remain in scope |
| PEX design/fab NAND after via extraction | 1088 contact ports, 1264 R nodes, 1260 resistors, 124 capacitors in both modes |
| PEX regression fixtures | 5 tests: analytic sheet/contact values, MASK/EXCLUDE, fab TEXT, Cox ownership and via-merged capacitance |
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
  T.B.D. The PEX deck omits their resistance/capacitance values; DRC and layer
  documentation retain only explicitly marked unqualified entries.
* **No standard cells or characterized I/O cells.** The frame GDS is an
  overlay, with 28 fixed independent I/O ports in `libs.ref/ICPS2023_5_fd_base/def/`;
  `libs.tech/librelane/` remains intentionally empty.
* **DRC document limits.** Strict mode remains opt-in: it reports 160 markers
  on the contest GDS and 166 on the NAND GDS, while default mode reports none.
  The fabricated examples therefore do not meet all strict contact-enclosure
  rules. See `libs.tech/klayout/drc/README.md` for per-rule results.
* **LVS scope.** Strict resistor/capacitor comparison is the default. Explicit
  `-rd filters=legacy` enables the old `max_res`/`min_caps` compatibility
  behavior; even strict LVS cannot check layout substrate/backgate connectivity
  because this PDK has no substrate/BOX geometry. See
  `libs.tech/klayout/lvs/README.md`.
* **PEX limits.** Routine PEX capacitance remains lumped per conductor component;
  it does not consume field-solver results or model layout-driven fringing,
  sidewall capacitance, or distributed RC. A separate research-only scikit-fem
  ideal-BOX coupon is documented in `libs.tech/klayout/pex/README.md`; it does
  not qualify the 2023 process stack or provide PEX coefficients. MOS channel
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
