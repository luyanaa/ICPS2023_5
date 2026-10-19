# Tail supply-current fluctuation diagnosis: fd bridge

Card: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc`
Simulator: `/usr/local/XyceNF_OMPI_7.10/bin/Xyce` (Xyce NF 7.10.0, BSIMSOI 4.5.0).

Status under diagnosis: TAIL_CURRENT_AC_UNRESOLVED. The settled tail
carries mean(I) within 0.12 nA of the DC prediction but with 0.62 uA RMS
and 1.36 uA peak-to-peak. No model parameter is modified here.

## A persistence: consecutive 20 ns sub-windows of the last 100 ns

Sustained fluctuation keeps every sub-window near the same RMS; a
still-decaying recovery shrinks window by window.

| sub-window (ns after last edge) | mean (nA) | RMS (nA) | pp (nA) | sign flips | samples |
|---:|---:|---:|---:|---:|---:|
- solver accepted steps reported by Xyce: see the run log; printed
  samples in the window: 5013

| 220-240 | -616186.85 | 252409.34 | 1544847.72 | 1 | 1012 |
| 240-260 | -560959.12 | 38106.42 | 144561.96 | 1 | 1000 |
| 260-280 | -329639.74 | 79874.74 | 271897.25 | 1 | 1000 |
| 280-300 | -107166.17 | 45488.00 | 155886.43 | 5 | 1000 |
| 300-320 | -21897.07 | 10238.64 | 37704.10 | 47 | 1001 |

## B timestep and integrator convergence (VDD=3 V, FO4)

If the alternation frequency rises as tmax shrinks, or the amplitude
changes materially, the fluctuation is numerical. The zero-crossing
rate is per microsecond of the observed tail window.

| setting | tail mean (nA) | RMS(I-mean) (nA) | pp (nA) | sign flips | flips per us | printed samples |
|---|---:|---:|---:|---:|---:|---:|
| trap/20ps | -327800.74 | 266248.24 | 1544847.72 | 2 | 20.0 | 5013 |
| trap/10ps | -327471.83 | 265875.87 | 1544647.35 | 2 | 20.0 | 10012 |
| trap/5ps | -327329.98 | 265779.39 | 1544653.73 | 4 | 40.0 | 20011 |
| gear/20ps | -327838.57 | 266253.06 | 1544647.19 | 2 | 20.0 | 5013 |
| gear/10ps | -327471.72 | 265877.17 | 1544647.21 | 2 | 20.0 | 10012 |

- RMS(I-mean) across settings: CHANGES with the timestep/integrator.

## C static control: VIN held at 0, NO input edge

The same card and topology, started from the DC operating point
(no .IC), with no switching at all. Any fluctuation here is a
model-internal or numerical effect, not FO4 charge.

| card | load | supply tail RMS (A) | pp (A) | tail mean (A) |
|---|---|---:|---:|---:|
| fd | fo4 | 4.914e-13 | 1.525e-12 | -3.537e-08 |
| fd | cap | 1.413e-13 | 5.684e-13 | -3.537e-08 |
| fd | none | 2.120e-13 | 7.730e-13 | -3.537e-08 |
| stage1 | fo4 | 1.068e-13 | 3.958e-13 | -5.357e-09 |
| stage1 | cap | 4.260e-14 | 1.476e-13 | -5.357e-09 |
| stage1 | none | 5.123e-14 | 1.779e-13 | -5.357e-09 |

## Interpretation guide

- A: sub-window RMS flat near 0.6 uA -> sustained fluctuation, not recovery.
- A: sub-window RMS shrinking -> still part of the recovery transient.
- B: alternation frequency rises as tmax shrinks -> solver time-step chatter.
- B: amplitude and frequency stable across settings -> a model response.
- C: fluctuation present with no switching -> model-internal state or
  numerical integration; the FO4 charge hypothesis is then excluded.
- C: fluctuation absent without switching -> it is an FO4 charge effect.

No model parameter is modified by this diagnosis, and no timing or energy
claim is made here.

