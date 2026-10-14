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

Compared with upstream [`drc.lydrc` (design/product auto-detect)](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/drc.lydrc) and the [design-to-SOI conversion deck](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/ICPS2023_5_to_SOI.lydrc), this headless deck uses explicit `layer_mode=design|fab`. It preserves the core design/fab predicates, adds PDF-backed design checks that the golden deck omitted, and fixes the golden deck's VIA1 spacing output typo (which reported the width rule instead).

Added relative to the golden design-mode rules:

* `TIN.W1` TiN gate width ≥ 10 µm
* `TIN.d1` gate TiN to contact distance ≥ 8 µm
* `TIN.m1` TiN overlap of the diffusion ≥ 2 µm (the golden deck checked this only in product mode)
* `TIN.S4` TiN space ≥ 6 µm (documentation; the golden deck used 4 µm)
* `VIA2.*`, `ML3.*` for the unqualified upper metal option, marked `[TBD]`

`DIFF.m3` is retained from the original deck: `PAREA` and `NAREA` must each enclose SOI by 2 µm. The rule's correct direction is dopant-area enclosure of the SOI island.

`TIN.d1` excludes the 4 µm fringe around an explicit `CAP` window because the
native N/P diffusion-capacitor PCells extend their TiN plate by 4 µm there.
The exclusion is local to the capacitor window; it does not suppress separate
gate TiN elsewhere.

The gate-contact split (`CNT ∩ TIN - DIFF` versus `CNT ∩ DIFF`) is what makes
the two Al enclosure rules (m6 = 4 µm, m7 = 2.5 µm) expressible at all.

### `-rd strict=true`

The strict-mode limits are the published design limits that the golden deck
relaxed. Keep `strict=false` as the default: the contest GDS has 160 violations
(32 `CNT.m5`, 128 `CNT.m6`) and the NAND GDS has 166 (32 `CNT.m5`, 134
`CNT.m6`). Both pass with default golden limits. The shipped `pcont_min` also
provides only 2.0 µm enclosure for two strict 2.5 µm gate-contact rules.

| Rule | Default | `strict=true` | Evidence |
|------|---------|---------------|----------|
| `CNT.G` contact size | ≥ 4 µm (golden) | ≥ 5 µm (document S1) | shipped/fabricated geometry uses the golden 4 µm limit |
| `CNT.m5` SOI enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m5) | contest 32; NAND 32 |
| `CNT.m6` ML1 enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m6) | contest 128; NAND 134 |
| `TIN.m4` TiN enclosure of gate contact | ≥ 2 µm (golden `TIN.C`) | ≥ 2.5 µm (document m4) | `pcont_min` provides 2.0 µm |
| `CNT.m7` ML1 enclosure of gate contact | ≥ 2 µm (golden `ML1.C`) | ≥ 2.5 µm (document m7) | `pcont_min` provides 2.0 µm |

Strict limits are suitable as an opt-in migration check, not as the default or
sign-off criterion for the shipped reference layouts.

## `icps2023_5_fab_prep.drc`

Design numbering -> foundry mask numbering, applying the mask biases of the
legacy conversion script (2 µm doping-mask bias, gate-window bias, diode guard
ring).

```bash
klayout -b -r icps2023_5_fab_prep.drc -rd input=design.gds -rd output=fab.gds
```

The output is checked with `-rd layer_mode=fab` on the DRC and LVS decks; the
fabricated NAND sample passes both after conversion.

Compared with the upstream [`ICPS2023_5_to_SOI.lydrc`](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/ICPS2023_5_to_SOI.lydrc), this deck accepts canonical design layers only; the legacy script also preserved already-mask-numbered geometry inside `EXCLUDE`. The TiN output retains the legacy 2 µm gate-window union and `TIN_BLK` geometry; a 4 µm guard around diffusion-capacitor `CAP` windows excludes their electrode fringe from gate-window bias. Mask `N_CH` (13/0) now maps directly from canonical `NCH` (21/0), as specified by the PDK layer table, instead of synthesizing legacy `pwell` from NAREA/TiN geometry. `MASK`/`EXCLUDE` input geometry is stripped rather than mixed into the output.

## Removed legacy decks

* `drc.lydrc` (golden) — superseded by `icps2023_5.drc`, which contains the same
  rules plus the document rules and works headless.
* `ICPS2023_5_to_SOI.lydrc` — superseded by `icps2023_5_fab_prep.drc`.
* `area_cut.lydrc` — stale Mineda2022_6 copy; its `input(20,0)` "pad" layer
  contradicts the PDK's `PAD` 50/0 and duplicates the `MASK` semantics of
  20/0, so it could not be reconciled with the layer table.
