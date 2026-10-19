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
  [-rd substrate=NET] [-rd filters=legacy]
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

The shipped NAND contest GDS is foundry-mask numbered in place; it contains
unqualified VIA1/ML2 geometry, so select the experimental profile explicitly to
reproduce its legacy LVS comparison. No fab-prep conversion is applied at test
time. The profile is not foundry qualification.

The B4SOI SPICE reader preserves `Mname D G S E model ...` terminal order.
The deck requires every `E` terminal to use the declared global substrate;
default is `.GLOBAL SUB`, overridable with `-rd substrate=NET`. `E` is still
projected out of comparison because this PDK has no matching substrate/BOX
geometry, so this checks the schematic substrate contract, not a physical
backside contact or layout-to-E connection. A five-terminal `D G S E P` line
maps `P` to an extracted four-terminal device (`NCH4`/`PCH4`). NMOS4 is
extracted physically (see below). PMOS body ties have no mask-visible body path
in the current foundry mask set: there is no P-ch channel mask and `NWL` has no
fab mask, and the PCell's off-island n-substrate contact is therefore not
visible to the LVS. The reader accepts `PCH4`, but an in-island N+ dot (the only
mask-visible tie signature) aborts the extraction instead of silently dropping
the body terminal; full PMOS4 extraction is an open item.

MOS `L` is measured geometrically from the SOI dopant-free gap: design mode
reproduces fab-prep's dopant-mask derivation, while fab mode uses the N/P
diffusion masks directly. Final TiN is checked as gate coverage, not used as a
fixed overlap correction. Thus an asymmetric final TiN outline does not alter
the extracted physical gap. This is a geometric layout quantity, not a claim
about metallurgical/electrical finished-silicon `Leff`, and only the documented
native PCell geometry is represented.

The shipped NAND's B4SOI reference is
`Samples/Semicon2023/NAND/nand_sample_b4soi_reference.cir`; the mask-numbered
GDS has a 14 um TiN gate window (the 10 um drawing gate plus 2 um per end) over
a 10 um physical dopant gap. That reference preserves the measured geometry; it
is separate from the current native PCell test geometry (`L=6` um).

### PMOS4 manufacturable-geometry contract (PMOS4_EXPERIMENTAL)

`mos4.W` does not require a dedicated manufacturing mask: it may be a layer
derived from several masks. What it does require is a mask-geometry rule that
guarantees the derived W is the correct physical network. `SOI - P_DIFF - N_CH`
is only a candidate: it does not make every remaining SOI region one conductor,
it must exclude the P+ source/drain, and a fully depleted path may not conduct.

`test_pmos4_mask_contract.py` draws a minimal fabrication-mask reference with no
drawing-to-fab conversion and pins four contracts: C1 a continuous N-type body
path outside `P_DIFF`; C2 the measured remaining corridor width on the final
masks (the two-sided 2 um bias is not a universal erosion); C3 the main channel
is independently identifiable and the neck is not under the gate (a gate-covered
neck reproduces the documented `L_E = L(1 + L_V/W)` distortion: the 9.09/6 test
ratio implies `L_V/W ~ 0.515`); C5 the N+ dot has a legal CNT/ML1 terminal. C4
(gate CNT not on the body path) is covered by the same fixture.

Two layouts are frozen as permanent negative regressions: a P+ intrusion that
cuts the neck, and a neck under the gate. Neither may ever match a normal
rectangular PMOS4; the current deck refuses every PMOS tie because the
experimental extractor is not implemented.

Drawing-to-fab realizability is gated in the same file: a legal drawing input
is converted and XOR-compared per mask against the reference. SOI, `P_DIFF`,
`N_DIFF`, CNT and ML1 are reproduced exactly (`PAREA`/`NAREA` are the target
windows inset by 2 um, so the 2 um dilation cancels; no keepout notch is needed
because the drawing dope windows stop at the channel edges). The single
unrealizable element is the gate: the isotropic 2 um TiN bias adds exactly
12 um^2 over the 6 um neck (2 um of gated neck), so `C3` is not realizable under
the current rule. The remedies are to shorten the drawing gate's edge extent at
the neck side by 2 um so the biased gate only touches the neck (this changes the
extracted `W` unless the island is grown on that side), or to make the gate bias
anisotropic (channel-length axis only). Relaxing the LVS to accept the wrong
mask is not an option.

Electrical qualification is separate. LVS can only establish a geometric body
tie. `dVth/dVbody` and `R_body-access(VG, VD, VB)` must come from TEG and model
work; if the body access is strongly bias-dependent or very high, a
three-terminal PMOS with the BSIMSOI `E` (backgate) terminal may be the correct
model, not a PMOS4 with an ideal `P` connection.

## Extracted devices

