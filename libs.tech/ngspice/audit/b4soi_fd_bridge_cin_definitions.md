# Load input capacitance: three definitions, one recorded transient

Card: `libs.tech/ngspice/models/b4soi_fd_bridge_experimental.inc` (EXPERIMENTAL, SOIMOD=2)
Bench: x1 DUT (W24 NMOS + W48 PMOS) driving x1 reference gate load,
VDD = 3 V, 2 ns edge, separate DUT/load supply branches.
All integrals: `waveint.py` (boundary-interpolated net/gross/E+/E-,
additivity-invariant; self-tests must pass before any number here).

The p.9 overlap-derived quasi-static value (~0.97 pF) and the dynamic
effective value (~2.6 pF) are NOT the same quantity and must not be
merged into one pin capacitance.

## E_DUT window audit (I(VPWRD) x VDD)

**Previous run's whole-cycle definition confirmed**: `0 - times[-1]` = [0, 620.0 ns]. The old segment list omitted the 20-21 ns window below, so the segment sum was missing exactly E(20-21 ns).

Partition coverage check: PASS: report windows tile [0, 620 ns] exactly; segment sums are comparable to the whole.

| window | net signed (pJ) | gross abs (pJ) | E+ (pJ) | E- (pJ) |
|---|---:|---:|---:|---:|
| pre-edge static (0-20 ns) | -0.0021 | 0.0021 | 0.0000 | 0.0021 |
| input-edge 1st half (20-21 ns) | +3.4914 | 3.4914 | 3.4914 | 0.0000 |
| falling phase | +0.3658 | 8.1672 | 4.2665 | 3.9007 |
| rising phase | -33.8981 | 38.7660 | 2.4340 | 36.3321 |
| **segment sum** | -30.0430 | 50.4267 | | |
| whole period [0, 620.0 ns] | -30.0430 | 50.4267 | 10.1919 | 40.2349 |
| **segment - whole** | +0.0000 | +0.0000 | | |

- Residual attribution: the previous report's segment list missed the 20-21 ns window, whose net energy is **+3.4914 pJ** (gross 3.4914 pJ). The reviewer's prediction was +3.516 pJ; the segment-vs-whole residual of this run closes to float precision (see the segment-whole row above).
- gross(net) vs integral(|P|): whole-window net is -30.0430 pJ but the true absolute-power integral is **50.4267 pJ**; the old `abs(net)` convention under-reported gross by 20.3837 pJ. E+ = 10.1919 pJ, E- = 40.2349 pJ (E- is the energy returned to the rail under the I(V) sign convention; |net| is what a power report should quote as delivered). The waveint identities net = E+ - E-, gross = E+ + E- are asserted per window.
- Additivity invariant spot-checked on raw I(VPWRD) at splits 21 ns and 223.0 ns: E(a,c) = E(a,b) + E(b,c) holds to float precision (asserted, would raise otherwise).

## Static tail check: exactly the last 50 ns, one window

Both figures below use the IDENTICAL window [570.0, 620.0] ns, so VDD * I_mean * dt and VDD * integral(I dt) are the same number by construction; the earlier -0.032 pJ vs -0.0053 pJ mismatch came from comparing windows of DIFFERENT runs (different t_end), not from a statistics error.

| quantity | value |
|---|---:|
| window | 570.00 - 620.00 ns |
| I_mean (signed, trapezoid) | -3.5727e-08 A |
| E_net = VDD * integral(I dt) | -0.00536 pJ |
| I implied by E_net / (VDD*dt) | -3.5727e-08 A |
| identity residual | 3.309e-23 A |
| E_gross = integral(abs(P) dt) | 0.64030 pJ |
| gross/net ratio | 119.48 |

- gross/net = 119.48: an AC component raises the ABSOLUTE power integral but cannot change the signed net beyond its own DC offset; if a previous report quoted a tail net inconsistent with its own window mean, that report mixed windows.

## Gate-charge closure on I(VSO): window-pairing audit

For a load returning to the same internal state, the SIGNED charges of the two half-cycle windows must sum to ~0. The two pairings below integrate the SAME waveform; only the window boundaries differ. Pairing B is the old convention (start at the input half-crossing ~21 ns), which clips the first half of the input edge and shifts both boundaries by ~1 ns.

