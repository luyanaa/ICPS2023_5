# ICPS2023_5 PEX (KLayout)

`icps2023_5_pex.py` extracts layout parasitics for the minimal Fab SOI-CMOS
process. The legacy PDK shipped no PEX deck at all; this one replaces the
`Backannotate device parasitics` macro workflow with a headless, reproducible
extraction.

## Usage

```bash
nix develop --command python3 \
  libs.tech/klayout/pex/icps2023_5_pex.py \
  --input <layout.gds> [--top CELL] [--output <parasitics.spice>] \
  [--layer-mode design|fab] [--no-capacitance] [--process-profile NAME]
```

Defaults: `--output <input>_pex.spice` and `--layer-mode design`. Profile
selection precedence is `--process-profile`, the
`ICPS2023_5_PROCESS_PROFILE` environment variable, then
`process_profiles.json`'s `icps2023_5_1m` default. The resolved profile is
printed in the PEX banner. Files with more than one top cell require
`--top CELL` (library GDS files do).

PEX rejects any unmasked VIA1/ML2/VIA2/ML3 geometry in both profiles rather than
silently omitting it: no upper-metal sheet/contact resistance or coupling
capacitance model is qualified. Experimental permits VIA1/ML2 PCell geometry
and provisional DRC/fab-prep handling only; it does not extend PEX coverage.

## Model

The extractor models only the qualified layers in the source PDK documents.
The `fab` layer IDs are from `PDK_minimalFabSOICMOS.pdf`, p.19, **Table of
Drawn Layers for Each Device**: P-diff 2/0, N-diff 3/0, SOI 4/0, TiN-gate
5/0, contact 6/0, and Al (M1) 7/0.  `design` IDs are the repository's
designer-facing aliases for those layers (see `../doc/layers.md`).  Upper
metal/via IDs are intentionally not in this deck: the reference manual p.3
marks Via, M2(Al), Via, and M3(Al) **T.B.D.**

### Geometry filters

In `design` mode, `MASK` (20/0) and `EXCLUDE` (63/63) are subtracted from
every extracted conductor, contact, and capacitor region. Fab preparation
consumes both layers before writing the mask geometry; `fab` mode subtracts
`EXCLUDE` if it is still present and does not reinterpret design `MASK`.

### Resistance

Values below are direct measurements from `PDK_minimalFabSOICMOS.pdf`, p.8,
**Table of Sheet & Contact Resistance**.  The reference manual p.4,
**抵抗**, reports the same values (with P+ written as 1.2 kΩ/sq).

| Conductor/contact | Value | Source |
|-------------------|------:|--------|
| ML1 (Al) | 0.20 Ω/sq | minimalFab p.8, Sheet Resistance |
| TiN | 20 Ω/sq | minimalFab p.8, Sheet Resistance |
| N+ diffusion (`NAREA`) | 180 Ω/sq | minimalFab p.8, Sheet Resistance |
| P+ diffusion (`PAREA`) | 1.2 kΩ/sq (1200 Ω/sq) | minimalFab p.8, Sheet Resistance |
| Al–N+ contact (`CNT`) | 150 Ω | minimalFab p.8, Contact Resistance; reference p.4 `RCALN` |
| Al–P+ contact (`CNT`) | 200 Ω | minimalFab p.8, Contact Resistance; reference p.4 `RCALP` |
| Al–TiN contact (`CNT`) | 0.9 Ω | minimalFab p.8, Contact Resistance; reference p.4 `RCALT` |

The GDS has one untyped `CNT` mask for all three contact classes. Before
extraction the script partitions non-MOS cuts into internal via layers using
the common cut/bottom/top geometry. If a cut overlaps both TiN and an implant
mask, the TiN–ML1 assignment wins; this geometric priority avoids turning a
gate contact into a diffusion short. This is an extraction convention, not a
separate foundry layer or measured classification rule. MOS source/drain cuts
are excluded from these resistive vias and are handled as ideal graph ties
between the ML1 terminal and remaining external diffusion.

