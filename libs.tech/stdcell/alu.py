"""Deterministic 4-bit ALU mapped to accepted physical 1M standard cells.

COUT is zero for AND/OR operations. For addition it is the carry-out; for
subtraction it is the no-borrow indication after subtracting CIN as a borrow-in.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


PORT_DIRECTIONS = {
    **{f"A{bit}": "input" for bit in range(4)},
    **{f"B{bit}": "input" for bit in range(4)},
    "S0": "input",
    "S1": "input",
    "CIN": "input",
    **{f"Y{bit}": "output" for bit in range(4)},
    "COUT": "output",
    "VDD": "inout",
    "VSS": "inout",
}
INPUT_PORTS = tuple(name for name, direction in PORT_DIRECTIONS.items()
                    if direction == "input")
OUTPUT_PORTS = tuple(name for name, direction in PORT_DIRECTIONS.items()
                     if direction == "output")


def _mapping_module() -> Any:
    try:
        from . import mapping
    except ImportError:  # direct import from libs.tech/stdcell on sys.path
        import mapping  # type: ignore[no-redef]
    return mapping


def _accepted_physical_specs(manifest_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    try:
        from .library import physical_specs
    except ImportError:  # direct import from libs.tech/stdcell on sys.path
        from library import physical_specs  # type: ignore[no-redef]
    accepted = physical_specs(manifest_path)
    if not isinstance(accepted, dict):
        raise TypeError("library.physical_specs() must return a cell-to-spec mapping")
    return accepted


def _bit(value: Any, label: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError(f"{label} must be a single bit (0 or 1), got {value!r}")


def _word(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xF:
        raise ValueError(f"{label} must be a four-bit unsigned integer, got {value!r}")
    return value


def reference_alu(a: int, b: int, s1: int, s0: int, cin: int) -> dict[str, int]:
    """Return the scalar outputs for the ALU operation selected by ``S1S0``.

    ``00`` is bitwise AND, ``01`` is bitwise OR, ``10`` is ``A+B+CIN``,
    and ``11`` is unsigned ``A-B-CIN`` with ``CIN`` interpreted as borrow-in.
    Subtraction uses the equivalent two's-complement sum ``A + ~B + !CIN``.
    The four-bit result is truncated modulo 16. COUT is zero for logic
    operations; for arithmetic it is the carry-out, which is no-borrow for
    subtraction.
    """
    a = _word(a, "A")
    b = _word(b, "B")
    s1 = _bit(s1, "S1")
    s0 = _bit(s0, "S0")
    cin = _bit(cin, "CIN")

    operation = (s1 << 1) | s0
    if operation == 0:
        result, carry = a & b, 0
    elif operation == 1:
        result, carry = a | b, 0
    else:
        operand_b = b if operation == 2 else b ^ 0xF
        carry_in = cin if operation == 2 else 1 - cin
        total = a + operand_b + carry_in
        result, carry = total & 0xF, (total >> 4) & 1

    return {**{f"Y{bit}": (result >> bit) & 1 for bit in range(4)},
            "COUT": carry}


def evaluate_alu(inputs: Mapping[str, int]) -> dict[str, int]:
    """Evaluate the ALU from its named scalar input-port values."""
    if not isinstance(inputs, Mapping):
        raise TypeError("inputs must be a mapping of scalar port names to bits")
    expected = set(INPUT_PORTS)
    supplied = set(inputs)
    missing, extra = expected - supplied, supplied - expected
    if missing or extra:
        raise ValueError(f"input ports mismatch; missing={sorted(missing)}, extra={sorted(extra)}")

    a = sum(_bit(inputs[f"A{bit}"], f"A{bit}") << bit for bit in range(4))
    b = sum(_bit(inputs[f"B{bit}"], f"B{bit}") << bit for bit in range(4))
    outputs = reference_alu(a, b, inputs["S1"], inputs["S0"], inputs["CIN"])
    return outputs


def build_alu(name: str = "ALU4", *,
              manifest_path: str | Path | None = None) -> dict[str, Any]:
    """Build a canonical mapping-compatible 4-bit ALU netlist.

    The builder routes every Boolean expression through the existing physical
    mapper and the accepted-cell manifest. Intermediate control, inversion,
    propagate, and carry nets are shared across the four result bits.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")

    outputs: dict[str, str] = {
        "ALU_S0N": "!S0",
        "ALU_S1N": "!S1",
        "ALU_AND_EN": "(ALU_S1N & ALU_S0N)",
        "ALU_OR_EN": "(ALU_S1N & S0)",
        # CIN is carry-in for addition, but borrow-in for subtraction.
        "ALU_CARRY0": "(CIN ^ S0)",
    }
    carry = "ALU_CARRY0"
    for bit in range(4):
        inv_b = f"ALU_B{bit}N"
        b_xor = f"ALU_BX{bit}"
        propagate = f"ALU_P{bit}"
        carry_out = f"ALU_C{bit + 1}"
        sum_bit = f"ALU_SUM{bit}"
        and_bit = f"ALU_AND{bit}"
        or_bit = f"ALU_OR{bit}"

        outputs[inv_b] = f"!B{bit}"
        # B XOR S0, written using the shared complement of S0 and B.
        outputs[b_xor] = (
            f"((B{bit} & ALU_S0N) | ({inv_b} & S0))")
        outputs[propagate] = f"(A{bit} ^ {b_xor})"
        outputs[sum_bit] = f"({propagate} ^ {carry})"
        outputs[carry_out] = f"((A{bit} & {b_xor}) | ({propagate} & {carry}))"
        outputs[and_bit] = f"(A{bit} & B{bit})"
        outputs[or_bit] = f"(A{bit} | B{bit})"
        outputs[f"Y{bit}"] = (
            f"((S1 & {sum_bit}) | (ALU_OR_EN & {or_bit}) | "
            f"(ALU_AND_EN & {and_bit}))")
        carry = carry_out
    outputs["COUT"] = f"(S1 & {carry})"

    netlist = _mapping_module().map_boolean_design(
        name,
        dict(PORT_DIRECTIONS),
        outputs,
        library_view="physical_qualified",
        manifest_path=manifest_path,
    )
    validate_alu_netlist(netlist, manifest_path=manifest_path)
    return netlist


