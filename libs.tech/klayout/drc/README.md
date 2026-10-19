# DRC decks

## `icps2023_5.drc`

Single rule deck for the process. It is a faithful port of the golden
Mineda2022_6 v0.32 deck (`drc.lydrc` of the KLayout salt package) with the
design/fab double branch replaced by an explicit layer mode, plus the
PDK-document rules the golden deck was missing.

```bash
klayout -b -r icps2023_5.drc -rd input=layout.gds \
        [-rd top=CELL] [-rd layer_mode=design|fab] \
        [-rd report=out.lyrdb] [-rd strict=true] [-rd process_profile=NAME]
```

Rule IDs use the PDK symbol names (W1, W2, S1, m1..m7, d1, W3, S2) and keep the
golden deck's numbers in the description. `MASK` (20/0) and `EXCLUDE` (63/63)
regions are ignored.

The shared profile file is `../process_profiles.json`. The default
`icps2023_5_1m` rejects unmasked VIA1/ML2/VIA2/ML3 geometry and omits
provisional upper-metal checks. The experimental profile permits VIA1/ML2 and
runs their provisional checks; unmasked VIA2/ML3 remains rejected because no
LVS connectivity is defined and fab prep does not emit those layers.

Compared with upstream [`drc.lydrc` (design/product auto-detect)](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/drc.lydrc) and the [design-to-SOI conversion deck](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/ICPS2023_5_to_SOI.lydrc), this headless deck uses explicit `layer_mode=design|fab`. It preserves the core design/fab predicates, adds PDF-backed design checks that the golden deck omitted, and fixes the golden deck's VIA1 spacing output typo (which reported the width rule instead).

Added relative to the golden design-mode rules:

* `TIN.W1` TiN gate width ≥ 10 µm
* `TIN.d1` gate TiN to contact distance ≥ 8 µm
* `TIN.m1` TiN overlap of the diffusion ≥ 2 µm (the golden deck checked this only in product mode)
* `TIN.S4` TiN space ≥ 6 µm (documentation; the golden deck used 4 µm)
* `VIA1.*`, `ML2.*` in the experimental profile only; these rules remain
  provisional and do not qualify the layers.

`DIFF.m3` is retained from the original deck: `PAREA` and `NAREA` must each enclose SOI by 2 µm. The rule's correct direction is dopant-area enclosure of the SOI island.

`TIN.d1` excludes the 4 µm fringe around an explicit `CAP` window because the
native N/P diffusion-capacitor PCells extend their TiN plate by 4 µm there.
The exclusion is local to the capacitor window; it does not suppress separate
gate TiN elsewhere.

The gate-contact split (`CNT ∩ TIN - DIFF` versus `CNT ∩ DIFF`) is what makes
the two Al enclosure rules (m6 = 4 µm, m7 = 2.5 µm) expressible at all.

### `-rd strict=true`

The strict-mode limits are the published design limits that the golden deck
relaxed. Earlier unprofiled checks reported 160 contest-layout markers (32
`CNT.m5`, 128 `CNT.m6`) and 166 NAND markers (32 `CNT.m5`, 134 `CNT.m6`).
Those counts are historical: the current 1M profile rejects unmasked upper
metal before rule evaluation. Reproduce contact-rule results only with the
explicit experimental profile, and only when no unmasked VIA2/ML3 is present;
those results are not foundry qualification. The shipped `pcont_min` also
provides only 2.0 µm enclosure for two strict 2.5 µm gate-contact rules.

The shipped NAND GDS is foundry-mask numbered in place and is checked directly
in fab mode with `strict=false`; no conversion step remains in the flow. The
report contains only the experimental `TIN.SOIEDGE` step-edge screen (69
crossings): the legacy layout predates that design-practice convention, and the
screen is not a process limit. No document-limit rule fires. This is a legacy
compatibility smoke, not foundry qualification.
`drc/test_foundry_migration.py` pins the result: the shipped mask views are
clean except for that experimental screen.

| Rule | Default | `strict=true` | Evidence |
|------|---------|---------------|----------|
| `CNT.G` contact size | ≥ 4 µm (golden) | ≥ 5 µm (document S1) | shipped/fabricated geometry uses the golden 4 µm limit |
| `CNT.m5` SOI enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m5) | contest 32; NAND 32 |
| `CNT.m6` ML1 enclosure of S/D contact | ≥ 2 µm (golden) | ≥ 4 µm (document m6) | contest 128; NAND 134 |
| `TIN.m4` TiN enclosure of gate contact | ≥ 2 µm (golden `TIN.C`) | ≥ 2.5 µm (document m4) | `pcont_min` provides 2.0 µm |
| `CNT.m7` ML1 enclosure of gate contact | ≥ 2 µm (golden `ML1.C`) | ≥ 2.5 µm (document m7) | `pcont_min` provides 2.0 µm |

Strict limits are suitable as an opt-in migration check, not as the default or
sign-off criterion for the shipped reference layouts.

