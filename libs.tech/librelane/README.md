# ICPS2023_5 LibreLane technology bring-up

This directory contains an **experimental router overlay**, not a complete or
foundry-qualified LibreLane PDK. The 28-pad frame is physical-only; the partial
`ICPS2023_5_gen_sc` package has accepted GDS/LEF/SPICE views but is not a usable
SCL. There is no qualified placement site, characterized timing library,
antenna repair cell/model, or OpenRCX/capacitance model. A full placement and
routing run is therefore unavailable.

The organization follows the IHP Open-PDK pattern (`libs.tech/librelane/config.tcl`,
per-library `config.tcl`, and library-owned tracks) only. No IHP process values
or layer data are reused.

## Routing assumptions

| LibreLane layer | Direction | Width | Pitch | R/sq | Provenance |
|---|---|---:|---:|---:|---|
| `POLY` | horizontal | 0.12 µm | 0.32 µm | 25 Ω/sq | User-directed experiment; no POLY layer/mask in this PDK |
| `TIN` | vertical | 0.14 µm | 0.28 µm | 5 Ω/sq | User-directed experiment; checked-in DRC requires much larger TiN geometry and the PEX source reports 20 Ω/sq |
| `M1` | horizontal | 6.0 µm | 10.0 µm | 0.20 Ω/sq | `M1` aliases physical `ML1` GDS 8/0; width/space are current DRC minima (6/4 µm), pitch is their sum, sheet R is from the PDK resistance table |

The TiN/POLY gaps in the LEF are pitch minus width. These assumed submicron
TiN/POLY geometries conflict with the checked-in micrometre-scale DRC limits;
they are not DRC-clean, manufacturable, or signoff collateral. M1's 10 µm pitch
is incompatible with a conventional compact 5-track cell unless the physical
rules or the cell-height plan change. No site size or row pitch was supplied,
so this overlay intentionally defines no `SITE` and does not generate cells.

`POLY` is assigned local engineering GDS 200/0 and the three synthetic via cuts
201/0–203/0. These IDs are not canonical process layers or foundry mask
assignments. The actual pad `CNT` remains mapped to 7/0; the experimental
0.08 × 0.08 µm cuts are never aliased to CNT. The POLY↔TiN via is an additional
placeholder required to connect the three-layer stack. Its POLY/TiN landings
are 0.12/0.14 µm squares; user-provided M1 via examples are represented with
6 × 6 µm M1 lands to respect the current M1 width minimum. All small cuts and
non-M1 enclosures remain unqualified assumptions.

`icps2023_5_1m.tech.lef` and `tracks.info` expose only POLY, TiN, and M1. The
routing directions follow the proposed 5T topology:

```text
Track 5 (top)     VDD rail on M1
Track 4           PMOS active / PMOS pin channel
Track 3           TiN vertical interconnect / cross-routing zone
Track 2           NMOS active / NMOS pin channel
Track 1 (bottom)  VSS rail on M1

M1:   horizontal primary signal routing; clocks constrained to M1
TiN:  vertical cell-pin access and row-crossing jumper
POLY: horizontal last-resort jumper
```

The LibreLane layer bounds are `RT_MIN_LAYER=POLY` and `RT_MAX_LAYER=M1`.
`flow_defaults.json` is a **design-config fragment** to merge into a future
LibreLane design config; LibreLane does not load that JSON automatically. It
sets 39% placement density, M1-only clock routing, congestion disallowed, and
antenna repair enabled. The router's min/max layer bounds do not themselves
enforce “90% of signal trunks on M1” or “POLY only when M1 is blocked”; those
policies need route-level guides/costs and validation against actual cells.
Antenna repair is only switched on: no qualified antenna ratios or diode cell
exist yet.

## LibreLane integration

`config.tcl` provides experimental technology-level defaults and selects the
existing physical-only pad library `ICPS2023_5_fd_base`. It deliberately does
**not** set a default `STD_CELL_LIBRARY`.

`ICPS2023_5_gen_sc/config.tcl` follows the per-library organization used by
icsprout55-pdk but copies no process values. It lists only the accepted local
LEF/GDS/SPICE views and their VDD/VSS pins. It intentionally leaves timing
corners/Liberty, `PLACE_SITE`, drive/tie/buffer cells, and qualified PDN/RC/CTS
data unset. The cell LEFs use canonical `ML1`, while the experimental router
technology LEF uses the `M1` alias; no SCL alias LEFs are published.

An isolated Classic configuration used for validation can substitute out
Magic-dependent and selected signoff steps. This substitution is not part of
the checked-in SCL view inventory, does not make the SCL runnable, and does not
replace verification. `MAGIC_PDK_SETUP` is therefore not a prerequisite for
that Magic-free configuration check; it remains required before enabling any
flow that invokes Magic.

The project's process verification path remains independent of Magic: native
KLayout DRC (`libs.tech/klayout/drc/icps2023_5.drc`), native KLayout LVS
(`libs.tech/klayout/lvs/icps2023_5.lvs`), native PEX
(`libs.tech/klayout/pex/icps2023_5_pex.py`), and the ngspice post-layout
assembler are run by the stdcell verification helpers. A future LibreLane
integration may wrap these checks with a custom step, but it must preserve
their explicit 1M profile, strict settings, named-pin contracts, and
fail-closed behavior; substituting a checker away is not verification.

LibreLane 3.0.11 was tested with `--scl ICPS2023_5_gen_sc` and an isolated
Classic config that removed Magic-dependent and selected KLayout/signoff
steps. PDK validation still stops before synthesis or physical design because
`DEFAULT_CORNER`, `STA_CORNERS`, `LIB`, `PLACE_SITE`, synthesis utility cells,
and PDN/RC/CTS inputs are unset. No floorplan, placement, or routing ran. The
config in this directory remains an accepted-view inventory, not a runnable
place-and-route flow.

After cell characterization is complete, LibreLane closure is the first
integration task. Work in dependency order: (1) produce characterized Liberty
and justified default/STA corners, plus a consistent `ML1`/`M1` standard-cell
LEF alias; (2) establish the cell site/row and qualified synthesis drive, tie,
and buffer cells; (3) add sourced PDN geometry, RC/RCX data, CTS buffers and
settings; (4) add a valid `MAGIC_PDK_SETUP` only if Magic steps are enabled,
then rerun PDK validation and exercise the ALU through synthesis, floorplan,
placement, and routing. Do not fill any of these contracts with values copied
from another PDK or guessed from the experimental overlay. Keep LibreLane out
of CI until the complete path passes with those inputs.

`ICPS2023_5_fd_base/config.tcl` supplies the pad LEF/GDS. The frame generator
keeps canonical ML1 LEF/DEF intact and emits separate LibreLane alias views
where `M1` names the same physical ML1 layer 8/0:

- `libs.ref/ICPS2023_5_fd_base/lef/ICPS2023_5_fd_base_librelane.lef`
- `libs.ref/ICPS2023_5_fd_base/def/ICPS2023_5_fd_base_librelane.def`

The DEF carries all 28 fixed pad pins; assign actual design nets separately.
The frame GDS remains an overlay at its original origin, not a 1600 µm placeable
macro. The map is limited to the frame plus experimental routing layers; extend
it from the real standard-cell GDS when that library is created.

The experimental overlay must not be used for fabrication, process DRC/LVS/PEX
claims, timing signoff, or mask generation. The current ICPS2023_5 DRC, LVS,
PEX, and canonical layer table are unchanged.
