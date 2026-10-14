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
pcells/                       device PCells (require AnagixLoader's MinedaPCell)
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
python3 pex/icps2023_5_pex.py --input design.gds --top CELL --output pex.spice
```

Common options: `-rd top=CELL` for files with several top cells (library GDS
files need it), `-rd layer_mode=design|fab` for the two number spaces, and
`-rd report=`/`-rd output=` to place the outputs.

## Comparison with the legacy KLayout package

The legacy package was driven from the KLayout GUI through AnagixLoader
(`MinedaCommon::MinedaInput`, `.yaml` reference files, spreadsheet export,
symlinks under `lvs_work/`). Those wrappers are gone: each deck now takes its
inputs and outputs from the command line, prints a banner, and fails with an
actionable message instead of writing into the design directory.

The device PCells (`pcells/`) still need AnagixLoader for their base classes
(`MinedaPCell`, `MinedaNch_SOI`, ...); they are the only remaining dependency on
that package. The scripts under `macros/` are KLayout GUI macros and were kept
as-is (only the document paths in `macros/Documents/` were updated).

## Layer numbering

See `doc/layers.md`. In short: design numbering is canonical for everything in
this repository, `layer_mode=fab` switches the decks to the foundry mask
numbers, and `drc/icps2023_5_fab_prep.drc` converts between them.