## TiN/SOI step-edge protection (`TIN.SOIEDGE`, `TIN.SOIBYPASS`, `CNT.SOIGATE`)

These are **experimental topography-dependent** rules, not document limits, and
not foundry qualification. They exist because TiN thins where it steps over the
SOI island edge and can fracture there; the published recipe is to carry ML1 over
the gate at the step. A DRC-only view of this is possible, but nothing here
proves the fracture physics — it is a design-practice obligation.

* `TIN.SOIEDGE` — TiN that **straddles** the island boundary must be covered by
  ML1 that reaches `SOI_EDGE_MARGIN_UM` beyond it on every side. The target is
  the crossing itself (a 2 µm band across the boundary) restricted to MOS gates
  (`TiN` interacting with N/P dope). Deliberately *not* targets: the off-island
  edge landing pad, off-island TiN jumper bars between fingers, capacitor plates,
  diffusion resistors and other non-gate TiN — none of them crosses the boundary.
  The margin defaults to 2.0 µm; `-rd soi_edge_margin=<um>` may raise it, and
  values below 2.0 µm are rejected. See the caveat below.
* `TIN.SOIBYPASS` (`-rd edge_redundancy=true`) — each MOS gate must cross the
  boundary at two separate places, every off-island branch must carry a CNT tied
  to ML1, and one connected ML1 component must contact at least two
  gate-connected CNTs. This is the electrical *redundancy* half of the recipe: if
  one crossing fractures, the other still drives the gate.
* `CNT.SOIGATE` (`-rd edge_redundancy=true`) — a gate CNT may not sit on the
  active channel; the bypass contacts are off-island only.

`TIN.SOIEDGE` needs both a coverage term and a margin term. A bare
`ml1.enclosing(target, margin)` is not enough: `enclosing` only inspects ML1
polygons that meet the target, so a gate with no strap at all passes it silently.

### Margin status

The margin is experimental. The native PCell's gate bay (`MIN_GATE_BAY_UM = 8`
µm) leaves room for the ML1 strap, and the `nmos_min` fixture passes the screen
at the 2.0 µm default in both design and fab modes (also with
`strict=true`/`edge_redundancy=true`); the deliberately unprotected diagnostic
fixture reports `TIN.SOIEDGE`/`TIN.SOIBYPASS` as intended. Raising the value to
2.4 µm still reports `TIN.SOIEDGE` markers on the same fixture, so the cells are
**not** compliant with the 2–4 µm range the recipe suggests. The older note that
0.5 µm was the widest achievable margin predates the widened gate bay and is
obsolete; do not cite it as the current limit.

## Conversion semantics

Three derivations in `icps2023_5_fab_prep.drc` change manufacturing geometry;
`test_conversion_semantics.py` pins each of them.

* **Doping windows.** `P_DIFF`/`N_DIFF` are not the drawing `PAREA`/`NAREA`
  shapes. Each is `(area & DIFF).sized(2) - (TIN - CAP + dio2)`, so the TiN
  subtraction cuts the channel window out of the dope mask. The mask therefore
  carries device-structure semantics: the fab LVS derives the MOS length from
  the window, and `L=6` in the drawing becomes a 6 µm mask window.
* **Whole-polygon TiN bias.** `interacting` selects complete polygons, so
  `TIN.interacting(DIFF).sized(2) & bb` biases every connected TiN conductor
  that touches the island: the gate window, its off-island landing pads and any
  jumper bar wired to it. `bb` clips the growth to the `(DIFF + TiN)` extents,
  so only directions with island overhang grow. This is the inherited legacy
  convention, not a local Ti/TiN-edge rule.
* **N-ch mask source.** The legacy deck generated a PWell region from every
  `NAREA` polygon that interacts with TiN
  (`pwell = narea.interacting(tin).sized(2) + input(21,0)`). The current deck
  emits only the explicit drawing `21/0` by default; `-rd legacy_pwell=true`
  reproduces the legacy mask. For a native NMOS this is a ~13x area difference
  (120 µm² vs 1568 µm² for `nmos_min`), so it is a behaviour change that needs
  recipe confirmation, not a bug fix.

## Report-only N_CH mask audit (`audit_nch_mask.py`)

The N_CH audit inventories geometry and compares source/fab mask provenance;
it is **not** a DRC deck or manufacturing qualification. It accepts explicit
design or fab input and emits JSON to standard output (or `--output`):

```bash
python3 libs.tech/klayout/drc/audit_nch_mask.py \
  --input design.gds --mode design [--top CELL] [--output nch-report.json]

python3 libs.tech/klayout/drc/audit_nch_mask.py \
  --input fab.gds --mode fab [--top CELL] \
  [--source design.gds [--source-top CELL] \
   [--source-provenance explicit|legacy]]
```

For multi-top files, select the cell with `--top` (and `--source-top` for a
multi-top source). The report records each input path, selected top, SHA-256,
database unit, and layer mapping. Drawing `21/0` maps to fab `13/0`. A design
input reports the explicit NCH candidate and the historical legacy candidate
separately; the latter is exactly
`NAREA.interacting(TIN).sized(2.0 um) + NCH`, with fab-prep's `MASK`/`EXCLUDE`
subtraction applied first. Neither candidate is asserted to be the physically
correct recipe.

