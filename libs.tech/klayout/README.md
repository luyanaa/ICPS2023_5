# libs.tech/klayout

KLayout side of the ICPS2023_5 PDK: the technology definition, the layer
properties, the verification/extraction decks, the device PCells, the utility
macros and the process cross sections.

```
tech.lyt                      KLayout technology (dbu 0.001, design layer set)
icps2023_5.lyp                layer properties, design numbering
icps2023_5_fab.lyp            layer properties, foundry mask numbering
drc/  icps2023_5.drc          design rule check (design|fab numbering)
      icps2023_5_fab_prep.drc design -> foundry mask numbering
lvs/  icps2023_5.lvs          layout vs schematic
pex/  icps2023_5_pex.py       parasitic extraction (R network + lumped C)
pcells/                       native RBA device PCells (no AnagixLoader)
process_profiles.json        shared 1M and experimental profile definitions
process_profile.rb           shared KLayout profile and upper-layer guards
macros/                       utility macros from the original package
xs/                           process cross sections and their colour maps
doc/layers.md                 canonical layer table + numbering history
doc/OR_to_ICPS.txt            legacy OpenRule1um -> ICPS layer map
```

## Decks

All decks are plain KLayout DRC/LVS DSL or Python, runnable headless:

```bash
# DRC -- report is written as a KLayout marker database
klayout -b -r drc/icps2023_5.drc -rd input=design.gds -rd report=drc.lyrdb
klayout -b -r drc/icps2023_5.drc -rd input=fab.gds -rd layer_mode=fab -rd report=drc.lyrdb
klayout -b -r drc/icps2023_5.drc -rd input=design.gds -rd strict=true

# foundry mask preparation
klayout -b -r drc/icps2023_5_fab_prep.drc -rd input=design.gds -rd output=fab.gds

# LVS
klayout -b -r lvs/icps2023_5.lvs -rd input=design.gds -rd schematic=design.cir \
        -rd output=extracted.cir -rd report=lvs.lyrdb

# PEX
python3 pex/icps2023_5_pex.py --input design.gds --top CELL --output pex.spice \
        [--process-profile icps2023_5_multimetal_experimental]
```

Common options: `-rd top=CELL` for files with several top cells (library GDS
files need it), `-rd layer_mode=design|fab` for the two number spaces,
`-rd process_profile=NAME` to select a profile for DRC, fab prep, or LVS, and
`-rd report=`/`-rd output=` to place the outputs.

## Process profiles

The default `icps2023_5_1m` rejects unmasked VIA1/ML2/VIA2/ML3 geometry in DRC,
fab prep, and LVS. PCell `with_via` defaults are false. The
`icps2023_5_multimetal_experimental` profile permits VIA1/ML2 PCell geometry,
enables provisional DRC rules for those layers, and maps only VIA1/ML2 in fab
prep. Unmasked VIA2/ML3 remains rejected. Experimental LVS covers VIA1/ML2
only; PEX rejects all upper-metal geometry because it has no qualified
extraction model. Experimental does not mean foundry-qualified.

Batch profile precedence is an explicit `-rd process_profile=NAME` for DRC,
fab-prep, and LVS (or `--process-profile NAME` for PEX), then
`ICPS2023_5_PROCESS_PROFILE`, then the JSON default. An explicit command-line
profile overrides an inherited environment variable; without either, the
configured 1M default is used. KLayout PCells read the environment variable or
the JSON default at library load.

## Comparison with the legacy KLayout package

The legacy package was driven from the KLayout GUI through AnagixLoader
(`MinedaCommon::MinedaInput`, `.yaml` reference files, spreadsheet export,
symlinks under `lvs_work/`). Those wrappers are gone: each deck now takes its
inputs and outputs from the command line, prints a banner, and fails with an
actionable message instead of writing into the design directory.

`pcells/pcells_v0.1.lym` declares the nine active device cells directly with
KLayout's RBA PCell API. It runs in batch mode without AnagixLoader, the
`MinedaPCell` base classes, or the `ICPS2023_5_Basic` library.

The library registers nine active PCell types and documents five optional
variants for Nch body ties, p-substrate contacts, two fingers, Pch n-substrate
contacts, and an N-well P-diffusion resistor. The selected process profile
controls whether optional VIA1/ML2 geometry is enabled. Utility scripts under
`macros/` remain GUI macros; only document paths inside `macros/Documents/`
were updated.

The PCells read `process_profiles.json` through `process_profile.rb`; keep both
files alongside the macro when installing the technology.

Run `python3 test_process_profiles.py` inside the KLayout environment to verify
PCell defaults, upper-layer rejection, and profile-specific DRC/fab-prep/LVS/
PEX behavior.

`python3 pcells/test_native_pcells.py` checks representative mask bounding
boxes for all nine native PCell types, a multi-finger MOS routing variant,
and the default profile's upper-metal exclusion. The original Anagix PCell
generator is absent; these native geometry baselines do not claim legacy
equivalence.

