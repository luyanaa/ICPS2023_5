# ICPS2023_5 xschem library

This directory contains the xschem symbols for the ICPS2023_5 minimal-fab
process. The process uses four-terminal B4SOI MOS devices with a minimum design
gate length of 6 µm and minimum SOI width of 13 µm. The stock symbols emit pins
in D, G, S, E order. The Nch symbol's `w=20u l=10u` defaults match its native
PCell; the Pch symbol's `l=10u` matches, but its `w=20u` default differs from
the native Pch PCell default `w=40u`. Both symbol defaults are outside the PDF
IV fit point `W=24u L=6u`.

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
.lib "$PDK_ROOT/ICPS2023_5/libs.tech/ngspice/icps2023_5.lib.spice" b4soi
```

This selects the fitted BSIMSOI v4 model section; do not use `.include` for
the selector library.

## Devices

| Symbol | Netlist model/subcircuit | Pins | Defaults |
| --- | --- | --- | --- |
| `nch`, `NMOS_MIN` | MOS model `nch` | D, G, S, E | `w=20u l=10u`, areas/perimeters 0, `m=1` |
| `pch`, `PMOS_MIN` | MOS model `pch` | D, G, S, E | `w=20u l=10u`, areas/perimeters 0, `m=1` |
| `Bridge` | `bridge` | PLUS, MINUS | `l=25u w=10u` |
| `NBridge` | `nbridge` | PLUS, MINUS | `l=25u w=10u` |
| `NDiff_cap` | `cap_ndiff` | PLUS, MINUS | `l=25u w=25u` |
| `PDiff_cap` | `cap_pdiff` | PLUS, MINUS | `l=25u w=25u` |
| `TiN_cap` | `cap_tin` | PLUS, MINUS | `l=25u w=25u` |
| `R_ndiff` | `r_ndiff` | PLUS, MINUS | `l=40u w=10u` |
| `R_pdiff` | `r_pdiff` | PLUS, MINUS | `l=40u w=10u` |
| `ndio` | `dio_ndio` | PLUS, MINUS | `l=25u w=25u` |
| `pdio` | `dio_pdio` | PLUS, MINUS | `l=25u w=25u` |
| `nmos_esd` | `nmos_esd` | PAD, VSS, SUB | no geometry parameters |
| `pmos_esd` | `pmos_esd` | PAD, VDD, SUB | no geometry parameters |
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
M1... D G S E nch w=... l=... as=... ps=... ad=... pd=... m=...
```

The fourth pin is B4SOI `E`, the common external substrate terminal; it is not
a bulk-body `B` pin. The B4SOI model also accepts an optional fifth `P` body
terminal in a direct five-node `M` card. The stock symbols are four-pin and do
not expose `P`; keep `E` on the common substrate net.
The ESD wrappers expose `SUB` as their final pin and connect it to the internal
B4SOI `E` terminal; tie both wrappers to the same intended substrate net.

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

* `NMOS_MIN` and `PMOS_MIN` retain `w=20u l=10u` defaults and emit four MOS
  pins plus area/perimeter parameters. The former fourth pin `B` is now B4SOI
  `E` (external substrate), not a bulk-body connection.
* `HVNMOS_MIN` and `HVPMOS_MIN` no longer reference `n6.0`/`p6.0` or
  submicron geometry.  They use the qualified `nch`/`pch` models and ordinary
  process defaults because the PDK does not provide those foreign models.
* `NDIO_MIN` and `PDIO_MIN` were empty netlisting stubs.  They now draw a
  diode and emit `dio_ndio`/`dio_pdio` instances.
* The old uppercase passive/ESD symbols used primitive or missing netlist
  types.  They now point at the PDK subcircuits listed above.  Existing
  schematics continue to resolve by filename; only their emitted line type
  changed.

## Regression check

Run `ruby libs.tech/xschem/tools/validate_symbols.rb` from the repository root.
It checks symbol structure, device/subcircuit names against the ngspice library,
and ordered xschem pins against the actual ESD `.subckt` interfaces; it does not
require the xschem executable.
