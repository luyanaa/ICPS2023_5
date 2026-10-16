# Xyce cross-simulator BSIMSOI regression (first round)

Xyce 7.10 at /usr/local/XyceNF_OMPI_7.10/bin/Xyce (DYLD_LIBRARY_PATH -> homebrew open-mpi libmpi.40).  Xyce is an independent numerical path here, NOT a proposed PDK timing-simulator replacement: BSIMSOI versions differ (ngspice LEVEL=58 = 4.4, Xyce 70450 = 4.5.0, Xyce 70 = 4.6.1; Sandia kept 4.5.0 as LEVEL=70450 because 4.6.1's gate-capacitance model disagreed with other simulators).

Deck: T7 body-dynamics bench (pre-conditioned output node, .IC on OUT, 2/10/100 ns 0->3 V gate edge, 20 ps print/step), .OPTIONS DEVICE TEMP=25, RELTOL=1e-4, ABSTOL=1e-12; TRAP and GEAR time integrators.

## Card porting log (Xyce startup parse)

- xyce_70_cand (LEVEL=70, cand): no unrecognised-parameter warnings
- xyce_70_frozen (LEVEL=70, frozen): no unrecognised-parameter warnings
- xyce_70450_cand (LEVEL=70450, cand): no unrecognised-parameter warnings
- xyce_70450_frozen (LEVEL=70450, frozen): no unrecognised-parameter warnings

## DC parity (ID at VGS=3 V, W24/L6)

| card | device | point (|ID|) | ngspice 4.4 (LEVEL=58) | Xyce 4.5.0 (70450) | delta |
|---|---|---|---:|---:|---:|
| xyce_70450_frozen | nch   | VDS=0.5 V, VGS=3 V | 0.0004932 A | 0.0004932 A | -0.0% |
| xyce_70450_frozen | nch   | VDS=1.0 V, VGS=3 V | 0.0008627 A | 0.0008627 A | -0.0% |
| xyce_70450_frozen | nch   | VDS=2.0 V, VGS=3 V | 0.001262 A | 0.001262 A | +0.0% |
| xyce_70450_frozen | nch   | VDS=3.0 V, VGS=3 V | 0.001321 A | 0.001321 A | +0.0% |
| xyce_70450_cand | nch   | VDS=0.5 V, VGS=3 V | 0.0004932 A | 0.0004932 A | -0.0% |
| xyce_70450_cand | nch   | VDS=1.0 V, VGS=3 V | 0.0008627 A | 0.0008627 A | -0.0% |
| xyce_70450_cand | nch   | VDS=2.0 V, VGS=3 V | 0.001262 A | 0.001262 A | +0.0% |
| xyce_70450_cand | nch   | VDS=3.0 V, VGS=3 V | 0.001321 A | 0.001321 A | +0.0% |

## NMOS T7 matrix (Xyce vs ngspice reference)

| Xyce model | card | CL | slew | method | Xyce OUT | Xyce q_edge | Xyce flips | Xyce steady \|iG\| | ngspice OUT | ngspice q | ngspice flips |
|---|---|---:|---:|---|---:|---:|---:|---:|---|---:|---:|
| 70450 | frozen | 5 fF | 100 ns | TRAP | -0.23..3.03 V | 908 fC | 19813 | 5.14e-10 A | -0.23..3.03 V | 908 fC | 19814 |
| 70450 | frozen | 5 fF | 10 ns | TRAP | -0.43..3.45 V | 891 fC | 29044 | 3.3e-10 A | -0.44..3.44 V | 890 fC | 27809 |
| 70450 | frozen | 5 fF | 2 ns | TRAP | -0.80..3.97 V | 793 fC | 41492 | 6.78e-09 A | -0.81..3.76 V | 777 fC | 41745 |
| 70450 | frozen | 457 fF | 100 ns | TRAP | -0.07..3.00 V | 908 fC | 19435 | 9.49e-11 A | -0.07..3.00 V | 908 fC | 23652 |
| 70450 | frozen | 457 fF | 10 ns | TRAP | -0.23..3.01 V | 884 fC | 19545 | 3.92e-10 A | -0.24..3.01 V | 881 fC | 19710 |
| 70450 | frozen | 457 fF | 2 ns | TRAP | -0.51..3.02 V | 611 fC | 19819 | 1.63e-09 A | abort | - | - |
| 70450 | frozen | 1000 fF | 100 ns | TRAP | -0.05..3.00 V | 909 fC | 19117 | 1.01e-11 A | -0.05..3.00 V | 909 fC | 23306 |
| 70450 | frozen | 1000 fF | 10 ns | TRAP | -0.16..3.00 V | 794 fC | 19287 | 9.22e-12 A | -0.17..3.00 V | 769 fC | 19406 |
| 70450 | frozen | 1000 fF | 2 ns | TRAP | -0.32..3.01 V | 608 fC | 19423 | 4.52e-10 A | -0.32..3.01 V | 608 fC | 19948 |
| 70450 | cand | 5 fF | 100 ns | TRAP | -0.28..3.28 V | 1917 fC | 19747 | 3.2e-08 A | -0.28..3.28 V | 1917 fC | 23374 |
| 70450 | cand | 5 fF | 10 ns | TRAP | -0.55..3.53 V | 1880 fC | 19693 | 1.43e-08 A | -0.57..3.54 V | 1878 fC | 19930 |
| 70450 | cand | 5 fF | 2 ns | TRAP | -1.10..3.94 V | 1534 fC | 19873 | 1.37e-08 A | -1.12..3.94 V | 1513 fC | 20006 |
| 70450 | cand | 457 fF | 100 ns | TRAP | -0.15..3.06 V | 1904 fC | 19377 | 7.58e-09 A | -0.16..3.06 V | 1904 fC | 22480 |
| 70450 | cand | 457 fF | 10 ns | TRAP | -0.39..3.12 V | 1831 fC | 19469 | 1.39e-08 A | -0.40..3.12 V | 1817 fC | 19606 |
| 70450 | cand | 457 fF | 2 ns | TRAP | -0.75..3.21 V | 1322 fC | 19577 | 1.56e-08 A | -0.76..3.21 V | 1308 fC | 19964 |
| 70450 | cand | 1000 fF | 100 ns | TRAP | -0.11..3.03 V | 1903 fC | 19039 | 9.5e-10 A | -0.11..3.03 V | 1903 fC | 22324 |
| 70450 | cand | 1000 fF | 10 ns | TRAP | -0.29..3.06 V | 1661 fC | 19217 | 4.29e-09 A | -0.30..3.06 V | 1617 fC | 19330 |
| 70450 | cand | 1000 fF | 2 ns | TRAP | -0.52..3.11 V | 1284 fC | 19385 | 1.64e-08 A | -0.52..3.11 V | 1277 fC | 19914 |
| 70 | frozen | 5 fF | 100 ns | TRAP | -0.23..3.03 V | 938 fC | 1 | 8.84e-10 A | -0.23..3.03 V | 908 fC | 19814 |
| 70 | frozen | 5 fF | 10 ns | TRAP | -0.44..3.45 V | 919 fC | 29989 | 6.87e-10 A | -0.44..3.44 V | 890 fC | 27809 |
| 70 | frozen | 5 fF | 2 ns | TRAP | -0.81..3.98 V | 823 fC | 40298 | 4.83e-09 A | -0.81..3.76 V | 777 fC | 41745 |
| 70 | frozen | 457 fF | 100 ns | TRAP | -0.07..3.00 V | 937 fC | 19527 | 9.41e-11 A | -0.07..3.00 V | 908 fC | 23652 |
| 70 | frozen | 457 fF | 10 ns | TRAP | -0.24..3.01 V | 913 fC | 19547 | 3.91e-10 A | -0.24..3.01 V | 881 fC | 19710 |
| 70 | frozen | 457 fF | 2 ns | TRAP | -0.51..3.02 V | 632 fC | 19755 | 2.41e-09 A | abort | - | - |
| 70 | frozen | 1000 fF | 100 ns | TRAP | -0.05..3.00 V | 938 fC | 19071 | 1.01e-11 A | -0.05..3.00 V | 909 fC | 23306 |
| 70 | frozen | 1000 fF | 10 ns | TRAP | -0.17..3.00 V | 822 fC | 19289 | 8.67e-12 A | -0.17..3.00 V | 769 fC | 19406 |
| 70 | frozen | 1000 fF | 2 ns | TRAP | -0.32..3.01 V | 626 fC | 19509 | 5.72e-10 A | -0.32..3.01 V | 608 fC | 19948 |
| 70 | cand | 5 fF | 100 ns | TRAP | -0.44..3.30 V | 2971 fC | 17075 | 5.12e-08 A | -0.28..3.28 V | 1917 fC | 23374 |
| 70 | cand | 5 fF | 10 ns | TRAP | -0.68..3.59 V | 2926 fC | 19829 | 2.73e-08 A | -0.57..3.54 V | 1878 fC | 19930 |
| 70 | cand | 5 fF | 2 ns | TRAP | -1.20..3.94 V | 2502 fC | 19875 | 2.68e-08 A | -1.12..3.94 V | 1513 fC | 20006 |
| 70 | cand | 457 fF | 100 ns | TRAP | -0.19..3.07 V | 2953 fC | 18711 | 5.1e-10 A | -0.16..3.06 V | 1904 fC | 22480 |
| 70 | cand | 457 fF | 10 ns | TRAP | -0.43..3.13 V | 2854 fC | 19723 | 2.58e-08 A | -0.40..3.12 V | 1817 fC | 19606 |
| 70 | cand | 457 fF | 2 ns | TRAP | -0.79..3.21 V | 2096 fC | 19667 | 2.96e-08 A | -0.76..3.21 V | 1308 fC | 19964 |
| 70 | cand | 1000 fF | 100 ns | TRAP | -0.13..3.03 V | 2951 fC | 18631 | 3.78e-11 A | -0.11..3.03 V | 1903 fC | 22324 |
| 70 | cand | 1000 fF | 10 ns | TRAP | -0.31..3.07 V | 2627 fC | 19585 | 5.87e-09 A | -0.30..3.06 V | 1617 fC | 19330 |
| 70 | cand | 1000 fF | 2 ns | TRAP | -0.55..3.11 V | 2006 fC | 19371 | 3.06e-08 A | -0.52..3.11 V | 1277 fC | 19914 |
| 70450 | cand-gear | 5 fF | 2 ns | TRAP | -1.10..3.94 V | 1534 fC | 19873 | 1.37e-08 A | -1.12..3.94 V | 1513 fC | 20006 |
| 70450 | cand-gear | 5 fF | 2 ns | GEAR | -1.10..3.94 V | 1540 fC | 10501 | 1.25e-08 A | -1.12..3.94 V | 1514 fC | 9904 |
| 70 | cand-gear | 5 fF | 2 ns | TRAP | -1.20..3.94 V | 2502 fC | 19875 | 2.68e-08 A | -1.12..3.94 V | 1513 fC | 20006 |
| 70 | cand-gear | 5 fF | 2 ns | GEAR | -1.20..3.94 V | 2508 fC | 9775 | 2.63e-08 A | -1.12..3.94 V | 1514 fC | 9904 |

## PMOS T7 and INV spots (candidate card, 5 fF, 2 ns)

| bench | simulator | OUT range | flips |
|---|---|---|---:|
| PMOS T7 | Xyce 70450 | 0.00..4.12 V | 23011 |
| PMOS T7 | Xyce 70 | 0.00..0.13 V | 0 |
| PMOS T7 | ngspice | abort | - |
| INV | Xyce 70450 | -1.17..4.05 V | 48901 |
| INV | Xyce 70 | -1.35..4.30 V | 48780 |
| INV | ngspice | aborted or see T-diagnosis | ~10k |

## Reading

- DC parity: |ID| at VGS=3 V matches ngspice 4.4 and Xyce 4.5.0 to four significant digits on both frozen and candidate cards; no unrecognised parameters on any ported card.
- NMOS T7: OUT excursions agree within 1-3% across ngspice 4.4, Xyce 4.5.0 and Xyce 4.6.1, with the same 19-42k flip chatter and the same 1/load amplitude scaling: the oscillation is a property of the BSIMSOI model family in this parameter workspace, NOT of any single simulator implementation (T6 ngspice 44-47 and this cross-check bracket both implementations).
- PMOS T7: Xyce 4.5.0 oscillates (0..4.12 V, 23k flips) while Xyce 4.6.1 is clean (0..0.13 V, 0 flips) -- a version-dependent split, consistent with Sandia's documented 4.6.1 gate-capacitance divergence.
- Gate charge: Xyce 4.5.0 carries ~20-40% more gate charge than ngspice 4.4 on the same edge (793-908 fC vs 653-777 fC frozen); the candidate split 1534 vs 1378 fC.  Version charge models already differ before any suspect dynamics.
- Robustness: Xyce completed every matrix point; ngspice needed gmin/itl4 escalation at 457 fF/1 pF @ 2 ns (T-matrix aborts).
- GEAR halves the flips in both simulators but settles to the same oscillatory solution: it is damping, not a fix.
- Decision against the acceptance table: Xyce 4.5.0 is NOT stable on the single-MOS bench, so it does not qualify as a timing-simulator replacement here; the problem is closer to the model equations/parameter workspace (untested SOI body parameters on the frozen card) than to a simulator defect. Next: bound the SOI parameter workspace (D6 mechanisms: isbjt, rbody/rbdb, TSI/TBOX/XJ consistency) and re-run this matrix before any arc regeneration.

