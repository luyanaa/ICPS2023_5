"""Canonical 1M standard-cell logic specifications.

The module deliberately contains only logical/device intent.  Physical dimensions,
ports and areas are supplied by the geometry and characterization stages.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable


MIN_L_UM = 6.0
MIN_W_UM = 13.0
NOMINAL_W_UM = 24.0


def _m(name: str, polarity: str, d: str, g: str, s: str,
       w: float = NOMINAL_W_UM, l: float = MIN_L_UM) -> dict[str, Any]:
    return {"name": name, "polarity": polarity, "d": d, "g": g, "s": s,
            "w_um": float(w), "l_um": float(l)}


def _spec(name: str, inputs: list[str], function: dict[str, str], mos: list[dict[str, Any]],
          *, outputs: list[str] | None = None, sequential: dict[str, Any] | None = None,
          provenance: str = "canonical 1M CMOS topology; dimensions are nominal model inputs",
          variants: dict[str, Any] | None = None) -> dict[str, Any]:
    outputs = outputs or ["Y"]
    return {
        "name": name,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "pins": list(inputs) + list(outputs) + ["VDD", "VSS"],
        "function": dict(function),
        "mos": mos,
        "sequential": sequential,
        "provenance": provenance,
        "variants": variants or {},
    }


def _inv(name: str, a: str, y: str) -> list[dict[str, Any]]:
    return [_m(f"{name}_P", "p", "VDD", a, y), _m(f"{name}_N", "n", y, a, "VSS")]


def _nand(name: str, inputs: list[str], y: str) -> list[dict[str, Any]]:
    # PUN is parallel and PDN is series.  The explicit order is useful to a
    # geometry placer even though the Boolean function is commutative.
    p = [_m(f"{name}_P{i}", "p", "VDD", a, y) for i, a in enumerate(inputs)]
    n: list[dict[str, Any]] = []
    lower = "VSS"
    for i, a in enumerate(reversed(inputs)):
        upper = y if i == len(inputs) - 1 else f"{name}_NINT{i}"
        n.append(_m(f"{name}_N{i}", "n", upper, a, lower))
        lower = upper
    return p + n


def _nor(name: str, inputs: list[str], y: str) -> list[dict[str, Any]]:
    p: list[dict[str, Any]] = []
    lower = y
    for i, a in enumerate(inputs):
        upper = "VDD" if i == len(inputs) - 1 else f"{name}_PINT{i}"
        p.append(_m(f"{name}_P{i}", "p", upper, a, lower))
        lower = upper
    return p + [_m(f"{name}_N{i}", "n", y, a, "VSS") for i, a in enumerate(inputs)]


def _aoi21(name: str, a: str, b: str, c: str, y: str) -> list[dict[str, Any]]:
    # PDN: (A series B) in parallel with C; PUN is its CMOS dual.
    return [
        _m(f"{name}_PC", "p", "VDD", c, f"{name}_PINT"),
        _m(f"{name}_PA", "p", f"{name}_PINT", a, y),
        _m(f"{name}_PB", "p", f"{name}_PINT", b, y),
        _m(f"{name}_NA", "n", y, a, f"{name}_NINT"),
        _m(f"{name}_NB", "n", f"{name}_NINT", b, "VSS"),
        _m(f"{name}_NC", "n", y, c, "VSS"),
    ]


def _oai21(name: str, a: str, b: str, c: str, y: str) -> list[dict[str, Any]]:
    # PDN: C series with (A parallel B); PUN is its CMOS dual.
    return [
        _m(f"{name}_PC", "p", "VDD", c, y),
        _m(f"{name}_PA", "p", y, a, f"{name}_PINT"),
        _m(f"{name}_PB", "p", f"{name}_PINT", b, "VDD"),
        _m(f"{name}_NC", "n", y, c, f"{name}_NINT"),
        _m(f"{name}_NA", "n", f"{name}_NINT", a, "VSS"),
        _m(f"{name}_NB", "n", f"{name}_NINT", b, "VSS"),
    ]


def _aoi22(name: str, a: str, b: str, c: str, d: str, y: str) -> list[dict[str, Any]]:
    return [
        _m(f"{name}_PA", "p", "VDD", a, f"{name}_PINT"),
        _m(f"{name}_PB", "p", "VDD", b, f"{name}_PINT"),
        _m(f"{name}_PC", "p", f"{name}_PINT", c, y),
        _m(f"{name}_PD", "p", f"{name}_PINT", d, y),
        _m(f"{name}_NA", "n", y, a, f"{name}_NAB"),
        _m(f"{name}_NB", "n", f"{name}_NAB", b, "VSS"),
        _m(f"{name}_NC", "n", y, c, f"{name}_NCD"),
        _m(f"{name}_ND", "n", f"{name}_NCD", d, "VSS"),
    ]


def _oai22(name: str, a: str, b: str, c: str, d: str, y: str) -> list[dict[str, Any]]:
    return [
        _m(f"{name}_PA", "p", "VDD", a, f"{name}_PAB"),
        _m(f"{name}_PB", "p", f"{name}_PAB", b, y),
        _m(f"{name}_PC", "p", "VDD", c, f"{name}_PCD"),
        _m(f"{name}_PD", "p", f"{name}_PCD", d, y),
        _m(f"{name}_NA", "n", y, a, f"{name}_NINT"),
        _m(f"{name}_NB", "n", y, b, f"{name}_NINT"),
        _m(f"{name}_NC", "n", f"{name}_NINT", c, "VSS"),
        _m(f"{name}_ND", "n", f"{name}_NINT", d, "VSS"),
    ]


def _xor_core(name: str, a: str, b: str, y: str, xnor: bool = False) -> list[dict[str, Any]]:
    na, nb = f"{name}_NA", f"{name}_NB"
    mos = _inv(f"{name}_IA", a, na) + _inv(f"{name}_IB", b, nb)
    if xnor:
        # PUN = AB + !A!B; PDN = (A+B)(!A+!B).
        mos += [
            _m(f"{name}_PA", "p", "VDD", a, f"{name}_P0"),
            _m(f"{name}_PB", "p", f"{name}_P0", b, y),
            _m(f"{name}_PNA", "p", "VDD", na, f"{name}_P1"),
            _m(f"{name}_PNB", "p", f"{name}_P1", nb, y),
            _m(f"{name}_NA", "n", y, a, f"{name}_N0"),
            _m(f"{name}_NB", "n", f"{name}_N0", nb, "VSS"),
            _m(f"{name}_NNA", "n", y, na, f"{name}_N1"),
            _m(f"{name}_NNB", "n", f"{name}_N1", b, "VSS"),
        ]
    else:
        # PUN = A!B + !AB; PDN = AB + !A!B.
        mos += [
            _m(f"{name}_PA", "p", "VDD", a, f"{name}_P0"),
            _m(f"{name}_PNB", "p", f"{name}_P0", nb, y),
            _m(f"{name}_PNA", "p", "VDD", na, f"{name}_P1"),
            _m(f"{name}_PB", "p", f"{name}_P1", b, y),
            _m(f"{name}_NA", "n", y, a, f"{name}_N0"),
            _m(f"{name}_NB", "n", f"{name}_N0", b, "VSS"),
            _m(f"{name}_NNA", "n", y, na, f"{name}_N1"),
            _m(f"{name}_NNB", "n", f"{name}_N1", nb, "VSS"),
        ]
    return mos


def _mux_tg(name: str, a: str, b: str, s: str, y: str) -> list[dict[str, Any]]:
    ns = f"{name}_NS"
    return _inv(f"{name}_SELINV", s, ns) + [
        _m(f"{name}_A_N", "n", a, s, y), _m(f"{name}_A_P", "p", a, ns, y),
        _m(f"{name}_B_N", "n", b, ns, y), _m(f"{name}_B_P", "p", b, s, y),
    ]


def _mux_static(name: str, a: str, b: str, s: str, y: str) -> list[dict[str, Any]]:
    # Three NAND2 stages plus the select inverter; fully static and restored.
    ns = f"{name}_NS"
    return (_inv(f"{name}_SELINV", s, ns) +
            _nand(f"{name}_N0", [a, s], f"{name}_T0") +
            _nand(f"{name}_N1", [b, ns], f"{name}_T1") +
            _nand(f"{name}_N2", [f"{name}_T0", f"{name}_T1"], y))


def _latch_tg(name: str, d: str, clk: str, q: str) -> list[dict[str, Any]]:
    nc = f"{name}_NCLK"
    n1, n2 = f"{name}_N1", f"{name}_N2"
    return (_inv(f"{name}_CLKINV", clk, nc) +
            [_m(f"{name}_IN_N", "n", d, clk, n1), _m(f"{name}_IN_P", "p", d, nc, n1)] +
            _inv(f"{name}_INV0", n1, n2) + _inv(f"{name}_INV1", n2, q) +
            [_m(f"{name}_FB_N", "n", q, nc, n1), _m(f"{name}_FB_P", "p", q, clk, n1)])


def _latch_gate(name: str, d: str, clk: str, q: str) -> list[dict[str, Any]]:
    nd, sbar, rbar, nq = f"{name}_ND", f"{name}_SBAR", f"{name}_RBAR", f"{name}_NQ"
    return (_inv(f"{name}_DINV", d, nd) +
            _nand(f"{name}_SET", [d, clk], sbar) + _nand(f"{name}_RESET", [nd, clk], rbar) +
            _nand(f"{name}_LQ", [sbar, nq], q) + _nand(f"{name}_LNQ", [rbar, q], nq))


def _dff_tg(name: str, d: str, clk: str, q: str) -> list[dict[str, Any]]:
    nc = f"{name}_NCLK"
    m1, mq, s1 = f"{name}_M1", f"{name}_MQ", f"{name}_S1"
    q2 = f"{name}_Q2"
    mos = _inv(f"{name}_CLKINV", clk, nc)
    # Master is transparent low; slave is transparent high.
    mos += [_m(f"{name}_MI_N", "n", d, nc, m1), _m(f"{name}_MI_P", "p", d, clk, m1)]
    mos += _inv(f"{name}_M0", m1, mq) + _inv(f"{name}_M1I", mq, f"{name}_M2")
    mos += [_m(f"{name}_MF_N", "n", f"{name}_M2", clk, m1), _m(f"{name}_MF_P", "p", f"{name}_M2", nc, m1)]
    mos += [_m(f"{name}_SI_N", "n", f"{name}_M2", clk, s1), _m(f"{name}_SI_P", "p", f"{name}_M2", nc, s1)]
    mos += _inv(f"{name}_S0", s1, q2) + _inv(f"{name}_S1I", q2, q)
    mos += [_m(f"{name}_SF_N", "n", q, nc, s1), _m(f"{name}_SF_P", "p", q, clk, s1)]
    return mos


def _seq_meta(architecture: str, *, d: str = "D", clk: str = "CLK", q: str = "Q",
              transparent: bool = False,
              clock_inverter_net: str | None = None) -> dict[str, Any]:
    return {
        "kind": "latch" if transparent else "flip_flop",
        "architecture": architecture,
        "clock": clk,
        "clock_polarity": "high" if transparent else "rising",
        "data": d,
        "q": q,
        "enable": None,
        "async_controls": [],
        "clock_inversions": ([{"input": clk, "output": clock_inverter_net, "mos_count": 2}]
                             if clock_inverter_net else []),
        "feedback_paths": [q],
        "expected_state": {"q_key": q, "transparent_when": f"{clk}=1"} if transparent else
                          {"q_key": q, "capture": "rising_edge", "hold": "otherwise"},
    }


def cell_specs() -> dict[str, dict[str, Any]]:
    """Return independent, JSON-serializable canonical cell specifications."""
    out: dict[str, dict[str, Any]] = {}
    def add(s: dict[str, Any]) -> None: out[s["name"]] = s
    add(_spec("INV_X1", ["A"], {"Y": "!A"}, _inv("INV", "A", "Y")))
    add(_spec("BUF_X1", ["A"], {"Y": "A"}, _inv("I0", "A", "N") + _inv("I1", "N", "Y")))
    add(_spec("NAND2_X1", ["A", "B"], {"Y": "!(A & B)"}, _nand("NAND", ["A", "B"], "Y")))
    add(_spec("NAND2_BA_X1", ["B", "A"], {"Y": "!(B & A)"}, _nand("NAND", ["B", "A"], "Y"), variants={"equivalent_to": "NAND2_X1", "input_order": ["B", "A"]}))
    add(_spec("NAND3_X1", ["A", "B", "C"], {"Y": "!(A & B & C)"}, _nand("NAND", ["A", "B", "C"], "Y")))
    add(_spec("NOR2_X1", ["A", "B"], {"Y": "!(A | B)"}, _nor("NOR", ["A", "B"], "Y")))
    add(_spec("NOR2_BA_X1", ["B", "A"], {"Y": "!(B | A)"}, _nor("NOR", ["B", "A"], "Y"), variants={"equivalent_to": "NOR2_X1", "input_order": ["B", "A"]}))
    add(_spec("NOR3_X1", ["A", "B", "C"], {"Y": "!(A | B | C)"}, _nor("NOR", ["A", "B", "C"], "Y")))
    add(_spec("AOI21_X1", ["A", "B", "C"], {"Y": "!((A & B) | C)"}, _aoi21("AOI", "A", "B", "C", "Y")))
    add(_spec("OAI21_X1", ["A", "B", "C"], {"Y": "!((A | B) & C)"}, _oai21("OAI", "A", "B", "C", "Y")))
    add(_spec("AOI22_X1", ["A", "B", "C", "D"], {"Y": "!((A & B) | (C & D))"}, _aoi22("AOI", "A", "B", "C", "D", "Y")))
    add(_spec("OAI22_X1", ["A", "B", "C", "D"], {"Y": "!((A | B) & (C | D))"}, _oai22("OAI", "A", "B", "C", "D", "Y")))
    add(_spec("AND2_X1", ["A", "B"], {"Y": "A & B"}, _nand("N", ["A", "B"], "N") + _inv("I", "N", "Y")))
    add(_spec("OR2_X1", ["A", "B"], {"Y": "A | B"}, _nor("N", ["A", "B"], "N") + _inv("I", "N", "Y")))
    add(_spec("XOR2_X1", ["A", "B"], {"Y": "A ^ B"}, _xor_core("XOR", "A", "B", "Y")))
    add(_spec("XNOR2_X1", ["A", "B"], {"Y": "!(A ^ B)"}, _xor_core("XNOR", "A", "B", "Y", True)))
    add(_spec("MUX2_TG6T_X1", ["A", "B", "S"], {"Y": "(S & A) | (!S & B)"}, _mux_tg("MUX", "A", "B", "S", "Y"), variants={"architecture": "transmission_gate_6t", "restore_inverter": None}))
    add(_spec("MUX2_STATIC_X1", ["A", "B", "S"], {"Y": "(S & A) | (!S & B)"}, _mux_static("MUX", "A", "B", "S", "Y"), variants={"architecture": "static_cmos", "restore_inverter": "included_in_complementary_network"}))
    # MUX4 is retained as an area comparator; both levels are explicit cells.
    m4tg = _mux_tg("M0", "A", "B", "S0", "N0") + _mux_tg("M1", "C", "D", "S0", "N1") + _mux_tg("M2", "N0", "N1", "S1", "Y")
    m4st = _mux_static("M0", "A", "B", "S0", "N0") + _mux_static("M1", "C", "D", "S0", "N1") + _mux_static("M2", "N0", "N1", "S1", "Y")
    add(_spec("MUX4_TG_X1", ["A", "B", "C", "D", "S0", "S1"], {"Y": "(S1 & ((S0 & A) | (!S0 & B))) | (!S1 & ((S0 & C) | (!S0 & D)))"}, m4tg, variants={"architecture": "three_TG6T_levels"}))
    add(_spec("MUX4_STATIC_X1", ["A", "B", "C", "D", "S0", "S1"], {"Y": "(S1 & ((S0 & A) | (!S0 & B))) | (!S1 & ((S0 & C) | (!S0 & D)))"}, m4st, variants={"architecture": "three_static_mux_levels"}))
    add(_spec("HA_X1", ["A", "B"], {"SUM": "A ^ B", "COUT": "A & B"}, _xor_core("SUM", "A", "B", "SUM") + _nand("CARRY", ["A", "B"], "NC") + _inv("CARRYI", "NC", "COUT"), outputs=["SUM", "COUT"], variants={"comparator": "half_adder"}))
    add(_spec("FA_X1", ["A", "B", "CIN"], {"SUM": "A ^ B ^ CIN", "COUT": "(A & B) | (CIN & (A ^ B))"}, _xor_core("X0", "A", "B", "XAB") + _xor_core("X1", "XAB", "CIN", "SUM") + _nand("C0", ["A", "B"], "N0") + _nand("C1", ["CIN", "XAB"], "N1") + _nand("CO", ["N0", "N1"], "COUT"), outputs=["SUM", "COUT"], variants={"comparator": "full_adder"}))
    add(_spec("LATCH_TG_X1", ["D", "CLK"], {"Q": "D"}, _latch_tg("LATCH", "D", "CLK", "Q"), outputs=["Q"], sequential=_seq_meta("transmission_gate", transparent=True, clock_inverter_net="LATCH_NCLK")))
    add(_spec("LATCH_GATE_X1", ["D", "CLK"], {"Q": "D"}, _latch_gate("LATCH", "D", "CLK", "Q"), outputs=["Q"], sequential=_seq_meta("gate_latch", transparent=True)))
    add(_spec("DFF_TG_X1", ["D", "CLK"], {"Q": "D"}, _dff_tg("DFF", "D", "CLK", "Q"), outputs=["Q"], sequential=_seq_meta("transmission_gate", transparent=False, clock_inverter_net="DFF_NCLK")))
    gate_dff = (_inv("DFF_GATE_CLKINV", "CLK", "DFF_GATE_NCLK") +
                _latch_gate("MASTER", "D", "DFF_GATE_NCLK", "MQ") +
                _latch_gate("SLAVE", "MQ", "CLK", "Q"))
    add(_spec("DFF_GATE_X1", ["D", "CLK"], {"Q": "D"}, gate_dff, outputs=["Q"], sequential=_seq_meta("gate_latch", transparent=False, clock_inverter_net="DFF_GATE_NCLK")))
    return out


# --- Independent Boolean/state evaluator ---------------------------------
_TOKEN = re.compile(r"\s*(?:(0|1)|([A-Za-z_][A-Za-z0-9_]*)|(!|\^|&|\||\(|\)))")


def _eval_expr(expr: str, values: dict[str, Any]) -> int:
    toks: list[str] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"unsupported Boolean expression near {expr[pos:]}" )
        toks.append(next(g for g in m.groups() if g is not None))
        pos = m.end()
    i = 0
    def primary() -> int:
        nonlocal i
        if i >= len(toks): raise ValueError("incomplete Boolean expression")
        t = toks[i]
        if t == "(":
            i += 1; v = orexp()
            if i >= len(toks) or toks[i] != ")": raise ValueError("missing )")
            i += 1; return v
        i += 1
        if t in ("0", "1"): return int(t)
        if t == "!":
            return 1 - primary()
        return int(bool(values.get(t, 0)))
    def andexp() -> int:
        nonlocal i
        v = primary()
        while i < len(toks) and toks[i] == "&": i += 1; v &= primary()
        return v
    def xorexp() -> int:
        nonlocal i
        v = andexp()
        while i < len(toks) and toks[i] == "^": i += 1; v ^= andexp()
        return v
    def orexp() -> int:
        nonlocal i
        v = xorexp()
        while i < len(toks) and toks[i] == "|": i += 1; v |= xorexp()
        return v
    result = orexp()
    if i != len(toks): raise ValueError(f"unexpected token {toks[i]}")
    return int(bool(result))


def evaluate(spec: dict[str, Any], input_values: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, int]:
    """Evaluate a cell without invoking Python ``eval`` or a simulator.

    Sequential cells use ``state`` as the prior state.  The returned dictionary
    contains output values and ``__state`` is intentionally omitted; callers
    persist the supplied state and update its Q/previous-clock keys themselves.
    """
    vals = {k: int(bool(v)) for k, v in input_values.items()}
    seq = spec.get("sequential")
    if not seq:
        return {o: _eval_expr(expr, vals) for o, expr in spec["function"].items()}
    state = dict(state or {})
    q = seq["q"]
    old_q = int(bool(state.get(q, 0)))
    clk = seq["clock"]
    cv = int(bool(vals.get(clk, 0)))
    if seq["kind"] == "latch":
        new_q = vals.get(seq["data"], 0) if cv else old_q
    else:
        prev = state.get(f"__prev_{clk}")
        new_q = vals.get(seq["data"], 0) if prev is not None and not bool(prev) and cv else old_q
    result = {q: int(bool(new_q))}
    for o, expr in spec["function"].items():
        if o != q: result[o] = _eval_expr(expr, {**vals, q: new_q})
    return result


# --- SPICE reference ------------------------------------------------------
def write_reference(spec_or_design: dict[str, Any], path: str | Path,
                    top_name: str | None = None, *, library: bool = False) -> None:
    """Write ngspice-compatible MOS references or a subcircuit-only library.

    Every subcircuit declares ``SUB`` as a global substrate and every MOS uses
    the real ``nch``/``pch`` model cards.  Library mode omits the top-level
    test instance and ``.END`` so the file can be included by a caller.
    """
    if "mos" in spec_or_design:
        specs = {spec_or_design["name"]: spec_or_design}
        design = None
    else:
        raw = spec_or_design.get("specs", spec_or_design.get("cells", {}))
        specs = raw if isinstance(raw, dict) else {s["name"]: s for s in raw}
        design = spec_or_design
    top_name = top_name or (design.get("name", "TOP") if design else next(iter(specs)))
    # Dimensions below are already in metres; do not apply a second scale factor.
    lines = ["* ICPS2023_5 canonical 1M standard-cell reference", ".GLOBAL SUB"]
    for name, spec in specs.items():
        pins = spec["pins"]
        lines.append(f".SUBCKT {name} {' '.join(pins)}")
        for m in spec["mos"]:
            model = "nch" if m["polarity"] == "n" else "pch"
            instance = str(m["name"])
            if not instance.upper().startswith("M"):
                instance = "M_" + instance
            lines.append(f"{instance} {m['d']} {m['g']} {m['s']} SUB {model} W={m['w_um'] * 1e-6:.12g} L={m['l_um'] * 1e-6:.12g}")
        lines.append(f".ENDS {name}")
    if design and design.get("instances"):
        ports = design.get("ports", {})
        ordered = list(ports) if isinstance(ports, dict) else list(ports)
        lines.append(f".SUBCKT {top_name} {' '.join(ordered)}")
        for inst in design["instances"]:
            cell = inst["cell"]
            con = inst.get("connections", {})
            lines.append(f"X{inst['name']} {' '.join(str(con[p]) for p in specs[cell]['pins'])} {cell}")
        lines.append(f".ENDS {top_name}")
        top_pins = ordered
        top_cell = top_name
    elif not library:
        top_pins = specs[top_name]["pins"]
        top_cell = top_name
    if not library:
        lines.append(f"XTOP {' '.join(top_pins)} {top_cell}")
        lines.append(".END")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
