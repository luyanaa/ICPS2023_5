# ICPS2023_5 ngspice models

The library has one nominal selector, `b4soi`. The two MOS model cards use
ngspice's BSIMSOI v4.4 implementation (`LEVEL=58`). No process corners are
provided or inferred.

```spice
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b4soi
.options temp=25 tnom=25
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
| `RDSW` | 0 | 46250 | effective curve fit; no separate MOS access-resistance data |
| `XJ` | 50 nm | 50 nm | independent simulation placeholder, not measured |

The process geometry uses `TOX=15 nm` from the PDK PDF and `TSI=95 nm`,
`TBOX=400 nm` from `libs.tech/klayout/xs/AIST-SOI-CMOS.xs`. `XJ=50 nm` is
independent of `TSI` and satisfies `XJ ≤ TSI`; it is not a PDK measurement.
`SOIMOD=0` selects the partially depleted model mode. The PDF does not provide
body/back-gate sweeps, C–V data, temperature sweeps, or geometry sweeps, so the
fit makes no claims about those dimensions of behavior or silicon calibration.

### Fit residuals

Output-curve relative RMS errors, in `|VGS|=1.5, 2, 2.5, 3 V` order:

| Device | Relative RMS by gate bias |
|---|---|
| NMOS | 12.1%, 1.8%, 6.2%, 8.2% |
| PMOS | 14.5%, 2.8%, 3.0%, 5.4% |

Transfer-curve RMS in `log10(|ID|)` units, in `|VDS|=0.05, 1 V` order:

| Device | Log-current RMS |
|---|---|
| NMOS | 0.17, 0.34 decades |
| PMOS | 0.56, 0.69 decades |

The PMOS transfer fit remains visibly poor; do not use these cards as
precision analog or signoff models. At `|VGS|=|VDS|=3 V`, the fitted model gives
1.321 mA NMOS and 0.281 mA PMOS. The p.7 table implies 1.500 mA and 0.3096 mA
for `W=24 µm`; the digitized graph endpoints are 1.521 mA and 0.3075 mA.
Graph digitization and table values disagree, especially for subthreshold
slope (p.7: 71/65 mV/dec; pp.10–11 graph: approximately 83/100 mV/dec for
N/P). Both the disagreement and the residuals are retained rather than hidden.

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
* Expected ngspice warnings remain for zero source/drain perimeter in the
  smoke card and the NMOS `RDSW=0` lower-bound fit. They are documented model
  assumptions, not suppressed.

ngspice writes `b4soiv1check.log` in the invoking directory; `*check.log` is
ignored by the repository. Run simulations from a scratch directory if the
checker log should not be left beside a netlist.
