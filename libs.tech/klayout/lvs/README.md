# ICPS2023_5 LVS

`icps2023_5.lvs` is the single headless KLayout LVS deck for this PDK. It
replaces the two XML DSL decks (`lvs.lylvs` and `lvs_fabrication.lylvs`) and
has no AnagixLoader, MinedaCommon, Ruby gem, spreadsheet, or Windows-path
configuration dependency.

## Invocation

Run KLayout 0.30.7 in batch mode:

```sh
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
  -rd input=layout.gds \
  -rd schematic=reference.cir \
  [-rd top=CELL] [-rd layer_mode=design|fab] \
  [-rd report=out.lyrdb] [-rd output=extracted.cir]
```

`layer_mode` defaults to `design`. If omitted, `report` defaults to
`<input>_lvs.lyrdb` and `output` defaults to `<input>_extracted.cir`. A GDS
with multiple top cells must be invoked with `-rd top=CELL`; the deck reports
the available top-cell names when this is missing. `schematic` is required and
must point to a readable SPICE-format netlist.

The deck reads four-terminal schematic `M` devices with a small
`RBA::NetlistSpiceReaderDelegate`: it keeps source, gate, and drain, converts
`W`/`L` from SI units to micrometres, and thereby compares against the extracted
three-terminal `mos3` devices. The extracted `NMOS4` remains a four-terminal
body-tie device and uses the design `NCH`/fab `N_CH` path as terminal `W`.

## Extracted devices

| Class | Device | Value |
|---|---|---:|
| `NMOS` | three-terminal nMOS | `L`, `W` geometry |
| `PMOS` | three-terminal pMOS | `L`, `W` geometry |
| `NMOS4` | body-tied nMOS (`pwl`/NCH terminal) | `L`, `W` geometry |
| `NRES` | N+ diffusion resistor | 180 ohm/sq |
| `PRES` | P+ diffusion resistor | 1.2 kohm/sq |
| `R_SOI` | undoped SOI resistor | 250 Mohm/sq |
| `MIMCAP` | MIM capacitor | 0.115 fF/um² |
| `TINCAP` | TiN capacitor | 0.115 fF/um² |
| `PDIFFCAP` | P-diff capacitor | 2.3 fF/um² |
| `NDIFFCAP` | N-diff capacitor | 2.3 fF/um² |
| `D` | diode | diode extractor |

The connectivity stack is TiN, contact, ML1, VIA1, ML2, and body-tie path,
with the legacy capacitor, resistor, and diode connections retained. `MASK`
(20/0) excludes all geometry below it; `EXCLUDE` (63/63) is also ignored.
`TIN_BLK` (5/1) splits a TiN gate and enables `NMOS4` body-tie extraction.

## Comparison

The deck pairs the selected layout top with the SPICE reader's `.TOP` circuit,
creates top-level layout pins, flattens `Nch*` and `Pch*`, and runs alignment.
The class mappings are:

- `NMOS` ↔ `NCH`
- `PMOS` ↔ `PCH`
- `MIMCAP`, `TINCAP`, `PDIFFCAP`, `NDIFFCAP` ↔ `CAP`
- `NRES`, `PRES`, `R_SOI` ↔ `RES`

Relative tolerance is 3% for `R` on the resistor classes and `C` on the
capacitor classes. Device combination is enabled on both layout and schematic
netlists. The run prints `MATCH (mismatches=0)` or `NO MATCH`; detailed
cross-reference/mismatch information is written to the `.lyrdb` report.

For pad-array smoke layouts, the deck applies the legacy compare filters
`max_res(500)` and `min_caps(1e-12)`: very-high-value parasitic resistors and
sub-picofarad extraction fragments are omitted from the *comparison*. The
extractors and class definitions remain present, and no geometry/device rule is
removed by these filters.

## Layer modes

`design` uses the designer-facing layers in `doc/layers.md`: DIFF 3/0,
TIN 5/0, CNT 7/0, ML1 8/0, VIA1 9/0, ML2 10/0, PAREA 18/0, NAREA 19/0,
NCH 21/0, plus RES/CAP/DIO 15/0–17/0.

`fab` uses the foundry mask numbering: P_DIFF 2/0, N_DIFF 3/0, SOI 4/0,
TIN_GATE 5/0, CONTACT 6/0, M1_AL 7/0, VIA1 8/0, M2_AL 9/0, VIA2 11/0,
M3_AL 12/0, and N_CH 13/0. RES/CAP/DIO remain 15/0–17/0. ML2/VIA1/VIA2/ML3
are retained as options because the foundry qualification is still T.B.D.
