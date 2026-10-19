# NMOS4 explicit-P numerical resolution audit

Status: **MODEL_CAPABILITY_ONLY_NOT_SILICON_QUALIFIED** (NUMERICALLY_RESOLVED).

Unified bridge card, ngspice BSIMSOI 4.4, W20/L6, VD=1 V, VG=1.5 V, VP=-1 V.

| quantity | value |
|---|---:|
| g_mb,P = dID/dVP | 2.1924500003033323e-09 A/V |
| g_m = dID/dVG | 0.00034129102144 A/V |
| g_E = dID/dVE | -1.7160000000000222e-05 A/V |
| -g_mb,P/g_m (equivalent dVth/dVP) | -6.423989682039647e-06 |
| assembled-network g_mb,P | 1.7706599999157274e-09 A/V |
| tightened-tolerance g_mb,P | 2.1924500003033323e-09 A/V |
| Qb at VP0 | -8.3497376955e-14 C |

## Sweep matrix

| run | points | ID min (A) | ID max (A) | slope (A/V) | status |
|---|---:|---:|---:|---:|---|
| default | 31 | 0.000275281 | 0.000275288 | 1.85828e-09 | observed |
| reltol_1e-4 | 31 | 0.000275281 | 0.000275288 | 1.85828e-09 | observed |
| reltol_1e-6 | 31 | 0.000275281 | 0.000275289 | 1.91018e-09 | observed |
| reltol_1e-8 | 31 | 0.000275281 | 0.000275289 | 1.94962e-09 | observed |
| reverse_default | 31 | 0.000275281 | 0.000275289 | 1.88514e-09 | observed |

Resolution criterion: same sign and spread <= 25% of max |slope|. Same sign: True; relative spread: 0.1923829507305749.

## Limitations

* Local small-signal ratios at one bias point; not thresholds extracted from measured transfer curves.
* Unified card parameters were fitted to zero-body-bias I-V data; a small gmb may reflect the fit or the equation, and the audit does not separate them by refitting.
* Direct vs assembled compares one contact network; it does not characterize the physical N_CH body path.
* Model capability only; no silicon body-bias or FD/PD conclusion.
