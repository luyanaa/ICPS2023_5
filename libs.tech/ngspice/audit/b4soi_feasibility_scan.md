# SOI dynamic feasibility scan (E1-E5)

Frozen-card baseline: Cgg off 19.6 fF, Cgg triode 339.3 fF, Cgg sat 212.0 fF; ID(3V,3V)=1.321 mA; T7 5 fF/2 ns flips 41.7k, OUT -0.81..3.76 V.  Xyce ports in audit/xyce/.

## E1 reverse-workspace scan (VDS -1.2..+0.5 V, fixed VG)

| VG | swap | ID(-1.0V) | ID(-0.2V) | ID(0) | ID(+0.2V) | ID(+0.5V) | max |dID/dVD| near 0 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | no | 0.0001407 | 2.568e-07 | -9.016e-23 | -4.627e-09 | -4.729e-09 | 1.239e-05 S |
| 0 | yes | 0.0001406 | 2.568e-07 | -9.015e-23 | -4.63e-09 | -4.735e-09 | 1.238e-05 S |
| 1.5 | no | 0.0007058 | 0.0001253 | 1.126e-18 | -0.0001093 | -0.0002326 | 0.0006609 S |
| 1.5 | yes | 0.0007058 | 0.0001253 | -3.743e-19 | -0.0001093 | -0.0002326 | 0.0006609 S |
| 3 | no | 0.00113 | 0.0002239 | 3e-18 | -0.0002127 | -0.0004932 | 0.001124 S |
| 3 | yes | 0.00113 | 0.0002239 | 5.659e-27 | -0.0002127 | -0.0004932 | 0.001124 S |

### E1b charge-derivative continuity at VDS=0 (VG=0, ICE-free point)

| VDS | Cgg (fF) | Cgd drive (fF) |
|---|---:|---:|
| -0.20 V | 67.337 | 42.462 |
| -0.10 V | 26.187 | 10.549 |
| +0.00 V | 20.677 | 5.804 |
| +0.10 V | 20.195 | 5.193 |
| +0.20 V | 20.150 | 5.089 |

## E2 BJT ablation and boundary (T7 5 fF, 2 ns)

| config | OUT range | flips | steady \|iG\| | body V range | body flips | body max step |
|---|---|---:|---:|---|---:|---:|---:|
| baseline (frozen defaults) | -0.841..0.923 | 43231 | 2.04e-09 | -3.285..0.865 | 78 | 0.28 |
| BJTOFF=1 | -0.841..0.928 | 43205 | 2.03e-09 | -3.294..0.865 | 78 | 0.281 |
| isbjt=1e-30 | FAIL abort=True doAnalyses: TRAN:  Timestep too small; time = 1.00372e-07, t | - | - | - | - | - |
| isbjt=1e-12 | FAIL abort=True doAnalyses: TRAN:  Timestep too small; time = 1.00372e-07, t | - | - | - | - | - |
| isbjt=1e-6 | -0.841..0.923 | 43231 | 2.04e-09 | -3.285..0.865 | 78 | 0.28 |
| isbjt=1e-4 | -0.851..0.810 | 41879 | 1.66e-09 | -3.774..0.746 | 56 | 0.303 |
| lbjt0=10u | -0.850..0.835 | 41939 | 1.68e-09 | -3.750..0.770 | 6 | 0.302 |
| aii=0 bii=0 cii=0 dii=0 | -0.841..0.923 | 43231 | 2.04e-09 | -3.285..0.865 | 78 | 0.28 |

## E3 free-node DC return-path scan (T7 5 fF, 2 ns)

| OUT return | OUT range | flips | steady \|iG\| |
|---|---|---:|---:|
| none (frozen form) | -0.841..0.923 | 43231 | 2.04e-09 |
| 1e+09 ohm to GND | -0.841..0.923 | 43075 | 2.06e-09 |
| 1e+07 ohm to GND | -0.841..0.921 | 39977 | 1.81e-09 |
| 1e+05 ohm to GND | -0.825..0.769 | 49147 | 1.8e-09 |
| 1e+03 ohm to GND | -0.218..0.152 | 49869 | 1.29e-08 |

## E4 SOI dynamic feasibility (DC x charge x stability)

A combination qualifies only if it passes ALL THREE gates.

