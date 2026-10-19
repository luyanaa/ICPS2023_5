# ICPS2023_5 inverter timing, experimental and version-tagged

Card under test: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc` (variant `fd`, SOIMOD=2).
Reference card: `libs.tech/ngspice/models/icps2023_5_b4soi.inc` (unchanged nominal).
Simulator: `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` (Xyce NF 7.10.0, BSIMSOI 4.5.0).

**This is an experimental timing table for functional PnR exploration only.**
It is NOT a signoff Liberty, and no guaranteed fMAX or yield may be derived
from it. Every number carries an integrator/step convergence check, and the
fanout load is built from real inverter gates (FO1 = one, FO4 = four) with a
1 fF explicit parasitic per load output.

Bench: VDD/2 switching thresholds, 20 ns delay, 200 ns width, 400 ns period.

## Measured delays and switching energy

A delay/energy row is reported only if ALL of the following pass on the same condition: level margins (VOL <= 0.15 V, VOH >= VDD - 0.15 V), settled tail drift <= 10 mV peak-to-peak, supply tail current RMS <= 1e-03 A, and |VOUT_tail - VOUT_OP| <= 10 mV against a forced-DC operating point at the same input level. Anything else reports no delay at all.

| card | VDD | slew (ns) | FO | Vout low/high (V) | Vout_OP low/high (V) | \|tail-OP\| (mV) | drift pp (mV) | supply tail RMS (A) | gate status | tPHL (ns) | tPLH (ns) | tFALL (ns) | tRISE (ns) | E_sw (fJ) |
|---|---:|---:|---:|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| bridge_fd | 2.5 | 2 | 1 | 0.000 / 2.500 | 0.000 / 2.500 | 0.00 | 0.00 | 3.05e-07 | ACCEPTED | 4.388  | 11.633  | 10.693  | 21.602  | 26500.880  |
| bridge_fd | 2.5 | 2 | 4 | 0.000 / 2.487 | 0.000 / 2.500 | 69.37 | 69.03 | 3.09e-05 | NOT_SETTLED_TO_OP | n/a | n/a | n/a | n/a | n/a |
| bridge_fd | 2.5 | 10 | 1 | 0.000 / 2.500 | 0.000 / 2.500 | 0.01 | 0.01 | 7.73e-06 | ACCEPTED | 5.615  | 12.848  | 11.197  | 21.533  | 25597.023  |
| bridge_fd | 2.5 | 10 | 4 | 0.000 / 2.469 | 0.000 / 2.500 | 165.27 | 164.46 | 1.85e-05 | NOT_SETTLED_TO_OP | n/a | n/a | n/a | n/a | n/a |
| bridge_fd | 3.0 | 2 | 1 | 0.000 / 3.000 | 0.000 / 3.000 | 0.00 | 0.00 | 3.32e-05 | ACCEPTED | 3.785  | 8.965  | 9.167  | 17.302  | 38624.952  |
| bridge_fd | 3.0 | 2 | 4 | 0.000 / 2.998 | 0.000 / 3.000 | 16.20 | 16.18 | 1.88e-05 | NOT_SETTLED_TO_OP | n/a | n/a | n/a | n/a | n/a |
| bridge_fd | 3.0 | 10 | 1 | 0.000 / 3.000 | 0.000 / 3.000 | 0.00 | 0.00 | 5.81e-06 | ACCEPTED | 5.031  | 10.342  | 9.953  | 17.194  | 37638.706  |
| bridge_fd | 3.0 | 10 | 4 | 0.000 / 2.993 | 0.000 / 3.000 | 49.48 | 49.42 | 6.62e-06 | NOT_SETTLED_TO_OP | n/a | n/a | n/a | n/a | n/a |
| bridge_fd | 3.5 | 2 | 1 | 0.000 / 3.500 | 0.000 / 3.500 | 0.00 | 0.00 | 6.29e-05 | ACCEPTED | 3.355  | 7.251  | 8.076  | 14.400  | 53035.818  |
| bridge_fd | 3.5 | 2 | 4 | 0.000 / 3.499 | 0.000 / 3.500 | 3.59 | 3.59 | 7.36e-05 | ACCEPTED | 8.050  | 25.922  | 26.010  | 45.769  | 250385.289  |
| bridge_fd | 3.5 | 10 | 1 | 0.000 / 3.500 | 0.000 / 3.500 | 0.00 | 0.00 | 1.85e-05 | ACCEPTED | 4.531  | 8.746  | 9.087  | 14.404  | 52296.051  |
| bridge_fd | 3.5 | 10 | 4 | 0.000 / 3.498 | 0.000 / 3.500 | 13.95 | 13.94 | 1.93e-05 | NOT_SETTLED_TO_OP | n/a | n/a | n/a | n/a | n/a |
| stage1_reference | 2.5 | 2 | 1 | 0.000 / 2.500 | 0.000 / 2.500 | 0.01 | 0.00 | 6.08e-08 | ACCEPTED | 1.720  | 4.237  | 3.252  | 7.809  | 8510.398  |
| stage1_reference | 2.5 | 2 | 4 | 0.000 / 2.500 | 0.000 / 2.500 | 0.01 | 0.01 | 7.83e-05 | ACCEPTED | 4.322  | 15.526  | 13.216  | 25.084  | 37513.685  |
| stage1_reference | 2.5 | 10 | 1 | 0.000 / 2.500 | 0.000 / 2.500 | 0.01 | 0.00 | 1.53e-07 | ACCEPTED | 2.420  | 6.169  | 4.662  | 8.332  | 8705.138  |
| stage1_reference | 2.5 | 10 | 4 | 0.000 / 2.500 | 0.000 / 2.500 | 0.04 | 0.05 | 3.17e-05 | ACCEPTED | 5.543  | 17.194  | 14.107  | 25.108  | 37304.029  |
| stage1_reference | 3.0 | 2 | 1 | 0.000 / 3.000 | 0.000 / 3.000 | 0.01 | 0.00 | 2.09e-07 | ACCEPTED | 1.590  | 3.704  | 2.974  | 7.099  | 13042.916  |
| stage1_reference | 3.0 | 2 | 4 | 0.000 / 3.000 | 0.000 / 3.000 | 0.01 | 0.00 | 2.26e-05 | ACCEPTED | 3.990  | 13.236  | 11.996  | 22.530  | 60367.327  |
| stage1_reference | 3.0 | 10 | 1 | 0.000 / 3.000 | 0.000 / 3.000 | 0.01 | 0.00 | 7.13e-08 | ACCEPTED | 2.189  | 5.568  | 4.580  | 7.810  | 13966.762  |
| stage1_reference | 3.0 | 10 | 4 | 0.000 / 3.000 | 0.000 / 3.000 | 0.01 | 0.01 | 2.55e-05 | ACCEPTED | 5.178  | 14.790  | 13.075  | 22.623  | 60379.847  |
| stage1_reference | 3.5 | 2 | 1 | 0.000 / 3.500 | 0.000 / 3.500 | 0.01 | 0.00 | 3.10e-07 | ACCEPTED | 1.493  | 3.364  | 2.773  | 6.643  | 18660.969  |
| stage1_reference | 3.5 | 2 | 4 | 0.000 / 3.500 | 0.000 / 3.500 | 0.01 | 0.00 | 8.00e-05 | ACCEPTED | 3.759  | 11.778  | 11.060  | 20.903  | 88947.966  |
| stage1_reference | 3.5 | 10 | 1 | 0.000 / 3.500 | 0.000 / 3.500 | 0.01 | 0.00 | 4.18e-07 | ACCEPTED | 2.016  | 5.203  | 4.463  | 7.501  | 20866.591  |
| stage1_reference | 3.5 | 10 | 4 | 0.000 / 3.500 | 0.000 / 3.500 | 0.01 | 0.01 | 3.49e-05 | ACCEPTED | 4.933  | 13.280  | 12.295  | 21.089  | 89607.634  |

## Integrator / step-size convergence

Spread of the same quantity across trap/20ps, trap/5ps, gear/20ps.

| card | VDD | slew | FO | tPHL spread | tPLH spread | E_sw spread | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| bridge_fd | 2.5 | 2 | 1 | 0.00% | 0.00% | 0.01% | usable |
| bridge_fd | 2.5 | 2 | 4 | n/a | n/a | n/a | UNRESOLVED |
| bridge_fd | 2.5 | 10 | 1 | 0.00% | 0.00% | 0.00% | usable |
| bridge_fd | 2.5 | 10 | 4 | n/a | n/a | n/a | UNRESOLVED |
| bridge_fd | 3.0 | 2 | 1 | 0.00% | 0.00% | 0.01% | usable |
| bridge_fd | 3.0 | 2 | 4 | n/a | n/a | n/a | UNRESOLVED |
| bridge_fd | 3.0 | 10 | 1 | 0.00% | 0.00% | 0.00% | usable |
| bridge_fd | 3.0 | 10 | 4 | n/a | n/a | n/a | UNRESOLVED |
| bridge_fd | 3.5 | 2 | 1 | 0.00% | 0.00% | 0.01% | usable |
| bridge_fd | 3.5 | 2 | 4 | 0.00% | 0.00% | 0.00% | usable |
| bridge_fd | 3.5 | 10 | 1 | 0.00% | 0.00% | 0.00% | usable |
| bridge_fd | 3.5 | 10 | 4 | n/a | n/a | n/a | UNRESOLVED |
| stage1_reference | 2.5 | 2 | 1 | 0.00% | 0.01% | 0.01% | usable |
| stage1_reference | 2.5 | 2 | 4 | 0.00% | 0.00% | 0.01% | usable |
| stage1_reference | 2.5 | 10 | 1 | 0.00% | 0.00% | 0.01% | usable |
| stage1_reference | 2.5 | 10 | 4 | 0.00% | 0.00% | 0.00% | usable |
| stage1_reference | 3.0 | 2 | 1 | 0.00% | 0.01% | 0.02% | usable |
| stage1_reference | 3.0 | 2 | 4 | 0.01% | 0.00% | 0.01% | usable |
| stage1_reference | 3.0 | 10 | 1 | 0.00% | 0.00% | 0.01% | usable |
| stage1_reference | 3.0 | 10 | 4 | 0.00% | 0.00% | 0.00% | usable |
| stage1_reference | 3.5 | 2 | 1 | 0.00% | 0.01% | 0.01% | usable |
| stage1_reference | 3.5 | 2 | 4 | 0.01% | 0.00% | 0.01% | usable |
| stage1_reference | 3.5 | 10 | 1 | 0.00% | 0.01% | 0.01% | usable |
| stage1_reference | 3.5 | 10 | 4 | 0.00% | 0.00% | 0.00% | usable |

## Per-port current and charge conservation

Every MOS terminal carries a 0 V sense source. Each device's four
terminal currents are integrated over a full 400 ns period and must close;
the three driven sources must also sum to zero.

| card | VDD | slew | FO | NMOS closure (aC) | PMOS closure (aC) | source sum (aC) | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| bridge_fd | 2.5 | 2 | 1 | 0.000 | 0.001 | -10600328.280 | closed |
| bridge_fd | 2.5 | 2 | 4 | -0.003 | -0.001 | -47849758.184 | closed |
| bridge_fd | 2.5 | 10 | 1 | -0.000 | -0.000 | -10238258.970 | closed |
| bridge_fd | 2.5 | 10 | 4 | 0.000 | -0.000 | -47581817.739 | closed |
| bridge_fd | 3.0 | 2 | 1 | -0.003 | 0.002 | -12874984.925 | closed |
| bridge_fd | 3.0 | 2 | 4 | 0.000 | 0.001 | -59625587.288 | closed |
| bridge_fd | 3.0 | 10 | 1 | -0.000 | -0.000 | -12545545.666 | closed |
| bridge_fd | 3.0 | 10 | 4 | -0.001 | -0.000 | -59320253.531 | closed |
| bridge_fd | 3.5 | 2 | 1 | -0.000 | -0.000 | -15153138.668 | closed |
| bridge_fd | 3.5 | 2 | 4 | 0.003 | 0.000 | -71536161.291 | closed |
| bridge_fd | 3.5 | 10 | 1 | -0.000 | 0.001 | -14941084.625 | closed |
| bridge_fd | 3.5 | 10 | 4 | 0.000 | -0.001 | -71222192.296 | closed |
| stage1_reference | 2.5 | 2 | 1 | -0.000 | -0.000 | -3430754.684 | closed |
| stage1_reference | 2.5 | 2 | 4 | 0.000 | 0.000 | -14946974.524 | closed |
| stage1_reference | 2.5 | 10 | 1 | -0.000 | -0.000 | -3476076.871 | closed |
| stage1_reference | 2.5 | 10 | 4 | 0.000 | 0.000 | -14861761.058 | closed |
| stage1_reference | 3.0 | 2 | 1 | -0.001 | 0.001 | -4360311.534 | closed |
| stage1_reference | 3.0 | 2 | 4 | -0.002 | -0.000 | -20020491.927 | closed |
| stage1_reference | 3.0 | 10 | 1 | 0.000 | -0.000 | -4627872.132 | closed |
| stage1_reference | 3.0 | 10 | 4 | 0.001 | -0.000 | -20023237.895 | closed |
| stage1_reference | 3.5 | 2 | 1 | 0.000 | 0.000 | -5321953.528 | closed |
| stage1_reference | 3.5 | 2 | 4 | -0.001 | -0.001 | -25251762.170 | closed |
| stage1_reference | 3.5 | 10 | 1 | 0.000 | 0.000 | -5904957.881 | closed |
| stage1_reference | 3.5 | 10 | 4 | -0.002 | -0.001 | -25438959.497 | closed |

## Rail settling of the loaded DUT

A timing table is only meaningful for an inverter that actually restores
its levels. This is the gate that decides whether anything below may be
quoted.

| card | condition | settled low (V) | settled high (V) | verdict |
|---|---|---:|---:|---|
| bridge_fd | VDD=2.5 slew=2 FO1 | 0.000 | 2.500 | settles |
| bridge_fd | VDD=2.5 slew=2 FO4 | 0.000 | 2.487 | settles |
| bridge_fd | VDD=2.5 slew=10 FO1 | 0.000 | 2.500 | settles |
| bridge_fd | VDD=2.5 slew=10 FO4 | 0.000 | 2.469 | settles |
| bridge_fd | VDD=3.0 slew=2 FO1 | 0.000 | 3.000 | settles |
| bridge_fd | VDD=3.0 slew=2 FO4 | 0.000 | 2.998 | settles |
| bridge_fd | VDD=3.0 slew=10 FO1 | 0.000 | 3.000 | settles |
| bridge_fd | VDD=3.0 slew=10 FO4 | 0.000 | 2.993 | settles |
| bridge_fd | VDD=3.5 slew=2 FO1 | 0.000 | 3.500 | settles |
| bridge_fd | VDD=3.5 slew=2 FO4 | 0.000 | 3.499 | settles |
| bridge_fd | VDD=3.5 slew=10 FO1 | 0.000 | 3.500 | settles |
| bridge_fd | VDD=3.5 slew=10 FO4 | 0.000 | 3.498 | settles |
| stage1_reference | VDD=2.5 slew=2 FO1 | 0.000 | 2.500 | settles |
| stage1_reference | VDD=2.5 slew=2 FO4 | 0.000 | 2.500 | settles |
| stage1_reference | VDD=2.5 slew=10 FO1 | 0.000 | 2.500 | settles |
| stage1_reference | VDD=2.5 slew=10 FO4 | 0.000 | 2.500 | settles |
| stage1_reference | VDD=3.0 slew=2 FO1 | 0.000 | 3.000 | settles |
| stage1_reference | VDD=3.0 slew=2 FO4 | 0.000 | 3.000 | settles |
| stage1_reference | VDD=3.0 slew=10 FO1 | 0.000 | 3.000 | settles |
| stage1_reference | VDD=3.0 slew=10 FO4 | 0.000 | 3.000 | settles |
| stage1_reference | VDD=3.5 slew=2 FO1 | 0.000 | 3.500 | settles |
| stage1_reference | VDD=3.5 slew=2 FO4 | 0.000 | 3.500 | settles |
| stage1_reference | VDD=3.5 slew=10 FO1 | 0.000 | 3.500 | settles |
| stage1_reference | VDD=3.5 slew=10 FO4 | 0.000 | 3.500 | settles |

## Verdict

- runs attempted: 72, completed: 72, not completed: 0, not settling: 0
- a delay or energy row is listed only alongside its convergence spread;
  rows marked UNRESOLVED must not be quoted as timing data.
- rows whose loaded output does not settle have NO delay or energy reported
  at all: the quantity is not defined for a waveform that never restores its
  levels.
- this table is version-tagged experimental data. It is **not** a signoff
  Liberty, it does not establish a guaranteed fMAX, and no yield claim may
  be built on it. The open FD-validity questions in the model README apply
  unchanged.

