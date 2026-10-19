# D0-D3 inverter DC operating point and load topology: fd variant

A gate load is capacitive and passes no DC current, so a final output
level must not depend on it. These runs separate a DC balance problem
from a dynamic non-relaxation problem.

`pmos_e` selects the PMOS back gate: `SUB` is the PDK's common-substrate
four-terminal contract; `VDD` is the historical diagnostic tie that the
depletion audit flagged as a different topology.

## D0 forced DC, NO load

| card | pmos_e | VIN | VOUT (V) | PMOS VSG | PMOS VSD | NMOS VGS | NMOS VDS | PMOS ID (A) | NMOS ID (A) | PMOS IG (A) | VDD I (A) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bridge_fd / SUB / VIN=0 | 2.9999 | -3.000 | -0.000 | 0.000 | 3.000 | +3.537e-08 | +3.537e-08 | +1.380e-48 | -3.537e-08 |
| bridge_fd / SUB / VIN=3 | 0.0000 | 0.000 | -3.000 | 3.000 | 0.000 | +2.002e-12 | +2.002e-12 | -9.383e-49 | -2.002e-12 |
| bridge_fd / VDD / VIN=0 | 2.9999 | -3.000 | -0.000 | 0.000 | 3.000 | +3.537e-08 | +3.537e-08 | -1.661e-48 | -3.537e-08 |
| bridge_fd / VDD / VIN=3 | 0.0000 | 0.000 | -3.000 | 3.000 | 0.000 | +7.672e-13 | +7.672e-13 | -8.975e-49 | -7.672e-13 |
| stage1_reference / SUB / VIN=0 | 3.0000 | -3.000 | -0.000 | 0.000 | 3.000 | +5.357e-09 | +5.357e-09 | -1.284e-48 | -5.357e-09 |
| stage1_reference / SUB / VIN=3 | 0.0000 | 0.000 | -3.000 | 3.000 | 0.000 | +7.254e-13 | +7.254e-13 | -4.287e-47 | -7.254e-13 |
| stage1_reference / VDD / VIN=0 | 3.0000 | -3.000 | -0.000 | 0.000 | 3.000 | +5.357e-09 | +5.357e-09 | -1.284e-48 | -5.357e-09 |
| stage1_reference / VDD / VIN=3 | 0.0000 | 0.000 | -3.000 | 3.000 | 0.000 | +7.254e-13 | +7.254e-13 | -4.287e-47 | -7.254e-13 |

## D1 forced DC with ideal capacitive load

A capacitor cannot change a DC operating point. Any VOUT movement
here means the added element is not purely capacitive.

| card | load | VOUT (V) | PMOS ID (A) | NMOS ID (A) | VDD I (A) |
|---|---|---:|---:|---:|---:|
| bridge_fd | 5 fF | 2.9999 | +3.537e-08 | +3.537e-08 | -3.537e-08 |
| bridge_fd | 100 fF | 2.9999 | +3.537e-08 | +3.537e-08 | -3.537e-08 |
| bridge_fd | 500 fF | 2.9999 | +3.537e-08 | +3.537e-08 | -3.537e-08 |
| bridge_fd | 1000 fF | 2.9999 | +3.537e-08 | +3.537e-08 | -3.537e-08 |
| stage1_reference | 5 fF | 3.0000 | +5.357e-09 | +5.357e-09 | -5.357e-09 |
| stage1_reference | 100 fF | 3.0000 | +5.357e-09 | +5.357e-09 | -5.357e-09 |
| stage1_reference | 500 fF | 3.0000 | +5.357e-09 | +5.357e-09 | -5.357e-09 |
| stage1_reference | 1000 fF | 3.0000 | +5.357e-09 | +5.357e-09 | -5.357e-09 |

## D2 forced DC with a real FO1 gate load

The load inverter has explicit drain (FL), gate (OUT), source and
substrate. A pure gate load must draw no DC current, so any VOUT
change here is a topology or DC-path effect, not drive strength.

| card | load | VOUT (V) | PMOS ID (A) | NMOS ID (A) | load gate I (A) | load supply I (A) | VDD I (A) |
|---|---|---:|---:|---:|---:|---:|---:|
| bridge_fd | none | 2.9999 | +3.537e-08 | +3.537e-08 | 1.380e-48 | -3.537e-08 | -3.537e-08 |
| bridge_fd | FO1 | 2.9999 | +3.537e-08 | +3.537e-08 | 1.380e-48 | -3.537e-08 | -3.537e-08 |
| stage1_reference | none | 3.0000 | +5.357e-09 | +5.357e-09 | -1.284e-48 | -5.357e-09 | -5.357e-09 |
| stage1_reference | FO1 | 3.0000 | +5.357e-09 | +5.357e-09 | -1.284e-48 | -5.357e-09 | -5.358e-09 |

## D3 long transient versus the DC operating point

The input is held at 0 V for 10 ms so the output has every chance to
relax. If the tail stays away from the D0 operating point, the level
is a dynamic artefact; if it equals D0, the DC balance itself is low.

| card | load | VOUT_OP (V) | VOUT_tail mean (V) | tail pp (mV) | supply tail RMS (A) | PMOS ID tail (A) | NMOS ID tail (A) | VOUT_tail - VOUT_OP (mV) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| (skipped) | | | | | | | | |


## How to read this

- **VOUT(D0, no load) close to VDD but the transient tail far below it**:
  the DC solution is fine and the problem is dynamic (state, initial
  condition, numerical integration, floating nodes).
- **VOUT(D0, no load) already low**: the DC balance, bias or connectivity is
  wrong, and no amount of transient sweeping or DUT upsizing fixes it.
- **VOUT unchanged by ideal capacitors (D1) but moved by a real gate (D2)**:
  the load inverter introduces a DC path that a gate should not have.
- **PMOS VSG near -3 V with PMOS VSD well inside saturation but little
  current**: the question becomes where the PMOS current goes, which the
  per-device currents above are meant to answer.

Timing acceptance remains blocked until VOH >= 2.85 V, VOL <= 0.15 V and
|VOUT_tail - VOUT_OP| <= 10 mV are all satisfied on the same condition.