`python3 pcells/test_1m_golden.py` generates temporary GDS from native PCells:
minimum 13 um NMOS/PMOS, inverter, NAND2, N+/TiN bridges, and 3-/5-stage ring
oscillator cores. NAND2 and each ring stage expose separate local supply pins
for parent-level `VDD`/`VSS` binding; no cross-layer route is assumed. The test runs default 1M DRC,
strict LVS, and 1M PEX for each design, checks for zero DRC markers and no
unqualified upper-metal geometry, and validates the emitted physical-terminal
`PORT_MAP`.

`lvs/test_lvs_contract.py` also has a hand-drawn strict-LVS contract fixture.
It is explicitly logic-only: its 10 um MOS width and 16 um resistor length are
below the native PCell minima, so it is not a 1M DRC-clean golden layout.

## Independent Pad/IO/ESD evidence gate

`libs.tech/stdcell/io_dfm.py` checks actual PAD-opening containment in ML1,
process-profile upper-layer exclusion, explicit pad-function assignments, and
exact design-to-fab PAD/ML1 geometry after MASK/EXCLUDE subtraction. Design
PAD `50/0` maps to fab PAD `10/0`; fab `10/0` must not be mistaken for design
ML2. The checker never generates openings or infers pad functions from labels.

Run from the repository root in the supported KLayout environment:

```bash
env NIX_BUILD_SHELL=/bin/bash nix-shell ../librelane --run \
  'python3 libs.tech/stdcell/io_dfm.py --mode manufacturing --out /tmp/io_dfm.json'
```

Structural inspection and manufacturing acceptance are separate. Structural
mode exits `0` only for complete, passing geometry, otherwise `1`;
manufacturing mode exits `3` and reports `BLOCKED` because official opening
dimensions/enclosures, die-edge/seal-ring clearances, bond/probe current limits,
and ESD qualification are unavailable. The existing 28-pad frame has no PAD
opening polygons and therefore reports structural `INCOMPLETE`. Its 60 µm ML1
pads and configured keepouts are engineering reservations, not process rules.
An explicit ESD-device declaration is not HBM/CDM/TLP or clamp qualification.

Pass `--gds <design.gds> --fab-gds <actual_fab.gds>` to compare an actual
fab-prep result, and `--pad-mapping <JSON_FILE>` for explicit physical-pad
assignments. The Users' Manual v1.05's struck-through PAD70/PAD100 entries do
not establish current pad rules. Its recommendation to use N-diffusion power
bridges also means existing experimental TiN power risers remain an open
manufacturing constraint; SOI-edge protection does not qualify that PDN.

## Foundry-mask (mask-first) status

The flow artifacts are mask-numbered. `drc/icps2023_5_fab_prep.drc` is the only
place drawing layers are interpreted; nothing downstream converts a layout.

* The shipped frames (`libs.ref/ICPS2023_5_fd_base*`) and the NAND sample
  (`Samples/Semicon2023/NAND/nand_sample.GDS`) are mask-numbered in place and
  keep their structure. Frame conversion uses
  `drc/make_mask_view.py`, which preserves the cell hierarchy (the LEF/DEF
  views are emitted per macro) and refuses to write a view that differs from a
  real fab-prep conversion of the same input.
* DRC/LVS/PEX run `layer_mode=fab` on those files directly
  (`drc/test_foundry_migration.py`, `lvs/test_lvs_contract.py`). The
  mask-numbered NAND reproduces the reference netlist: 16 NCH (L=10/W=10) +
  16 PCH (L=10/W=40), `MATCH (mismatches=0)` with the documented legacy
  compatibility filters; strict comparison stays `NO MATCH` because of the
  unmodelled pad parasitics.
* The three conversion derivations that change manufacturing geometry - the
  derived doping windows, the whole-polygon TiN bias and the N-ch mask source
  (`legacy_pwell`) - are pinned by `drc/test_conversion_semantics.py`.
* Native PCell MOS: drawing TiN is `L`, the final mask is `L+4` um, the physical
  dopant window is `L`, and both LVS modes extract `L`
  (`lvs/test_channel_length_pipeline.py`).
* Fab-mode body ties are identified physically - a channel in an N-ch body-dope
  component that reaches a contacted P+ dot; `TIN_BLK` has no fab mask
  (`lvs/test_fab_body_tie.py`).
* A hand-built foundry reference cell, its equality with the fab-prep conversion
  of the native PCell, fab-mode PEX, and post-layout assembly bound to the
  nominal card (ngspice smoke) and to the ported FD/unified bridge cards, both
  run on Xyce 7.10 (`lvs/test_fab_reference.py`).

Two drawing-numbered inputs remain: the vendor template
`Samples/Semicon2023/base_contest2023.GDS` is the frame generators' authoring
source, and the salt-package `ICPS2023_5_Basic.gds` (upstream) is the
authoring original of the now flat, mask-numbered primitive library
`libs.ref/ICPS2023_5_fd_pr/gds/`. That library was converted with
`make_mask_view.py --flat-library`: fab prep flattens, so its internal `DCNT`
child is gone and its ten primitive names survive as flattened tops. The DM_*
dummy-fill marks (101/0-105/0) have no mask mapping in the original conversion
deck either, and the original DRC's only dummy rule is commented out.

## Layer numbering

See `doc/layers.md`. In short: design numbering is canonical for everything in
this repository, `layer_mode=fab` switches the decks to the foundry mask
numbers, and `drc/icps2023_5_fab_prep.drc` converts between them.
