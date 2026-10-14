# ICPS2023_5 PEX (KLayout)

`icps2023_5_pex.py` extracts layout parasitics for the minimal Fab SOI-CMOS
process. The legacy PDK shipped no PEX deck at all; this one replaces the
`Backannotate device parasitics` macro workflow with a headless, reproducible
extraction.

## Usage

```bash
cd ~/Documents/librelane && nix-shell --run \
  'python3 libs.tech/klayout/pex/icps2023_5_pex.py \
      --input <layout.gds> [--top CELL] [--output <parasitics.spice>] \
      [--layer-mode design|fab] [--no-capacitance]'
```

Defaults: `--output <input>_pex.spice`, `--layer-mode design`. Files with more
than one top cell require `--top CELL` (library GDS files do).

## Model

Resistance — KLayout's native R extractor (`klayout.pex`, `RNetExtractor` with
the square-counting algorithm), driven by a tech stack built from the PDK
documents:

| Conductor | Sheet resistance | Source |
|-----------|------------------|--------|
| ML1 / ML2 / ML3 (Al) | 0.2 Ω/sq | PDK sheet-resistance table |
| TiN | 20 Ω/sq | PDK sheet-resistance table |
| N+ diffusion (`NAREA`) | 180 Ω/sq | PDK sheet-resistance table |
| P+ diffusion (`PAREA`) | 1200 Ω/sq | PDK sheet-resistance table |
| Undoped SOI film (`DIFF` outside the dope) | 250 MΩ/sq | legacy LVS deck `R_SOI` |

| Via | Resistance |
|-----|-----------|
| CNT on diffusion (Al–N+) | 150 Ω |
| CNT on TiN (Al–TiN) | 0.9 Ω |
| VIA1, VIA2 | 0.9 Ω |

Capacitance — lumped per conductor component, attached to the same nodes as the
resistance network:

| Pair | Areal capacitance | Source |
|------|-------------------|--------|
| TiN ↔ DIFF (15 nm gate oxide) | 2.3 fF/µm² | PDK `Cox`, LVS `PDIFFCAP`/`NDIFFCAP` |
| TiN ↔ ML1 (300 nm TEOS) | 0.115 fF/µm² | PDK `TiN_cap`, LVS `TINCAP` |
| ML1 ↔ ML2 (300 nm TEOS) | 0.115 fF/µm² | LVS `MIMCAP` |
| DIFF ↔ substrate (400 nm BOX) | 0.086 fF/µm² | PDK MOSFET capacitance table |

Terminals — every contact (`CNT`) and via (`VIA1`/`VIA2`) becomes a port on the
lowest conductor it touches, so the network is broken where a device or the
outside world connects. `TEXT` (13/0) labels name nets; labelled nets become
the `.SUBCKT` ports.

## Output

A SPICE subcircuit named `<top cell>_pex` containing `R…` elements (from the
extractor) and `C…` elements (`CS_*` to the `SUB` node, `CC_*` between
conductors of different nets).

## Limits (read before trusting a number)

* Capacitance is **lumped per conductor component** — no field solver, no
  fringing or sidewall capacitance, no distributed RC line behaviour. A
  component that carries several extracted R nodes attaches its capacitance to
  one of them.
* The PEX net names come from `TEXT` labels only. Without labels the netlist
  carries generated node names and no `.SUBCKT` ports; run LVS
  (`../lvs/icps2023_5.lvs`) first if you need net names, or label your nets.
* Coupling capacitance is computed per overlapping component pair; overlap
  between three (or more) layers is handled pairwise, so a TiN/ML1/DIFF stack
  contributes both the oxide term and the TEOS term.
* Substrate coupling is modelled as a single global `SUB` node. The process has
  no body contact; the SOI film potential is not solved self-consistently.
* The resistance network uses the square-counting algorithm, which assumes
  "thin" conductor parts. For large sheet-like regions the tesselation
  algorithm would be the better choice — swap
  `pex.Algorithm.SquareCounting` for `pex.Algorithm.Tesselation` in the script
  if your layout has them.
