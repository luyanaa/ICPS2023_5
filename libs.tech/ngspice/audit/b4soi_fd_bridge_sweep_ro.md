# DUT/load sizing sweep and ring oscillators: fd bridge

Card: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc` (EXPERIMENTAL, SOIMOD=2)
Simulator: `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` (Xyce NF 7.10.0, BSIMSOI 4.5.0).
DUT and load use PARALLEL W24/L6 reference devices; no uncalibrated new
geometry is introduced, so nothing here unlocks a geometry-scaling
qualification. Experimental and version-tagged: no signoff Liberty, no
guaranteed fMAX, no yield claim.

## Part A: DUT/load sizing sweep (VDD=3 V, 2 ns edge)

| DUT | load | VOH (V) | VOL (V) | tPHL (ns) | tPLH (ns) | tFALL (ns) | tRISE (ns) | E_DUT fall+rise (pJ) | E_load fall (pJ) | Q_load fall (fC) | C_in,load (fF) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| x1 | x0 | 3.000 | 0.000 | 2.032 | 3.167 | 1.437 | 3.608 | 12.93 | 0.00 | 0.00 | 0.0 |
| x1 | x1 | 3.000 | 0.000 | 3.785 | 8.965 | 9.167 | 17.302 | 34.32 | 16.70 | 7905.79 | 2635.3 |
| x1 | x2 | 3.000 | 0.000 | 5.554 | 17.390 | 16.221 | 29.901 | 55.14 | 34.98 | 15069.64 | 5023.2 |
| x1 | x4 | 2.999 | 0.000 | 9.060 | 31.897 | 29.597 | 55.040 | 96.19 | 81.18 | 28932.36 | 9644.1 |
| x2 | x0 | 3.000 | 0.000 | 2.032 | 3.167 | 1.437 | 3.608 | 25.93 | 0.00 | 0.00 | 0.0 |
| x2 | x1 | 3.000 | 0.000 | 2.888 | 5.793 | 4.613 | 10.936 | 47.53 | 16.83 | 8260.28 | 2753.4 |
| x2 | x2 | 3.000 | 0.000 | 3.785 | 8.965 | 9.167 | 17.302 | 68.71 | 33.39 | 15803.93 | 5268.0 |
| x2 | x4 | 3.000 | 0.000 | 5.554 | 17.390 | 16.221 | 29.901 | 110.21 | 69.99 | 30144.67 | 10048.2 |
| x4 | x0 | 3.000 | 0.000 | 2.032 | 3.167 | 1.437 | 3.608 | 51.75 | 0.00 | 0.00 | 0.0 |
| x4 | x1 | 3.000 | 0.000 | 2.445 | 4.394 | 2.765 | 7.632 | 73.60 | 17.21 | 8535.10 | 2845.0 |
| x4 | x2 | 3.000 | 0.000 | 2.888 | 5.793 | 4.613 | 10.936 | 95.01 | 33.67 | 16520.27 | 5506.8 |
| x4 | x4 | 3.000 | 0.000 | 3.785 | 8.965 | 9.167 | 17.302 | 137.11 | 66.86 | 31628.61 | 10542.9 |

## Part B: ring oscillators

Each stage is one W24/L6 NMOS + W48/L6 PMOS inverter; odd stages;
N0 forced low to break the symmetric operating point.

| stages | setting | tstop | rising crossings | periods used | mean period (ns) | period spread | frequency (MHz) | Vout min..max (V) |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 5 | trap/20ps | 2000 | 24 | 12 | 85.79 | 0.00% | 11.66 | -0.196..3.206 |
| 5 | trap/50ps | 2000 | 24 | 12 | 85.79 | 0.00% | 11.66 | -0.196..3.206 |
| 11 | trap/20ps | 4224 | 23 | 11 | 190.63 | 0.00% | 5.25 | -0.222..3.222 |
| 11 | trap/50ps | 4224 | 23 | 11 | 190.63 | 0.00% | 5.25 | -0.222..3.222 |
| 21 | trap/20ps | 8064 | 23 | 11 | 363.93 | 0.00% | 2.75 | -0.222..3.222 |
| 21 | trap/50ps | 8064 | 23 | 11 | 363.94 | 0.00% | 2.75 | -0.222..3.222 |

## Interpretation and scope

- Part A is a 3 V, 25 °C, 2 ns-edge experiment on parallel W24/L6 reference
  devices. Its `C_in,load` column is a trajectory-dependent dynamic-charge
  result from the historical sweep window; it is not a fixed Liberty pin
  capacitance. See `b4soi_fd_bridge_cin_definitions.md` for the
  closure-consistent 2258.3 fF edge-start result, intrinsic Cgg values, and
  the frozen `C_eff_energy` status.
- Part B is a closed-loop check, not confirmation of the isolated FO1
  extrapolation. For odd stage count N, `f = 1/[N(tPHL+tPLH)]`. The observed
  mean per-stage `tPHL+tPLH` is 17.15 ns for 5 stages and 17.32 ns for 11 and
  21 stages; the mean one-way propagation delay is therefore 8.58–8.66 ns.
  The closed-loop/FO1 frequency ratios are 0.743, 0.736, and 0.737. The 0.74
  factor is specific to this topology, card, bias, and slew history; it is not
  a universal derating.
- The reported `0.00%` period spread is rounded to two decimal places. This
  report covers TRAP with 20 ps and 50 ps maximum steps only; GEAR and 5 ps
  convergence remain open checks.
- `Vout min..max` records the observed rail excursion (about -0.20 to 3.21 V).
  Excursion duration, terminal-node attribution, and finer-step sensitivity
  are not characterized here and are not a silicon-waveform claim.
- The 34.32 pJ x1/x1 sweep entry and the signed whole-cycle -30.043 pJ energy
  in the C_in audit use different historical windows/sign conventions.
  `C_eff_energy` remains frozen until the complete sizing sweep is
  re-integrated through `waveint.py`.

## Engineering planning baseline

Use only as version-limited FD-bridge/Xyce simulation planning values: 5-stage
RO approximately 11.7 MHz, 11-stage approximately 5.3 MHz, 21-stage
approximately 2.8 MHz, and closed-loop mean propagation delay approximately
8.6 ns/stage. Keep the earlier 2 ns-edge FO1 extrapolations (approximately
15.7 and 3.7 MHz) as a separate simplified reference. Do not emit signoff
Liberty or infer silicon frequency, PVT behavior, geometry scaling, or yield
from this report.

Experimental and version-tagged only: no signoff Liberty, no guaranteed fMAX,
no yield claim. Parallel reference devices are not a W96-equivalent transistor
and nothing here unlocks a geometry-scaling qualification.
