# FO4 long-window recovery study: fd bridge (SOIMOD=2)

Card: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc`
Approved for the five FO4 conditions that failed `NOT_SETTLED_TO_OP`.
Model, load, input slew and numerical settings are unchanged; only the hold
after the last switch is extended, and the 10 mV epsilon is kept.

Input: one cycle only (`period = 1e6`), so extending the stop time lengthens
the hold without adding switching events. The last switch is at 220 ns.

## VDD = 2.5 V, slew = 2 ns, FO4

| hold (ns) | steps | VOUT tail mean (V) | max \|e\| over WHOLE hold (mV) | max \|e\| in last 100 ns (mV) | e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 400 | 20014 | 2.3864 | 3272.61 | 0.00 | +0.00 | 267.8 | RECOVERED | n/a |

- Sample at the largest last-100 ns error: t = 520.0 ns after the last edge, VIN = 0.0000 V, VOUT = 2.4999 V, VOUT_OP = 2.4999 V, signed error = +0.00 mV.

- VOUT_OP = 2.4999 V, I(VPWR_D)_OP = -3.530e-08 A, I(VPWR_L)_OP = -1.640e-12 A
| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |
|---|---:|---:|---:|---:|---:|---:|---:|
| dut | 400 | -3.529694e-08 | -3.533674e-08 | -3.980e-11 | 2.005204e-07 | 2.005204e-07 | 4.2923e-07 |
| load | 400 | -1.639679e-12 | +2.091807e-10 | +2.108e-10 | 1.047609e-06 | 1.047609e-06 | 2.5661e-06 |

## VDD = 2.5 V, slew = 10 ns, FO4

| hold (ns) | steps | VOUT tail mean (V) | max \|e\| over WHOLE hold (mV) | max \|e\| in last 100 ns (mV) | e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 400 | 20011 | 2.3051 | 2701.61 | 0.00 | +0.00 | 281.0 | RECOVERED | n/a |

- Sample at the largest last-100 ns error: t = 520.0 ns after the last edge, VIN = 0.0000 V, VOUT = 2.4999 V, VOUT_OP = 2.4999 V, signed error = +0.00 mV.

- VOUT_OP = 2.4999 V, I(VPWR_D)_OP = -3.530e-08 A, I(VPWR_L)_OP = -1.640e-12 A
| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |
|---|---:|---:|---:|---:|---:|---:|---:|
| dut | 400 | -3.529694e-08 | -3.542068e-08 | -1.237e-10 | 6.237638e-07 | 6.237638e-07 | 1.3570e-06 |
| load | 400 | -1.639679e-12 | +8.096469e-10 | +8.113e-10 | 4.034586e-06 | 4.034586e-06 | 9.4442e-06 |

## VDD = 3.0 V, slew = 2 ns, FO4

| hold (ns) | steps | VOUT tail mean (V) | max \|e\| over WHOLE hold (mV) | max \|e\| in last 100 ns (mV) | e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 400 | 20015 | 2.8874 | 3808.60 | 0.00 | +0.00 | 259.6 | RECOVERED | n/a |

- Sample at the largest last-100 ns error: t = 520.0 ns after the last edge, VIN = 0.0000 V, VOUT = 2.9999 V, VOUT_OP = 2.9999 V, signed error = +0.00 mV.

- VOUT_OP = 2.9999 V, I(VPWR_D)_OP = -3.537e-08 A, I(VPWR_L)_OP = -2.007e-12 A
| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |
|---|---:|---:|---:|---:|---:|---:|---:|
| dut | 400 | -3.536768e-08 | -3.491648e-08 | +4.512e-10 | 2.272037e-06 | 2.272038e-06 | 5.0450e-06 |
| load | 400 | -2.007195e-12 | -2.977040e-09 | -2.975e-09 | 1.480587e-05 | 1.480587e-05 | 3.4189e-05 |

## VDD = 3.0 V, slew = 10 ns, FO4

| hold (ns) | steps | VOUT tail mean (V) | max \|e\| over WHOLE hold (mV) | max \|e\| in last 100 ns (mV) | e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 400 | 20011 | 2.7890 | 3198.71 | 0.00 | +0.00 | 273.0 | RECOVERED | n/a |

- Sample at the largest last-100 ns error: t = 520.0 ns after the last edge, VIN = 0.0000 V, VOUT = 2.9999 V, VOUT_OP = 2.9999 V, signed error = +0.00 mV.

- VOUT_OP = 2.9999 V, I(VPWR_D)_OP = -3.537e-08 A, I(VPWR_L)_OP = -2.007e-12 A
| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |
|---|---:|---:|---:|---:|---:|---:|---:|
| dut | 400 | -3.536768e-08 | -3.527311e-08 | +9.457e-11 | 4.765638e-07 | 4.765639e-07 | 1.0395e-06 |
| load | 400 | -2.007195e-12 | -6.146416e-10 | -6.126e-10 | 3.046703e-06 | 3.046703e-06 | 7.1302e-06 |

## VDD = 3.5 V, slew = 10 ns, FO4

| hold (ns) | steps | VOUT tail mean (V) | max \|e\| over WHOLE hold (mV) | max \|e\| in last 100 ns (mV) | e_final signed (mV) | t_restore (ns) | status | tau_eff (ns) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 400 | 20011 | 3.2714 | 3698.72 | 0.00 | +0.00 | 267.5 | RECOVERED | n/a |

- Sample at the largest last-100 ns error: t = 520.0 ns after the last edge, VIN = 0.0000 V, VOUT = 3.4999 V, VOUT_OP = 3.4999 V, signed error = +0.00 mV.

- VOUT_OP = 3.4999 V, I(VPWR_D)_OP = -3.544e-08 A, I(VPWR_L)_OP = -2.539e-12 A
| branch | hold (ns) | I_OP (A) | mean(I_tail) (A) | mean-I_OP (A) | RMS(I-tail mean) (A) | RMS(I-I_OP) (A) | I_pp (A) |
|---|---:|---:|---:|---:|---:|---:|---:|
| dut | 400 | -3.543834e-08 | -3.574439e-08 | -3.061e-10 | 1.541918e-06 | 1.541918e-06 | 3.3695e-06 |
| load | 400 | -2.538758e-12 | +1.951661e-09 | +1.954e-09 | 9.718463e-06 | 9.718464e-06 | 2.2742e-05 |