| Class | Device | Value |
|---|---|---:|
| `NMOS` | three-terminal nMOS | `L`, `W` geometry |
| `PMOS` | three-terminal pMOS | `L`, `W` geometry |
| `NMOS4` | body-tied nMOS (`pwl`/NCH terminal) | `L`, `W` geometry |
| `NRES` | N+ diffusion resistor | 180 ohm/sq |
| `PRES` | P+ diffusion resistor | 1.2 kohm/sq |
| `R_TIN` | TiN resistor under RES, excluding CAP and SOI | 20 ohm/sq |
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
`TIN_BLK` (5/1) splits a TiN gate and enables `NMOS4` body-tie extraction in
design mode. `TIN_BLK` has no fab mask, so fab mode identifies a body tie
physically instead: the dopant-free channel must lie in an N-ch (body dope)
component that reaches a P+ dot inside the SOI, and that dot must carry a
contact. A wider N-ch dope band is not a tie by itself, and an N-ch body path
whose P+ dot is uncontacted aborts the extraction instead of matching a
three-terminal schematic.

## Comparison

The deck pairs the selected layout top with the SPICE reader's `.TOP` circuit,
creates top-level layout pins, flattens `Nch*` and `Pch*`, and runs alignment.
The class mappings are:

- `NMOS` ↔ `NCH`
- `PMOS` ↔ `PCH`
- `NMOS4` ↔ `NCH4`
- `MIMCAP`, `TINCAP`, `PDIFFCAP`, `NDIFFCAP` ↔ `CAP`
- `NRES`, `PRES`, `R_SOI`, `R_TIN` ↔ `RES`
Relative tolerance is 3% for `R` on the resistor classes and `C` on the
capacitor classes. Device combination is enabled on both layout and schematic
netlists. The run prints `MATCH (mismatches=0)` or `NO MATCH`; detailed
cross-reference/mismatch information is written to the `.lyrdb` report.

Before writing the extracted SPICE, the deck calls `netlist.make_top_level_pins`.
TEXT labels on ML1 are connected in the extraction graph, so the output keeps
named top-level terminals as formal nodes and adds `* net <node> <label>`
comments. The post-layout assembler uses these comments to associate LVS
logical pin names with PEX `PORT_MAP` anchors; it rejects missing or
non-one-to-one pin metadata rather than inferring a mapping from node order.

Each extracted `R_TIN` physical device is tagged in the LVS SPICE comment
stream with its instance, terminal net IDs, resistance, and geometry center.
The post-layout assembler accepts only these explicitly tagged resistor
instances; other passive LVS elements remain rejected. The TiN resistor body
is not included as an unmarked PEX wire.

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

* The MOS reader preserves `D/G/S`, validates the common B4SOI `E` substrate,
  and supports only the explicit N-channel `P` body-tie mapping to `NMOS4`.
* MOS `L` is the geometric SOI dopant-free gap. Design mode reproduces the
  fab-prep dopant-mask derivation; fab mode measures the physical dopant-mask
  gap. Final TiN must cover that gap but no fixed TiN overlap is subtracted.
* An extracted `R_TIN` is explicit LVS-owned resistance; its body is excluded
  from the PEX conductor wire to avoid parallel counting.
* NMOS4 body-tie gate geometry is removed from the source/drain diffusion
  regions; PMOS body ties have no mask-visible body path; an in-island N+ dot fails closed until PMOS4 extraction lands.

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

`test_lvs_contract.py` also builds a strict design-layer MOS/resistor/PDIFFCAP
fixture with physical channel `L=6`, rejects wrong width and wrong channel
length, and checks a fab-mask example where the 6 um dopant gap is covered by
asymmetric 12 um final TiN. The latter is a geometry regression fixture, not a
manufacturing DFM/signoff result.

`test_channel_length_pipeline.py` drives the native `Nch`/`Pch` PCells through
fab prep and both LVS modes for `L=6/10/15` um and measures the masks directly:
the drawing TiN is `L`, the final TiN is `L+4` um, the physical dopant window is
`L` and both modes extract `L`. Its negative control pre-expands the drawing TiN
by 2 um per end (the legacy PCell convention); fab prep then produces an `L+8` um
mask over an `L+4` um dopant window, and both modes must reject it. The
pre-consolidation deck subtracted `gate_overlap_um` per end and matched that
layout silently, so this fixture pins the current behavior.

`test_fab_body_tie.py` covers the fab-mask body-tie rule: a native tied `Nch`
extracts `NMOS4` in both modes, a plain `Nch` is never mistaken for a tie, and
deleting the tie contact aborts the extraction. The legacy NAND sample, whose
island-wide N-ch dope band previously tripped the old TiN/N-ch heuristic, is
covered by `test_lvs_contract.py` in both layer modes.