For fab input, `--source` enables geometry XORs against both candidates.
`--source-provenance explicit|legacy` declares which candidate is compared for
the provenance category; omit the declaration to record the source as
unconfirmed. XORs use direct coordinates after DBU normalization only; there
is no overlay correction. If no source is provided, `source` is reported as
unknown. A `PROVENANCE_MISMATCH` is a source/recipe discrepancy only; it is
distinct from process validity and never makes the report a manufacturing error.

Report categories and evidence grades:

* `NCH_MASK_INVENTORY` — merged mask components with bounding boxes and areas,
  plus area, empty-layer state, and raw degenerate NCH shapes retained by
  KLayout (if any).
* `NCH_SOI_RELATION` — on-SOI and off-SOI area/components, including parts
  disjoint from SOI. These are geometry observations; NCH is not assumed to be
  confined beneath a gate.
* `NCH_CHANNEL_COVERAGE` — reports the geometric candidate
  `(SOI ∩ TiN) − (effective P+ ∪ effective N+)`, not the biased overlap edges;
  it stays `UNCONFIRMED` because no accepted manufacturing coverage criterion
  exists and a mask gap is not a measured electrical channel.
* `NCH_EFFECTIVE_P_PLUS_OVERLAP` and `NCH_EFFECTIVE_N_PLUS_OVERLAP` — overlap
  areas/components against fab P+/N+ masks (or design-derived masks using the
  existing fab-prep formula). Overlap is observational, not a violation.
* `PROVENANCE_COMPARISON` — source/fab XOR evidence with
  `PROVENANCE_MATCH`, `PROVENANCE_MISMATCH`, or `UNCONFIRMED`.
* `PROCESS_LIMITS` — `UNCONFIRMED`; minimum size, spacing, exposure/polarity,
  overlay tolerance, allowed N_CH regions, and the intended recipe need
  foundry confirmation.

All reports remain `REPORT_ONLY`, `UNQUALIFIED`, and not suitable for sign-off.
Geometry-only evidence never promotes unknown size, spacing, exposure,
overlay, coverage, or allowed-region requirements to `PASS`. The audit cannot
substitute for a foundry-supplied N_CH rule set, and the unresolved
explicit-vs-legacy recipe question remains a prerequisite for any NMOS FD/PD
inference from this mask.

Focused verification (run in an environment with `klayout.db` and `klayout`):

```bash
python3 -m unittest discover -s libs.tech/klayout/drc -p 'test_nch_mask_audit.py'
```

The tests generate separate ordinary NMOS, body-tied NMOS4, and ordinary
no-body PMOS native PCells, compare both candidates to actual fab-prep
conversions, and exercise hierarchy, `MASK`/`EXCLUDE`, source XORs, and unknown
provenance. They do not imply PMOS4 LVS support.

## `icps2023_5_fab_prep.drc`

Design numbering -> foundry mask numbering, applying the mask biases of the
legacy conversion script (2 µm doping-mask bias, gate-window bias, diode guard
ring).

```bash
klayout -b -r icps2023_5_fab_prep.drc -rd input=design.gds -rd output=fab.gds \
        [-rd process_profile=NAME]
```

The shipped contest NAND layout contains unqualified VIA1/ML2 geometry, so
the default 1M profile rejects it. Select
`icps2023_5_multimetal_experimental` explicitly when reproducing legacy
fab-prep/DRC/LVS checks; it permits only VIA1/ML2 and does not establish foundry
qualification. Unmasked VIA2/ML3 remains rejected.

Compared with the upstream [`ICPS2023_5_to_SOI.lydrc`](https://github.com/mineda-support/ICPS2023_5/blob/main/Technology/tech/drc/ICPS2023_5_to_SOI.lydrc), this deck accepts canonical design layers only; the legacy script also preserved already-mask-numbered geometry inside `EXCLUDE`. The TiN output retains the legacy 2 µm gate-window union and `TIN_BLK` geometry; a 4 µm guard around diffusion-capacitor `CAP` windows excludes their electrode fringe from gate-window bias. Mask `N_CH` (13/0) now maps directly from canonical `NCH` (21/0), as specified by the PDK layer table, instead of synthesizing legacy `pwell` from NAREA/TiN geometry. `MASK`/`EXCLUDE` input geometry is stripped rather than mixed into the output.

## Removed legacy decks

* `drc.lydrc` (golden) — superseded by `icps2023_5.drc`, which contains the same
  rules plus the document rules and works headless.
* `ICPS2023_5_to_SOI.lydrc` — superseded by `icps2023_5_fab_prep.drc`.
* `area_cut.lydrc` — stale Mineda2022_6 copy; its `input(20,0)` "pad" layer
  contradicts the PDK's `PAD` 50/0 and duplicates the `MASK` semantics of
  20/0, so it could not be reconciled with the layer table.
