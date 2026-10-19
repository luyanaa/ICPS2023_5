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
│   ├── librelane/  experimental 1M/2M LibreLane technology, PDK/SCL contracts and design fragments
│   ├── kicad/      legacy EEschema symbol library (unmaintained)
│   └── ...
├── libs.ref/
│   ├── ICPS2023_5_fd_base/      frame overlay + pad views: gds/, lef/, def/, lib/
│   ├── ICPS2023_5_fd_base_2x2/  same views for the derived 3200 × 3200 µm 2x2 die option
│   ├── ICPS2023_5_fd_pr/        contacts, vias and pad primitives: gds/
│   └── doc/                     PDK documents (PDF)
├── Samples/        design contest template and the NAND tutorial sample
├── grain.xml       KLayout (salt) package descriptor
└── SOURCES         provenance of every artefact in this repository
```

The LibreLane overlay is user-authorized experimental collateral only. It does
not qualify POLY-Si or the specified narrow TiN routes, and does not override
canonical DRC/LVS/PEX layers. The accepted standard-cell package and its partial
SCL config expose LEF/GDS/SPICE views only; no published Liberty/Verilog/CDL,
compatible cell-layer alias, qualified placement site, or process-qualified
timing/RC/PDN data exists. Full PnR is unavailable. After cell characterization,
LibreLane closure is the first integration task; see
`libs.tech/librelane/README.md` for the dependency order.

## Documentation

* `libs.ref/doc/PDK_minimalFabSOICMOS.pdf` — device/process explanation, device
  characteristics, design manual, SPICE model, PCell and DRC/LVS rules.
* `libs.ref/doc/PDK_reference_manual.pdf` — Japanese reference manual (process,
  device table, layout rules, electrical data).
* `libs.tech/klayout/doc/layers.md` — the canonical layer table (design and
  fab numbering) and the history of the numbering mess this PDK used to have.
* `libs.tech/ngspice/README.md`, `libs.tech/xschem/README.md`,
  `libs.tech/klayout/{drc,lvs,pex}/README.md`, and
  `libs.tech/librelane/README.md` — how to run each tool flow and the boundaries
  of the experimental LibreLane overlay.
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
`MinedaCommon` runtime. The root Nix flake pins the non-LibreLane verification
toolchain (Nix-EDA 6.11.0; KLayout 0.30.7, Python 3.13.9, ngspice 44, and Ruby
3.3.10); enter it with `nix develop`. CI runs the non-LibreLane regressions.
LibreLane is excluded until cell characterization; closure is the first
integration task afterward, as documented in `libs.tech/librelane/README.md`.

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
nix develop --command python3 \
  libs.tech/klayout/pex/icps2023_5_pex.py \
  --input design.gds --top CELL --process-profile icps2023_5_1m
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
| Pads / frame (2x2) | `libs.ref/ICPS2023_5_fd_base_2x2` | derived 3200 × 3200 µm frame, 56 × 60 µm pads; the foundry ships no 2x2 GDS |

## Experimental 1M standard cells

`libs.tech/stdcell/` contains canonical MOS truth/topology data, a Yosys-to-X1
mapper, and an area-first geometry generator restricted to the native
`icps2023_5_1m` conductors. The standard-cell regression runs 22 combinational
transistor-reference truth tables, four sequential capture/hold benches,
Boolean-fallback and Yosys-mapped RTL simulations, strict DRC/LVS/PEX/assembly
plus postlayout characterization for AOI21, and pin-window coverage/bounds
checks:

```bash
nix develop --command python3 libs.tech/stdcell/test_stdcell.py
```

The scalar RTL in `libs.tech/stdcell/alu4.v` defines a four-operation unsigned
4-bit ALU: `S1S0=00` AND, `01` OR, `10` `A+B+CIN`, and `11` `A-B-CIN` with
`CIN` as borrow-in. `COUT` is zero for logic operations, carry-out for
addition, and no-borrow for subtraction. `alu.py` builds the canonical
physical-mapped netlist using only cells accepted by the manifest;
`test_alu.py` checks RTL synthesis, accepted-cell mapping, and exhaustive
operation vectors:

```bash
nix develop --command python3 libs.tech/stdcell/test_alu.py
```

**Status: the generated standard-cell library was cleared for redesign.** The
six output directories under `libs.ref/ICPS2023_5_gen_sc/` (`gds`, `lef`,
`liberty`, `spice`, `characterization`, `doc`) now contain only `.gitkeep`;
`tools/make_gen_sc.py` is retained. The paragraphs below describe the previous
generation and the intended contract; regenerate the library before relying on
any artifact path in them.

The experimental reference package is `libs.ref/ICPS2023_5_gen_sc/`: its SPICE
subcircuits cover all 26 canonical cell specifications, while GDS and LEF are
published only for cells that pass strict native-1M DRC/LVS/PEX and postlayout
assembly. See its `doc/README.md` and per-cell `doc/cell_manifest.json`.
`libs.tech/stdcell/export_views.py` emits structural Verilog and black-box
declarations for accepted cells, but its generic model-metadata path refuses
Liberty. The explicit experimental Xyce Liberty artifact is
`libs.ref/ICPS2023_5_gen_sc/liberty/ICPS2023_5_gen_sc_experimental_xyce.lib`; it is
evidence-scoped and not a qualified timing library.
### Version-scoped FD-bridge FO/RO planning evidence

The Xyce FD-bridge experiments are recorded separately from nominal stdcell
feasibility arcs:

* [`libs.tech/ngspice/audit/b4soi_fd_bridge_timing.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_timing.md)
  reports the 2 ns-edge FO1/FO4 table with real inverter gate loads.