def validate_alu_netlist(netlist: Mapping[str, Any], *,
                         manifest_path: str | Path | None = None) -> bool:
    """Validate canonical ALU ports, netlist structure, and accepted cells.

    Physical eligibility is taken only from ``library.physical_specs()``,
    which returns cells with accepted ``strict_physical_pass`` manifest
    records. A successful validation returns ``True``; malformed or
    non-accepted netlists raise ``ValueError``.
    """
    if not isinstance(netlist, Mapping):
        raise TypeError("netlist must be a canonical mapping")
    if set(netlist) != {"name", "ports", "instances", "nets"}:
        raise ValueError("netlist must use the canonical name/ports/instances/nets schema")
    if not isinstance(netlist["name"], str) or not netlist["name"]:
        raise ValueError("netlist name must be a non-empty string")
    ports = netlist["ports"]
    if not isinstance(ports, Mapping) or dict(ports) != PORT_DIRECTIONS:
        raise ValueError("netlist ports must be the explicit scalar ALU interface")

    nets = netlist["nets"]
    if not isinstance(nets, list) or any(not isinstance(net, str) or not net for net in nets):
        raise ValueError("canonical nets must be a list of non-empty scalar net names")
    if len(nets) != len(set(nets)) or len({net.casefold() for net in nets}) != len(nets):
        raise ValueError("netlist contains duplicate net names")
    if nets != sorted(nets):
        raise ValueError("canonical nets must be sorted")

    instances = netlist["instances"]
    if not isinstance(instances, list):
        raise ValueError("canonical instances must be a list")
    instance_names: list[str] = []
    for instance in instances:
        if not isinstance(instance, Mapping):
            raise ValueError("each canonical instance must be a mapping")
        if set(instance) != {"name", "cell", "connections"}:
            raise ValueError("instances must use canonical name/cell/connections records")
        if not isinstance(instance["name"], str) or not instance["name"]:
            raise ValueError("instance names must be non-empty strings")
        if not isinstance(instance["cell"], str) or not instance["cell"]:
            raise ValueError("instance cell names must be non-empty strings")
        instance_names.append(instance["name"])
        if not isinstance(instance["connections"], Mapping):
            raise ValueError(f"connections for {instance['name']} must be a mapping")
    if len(instance_names) != len(set(instance_names)) or len({name.casefold() for name in instance_names}) != len(instance_names):
        raise ValueError("netlist contains duplicate instance names")

    accepted = _accepted_physical_specs(manifest_path)
    all_connections: set[str] = set(ports)
    for instance in instances:
        cell = instance["cell"]
        spec = accepted.get(cell)
        if spec is None:
            raise ValueError(f"cell {cell!r} is not in the accepted physical specs")
        connections = instance["connections"]
        pins = spec.get("pins")
        if not isinstance(pins, list) or set(connections) != set(pins):
            raise ValueError(f"{cell} instance {instance['name']} has incompatible physical pins")
        for pin, connected_net in connections.items():
            if not isinstance(connected_net, str) or not connected_net:
                raise ValueError(f"{cell}.{pin} must connect to a scalar net name")
            all_connections.add(connected_net)
    if set(nets) != all_connections:
        raise ValueError("canonical nets do not exactly cover ports and instance connections")
    return True


__all__ = [
    "INPUT_PORTS",
    "OUTPUT_PORTS",
    "PORT_DIRECTIONS",
    "build_alu",
    "evaluate_alu",
    "reference_alu",
    "validate_alu_netlist",
]
