# Cross-model DUT/load timing experiment (VDD=3 V, 2 ns, real FO1)

Simulator: `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` (Xyce NF 7.10.0, BSIMSOI 4.5.0).
DUT and load stages have SEPARATE supply branches at the same voltage, so
the energies below are per stage, split into falling edge, rising edge,
full period and static tail.

`overlap` = the stage-1 DC card plus only the PDK p.9 measured CGSO/CGDO.
It is the counterfactual that separates the overlap-charge contribution
from the device-parameter contribution. The separation is an engineering
attribution from controlled counterfactuals, not a strict linear
decomposition.

## Delays and level restoration

| DUT | load | VOH (V) | VOL (V) | rails | tPHL (ns) | tPLH (ns) | tFALL (ns) | tRISE (ns) |
|---|---|---:|---:|---|---:|---:|---:|---:|
| stage1 | stage1 | 3.000 | 0.000 | ok | 1.590 | 3.704 | 2.974 | 7.099 |
| fd | fd | 3.000 | 0.000 | ok | 3.785 | 8.965 | 9.167 | 17.302 |
| fd | stage1 | 3.000 | 0.000 | ok | 2.844 | 6.114 | 3.834 | 8.086 |
| stage1 | fd | 3.000 | 0.000 | ok | 2.571 | 7.004 | 8.791 | 17.798 |
| overlap | stage1 | 3.000 | 0.000 | ok | 2.991 | 7.002 | 4.322 | 10.020 |
| overlap | overlap | 3.000 | 0.000 | ok | 4.026 | 9.918 | 9.349 | 19.420 |

## Energy attribution per stage

| DUT | load | DUT E full (pJ) | DUT E fall (pJ) | DUT E rise (pJ) | DUT static (uW) | load E full (pJ) | load E fall (pJ) | load E rise (pJ) | load static (uW) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| stage1 | stage1 | 10.18 | 0.25 | 12.23 | 0.00 | 2.87 | 6.05 | 3.17 | 0.00 |
| fd | fd | 30.02 | 0.38 | 33.92 | 0.10 | 8.61 | 16.70 | 7.43 | 0.02 |
| fd | stage1 | 15.86 | 0.74 | 20.25 | 0.11 | 3.13 | 6.75 | 3.57 | 0.09 |
| stage1 | fd | 23.66 | 0.31 | 25.42 | 0.00 | 8.58 | 15.38 | 6.53 | 0.00 |
| overlap | stage1 | 16.37 | 0.01 | 20.36 | 0.00 | 3.33 | 6.69 | 3.32 | 0.00 |
| overlap | overlap | 29.19 | 0.16 | 32.85 | 0.00 | 8.75 | 16.32 | 7.02 | 0.00 |

## Per-device terminal charge closure (full period)

| DUT | load | NMOS closure (aC) | PMOS closure (aC) | steps |
|---|---|---:|---:|---:|
| stage1 | stage1 | 0.0007 | 0.0006 | 20123 |
| fd | fd | 0.0000 | 0.0009 | 20031 |
| fd | stage1 | 0.0016 | 0.0015 | 20753 |
| stage1 | fd | 0.0011 | 0.0009 | 20029 |
| overlap | stage1 | 0.0007 | 0.0007 | 20205 |
| overlap | overlap | 0.0001 | 0.0001 | 20054 |

## Attribution, read off this table and nothing else

1. `fd/fd` versus `stage1/stage1` is the headline difference. It stays a
   whole-model circuit difference.
2. `fd/stage1` versus `stage1/stage1` isolates the **drive** change at an
   unchanged load input capacitance.
3. `stage1/fd` versus `stage1/stage1` isolates the **input-capacitance**
   change at an unchanged drive.
4. `overlap/stage1` versus `stage1/stage1` isolates the **overlap charge**
   alone, holding the DC card and SOIMOD=0 fixed. If adding the measured
   p.9 overlap already slows the cell substantially, then part of the
   fd-card slowdown represents real parasitic burden that the stage-1
   timing was implicitly understating, not a modelling regression.
5. The symmetric-versus-asymmetric split matters: if tPHL and tPLH move
   together, the dominant term is capacitive; if only one moves, the
   dominant term is that device's drive.

No PMOS RDSW/U0 refit should be attempted until this table says whether
the residual is drive or capacitance. These numbers are experimental and
version-tagged; they are not a signoff Liberty and no fMAX or yield claim
follows from them.

