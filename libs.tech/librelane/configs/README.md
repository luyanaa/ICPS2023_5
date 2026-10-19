# Explicit LibreLane experiment configurations

These JSON files are **configuration fragments**, not qualified PDK closure or
complete runnable designs. LibreLane loads a PDK's root Tcl configuration,
then the selected `STD_CELL_LIBRARY` and `PAD_CELL_LIBRARY` Tcl configs; the
JSON files below override the supported design/flow variables for one explicit
profile. They are not standalone substitutes for the PDK/SCL contracts.

The root PDK configuration remains the default 1M/1x1 setup. These fragments
are the explicit selection mechanism for the isolated post-2025 2M overlay and
the derived 2x2 pad template; no arbitrary `ICPS2023_5_*` Tcl selector is used.

- `icps2023_5_1m_1x1.json`: explicit conservative 1M routing and 1x1 fixed-pad template.
- `icps2023_5_2m_post2025_2x2.json`: explicit unqualified 2M geometry overlay and 2x2 fixed-pad template.

The 2x2 frame GDS remains an origin-fixed physical overlay. `FP_DEF_TEMPLATE`
provides the 56 fixed pins and die area to the flow; it does not make the full
macro. The standard-cell package now includes an experimental single-corner
Liberty view and the `BUF_X1`, `TIEHI_X1`, and `TIELO_X1` utility cells. The
profiles supply an unqualified `ICPS2023_5_ROW700` site and experimental PDN
and partial-RCX settings so PDK validation completes. They remain design
fragments (not RTL/design configs) and make no PnR/signoff claim. Both select
the Classic flow while removing Magic/Netgen steps; KLayout streamout/DRC and
the repository's separate KLayout PEX path remain. See `../README.md` for the
variable/source table, loader commands, and remaining design-side inputs.
