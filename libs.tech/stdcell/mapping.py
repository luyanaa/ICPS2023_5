"""Area-first RTL mapping and routed-netlist interchange.

Yosys/ABC remains the synthesis authority when available.  This module provides
small, deterministic adapters around Yosys JSON and a conservative expression
mapper for environments where only a tiny RTL benchmark is being inspected.
All returned objects are stdlib JSON-compatible dictionaries.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from .logic import cell_specs
except ImportError:  # loaded with libs.tech/stdcell on sys.path
    from logic import cell_specs


_PORT_RE = re.compile(r"\b(input|output|inout)\b(?:\s+wire|\s+reg)?\s*(?:\[[^]]+\]\s*)?([A-Za-z_$][\w$]*)")
_ASSIGN_RE = re.compile(r"assign\s+([A-Za-z_$][\w$]*)\s*=\s*([^;]+);")
_EXPR_TOKEN = re.compile(r"!|\^|&|\||\(|\)|[A-Za-z_$][\w$]*|[01]")


def _canonical_netlist(name: str, ports: dict[str, str], instances: list[dict[str, Any]], nets: Iterable[str] | None = None) -> dict[str, Any]:
    all_nets = set(nets or ()) | set(ports)
    for inst in instances:
        all_nets.update(inst.get("connections", {}).values())
    return {
        "name": name,
        "ports": dict(ports),
        "instances": [{"name": i["name"], "cell": i["cell"], "connections": dict(i.get("connections", {}))} for i in instances],
        "nets": sorted(str(n) for n in all_nets),
    }


def _load_json(path_or_obj: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_obj, dict):
        return path_or_obj
    return json.loads(Path(path_or_obj).read_text(encoding="utf-8"))


def from_yosys_json(data: str | Path | dict[str, Any], top: str | None = None) -> dict[str, Any]:
    """Convert Yosys JSON netlist into the router's stable interchange form."""
    raw = _load_json(data)
    modules = raw.get("modules", {})
    if not modules:
        # Already canonical, but normalize keys and ordering.
        return _canonical_netlist(raw.get("name", top or "TOP"), raw.get("ports", {}), raw.get("instances", []), raw.get("nets", []))
    top = top or raw.get("top") or next(iter(modules))
    mod = modules[top]
    netnames = mod.get("netnames", {})
    ports: dict[str, str] = {}
    input_bits: dict[Any, str] = {}
    output_bits: list[tuple[str, Any]] = []
    for pname, port in mod.get("ports", {}).items():
        direction = port.get("direction", "input")
        bits = port.get("bits", [])
        if len(bits) != 1:
            raise ValueError(f"Yosys port {pname} is not scalar; vector routing is unsupported")
        ports[pname] = direction
        bit = bits[0]
        if direction in ("input", "inout"):
            input_bits.setdefault(bit, pname)
        if direction in ("output", "inout"):
            output_bits.append((pname, bit))

    internal_nets: dict[Any, str] = {}
    used_net_names = {str(name).casefold() for name in ports} | {"vdd", "vss", "sub"}

    def internal_net(bit: Any, requested: str | None = None) -> str:
        if bit in internal_nets:
            return internal_nets[bit]
        name = requested if requested and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", requested) else None
        if name is None or name.casefold() in used_net_names:
            name = f"N_YOSYS_{bit}"
        while name.casefold() in used_net_names:
            name += "_"
        used_net_names.add(name.casefold())
        internal_nets[bit] = name
        return name

    def net(bit: Any) -> str:
        if isinstance(bit, str):
            return bit
        if bit in input_bits:
            return input_bits[bit]
        output_names = [name for name, candidate in output_bits if candidate == bit]
        if output_names:
            return output_names[0]
        for name, desc in netnames.items():
            if bit in desc.get("bits", []):
                return internal_net(bit, name)
        return internal_net(bit)

    instances: list[dict[str, Any]] = []
    for iname, inst in mod.get("cells", {}).items():
        con: dict[str, str] = {}
        for pin, bits in inst.get("connections", {}).items():
            if isinstance(bits, list):
                con[pin] = net(bits[0]) if len(bits) == 1 else "{" + ",".join(net(b) for b in bits) + "}"
            else:
                con[pin] = net(bits)
        cell = inst.get("type", iname)
        instances.append({"name": iname, "cell": cell, "connections": con})
    used_names = {str(i["name"]) for i in instances}
    for index, (name, bit) in enumerate(output_bits):
        source = net(bit)
        if source == name:
            continue
        alias_name = f"U_PORT_ALIAS{index}"
        while alias_name in used_names:
            alias_name += "_"
        used_names.add(alias_name)
        instances.append({"name": alias_name, "cell": "$_BUF_",
                          "connections": {"A": source, "Y": name}})
    return _canonical_netlist(top, ports, instances)


