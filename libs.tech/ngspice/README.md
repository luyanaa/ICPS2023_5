# ICPS2023_5 ngspice models

The `b4soi` selector selects the **nominal** card set for both polarities:
`models/icps2023_5_b4soi.inc` (ngspice BSIMSOI v4.4 `LEVEL=58`, `SOIMOD=0`, the
stage-1 fit). As with every other part of this PDK, it is a *traceable research
baseline*, not a signoff model, and no process corner is provided. Its
`NCH=1.7e17 cm^-3` inheritance and its transient pathology are both documented
below and are not hidden.

Alongside it, and **not** replacing it, are two version-scoped experimental
cards produced by `tools/fit_b4soi_bridge.py`:

| card | selector | `SOIMOD` | declared scope |
|---|---|---|---|
| `models/b4soi_fd_bridge_experimental.inc` | `b4soi_fd_bridge_experimental.lib.spice` | 2 (ideal-FD) | Xyce 7.10 / BSIMSOI 4.5.0, W24/L6, 3 V, 25 °C, 4-terminal D/G/S/E |
| `models/b4soi_unified_bridge_experimental.inc` | `b4soi_unified_bridge_experimental.lib.spice` | 1 (unified PD/FD) | same, plus ngspice-side DC use |

Both are experimental and neither is promoted to nominal. They exist because
the stage-1 card's low doping cannot be represented by the partially-depleted
formulation (see below), and they are kept **side by side**: the ideal-FD card
is the more stable numerical backend, but "more stable" is not evidence that
the real device is fully depleted, so the unified card is retained rather than
deleted on the strength of a convergence result.

```spice
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b4soi
* or, explicitly for the experimental backend:
* .lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/b4soi_fd_bridge_experimental.lib.spice" b4soi
.options temp=25 tnom=25
* Example common substrate reference, not a qualified bias prescription.
VSUB SUB 0 0
M1 d g s SUB nch W=24u L=6u
M2 d g s SUB pch W=24u L=6u
```

### Why the experimental cards exist, and why two of them

The experimental cards set `NCH=1.0e15 cm^-3` with explicit `NGATE=0`, so
`W_d,max = 863 nm >> TSI = 95 nm` and ngspice performs no band-bending `NCH`
reset in any selector mode. (The nominal stage-1 card's inherited
`NCH=1.7e17 cm^-3` *is* rewritten by ngspice to `1.46152e17 cm^-3` at
`SOIMOD=2`; that difference is used as a positive control in the verification
tool.)

With no neutral body region able to exist, the `SOIMOD=0` partially-depleted
premise is contradicted by the card's own doping, and that mismatch is also a
numerical pathology rather than a physics result: the same 120 ns T7 switch
needs **6.1e3** timesteps at `SOIMOD=2` and **more than 1e7** at `SOIMOD=0`
(over 80 MB of output, i.e. no completion inside a 60 s budget).

`SOIMOD=1` is not a safe substitute for `SOIMOD=2`: it is stable for some
parameter sets and stiff for others (7.8e3 versus over 1e7 timesteps on the same
bench), whereas mode 2 was stable for every parameter set measured. Modes 1 and
2 give identical DC here (transfer RMS 0.1931 versus 0.1932 decades), so the
stability costs no accuracy.

**`SOIMOD=2` is the ideal-FD limiting model, and that is a risk, not a
conclusion.** Using it is justified only by self-consistency with the card's own
low-doping premise. It does **not** establish any of the following, all of which
remain open:

1. whether the real Minimal Fab NMOS **and** PMOS operate fully depleted;
2. whether a neutral body region or a parasitic current path exists that the
   ideal-FD internal state cannot represent;
3. whether body/back-gate C–V from Xyce 4.5.0 matches the historical literature
   and future measurements;
4. whether simplifying the FD internal state happens to step around a real
   physical effect.

The unified `SOIMOD=1` card is therefore retained as a parallel candidate rather
than deleted for being less stable. The real device's depletion mode stays a TEG
question (AGENTS.md §1.2 and §6), and no mode is inferred from the process label
or from convergence ease.

### Qualification

| item | status |
|---|---|
| physical parameter checks (V0) | PASS |
| same-engine DC reproducibility in Xyce (V1a) | PASS — hard gate |
| cross-version DC equivalence (V1b) | **CROSS_VERSION_MODEL_DIVERGENCE** |
| PDK fidelity versus the stage-1 reference (V1c) | see the per-variant report |
| DC frozen regression (R1) | PASS |
| AC / charge closure (R2–R3) | PASS |
| Xyce dynamic, six benches | PASS |
| silicon FD model validation | **UNQUALIFIED** |

