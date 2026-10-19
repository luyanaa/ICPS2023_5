# NMOS4 explicit-P bias capability audit

Status: **MODEL_CAPABILITY_ONLY_NOT_SILICON_QUALIFIED**.

Fixed D/G/S/E; the fifth P terminal is swept. This asks only whether the candidate model responds to the explicit P terminal. It is not silicon body-bias qualification and does not validate body access.

| card | engine | geometry |
|---|---|---|
| unified (SOIMOD=1) | ngspice BSIMSOI 4.4 (LEVEL=58) | W20/L6 um |
| ideal-FD (SOIMOD=2) | Xyce 7.10 BSIMSOI 4.5.0 (LEVEL=70450) | W20/L6 um |

## unified_soimod1

| |VDS| (V) | |VGS| (V) | points | ID min (A) | ID max (A) | ID span (A) | dID/dVP (A/V) | max |IP| (A) | status |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 1.5 | 31 | 0.000275281 | 0.000275288 | 7.064e-09 | 1.85828e-09 | 4.15e-17 | observed (ok) |
| 3 | 3 | 31 | 0.00120853 | 0.00120855 | 2.131e-08 | 5.65718e-09 | 4.15e-17 | observed (ok) |

Forward P sweep (-3..3 V) retained as a negative regression: **PHYSICAL_SANITY_FAIL** (ID max 8.96141e+23 A). It is never used as a body-effect result.
## ideal_fd_soimod2

| |VDS| (V) | |VGS| (V) | points | ID min (A) | ID max (A) | ID span (A) | dID/dVP (A/V) | max |IP| (A) | status |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 1.5 | – | – | – | – | – | – | IDEAL_FD_EXPLICIT_P_UNSUPPORTED: Xyce rejects an externally specified fifth P node for SOIMOD=2 with TNODEOUT=0 (log retained in the run work directory) |
| 3 | 3 | – | – | – | – | – | – | IDEAL_FD_EXPLICIT_P_UNSUPPORTED: Xyce rejects an externally specified fifth P node for SOIMOD=2 with TNODEOUT=0 (log retained in the run work directory) |


## Observations

* unified SOIMOD=1: sweeping P over -3..0 V changes ID by at most 2.57e-05 relative (max |IP| 4.15e-17 A); no meaningful body control is demonstrated in this window.
* ideal-FD SOIMOD=2: Xyce 7.10 rejects an externally specified fifth P node when SOIMOD=2 and TNODEOUT=0 ('P node is not used in this mode'). The FD card's explicit-P body-bias capability is therefore untestable in its supported engine configuration; a five-terminal ngspice parse is not evidence of FD body control.

## Limitations

* A response (or lack of response) to the explicit P terminal is a property of the candidate card, not of the fabricated device.
* The two cards run on different simulator versions (ngspice BSIMSOI 4.4 vs Xyce 4.5.0); their numbers must not be mixed or read as one model on two backends.
* The FD card is experimental and remains non-nominal.
* The N_CH body path from the P+ dot to the channel is not extracted; no body-access resistance or bias dependence is qualified here.
* Body-bias/C-V and FD/PD assignment still require TEG data.
* Xyce 7.10 / BSIMSOI 4.5.0 with SOIMOD=2 and TNODEOUT=0 rejects an external fifth P node, so the ideal-FD card cannot be exercised as a five-terminal device in its scoped engine configuration.
