# Effective-parameter audit behind V1b: fd card (SOIMOD=2)

Card: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc`
Purpose: decide whether the V1b cross-version divergence may be attributed
to genuine BSIMSOI 4.4-vs-4.5.0 differences, or whether an unintended
effective-parameter reset has to be excluded first.

## ngspice 4.4 effective values (`showmod`)

| parameter | NMOS | PMOS |
|---|---:|---:|
| NCH | 1e+15 | 1e+15 |
| NSUB | 6e+16 | 6e+16 |
| NGATE | 0 | 0 |
| SOIMOD | 2 | 2 |
| FDMOD | 0 | 0 |
| TOX | 1.5e-08 | 1.5e-08 |
| TSI | 9.5e-08 | 9.5e-08 |
| TBOX | 4e-07 | 4e-07 |
| K1 | 0.0791435 | 0.0791435 |
| K2 | 0 | 0 |
| VTH0 | 0.171875 | -0.48181 |
| U0 | 513.75 | 93.9388 |
| UA | 1.20833e-09 | -5.7647e-10 |
| UB | 5.87e-19 | 5.0269e-19 |
| UC | -9.9625e-11 | -6.91667e-12 |
| VSAT | 1.03997e+06 | 1.29995e+06 |
| RDSW | 4.61853e-14 | 197.318 |
| PCLM | 3.8234 | 11.7314 |
| PDIBLC1 | 0.39 | 0.39 |
| PDIBLC2 | 0.00156843 | 1.0757e-06 |
| A0 | 1.03339 | 0.72732 |
| AGS | 0.2656 | 0.1971 |
| KETA | -0.0599948 | -0.057 |
| NFACTOR | 5.52141 | 1.96224 |
| VOFF | -0.19159 | -0.216641 |
| K1B | 1 | 1 |
| K2B | 0 | 0 |
| VBSA | 0 | 0 |
| LPE0 | 1.74e-07 | 1.74e-07 |
| CGEO | 0 | 0 |

## Xyce 4.5.0 effective values

**Not observable in this build.** Every probed name (31) is
rejected as an undefined symbol, so Xyce 4.5.0 does not publish
BSIMSOI model internals through the device-parameter interface
here. The Xyce-side effective values therefore cannot be compared
field by field.

## Port preservation check

- source vs ported lines differing outside the LEVEL token: 0
- conclusion: the Xyce model card is the ngspice card with the LEVEL token substituted, so the divergence is not a transcription reset.

## Measured DC currents at matched bias points

| device | VGS | VDS | ngspice 4.4 (A) | Xyce 4.5.0 (A) | delta |
|---|---:|---:|---:|---:|---:|
| nch | +3.0 | +3.0 | 1.45052e-03 | 1.52332e-03 | +5.02% |
| nch | +3.0 | +0.5 | 4.68585e-04 | 4.90360e-04 | +4.65% |
| nch | +1.5 | +3.0 | n/a | n/a | n/a |
| pch | -3.0 | -3.0 | n/a | n/a | n/a |
| pch | -3.0 | -0.5 | n/a | n/a | n/a |
| pch | -1.5 | -3.0 | n/a | n/a | n/a |

## Attribution status

- The divergence IS reproducible and bounded by the measurements above.
- It is NOT yet attributed: with the Xyce-side effective parameters
  unobservable in this build, a silent default/reset cannot be excluded
  field by field. Until a future Xyce build or a `.OP` file exposes them,
  treat the divergence as **attributed-by-inference only**, and keep V1b's
  restrictions (no mixing engines in one sample set, no cross-engine golden
  acceptance) in force.