V1b's divergence is deliberately **not** closed by widening a tolerance: it
blocks using an ngspice-4.4 DC golden signature to accept Xyce 4.5.0, mixing the
two engines' outputs into one Monte Carlo sample set, and claiming both backends
implement an identical compact model without calibration. It does not block a
version-pinned experimental Xyce dynamic backend.

MOS node order is **D, G, S, E**. `E` is the external substrate terminal;
connect all devices to the intended common substrate node. B4SOI also accepts
an optional fifth `P` body terminal in a direct five-node card:

```spice
M3 d g s SUB body nch W=24u L=6u
```

The stock xschem symbols expose the four-node D/G/S/E form only. The fit is
anchored at the one measured geometry `L=6 µm`, `W=24 µm`; the xschem and PCell
defaults are not additional calibrated geometries. The library does not set
simulator-global options.

Direct fifth-P syntax is not a claim of physical body-contact support. The
current LVS deck compares the NMOS-only `NMOS4`/`NCH4` body-tie structure;
there is no PMOS4/PCH4 extraction path. PEX/postlayout assembly accepts
ordinary four-terminal N/P devices only. Do not convert the historical bulk
BSIM3 `B=S` approximation into a BSIMSOI `E=S` connection or invent a P contact.

## Model provenance: the bridge fit

`models/icps2023_5_b4soi.inc` is a fit to the local PDK electrical data; it is
not a conversion of the dated bulk-BSIM3 source cards in `models/legacy_*.inc`.
The source measurements are `libs.ref/doc/PDK_minimalFabSOICMOS.pdf`:

* p.7 gives threshold voltage, saturation current and subthreshold-slope table
  values at `L/W=6/24` and `Ta=25 °C`.
* pp.10–11 provide NMOS/PMOS output curves at `|VGS|=1.5, 2, 2.5, 3 V` and
  transfer curves at `|VDS|=0.05, 1 V`, for the same geometry.
* `fit/b4soi_stage1_iv_digitized.csv` records 459 graph samples, the calibrated
  axis coordinate, digitized current, pixel centroid/support, and source page.
  `fit/b4soi_stage1_iv.svg` overlays those PDF samples with ngspice simulations.

`tools/fit_b4soi_bridge.py` rebuilds the card under an explicit provenance
policy. Every parameter carries a `kind` — `fixed`, `derived`, `prior` or
`fitted` — and a source, and the card emits them as a comment block so the
provenance travels with the values. Only `fitted`/`prior` parameters move, each
inside a bound; `prior` bounds are documented windows *around* the
same-process-line bulk-BSIM3 surrogate value rather than the whole imaginable
range, because an unbounded knob becomes a numerical crutch. The report flags any
parameter found sitting on a bound.

| Parameter | NMOS | PMOS | Kind and basis |
|---|---:|---:|---|
| `NCH` | 1.0e15 cm⁻³ | 1.0e15 cm⁻³ | fixed; removes the bulk-BSIM3 `1.7e17` residue (AGENTS.md 4.1) |
| `NGATE` | 0 | 0 | fixed; TiN metal gate, no polysilicon depletion (AGENTS.md 2.3) |
| `SOIMOD` | 2 | 2 | fixed; ideal-FD, self-consistent with the NCH=1e15 premise (see above) |
| `K1` | 0.0791 | 0.0791 | **derived**: `sqrt(2·q·ε_Si·NCH)/Cox`, not fitted |
| `VTH0` | 0.2115 V | −0.4704 V | fitted inside a bounded window |
| `NFACTOR` | 5.210 | 1.878 | fitted; anchored to the p.7 slope by a deadband constraint |
| `VOFF` | −0.238 | −0.191 | prior (bulk-BSIM3 surrogate), bounded refit |
| `U0` | 545 | 90.6 | fitted / prior |
| `UA`, `UB`, `UC` | 1.42e−9, 5.87e−19, −9.96e−11 | −5.76e−10, 5.03e−19, −9.96e−11 | prior; bulk-BSIM3 mobility degradation |
| `VSAT` | ~1.0e6 m/s | ~1.3e6 m/s | prior; ±30 % surrogate window |
| `RDSW` | ≈0 | 193 Ω·µm | fitted; stage-1 used an unphysical 46250 |
| `PCLM`, `PDIBLC2`, `A0`, `AGS`, `KETA` | bulk-BSIM3 prior | bulk-BSIM3 prior | bounded refit (AGENTS.md 4.1) |
| `CGSO`, `CGDO` | 5 fF/µm | 6.8 fF/µm | fixed; PDK p.9 measured (AGENTS.md 2.4) |

