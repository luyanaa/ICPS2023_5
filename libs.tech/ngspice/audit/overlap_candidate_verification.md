# Overlap candidate verification (b4soi_overlap_candidate.inc)

## R1 DC parity (12 frozen I-V curves, candidate library)

- all 12 frozen curves within baseline tolerances
- worst per-curve metric drift vs frozen baseline: 0

## R2 AC / charge consistency and PDF anchors

audit tool on candidate exit code: 0

| quantity | frozen | candidate | PDF anchor | predicted (linear) |
|---|---:|---:|---:|---:|
| NCH Cgd off (drive G->D) | 4.3 fF | 122.6 fF | 120.0 fF | 124.3 fF |
| NCH Cgg triode 3/0.05 | 339.3 fF | 576.0 fF | 571.2 fF | 579.3 fF |
| NCH Cgg sat 3/3 | 212.0 fF | 448.6 fF | 460.8 fF | 452.0 fF |
| PCH Cgd off (drive G->D) | 4.2 fF | 165.8 fF | 163.2 fF | 167.4 fF |
| PCH Cgg triode 3/0.05 | 338.7 fF | 661.8 fF | 657.6 fF | 665.1 fF |
| PCH Cgg sat 3/3 | 211.6 fF | 534.7 fF | 547.2 fF | 538.0 fF |

- KCL raw branch-charge residual, worst curve: 0.07301 fC (max |Qg| 1767 fC)
- candidate NMOS dQg/dV over 2.79->2.91 V (VDS=3): 451.3 fF vs AC Cgg mean over 2.75/3.0 V 448.7 fF (0.57% apart)

## R3 Switching charge vs VDS (quasi-static ramps)

| device | VDS | frozen | candidate | closed-form anchor | candidate/anchor |
|---|---:|---:|---:|---:|---:|
| nch | 0.05 V | 935 fC | 1645 fC | 1714 fC | -4.0% |
| nch | 1.00 V | 686 fC | 1400 fC | 1465 fC | -4.5% |
| nch | 3.00 V | 653 fC | 1378 fC | 1327 fC | +3.8% |
| pch | 0.05 V | 798 fC | 1767 fC | 1973 fC | -10.4% |
| pch | 1.00 V | 579 fC | 1544 fC | 1572 fC | -1.8% |
| pch | 3.00 V | 566 fC | 1527 fC | 1485 fC | +2.9% |

Anchor: PDK p.9 measured Cox and overlap densities in closed form (saturation 2/3·Cox·(VDD-VT)+2·Cov·VDD; triode (Cox+2Cov)·VDD; intermediate linear blend).  Pass: candidate within 15% of anchor and strictly above the frozen model.

## Verdict

- **R1_dc_parity**: PASS
- **R2_ac_charge**: PASS
- **R3_switching_charge**: PASS

Candidate: `/Users/yanlu/Documents/ICPS2023_5/libs.tech/ngspice/models/b4soi_overlap_candidate.inc` — charge-only; fitted DC parameter values unchanged. No nominal promotion or process qualification follows from R1–R3.
