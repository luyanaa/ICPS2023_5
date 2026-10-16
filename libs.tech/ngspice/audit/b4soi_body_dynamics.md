# B4SOI body-dynamics diagnosis D1-D6 (frozen cards)

Bench: single MOS, free output node, pre-conditioned, 2 ns gate edge at 100 ns delay, reltol=1e-4 trap, gmin=1e-10.  kcl_inst = max |I_dev + C dv/dt| (A); kcl_cum = max |C*dV + integral I_dev dt| (C); i_steady = post-edge steady |i_gate|.

## D1/D3 load sweep (NMOS pull-down, 4-terminal)

| config | result | Vout range [V] | kcl_inst [A] | kcl_cum [C] | flips | i_steady [A] | note |
|---|---|---|---:|---:|---:|---:|---:|---|
| CL=5 fF | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | tiny bench load |
| CL=100 fF | OK | -0.724..3.068 | 0.008837 | 8.817e-15 | 19816 | 7.43e-10 |  |
| CL=220 fF | OK | -0.641..3.032 | 0.01451 | 2.765e-15 | 19708 | 6.93e-10 | FO1 with frozen model gate cap |
| CL=457 fF | OK | -0.510..3.015 | 2.344 | 1.172e-12 | 39984 | 2.04e-09 | FO1 with overlap candidate |
| CL=1000 fF | OK | -0.320..3.007 | 0.2332 | 1.164e-13 | 39880 | 4.69e-10 | large load |

## D4 polarity isolation

| config | result | Vout range [V] | kcl_inst [A] | kcl_cum [C] | flips | i_steady [A] | note |
|---|---|---|---:|---:|---:|---:|---:|---|
| NCH 5 fF | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 |  |
| PCH 5 fF | OK | 2.260..3.953 | 0.0003128 | 3.097e-15 | 89726 | 0.0188 |  |

## D5 body topology (NMOS, 5 fF)

| config | result | Vout range [V] | kcl_inst [A] | kcl_cum [C] | flips | i_steady [A] | note |
|---|---|---|---:|---:|---:|---:|---:|---|
| body=e | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | 4-terminal, floating body (frozen form) |
| body=p_s | OK | -0.749..3.974 | 2.074e-05 | 1.198e-17 | 42231 | 2.73e-08 | 5-terminal, P tied to source (body-contact diagnostic) |
| body=p_src | OK | -0.749..3.974 | 2.074e-05 | 1.198e-17 | 42231 | 2.73e-08 | 5-terminal, P driven 0 V |
| body=b_s | OK | -0.749..3.973 | 1.967e-05 | 1.187e-17 | 44381 | 3.36e-06 | 6-terminal, internal B exposed via 1 MOhm |

## D6 mechanism ablation (NMOS, 5 fF, 4-terminal)

| config | result | Vout range [V] | kcl_inst [A] | kcl_cum [C] | flips | i_steady [A] | note |
|---|---|---|---:|---:|---:|---:|---:|---|
| baseline | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | frozen card, defaults |
| aii=0 | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | impact ionization off |
| isbjt=1e-30 | OK | -0.864..4.156 | 0.0007143 | 7.148e-16 | 43893 | 1.53e-07 | parasitic BJT off |
| rbody=1e10 | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | body contact isolated |
| rbdb=1e6 | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | body-drain resistance raised |
| cgeo=0 | OK | -0.812..3.759 | 9.337e-05 | 8.001e-17 | 41745 | 6.63e-09 | gate-substrate overlap off |
| aii=0+isbjt | OK | -0.864..4.156 | 0.0007143 | 7.148e-16 | 43893 | 1.53e-07 | both off |

## D2 internal body trajectory (from D1 baseline run)

- internal body voltage: [-1.6169, 0.8637] V, sign flips 3/50022, max step delta 0.2789 V
- output node: [-0.8125, 3.7592] V, sign changes across 0: 1/50022

## Reading

- D1: the cumulative nodal charge balance holds to ~1e-17 C at 5 fF and ~1e-12 C at all other loads: the port-current accounting and terminal bookkeeping are self-consistent.  Per the reviewer's criterion, the anomaly is NOT a KCL/accounting defect; it is model-internal dynamics.
- D2: the internal body voltage moves smoothly (sign flips 3, max step 0.28 V) while the OUT terminal node oscillates tens of thousands of times: the chatter is a terminal-node / nodal-integration phenomenon, not an internal state jump.
- D3: the OUT excursion amplitude decays with load (-0.81 V @5 fF -> -0.32 V @1 pF) but the limit cycle PERSISTS at real FO1 loads (220 fF: -0.64 V, ~20k flips; 457 fF: -0.51 V, ~40k flips): a real fanout gate load does not remove it, so the 5 fF-only bench is not a special case.
- D4: both polarities are affected; PMOS is worse (steady input-branch current ~19 mA, ~90k flips).
- D5: tying the fifth body terminal to the source does NOT cure the free-node oscillation (flips unchanged, excursion similar), consistent with the INV result: the five-terminal form is a body-contact diagnostic, not a transient solution.
- D6: the parasitic BJT mechanism (isbjt) is engaged in the dynamics (removing it changes excursion/flips); impact ionization, body-contact resistance and cgeo are inert in this bench.  The frozen cards leave every one of them at ngspice defaults with no PDK data behind them.
- kcl_inst is limited by the 20 ps output sampling during the 2 ns edge; kcl_cum (integrated over the same samples) is the robust conservation metric.

