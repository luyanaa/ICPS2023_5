#!/usr/bin/env python3
"""Read an explicitly selected, assembled/PEX SPICE subcircuit for reliability analysis.

This reader deliberately supports only resistor, capacitor, and MOSFET elements.
It never expands arbitrary hierarchy or quietly discards an unsupported device.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


class ReliabilityNetlistError(ValueError):
    """The selected SPICE network cannot be represented without losing evidence."""


_NUMBER = re.compile(
    r"\A(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?P<suffix>[A-Za-z]*)\Z"
)
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.])(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[A-Za-z]*"
)
_ASSIGNMENT = re.compile(r"\A([A-Za-z_][A-Za-z0-9_$]*)=(.*)\Z", re.DOTALL)
_PARAM_ASSIGNMENT = re.compile(
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*(\{[^{}]*\}|\"[^\"]*\"|'[^']*'|[^\s]+)"
)
_SUFFIXES = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "mil": 25.4e-6,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}
_NATIVE_RESISTOR_MAPS = ("ICPS2023_5_R_TIN", "R_TIN_MAP")
_MAP_PREFIXES = {
    "PORT_MAP": "port_maps",
    "MOS_MAP": "mos_maps",
    "RESISTOR_MAP": "resistor_maps",
    "ASSEMBLY_NODE_MAP": "assembly_node_maps",
    "MOS_DEVICE_MAP": "mos_device_maps",
}


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Join SPICE '+' continuation lines while retaining the starting line number."""
    result: list[tuple[int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("+"):
            if not result:
                raise ReliabilityNetlistError(
                    "line %d: continuation has no preceding SPICE line" % line_number
                )
            start, previous = result[-1]
            result[-1] = (start, previous + " " + raw.lstrip()[1:].strip())
        else:
            result.append((line_number, raw))
    return result


def _fields(line: str) -> list[str]:
    """Split tokens without breaking braced expressions or quoted strings."""
    fields: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise ReliabilityNetlistError("unbalanced '}' in SPICE statement: %s" % line)
        elif char.isspace() and depth == 0:
            if start < index:
                fields.append(line[start:index])
            start = index + 1
    if quote is not None or depth != 0:
        raise ReliabilityNetlistError("unbalanced quote or brace in SPICE statement: %s" % line)
    if start < len(line):
        fields.append(line[start:])
    return fields


def _strip_inline_comment(line: str) -> str:
    """Remove unquoted '$' and ';' comments, not text inside parameter braces."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 and char in "$;":
            return line[:index].rstrip()
    return line.rstrip()


def _spice_number(text: str) -> float:
    """Parse a SPICE numeric literal and its engineering suffix."""
    value = text.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    match = _NUMBER.fullmatch(value)
    if match is None:
        raise ReliabilityNetlistError("not a numeric SPICE value: %r" % text)
    number = float(match.group("number"))
    suffix = match.group("suffix").casefold()
    if suffix:
        scale = next((factor for prefix, factor in sorted(
            _SUFFIXES.items(), key=lambda item: -len(item[0])
        ) if suffix.startswith(prefix)), None)
        if scale is None:
            raise ReliabilityNetlistError("unknown SPICE engineering suffix in %r" % text)
        number *= scale
    if not math.isfinite(number):
        raise ReliabilityNetlistError("SPICE value is not finite: %r" % text)
    return number


def _parameter_declarations(fields: list[str], line: str) -> dict[str, str]:
    body = line.strip()[len(fields[0]):].strip()
    matches = list(_PARAM_ASSIGNMENT.finditer(body))
    if not matches:
        raise ReliabilityNetlistError("malformed .param directive: %s" % line)
    residue = list(body)
    for match in matches:
        residue[match.start():match.end()] = " " * (match.end() - match.start())
    if "=" in "".join(residue):
        raise ReliabilityNetlistError("malformed .param assignment: %s" % line)
    declarations: dict[str, str] = {}
    for match in matches:
        key = match.group(1).casefold()
        if key in declarations:
            raise ReliabilityNetlistError("duplicate .param name %s" % match.group(1))
        declarations[key] = match.group(2)
    return declarations


def _eval_expression(expression: str, parameters: dict[str, str], stack: tuple[str, ...]) -> float:
    """Evaluate scalar SPICE arithmetic without Python eval or simulator functions."""
    substitutions: list[str] = []

    def replace_number(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            parsed = _spice_number(token)
        except ReliabilityNetlistError:
            return token
        replacement = "_n%d" % len(substitutions)
        substitutions.append(replacement + "=" + repr(parsed))
        return replacement

    source = _NUMBER_TOKEN.sub(replace_number, expression)
    values = {item.split("=", 1)[0]: float(item.split("=", 1)[1]) for item in substitutions}

    def resolve_name(name: str) -> float:
        key = name.casefold()
        if key in values:
            return values[key]
        if key not in parameters:
            raise ReliabilityNetlistError("unresolved SPICE parameter %s" % name)
        if key in stack:
            raise ReliabilityNetlistError("cyclic SPICE parameter definition: %s" % " -> ".join(stack + (key,)))
        return _resolve_value(parameters[key], parameters, stack + (key,))

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            return resolve_name(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            return left ** right
        raise ReliabilityNetlistError("unsupported SPICE scalar expression %r" % expression)

    try:
        result = evaluate(ast.parse(source, mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError) as error:
        raise ReliabilityNetlistError("invalid SPICE scalar expression %r: %s" % (expression, error)) from error
    if not math.isfinite(result):
        raise ReliabilityNetlistError("SPICE expression is not finite: %r" % expression)
    return result


def _resolve_value(text: str, parameters: dict[str, str], stack: tuple[str, ...] = ()) -> float:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    try:
        return _spice_number(value)
    except ReliabilityNetlistError:
        pass
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value):
        key = value.casefold()
        if key not in parameters:
            raise ReliabilityNetlistError("unresolved SPICE parameter %s" % value)
        if key in stack:
            raise ReliabilityNetlistError("cyclic SPICE parameter definition: %s" % " -> ".join(stack + (key,)))
        return _resolve_value(parameters[key], parameters, stack + (key,))
    return _eval_expression(value, parameters, stack)


def _valid_assignments(tokens: list[str]) -> bool:
    return all(_ASSIGNMENT.fullmatch(token) is not None for token in tokens)


def _parse_assignments(tokens: list[str], element: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in tokens:
        match = _ASSIGNMENT.fullmatch(token)
        if match is None:
            raise ReliabilityNetlistError("unsupported positional parameter %r on %s" % (token, element))
        key = match.group(1).casefold()
        if key in result:
            raise ReliabilityNetlistError("duplicate parameter %s on %s" % (match.group(1), element))
        result[key] = match.group(2)
    return result


def _map_records(lines: list[tuple[int, str]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    records = {field: [] for field in _MAP_PREFIXES.values()}
    records["native_resistor_maps"] = []
    plain_comments: list[str] = []
    for line_number, raw in lines:
        line = raw.strip()
        if not line.startswith("*"):
            continue
        recognized = False
        for prefix, field in _MAP_PREFIXES.items():
            marker = "* " + prefix + " "
            if line.startswith(marker):
                try:
                    record = json.loads(line[len(marker):])
                except json.JSONDecodeError as error:
                    raise ReliabilityNetlistError(
                        "line %d: malformed %s JSON: %s" % (line_number, prefix, error)
                    ) from error
                if not isinstance(record, dict):
                    raise ReliabilityNetlistError(
                        "line %d: %s must contain a JSON object" % (line_number, prefix)
                    )
                records[field].append(record)
                recognized = True
                break
        if not recognized:
            for prefix in _NATIVE_RESISTOR_MAPS:
                marker = "* " + prefix + " "
                if line.startswith(marker):
                    try:
                        record = json.loads(line[len(marker):])
                    except json.JSONDecodeError as error:
                        raise ReliabilityNetlistError(
                            "line %d: malformed %s JSON: %s" % (line_number, prefix, error)
                        ) from error
                    if not isinstance(record, dict):
                        raise ReliabilityNetlistError(
                            "line %d: %s must contain a JSON object" % (line_number, prefix)
                        )
                    record = dict(record)
                    record["_comment_type"] = prefix
                    records["native_resistor_maps"].append(record)
                    recognized = True
                    break
        if not recognized:
            plain_comments.append(line[1:].strip())
    return records, plain_comments


def _select_subcircuit(lines: list[tuple[int, str]], top: str | None) -> tuple[str, list[str], list[tuple[int, str]], list[str]]:
    subcircuits: list[tuple[str, list[str], list[tuple[int, str]]]] = []
    current_name: str | None = None
    current_ports: list[str] = []
    current_body: list[tuple[int, str]] = []
    top_level_directives: list[str] = []
    for line_number, raw in lines:
        stripped = _strip_inline_comment(raw).strip()
        if not stripped or stripped.startswith("*"):
            continue
        fields = _fields(stripped)
        if not fields:
            continue
        directive = fields[0].casefold()
        if directive == ".subckt":
            if current_name is not None:
                raise ReliabilityNetlistError(
                    "line %d: nested .SUBCKT %s hierarchy is unsupported" % (line_number, fields[1] if len(fields) > 1 else "")
                )
            if len(fields) < 2:
                raise ReliabilityNetlistError("line %d: malformed .SUBCKT header" % line_number)
            current_name = fields[1]
            current_ports = fields[2:]
            current_body = []
        elif directive == ".ends":
            if current_name is None:
                raise ReliabilityNetlistError("line %d: .ENDS has no matching .SUBCKT" % line_number)
            if len(fields) > 1 and fields[1].casefold() != current_name.casefold():
                raise ReliabilityNetlistError(
                    "line %d: .ENDS %s does not match .SUBCKT %s" % (line_number, fields[1], current_name)
                )
            subcircuits.append((current_name, current_ports, current_body))
            current_name = None
            current_ports = []
            current_body = []
        elif current_name is not None:
            current_body.append((line_number, raw))
        elif directive.startswith("."):
            top_level_directives.append(stripped)
    if current_name is not None:
        raise ReliabilityNetlistError("unterminated .SUBCKT %s" % current_name)
    if not subcircuits:
        raise ReliabilityNetlistError("input contains no complete .SUBCKT")
    if top is None:
        if len(subcircuits) != 1:
            raise ReliabilityNetlistError(
                "top is required when input contains %d subcircuits: %s" %
                (len(subcircuits), [item[0] for item in subcircuits])
            )
        selected = subcircuits[0]
    else:
        matches = [item for item in subcircuits if item[0].casefold() == top.casefold()]
        if len(matches) != 1:
            raise ReliabilityNetlistError(
                "expected exactly one .SUBCKT %s, found %d" % (top, len(matches))
            )
        selected = matches[0]
    return selected[0], selected[1], selected[2], top_level_directives


def _index_records(records: list[dict[str, Any]], keys: tuple[str, ...], kind: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        candidates = [record[key] for key in keys if isinstance(record.get(key), str) and record[key]]
        if not candidates:
            continue
        key = candidates[0].casefold()
        if key in indexed:
            raise ReliabilityNetlistError("duplicate %s mapping for %s" % (kind, candidates[0]))
        indexed[key] = record
    return indexed


def _resistor_value(token: str, parameters: dict[str, str], mapping: dict[str, Any] | None, name: str) -> float:
    try:
        value = _resolve_value(token, parameters)
    except ReliabilityNetlistError as parse_error:
        if mapping is None or "resistance_ohm" not in mapping:
            raise ReliabilityNetlistError(
                "resistor %s has unsupported/non-numeric value %r (%s); provide a numeric value, resolvable .param, or RESISTOR_MAP resistance_ohm"
                % (name, token, parse_error)
            ) from parse_error
        try:
            value = float(mapping["resistance_ohm"])
        except (TypeError, ValueError) as error:
            raise ReliabilityNetlistError("RESISTOR_MAP resistance_ohm for %s is not numeric" % name) from error
    if not math.isfinite(value) or value <= 0:
        raise ReliabilityNetlistError("resistor %s must have positive finite resistance; got %r" % (name, value))
    return value


def read_network(path: str | Path, top: str | None = None) -> dict[str, Any]:
    """Read one R/C/M SPICE subcircuit and its JSON geometry/mapping comments.

    Args:
        path: SPICE netlist path.
        top: Optional subcircuit name. If omitted, the file must contain exactly
            one complete subcircuit.

    Returns a plain dictionary with original node/instance spelling and
    case-insensitive SPICE map matching. Only explicit circuit elements are
    included; unsupported hierarchy/devices raise :class:`ReliabilityNetlistError`.
    """
    source = Path(path)
    try:
        payload = source.read_bytes()
        text = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReliabilityNetlistError("cannot read UTF-8 SPICE netlist %s: %s" % (source, error)) from error
    logical = _logical_lines(text)
    name, ports, body, top_directives = _select_subcircuit(logical, top)
    map_records, all_comments = _map_records(logical)
    selected_comments = [
        raw.strip()[1:].strip() for _line_no, raw in body
        if raw.strip().startswith("*")
    ]

    folded_ports = [port.casefold() for port in ports]
    if len(folded_ports) != len(set(folded_ports)):
        raise ReliabilityNetlistError(".SUBCKT %s has duplicate case-insensitive ports" % name)
    if any(not port for port in ports):
        raise ReliabilityNetlistError(".SUBCKT %s has an empty port name" % name)

    param_raw: dict[str, str] = {}
    active_lines: list[tuple[int, list[str], str]] = []
    directives = list(top_directives)
    for line_number, raw in body:
        stripped = _strip_inline_comment(raw).strip()
        if not stripped or stripped.startswith("*"):
            continue
        fields = _fields(stripped)
        if not fields:
            continue
        command = fields[0].casefold()
        if command.startswith("."):
            directives.append(stripped)
            if command == ".param":
                declarations = _parameter_declarations(fields, stripped)
                for key, value in declarations.items():
                    if key in param_raw:
                        raise ReliabilityNetlistError("line %d: duplicate .param %s" % (line_number, key))
                    param_raw[key] = value
            elif command in (".subckt", ".ends"):
                raise ReliabilityNetlistError("line %d: incomplete hierarchy in selected circuit" % line_number)
            continue
        active_lines.append((line_number, fields, stripped))

    resolved_parameters: dict[str, float] = {}
    for key in param_raw:
        resolved_parameters[key] = _resolve_value(param_raw[key], param_raw, (key,))

    resistor_maps = map_records["resistor_maps"]
    resistor_map_by_name = _index_records(resistor_maps, ("name", "instance", "element"), "RESISTOR_MAP")
    native_resistor_by_name = _index_records(
        map_records["native_resistor_maps"], ("name", "instance", "element"), "native resistor"
    )
    mos_device_by_name = _index_records(
        map_records["mos_device_maps"], ("name", "instance", "mos"), "MOS_DEVICE_MAP"
    )

    resistors: list[dict[str, Any]] = []
    capacitors: list[dict[str, Any]] = []
    mos: list[dict[str, Any]] = []
    nodes: dict[str, str] = {port.casefold(): port for port in ports}
    element_names: dict[str, str] = {}

    def add_node(node: str) -> None:
        if not node:
            raise ReliabilityNetlistError("empty node name in selected circuit")
        nodes.setdefault(node.casefold(), node)

    def register_element(instance: str, line_number: int) -> str:
        if not instance:
            raise ReliabilityNetlistError("line %d: empty element name" % line_number)
        folded = instance.casefold()
        if folded in element_names:
            raise ReliabilityNetlistError(
                "line %d: duplicate case-insensitive element name %s (also %s)" %
                (line_number, instance, element_names[folded])
            )
        element_names[folded] = instance
        return instance[0].upper()

    for line_number, fields, raw_line in active_lines:
        instance = fields[0]
        device = register_element(instance, line_number)
        folded = instance.casefold()
        if device == "R":
            if len(fields) < 4:
                raise ReliabilityNetlistError("line %d: malformed resistor: %s" % (line_number, raw_line))
            a, b = fields[1], fields[2]
            value_token = fields[3]
            extra = fields[4:]
            value_params: dict[str, str] = {}
            if value_token.casefold().startswith("r="):
                value_token = value_token.split("=", 1)[1]
            elif "=" in value_token:
                raise ReliabilityNetlistError(
                    "line %d: resistor %s must give a numeric value or R=value, got %r" %
                    (line_number, instance, value_token)
                )
            if extra:
                value_params = _parse_assignments(extra, instance)
            mapping = {}
            native_mapping = native_resistor_by_name.get(folded)
            if native_mapping:
                mapping.update(native_mapping)
            resistor_mapping = resistor_map_by_name.get(folded)
            if resistor_mapping:
                mapping.update(resistor_mapping)
            resistance = _resistor_value(value_token, param_raw, mapping, instance)
            record: dict[str, Any] = {
                "name": instance, "a": a, "b": b, "resistance_ohm": resistance,
                "material": (mapping.get("material", "UNKNOWN") if mapping else "UNKNOWN"),
            }
            if mapping:
                geometry = {key: value for key, value in mapping.items()
                            if key not in {"name", "instance", "element", "a", "b", "resistance_ohm", "material"}}
                if geometry:
                    record["geometry"] = geometry
                record["resistor_map"] = dict(mapping)
            if value_params:
                record["parameters"] = value_params
            resistors.append(record)
            add_node(a)
            add_node(b)
        elif device == "C":
            if len(fields) < 4:
                raise ReliabilityNetlistError("line %d: malformed capacitor: %s" % (line_number, raw_line))
            a, b = fields[1], fields[2]
            value_token = fields[3]
            if "=" in value_token:
                raise ReliabilityNetlistError(
                    "line %d: capacitor %s must give capacitance as its value, got %r" %
                    (line_number, instance, value_token)
                )
            capacitance = _resolve_value(value_token, param_raw)
            if not math.isfinite(capacitance) or capacitance < 0:
                raise ReliabilityNetlistError(
                    "capacitor %s must have nonnegative finite capacitance; got %r" % (instance, capacitance)
                )
            params = _parse_assignments(fields[4:], instance) if len(fields) > 4 else {}
            record = {"name": instance, "a": a, "b": b, "capacitance_f": capacitance}
            if params:
                record["parameters"] = params
            capacitors.append(record)
            add_node(a)
            add_node(b)
        elif device == "M":
            if len(fields) < 6:
                raise ReliabilityNetlistError("line %d: malformed MOS device: %s" % (line_number, raw_line))
            if _valid_assignments(fields[6:]):
                d, g, s, e = fields[1:5]
                p = None
                model = fields[5]
                parameter_tokens = fields[6:]
            elif len(fields) >= 7 and _valid_assignments(fields[7:]):
                d, g, s, e, p = fields[1:6]
                model = fields[6]
                parameter_tokens = fields[7:]
            else:
                raise ReliabilityNetlistError(
                    "line %d: unsupported MOS terminal/parameter syntax: %s" % (line_number, raw_line)
                )
            parameters = _parse_assignments(parameter_tokens, instance)
            annotation = mos_device_by_name.get(folded)
            record = {
                "name": instance, "d": d, "g": g, "s": s, "e": e, "p": p,
                "model": model, "parameters": parameters,
                "body_state": annotation.get("body_state") if annotation else None,
                "layout_device": annotation.get("layout_device") if annotation else None,
            }
            mos.append(record)
            for node in (d, g, s, e, p):
                if node is not None:
                    add_node(node)
        elif device == "X":
            raise ReliabilityNetlistError(
                "line %d: hierarchical instance %s is unsupported; provide a flattened assembled R/C/M subcircuit"
                % (line_number, instance)
            )
        else:
            raise ReliabilityNetlistError(
                "line %d: unsupported %s element %s; reliability analysis accepts only R/C/M devices"
                % (line_number, device, instance)
            )

    # Bind exact instance-name device metadata without guessing from net order.
    for record in mos:
        mapped = mos_device_by_name.get(record["name"].casefold())
        if mapped:
            record["body_state"] = mapped.get("body_state")
            record["layout_device"] = mapped.get("layout_device")

    comments = [comment for comment in all_comments]
    # Keep provenance verbatim by category; no declaration is treated as physical evidence.
    model_comments = [text for text in comments if any(
        token in text.casefold() for token in ("model", "b4soi", "bsim")
    )]
    selfheat_comments = [text for text in comments if any(
        token in text.casefold() for token in ("selfheat", "self-heat", "self heat", "shmod")
    )]
    access_comments = [text for text in comments if any(
        token in text.casefold() for token in ("access", "rdsw", "contact", "cnt", "diffusion", "sheet")
    )]
    metadata = {
        "comments": comments,
        "selected_comments": selected_comments,
        "model_provenance": model_comments,
        "selfheat_provenance": selfheat_comments,
        "access_provenance": access_comments,
        "directives": directives,
        "parameters": dict(param_raw),
        "resolved_parameters": resolved_parameters,
    }
    return {
        "name": name,
        "ports": ports,
        "nodes": sorted(nodes.values(), key=lambda item: (item.casefold(), item)),
        "resistors": resistors,
        "capacitors": capacitors,
        "mos": mos,
        "port_maps": map_records["port_maps"],
        "mos_maps": map_records["mos_maps"],
        "resistor_maps": resistor_maps,
        "assembly_node_maps": map_records["assembly_node_maps"],
        "mos_device_maps": map_records["mos_device_maps"],
        "native_resistor_maps": map_records["native_resistor_maps"],
        "metadata": metadata,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source": str(source.resolve()),
    }


__all__ = ["ReliabilityNetlistError", "read_network"]
