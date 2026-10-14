# DRC decks

## `icps2023_5.drc`

Single rule deck for the process. It is a faithful port of the golden
Mineda2022_6 v0.32 deck (`drc.lydrc` of the KLayout salt package) with the
design/fab double branch replaced by an explicit layer mode, plus the
PDK-document rules the golden deck was missing.

```bash
klayout -b -r icps2023_5.drc -rd input=layout.gds \
        [-rd top=CELL] [-rd layer_mode=design|fab] \
        [-rd report=out.lyrdb] [-rd strict=true]
```

Rule IDs use the PDK symbol names (W1, W2, S1, m1..m7, d1, W3, S2) and keep the
golden deck's numbers in the description. `MASK` (20/0) and `EXCLUDE` (63/63)
regions are ignored.

Added relative to the golden deck (all satisfied by the fabricated reference
layouts):

* `TIN.W1` TiN gate width ≥ 10 µm
* `TIN.d1` gate TiN to contact distance ≥ 8 µm
* `DIFF.m3` SOI enclosure of `PAREA`/`NAREA` ≥ 2 µm
* `TIN.m1` TiN overlap of the diffusion ≥ 2 µm (was product-mode only)
* `TIN.S4` TiN space ≥ 6 µm (documentation; the golden deck used 4 µm)
* `VIA2.*`, `ML3.*` for the unqualified upper metal option, marked `[TBD]`

The gate-contact split (`CNT ∩ TIN - DIFF` versus `CNT ∩ DIFF`) is what makes
the two Al enclosure rules (m6 = 4 µm, m7 = 2.5 µm) expressible at all.

### `-rd strict=true`

Enables the document limits that shipped geometry does **not** meet. The
criterion for the default set is: every rule that the fabricated reference
layouts and every shipped library cell satisfy.

| Rule | Default | `strict=true` | Why the document value is not the default |
|------|---------|---------------|-------------------------------------------|
| `CNT.G` contact size | ≥ 4 µm (golden) | ≥ 5 µm (document S1) | the golden deck and all fabricated data use 4 µm as the limit |
| `CNT.m5` SOI enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m5) | 32 violations on the fabricated contest template |
| `CNT.m6` ML1 enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m6) | 128 violations on the fabricated contest template |
| `TIN.m4` TiN enclosure of gate contact | ≥ 2 µm (golden `TIN.C`) | ≥ 2.5 µm (document m4) | the shipped `pcont_min` primitive provides exactly 2.0 µm |
| `CNT.m7` ML1 enclosure of gate contact | ≥ 2 µm (golden `ML1.C`) | ≥ 2.5 µm (document m7) | same `pcont_min` cell |

With `strict=true` the contest template reports 160 markers (32 `CNT.m5` + 128
`CNT.m6`); with the default limits it is clean. Treat the document values as a
to-do for the next mask revision, not as sign-off.

## `icps2023_5_fab_prep.drc`

Design numbering -> foundry mask numbering, applying the mask biases of the
legacy conversion script (2 µm doping-mask bias, gate-window bias, diode guard
ring).

```bash
klayout -b -r icps2023_5_fab_prep.drc -rd input=design.gds -rd output=fab.gds
```

The output is checked with `-rd layer_mode=fab` on the DRC and LVS decks; the
fabricated NAND sample passes both after conversion.

## Removed legacy decks

* `drc.lydrc` (golden) — superseded by `icps2023_5.drc`, which contains the same
  rules plus the document rules and works headless.
* `ICPS2023_5_to_SOI.lydrc` — superseded by `icps2023_5_fab_prep.drc`.
* `area_cut.lydrc` — stale Mineda2022_6 copy; its `input(20,0)` "pad" layer
  contradicts the PDK's `PAD` 50/0 and duplicates the `MASK` semantics of
  20/0, so it could not be reconciled with the layer table.
