# Switching impact: frozen b4soi cards vs measured overlap caps

Dynamic bench conditions from libs.tech/stdcell/characterize.py: VDD=3 V, CLOAD=5 fF, 2 ns edges, W/L=24/6 um, 25 C.  The frozen cards never change; overlaps enter as bench-external lumped capacitors of the PDK p.9 measured size (5 fF/um NFET / 6.8 fF/um PFET x W=24 um = 120 fF / 163 fF per side).

## Per-switch gate charge (quasi-static 1 ms ramp)

| device | VDS | frozen model | + measured overlap | theory | shortfall of overlay vs theory |
|---|---:|---:|---:|---:|---:|
| nch | 3.00 V (on-rail) | 653 fC | 1370 fC | 1327 fC | -3% |
| nch | 0.05 V (triode) | 935 fC | 1655 fC | 1714 fC | 3% |
| pch | 3.00 V (on-rail) | 566 fC | 1545 fC | 1485 fC | -4% |
| pch | 0.05 V (triode) | 798 fC | 1777 fC | 1973 fC | 10% |

## Dynamic-flow consequence (charge balance)

- Frozen model per 3 V switch: NMOS 653 fC, PMOS 566 fC.
- Same deck plus the PDK-measured overlap: NMOS 1370 fC, PMOS 1545 fC.
- The frozen cards therefore compute only 48% (NMOS) / 37% (PMOS) of the physical per-switch gate charge; the missing charge is the overlap term, absent from the frozen cards.
- On the 5 fF characterize bench load (15 fC per window) one W24 gate switch is 91x larger: at realistic fanout the intrinsic gate charge dominates the arc, and every fanout gate is under-charged by ~52%.
- Effective gate load the frozen cards present to the driving stage: NMOS 218 fF vs physical ~457 fF; PMOS 189 fF vs physical ~515 fF.

This is the quantitative path between the charge/overlap audit and the optimistic dynamic model: the transient flow integrates exactly these charges, so arc delays and transitions are computed against an under-loaded fanout and come out fast.  Setting the SOI overlap parameters (CGSO/CGDO, or equivalent bench caps) to the measured 5/6.8 fF/um before extracting any timing is required for a defensible arc.

## Transient stability probe (frozen cards, 2 ns edges)

- b4soi   deck: OUT spans [-0.410, 3.527] V; input-branch sign flips 9971 of 10080 samples.
- level1  deck: OUT spans [-0.000, 3.000] V; input-branch sign flips 0 of 10032 samples.

The identical deck on a LEVEL1 control deck stays within [0, VDD] with zero chatter, so the rail excursion and gate-current oscillation are properties of the frozen B4SOI cards, not of the bench.  Root cause per the T1-T7 matrix in b4soi_transient_diagnosis.md: the drain-side analog node of the PD-SOI body-capacitance network at ns-scale slew (a lone NMOS discharging 5 fF swings OUT to -0.8 V with 4x10^4 step flips; the gate charge itself stays charge-balanced to within 1% of the quasi-static value at every slew).  It is slew-triggered, not fixed by body-tied P terminals, not by XMU damping, and reproduces on ngspice 44/45/46/47.  Transient-level arc numbers from the current cards therefore carry BOTH the charge deficit above and a numerical-quality caveat; the quasi-static curves in b4soi_cap_audit_charge.csv and b4soi_cap_audit_ac.csv remain the trustworthy charge accounting until this is resolved.
