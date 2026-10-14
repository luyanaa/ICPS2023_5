# ICPS2023_5 xschem library

This directory contains the xschem symbols for the ICPS2023_5 minimal-fab
SOI-CMOS process.  The process uses 4-terminal MOS devices with a minimum
design gate length of 6 um and a minimum SOI width of 13 um.  The MOS symbol
templates intentionally start at `w=20u l=10u`, matching the PCell defaults;
edit `w`, `l`, `as`, `ps`, `ad`, `pd`, and `m` on an instance as needed.

## Setup

Start xschem with this directory's `xschemrc`, or source it from a project
configuration.  The configuration honors `PDK_ROOT` and `PDK` environment
variables.  With the usual layout (`$PDK_ROOT/ICPS2023_5`), use:

```sh
export PDK_ROOT=/path/to/pdks
export PDK=ICPS2023_5
xschem --rcfile /path/to/pdks/ICPS2023_5/libs.tech/xschem/xschemrc
```

If `PDK_ROOT` already points at the ICPS2023_5 checkout, the fallback detects
that layout too.  The startup file adds `libs.tech/xschem/symbols` to
`xschem_library_path` (and exports the historical `XSCHHEM_SYMBOL_PATH`) and
adds:

```spice
.include $PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice
```

The include file is supplied by the ngspice model package; a testbench should
not include files from outside this PDK.

## Devices

| Symbol | Netlist model/subcircuit | Pins | Defaults |
| --- | --- | --- | --- |
| `nch`, `NMOS_MIN` | MOS model `nch` | D, G, S, B | `w=20u l=10u`, areas/perimeters 0, `m=1` |
| `pch`, `PMOS_MIN` | MOS model `pch` | D, G, S, B | `w=20u l=10u`, areas/perimeters 0, `m=1` |
| `Bridge` | `bridge` | PLUS, MINUS | `l=25u w=10u` |
| `NBridge` | `nbridge` | PLUS, MINUS | `l=25u w=10u` |
| `NDiff_cap` | `cap_ndiff` | PLUS, MINUS | `l=25u w=25u` |
| `PDiff_cap` | `cap_pdiff` | PLUS, MINUS | `l=25u w=25u` |
| `TiN_cap` | `cap_tin` | PLUS, MINUS | `l=25u w=25u` |
| `R_ndiff` | `r_ndiff` | PLUS, MINUS | `l=40u w=10u` |
| `R_pdiff` | `r_pdiff` | PLUS, MINUS | `l=40u w=10u` |
| `ndio` | `dio_ndio` | PLUS, MINUS | `l=25u w=25u` |
| `pdio` | `dio_pdio` | PLUS, MINUS | `l=25u w=25u` |
| `nmos_esd` | `nmos_esd` | PAD, VSS | no geometry parameters |
| `pmos_esd` | `pmos_esd` | PAD, VDD | no geometry parameters |
| `cap_min` | `cap_min` | PLUS, MINUS | `l=25u w=25u` |
| `res_min` | `res_min` | PLUS, MINUS | `l=40u w=10u` |
| `r_poly` | `r_poly` | PLUS, MINUS | `l=40u w=10u` |
| `hr_poly` | `hr_poly` | PLUS, MINUS | `l=40u w=10u` |
| `r_soi` | `r_soi` | PLUS, MINUS | `l=40u w=10u` |
| `r_tin` | `r_tin` | PLUS, MINUS | `l=40u w=10u` |
| `r_ml1` | `r_ml1` | PLUS, MINUS | `l=40u w=10u` |
| `mim_cap` | `mim_cap` | PLUS, MINUS | `l=25u w=25u` |

The corresponding uppercase LTspice-era names (`CAP_MIN`, `RES_MIN`,
`R_POLY_MIN`, `HR_POLY_MIN`, `NDIO_MIN`, `PDIO_MIN`, `NMOS_ESD_MIN`, and
`PMOS_ESD_MIN`) remain available and now emit the same PDK subcircuit
instances.  `HVNMOS_MIN` and `HVPMOS_MIN` remain available as aliases of the
4-terminal PDK MOS symbols; this process has no separate foreign `n6.0` or
`p6.0` high-voltage model.

## Netlisting and pin conventions

MOS symbols emit the native ngspice line

```spice
M... D G S B nch w=... l=... as=... ps=... ad=... pd=... m=...
```

Two-terminal symbols emit `X... PLUS MINUS <subcircuit> ...`.  `PLUS` and
`MINUS` are electrical pin names, not polarity restrictions; wire either pin
as required by the circuit.  The `symname` attribute is visible/editable on
subcircuit instances, so a symbol can be selected or repointed to an
in-package subcircuit without changing the library path.

The generic xschem symbols (`cap`, `res`, `gnd`, `ipin`, `opin`, `iopin`,
`lab_pin`, `code`, `code_shown`, `netlist`, `voltage`, `vac`, `vpulse`, and
`vsin`) are retained for sources, labels, commands, and ordinary schematic
work.  They are not process devices and therefore keep their standard xschem
netlist formats.

## Compatibility notes

* `NMOS_MIN` and `PMOS_MIN` changed from `w=5u l=0.18u` defaults to
  `w=20u l=10u` and now emit all four MOS pins plus area/perimeter parameters.
  This is required by the ICPS2023_5 process (L >= 6 um).
* `HVNMOS_MIN` and `HVPMOS_MIN` no longer reference `n6.0`/`p6.0` or
  submicron geometry.  They use the qualified `nch`/`pch` models and ordinary
  process defaults because the PDK does not provide those foreign models.
* `NDIO_MIN` and `PDIO_MIN` were empty netlisting stubs.  They now draw a
  diode and emit `dio_ndio`/`dio_pdio` instances.
* The old uppercase passive/ESD symbols used primitive or missing netlist
  types.  They now point at the PDK subcircuits listed above.  Existing
  schematics continue to resolve by filename; only their emitted line type
  changed.