* [`libs.tech/ngspice/audit/b4soi_fd_bridge_sweep_ro.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_sweep_ro.md)
  reports DUT/load scaling and 5/11/21-stage closed-loop RO results. The
  observed planning values are approximately 11.7, 5.3, and 2.8 MHz; the
  closed-loop mean propagation delay is approximately 8.6 ns/stage. These are
  Xyce 7.10 / BSIMSOI 4.5.0 FD-bridge simulations, not silicon measurements.
* [`libs.tech/ngspice/audit/b4soi_fd_bridge_cin_definitions.md`](libs.tech/ngspice/audit/b4soi_fd_bridge_cin_definitions.md)
  separates dynamic charge-derived input capacitance, intrinsic Cgg, and the
  energy-derived definition. The closure-consistent dynamic result is 2258.3
  fF for the recorded edge-start window; the historical 2635 fF pairing and
  `C_eff_energy` remain explicitly non-frozen quantities.

The nominal `libs.tech/stdcell/characterize.py` transient bench remains a
fixed-slew, ideal-lumped-load feasibility stimulus. It is not silently
reinterpreted as the FD-bridge timing bench. The checked-in
`ICPS2023_5_gen_sc_experimental_xyce.lib` is rendered only from complete
Xyce 7.10 / BSIMSOI 4.5.0 evidence at nominal 3 V / 25 °C: 17 combinational
cells have measured arcs, the two constant tie cells carry their function with
no arcs, and the two sequential cells are explicitly excluded. The evidence
itself is checked in under `libs.ref/ICPS2023_5_gen_sc/characterization/` and
produced by `libs.tech/stdcell/xyce_characterization.py`, which re-runs strict
physical verification whenever a published cell's layout changed. This artifact
is experimental only; it has no PVT extrapolation, power model, sequential
setup/hold model, or signoff claim.


`frame.py` measures the actual frame polygons of a configured frame library.
The canonical `config.json` measures the 1600 × 1600 µm / 28-pad 1x1 frame:
with explicit 20/10/10 µm pad/frame/edge engineering reservations the Boolean
available polygon is 1,997,200 µm², whose largest 10 µm-grid square routing
envelope is 1340 × 1340 µm. `config_2x2.json` measures the derived
3200 × 3200 µm / 56-pad library (`libs.ref/ICPS2023_5_fd_base_2x2/doc/README.md`)
with the same reservations: 9,097,200 µm² available and a 2940 × 2940 µm
envelope, 4.55× the 1x1 capacity. Neither is a process-qualified usable core or
a bondout/power assignment. The two unverified “4×” interpretations stay
attached to the 1x1 measurement only; the measured 2x2 frame supersedes them
and reports no scaling scenario.

`feasibility.py` compares INV/NAND2/AOI21 template heights and three physically
drawn PDNs (`edge_rails`, `segmented_rails`, `local_clusters`) on one fixed
NAND+INV benchmark. Before shared-diffusion generation, sparse-channel H200
candidates closed strict DRC/LVS/PEX, postlayout assembly, DC truth and
sensitized path arcs for all three PDNs: 548×272 =149,056µm² experimental
logic core, including signal/supply routing and whitespace, excluding
unintegrated frame/IO. A mirrored experiment also closed all three PDNs.
The 50/60/70µm candidates fail that template's height bounds; this is neither a
global 5/6/7-track impossibility proof nor a qualified row/site. Run selected
current experiments rather than repeating unchanged historical failures:

```bash
nix develop --command python3 libs.tech/stdcell/feasibility.py --out build/m0-selected --height 50 --height 60 --height 70 --height 200 --architecture channel --mirror
nix develop --command python3 libs.tech/stdcell/benchmarks.py all --out build/rtl-benchmarks
```

`metrics.py` requires complete observed physical/functional/routing gates and
ranks routed-core area first, routing robustness next, electrical/contact/PDN
costs last. Missing measurements stay unknown, never zero. No clock target is
specified, so measured delay is not a ranking objective. `benchmarks.py`
provides real RTL and independent reference semantics for tiny logic,
INV/NAND2/AOI21, mux4+decoder, counter, shift-register4, ALU8 and ripple-adder8;
mapped-netlist success alone is not routed or electrical acceptance.

`channel_geometry.py` now reuses topology-safe native Euler diffusion sharing.
The private W24/L6, reverse-signal-order bank has 16 strict physical passes.
Its 14 combinational cells have 13 complete nominal truth/arc passes; OAI21's
arc convergence failure remains explicit. Example channel-template dimensions
are below; these are **A_cell**, not completed core area or process minima:

| Cell | Width × height (µm) | A_cell (µm²) |
|------|---------------------|--------------|
| INV | 166 × 150 | 24,900 |
| NAND2 / NOR2 | 236 × 170 | 40,120 |
| AOI21 / OAI21 | 306 × 200 | 61,200 |
| BUF | 236 × 160 | 37,760 |
| XOR2 | 516 × 240 | 123,840 |
| MUX2 TG6T / static | 372 × 190 / 652 × 260 | 70,680 / 169,520 |

Six minimum logic cells passed 24 actual zero-gap array cases: 2×2, 4×4,
mirrored rows and reversed R180 rows, including strict checks, per-instance
truth, pin windows and conductor isolation. Their supplies are instance-scoped
and ideally driven by the bench, not a completed shared row/core PDN.

Build a new private bank and exercise a real RTL core:

```bash
nix develop --command python3 libs.ref/ICPS2023_5_gen_sc/tools/make_gen_sc.py \
  --candidate-output build/channel-bank --auto-channel-height \
  --pin-order-mode reverse_signals \
  --build-cell INV_X1 --build-cell BUF_X1 \
  --build-cell NAND2_X1 --build-cell NAND3_X1 \
  --build-cell NOR2_X1 --build-cell NOR3_X1 \
  --build-cell AOI21_X1 --build-cell OAI21_X1 \
  --build-cell AOI22_X1 --build-cell OAI22_X1 \
  --build-cell XOR2_X1 --build-cell XNOR2_X1 \
  --build-cell MUX2_TG6T_X1 --build-cell MUX2_STATIC_X1 \
  --build-cell LATCH_GATE_X1 --build-cell DFF_TG_X1
nix develop --command python3 libs.tech/stdcell/routed_benchmarks.py \
  --manifest build/channel-bank/doc/cell_manifest.json \
  --out build/tiny-routed --benchmark tiny_and
```

`--config libs.tech/stdcell/config.json` (default) routes inside the canonical
1x1 frame; `--config libs.tech/stdcell/config_2x2.json` routes the same
fixtures inside the measured 2x2 frame library with the same engineering
reservations. Omit `--benchmark` to run all seven RTL fixtures. Outputs must be
new directories. The flow retains both accepted-only area-oriented ABC and
primitive covers: ABC is heuristic, so neither an ABC cover nor a smaller
A_cell sum is declared the routed winner. Actual ML1/TiN/CNT connectivity,
strict physical checks, full-top electrical behavior and exact polygon fit
gate ranking. Multirow placement is width-bounded, not a qualified site or an
optimal placement proof; wrapped row origins are snapped back to the route
lattice, and opposing-row rail spacing evidence is reported in µm. Complete
A_core remains separate from frame/pad and power-source integration; failed
routing, SPICE or frame fit stays ineligible.

### Current closure boundary

The all-fixture routed runs (same 16-cell private channel bank, all seven RTL
fixtures, three physical PDNs) reproduce one eligible experimental result in
both frames: `tiny_and/primitive/segmented_rails` at 116,644 µm² routed-core
area, with exact measured-polygon fit, strict native-1M DRC/LVS/PEX, whole-core
postlayout behavior, and zero route overflow/shorts; its two sibling PDN
variants pass every gate as well and ranking selects one winner.
`inv_nand2_aoi21`, `mux4_decoder`, `counter`, and `shift_register4` remain
failed by observed route or physical-conductor gates. In the 1x1 frame the
first, second and fourth occupy only 12–73% of the measured envelope, while
`counter`'s 1298 × 2272 µm allocation exceeds the 1340 × 1340 µm envelope
altogether; they fail with 4–30 unrouted or overflowing connections.

Routing the same fixtures inside the measured 3200 × 3200 µm / 56-pad 2x2
frame (`--config libs.tech/stdcell/config_2x2.json`: 9,097,200 µm² available,
2940 × 2940 µm square envelope, 4.55× the 1x1 capacity) makes the two
area-rejected fixtures route for the first time and resolves the
placement-fit gate for `counter`, but closes nothing new. `alu8/area_abc`
needs 2906 × 4932 µm after row packing — taller than the 2x2 die band of
3080 µm — and fails with 154–178 overflowing connections; its primitive
cover (9,777,420 µm²) is still rejected by the 2x2 capacity before routing.
All six `ripple_adder8` candidates fit the envelope and fail with 49–80
overflowing connections. The wider frame re-packs the rows, so three
candidates gain and three lose the `power_connectivity` gate relative to the
1x1 runs. The limiting resource is therefore single-layer ML1/TiN routing
capacity and the allocated row geometry, not die area: closing these fixtures
needs cheaper wiring demand (smaller cells, better row/channel strategy), not
a larger die. The comparison remains an experimental core result, not
frame/pad, power-source, or I/O integration.

The remaining blockers are explicit:

* **Device and timing calibration:** the nominal BSIMSOI fit is only W=24 µm,
  L=6 µm at 25 °C. No measured geometry scaling, PVT/corner, body/back-gate,
  C–V, silicon, jitter, metastability, or qualified clock/timing library is
  available. Reported storage bounds and cell arcs are feasibility evidence,
  not fMAX or timing signoff.
* **Physical implementation qualification:** accepted cells and routed cores
  are checked against repository DRC/LVS/PEX/assembly decks, but no qualified
  placement site, row/abutment contract, density, CTS, clock network, PDN,
  IR-drop, EM, or application-scale routed sequential core exists. The
  sequential evidence is leaf-cell evidence; counter and shift-register
  routing did not close.
* **Frame, I/O, and power delivery:** the measured frame Boolean polygon is a
  geometric fit envelope only. There is no qualified pad-to-net assignment,
  VDD/VSS source path, M3–M4/M5 I/O acceptance, ESD qualification, or assembled
  frame/core DRC/LVS/PEX closure.
* **Library and process contract:** no qualified Liberty, CDL, Verilog, site,
  timing corners, or LibreLane/OpenROAD flow is shipped. Upper metals remain
  outside the canonical 1M contract, and SRAM is deferred.

 
The shared-diffusion gated latch and TG DFF separately passed both stored
states and 10/1000µs stopped clocks at nominal 3V/25°C, 10fF, 2ns slew with
explicit Gear integration. Their conservative tested setup/hold bounds are
16/−4ns and 8/2ns; DFF high/low pulse bounds are 8/16ns. Six DFF aperture
samples failed numerically and remain failed, never interpolated into passing
bounds. These leaf experiments are not routed counter/shift-register closure,
fMAX, PVT, metastability, retention qualification or measured model calibration.
This is an experimental cell-generation and model-validation path, not a
qualified standard-cell library or placed-core flow. Generated dimensions and
route lengths are geometric candidate metrics; they do not establish a
qualified row/site, abutment, placement density, clock/power network, timing
corner, Liberty/LEF contract, or process qualification. The only device-model
calibration is the documented nominal W=24 µm/L=6 µm point. No timing or
silicon-performance claim is made.

I/O design remains gated on M3–M4 standard-cell acceptance. The M5 I/O classes
and ESD validation states are reserved in `libs.tech/stdcell/config.json`, but
there is no qualified frame-to-pad map or ESD qualification; simplified ESD
wrappers and absolute-maximum voltages are not ESD ratings. SRAM is deferred.


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
| PEX regression fixtures | 17 tests cover sheet/contact R, MOS/RES/CAP/DIO ownership, physical-port mapping, distributed passive C, masks, fab labels, orphaned-gate rejection, and unqualified-coupling omission |
| Layered FEM matrix fixtures | `libs.tech/klayout/pex/test_fieldsolve_box.py` checks matrix symmetry, passivity, charge conservation, material sensitivity, and layer-boundary validation; test permittivities are synthetic |
| Pad LEF/DEF consistency | 28 fixed independent DEF pins match the frame GDS; LEF/Liberty remain physical-only |
| 2x2 frame derivative | `ICPS2023_5_fd_base_2x2` holds by construction and by read-back: 3200 × 3200 µm die, 50 µm ring, 56 60 µm pads at 200 µm pitch; 56 fixed DEF pins match the GDS; 0 DRC markers on the bare overlay in default and strict 1M profiles; the foundry ships no 2x2 frame GDS |
| 2x2 frame routing experiment | Same 16-cell bank and seven RTL fixtures routed inside the measured 2x2 frame (9,097,200 µm² available, 2940 µm envelope): the eligible set is unchanged (`tiny_and/primitive` at 116,644 µm², all three PDN variants), ALU8 and ripple-adder8 route for the first time and fail on 154–178 / 49–80 overflowing connections, and no new fixture closes |
| ngspice 47 ESD wrapper smoke | Both wrappers run with common `SUB`; expected zero-perimeter and RDSW warnings are nonfatal |
| ngspice 47 two-input NAND transient | Finite-slew states `00/10/01/11` at 1/6/14/21 ns: 3.000/3.019/3.012/0.121 V (the low state was still falling) |
| xschem symbol validator | 45 symbols validated; ESD pin order checked against ngspice `.subckt` definitions |
| Automated CI | `.github/workflows/ci.yml` runs process-profile, LVS, native-PCell/golden, PEX, pad-view, experimental stdcell and ALU contract, ngspice, xschem, and FEM regressions; LibreLane/OpenROAD remains excluded because the SCL config is partial and has no usable site or timing library. |
| Experimental stdcell pilot | H160 `AOI21_X1` (183 × 160 µm; 29,280 µm²): strict DRC/LVS/PEX and postlayout assembly passed; 8 truth rows and 5 model-based arcs passed. Not process or timing qualification. |
| LibreLane configuration checks | LibreLane 3.0.11 loads the partial SCL. A normal invocation stops at PDK validation with missing timing/site/drive/tie/buffer, PDN/RC/CTS, and `MAGIC_PDK_SETUP` inputs; an isolated Classic config substituting Magic-dependent and selected KLayout/signoff steps removes the Magic setup prerequisite from that check but still stops on the remaining PDK inputs. No floorplan, placement, or routing ran; native KLayout DRC/LVS/PEX remains the independent verification path. CI remains non-LibreLane. |
| ALU view and native route evidence | Accepted-cell ALU4 mapping/export and reference semantics were exercised; its earlier native-route attempts timed out without completed area evidence. New RTL fixtures cover ALU8 and ripple-adder8. Neither a mapped instance-area sum nor a timeout is promoted to routed-core area. |
| Sequential geometry pilot | Native sparse-channel TG/gated latch and DFF layouts passed strict DRC/LVS/PEX/assembly and real postlayout capture/hold without internal loads. Gated latch/DFF and TG DFF also passed nominal both-polarity setup/hold/pulse and 10/1000µs stopped-clock experiments. One TG-latch hold0ns sample failed ngspice convergence; no pass is claimed for that candidate. No row/application/PVT/metastability qualification follows. |

## Known limits

* **Model accuracy.** The active cards are a stage-1 BSIMSOI v4.4 fit to the
  local PDK PDF, not a bulk-card conversion or a silicon-calibrated model.
  Output-curve relative RMS residuals are 1.8–14.5%; transfer-curve
  log-current RMS is 0.17–0.69 decades. The PMOS transfer fit is poor. The
  fit covers only L=6 µm, W=24 µm at 25 °C and does not characterize body,
  back-gate, C–V, temperature, or geometry scaling. See
  `libs.tech/ngspice/README.md` and `libs.tech/ngspice/fit/`.
* **Automated CI.** `.github/workflows/ci.yml` runs the non-LibreLane
  verification suites with versions pinned by `flake.lock` and
  `requirements-fem.txt`. LibreLane/OpenROAD is deliberately excluded until
  cell characterization completes.
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
* **PEX limits.** Routine PEX allocates reported diffusion capacitance by
  bounded Voronoi area to resistance-network nodes on the same diffusion
  layer; it does not consume field-solver results or model layout-driven fringing,
  sidewall capacitance, or full distributed RC. Research-only FEM studies
  neither qualify the 2023 stack nor provide PEX coefficients. The layered
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

