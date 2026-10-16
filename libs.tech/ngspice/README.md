# ICPS2023_5 ngspice models

The compatibility selector `b4soi` retains the frozen stage-1 **PD reference**
cards (`SOIMOD=0`, ngspice BSIMSOI v4.4 `LEVEL=58`). This simulator selection
does not establish that either ICPS2023_5 NMOS or PMOS is physically partially
depleted. No process corners or polarity-specific depletion assignment are
provided or inferred.

```spice
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b4soi
.options temp=25 tnom=25
* Example common substrate reference, not a qualified bias prescription.
VSUB SUB 0 0
M1 d g s SUB nch W=24u L=6u
M2 d g s SUB pch W=24u L=6u
```

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

## Stage-1 fit and data provenance

`models/icps2023_5_b4soi.inc` is a new fit to the local PDK electrical data; it
is not a conversion of the dated bulk-BSIM3 source cards in `models/legacy_*.inc`.
The source measurements are `libs.ref/doc/PDK_minimalFabSOICMOS.pdf`:

* p.7 gives `VTH0` targets at `L/W=6/24`, `VDS=±0.05 V`, and saturation current
  and subthreshold-slope table values at `Ta=25 °C`.
* pp.10–11 provide NMOS/PMOS output curves at `|VGS|=1.5, 2, 2.5, 3 V` and
  transfer curves at `|VDS|=0.05, 1 V`, for the same geometry.
* `fit/b4soi_stage1_iv_digitized.csv` records 459 graph samples, the calibrated
  axis coordinate, digitized current, pixel centroid/support, and source page.
  `fit/b4soi_stage1_iv.svg` overlays those PDF samples with ngspice simulations.

`VTH0` is anchored to the p.7 table. `NFACTOR` follows the transfer-graph
subthreshold slopes; `U0`, `K1`, and `RDSW` are effective fit parameters from
the joint output/transfer curves, not independent physical measurements. The
fit fixes `VTH0`, sets `NFACTOR` from the digitized graph slope, then searches
`U0`, `K1`, and `RDSW` with a 0.25 transfer-term weight. `K2=0` is explicit:
there are no body-bias curves to constrain it. Unlisted B4SOI parameters retain
ngspice defaults.

| Parameter | NMOS | PMOS | Basis |
|---|---:|---:|---|
| `VTH0` | 0.25 V | −0.71 V | direct p.7 table values |
| `U0` | 670 | 268.75 | effective curve fit |
| `NFACTOR` | 1.38 | 1.67 | p.10–11 graph slope |
| `K1` | 0.125 | 0.14375 | effective curve fit; no body-bias sweep |
| `K2` | 0 | 0 | explicit unfit assumption |
| `RDSW` | 0 | 46250 | provisional effective fit; separate MOS access resistance is unmeasured |
| `XJ` | 50 nm | 50 nm | independent simulation placeholder, not measured |

The process geometry uses `TOX=15 nm` from the 2023 PDK PDF. `TSI=95 nm` and
`TBOX=400 nm` are initial values from the July 2021 `AIST-SOI-CMOS.xs` prior,
not a validated 2023 production-stack characterization. `XJ=50 nm` is
independent of `TSI` and satisfies `XJ ≤ TSI`; it is not a PDK measurement.
`SOIMOD=0` selects the partially depleted equations and remains the frozen
reference. The isolated depletion audit compares unified `SOIMOD=1` and
ideal-FD `SOIMOD=2` separately for NMOS and PMOS. These are uncalibrated,
SOIMOD-only sensitivities, not migrated FD fits. Unified mode 1 is the next
investigation path; ideal-FD mode 2 is a limiting control, not an automatic
replacement. No mode 3 or nominal promotion is implied.

### Frozen fit regression

`fit/b4soi_stage1_baseline.json` freezes both complete model cards, the
digitized-CSV SHA-256, 12 curve identities, and 459 sample points. The
`tools/regress_b4soi_stage1_iv.py` evaluator runs the unchanged cards at
`L=6 µm`, `W=24 µm`, `TEMP=TNOM=25 °C`; it never optimizes or edits parameters.
Each curve uses a 1 mV DC sweep with interpolation at the digitized sample
axes. Output-curve error is unweighted pointwise relative RMS; transfer-curve
error is unweighted RMS in `log10(|ID|)`. The default run fails if the model,
dataset, or any per-curve metric drifts beyond its frozen tolerance.

```sh
nix develop /path/to/librelane --command \
  python3 libs.tech/ngspice/tools/regress_b4soi_stage1_iv.py
python3 libs.tech/ngspice/tools/test_b4soi_stage1_iv.py
```

Observed baseline output-curve relative RMS errors, in `|VGS|=1.5, 2, 2.5,
3 V` order:

| Device | Relative RMS by gate bias |
|---|---|
| NMOS | 26.3%, 2.8%, 6.8%, 9.8% |
| PMOS | 17.2%, 3.4%, 3.9%, 8.4% |

Transfer-curve RMS in `log10(|ID|)` units, in `|VDS|=0.05, 1 V` order:

| Device | Log-current RMS |
|---|---|
| NMOS | 0.252, 0.407 decades |
| PMOS | 0.736, 0.885 decades |

This unweighted digitized-point regression is an audit baseline, not a
refitting objective or a silicon/signoff accuracy claim. The PMOS transfer
fit remains visibly poor. The PDK table and graph digitization disagree;
both are retained rather than hidden.

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

With ngspice 47, the selected library was loaded from `/tmp` using the `.lib`
selector and exercised at `TEMP=TNOM=25 °C`:

* Four-terminal NMOS `M D G S E nch` produced `ID=1.32119 mA` at
  `VDS=VGS=3 V`, matching the reported fit sweep.
* Five-terminal NMOS and PMOS `M D G S E P model` cards parsed and ran with
  `E=SUB` and `P` tied to the source. No unrecognized model parameters or fatal
  errors occurred.
* `nmos_esd PAD VSS SUB` and `pmos_esd PAD VDD SUB` loaded and ran in an
  ngspice smoke deck with one explicitly driven common `SUB`; no clamp
  characterization is implied.
* A two-input NAND using the fitted `W=10 µm` NMOS and `W=40 µm` PMOS cards,
  `L=6 µm`, explicit `SUB=0`, and a 5 fF output load passed a finite-slew
  transient. Samples at input states `00/10/01/11` at 1/6/14/21 ns were
  3.000/3.019/3.012/0.121 V; the low state was still discharging at 21 ns.

The native legal-1M W=24 µm/L=6 µm inverter and NAND integration run also
completed DRC with zero markers, strict LVS, PEX, assembly, and finite-slew
transients in both reference and post-layout forms. With a 3 V supply,
25 °C, 5 fF load, and 2 ns input edges, the observed delay and peak supply
current were:

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
  smoke card and the NMOS `RDSW=0` lower-bound fit. They are documented model
  assumptions, not suppressed.

ngspice writes `b4soiv1check.log` in the invoking directory; `*check.log` is
ignored by the repository. Run simulations from a scratch directory if the
checker log should not be left beside a netlist.

## Depletion-mode audit: reference versus candidates

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
