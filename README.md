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
│   ├── ngspice/    BSIM3SOI models, device subcircuits, corner/variant selector
│   ├── xschem/     symbol library + xschemrc
│   ├── kicad/      legacy EEschema symbol library (unmaintained)
│   └── librelane/  (placeholder - no standard cell library yet)
├── libs.ref/
│   ├── ICPS2023_5_fd_base/   pad ring + frame: gds/, lef/, lib/
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

The KLayout side of the PDK is a KLayout *salt* package: clone this repository
(and `AnagixLoader`, which provides the PCell base classes and the legacy macro
runtime) below KLayout's salt directory:

```bash
mkdir -p ~/.klayout/salt && cd ~/.klayout/salt
git clone https://github.com/mineda-support/AnagixLoader.git
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
python3 libs.tech/klayout/pex/icps2023_5_pex.py --input design.gds --top CELL
```

SPICE:

```spice
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b3soipd
X1 d g s b nch w=20u l=10u
```

Schematic capture: add `libs.tech/xschem/symbols` to the xschem symbol path (or
source `libs.tech/xschem/xschemrc`).

## Devices

| Device | SPICE | Notes |
|--------|-------|-------|
| NMOS / PMOS | `nch`, `pch` | BSIM3SOI (level 57 PD / 55 FD), 4 terminals, L ≥ 6 µm, W ≥ 13 µm |
| N+/P+ diffusion resistors | `r_ndiff` (180 Ω/sq), `r_pdiff` (1.2 kΩ/sq) | |
| SOI film resistor | `r_soi` (250 MΩ/sq) | |
| TiN / Al resistors | `r_tin` (20 Ω/sq), `r_ml1` (0.2 Ω/sq) | local interconnect |
| Diffusion capacitors | `cap_ndiff`, `cap_pdiff` (2.3 fF/µm²) | gate oxide over diffusion |
| TiN capacitor | `cap_tin` (0.115 fF/µm²) | Al over TiN, 300 nm TEOS |
| MIM capacitor | `mim_cap` (0.115 fF/µm²) | ML1 over ML2 |
| Diodes, ESD devices | `dio_ndio`, `dio_pdio`, `nmos_esd`, `pmos_esd` | |
| Local wiring | `bridge` (TiN), `nbridge` (N+ diffusion) | |
| Pads / frame | `libs.ref/ICPS2023_5_fd_base` | 1600 × 1600 µm frame, 28 × 60 µm pads |

## Verification status (this refactor)

| Check | Result |
|-------|--------|
| DRC on `Samples/Semicon2023/base_contest2023.GDS` (fabricated contest template) | 0 markers |
| DRC on `Samples/Semicon2023/NAND/nand_sample.GDS` (fabricated NAND) | 0 markers |
| DRC on `libs.ref/ICPS2023_5_fd_base` (frame + 28 pads) | 0 markers |
| DRC on `libs.ref/ICPS2023_5_fd_pr` primitive cells | 0 markers except 1 known golden-deck marker on each `PAD60/100/150` (see its README) |
| DRC `-rd strict=true` on the contest template | 160 document-limit markers (32 `CNT.m5`, 128 `CNT.m6`) |
| LVS on the NAND sample against its reference netlist | MATCH, 0 mismatches |
| LVS on the fab-numbered NAND (`fab_prep` output, `layer_mode=fab`) | MATCH, 0 mismatches |
| DRC on the fab-numbered NAND (`layer_mode=fab`) | 0 markers |
| PEX on the NAND sample | 1769 R + 95 C elements, 1.2 s |
| ngspice NAND gate from `Samples/Semicon2023/NAND/nand_min.asc` with the PDK library | correct NAND transfer curve (output 3.30 V → 4e-5 V as A sweeps with B high; stays at 3.30 V with B low) |
| xschem symbol library validator | 45 symbols validated against the ngspice library |

## Known limits

* **Model accuracy.** The foundry supplied bulk-BSIM3 (`LEVEL=8`) cards with SOI
  parameters. They have been re-targeted to ngspice's BSIM3SOI (which is what
  the process needs: floating body, BOX, no bulk junction) keeping the fitted
  parameter values. The re-targeted model reproduces Vth (≈0.25 V NMOS) but
  delivers about 35 µA/µm instead of the datasheet's 62.5 µA/µm at
  Vgs = Vds = 3 V. A foundry re-fit on BSIM3SOI is required for quantitative
  analogue work; see `libs.tech/ngspice/README.md`.
* **ML2 / VIA2 / ML3 are not qualified.** The reference manual lists them as
  T.B.D. They stay in the layer table and in every deck as options, marked
  `[TBD]`, because the shipped cells (`TIN_cap`) and the contest templates use
  them.
* **No standard cells, no I/O cells.** There is no synthesis library; the frame
  with its 28 pads is the I/O. `libs.tech/librelane/` is intentionally empty.
* **DRC document limits.** The deck defaults to the limits that the fabricated
  reference layouts and every shipped library cell meet (the golden
  Mineda2022_6 deck's values). The stricter PDK-document limits — contact size
  5 µm, SOI and ML1 enclosure of source/drain contacts 4 µm, TiN/ML1 enclosure
  of gate contacts 2.5 µm — are behind `-rd strict=true`, because the fabricated
  contest template and the shipped `pcont_min` primitive do not meet them. See
  `libs.tech/klayout/drc/README.md` for the per-rule table.
* **LVS compare filters.** The LVS deck filters pad-ring parasitic R/C
  fragments (`max_res`, `min_caps`) so that a schematic without them still
  matches. Values and consequences are documented in
  `libs.tech/klayout/lvs/README.md`.
* **PEX capacitance is lumped** per conductor component (no field solver, no
  fringing). See `libs.tech/klayout/pex/README.md`.

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