The PDF contact values are for 5 × 5 µm contacts. KLayout's
[`RExtractorTechVia`](https://www.klayout.de/doc-qt5/code/class_RExtractorTechVia.html)
accepts area resistance in Ω·µm², not fixed Ω. The deck sets
`R_A = R_5×5 × 25 µm²`, so a nominal 5 × 5 µm cut reproduces the table value
and other cut areas use inverse-area scaling. Only the 5 × 5 µm value is
measured; scaling other contact sizes is an explicit model assumption.

### Ownership with LVS

The PEX subcircuit contains parasitics only; it does not emit MOS, resistor,
capacitor, or diode devices. Use this allocation when pairing it with the LVS
device netlist:

| Geometry/term | Owner | PEX behavior and limit |
|---|---|---|
| MOS channel and intrinsic charge | BSIMSOI device model | No duplicate MOS element or gate/channel C. PEX clips each bounded gate-to-terminal diffusion corridor through the outer edge of its nearest opposing CNT cut, then contracts the ML1 and external-diffusion anchors into one ideal network node. The MOS-owned CNT contact/access resistance is not added; connected diffusion beyond the cut remains PEX-owned. B4SOI `RDSW` is an effective fit with no separate access-resistance data, so this avoids double-counting an unseparated term. |
| `RES ∩ DIFF` resistor body | LVS `NRES`/`PRES` device | PEX removes the marked N/P body from sheet-R and diffusion-C extraction. Contacts whose lower conductor is wholly within the marker are omitted too; the resistor model has no independently measured terminal-contact term. |
| Explicit `CAP ∩ TIN ∩ DIFF` | LVS `NDIFFCAP`/`PDIFFCAP` device | PEX emits no duplicate 2.3 fF/µm² Cox. Plate wiring resistance and qualified CNT contacts remain PEX-owned outside MOS/RES exclusions. |
| `DIO` diode primitive | LVS diode I/V model | The shipped diode model specifies `Is` and `N`, not series R or junction C. PEX owns measured N/P diffusion and CNT access resistance in DIO geometry; it emits no diode capacitance because the MOSFET diffusion density is not a diode C–V characterization. |
| Other N/P diffusion | PEX | PEX owns measured sheet/contact resistance. It may emit reported diffusion density only outside bounded MOS terminal corridors, `RES`, `CAP`, and `DIO` regions, mapped to assumed global `SUB` as described below. |
| TiN, ML1, and TiN-to-ML1 CNT | PEX | PEX owns sheet and contact resistance; these terms are not represented by the LVS device primitives. |

MOS-access suppression is local geometry, not whole-component selection. For
each gate, the extractor pairs the nearest overlapping CNT cuts on opposite
sides along the uniquely best axis, then removes N/P diffusion from the gate
edge through the outer edge of each cut. Connected diffusion beyond those cuts
remains PEX-owned. The selected MOS CNT contact is omitted from the resistive
via model; the extractor contracts its ML1 and external-diffusion anchor nodes
as an ideal connection, with no epsilon or measured contact resistance. Missing
opposing contacts, tied nearest-contact choices, ambiguous axes, and corridors
that cross another gate abort extraction. A gate with no nearby NAREA or PAREA
component is reported as orphaned and aborts extraction; a gate associated only
with the opposite polarity is not miscounted as orphaned for the current layer.
`MOS_ACCESS_RESULT` JSON records the layer, gate bounds, failure reason, and
bounded/ambiguous/unbounded/orphaned counts; a failed layout emits no new
parasitic netlist. This is a geometric ownership check, not a foundry device
classifier.

If the local carve leaves no external-diffusion sliver at a selected contact,
the MOS terminal maps to that contact's surviving ML1 anchor. A selected cut
with neither an external-diffusion nor an ML1 anchor is rejected.
RDSW/access remains provisional and is not independently characterized by the
supplied data.

### Capacitance

PEX does not emit Cox for explicit `CAP` windows. LVS owns those two-terminal
capacitor devices at the measured 2.3 fF/µm² density. Intrinsic MOS charge is
left to BSIMSOI; the stage-1 fit has no validated C–V characterization. The
only emitted PEX capacitance is the reported N/P diffusion density on eligible
non-device diffusion:

| Term | Value | Extracted? | Source/ownership |
|------|------:|:----------:|--------|
| Eligible N/P diffusion to assumed `SUB` | 0.086 fF/µm² | yes, model assumption | minimalFab p.9, MOSFET Capacitance; reference p.4 `CNdiff`/`CPdiff` |
| Explicit diffusion-capacitor Cox | 2.3 fF/µm² | **no** | LVS `NDIFFCAP`/`PDIFFCAP`; minimalFab p.9 |
| NFET overlap | 5 fF/µm | **no** | minimalFab p.9; no validated perimeter rule |
| PFET overlap | 6.8 fF/µm | **no** | minimalFab p.9; no validated perimeter rule |

For each merged electrical component, the total eligible capacitance remains
`0.086 fF/µm² × eligible area`; the extractor divides that total equally among
all located resistance-network nodes in the component, including internal and
vertex-port nodes. This is a graph-node allocation, not a local-area field
solution or full distributed-RC model.

The PDFs report `CNdiff`/`CPdiff` density, measurement dimensions, and bias,
but do not identify the return conductor. Mapping eligible passive diffusion
to global `SUB` is an explicit assumption, not evidence of measured
diffusion-to-BOX coupling. MOS-access, `RES`, explicit `CAP`, and `DIO` regions
are excluded from this density to keep device-owned terms separate.

The PDK gives no numeric qualified interconnect-coupling coefficient: p.9
reports device Cox, MOSFET diffusion density, and one-dimensional MOS overlap,
not TiN/ML1 or wire-to-wire coupling. The deck emits no interconnect coupling
instead of reusing a device value or inferring a dielectric coefficient.

`AIST-SOI-CMOS.xs` is v0.2 based on a July 2021 cross-section; it is a process
prior, not a validated complete 2023 production stack. The October 2023 PDK
lists a 15 nm gate oxide (p.4) and a 300 nm TEOS dielectric for its TiN–Al
capacitor (p.18), but those device-specific dimensions do not qualify the
dielectric stack for other conductor pairs. The stack discrepancy remains
unresolved; the following BOX calculation is illustrative only.

Independently, an ideal parallel-plate estimate for the 400 nm BOX shown in
`AIST-SOI-CMOS.xs`, using `εr=3.9`, is
`ε0 εr / tBOX = 0.0863 fF/µm²` (8.63 fF per 100 µm²). This value is **not**
emitted by this deck. Its numerical proximity to the documented
`CNdiff`/`CPdiff` density does not make the two quantities equivalent; the
source, physical terminal, and device-ownership boundaries differ.

The measured overlap values are one-dimensional fF/µm quantities.  They are
documented but omitted from the netlist because the source documents do not
define a validated layout edge/perimeter rule for converting them into a
distributed term; the script therefore does not invent one.  Likewise, no
TiN–Al or M1–M2 capacitance is included: the PDFs do not provide a qualified
numeric value for it, and M2 is marked T.B.D.

### Terminals and output

Every non-MOS cut assigned to a qualified conductor pair becomes an internal
port at the common via footprint. Bounded MOS source/drain cuts instead create
internal anchors on the ML1 terminal and remaining external diffusion. The
extractor joins those anchors and removes exact zero-ohm graph edges before
writing SPICE; it does not add MOS-terminal CNT resistance. In `design` mode,
`TEXT` (13/0) labels are read. Fab prep preserves them on 13/1 so they do not
collide with the N-channel mask on 13/0; `--layer-mode fab` reads that layer.

Each distinct `(logical label, conductor layer, x, y)` text anchor initially
receives a deterministic name (`PORT0001`, `PORT0002`, etc.). After network
extraction, anchors joined by an ideal physical connection share one physical
token. The `.SUBCKT <top>_pex` header lists each unique physical token once,
followed by the global `SUB` return. A SPICE comment for each logical terminal
contains JSON:

```text
* PORT_MAP {"layer":"ML1","logical":"OUT","physical":"PORT0001","x_um":10.0,"y_um":5.0}
.SUBCKT CELL_pex PORT0001 PORT0002 SUB
```

Repeated logical labels at different physical anchors remain distinct unless
the extracted network physically contracts them to one node. Exact duplicate
label/layer/coordinate records are deduplicated. Identical `physical` values in
`PORT_MAP` denote one extracted electrical node; distinct values remain
separate for the caller's wrapper/assembler to connect according to its source
netlist. No automatic LVS-to-PEX netlist merge is provided.

For each mapped MOS, PEX also writes one `* MOS_MAP` JSON record containing
the geometry key/polarity, terminal axis and side convention, physical D/G/S
nodes, separate source/drain CNT IDs, gate-center coordinates, and `E:"SUB"`.
For the supported native R0 cells, `axis:"x"` maps the positive-x diffusion
terminal to D and negative-x to S; the gate anchor is the TiN-side physical
node. These records describe physical anchors, not another MOS element and not
a replacement for LVS comparison. Missing terminal contacts or unowned MOS
ties abort extraction.

`libs.tech/ngspice/tools/assemble_postlayout.py` consumes the LVS named-pin
comments, `PORT_MAP`, `MOS_MAP`, and PEX R/C elements. It aliases only each
top-level logical pin's unique ML1 anchor, preserves finite gate/drain/source
resistance paths, and assigns LVS D/G/S terminals to their mapped physical
nodes. Its graph checks reject disconnected terminals on one LVS net, merged
distinct LVS nets, duplicate contact claims to distinct physical nodes, and
floating PEX components. The assembler accepts only the documented R0
MOS-only inverter/NAND extraction contract; it rejects unsupported device
classes and MOS orientations instead of guessing D/S order.

`SUB` is always a separate global model reference. A physical text label named
`SUB` is exported as a `PORT####` with `"logical":"SUB"`; it is not silently
aliased to the assumed diffusion-capacitance return. Connect both only when
the consuming circuit explicitly intends that topology. Generated CNT and
unlabelled internal nodes are not formal subcircuit pins.

### Research-only 2-D electrostatics FEM

`fieldsolve_box.py` is an optional scikit-fem 11.0.0 finite-element study,
run on tinybox with NumPy 2.2.6 and SciPy 1.14.1. Its default mode solves a
homogeneous 400 nm SiO2 slab using an assumed `epsilon_r=3.9` (not a measured
2023 PDK parameter), between an ideal grounded bottom plane and a finite-width
top strip. The dielectric surface outside the strip and the
lateral boundaries are insulating; the 2-D cross-section represents an
infinitely long strip. Capacitance per unit length is calculated from
electrostatic energy. This is an ideal BOX coupon, not the PDK's complete
conductor stack or a qualified substrate-return model.

The same script has an opt-in generic layered mode. Repeat
`--dielectric-layer NAME:THICKNESS_UM:EPSILON_R` in bottom-to-top order and
`--electrode NAME:Z_UM:CENTER_X_UM:WIDTH_UM` for each finite conductor strip.
Electrodes must lie on layer boundaries. This mode returns a Maxwell
capacitance matrix in fF/um, with rows and columns in electrode order; the
bounded domain's lateral and unelectroded top boundaries are insulating.
Relative permittivities and electrode geometry are required caller inputs;
there is no embedded 2023 multilayer stack or default dielectric coefficient.

The 2023 PDK PDF gives 15 nm gate oxide (p.4; also p.18's diffusion-capacitor
cross-sections) and 300 nm TEOS in the TiN/Al capacitor cross-section (p.18),
but no relative permittivities or complete 2023 conductor/return stack. These
separate device cross-sections do not establish one combined stack. The
generic solver can evaluate only a specifically justified caller-supplied
stack; it does not infer one from the older 2021 XS examples. Any supplied
permittivity not backed by process data is a sensitivity input, not a PDK fact.

With a 20 nm maximum Cartesian grid step (triangle edges up to 28.3 nm) and a lateral
margin of 12 BOX thicknesses, the infinite-plate analytic limit is 0.0863283 fF/um2.
Finite-strip results:

| Strip width (um) | FEM C (fF/um) | FEM C/width (fF/um2) | Ratio to plate limit |
|----------------:|--------------:|--------------------:|---------------------:|
| 1 | 0.118032 | 0.118032 | 1.36724 |
| 10 | 0.894989 | 0.0894986 | 1.03673 |
| 100 | 8.66454 | 0.0866454 | 1.00367 |

For a 10 um strip, refining the maximum Cartesian grid step from 40 nm to
20 nm to 10 nm changes density from 0.0896236 to 0.0894986 to 0.0894371
fF/um2. At 20 nm grid spacing, changing the side margin from 6 to 24 BOX
thicknesses changes the 10 um result by less than 0.001%. This checks
numerical stability for this coupon only; the remaining mesh refinement error
is not a process-model uncertainty estimate.

The FEM tests check the wide-strip parallel-plate limit, mesh refinement,
geometric scaling, a two-dielectric series-capacitor reference, matrix
passivity/charge conservation, and permittivity sensitivity. The checked-in
[`interconnect_cap_reference.json`](interconnect_cap_reference.json) stores a
synthetic three-electrode geometry and its numerical matrix for regression;
neither the materials nor the matrix are 2023 process data. PEX ownership is
separate: `test_unqualified_interconnect_coupling_is_not_emitted` asserts that
unsupported interconnect coupling is omitted, and the FEM matrix is not an
extraction coefficient.

Reproduce the homogeneous coupon and its tests with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: \
  "numpy==2.2.6" "scipy==1.14.1" "scikit-fem==11.0.0"
.venv/bin/python libs.tech/klayout/pex/fieldsolve_box.py \
  --widths-um 1 10 100 --max-step-um 0.02
.venv/bin/python libs.tech/klayout/pex/test_fieldsolve_box.py
```

Do not transfer either coupon's results into the PEX deck. The homogeneous
study assumes an ideal substrate return across BOX; the layered mode is only
as meaningful as its caller-provided material and electrode inputs. The PDK
does not identify the return terminal for measured `CNdiff`/`CPdiff`, and its
2021 cross-section is not a validated 2023 stack. Neither mode models MOS
charge, layout-derived finite-length geometry, or distributed RC.
## Shipped sample limitation

The shipped NAND contest GDS contains unmasked VIA1/ML2 geometry. PEX rejects
that input in both profiles before writing an output file; earlier NAND
extraction counts are not current PEX evidence. The integration suite exercises
bounded, hand-built geometries instead of claiming full-chip extraction.

## Limits (read before trusting a number)

* Eligible passive-diffusion area is partitioned into bounded Euclidean
  Voronoi cells around located resistance-network nodes on the same diffusion
  layer. Each cell's geometric area receives the reported density, preserving
  the component total while avoiding an equal split across all electrical
  layers/nodes. This remains node-lumped capacitance, not continuous distributed
  RC. It emits no MOS intrinsic C, explicit CAP Cox, DIO C, fringing, or
  interconnect coupling. The research-only BOX coupon above supplies no PEX
  coefficient.
* MOS source/drain access from each gate edge through the outer edge of its
  nearest terminal CNT cut is omitted from additive PEX because fitted `RDSW`
  cannot be separated from physical access. Connected diffusion beyond the cut
  remains PEX-owned and is joined to the MOS terminal by an ideal graph
  contraction; MOS CNT contact resistance is not independently added. Marked
  `RES` bodies and wholly enclosed terminal contacts are also omitted because
  LVS owns the resistor body and their terminal-contact contributions are not
  independently characterized.
* `PORT_MAP` JSON rows preserve each physical text anchor and its logical
  label. Duplicate logical labels are not merged inside the PEX subcircuit.
  The global `SUB` model reference remains separate from any physical text
  label named `SUB`; see [Terminals and output](#terminals-and-output).
* The diffusion-to-`SUB` coefficient remains an explicit return-node
  assumption; it does not model a physical BOX/substrate network or connect
  `SUB` to the drawn NCH/body-tie path.
* `CNT` is not type-encoded. The per-cut priority (TiN, NAREA, PAREA) is a
  geometric disambiguation heuristic; layouts whose contact geometry does not
  uniquely identify its lower conductor need review before using extracted R.
* The resistance network uses square counting, which assumes thin conductor
  parts. For large sheet-like regions, use the tessellation algorithm only
  after comparing its result against geometry-appropriate references.

## Regression checks

Run `python3 libs.tech/klayout/pex/test_pex.py` inside the KLayout/LibreLane
Nix development shell. The 15 tests verify analytic N/P/TiN/ML1 sheet
resistance, typed contact resistance, an ngspice 1 mA DC IR path through TiN,
ML1, and an Al–TiN contact, 100/300 µm N+/P+ diffusion-to-terminal
connectivity and R□L/W, MOS/RES/CAP/DIO ownership, duplicate-label and
physical-SUB port mappings, area-conserving spatial diffusion capacitance,
omission of unqualified interconnect coupling, mask/exclude behavior, and
fab-prep label preservation. They do not claim characterized MOS-access or
resistor-terminal contact resistance, BOX return, fringe, coupling, or MOS C–V
signoff.

The DC IR stimulus checks an extracted five-square TiN segment (100 Ω), the
modeled 0.9 Ω, 5 µm × 5 µm Al–TiN contact, and 50/6 ML1 squares (1.67 Ω):
the 1 mA expected drop is 102.57 mV. It checks the modeled resistive network,
not a current-density or temperature limit. The source PDFs provide no qualified
EM/current-density or thermal limits, so none are inferred here.
