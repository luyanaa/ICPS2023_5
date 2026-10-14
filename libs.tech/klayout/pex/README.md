# ICPS2023_5 PEX (KLayout)

`icps2023_5_pex.py` extracts layout parasitics for the minimal Fab SOI-CMOS
process. The legacy PDK shipped no PEX deck at all; this one replaces the
`Backannotate device parasitics` macro workflow with a headless, reproducible
extraction.

## Usage

```bash
cd ~/Documents/librelane && nix-shell --run \
  'python3 libs.tech/klayout/pex/icps2023_5_pex.py \
      --input <layout.gds> [--top CELL] [--output <parasitics.spice>] \
      [--layer-mode design|fab] [--no-capacitance]'
```

Defaults: `--output <input>_pex.spice`, `--layer-mode design`. Files with more
than one top cell require `--top CELL` (library GDS files do).

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
extraction the script partitions each cut into internal via layers using the
common cut/bottom/top geometry. If a cut overlaps both TiN and an implant mask,
the TiN–ML1 assignment wins; this geometric priority avoids turning a gate
contact into a diffusion short. This is an extraction convention, not a
separate foundry layer or a measured classification rule. N+ and P+ contact
resistances remain distinct.

The PDF contact values are for 5 × 5 µm contacts. KLayout's
[`RExtractorTechVia`](https://www.klayout.de/doc-qt5/code/class_RExtractorTechVia.html)
accepts area resistance in Ω·µm², not fixed Ω. The deck sets
`R_A = R_5×5 × 25 µm²`, so a nominal 5 × 5 µm cut reproduces the table value
and other cut areas use inverse-area scaling. Only the 5 × 5 µm value is
measured; scaling other contact sizes is an explicit model assumption.

### Capacitance

The script emits lumped Cox only for explicit diffusion-capacitor windows:
`CAP` must overlap TiN and N/P diffusion. Before resistance extraction and
diffusion-to-`SUB` area calculation, it removes TiN-over-DIFF MOS channel regions
from the N/P conductors, except where `CAP` marks an explicit capacitor. This
prevents a false source-drain sheet path through the channel; intrinsic MOS
charge remains the BSIMSOI device model's responsibility. Diffusion-capacitance
densities are mapped to global `SUB`.

| Term | Value | Extracted? | Source |
|------|------:|:----------:|--------|
| TiN ↔ N+ diffusion (Cox; explicit `CAP` windows only) | 2.3 fF/µm² | yes | minimalFab p.9, Gate Oxide; reference p.4 `CoxN` |
| TiN ↔ P+ diffusion (Cox; explicit `CAP` windows only) | 2.3 fF/µm² | yes | minimalFab p.9, Gate Oxide; reference p.4 `CoxP` |
| N+ diffusion capacitance density (deck maps to `SUB`) | 0.086 fF/µm² | yes | minimalFab p.9, MOSFET Capacitance; reference p.4 `CNdiff` |
| P+ diffusion capacitance density (deck maps to `SUB`) | 0.086 fF/µm² | yes | minimalFab p.9, MOSFET Capacitance; reference p.4 `CPdiff` |
| NFET overlap | 5 fF/µm | **no** | minimalFab p.9, MOSFET Capacitance |
| PFET overlap | 6.8 fF/µm | **no** | minimalFab p.9, MOSFET Capacitance |

The PDFs report `CNdiff`/`CPdiff` density, measurement dimensions, and bias,
but do not identify the return conductor. Attaching those densities to the
single global `SUB` node is an extractor assumption, not evidence of measured
diffusion-to-BOX coupling.

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

Every cut assigned to a qualified conductor pair becomes a port at the
common via footprint. In `design` mode, `TEXT` (13/0) labels name nets. Fab
prep preserves those labels on 13/1 so they do not collide with the N-channel
mask on 13/0; `--layer-mode fab` reads that label layer. A mask-only fab input
without preserved text uses generated conductor names.
Output contains a subcircuit named `<top cell>_pex`; it always exposes the
global substrate node as `SUB` and emits `R…` elements plus sequentially named
`C…` elements. Diffusion-capacitance elements connect N/P conductor components
to `SUB`; Cox elements connect overlapping TiN/diffusion components only inside
explicit `CAP` windows. If a text label is named `SUB` (case-insensitive), it
supplies that port without adding a duplicate.

`SUB` is the deck's global model reference, not a PDK-identified BOX coupling
terminal and not a source/drain body pin. The PDK PDFs do not specify the return
node for `CNdiff`/`CPdiff`; mapping their densities to `SUB` is an explicit
single-node modeling assumption. Connect `SUB` explicitly in the consumer
testbench. The deck does not infer a substrate contact from a text label or
solve the SOI-film potential.

### Research-only 2-D BOX FEM

`fieldsolve_box.py` is an optional scikit-fem 11.0.0 finite-element study,
run on tinybox with NumPy 2.2.6 and SciPy 1.14.1. It solves a homogeneous
400 nm SiO2 slab (`epsilon_r=3.9`) between an ideal grounded bottom plane and
a finite-width top strip. The dielectric surface outside the strip and the
lateral boundaries are insulating; the 2-D cross-section represents an
infinitely long strip. Capacitance per unit length is calculated from
electrostatic energy. This is an ideal BOX coupon, not the PDK's complete
conductor stack or a qualified substrate-return model.

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

Reproduce with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: \
  "numpy==2.2.6" "scipy==1.14.1" "scikit-fem==11.0.0"
.venv/bin/python libs.tech/klayout/pex/fieldsolve_box.py \
  --widths-um 1 10 100 --max-step-um 0.02
```

Do not transfer these values into the PEX deck. The coupon assumes an ideal
substrate return across homogeneous BOX, whereas the PDK does not identify the
return terminal for measured `CNdiff`/`CPdiff`; its 2021 cross-section is also
not a validated 2023 stack. It contains no MOS charge, TEOS/metal layers,
layout-derived finite-length geometry, or distributed RC.

## Exercised behavior

* Unlabelled `Samples/Semicon2023/NAND/nand_sample.GDS` in design mode:
  1088 contact ports, 1264 R-network nodes, 1260 resistors, 124 capacitors;
  the output header is `.SUBCKT nand_pex SUB`.
* The same NAND after fab preparation in fab mode: 1088 contact ports, 1264
  nodes, 1260 resistors, and 124 capacitors; the output also exposes only `SUB`.

## Limits (read before trusting a number)

* Baseline PEX capacitance is **lumped per conductor component**. It consumes no
  field-solver result and does not model layout-driven fringing or sidewall
  capacitance, nor distributed RC line behaviour. The research-only BOX coupon
  above is separate and supplies no PEX coefficient. A component that carries
  several extracted R nodes attaches its capacitance to one of them.
* The PEX net names come from `TEXT` labels only. Without labels, conductor
  nodes use generated names and only the explicit `SUB` substrate port is
  exposed; run LVS (`../lvs/icps2023_5.lvs`) or label nets for named terminals.
* Coupling capacitance is computed per overlapping TiN/NAREA or TiN/PAREA
  component pair only where an explicit `CAP` window preserves the diffusion
  plate. MOS channel overlap is excluded from interconnect PEX; no unqualified
  M2/ML2 or TiN–Al dielectric term is emitted.
* The extractor maps the reported diffusion capacitance densities to global
  `SUB` as an explicit assumption; it does not model a physical BOX/substrate
  network or connect `SUB` to the drawn NCH/body-tie path.
* `CNT` is not type-encoded. The per-cut priority (TiN, NAREA, PAREA) is a
  geometric disambiguation heuristic; layouts whose contact geometry does not
  uniquely identify its lower conductor need review before using extracted R.
* The resistance network uses the square-counting algorithm, which assumes
  "thin" conductor parts. For large sheet-like regions the tesselation
  algorithm would be the better choice — swap
  `pex.Algorithm.SquareCounting` for `pex.Algorithm.Tesselation` in the script
  if your layout has them.

## Regression checks

Run `python3 libs.tech/klayout/pex/test_pex.py` inside the KLayout/LibreLane
Nix shell. The checks solve the extracted resistor network for the analytic
100 × 10 µm N+, P+, TiN, and ML1 sheet values and 5 × 5 µm N+, P+, and TiN
contact values; prove that design-mode `MASK` and `EXCLUDE` and fab-mode
`EXCLUDE` break an N+ path; and verify fab-prep → fab-mode labels on 13/1.
They also assert 230 fF Cox for an explicit 100 µm² `CAP` window connected
through an N+/ML1 via, verify the lumped capacitances follow that merged
electrical component, exclude unmarked MOS gate overlap from PEX Cox, and account
separately for the documented diffusion-to-`SUB` density. They do not claim
BOX, fringe, coupling, or MOS C–V signoff.
