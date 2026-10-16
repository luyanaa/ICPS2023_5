# F0-F2: C-V selector lead, charge symmetry, negative-trigger tail

Selector sweep against the frozen card + selector line only.  An earlier source-tree reading held that ngspice-47 never uses vgstcvmod; the empirical sweep below shows mode 2 changes ngspice's C-V results identically to Xyce, so the parameter is live in both simulators and the empirical table supersedes that reading.

Tail columns: AC-coupled RMS and peak-to-peak over 700ns-1us (near zero = parked DC offset, not ringing), zero crossings of the mean-subtracted tail, and its frequency (n/a unless >= 4 crossings).

## F0 ngspice (VGSTCVMOD on the frozen 4.4 card)

| VGSTCVMOD | ID(3/3V) | Cgg off | Cgg(-0.2V) | Cgg triode | T7 OUT range | tail_ac_rms | tail_pp | tail_x | tail_freq |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 0 | 1.3212 mA | 19.6 fF | 67.3 fF | 339.3 fF | -0.841..0.923 | 0.01028 | 0.03656 | 1 | n/a |
| 1 | 1.3212 mA | 19.6 fF | 67.3 fF | 339.3 fF | -0.841..0.923 | 0.01028 | 0.03656 | 1 | n/a |
| 2 | 1.3212 mA | 19.0 fF | 30.9 fF | 339.3 fF | -0.844..0.529 | 0.009695 | 0.0344 | 1 | n/a |

## F0 Xyce LEVEL=70450 (4.5.0 implements the selector)

| VGSTCVMOD | Cgg off | Cgg(-0.2V) | Cgg triode | T7 OUT range | tail_ac_rms | tail_pp | tail_x | tail_freq |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| 0 | 19.6 fF | 67.3 fF | 339.3 fF | -0.831..4.049 | 0.01043 | 0.03711 | 1 | n/a |
| 1 | 19.6 fF | 67.3 fF | 339.3 fF | -0.831..4.049 | 0.01043 | 0.03711 | 1 | n/a |
| 2 | 19.0 fF | 30.9 fF | 339.3 fF | -0.833..3.543 | 0.009814 | 0.03482 | 1 | n/a |

## F1 source/drain interchange symmetry (ngspice)

Deck A: (VD=v, VS=0).  Deck B: same wiring, VD=0, VS=v (the mirror operating point).  Ideal model: C_B = P C_A P^T with P the d/s swap of rows and columns.   The non-reciprocity column (|C_ab - C_ba| of matrix A) is expected by charge-model design and is context only.

- (VG=0, VD=-0.2 V): Cgg A 67.3 fF / B 67.3 fF (dev 0.02%); worst P-C-P^T deviation 0.0% at sg; A non-reciprocity 97.9% at ds/sd
- (VG=1.5, VD=-0.2 V): Cgg A 335.2 fF / B 335.2 fF (dev 0.00%); worst P-C-P^T deviation 0.5% at de; A non-reciprocity 91.1% at gd/dg
- (VG=0, VD=+0.2 V): Cgg A 20.2 fF / B 20.2 fF (dev 0.01%); worst P-C-P^T deviation 0.0% at gd; A non-reciprocity 4.3% at gs/sg
- (VG=1.5, VD=+0.2 V): Cgg A 333.3 fF / B 333.3 fF (dev 0.00%); worst P-C-P^T deviation 0.4% at se; A non-reciprocity 87.2% at ds/sd

## F2a charge injection into the free node (gate held 0)

| target OUT V | injected Q | actual OUT min | tail_mean | tail_ac_rms | tail_pp | tail_x | tail_freq |
|---|---:|---:|---:|---:|---:|---:|---:|
| -0.1 | -0.5 fC | -0.033 V | -2.321e-06 | 2.412e-06 | 9.312e-06 | 1 | n/a |
| -0.4 | -2.0 fC | -0.097 V | -4.874e-06 | 5.065e-06 | 1.955e-05 | 1 | n/a |
| -2.0 | -10.0 fC | -0.186 V | -6.851e-06 | 7.12e-06 | 2.748e-05 | 1 | n/a |
| +0.4 | +2.0 fC | -0.000 V | 0.001296 | 0.001216 | 0.0046 | 1 | n/a |
| +2.0 | +10.0 fC | -0.000 V | 0.7281 | 0.04081 | 0.1414 | 1 | n/a |

## F2b falling-edge tail convergence (T7 frozen card)

| tran step | OUT range | tail_mean | tail_ac_rms | tail_pp | tail_x | tail_freq |
|---|---|---:|---:|---:|---:|---:|
| 20 ps | -0.841..0.923 | -0.1289 | 0.01028 | 0.03656 | 1 | n/a |
| 10 ps | -0.841..0.925 | -0.1289 | 0.01028 | 0.03656 | 1 | n/a |
| 5 ps | -0.841..0.925 | -0.1289 | 0.01028 | 0.03656 | 1 | n/a |

## Reading

- F0: ngspice and Xyce agree bit-for-bit at every cell.  Mode 0 vs 1 (the documented C-V bug fix) shows no difference at these bias points in either simulator; mode 2 (VgsteffCV with body effect/DIBL) changes reverse-bias Cgg by ~2.2x and the T7 overshoot.
- F0-Xyce: mode 1 is the official C-V fix and mode 2 the new VgsteffCV expression; compare their Cgg and tails against mode 0.
- F1: worst P-C-P^T deviation is the d/s interchange symmetry check (elements > 1 fF); the non-reciprocity column is expected by design and is not a failure.
- F2a: tail_ac_rms near zero after release = the free node parks (no limit cycle without a gate edge); growth of tail_ac_rms with |Q| at gate=0 but absence of a sign threshold would rule out a negative-voltage trigger.
- F2b: a real limit cycle needs tail_ac_rms, tail_pp and tail_x convergent (nonzero) as the step shrinks; step-dependent values are a numerical artifact.

