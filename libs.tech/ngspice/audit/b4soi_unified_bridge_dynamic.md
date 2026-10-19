# B4SOI unified experimental bridge: dynamic stability in Xyce

Card: `libs.tech/ngspice/models/b4soi_unified_bridge_experimental.inc` (EXPERIMENTAL; the nominal model is unchanged).
Simulator: `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` (Xyce NF 7.10.0, BSIMSOI 4.5.0 via LEVEL=70450).
Fixed numerical setting TSTEP=2e-11 TMAX=2e-11 METHOD=TRAP RELTOL=1e-4;
no retry ladder and no tolerance loosening. Four-terminal D/G/S/E with a
single driven common substrate; PMOS E is SUB, never VDD.

Benches: VDD=3 V, 5 fF load, 2 ns edges, 100 ns delay, 400 ns width, 1 us stop.

## Results

| card | bench | status | steps | dt max (ps) | Vout min..max (V) | tail V rms (uV) | tail current pp (mA) | tail zero x | edge charge (fC) | false quiet tail |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---|
| bridge candidate | inverter | incomplete:  | - | - | - | - | - | - | - | - |
| bridge candidate | t7_nch | over_budget: output exceeded 200000000 bytes (200.8 MB) after 98 s | - | - | - | - | - | - | - | - |
| bridge candidate | t7_pch | observed | 50103 | 20.0 | 0.0000..4.1416 | 0.000 | 0.0073 | 5000 | 1540.968 | no |
| stage-1 frozen | inverter | observed | 200094 | 20.0 | -0.4313..3.6453 | 0.000 | 0.0014 | 5000 | 988.849 | no |
| stage-1 frozen | t7_nch | observed | 50136 | 20.0 | -0.7995..3.9714 | 2272.917 | 0.0000 | 5000 | 860.352 | no |
| stage-1 frozen | t7_pch | observed | 50132 | 20.0 | 0.0000..3.9729 | 0.000 | 0.0166 | 5000 | 762.370 | no |

## Verdict

- the bridge candidate did not pass 2 bench(es):
  - bridge candidate / inverter: false quiet tail
  - bridge candidate / t7_nch: output exceeded 200000000 bytes (200.8 MB) after 98 s

- stage-1 card, for comparison: all benches completed

**How to read the step counts.** At 1 us with a 20 ps maximum step, roughly
5e4 accepted steps is simply the baseline magnitude for this output
resolution - it is not by itself an improvement. What the comparison shows
is that the candidate no longer generates a large number of *extra* solve
steps from high-frequency current chatter, which is strong stability
evidence but is not proof of physical accuracy.

**Not yet done, and required before any timing claim**: inverter FO1/FO4
with several input slews, other supply voltages and load values, plus
propagation-delay and switching-energy convergence under integrator and
step changes, with every MOS port, the whole supply current and charge
conservation re-checked. Only then may version-tagged *experimental* timing
tables be generated for functional PnR exploration; no guaranteed fMAX or
yield may be derived from them.

Agreement on completion and on the absence of a false quiet tail is what
this gate checks. It does not qualify the model for timing signoff, and a
quiet output alone is never a pass.

