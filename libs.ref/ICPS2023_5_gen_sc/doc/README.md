# ICPS2023_5_gen_sc — experimental 1M standard-cell references

This is a partial, experimental reference package, not a qualified standard-cell
library or placed-core flow. The directory name follows the existing
`ICPS2023_5_<lib_src>` convention while retaining this repository's `_5` PDK
revision.

## Views

| View | Contents | Qualification |
|------|----------|---------------|
| SPICE | `spice/ICPS2023_5_gen_sc.spice` | Transistor-level reference subcircuits for all 26 canonical cell specifications; functional/model reference only |
| GDSII | `gds/<cell>.gds` | Only cells with strict native-1M DRC, LVS, PEX, and assembly success; exact membership and evidence are in `cell_manifest.json` |

The SPICE file defines `nch`/`pch` subcircuits and declares global `SUB`; it
does not embed model cards. A simulation deck must load the `b4soi` section from
`libs.tech/ngspice/icps2023_5.lib.spice` and include the cell library. The MOS
model is a provisional nominal W=24 µm/L=6 µm fit, not silicon- or
corner-qualified.

No `cdl`, `lef`, `lib`, `verilog`, or `xschem` view is published: the current
stdcell flow has no independent exporter/contract for those views. In
particular, no row/site, abutment, LEF obstruction, timing arc, drive strength,
or placement/routing contract is established. Missing views are intentional,
not empty placeholders.

## Regenerating

From the repository root:

```bash
nix develop --command python3 libs.ref/ICPS2023_5_gen_sc/tools/make_gen_sc.py
```

The default command regenerates the all-cell SPICE subcircuit library and
re-verifies every GDS already present in `gds/`. Cells without published GDS
remain `not_published` in the manifest. Build and strictly verify an individual
candidate with `--build-cell <CELL>`; repeat the option to select multiple
cells. A GDS is copied into the package only after strict DRC/LVS, PEX, and
postlayout assembly pass. Failed attempts remain documented in the manifest,
not as GDS views.

## Limits

* Only the exact cells listed as `strict_physical_pass` in the manifest have
  published GDS. That evidence is per-layout acceptance under the checked-in
  native-1M decks, not process qualification.
* Sequential logical references exist, but no sequential GDS is published
  unless its geometry passes strict verification. The current `LATCH_TG_X1`
  H210/40 µm route attempt fails to find an ML1 route for N1; there is no
  sequential strict DRC/LVS/PEX/assembly or postlayout-state evidence.
* No process-qualified technology LEF, placement site, routing tracks, timing
  corners, Liberty contract, or OpenROAD/LibreLane flow is shipped.
* M3–M4 I/O acceptance and ESD qualification remain open; SRAM is deferred.
  See the root README and `libs.tech/stdcell/config.json` for the preserved
  process, I/O, and SRAM caveats.
