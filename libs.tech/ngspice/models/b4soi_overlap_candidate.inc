* ICPS2023_5 BSIMSOI v4.4 charge-only overlap candidate.
*
* SCOPE: this file is a CHARGE-ONLY candidate.  It reproduces the frozen
* models/icps2023_5_b4soi.inc verbatim and adds ONLY the gate-source /
* gate-drain overlap capacitance parameters:
*
*   nch: CGSO=5e-9  CGDO=5e-9   F/m  (= 5 fF/um, PDK p.9 measured)
*   pch: CGSO=6.8e-9  CGDO=6.8e-9 F/m  (= 6.8 fF/um, PDK p.9 measured)
*
* No DC parameter is touched: VTH0/U0/NFACTOR/K1/K2/RDSW, TSI/TBOX/TOX/XJ,
* SOIMOD/MOBMOD/CAPMOD and every other default are byte-identical to the
* frozen cards, so this candidate cannot change any DC operating point or
* I-V curve.  In this ngspice-47 LEVEL=58 build CGSO adds to Cgg and to both
* Cgs directions, CGDO adds to Cgg and both Cgd directions (probed), and
* neither enters the DC solution.
*
* Provenance of the numbers: PDK_minimalFabSOICMOS.pdf p.9 "NFET Overlap
* Cap. 5 fF/um" / "PFET Overlap Cap. 6.8 fF/um" at |V|=2 V, cross-checked
* against eps*Lov*W with Lov=2 um per side (p.4/p.16).  This is the first
* version of the external-gate-capacitance correction; CGSL/CGDL are
* deliberately NOT set so that only the measured loop closes.
*
* STATUS: candidate under verification by
* tools/verify_overlap_candidate.py (DC parity + AC/charge consistency +
* PDF anchors).  Replace the frozen cards only after all three regressions
* pass.
.model nch NMOS (
+ LEVEL=58 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25
+ TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9
+ VTH0=0.25 U0=670 NFACTOR=1.38 K1=0.125 K2=0 RDSW=0
+ CGSO=5e-9 CGDO=5e-9
+ )
.model pch PMOS (
+ LEVEL=58 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25
+ TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9
+ VTH0=-0.71 U0=268.75 NFACTOR=1.67 K1=0.14375 K2=0 RDSW=46250
+ CGSO=6.8e-9 CGDO=6.8e-9
+ )