def _map_yosys_primitives(netlist: dict[str, Any],
                          specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace Yosys simple gates with real library cells; reject unknown cells."""
    ports = dict(netlist["ports"])
    ports.setdefault("VDD", "inout")
    ports.setdefault("VSS", "inout")
    mapped: list[dict[str, Any]] = []
    used_names = {str(i["name"]) for i in netlist["instances"]}
    used_net_names = {str(net).casefold() for net in netlist.get("nets", [])}
    used_net_names.update(str(net).casefold() for net in ports)
    serial = 0

    def fresh_name() -> str:
        nonlocal serial
        while True:
            name = f"U_MAP{serial}"
            serial += 1
            if name not in used_names:
                used_names.add(name)
                return name

    def fresh_net() -> str:
        nonlocal serial
        while True:
            name = f"N_MAP_{serial}"
            serial += 1
            if name.casefold() not in used_net_names:
                used_net_names.add(name.casefold())
                return name

    def emit(cell: str, connections: dict[str, str], name: str | None = None) -> None:
        cell_spec = specs[cell]
        actual = dict(connections)
        if "VDD" in cell_spec["pins"]:
            actual.setdefault("VDD", "VDD")
        if "VSS" in cell_spec["pins"]:
            actual.setdefault("VSS", "VSS")
        missing = set(cell_spec["pins"]) - set(actual)
        if missing:
            raise ValueError(f"{cell} mapping is missing pins {sorted(missing)}")
        mapped.append({"name": name or fresh_name(), "cell": cell,
                       "connections": actual})

    def scalar(value: Any) -> str:
        text = str(value)
        if text == "0":
            return "VSS"
        if text == "1":
            return "VDD"
        if text.startswith("{"):
            raise ValueError(f"vector connection is outside scalar router scope: {text}")
        return text

    for inst in netlist["instances"]:
        cell = str(inst["cell"])
        con = {pin: scalar(value) for pin, value in inst["connections"].items()}
        if cell in specs:
            emit(cell, con, str(inst["name"]))
            continue
        output = con.get("Y")
        a, b, select = con.get("A"), con.get("B"), con.get("S")
        if cell == "$_NOT_" and output and a:
            emit("INV_X1", {"A": a, "Y": output})
        elif cell == "$_BUF_" and output and a:
            emit("BUF_X1", {"A": a, "Y": output})
        elif cell == "$_AND_" and output and a and b:
            intermediate = fresh_net()
            emit("NAND2_X1", {"A": a, "B": b, "Y": intermediate})
            emit("INV_X1", {"A": intermediate, "Y": output})
        elif cell == "$_OR_" and output and a and b:
            intermediate = fresh_net()
            emit("NOR2_X1", {"A": a, "B": b, "Y": intermediate})
            emit("INV_X1", {"A": intermediate, "Y": output})
        elif cell == "$_NAND_" and output and a and b:
            emit("NAND2_X1", {"A": a, "B": b, "Y": output})
        elif cell == "$_NOR_" and output and a and b:
            emit("NOR2_X1", {"A": a, "B": b, "Y": output})
        elif cell == "$_XOR_" and output and a and b:
            emit("XOR2_X1", {"A": a, "B": b, "Y": output})
        elif cell == "$_XNOR_" and output and a and b:
            emit("XNOR2_X1", {"A": a, "B": b, "Y": output})
        elif cell in ("$_MUX_", "$_NMUX_") and output and a and b and select:
            intermediate = output if cell == "$_MUX_" else fresh_net()
            # Yosys selects B for S=1; this library's MUX selects A for S=1.
            emit("MUX2_TG6T_X1", {"A": b, "B": a, "S": select, "Y": intermediate})
            if cell == "$_NMUX_":
                emit("INV_X1", {"A": intermediate, "Y": output})
        elif cell in ("$_DFF_P_", "$_DFF_N_") and con.get("D") and con.get("C") and con.get("Q"):
            clock = con["C"]
            if cell == "$_DFF_N_":
                inverted_clock = fresh_net()
                emit("INV_X1", {"A": clock, "Y": inverted_clock})
                clock = inverted_clock
            emit("DFF_TG_X1", {"D": con["D"], "CLK": clock, "Q": con["Q"]})
        elif cell in ("$_DLATCH_P_", "$_DLATCH_N_") and con.get("D") and con.get("E") and con.get("Q"):
            enable = con["E"]
            if cell == "$_DLATCH_N_":
                inverted_enable = fresh_net()
                emit("INV_X1", {"A": enable, "Y": inverted_enable})
                enable = inverted_enable
            emit("LATCH_TG_X1", {"D": con["D"], "CLK": enable, "Q": con["Q"]})
        else:
            raise ValueError(f"unsupported Yosys cell {cell} at {inst['name']}")
    return _canonical_netlist(netlist["name"], ports, mapped, netlist["nets"])


def export_netlist(netlist: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """Write canonical routed-design input JSON and return the normalized object."""
    normalized = _canonical_netlist(netlist["name"], netlist.get("ports", {}), netlist.get("instances", []), netlist.get("nets", []))
    Path(path).write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def _ports_from_verilog(source: str) -> tuple[str, dict[str, str]]:
    header = re.search(r"\bmodule\s+([A-Za-z_$][\w$]*)\s*(?:#\([^)]*\))?\s*\((.*?)\)\s*;", source, re.S)
    if not header:
        raise ValueError("module declaration with ANSI/non-ANSI port list required")
    name, body = header.group(1), header.group(2)
    ports: dict[str, str] = {}
    # ANSI declarations are enough for supplied benchmarks; non-declared names
    # are retained as ports and default to input for deterministic diagnostics.
    for direction, pname in _PORT_RE.findall(body): ports[pname] = direction
    for pname in re.findall(r"[A-Za-z_$][\w$]*", body):
        if pname not in {"input", "output", "inout", "wire", "reg"} and pname not in ports:
            ports[pname] = "input"
    # Capture declarations in module body for non-ANSI syntax.
    for direction, pname in _PORT_RE.findall(source[header.end():]): ports.setdefault(pname, direction)
    return name, ports


def _expr_ast(expr: str) -> Any:
    toks = _EXPR_TOKEN.findall(expr)
    if "".join(toks).replace(" ", "") not in re.sub(r"\s+", "", expr):
        raise ValueError(f"unsupported RTL expression: {expr}")
    i = 0
    def primary() -> Any:
        nonlocal i
        if i >= len(toks): raise ValueError("incomplete expression")
        t = toks[i]
        if t == "(":
            i += 1; x = orexp()
            if i >= len(toks) or toks[i] != ")": raise ValueError("missing expression parenthesis")
            i += 1; return x
        i += 1
        if t == "!": return ("not", primary())
        return t
    def andexp() -> Any:
        nonlocal i
        x = primary()
        while i < len(toks) and toks[i] == "&": i += 1; x = ("and", x, primary())
        return x
    def xorexp() -> Any:
        nonlocal i
        x = andexp()
        while i < len(toks) and toks[i] == "^": i += 1; x = ("xor", x, andexp())
        return x
    def orexp() -> Any:
        nonlocal i
        x = xorexp()
        while i < len(toks) and toks[i] == "|": i += 1; x = ("or", x, xorexp())
        return x
    out = orexp()
    if i != len(toks): raise ValueError(f"unexpected token {toks[i]}")
    return out


def _expr_text(x: Any) -> str:
    if isinstance(x, str): return x
    op = x[0]
    if op == "not": return "!" + _expr_text(x[1])
    return "(" + f" {op_map[op]} ".join(_expr_text(y) for y in x[1:]) + ")"


op_map = {"and": "&", "or": "|", "xor": "^"}


def _map_expr(expr: str, output: str, specs: dict[str, dict[str, Any]], instances: list[dict[str, Any]], index: list[int], nets: set[str]) -> str:
    ast = _expr_ast(expr)
    used_net_names = {str(net).casefold() for net in nets}

    def fresh_net() -> str:
        while True:
            name = f"N_MAP_{index[0]}"
            index[0] += 1
            if name.casefold() not in used_net_names:
                used_net_names.add(name.casefold())
                nets.add(name)
                return name

    def rec(x: Any) -> str:
        if isinstance(x, str):
            return {"0": "VSS", "1": "VDD"}.get(x, x)
        op = x[0]
        if op == "not":
            a = rec(x[1]); n = fresh_net()
            instances.append({"name": f"U{len(instances)}", "cell": "INV_X1", "connections": {"A": a, "Y": n, "VDD": "VDD", "VSS": "VSS"}})
            return n
        left, right = rec(x[1]), rec(x[2]); n = fresh_net()
        wanted = {"and": "NAND2_X1", "or": "NOR2_X1", "xor": "XOR2_X1"}[op]
        # De Morgan cells require an output inversion; this keeps every mapped
        # function exact while retaining actual library area accounting.
        instances.append({"name": f"U{len(instances)}", "cell": wanted, "connections": {"A": left, "B": right, "Y": n, "VDD": "VDD", "VSS": "VSS"}})
        if op in ("and", "or"):
            final = fresh_net()
            instances.append({"name": f"U{len(instances)}", "cell": "INV_X1", "connections": {"A": n, "Y": final, "VDD": "VDD", "VSS": "VSS"}})
            return final
        return n
    result = rec(ast)
    if result != output:
        instances.append({"name": f"U{len(instances)}", "cell": "BUF_X1", "connections": {"A": result, "Y": output, "VDD": "VDD", "VSS": "VSS"}})
    return output


def map_boolean_design(name: str, ports: dict[str, str], outputs: dict[str, str], *, specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Map scalar Boolean assignments into real X1 library instances."""
    specs = specs or cell_specs()
    port_directions = dict(ports)
    port_directions.setdefault("VDD", "inout")
    port_directions.setdefault("VSS", "inout")
    instances: list[dict[str, Any]] = []
    nets = set(port_directions)
    index = [0]
    for output, expr in outputs.items():
        _map_expr(expr, output, specs, instances, index, nets)
    return _canonical_netlist(name, port_directions, instances, nets)


def synthesize_rtl(source: str | Path, *, top: str | None = None, specs: dict[str, dict[str, Any]] | None = None,
                   yosys: str | None = None, workdir: str | Path | None = None) -> dict[str, Any]:
    """Synthesize RTL, then map the scalar Yosys gate set to physical X1 cells.

    ABC performs technology-neutral logic optimization; it does not consume
    unqualified timing arcs or claim area-optimal mapping. Without Yosys, only
    scalar continuous assignments are accepted.
    """
    source_text = Path(source).read_text(encoding="utf-8") if isinstance(source, (str, Path)) and Path(source).exists() else str(source)
    top_name, ports = _ports_from_verilog(source_text)
    top_name = top or top_name
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", top_name):
        raise ValueError(f"invalid Yosys top module name {top_name!r}")
    selected_specs = specs or cell_specs()
    exe = yosys or shutil.which("yosys")
    if exe:
        def run_in(directory: Path) -> dict[str, Any]:
            directory.mkdir(parents=True, exist_ok=True)
            inp, out = directory / "input.v", directory / "mapped.json"
            inp.write_text(source_text, encoding="utf-8")
            script = (f"read_verilog -sv {json.dumps(str(inp))}; "
                      f"hierarchy -check -top {top_name}; proc; opt; techmap; opt; "
                      f"abc -g simple; opt; check; write_json {json.dumps(str(out))}")
            run = subprocess.run([exe, "-q", "-p", script], capture_output=True, text=True)
            if run.returncode != 0 or not out.exists():
                raise RuntimeError(f"Yosys failed: {run.stderr.strip()}")
            raw_netlist = from_yosys_json(out, top_name)
            return _map_yosys_primitives(raw_netlist, selected_specs)
        if workdir is not None:
            return run_in(Path(workdir))
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_") as temporary:
            return run_in(Path(temporary))
    outputs = {m.group(1): m.group(2).strip() for m in _ASSIGN_RE.finditer(source_text)}
    declared_outputs = {name for name, direction in ports.items() if direction == "output"}
    missing_outputs = declared_outputs - set(outputs)
    if not outputs or missing_outputs:
        raise RuntimeError("Yosys unavailable and scalar continuous assignments do not drive all outputs: %s" %
                           sorted(missing_outputs))
    return map_boolean_design(top_name, ports, outputs, specs=selected_specs)


def synth(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return synthesize_rtl(*args, **kwargs)


def area(netlist: dict[str, Any], manifests: dict[str, dict[str, Any]] | None = None,
         specs: dict[str, dict[str, Any]] | None = None) -> float:
    """Sum qualified geometry manifests; incomplete cell area is an error."""
    manifests = manifests or {}
    specs = specs or cell_specs()
    total = 0.0
    for inst in netlist.get("instances", []):
        cell = inst["cell"]
        if cell not in specs:
            raise ValueError(f"unknown standard cell {cell!r} in area query")
        manifest = manifests.get(cell)
        if manifest is None:
            raise ValueError(f"no physical area manifest for {cell}")
        value = manifest.get("area_um2")
        if value is None:
            if "width_um" not in manifest or "height_um" not in manifest:
                raise ValueError(f"area manifest for {cell} has no area or dimensions")
            value = float(manifest["width_um"]) * float(manifest["height_um"])
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid physical area for {cell}: {value!r}")
        total += value
    return total
