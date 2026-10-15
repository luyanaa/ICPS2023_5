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


class _CellMapper:
    """Emit only cells in the selected view, decomposing common gates as needed."""

    _NAND = ("NAND2_X1", "NAND2_BA_X1")
    _NOR = ("NOR2_X1", "NOR2_BA_X1")

    def __init__(self, specs: dict[str, dict[str, Any]], library_view: str,
                 instances: list[dict[str, Any]], nets: set[str]) -> None:
        self.specs = specs
        self.library_view = library_view
        self.instances = instances
        self.nets = nets
        self.used_names = {str(inst["name"]) for inst in instances}
        self.used_net_names = {str(net).casefold() for net in nets}
        self.serial = 0

    def fresh_name(self) -> str:
        while True:
            name = f"U_MAP{self.serial}"
            self.serial += 1
            if name not in self.used_names:
                self.used_names.add(name)
                return name

    def fresh_net(self) -> str:
        while True:
            name = f"N_MAP_{self.serial}"
            self.serial += 1
            if name.casefold() not in self.used_net_names:
                self.used_net_names.add(name.casefold())
                self.nets.add(name)
                return name

    def _cell(self, names: Iterable[str]) -> str | None:
        return next((name for name in names if name in self.specs), None)

    def emit(self, cell: str, connections: dict[str, str],
             name: str | None = None) -> None:
        cell_spec = self.specs.get(cell)
        if cell_spec is None:
            raise ValueError(f"{cell} is not eligible in the {self.library_view} view")
        actual = dict(connections)
        if "VDD" in cell_spec["pins"]:
            actual.setdefault("VDD", "VDD")
        if "VSS" in cell_spec["pins"]:
            actual.setdefault("VSS", "VSS")
        missing = set(cell_spec["pins"]) - set(actual)
        if missing:
            raise ValueError(f"{cell} mapping is missing pins {sorted(missing)}")
        self.instances.append({"name": name or self.fresh_name(), "cell": cell,
                               "connections": actual})

    def invert(self, source: str, target: str | None = None) -> str:
        target = target or self.fresh_net()
        cell = self._cell(("INV_X1", *self._NAND, *self._NOR))
        if cell is None:
            raise ValueError(
                f"cannot implement NOT in the {self.library_view} view: "
                "INV_X1, NAND2_X1, and NOR2_X1 are unavailable")
        if cell == "INV_X1":
            self.emit(cell, {"A": source, "Y": target})
        else:
            self.emit(cell, {"A": source, "B": source, "Y": target})
        return target

    def buffer(self, source: str, target: str) -> str:
        if "BUF_X1" in self.specs:
            self.emit("BUF_X1", {"A": source, "Y": target})
            return target
        intermediate = self.invert(source)
        return self.invert(intermediate, target)

    def constant(self, value: str, target: str) -> str:
        if value == "0":
            source = "VDD"
        else:
            source = "VSS"
        return self.invert(source, target)

    def nand2(self, a: str, b: str, target: str) -> str:
        cell = self._cell(self._NAND)
        if cell is not None:
            self.emit(cell, {"A": a, "B": b, "Y": target})
            return target
        positive = self.and2(a, b, self.fresh_net())
        return self.invert(positive, target)

    def nor2(self, a: str, b: str, target: str) -> str:
        cell = self._cell(self._NOR)
        if cell is not None:
            self.emit(cell, {"A": a, "B": b, "Y": target})
            return target
        positive = self.or2(a, b, self.fresh_net())
        return self.invert(positive, target)

    def and2(self, a: str, b: str, target: str) -> str:
        direct = "AND2_X1" in self.specs
        if direct and self.library_view != "physical_qualified":
            self.emit("AND2_X1", {"A": a, "B": b, "Y": target})
        elif self._cell(self._NAND) is not None:
            negative = self.fresh_net()
            self.nand2(a, b, negative)
            self.invert(negative, target)
        elif self._cell(self._NOR) is not None:
            self.nor2(self.invert(a), self.invert(b), target)
        elif direct:
            self.emit("AND2_X1", {"A": a, "B": b, "Y": target})
        else:
            raise ValueError(
                f"cannot implement AND in the {self.library_view} view: "
                "AND2_X1, NAND2_X1, and NOR2_X1 are unavailable")
        return target

    def or2(self, a: str, b: str, target: str) -> str:
        direct = "OR2_X1" in self.specs
        if direct and self.library_view != "physical_qualified":
            self.emit("OR2_X1", {"A": a, "B": b, "Y": target})
        elif self._cell(self._NOR) is not None:
            negative = self.fresh_net()
            self.nor2(a, b, negative)
            self.invert(negative, target)
        elif self._cell(self._NAND) is not None:
            self.nand2(self.invert(a), self.invert(b), target)
        elif direct:
            self.emit("OR2_X1", {"A": a, "B": b, "Y": target})
        else:
            raise ValueError(
                f"cannot implement OR in the {self.library_view} view: "
                "OR2_X1, NAND2_X1, and NOR2_X1 are unavailable")
        return target

    def xor2(self, a: str, b: str, target: str) -> str:
        if "XOR2_X1" in self.specs:
            self.emit("XOR2_X1", {"A": a, "B": b, "Y": target})
        elif self._cell(self._NAND) is not None:
            not_a, not_b = self.invert(a), self.invert(b)
            left, right = self.fresh_net(), self.fresh_net()
            self.nand2(a, not_b, left)
            self.nand2(not_a, b, right)
            self.nand2(left, right, target)
        else:
            not_a, not_b = self.invert(a), self.invert(b)
            left, right = self.fresh_net(), self.fresh_net()
            self.and2(a, not_b, left)
            self.and2(not_a, b, right)
            self.or2(left, right, target)
        return target

    def xnor2(self, a: str, b: str, target: str) -> str:
        if "XNOR2_X1" in self.specs:
            self.emit("XNOR2_X1", {"A": a, "B": b, "Y": target})
        else:
            intermediate = self.fresh_net()
            self.xor2(a, b, intermediate)
            self.invert(intermediate, target)
        return target

    def mux_yosys(self, a: str, b: str, select: str, target: str) -> str:
        cell = self._cell(("MUX2_TG6T_X1", "MUX2_STATIC_X1"))
        if cell is not None:
            # The library mux selects A at S=1; Yosys selects B.
            self.emit(cell, {"A": b, "B": a, "S": select, "Y": target})
        else:
            selected_b = self.and2(b, select, self.fresh_net())
            selected_a = self.and2(a, self.invert(select), self.fresh_net())
            self.or2(selected_b, selected_a, target)
        return target


