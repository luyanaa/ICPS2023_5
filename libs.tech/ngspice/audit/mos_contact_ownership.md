# MOS contact ownership I-V audit

Status: **RDSW_RCNT_SEPARATION_UNCALIBRATED**. Same card and engine for every case; no refit, no RDSW subtraction.

ngspice (** ngspice-44 : Circuit level simulation program)

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
| n_out_vg15 | 45 | 26.35 | 17.01 | 17.01 | -9.332 | -9.332 |
| n_out_vg2 | 43 | 2.845 | 10.58 | 10.58 | +7.74 | +7.74 |
| n_out_vg25 | 44 | 6.787 | 18.34 | 18.34 | +11.55 | +11.55 |
| n_out_vg3 | 41 | 9.819 | 22.6 | 22.6 | +12.78 | +12.78 |
| n_transfer_blue | 32 | 0.2522 | 0.2517 | 0.2517 | -0.000476 | -0.000476 |
| n_transfer_orange | 29 | 0.4072 | 0.4073 | 0.4073 | +3.321e-05 | +3.321e-05 |

Metric per family: output curves = relative RMS % of drain current; transfer curves = log10-current RMS in decades (same convention as the frozen stage-1 regression).

Bare case vs frozen stage-1 manifest: **MATCH** (`fit/b4soi_stage1_baseline.json`).
* full_postlayout reproduces explicit_rcnt on every curve (worst relative difference 9.3e-10): for this single-MOS bench the only external series difference is the added contact network, so the bare-to-case shift is not PEX routing.

## PMOS (pmos_min_fit)

Contact counts read from the actual PEX MOS_MAP: drain=1, source=1; R_unit=200 ohm.

| curve | samples | bare | explicit_rcnt | full_postlayout | ΔRCNT | Δfull |
|---|---:|---:|---:|---:|---:|---:|
| p_out_vg15 | 43 | 17.22 | 14.79 | 14.79 | -2.437 | -2.437 |
| p_out_vg2 | 43 | 3.429 | 2.847 | 2.847 | -0.5818 | -0.5818 |
| p_out_vg25 | 43 | 3.901 | 6.927 | 6.927 | +3.026 | +3.026 |
| p_out_vg3 | 42 | 8.381 | 12.29 | 12.29 | +3.912 | +3.912 |
| p_transfer_blue | 27 | 0.7355 | 0.7352 | 0.7352 | -0.0003383 | -0.0003383 |
| p_transfer_orange | 27 | 0.8847 | 0.8848 | 0.8848 | +0.0001573 | +0.0001573 |

Metric per family: output curves = relative RMS % of drain current; transfer curves = log10-current RMS in decades (same convention as the frozen stage-1 regression).

Bare case vs frozen stage-1 manifest: **MATCH** (`fit/b4soi_stage1_baseline.json`).
* full_postlayout reproduces explicit_rcnt on every curve (worst relative difference 1.3e-08): for this single-MOS bench the only external series difference is the added contact network, so the bare-to-case shift is not PEX routing.

## Limitations

* No Kelvin/TLM or contact-count matrix exists, so the two series terms cannot be declared physically separate.
* full_postlayout carries PEX routing R/C in general; in these single-MOS benches it added no series element beyond the contact network, but on other topologies a shift is a combined confound.
* Both cases reuse one card; the experiment changes only the external network, never the fitted parameters.
* Digitized curves carry the conflicts already recorded in the stage-1/bridge audits; they are the only available DC target.