### A: edge-start boundaries [20,220] + [220,620] ns

| window | Q signed (fC) | E+ (fC) | E- (fC) | C=|Q|/VDD (fF) |
|---|---:|---:|---:|---:|
| window 1 (OUT falls) | -6775.0 | 3538.2 | 10313.2 | 2258.3 |
| window 2 (OUT rises) | +6775.0 | 14280.0 | 7505.0 | 2258.3 |
| **signed closure Q1+Q2** | **+0.0** | | | |

### B: half-crossing boundaries [21.0,223.0] + [223.0,620] ns (OLD)

| window | Q signed (fC) | E+ (fC) | E- (fC) | C=|Q|/VDD (fF) |
|---|---:|---:|---:|---:|
| window 1 (OUT falls) | -7908.0 | 2944.4 | 10852.4 | 2636.0 |
| window 2 (OUT rises) | +7286.4 | 14252.2 | 6965.8 | 2428.8 |
| **signed closure Q1+Q2** | **-621.6** | | | |

## Closure attribution

- OLD pairing closure: -621.6 fC (the reported 625.7 fC residual).
- NEW edge-start pairing closure: +0.0 fC.
- Difference attributable to window convention alone: 621.6 fC.
- If the new pairing still leaves a large residual, the remaining suspects are (in order): un-recovered internal model state at t_end vs t=20 ns, and only then a true trajectory asymmetry. The E+/E- columns above distinguish sign-flipping AC (large E+, E-, small net) from one-way charge offsets.

Only if closure fails under BOTH pairings does the fixed-drain Miller cross-check become worth running.

## Definition 1: C_in_dynamic from the load-branch charge

Q = SIGNED integral of I(VSO) over the transition; C = |Q| / VDD.

| transition | window (ns) | Q signed (fC) | C_in_dynamic (fF) |
|---|---|---:|---:|
| falling (edge-start window) | | -6775.0 | 2258.3 |
| rising (edge-start window) | | +6775.0 | 2258.3 |

The two C_in_dynamic values differ because Miller coupling depends on the switching trajectory; that is expected. Only the SIGNED CLOSURE (part B) is a correctness criterion.

## Definition 2: C_in_intrinsic from the card's own Cgg

Quoted from the existing AC/quasi-static audit of this card
(`audit_b4soi_caps.py`), not re-simulated here. NMOS and PMOS listed separately; the gate-pair total is the sum of both rows.

| card | device | operating point | Cgg (fF) |
|---|---|---|---:|
| fd bridge | NMOS | off (VGS=0, VDS=3) | 255.9 |
| fd bridge | NMOS | strong triode (3 V / 0.05 V) | 575.7 |
| fd bridge | NMOS | strong saturation (3 V / 3 V) | 447.0 |
| fd bridge | PMOS | off (VGS=0, VDS=3) | 342.9 |
| fd bridge | PMOS | strong triode (3 V / 0.05 V) | 661.7 |
| fd bridge | PMOS | strong saturation (3 V / 3 V) | 535.8 |
| stage-1 reference | NMOS | off (VGS=0, VDS=3) | 19.6 |
| stage-1 reference | NMOS | strong triode (3 V / 0.05 V) | 339.3 |
| stage-1 reference | NMOS | strong saturation (3 V / 3 V) | 212.0 |

- The stage-1 PMOS row is pending: the same audit's `pch` section must be quoted before any gate-pair total is computed from this table.

## Definition 3: C_eff_energy (FROZEN pending sweep re-integration)

The sweep's E_DUT numbers were produced by the OLD integrator (mask-windowed, abs(net) gross, and per-transition windows that may not tile the cycle). They must be re-derived with waveint before C_eff_energy = dE/VDD^2 is quoted again. The 2377 fF figure is therefore FROZEN, not confirmed.


## Conclusion

- All energy/charge numbers in future audits MUST come from `waveint.py`; report net, gross, E+ and E- together.
- Every multi-window report MUST pass `waveint.check_partition` against the whole window before its segment sums are compared to a whole-cycle integral.
- C_eff_energy stays frozen until the sizing sweep is re-integrated through waveint.
- The fd card's larger Cgg versus stage-1 is the R2/R3 charge correction, not a regression.

