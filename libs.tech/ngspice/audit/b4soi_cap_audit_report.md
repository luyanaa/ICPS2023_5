# BSIMSOI charge/overlap capacitance audit (W=24u L=6u, 25 C)

Models: `/Users/yanlu/Documents/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice` (`.lib b4soi`, LEVEL=58, CAPMOD=2, SOIMOD=0).
Method: AC terminal injection (1 kHz, amp per linearity probe) for the 4x4 capacitance matrix, plus a 1 ms quasi-static ramp integrating every terminal current for terminal charge.
Data: `b4soi_cap_audit_ac.csv`, `b4soi_cap_audit_charge.csv`, `b4soi_cap_audit.svg`.

charge-integration self test: 100 fF, ramp 0->1 V over 1 ms: integral=-1e-13 C, error -0.001%, sign=-1
AC linearity probe (NMOS 1.5 V/3 V): amp=5 mV -> Cgg=212.53 fF, amp=10 mV -> Cgg=212.53 fF, amp=20 mV -> Cgg=212.53 fF

## nch (W=24 um, L=6 um)

| quantity | BSIMSOI model | theory (PDK PDF p.9) | ratio |
|---|---:|---:|---:|
| intrinsic Cox (2.3 fF/um^2 x 144 um^2) | - | 331.2 fF | - |
| overlap per side (5.0 fF/um x 24 um) | - | 120.0 fF | - |
| Cgg off (VGS=0, VDS=3) | 19.6 fF | 240.0 fF = 2x overlap | 8% |
| Cgd off (drive: inject G, read D) | 4.3 fF | 120.0 fF | 4% |
| Cgs off (drive: inject G, read S) | 5.8 fF | 120.0 fF | 5% |
| Cgg strong triode (3 V / 0.05 V) | 339.3 fF | 571.2 fF = Cox+2xoverlap | 59% |
| Cgg strong saturation (3 V / 3 V) | 212.0 fF | 460.8 fF = 2/3 Cox+2xoverlap | 46% |
| Cgg strong saturation / Cox | 0.640 | ~0.667 intrinsic part | - |
| Csd off (drain-source, drive S) | 0.1 fF | junction+drain cap (unscored) | flagged |
| Ce-g off (film/BOX, drive G) | 9.4 fF | not a PDF measurement | flagged |
| dQg 0->3 V at VDS=3 | 653.5 fC | intrinsic + overlap charge | - |
| dQg 0->3 V at VDS=0.05 | 935.4 fC | (Cox+2xoverlap) x 3 V = 1714 fC | - |

## pch (W=24 um, L=6 um)

| quantity | BSIMSOI model | theory (PDK PDF p.9) | ratio |
|---|---:|---:|---:|
| intrinsic Cox (2.3 fF/um^2 x 144 um^2) | - | 331.2 fF | - |
| overlap per side (6.8 fF/um x 24 um) | - | 163.2 fF | - |
| Cgg off (VGS=0, VDS=3) | 20.1 fF | 326.4 fF = 2x overlap | 6% |
| Cgd off (drive: inject G, read D) | 4.2 fF | 163.2 fF | 3% |
| Cgs off (drive: inject G, read S) | 5.4 fF | 163.2 fF | 3% |
| Cgg strong triode (3 V / 0.05 V) | 338.7 fF | 657.6 fF = Cox+2xoverlap | 52% |
| Cgg strong saturation (3 V / 3 V) | 211.6 fF | 547.2 fF = 2/3 Cox+2xoverlap | 39% |
| Cgg strong saturation / Cox | 0.639 | ~0.667 intrinsic part | - |
| Csd off (drain-source, drive S) | 0.0 fF | junction+drain cap (unscored) | flagged |
| Ce-g off (film/BOX, drive G) | 10.5 fF | not a PDF measurement | flagged |
| dQg 0->3 V at VDS=3 | -565.8 fC | intrinsic + overlap charge | - |
| dQg 0->3 V at VDS=0.05 | -797.9 fC | (Cox+2xoverlap) x 3 V = 1973 fC | - |

Theory anchors: Lov=2 um/side (p.4/p.16), Tox=15 nm (p.4). eps-based Cox 331.5 fF vs measured-density 331.2 fF; eps-based per-side overlap 110.5 fF vs measured 120.0 fF (difference is fringe).
Overlap-cap wiring (probed in this build): CGSO is the gate-source overlap (F/m) and appears in Cgg and both Cgs directions; CGDO is the gate-drain overlap and appears in Cgg and both Cgd directions; neither changes DC.  Unset CGSO/CGDO is empirically identical to 0 in this ngspice-47 LEVEL=58 build (probe: off-state Cgs bit-identical with CGSO=0), so the Berkeley-manual 0.6*TSI*Cox default approximation is not active here; the residual 4-7 fF off-state terms are intrinsic film coupling, not a default.
AS/AD/PS/PD are instance geometry parameters supplied by LVS/GDS, not model-card parameters; the audit decks omit them, so the model computes zero junction area terms.  SOI body capacitances (C_S,P, C_D,P, C_S,E, C_D,E) and BSIMSOI's external-substrate overlap CGEO are a separate scope item, not scored here.
Charge-method note: Qg is the gate displacement charge (no DC path, validated by the 100 fF self test and AC cross-check).  Qd/Qs individually include the ramp's DC conduction integral; their SUM is the d/s-pair charge, and the four terminal integrals satisfy KCL to ~1e-17 C.  The ramp-integrity check (attempt ladder in tools/audit_b4soi_caps.py) rejects truncated runs after an ngspice abort.
PDK diffusion capacitance 0.086 fF/um^2 (p.9) has no identified return conductor in the PDF; it is flagged here and excluded from the scoring.

