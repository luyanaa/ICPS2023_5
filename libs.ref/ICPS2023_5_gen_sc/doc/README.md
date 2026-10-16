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
| LEF | `lef/<cell>.lef` | Accepted cell geometry and pin/access/obstruction contract; no qualified site or row pitch |

The SPICE file defines `nch`/`pch` subcircuits and declares global `SUB`; it
does not embed model cards. A simulation deck must load the `b4soi` section from
`libs.tech/ngspice/icps2023_5.lib.spice` and include the cell library. The MOS
model is a provisional nominal W=24 µm/L=6 µm fit, not silicon- or
corner-qualified.

No `cdl`, Liberty, or published Verilog view exists. The experimental
`libs.tech/stdcell/export_views.py` emits structural Verilog and accepted-cell
black-box declarations on request, but refuses Liberty: nominal model arcs,
geometry scaling, wire coupling, placement site, and timing corners are not
qualified.

## Regenerating

From the repository root:

```bash
nix develop --command python3 libs.ref/ICPS2023_5_gen_sc/tools/make_gen_sc.py
```

The default command validates and reuses unchanged accepted views. Select
`--build-cell <CELL>` (repeatable) to build and strictly verify new or changed
cells; `--rebuild-published` explicitly rebuilds published layouts, while
`--reverify` reruns strict checks on unchanged GDS after a deck-only change.
Unselected cells are not needlessly re-simulated. Publication is replaced only
after every selected cell passes; a failure leaves the previous package intact.

Use `--candidate-output <NEW_DIRECTORY>` with selected cells for private
experiments. Select an explicit `--height-um` and
`--architecture compact_rows|channel`, or use `--auto-channel-height` for the
per-cell channel-template bound. `--pin-order-mode reverse_signals`, `--mirror`,
`--width-um`, and `--fingers` describe explicit private variants; automatic
height is not a process-minimum or qualified track count. Private
`attempts.json` retains failed geometry/physical results without advertising a
failed GDS as accepted.

Channel generation shares diffusion only along topology-safe Euler trails,
grouped by polarity and exact MOS W/L, using the existing native-island
generator. It retains native TiN/CNT/ML1 pin buses and physical per-finger
metadata. A private 16-cell shared-diffusion bank passed strict physical
checks; 13 of its 14 combinational cells passed full nominal truth/arcs.
OAI21's retained arc simulation failure is not a pass. Six minimum logic
cells also passed 24 zero-gap array cases, including mirrored and rotated
neighbors; their instance-scoped ideal test supplies do not prove a shared PDN.

`libs.tech/stdcell/routed_benchmarks.py --manifest <PRIVATE_MANIFEST> --out <NEW_DIRECTORY>`
runs actual RTL synthesis, accepted-cell ABC and primitive covers, three
physical PDNs, strict checks and whole-core electrical checking. It ranks
only completed routed-core area, requires exact containment in the measured
available frame polygon, and preserves failed attempts. This is not frame-pad,
power-source, site, timing-library, or process qualification.

## Limits

* Only the exact cells listed as `strict_physical_pass` in the manifest have
  published GDS. That evidence is per-layout acceptance under the checked-in
  native-1M decks, not process qualification.
* Static TG and gated latch/DFF candidates have private physical and
  no-internal-load state evidence. The shared-diffusion gated latch and TG DFF
  passed both stored states and 10/1000µs stopped-clock checks at nominal
  3V/25°C, 10fF, 2ns slew with explicit Gear integration. The DFF's six
  nonconverged unsafe aperture samples remain failed; conservative bounds
  require every tested sample at and above the reported bound to pass.
  Earlier trap-integration failures remain recorded. None of this substitutes
  for a routed counter/shift-register check or qualifies a sequential/site
  contract.
* No process-qualified technology LEF, placement site, routing tracks, timing
  corners, Liberty contract, or OpenROAD/LibreLane flow is shipped.
* M3–M4 I/O acceptance and ESD qualification remain open; SRAM is deferred.
  See the root README and `libs.tech/stdcell/config.json` for the preserved
  process, I/O, and SRAM caveats.
