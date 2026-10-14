# ICPS2023_5 LVS

`icps2023_5.lvs` is the single headless KLayout LVS deck for this PDK. It
replaces the two XML DSL decks (`lvs.lylvs` and `lvs_fabrication.lylvs`) and
has no AnagixLoader, MinedaCommon, Ruby gem, spreadsheet, or Windows-path
configuration dependency.

## Invocation

Run KLayout 0.30.7 in batch mode:

```sh
klayout -b -r libs.tech/klayout/lvs/icps2023_5.lvs \
  -rd input=layout.gds -rd schematic=reference.cir \
  [-rd top=CELL] [-rd layer_mode=design|fab] [-rd process_profile=NAME] \
  [-rd report=out.lyrdb] [-rd output=extracted.cir] \
  [-rd substrate=NET] [-rd filters=legacy] [-rd gate_overlap_um=2.0]
```

`layer_mode` defaults to `design`. If omitted, `report` defaults to
`<input>_lvs.lyrdb` and `output` defaults to `<input>_extracted.cir`. A GDS
with multiple top cells must be invoked with `-rd top=CELL`; the deck reports
the available top-cell names when this is missing. `schematic` is required and
must point to a readable SPICE-format netlist.

The default `icps2023_5_1m` profile rejects unmasked VIA1/ML2/VIA2/ML3 geometry.
The experimental profile preserves the VIA1/ML2 connectivity option and
rejects VIA2/ML3 because no extracted connectivity is defined for them. Select
it with `-rd process_profile=icps2023_5_multimetal_experimental`; it does not
imply foundry qualification.

The shipped NAND contest GDS contains unqualified VIA1/ML2 geometry. Select
the experimental profile explicitly to reproduce its legacy LVS and
fab-prep checks; the profile is not foundry qualification. The regression
helper uses this profile only for those legacy samples.

The B4SOI SPICE reader preserves `Mname D G S E model ...` terminal order.
The deck requires every `E` terminal to use the declared global substrate;
default is `.GLOBAL SUB`, overridable with `-rd substrate=NET`. `E` is still
projected out of layout comparison because this PDK has no matching
substrate/BOX geometry, so this checks the schematic substrate contract, not a
physical backside contact or layout-to-E connection. A five-terminal
`D G S E P` device is rewritten so `P` maps to the extracted `NMOS4` body tie.
The default `gate_overlap_um=2.0` removes 2 µm at each gate end from extracted
`L`, matching the PDF-documented overlap. Override it in microns per end with
`-rd gate_overlap_um=VALUE`; extracted channel length must remain positive.

The shipped NAND's B4SOI reference is
`Samples/Semicon2023/NAND/nand_sample_b4soi_reference.cir`; the legacy
`lvs_work/nand_sample_reference.cir.txt` uses the older fourth-terminal body
convention and records `L=10`; the B4SOI reference uses design-channel `L=6`.

## Extracted devices

| Class | Device | Value |
|---|---|---:|
| `NMOS` | three-terminal nMOS | `L`, `W` geometry |
| `PMOS` | three-terminal pMOS | `L`, `W` geometry |
| `NMOS4` | body-tied nMOS (`pwl`/NCH terminal) | `L`, `W` geometry |
| `NRES` | N+ diffusion resistor | 180 ohm/sq |
| `PRES` | P+ diffusion resistor | 1.2 kohm/sq |
| `R_SOI` | undoped SOI resistor | 250 Mohm/sq (legacy, unqualified) |
| `MIMCAP` | MIM capacitor | 0.115 fF/um² (legacy, unqualified) |
| `TINCAP` | TiN/Al capacitor | 0.115 fF/um² (legacy, unqualified) |
| `PDIFFCAP` | TiN/P-diffusion capacitor | 2.3 fF/um² (Cox) |
| `NDIFFCAP` | TiN/N-diffusion capacitor | 2.3 fF/um² (Cox) |
| `D` | diode | diode extractor |

The PDIFF/NDIFF geometry is the TiN/diffusion capacitor shown on PDK p.18 and
uses the measured 2.3 fF/um² gate-oxide Cox from p.9. The PDFs do not qualify
the listed `R_SOI`, MIMCAP, or TiN/Al-capacitance values; `R_SOI` and MIM/TIN
extraction remain legacy compatibility entries.

In the experimental profile, the connectivity stack is TiN, contact, ML1,
VIA1, and ML2, with the legacy capacitor, resistor, and diode connections
retained. The default 1M profile rejects upper-metal geometry. `MASK` (20/0)
excludes all geometry below it; `EXCLUDE` (63/63) is also ignored.
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

This deck uses strict comparison by default: `max_res` and `min_caps` are
disabled so small capacitors and high-value resistors remain in scope. For
legacy pad-array smoke layouts only, pass `-rd filters=legacy` to apply
`max_res(500)` and `min_caps(1e-12)` to both netlists. That compatibility mode
can hide schematic-only resistors above 500 Ω or capacitors below 1 pF; it is
not device signoff.

## Differences from the original LVS decks

The upstream [design LVS](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/lvs/lvs.lylvs)
and [fabrication LVS](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/lvs/lvs_fabrication.lylvs)
are separate wrapper-driven decks. This consolidated deck selects design or
fab layers explicitly and removes the Anagix/MinedaCommon, spreadsheet, and
symlink runtime. It retains the original extraction/connectivity rules, with
the following behavior changes:

* The old MOS reader maps `S/G/D` against SPICE node order `D/G/S`, swapping
  drain and source. This reader preserves `D/G/S` and adds B4SOI `E` validation
  plus optional external-body `P` mapping to `NMOS4`.
* Both design and fab modes subtract `2 * gate_overlap_um` from extracted `L`;
  the default 2 µm per end is documented on PDF p.4 and p.20.
* Body-tied NCH gate geometry is removed from both `diff_nores` calculations,
  preventing the tied gate region from also being extracted as source/drain.

Use `-rd filters=legacy` only for the established pad-array compatibility
comparison. Strict mode remains a preliminary LVS check, not complete SOI
signoff: the layout has no substrate/BOX geometry or backgate extraction.

## Layer modes

`design` uses the designer-facing layers in `doc/layers.md`: DIFF 3/0,
TIN 5/0, CNT 7/0, ML1 8/0, VIA1 9/0, ML2 10/0, PAREA 18/0, NAREA 19/0,
NCH 21/0, plus RES/CAP/DIO 15/0–17/0.

`fab` uses the foundry mask numbering: P_DIFF 2/0, N_DIFF 3/0, SOI 4/0,
TIN_GATE 5/0, CONTACT 6/0, M1_AL 7/0, VIA1 8/0, M2_AL 9/0, VIA2 11/0,
M3_AL 12/0, and N_CH 13/0. RES/CAP/DIO remain 15/0–17/0. ML2/VIA1/VIA2/ML3
are retained as options because the foundry qualification is still T.B.D.

## Regression checks

Run `python3 libs.tech/klayout/lvs/test_lvs_contract.py` and
`python3 libs.tech/klayout/test_process_profiles.py` inside the KLayout
environment. The profile suite verifies upper-layer rejection in 1M mode and
preservation of supported experimental VIA1/ML2 handling.

`test_lvs_contract.py` also builds a strict design-layer fixture with a positive
MOS/resistor/PDIFFCAP reference, then checks that a changed MOS terminal,
changed width, and changed overlap setting fail comparison.
