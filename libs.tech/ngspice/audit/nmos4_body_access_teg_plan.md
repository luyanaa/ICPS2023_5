# NMOS4 body-access TEG/TCAD plan (not executed)

Purpose: qualify the physical path from the P+ body dot through the N_CH film
to the channel. The current PDK only proves geometric connectivity and an
explicit model terminal; it does not measure body access resistance, its bias
dependence, or whether the fabricated device is FD or PD.

## Structures

1. **Kelvin body bridge**: P+ dot contacted by two independent CNT/ML1
   terminals; force current through one, sense the other. Repeat with several
   P+ dot sizes and distances to the gate edge.
2. **Multi-spacing TLM** between the P+ dot and the channel edge, mirroring the
   existing source/drain TLM, to separate sheet resistance from contact
   resistance.
3. **Back-gate sweep**: `V_SUB = -5..+5 V` at fixed D/G/S, on both polarities.
   FD samples should respond to back-gate bias; PD samples retain a screening
   neutral region.
4. **Body-bias transfer**: `V_P` sweep at several `V_G`, `V_D`, and temperature
   points, with the internal body potential monitored where the model/engine
   exposes it.
5. **15 nm gate-oxide MOSCAP** (already a TEG requirement) to calibrate `D_it`
   and flatband independently of the body path.

## Quantities to extract

- `R_body_access(V_G, V_D, V_E, T)` and its separation from the P+ sheet.
- `g_mb,P = dI_D/dV_P` and `g_m = dI_D/dV_G` over bias; equivalent
  `dV_th,eq/dV_P = -g_mb,P/g_m` as a small-signal ratio, not a measured Vth.
- Internal body potential/charge (`Q_B`, `V_BS`) versus `V_P` and `V_E`.
- FD/PD classification per polarity; NMOS and PMOS independently.

## TCAD cross-checks

- 2-D electrostatics with the measured film/BOX thickness and the N_CH mask
  geometry; sweep P+ dot geometry to bound the body path resistance.
- Compare ideal-FD (`SOIMOD=2`) and unified (`SOIMOD=1`) predictions against
  the measured body path; do not promote either candidate on numerical
  stability.

## Acceptance gates before any device qualification

- Measured `R_body_access` with uncertainty; no LVS-only claim.
- Repeatable `g_mb,P` above the numerical-resolution floor (see
  `audit/nmos4_body_bias_resolution.md`).
- Back-gate response consistent with the assigned FD/PD mode.
- Model card parameters (`NCH`, `K1B`, `K2B`, body/flatband terms) constrained
  by body-bias data, not retuned to force a visible response.

Until these gates close, NMOS4 remains a bounded five-terminal post-layout
experiment; digital standard cells stay on NMOS3/PMOS3.