The geometry is unchanged: `TOX=15 nm` from the PDK PDF; `TSI=95 nm` and
`TBOX=400 nm` from the July 2021 `AIST-SOI-CMOS.xs` drawing prior, not a
validated 2023 production-stack characterization; `XJ=50 nm` an independent
placeholder satisfying `XJ ≤ TSI`.

### Two documented conflicts with the p.7 table

Both are retained rather than hidden:

1. **PMOS threshold.** The p.7 table states `VTH0 = −0.71 V`, but the digitized
   pp.10–11 PMOS transfer curves turn on near `|VGS| = 0.45 V` and conduct
   1.1–2.1 decades above what `−0.71 V` permits at six points with high pixel
   support. Since the same-line bulk-BSIM3 `XVERIFIER` fit independently lands
   at `−0.4348 V`, the p.7 row is the outlier and `VTH0` is a bounded fit. The
   fitted `−0.4704 V` / `0.2115 V` sit within 0.04 V of that independent
   bulk-BSIM3 pair (`−0.4348 V` / `0.20809 V`).
2. **PMOS subthreshold slope.** The p.7 table states one slope per device
   (71/65 mV/dec), but the two digitized curves of a device differ by
   ~6 mV/dec in their own swing over the same deep-subthreshold window
   (NMOS 69.3 / 75.6; PMOS 65.9 / 75.7 mV/dec). A single slope cannot satisfy
   both; forcing the table value drives the transfer residual back above the
   0.3-decade target. The fit uses the table value as a deadband target and
   lands inside the measured spread.

### Observed residuals, bridge versus the superseded stage-1 card

Output-curve relative RMS, in `|VGS|=1.5, 2, 2.5, 3 V` order:

| Device | fd (SOIMOD=2) | unified (SOIMOD=1) | stage-1 |
|---|---|---|---|
| NMOS | 23.7 %, 2.0 %, 4.2 %, 5.6 % | 23.7 %, 2.1 %, 3.9 %, 5.5 % | 26.3 %, 2.8 %, 6.8 %, 9.8 % |
| PMOS | 12.8 %, 5.5 %, 5.6 %, 8.0 % | 10.8 %, 6.7 %, 6.3 %, 7.9 % | 17.2 %, 3.4 %, 3.9 %, 8.4 % |

Transfer-curve RMS in `log10(|ID|)` decades, in `|VDS|=0.05, 1 V` order:

| Device | fd (SOIMOD=2) | unified (SOIMOD=1) | stage-1 |
|---|---|---|---|
| NMOS | 0.200, 0.180 | 0.193, 0.174 | 0.252, 0.407 |
| PMOS | 0.294, 0.235 | 0.285, 0.233 | 0.736, 0.885 |

Saturation current, `|VDS|=|VGS|=3 V`, versus the p.7 target:

| Device | fd | unified | stage-1 | p.7 target |
|---|---:|---:|---:|---:|
| NMOS | 60.4 µA/µm (−3.3 %) | 60.4 µA/µm (−3.3 %) | 55.1 µA/µm (−13.1 %) | 62.5 µA/µm |
| PMOS | 12.3 µA/µm (−4.6 %) | 12.2 µA/µm (−5.3 %) | 11.7 µA/µm (−8.5 %) | 12.9 µA/µm |

The two variants fit the measured curves about equally well. The reason to
prefer fd as the *dynamic* backend is numerical, and the reason to keep unified
is that this numerical preference is not physical evidence (see above).
Concretely, the dynamic audit passes all six benches for **fd**, while the
**unified** card's T7 bench exceeds the audit's output budget — i.e. the unified
formulation, fitted to the same data, is not a usable dynamic backend for this
parameter set even though its DC results are equivalent. That asymmetry is
itself a reason to treat "fd is stable" as a property of one fit, not a
statement about the silicon.

Two trade-offs are deliberate and must not be read as free wins:

* PMOS output curves at `|VGS|=2.0/2.5 V` are worse than the stage-1 reference in
  **both** variants (V1c reports `REGRESSED_ON_2_CURVES`). Stage-1 bought that
  agreement with `RDSW=46250 Ω·µm` (≈2 kΩ of series resistance); a physical
  `~190 Ω·µm` cannot and should not reproduce it.