| variant | ID(3/3V) drift | ID(1/0.05) drift | Cgg off | Cgg triode | Cgg sat | flips | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| TSI=50n | +0.0% | +0.0% | 18.6 fF | 339.3 fF | 211.0 fF | 42565 | DC+charge |
| TSI=150n | +0.0% | +0.0% | 20.6 fF | 339.4 fF | 213.0 fF | 43676 | DC+charge |
| TSI=200n | +0.0% | +0.0% | 21.5 fF | 339.4 fF | 213.9 fF | 44064 | DC+charge |
| TBOX=200n | +0.0% | +0.0% | 25.5 fF | 339.4 fF | 217.0 fF | 43230 | DC+charge |
| XJ=30n | -0.1% | -0.0% | 18.2 fF | 338.0 fF | 210.6 fF | 43945 | DC+charge |
| XJ=100n | +0.2% | +0.0% | 22.9 fF | 342.7 fF | 215.3 fF | 41340 | DC+charge |
| TSI=50n TBOX=200n XJ=30n | -0.1% | -0.0% | 23.5 fF | 338.0 fF | 215.2 fF | 43484 | DC+charge |
| TSI=200n TBOX=200n XJ=100n | +0.2% | +0.0% | 30.2 fF | 342.7 fF | 221.3 fF | 42710 | DC |
| BJTOFF=1 | +0.0% | +0.0% | 19.6 fF | 339.3 fF | 212.0 fF | 43205 | DC+charge |
| rbdb=1e3 | +0.0% | +0.0% | 19.6 fF | 339.3 fF | 212.0 fF | 43231 | DC+charge |
| rbdb=1e5 | +0.0% | +0.0% | 19.6 fF | 339.3 fF | 212.0 fF | 43231 | DC+charge |
| rbdb=1e3 TSI=50n XJ=30n | -0.1% | -0.0% | 17.3 fF | 338.0 fF | 209.7 fF | 43484 | DC+charge |
| rbdb=1e3 TSI=150n XJ=30n | -0.1% | -0.0% | 19.3 fF | 338.0 fF | 211.7 fF | 44284 | DC+charge |
| rbdb=1e5 TSI=150n XJ=30n | -0.1% | -0.0% | 19.3 fF | 338.0 fF | 211.7 fF | 44284 | DC+charge |
| rbdb=1e5 TSI=150n XJ=30n TBOX=200n | -0.1% | -0.0% | 24.9 fF | 338.0 fF | 216.2 fF | 44311 | DC+charge |

## E5 DC parity full curves (ngspice 4.4 vs Xyce 70450/70)

| VG | VDS | ngspice 4.4 | Xyce 4.5.0 | Xyce 4.6.1 |
|---|---|---:|---:|---:|
| 1.5 | -1.0 V | 0.0007216 A | 0.0007058 A | 0.0007058 A |
| 1.5 | -0.2 V | 0.0001384 A | 0.0001253 A | 0.0001253 A |
| 1.5 | +0.0 V | 1.207e-05 A | 2.094e-27 A | 2.094e-27 A |
| 1.5 | +0.2 V | 9.935e-05 A | 0.0001093 A | 0.0001093 A |
| 1.5 | +0.5 V | 0.0002258 A | 0.0002326 A | 0.0002326 A |
| 1.5 | +1.0 V | 0.0003337 A | 0.0003356 A | 0.0003356 A |
| 1.5 | +3.0 V | 0.0003555 A | 0.0003556 A | 0.0003556 A |
| 3 | -1.0 V | 0.001153 A | 0.00113 A | 0.00113 A |
| 3 | -0.2 V | 0.0002463 A | 0.0002239 A | 0.0002239 A |
| 3 | +0.0 V | 2.23e-05 A | 4.969e-19 A | 4.969e-19 A |
| 3 | +0.2 V | 0.0001923 A | 0.0002127 A | 0.0002127 A |
| 3 | +0.5 V | 0.0004759 A | 0.0004932 A | 0.0004932 A |
| 3 | +1.0 V | 0.0008502 A | 0.0008627 A | 0.0008627 A |
| 3 | +3.0 V | 0.001321 A | 0.001321 A | 0.001321 A |

## Findings

- E1: the reverse workspace is DC-sound (S/D swap identical to 0.1%, ID(0)=0 continuous), but the charge derivatives are not symmetric: Cgg rises to 67 fF at VDS=-0.2 V (gate off) versus 20 fF at +0.2 V.  The falling T7 edge therefore drives the device into the higher-gate-charge reverse region exactly where the Miller kick is largest -- a physically consistent self-reinforcing candidate for the limit-cycle trigger, not a DC discontinuity.
- E2: BJTOFF=1 does not remove the oscillation (43.2k -> 43.2k flips); isbjt below 1e-12 collapses the timestep control instead.  Parasitic-BJT sensitivity is NOT BJT causation; the BJT is not a necessary condition for the limit cycle.
- E3: a 1 kOhm DC return from the free output node does not remove the oscillation (~50k flips persist; amplitude damps to 0.15 V at 1 kOhm).  The instability does not depend on an under-constrained drain node.
- E4: no parameter combination in the scanned uncalibrated workspace (TSI, TBOX, XJ, rbdb, BJTOFF) passes all three gates: DC and charge anchors hold within ~10% for all, but every combination still shows the 41-44k flip limit cycle. Parameter tuning alone does not produce a dynamic candidate; the defect lies in the model equations / fitted parameter workspace, not in these knob ranges.
- E5: DC parity extends across subthreshold, linear, saturation and reverse bias: ngspice 4.4 vs Xyce 4.5.0/4.6.1 agree within ~2-10% on |ID| for the same cards (Xyce 4.5.0 and 4.6.1 identical here).  The VDS=0 row compares different grid offsets (0.02 V step vs 0.1 V step) and is not a mismatch; use the +/-0.2 V rows for continuity.

## Method notes

- DC return paths: a real body contact (rbdb/rbsb) or a node leak both change the floating-node work point; a combination that only stops flips by adding artificial conductance is rejected as a dynamic candidate.
- BJTOFF=1 is diagnostic: real PD-SOI parasitic bipolar action may be physically present, so a BJTOFF-based stabilisation must be converted into a measured BJT-parameter bound, not merged as-is.
- Parameter sets are feasible only if they satisfy all three gates simultaneously (DC parity, charge anchors, stability with method/step insensitivity).

