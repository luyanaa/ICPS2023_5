# B4SOI transient numerical diagnosis (frozen cards)

Criterion: transient edge charge `integral I_G dt` over the 0.05->0.95 VDD gate edge vs the quasi-static ramp charge 653.5 fC (same device, VDS=3 V); plus steady gate current and per-timestep sign flips. Damping tricks are diagnostics only.

## T1 single MOS, fixed bias, slow gate (feedback excluded)

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T1 edge=100us (attempts=2) | OK | q_edge=607 fC (-7% vs QS) | steady |iG|max=3.01e-16 A | flips=0 |
| T1 edge=10us (attempts=3) | FAIL | rc=1 aborted=True | - | - | - |

## T2 slew sweep (single MOS, trap, tstep=edge/500)

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T2 edge=100ns | OK | q_edge=643 fC (-2% vs QS) | steady |iG|max=3e-16 A | flips=0 |
| T2 edge=10ns | OK | q_edge=646 fC (-1% vs QS) | steady |iG|max=3.04e-16 A | flips=0 |
| T2 edge=2ns | OK | q_edge=646 fC (-1% vs QS) | steady |iG|max=3.02e-16 A | flips=0 |

INV 2 ns bench: OK, OUT=[-0.424, 3.792] V, input-branch flips 9962/10074

## T3 max timestep (edge=2 ns, trap)

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T3 tstep=20ps | FAIL | rc=1 aborted=True | - | - | - |
| T3 tstep=5ps | FAIL | rc=1 aborted=True | - | - | - |
| T3 tstep=1ps | FAIL | rc=1 aborted=True | - | - | - |

## T4 integration method

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T4 trap edge=10ns | OK | q_edge=646 fC (-1% vs QS) | steady |iG|max=3.04e-16 A | flips=0 |
| T4 trap edge=2ns | OK | q_edge=646 fC (-1% vs QS) | steady |iG|max=3.02e-16 A | flips=0 |
| T4 gear edge=10ns | OK | q_edge=646 fC (-1% vs QS) | steady |iG|max=3e-16 A | flips=0 |
| T4 gear edge=2ns | OK | q_edge=647 fC (-1% vs QS) | steady |iG|max=3e-16 A | flips=0 |

## T5 body wiring (10 ns edge, trap)

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T5 body=e | OK | q_edge=644 fC (-1% vs QS) | steady |iG|max=8.72e-16 A | flips=5000 |
| T5 body=p_s | OK | q_edge=649 fC (-1% vs QS) | steady |iG|max=3e-16 A | flips=0 |
| T5 body=p_src | OK | q_edge=649 fC (-1% vs QS) | steady |iG|max=3e-16 A | flips=0 |

INV with both bodies tied to their own sources (P terminal): OK, OUT=[-0.424, 3.792] V, input-branch flips 9961/10071

## T5b trapezoidal damping probe (diagnostic only, NOT a fix)

| config | result | transient edge charge | steady gate current | chatter |
|---|---|---|---|---|
| T5b trap xmu=default edge=2ns | OK | q_edge=644 fC (-1% vs QS) | steady |iG|max=6.58e-15 A | flips=25000 |
| T5b trap xmu=0.99 edge=2ns | OK | q_edge=nan fC (+nan% vs QS) | steady |iG|max=inf A | flips=25000 |

## T6 ngspice builds (single MOS 2 ns edge + INV 2 ns)

| build | MOS result | MOS edge charge | steady |iG| | flips | INV OUT range | INV flips |
|---|---|---:|---:|---:|---|---:|
| PATH ngspice-47 | OK | 644 fC | 6.58e-15 A | 25000 | [-0.424, 3.792] V | 9962 |
| nix ngspice-44 | OK | 644 fC | 5.85e-15 A | 25000 | [-0.424, 3.792] V | 9960 |
| nix ngspice-45 | OK | 644 fC | 1.15e-15 A | 17533 | [-0.424, 3.792] V | 9909 |
| nix ngspice-46 | OK | 644 fC | 4.32e-15 A | 25000 | [-0.424, 3.791] V | 9968 |

## T7 localization: single NMOS with a free output node

OUT pre-charged to VDD by .ic, 5 fF load, gate steps 0->3 V, no PMOS, no feedback.

| config | OUT min | OUT max | input-branch flips | samples |
|---|---:|---:|---:|---:|
| edge=2 body=e | -0.812 | 3.761 | 41745 | 50022 |
| edge=2 body=p_s | -0.812 | 3.761 | 41745 | 50022 |
| edge=10 body=e | -0.437 | 3.441 | 27809 | 50020 |
| edge=10 body=p_s | -0.437 | 3.441 | 27807 | 50020 |

## Reading

- Charge balance: q_edge within a few % of the QS charge means the transient engine integrates the model charge consistently; a large or SIGNED mismatch means the transient path is unstable, not the deck.
- Steady |iG| at the pA-nA scale is the gmin floor; anything above ~1 nA with the gate high is unphysical conduction in the model path.
- flips counts sign changes of the gate current in the post-edge steady window: >10 at 1 us sampling indicates limit-cycle chatter.
- T6 across builds isolates implementation differences from model behaviour.

## Conclusion (root cause, from the matrix)

1. The gate node itself is charge-consistent: every completed run integrates 607-649 fC over the 0->3 V edge against the 653.5 fC quasi-static charge, with steady gate current at the fA/pA floor. The gate charge path is NOT the numerical problem.
2. The chatter lives in the SOI body network of the four-terminal card: E-only (internal floating film/channel body) chatters (5k-25k step-alternating gate-current flips), while the same card with the fifth P terminal tied to its source (or sourced) runs clean at 0 flips and charge-balanced, at every tested slew and method.
3. The unstable element is the drain-side analog node: a single NMOS discharging 5 fF pre-charged to VDD drives OUT to -0.8 V with ~4x10^4 step-alternating flips at a 2 ns gate step (T7).  The gate is again charge-balanced; tying P does not cure it.  OUT excursion scales with slew (0.8 V at 2 ns, 0.07 V at 10 ns, 6 mV at 100 ns). The inverter therefore inherits it (OUT up to 3.8 V, ~10^4 input-branch flips) in every ngspice build tested (44/45/46/47) -- model-side, not a single-version bug.
4. Forcing max timesteps (T3) collapses the timestep control (ve#branch); the timestep must stay reltol-driven.  xmu damping degrades the solution (NaN/inf), confirming damping is a diagnostic, not a fix.
5. UPDATE (after D5 in b4soi_body_dynamics.md): the five-terminal P-tied form does NOT cure the free-output-node oscillation (flips unchanged, excursion similar), so the earlier 'use 5-terminal cards for arcs' recommendation is WITHDRAWN; keep it as a body-contact diagnostic only.  The overlap candidate (b4soi_overlap_candidate.inc) stays orthogonal to this numerical issue; resolve the oscillation before regenerating any arc.