* The PMOS transfer fit is the AGENTS.md §4.1 acceptance target; those two output
  curves are the price paid for it.

This remains an unweighted digitized-point fit, not a silicon/signoff claim.
The `p.10–11` graphs and the `p.7` table disagree, and both are retained.

## Frozen fit regression

`fit/b4soi_stage1_baseline.json` freezes the nominal card's complete parameter
set, the digitized-CSV SHA-256, the expected selector, 12 curve identities, and
459 sample points. The `tools/regress_b4soi_stage1_iv.py` evaluator runs the
unchanged cards at `L=6 µm`, `W=24 µm`, `TEMP=TNOM=25 °C`; it never optimizes or
edits parameters. Each curve uses a 1 mV DC sweep with interpolation at the
digitized sample axes. Output-curve error is unweighted pointwise relative RMS;
transfer-curve error is unweighted RMS in `log10(|ID|)`. The default run fails if
the model, dataset, expected selector, or any per-curve metric drifts beyond its
frozen tolerance.

The file and tool keep their historical `stage1` names, but since 2026-10-17 the
nominal card is the verified bridge fit. The superseded stage-1 card and its
manifest are preserved verbatim as `models/stage1_b4soi_reference.inc`,
`stage1_b4soi_reference.lib.spice` and `fit/stage1_b4soi_baseline.json`; the
historical depletion and overlap audits point at those, so they keep their
original meaning (AGENTS.md 1.2's non-driftable reference point lives on).

```sh
nix develop /path/to/librelane --command \
  python3 libs.tech/ngspice/tools/regress_b4soi_stage1_iv.py
python3 libs.tech/ngspice/tools/test_b4soi_stage1_iv.py
```


## Passives and non-MOS devices

Resistor dimensions are metres at the SPICE call site. Values sourced from
`PDK_minimalFabSOICMOS.pdf` p.8 or the reference manual are distinct from legacy
LVS approximations:

| Subcircuit | Value | Provenance/limit |
|---|---:|---|
| `r_ndiff` | 180 Ω/sq | measured p.8 |
| `r_pdiff` | 1.2 kΩ/sq | measured p.8 |
| `r_tin` | 20 Ω/sq | measured p.8 |
| `r_ml1` | 0.20 Ω/sq | measured p.8 |
| `bridge`, `nbridge` | TiN 20 / N+ 180 Ω/sq | measured p.8 |
| `r_soi`, `hr_poly` | 250 MΩ/sq | legacy LVS approximation; not PEX data |
| `res_min`, `r_poly` | 180 Ω/sq | compatibility symbol mapping, not a distinct polysilicon layer |
| `cap_ndiff`, `cap_pdiff` | 2.3 fF/µm² | measured Cox, p.9 and p.18 |
| `cap_ov_n`, `cap_ov_p` | 5 / 6.8 fF/µm | measured p.9; explicit helper only |
| `cap_diff_sub` | 0.086 fF/µm² | measured diffusion density; return to `SUB` is an assumption |
| `cap_tin`, `mim_cap`, `cap_min` | 0.115 fF/µm² | legacy LVS rule; not a qualified PDK measurement |
| `dio_ndio`, `dio_pdio` | placeholder diode laws | no foundry diode I/V data supplied |

The PDK reports `CNdiff`/`CPdiff` density but does not identify the return
conductor. `cap_diff_sub` therefore requires the user to connect its second
terminal to `SUB`; it is not evidence of measured diffusion-to-BOX coupling.
The overlap helpers do not define a validated layout perimeter model and are
not used by the KLayout PEX deck. `r_soi`, `cap_tin`, `mim_cap`, and the diode
models are compatibility-level circuit helpers, not fitted B4SOI parameters.

The ESD wrappers expose `nmos_esd PAD VSS SUB` and
`pmos_esd PAD VDD SUB`; `SUB` is the shared B4SOI E terminal. Their xschem
symbols expose the same third `SUB` pin. They are compatibility clamps built
from the documented contact resistances and fitted MOS cards, not qualified
ESD protection models.

## LVS/PEX assembly

`tools/assemble_postlayout.py` combines one KLayout LVS extraction, the paired
PEX network, and this library's `b4soi` cards. The LVS reference must declare
`.GLOBAL SUB`; extracted `* net` comments preserve the logical pin identities.
The assembler binds each top-level pin only through one unique ML1 `PORT_MAP`
anchor and binds every LVS MOS D/G/S through its matching `MOS_MAP` geometry
record. It emits PEX R/C plus LVS MOS geometry, with a separate `E_SUB` formal
for BSIMSOI E and the PEX diffusion-C return. This formal is distinct
from a physical top-level pin and is connected by the caller.

**Explicit contact resistance (AGENTS.md 4.2)**: every MOS D/S terminal gets a
lumped `RCNT` resistor `R_cnt = R_unit / N_cnt` between its PEX node and a
private internal MOS terminal node (`M0001_DRAIN`, ...), with `R_unit` the
p.8 measured per-opening resistance (N+ 150 ohm, P+ 200 ohm) and `N_cnt` the
parallel CNT cut count PEX reports in `MOS_MAP` (`drain_contact_cuts` /
`source_contact_cuts`, default 1 for older PEX outputs). The gate side is not
repeated here: its Al-TiN contact is PEX-owned (`CNT_TIN` via model). The
core MOS keeps its fitted `RDSW`; the physical contact term is now visible
and editable in the assembled deck instead of hiding inside it. Note that
additional (non-terminal) cuts on the same diffusion edge remain PEX-owned
with their own area-scaled via resistance; `N_cnt` counts actual openings
per terminal side, so such layouts are conservative (lower total access R)
until the LVS/PEX ownership boundary is revisited.

```sh
python3 libs.tech/ngspice/tools/assemble_postlayout.py \
  --lvs inverter_min_fit.extracted.cir \
  --pex inverter_min_fit.pex.spice \
  --schematic inverter_min_fit.cir \
  --top inverter_min_fit \
  --output inverter_min_fit.postlayout.spice \
  --require-multiple-terminals
```

The assembler checks MOS geometry/polarity, terminal contacts, source-tied
LVS MOS4 body **serialization artifacts**, one physical component per logical
LVS net, no resistive component joining distinct LVS nets, and no unanchored
PEX component. That source-tied fourth node is not the BSIMSOI E terminal:
every assembled MOS gets the separate `E_SUB` formal. The assembler supports
ordinary MOS-only inverter/NAND extraction, not NMOS4/PCH4 body contacts or
arbitrary LVS elements.

Run the native integration regression inside the KLayout/ngspice development
shell:

```sh
python3 libs.tech/klayout/pcells/test_1m_golden.py
```

It runs DRC/LVS/PEX at legal W=24 µm, L=6 µm native-PCell geometry, assembles
both circuits, exercises open/short/contact/floating rejections, and compares
pre-/post-layout transient logic states, threshold delays, and peak supply
current. The bench uses 3 V, 25 °C, a 5 fF output load, 2 ns input edges, and
ngspice 47. These simulation conditions are regression stimuli, not additional
process measurements or a model/signoff qualification.

## Verification

The bridge candidate passed five gates before promotion; the full report is
[`audit/b4soi_bridge_candidate_verification.md`](audit/b4soi_bridge_candidate_verification.md):

| gate | what it checks | result |
|---|---|---|
| V0 | physical self-consistency and removal of the `NCH` truncation, with the stage-1 card as a positive control that the detector can fail | PASS |
| V1 | every one of the 12 curves re-evaluated in Xyce BSIMSOI 4.5.0 | PASS (worst 0.008 %) |
| R1 | the 12 metrics reproduce the frozen fit manifest | PASS |
| R2 | AC/charge closure and the PDK p.9 capacitance anchors | PASS (1.006–1.021) |
| R3 | quasi-static switching charge versus the closed-form p.9 anchor, integrated in **Xyce** | PASS (0.93–1.02) |

Dynamic acceptance and its own three-dimensional criterion (port currents, node
charge integrals and time-step continuity, with an explicit false-quiet-tail
test) are in [`audit/b4soi_bridge_dynamic.md`](audit/b4soi_bridge_dynamic.md).

With ngspice 47, the selected library was loaded from `/tmp` using the `.lib`
selector and exercised at `TEMP=TNOM=25 °C`:

* Four-terminal NMOS `M D G S E nch` at `VDS=VGS=3 V` produced the fitted
  current; `showmod` reports `NCH=1e15 cm^-3` in `SOIMOD=0/1/2`, i.e. the
  simulator applies no `NCH` rewrite.
* Five-terminal NMOS and PMOS `M D G S E P model` cards parsed and ran with
  `E=SUB` and `P` tied to the source. No unrecognized model parameters or fatal
  errors occurred.
* `nmos_esd PAD VSS SUB` and `pmos_esd PAD VDD SUB` loaded and ran in an
  ngspice smoke deck with one explicitly driven common `SUB`; no clamp
  characterization is implied.

The native legal-1M W=24 µm/L=6 µm inverter and NAND integration run also
completed DRC with zero markers, strict LVS, PEX, assembly, and finite-slew
transients in both reference and post-layout forms. The delay/current table
below was measured on the **superseded stage-1 card** and is retained as its
record; the bridge card's timing has not been re-characterized, and the golden
test asserts only pre/post-layout self-consistency (states, positive delays,
current ratio), never these absolute values:

| Circuit | tPHL pre/post (ns) | tPLH pre/post (ns) | Peak current pre/post (mA) |
|---|---:|---:|---:|
| Inverter | 0.650 / 0.695 | 1.796 / 1.942 | 3.335 / 3.408 |
| NAND2 | 1.041 / 1.109 | 5.366 / 5.654 | 10.891 / 6.020 |

Delays use 1.5 V input/output crossings; current is the maximum drawn from the
ideal 3 V source. The pre-layout deck uses the same LVS-extracted MOS
geometry/parameters without PEX R/C. These results verify this geometry and
bench only; they do not validate general model accuracy, power delivery, or
silicon timing.
* Expected ngspice warnings remain for zero source/drain perimeter in the
  smoke card and the NMOS `RDSW≈0` lower-bound fit. They are documented model
  assumptions, not suppressed.

ngspice writes `b4soiv1check.log` in the invoking directory; `*check.log` is
ignored by the repository. Run simulations from a scratch directory if the
checker log should not be left beside a netlist.

## Depletion-mode audit: reference versus candidates

This audit is a **historical, stage-1-based** study. Since 2026-10-17 it runs
against the preserved stage-1 reference card and its own frozen manifest
(`models/stage1_b4soi_reference.inc`,
`fit/stage1_b4soi_baseline.json`), not against the new nominal, so its original
findings and counts stay reproducible.

See [`audit/b4soi_depletion_mode.md`](audit/b4soi_depletion_mode.md) and its
[`JSON snapshot`](audit/b4soi_depletion_mode.json) for the process-evidence
hierarchy, endpoint trace, F0–F5 boundaries, and per-case numerical results.
Neither Technology 2018 nor the 2022 FD/PD NMOS comparison assigns a
depletion mode to both polarities of this process revision.

```sh
python3 libs.tech/ngspice/tools/audit_b4soi_depletion_static.py \
  --ngspice /path/to/ngspice --out build/ngspice-depletion
python3 libs.tech/ngspice/tools/audit_b4soi_depletion_dynamic.py \
  --ngspice /path/to/ngspice --out build/ngspice-depletion
python3 libs.tech/ngspice/tools/test_b4soi_depletion.py
```

Both audit commands generate explicitly selected libraries under the output
directory. Each library preserves the frozen or existing overlap base and
changes only the independently chosen N/P SOIMOD values. No candidate is
included by `icps2023_5.lib.spice`. FD channel doping, flatband/back-gate and
body parameters remain inherited assumptions, not process measurements.
In mode 2, ngspice warns that inherited NCH=1.7e17 cm⁻³ and TSI=95 nm are
incompatible with its band-bending limit, then resets NCH to 1.46152e17 cm⁻³.
The snapshot records the exposed effective value, not just the card-input delta.

The commands retain every deck/log/data file and report `observed`, `failed`
and `inconclusive` separately. Exit 0 means that evidence collection completed,
not that every bench passed or that a model is qualified. Full `showmod`
query failures are separate from finite operating-point/model-load results.
The new ramp audit has no hidden convergence retry ladder.
The recorded matrix retains 3/24 gate-ramp aborts and 21/216 transient aborts;
all 72 offline substrate sweeps complete. Quiet voltage tails do not qualify
charge/current behavior, physical depletion, or reliability.

F4 substrate sweeps are offline mathematical sensitivity only: the repository
does not contain manufacturer-approved E/source/body reliability limits.
Hardware discrimination and F5 silicon fitting remain blocked by those limits
and revision-specific, polarity-resolved body-bias/C–V/process data.

Historical transient reports are not interchangeable with the corrected
common-E benches: `diagnose_b4soi_transient.inv_deck` used PMOS E=VDD and
emitted a fifth PMOS node even in its `body=e` branch;
`audit_b4soi_switching.inverter_deck` also used PMOS E=VDD. Some old T7 decks
in `diagnose_b4soi_cv_leads.py`/`diagnose_b4soi_feasibility.py` did not drive
SUB explicitly. Those artifacts are retained as historical numerical studies,
not proof of the present four-terminal, common-substrate topology.