def _validate_library_view(library_view: str) -> None:
    if library_view not in ("logical_reference", "physical_qualified"):
        raise ValueError(
            "library_view must be 'logical_reference' or 'physical_qualified'")


def _mapping_specs(library_view: str, manifest_path: str | Path | None,
                   specs: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    _validate_library_view(library_view)
    if library_view == "logical_reference":
        return cell_specs() if specs is None else specs
    try:
        try:
            from .library import physical_specs
        except ImportError:  # loaded with libs.tech/stdcell on sys.path
            from library import physical_specs
    except ImportError as exc:
        raise RuntimeError(
            "physical_qualified mapping requires library.physical_specs()") from exc
    accepted = physical_specs(manifest_path)
    if not isinstance(accepted, dict):
        raise TypeError("library.physical_specs() must return a cell-to-spec mapping")
    if specs is None:
        return accepted
    # An explicit spec set can narrow a qualified view, never broaden it or
    # replace its validated physical records.
    return {name: accepted[name] for name in specs if name in accepted}


def _map_yosys_primitives(netlist: dict[str, Any],
                          specs: dict[str, dict[str, Any]], *,
                          library_view: str) -> dict[str, Any]:
    """Map Yosys cells into eligible library cells or an eligible gate network."""
    _validate_library_view(library_view)
    ports = dict(netlist["ports"])
    ports.setdefault("VDD", "inout")
    ports.setdefault("VSS", "inout")
    mapped: list[dict[str, Any]] = []
    nets = set(netlist.get("nets", [])) | set(ports)
    mapper = _CellMapper(specs, library_view, mapped, nets)
    canonical_specs = cell_specs()

    storage_cells: dict[int, str] = {}
    for index, inst in enumerate(netlist["instances"]):
        cell = str(inst["cell"])
        if cell in ("$_DFF_P_", "$_DFF_N_"):
            candidates = ("DFF_TG_X1", "DFF_GATE_X1")
            kind = "DFF"
        elif cell in ("$_DLATCH_P_", "$_DLATCH_N_"):
            candidates = ("LATCH_TG_X1", "LATCH_GATE_X1")
            kind = "latch"
        elif cell in canonical_specs and canonical_specs[cell].get("sequential"):
            candidates = (cell,)
            kind = "latch" if "LATCH" in cell else "DFF"
        else:
            continue
        storage_cell = next((candidate for candidate in candidates
                             if candidate in specs), None)
        if storage_cell is None:
            raise ValueError(
                f"{kind} cell {'/'.join(candidates)} is not eligible in the "
                f"{library_view} view")
        storage_cells[index] = storage_cell

    def scalar(value: Any) -> str:
        text = str(value)
        if text == "0":
            return "VSS"
        if text == "1":
            return "VDD"
        if text.startswith("{"):
            raise ValueError(f"vector connection is outside scalar router scope: {text}")
        return text

    for index, inst in enumerate(netlist["instances"]):
        cell = str(inst["cell"])
        con = {pin: scalar(value) for pin, value in inst["connections"].items()}
        if cell in specs:
            mapper.emit(cell, con, str(inst["name"]))
            continue
        if cell in canonical_specs:
            raise ValueError(
                f"canonical cell {cell} is not eligible in the {library_view} view")
        output = con.get("Y")
        a, b, select = con.get("A"), con.get("B"), con.get("S")
        if cell == "$_NOT_" and output and a:
            mapper.invert(a, output)
        elif cell == "$_BUF_" and output and a:
            mapper.buffer(a, output)
        elif cell == "$_AND_" and output and a and b:
            mapper.and2(a, b, output)
        elif cell == "$_OR_" and output and a and b:
            mapper.or2(a, b, output)
        elif cell == "$_NAND_" and output and a and b:
            mapper.nand2(a, b, output)
        elif cell == "$_NOR_" and output and a and b:
            mapper.nor2(a, b, output)
        elif cell == "$_XOR_" and output and a and b:
            mapper.xor2(a, b, output)
        elif cell == "$_XNOR_" and output and a and b:
            mapper.xnor2(a, b, output)
        elif cell in ("$_MUX_", "$_NMUX_") and output and a and b and select:
            if cell == "$_NMUX_":
                intermediate = mapper.fresh_net()
                mapper.mux_yosys(a, b, select, intermediate)
                mapper.invert(intermediate, output)
            else:
                mapper.mux_yosys(a, b, select, output)
        elif cell in ("$_DFF_P_", "$_DFF_N_") and con.get("D") and con.get("C") and con.get("Q"):
            clock = con["C"]
            if cell == "$_DFF_N_":
                clock = mapper.invert(clock)
            mapper.emit(storage_cells[index],
                        {"D": con["D"], "CLK": clock, "Q": con["Q"]})
        elif cell in ("$_DLATCH_P_", "$_DLATCH_N_") and con.get("D") and con.get("E") and con.get("Q"):
            enable = con["E"]
            if cell == "$_DLATCH_N_":
                enable = mapper.invert(enable)
            mapper.emit(storage_cells[index],
                        {"D": con["D"], "CLK": enable, "Q": con["Q"]})
        else:
            raise ValueError(f"unsupported Yosys cell {cell} at {inst['name']} "
                             f"in the {library_view} view")
    return _canonical_netlist(netlist["name"], ports, mapped, nets)


def map_yosys_netlist(data: str | Path | dict[str, Any], top: str | None = None, *,
                      library_view: str = "logical_reference",
                      manifest_path: str | Path | None = None,
                      specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Convert Yosys JSON (or canonical interchange) into the selected cell view."""
    selected_specs = _mapping_specs(library_view, manifest_path, specs)
    return _map_yosys_primitives(from_yosys_json(data, top), selected_specs,
                                 library_view=library_view)




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


def _map_expr(expr: str, output: str, specs: dict[str, dict[str, Any]],
              instances: list[dict[str, Any]], nets: set[str], *,
              library_view: str) -> str:
    ast = _expr_ast(expr)
    mapper = _CellMapper(specs, library_view, instances, nets)

    def rec(x: Any, target: str | None = None) -> str:
        if isinstance(x, str):
            if x in ("0", "1"):
                if target is None:
                    return "VSS" if x == "0" else "VDD"
                return mapper.constant(x, target)
            if target is not None and target != x:
                return mapper.buffer(x, target)
            return x
        op = x[0]
        if op == "not":
            return mapper.invert(rec(x[1]), target)
        left, right = rec(x[1]), rec(x[2])
        destination = target or mapper.fresh_net()
        {"and": mapper.and2, "or": mapper.or2,
         "xor": mapper.xor2}[op](left, right, destination)
        return destination

    return rec(ast, output)


def map_boolean_design(name: str, ports: dict[str, str],
                       outputs: dict[str, str], *,
                       library_view: str = "logical_reference",
                       manifest_path: str | Path | None = None,
                       specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Map scalar Boolean assignments into cells eligible in the selected view."""
    selected_specs = _mapping_specs(library_view, manifest_path, specs)
    port_directions = dict(ports)
    port_directions.setdefault("VDD", "inout")
    port_directions.setdefault("VSS", "inout")
    instances: list[dict[str, Any]] = []
    nets = set(port_directions)
    for output, expr in outputs.items():
        _map_expr(expr, output, selected_specs, instances, nets,
                  library_view=library_view)
    return _canonical_netlist(name, port_directions, instances, nets)


def synthesize_rtl(source: str | Path, *, top: str | None = None,
                   specs: dict[str, dict[str, Any]] | None = None,
                   library_view: str = "logical_reference",
                   manifest_path: str | Path | None = None,
                   yosys: str | None = None,
                   workdir: str | Path | None = None) -> dict[str, Any]:
    """Synthesize RTL and map it using the requested reference or physical view.

    ABC performs technology-neutral logic optimization; it does not consume
    unqualified timing arcs or claim area-optimal mapping. Without Yosys, only
    scalar continuous assignments are accepted.
    """
    _validate_library_view(library_view)
    source_text = Path(source).read_text(encoding="utf-8") if isinstance(source, (str, Path)) and Path(source).exists() else str(source)
    top_name, ports = _ports_from_verilog(source_text)
    top_name = top or top_name
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", top_name):
        raise ValueError(f"invalid Yosys top module name {top_name!r}")
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
            return map_yosys_netlist(out, top_name, library_view=library_view,
                                     manifest_path=manifest_path, specs=specs)
        if workdir is not None:
            return run_in(Path(workdir))
        with tempfile.TemporaryDirectory(prefix="icps_stdcell_") as temporary:
            return run_in(Path(temporary))
    if (library_view == "physical_qualified"
            and re.search(r"\balways(?:_ff|_latch)?\b", source_text)):
        raise ValueError(
            "procedural always blocks, including latch/DFF storage, are "
            "unsupported by scalar synthesis in the physical_qualified view")
    outputs = {m.group(1): m.group(2).strip() for m in _ASSIGN_RE.finditer(source_text)}
    declared_outputs = {name for name, direction in ports.items() if direction == "output"}
    missing_outputs = declared_outputs - set(outputs)
    if not outputs or missing_outputs:
        raise RuntimeError("Yosys unavailable and scalar continuous assignments do not drive all outputs: %s" %
                           sorted(missing_outputs))
    return map_boolean_design(top_name, ports, outputs, library_view=library_view,
                              manifest_path=manifest_path, specs=specs)



def synth(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return synthesize_rtl(*args, **kwargs)


def area(netlist: dict[str, Any], manifests: dict[str, dict[str, Any]] | None = None,
         specs: dict[str, dict[str, Any]] | None = None, *,
         manifest_path: str | Path | None = None) -> float:
    """Sum only cells covered by an explicit accepted physical area contract."""
    if manifests is None:
        try:
            try:
                from .library import physical_manifests
            except ImportError:  # loaded with libs.tech/stdcell on sys.path
                from library import physical_manifests
        except ImportError as exc:
            raise RuntimeError(
                "physical area requires library.physical_manifests()") from exc
        manifests = physical_manifests(manifest_path)
        if not isinstance(manifests, dict):
            raise TypeError(
                "library.physical_manifests() must return a cell-to-record mapping")
        eligible = ({cell: {} for cell in manifests} if specs is None else
                    {cell: specs[cell] for cell in specs if cell in manifests})
    else:
        if not isinstance(manifests, dict):
            raise TypeError("manifests must be a cell-to-record mapping")
        eligible = ({cell: {} for cell in manifests} if specs is None else
                    {cell: specs[cell] for cell in specs if cell in manifests})

    total = 0.0
    for inst in netlist.get("instances", []):
        cell = inst["cell"]
        if cell not in eligible:
            raise ValueError(
                f"cell {cell!r} is not eligible in the physical area contract")
        manifest = manifests.get(cell)
        if not isinstance(manifest, dict):
            raise ValueError(f"no physical area manifest for {cell}")
        try:
            width = float(manifest["width_um"])
            height = float(manifest["height_um"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"area manifest for {cell} must provide finite width_um and height_um") from exc
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise ValueError(
                f"invalid physical dimensions for {cell}: {width!r} x {height!r}")
        dimension_area = width * height
        if not math.isfinite(dimension_area) or dimension_area <= 0:
            raise ValueError(f"invalid physical area from dimensions for {cell}")
        raw_area = manifest.get("area_um2")
        if raw_area is not None:
            try:
                value = float(raw_area)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid physical area for {cell}: {raw_area!r}") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid physical area for {cell}: {value!r}")
            if not math.isclose(value, dimension_area, rel_tol=1e-6, abs_tol=1e-9):
                raise ValueError(
                    f"physical area for {cell} ({value}) disagrees with dimensions "
                    f"({dimension_area})")
        else:
            value = dimension_area
        total += value
    return total
