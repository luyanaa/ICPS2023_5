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
| `nsubcont` | 12 × 12 | n-substrate (body) contact | `Pch` PCell (`with_nsubcont`) |
| `DCNT` | 13 × 13 | double contact | `psubcont`, `nsubcont` |
| `DCNT2` | 9 × 9 | double contact, small | device contacts |
| `Via` | 13 × 13 | ML1–ML2 via | resistor and capacitor PCells |
| `PAD60` | 60 × 60 | 60 µm pad | pad ring of the contest templates |
| `PAD100` | 100 × 100 | 100 µm pad | alternative pad size |
| `PAD150` | 150 × 150 | 150 µm pad | alternative pad size |

Every cell uses the design layer numbers of `libs.tech/klayout/doc/layers.md`
(DIFF 3/0, TIN 5/0, CNT 7/0, ML1 8/0, VIA1 9/0, ML2 10/0, PAREA 18/0,
NAREA 19/0, PAD 50/0 and the DM_* dummy marks 101–104).

The 60 µm pad used by the frame library is `pad_60_m1_tin` in
`libs.ref/ICPS2023_5_fd_base`; `PAD60/100/150` here are the vendor's standalone
pad cells.

## Known DRC markers

Run of `libs.tech/klayout/drc/icps2023_5.drc` per cell (default limits):

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

## Provenance

`gds/ICPS2023_5_fd_pr.gds` is the `ICPS2023_5_Basic` static library of the
original KLayout salt package, renamed and moved unchanged. See `SOURCES` in
the repository root.
