# ICPS2023_5_fd_pr — primitive cells

Technology primitives of the minimal Fab SOI-CMOS process, as shipped by the
foundry: the contact, via and pad building blocks that the device PCells
(`libs.tech/klayout/pcells/pcells_v0.1.lym`) instantiate. All cells are
geometry-only; the electrical models live in `libs.tech/ngspice/`.

| Cell | Size (µm) | Contents | Used by |
|------|-----------|----------|---------|
| `dcont` | 13 × 13 | diffusion (S/D) contact cluster | `Nch`, `Pch`, `R_ndiff`, `R_pdiff`, `TIN_cap` PCells |
| `pcont` | 13 × 13 | gate/TiN contact cluster | `Nch`, `Pch`, `TIN_cap` PCells |
| `pcont_min` | 9 × 13 | minimum gate contact | `Nch`, `Pch` PCells |
| `psubcont` | 12 × 12 | p-substrate (body) contact | `Nch` PCell (`with_psubcont`) |
| `nsubcont` | 12 × 12 | n-substrate contact | `Pch` PCell (`experimental_nplus_island`: separate N+ SOI island; not a PMOS body contact) |
| `DCNT` | 13 × 13 | double contact | `psubcont`, `nsubcont` |
| `DCNT2` | 9 × 9 | double contact, small | device contacts |
| `Via` | 13 × 13 | ML1–ML2 via | resistor and capacitor PCells |
| `PAD60` | 60 × 60 | 60 µm pad | pad ring of the contest templates |
| `PAD100` | 100 × 100 | 100 µm pad | alternative pad size |
| `PAD150` | 150 × 150 | 150 µm pad | alternative pad size |

Every cell is shipped in the foundry mask numbering: SOI 4/0, N-diff 3/0,
P-diff 2/0, TiN 5/0, contact 6/0, M1(Al) 7/0, VIA1 8/0, ML2 9/0, PAD opening
10/0 and TEXT 13/1. The `DM_*` dummy-fill marks (101/0-104/0) and the design
NWL layer (1/0) have no mask output and are not present.

The 60 µm pad used by the frame library is `pad_60_m1_tin` in
`libs.ref/ICPS2023_5_fd_base`; `PAD60/100/150` here are the vendor's standalone
pad cells.

## Known DRC markers

Run of `libs.tech/klayout/drc/icps2023_5.drc` with `-rd layer_mode=fab` per cell
(default limits; the shipped file is mask-numbered):

| Cell | Markers |
|------|---------|
| `dcont`, `pcont`, `pcont_min`, `psubcont`, `nsubcont`, `DCNT`, `DCNT2`, `Via` | 0 |
| `PAD60`, `PAD100`, `PAD150` | 1 each: `VIA1.G ML1-ML2 Via not 5x5 um` |

The pad cells stitch ML1 to ML2 with a via grid that is clipped at the pad
border, so the via polygons in the `via1 & ml1 & ml2` region are not the 5 × 5 µm
squares the rule asks for. This is a property of the vendor cells: the golden
Mineda2022_6 deck contains the same rule (`M1Via 5um Fixed Size`) and flags
them too. The pad used by the frame library (`pad_60_m1_tin` in
`libs.ref/ICPS2023_5_fd_base`) does **not** trip it and is DRC-clean.

With `-rd strict=true`, `pcont_min` additionally reports two `TIN.m4` and two
`CNT.m7` markers: the minimum gate contact provides 2.0 µm of TiN/ML1
enclosure where the PDK documents ask for 2.5 µm. See
`libs.tech/klayout/drc/README.md` for the strict/default split.

## Foundry view

The shipped GDS is the foundry-mask view of the salt-package library. It was
produced with
`python3 libs.tech/klayout/drc/make_mask_view.py --flat-library` at the
experimental profile (the cells contain unqualified VIA1/ML2 geometry), which
converts every top cell with `icps2023_5_fab_prep.drc` and merges the results.

Two consequences are explicit:

* **The hierarchy is flattened.** `fab_prep` writes flattened output, and these
  primitives are meant to be instantiated cell by cell (`psubcont`/`nsubcont`
  share the `DCNT` child). The ten primitive cell names survive as flattened
  top cells; the internal `DCNT` child does not. The content also engages real
  fab-prep derivations (`DIFF` in the children interacts with `PAREA`/`NAREA` in
  the parents), so the frame-style hierarchical renaming does not apply here.
* **The `DM_*` dummy-fill marks are dropped.** They have no mask mapping: the
  original conversion deck (`ICPS2023_5_to_SOI.lydrc`) does not map them either,
  and the original DRC's only dummy rule (`VIA1` minus `DM_via1`) is commented
  out upstream. Dummy fill, if required, is a separate DFM step that draws real
  geometry.

## Provenance

The drawing original is `Basic/libraries/ICPS2023_5_Basic.gds` from the
original KLayout salt package (upstream Mineda repository); the shipped GDS is
its foundry-mask view. See `SOURCES` in the repository root.
