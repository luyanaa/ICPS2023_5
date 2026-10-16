# B4SOI full source/drain charge symmetry

Model: `icps2023_5.lib.spice`, LEVEL=58, W=24 µm, L=6 µm, 25 °C.
Deck B keeps the same D/G/S/E wiring and swaps only the absolute D/S voltage values.
Expected mirror: `(Qg,Qd,Qs,Qb)B = (Qg,Qs,Qd,Qb)A`; for the available Cij fields, `C_B = P C_A P^T`.

`ngspice-47` exposes QD, QS, and QB but not QG/QE through the instance-query interface. QG is reconstructed from the model charge-conservation relation; QE is not independently observable in this build.

| VG | VD(A) | QG A (fC) | QD A | QS A | QB A | QG B | QD B | QS B | QB B | max charge deviation |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | -0.2 | 24.0256 | -0.445467 | -1.85448 | -21.7257 | 24.0342 | -0.445319 | -1.85384 | -21.735 | 76% |
| 0 | +0.2 | 10.3043 | -0.00690467 | -0.0288438 | -10.2686 | 10.2931 | -0.0069088 | -0.0288614 | -10.2573 | 76.1% |
| 1.5 | -0.2 | 449.429 | -162.961 | -219.711 | -66.757 | 449.432 | -162.959 | -219.709 | -66.7636 | 25.8% |
| 1.5 | +0.2 | 381.272 | -131.218 | -187.054 | -63.0005 | 381.269 | -131.219 | -187.056 | -62.9938 | 29.9% |
| 1.5 | +3 | 306.03 | -37.0984 | -165.956 | -102.976 | 305.968 | -37.0967 | -165.982 | -102.889 | 77.6% |

## Public capacitance/Jacobian mirror check

The Cij values are signed public model quantities. Only the nine fields available in ngspice-47 are compared; missing substrate-column fields are not inferred.

| VG | VD(A) | max signed Cij mirror deviation | largest field |
|---:|---:|---:|---|
| 0 | -0.2 | 194% | cds |
| 0 | +0.2 | 189% | cdg |
| 1.5 | -0.2 | 186% | cbd |
| 1.5 | +0.2 | 187% | cbs |
| 1.5 | +3 | 198% | cdg |

## Interpretation

- The charge test uses signed QD/QS/QB values and the correct absolute-voltage D/S swap; it is not the invalid comparison of `Cgg(VD=-0.2)` against `Cgg(VD=+0.2)` with source held fixed.
- Non-reciprocal Cgd/Cdg and Cgs/Cds are allowed. The test checks the complete D/S permutation instead of element-wise reciprocity.
- The missing independent QE query limits the charge-vector test to the exposed B4SOI quantities plus conservation-derived QG; it does not prove hidden substrate/body derivatives.
- This report does not establish dynamic stability; F2 remains a separate transient/timestep experiment.
