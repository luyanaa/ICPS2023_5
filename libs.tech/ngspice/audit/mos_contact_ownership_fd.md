# MOS contact ownership I-V audit

Status: **RDSW_RCNT_SEPARATION_UNCALIBRATED**. Same card and engine for every case; no refit, no RDSW subtraction.

xyce-fd (Xyce 7.10 / BSIMSOI 4.5.0 (LEVEL=70450))

| case | meaning |
|---|---|
| bare | original fitted W24/L6 deck, unchanged |
| explicit_rcnt | same device plus R_cnt = R_unit/N_cnt per D/S side |
| full_postlayout | native PCell -> fab-prep -> LVS -> PEX -> assembly |

## Original measurement

| field | value |
|---|---|
| contact_count_used_in_fit | None |
| note | The original W24/L6 measurement's contact configuration is unreported; a shift between cases cannot itself prove or disprove double-counting. |

## NMOS (nmos_min_fit)

Contact counts read from the actual PEX MOS_MAP: drain=1, source=1; R_unit=150 ohm.

| curve | samples | bare | explicit_rcnt | full_postlayout | ΔRCNT | Δfull |
|---|---:|---:|---:|---:|---:|---:|
| n_out_vg15 | 45 | 30.25 | 19.53 | 19.53 | -10.71 | -10.71 |
| n_out_vg2 | 43 | 7.367 | 8.227 | 8.227 | +0.8592 | +0.8592 |
| n_out_vg25 | 44 | 2.716 | 15.59 | 15.59 | +12.87 | +12.87 |
| n_out_vg3 | 41 | 4.373 | 20.07 | 20.07 | +15.7 | +15.7 |
| n_transfer_blue | 32 | 0.2034 | 0.2039 | 0.2039 | +0.0004472 | +0.0004472 |
| n_transfer_orange | 29 | 0.1983 | 0.1937 | 0.1937 | -0.0046 | -0.0046 |

Metric per family: output curves = relative RMS % of drain current; transfer curves = log10-current RMS in decades (same convention as the frozen stage-1 regression).

Bare-case baseline cross-check: **NOT APPLICABLE** — the FD bridge card is a different, Xyce-scoped card; the stage-1 frozen baseline does not apply. The FD fit manifest's per-curve metrics were themselves computed with ngspice BSIMSOI 4.4, so they are not a cross-check for this Xyce 4.5.0 run either.
* full_postlayout reproduces explicit_rcnt on every curve (worst relative difference 0): for this single-MOS bench the only external series difference is the added contact network, so the bare-to-case shift is not PEX routing.

## PMOS (pmos_min_fit)

Contact counts read from the actual PEX MOS_MAP: drain=1, source=1; R_unit=200 ohm.

| curve | samples | bare | explicit_rcnt | full_postlayout | ΔRCNT | Δfull |
|---|---:|---:|---:|---:|---:|---:|
| p_out_vg15 | 43 | 21.76 | 19.61 | 19.61 | -2.15 | -2.15 |
| p_out_vg2 | 43 | 3.609 | 4.756 | 4.756 | +1.147 | +1.147 |
| p_out_vg25 | 43 | 3.779 | 4.231 | 4.231 | +0.4511 | +0.4511 |
| p_out_vg3 | 42 | 8.997 | 5.813 | 5.813 | -3.184 | -3.184 |
| p_transfer_blue | 27 | 0.2973 | 0.2953 | 0.2953 | -0.001964 | -0.001964 |
| p_transfer_orange | 27 | 0.2531 | 0.2518 | 0.2518 | -0.001272 | -0.001272 |

Metric per family: output curves = relative RMS % of drain current; transfer curves = log10-current RMS in decades (same convention as the frozen stage-1 regression).

Bare-case baseline cross-check: **NOT APPLICABLE** — the FD bridge card is a different, Xyce-scoped card; the stage-1 frozen baseline does not apply. The FD fit manifest's per-curve metrics were themselves computed with ngspice BSIMSOI 4.4, so they are not a cross-check for this Xyce 4.5.0 run either.
* full_postlayout reproduces explicit_rcnt on every curve (worst relative difference 0): for this single-MOS bench the only external series difference is the added contact network, so the bare-to-case shift is not PEX routing.

## Limitations

* No Kelvin/TLM or contact-count matrix exists, so the two series terms cannot be declared physically separate.
* Xyce BSIMSOI 4.5.0 (LEVEL=70450) is a different model version from the ngspice 4.4 nominal path; these FD results must not be mixed with nominal-card results or read as two backends of one model.
* The FD card itself is experimental and version-scoped; this run does not promote it to nominal.
* full_postlayout carries PEX routing R/C in general; in these single-MOS benches it added no series element beyond the contact network, but on other topologies a shift is a combined confound.
* Digitized curves carry the conflicts already recorded in the stage-1/bridge audits; they are the only available DC target.
