"""Deterministic RTL benchmark sources, mapping artifacts, and reference semantics.

Run ``python libs.tech/stdcell/benchmarks.py list`` or use ``write_benchmark`` /
``map_benchmark`` from another Python module.  Mapping results are evidence only;
this module does not infer routed area, foundry qualification, or timing closure.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from . import logic, mapping
except ImportError:  # loaded with libs.tech/stdcell on sys.path or run as a script
    import logic  # type: ignore[no-redef]
    import mapping  # type: ignore[no-redef]


_FIXTURE_DIR = Path(__file__).resolve().parent / "benchmarks"

_BENCHMARKS: dict[str, dict[str, Any]] = {
    "tiny_and": {
        "top": "tiny_and",
        "kind": "combinational",
        "source": "tiny_and.v",
        "inputs": ("A", "B"),
        "outputs": ("Y",),
        "expected_outputs": {"Y": "A & B"},
        "description": "Two-input AND; an accepted physical mapper may realize it as NAND2 plus INV.",
    },
    "inv_nand2_aoi21": {
        "top": "inv_nand2_aoi21",
        "kind": "combinational",
        "source": "inv_nand2_aoi21.v",
        "inputs": ("A", "B", "C"),
        "outputs": ("INV_Y", "NAND_Y", "AOI21_Y"),
        "expected_outputs": {
            "INV_Y": "!A",
            "NAND_Y": "!(A & B)",
            "AOI21_Y": "!((A & B) | C)",
        },
        "description": "Independent inverter, NAND2, and AOI21 outputs.",
    },
    "mux4_decoder": {
        "top": "mux4_decoder",
        "kind": "combinational",
        "source": "mux4_decoder.v",
        "inputs": ("D0", "D1", "D2", "D3", "S0", "S1"),
        "outputs": ("Y", "SEL0", "SEL1", "SEL2", "SEL3"),
        "expected_outputs": {
            "SEL0": "!S1 & !S0",
            "SEL1": "!S1 & S0",
            "SEL2": "S1 & !S0",
            "SEL3": "S1 & S0",
            "Y": "D0*SEL0 | D1*SEL1 | D2*SEL2 | D3*SEL3; select index is {S1,S0}",
        },
        "description": "Four-way one-hot decoder and data mux, select index {S1,S0}.",
    },
    "counter": {
        "top": "counter",
        "kind": "sequential",
        "source": "counter.v",
        "inputs": ("CLK", "RESET"),
        "outputs": ("Q0", "Q1", "Q2", "Q3"),
        "state": ("Q0", "Q1", "Q2", "Q3"),
        "clock": "CLK",
        "expected_outputs": {"Q0": "state bit 0", "Q1": "state bit 1", "Q2": "state bit 2", "Q3": "state bit 3"},
        "transition": "On each rising edge: RESET=1 synchronously loads 0; otherwise increment modulo 16. Hold state without a rising edge.",
        "description": "Four-bit binary counter implemented with four static posedge DFFs and explicit toggle/carry equations.",
    },
    "shift_register4": {
        "top": "shift_register4",
        "kind": "sequential",
        "source": "shift_register4.v",
        "inputs": ("CLK", "DIN"),
        "outputs": ("Q0", "Q1", "Q2", "Q3"),
        "state": ("Q0", "Q1", "Q2", "Q3"),
        "clock": "CLK",
        "expected_outputs": {"Q0": "state bit 0", "Q1": "state bit 1", "Q2": "state bit 2", "Q3": "state bit 3"},
        "transition": "On each rising edge: Q0<=DIN, Q1<=old Q0, Q2<=old Q1, Q3<=old Q2. Hold state without a rising edge.",
        "description": "Four-bit static posedge shift register with explicit per-stage state.",
    },
    "alu8": {
        "top": "alu8",
        "kind": "combinational",
        "source": "alu8.v",
        "inputs": tuple([*(f"A{i}" for i in range(8)), *(f"B{i}" for i in range(8)), "S0", "S1", "CIN"]),
        "outputs": tuple([*(f"Y{i}" for i in range(8)), "COUT"]),
        "expected_outputs": {
            "Y[7:0]": "S1 ? (A + (B XOR {8{S0}}) + (CIN XOR S0))[7:0] : (S0 ? (A | B) : (A & B))",
            "COUT": "S1 ? (A + (B XOR {8{S0}}) + (CIN XOR S0))[8] : 0",
            "operations": "S1S0=00 AND; 01 OR; 10 unsigned A+B+CIN; 11 unsigned A-B-CIN with CIN as borrow-in and COUT as no-borrow.",
        },
        "description": "Eight-bit AND/OR/add/subtract ALU; no ALU4 substitution.",
    },
    "ripple_adder8": {
        "top": "ripple_adder8",
        "kind": "combinational",
        "source": "ripple_adder8.v",
        "inputs": tuple([*(f"A{i}" for i in range(8)), *(f"B{i}" for i in range(8)), "CIN"]),
        "outputs": tuple([*(f"Y{i}" for i in range(8)), "COUT"]),
        "expected_outputs": {
            "Y[7:0]": "(A + B + CIN) mod 256",
            "COUT": "floor((A + B + CIN) / 256)",
            "implementation": "Explicit bitwise propagate/generate recurrence C[i+1]=G[i]|(P[i]&C[i]), P=A XOR B, G=A&B.",
        },
        "description": "Eight-bit ripple-carry adder with explicit per-bit carry recurrence.",
    },
}


class BenchmarkError(RuntimeError):
    """Raised when a benchmark source or semantic check is invalid."""


def list_benchmarks() -> tuple[str, ...]:
    """Return benchmark names in stable CLI and artifact order."""
    return tuple(_BENCHMARKS)


def _definition(name: str) -> dict[str, Any]:
    try:
        return _BENCHMARKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark {name!r}; choose from {', '.join(_BENCHMARKS)}") from exc


def _port_directions(definition: dict[str, Any]) -> dict[str, str]:
    return {**{pin: "input" for pin in definition["inputs"]},
            **{pin: "output" for pin in definition["outputs"]}}


def _reference_artifact(name: str) -> dict[str, Any]:
    definition = _definition(name)
    record: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": name,
        "top": definition["top"],
        "kind": definition["kind"],
        "port_directions": _port_directions(definition),
        "reference_function": "evaluate_combinational(name, inputs)" if definition["kind"] == "combinational" else "evaluate_reference(name, inputs, state)",
        "expected_outputs": definition["expected_outputs"],
        "description": definition["description"],
        "evidence_scope": "independent electrical expected-output contract; not a physical qualification or measured SPICE result",
    }
    if definition["kind"] == "sequential":
        record.update({
            "state": list(definition["state"]),
            "clock": definition["clock"],
            "active_edge": "rising",
            "power_up_state": "unspecified by RTL; a test-only all-zero initial state is stated explicitly in each cycle record",
            "transition_function": "transition_reference(name, state, inputs, rising_edge=True)",
            "transition": definition["transition"],
            "expected_cycle_sequence": _expected_cycle_sequence(name),
        })
    return record


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_benchmark(name: str, out: str | Path) -> dict[str, Any]:
    """Copy one checked-in RTL fixture and its deterministic references to ``out``.

    ``out`` is a per-benchmark artifact directory. Returned paths point to both
    the source fixture and the copied RTL used by synthesis.
    """
    definition = _definition(name)
    output_dir = Path(out)
    fixture = _FIXTURE_DIR / definition["source"]
    if not fixture.is_file():
        raise BenchmarkError(f"benchmark RTL fixture is missing: {fixture}")
    rtl_dir = output_dir / "rtl"
    rtl_dir.mkdir(parents=True, exist_ok=True)
    rtl_path = rtl_dir / f"{definition['top']}.v"
    shutil.copyfile(fixture, rtl_path)
    source_bytes = fixture.read_bytes()
    source_evidence = {
        "schema_version": 1,
        "benchmark": name,
        "top": definition["top"],
        "fixture_path": str(fixture),
        "rtl_path": str(rtl_path),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "kind": definition["kind"],
        "ports": _port_directions(definition),
    }
    reference = _reference_artifact(name)
    reference_path = output_dir / "reference.json"
    source_evidence_path = output_dir / "source.json"
    _json_write(reference_path, reference)
    _json_write(source_evidence_path, source_evidence)
    return {
        "name": name,
        "top": definition["top"],
        "kind": definition["kind"],
        "rtl_path": str(rtl_path),
        "fixture_path": str(fixture),
        "reference_path": str(reference_path),
        "source_evidence_path": str(source_evidence_path),
        "ports": _port_directions(definition),
        "expected_outputs": dict(definition["expected_outputs"]),
        "reference_function": reference["reference_function"],
        **({"transition": definition["transition"], "expected_cycle_sequence": reference["expected_cycle_sequence"]}
           if definition["kind"] == "sequential" else {}),
    }


def _validated_inputs(name: str, inputs: Mapping[str, Any]) -> dict[str, int]:
    definition = _definition(name)
    if not isinstance(inputs, Mapping):
        raise TypeError("inputs must map scalar port names to bits")
    expected = set(definition["inputs"])
    supplied = set(inputs)
    if expected != supplied:
        raise ValueError(f"input ports mismatch; missing={sorted(expected - supplied)}, extra={sorted(supplied - expected)}")
    values: dict[str, int] = {}
    for pin in definition["inputs"]:
        value = inputs[pin]
        if isinstance(value, bool):
            values[pin] = int(value)
        elif isinstance(value, int) and value in (0, 1):
            values[pin] = value
        else:
            raise ValueError(f"{pin} must be a single bit (0 or 1), got {value!r}")
    return values


def _word(inputs: Mapping[str, int], prefix: str, width: int) -> int:
    return sum(inputs[f"{prefix}{bit}"] << bit for bit in range(width))


def _word_outputs(prefix: str, value: int, width: int) -> dict[str, int]:
    return {f"{prefix}{bit}": (value >> bit) & 1 for bit in range(width)}


def evaluate_combinational(name: str, inputs: Mapping[str, Any]) -> dict[str, int]:
    """Return independent Boolean/arithmetic expected outputs for a combinational benchmark."""
    definition = _definition(name)
    if definition["kind"] != "combinational":
        raise ValueError(f"{name} is sequential; use evaluate_reference with an explicit state")
    values = _validated_inputs(name, inputs)
    if name == "tiny_and":
        return {"Y": values["A"] & values["B"]}
    if name == "inv_nand2_aoi21":
        return {
            "INV_Y": 1 - values["A"],
            "NAND_Y": 1 - (values["A"] & values["B"]),
            "AOI21_Y": 1 - ((values["A"] & values["B"]) | values["C"]),
        }
    if name == "mux4_decoder":
        select = (values["S1"] << 1) | values["S0"]
        decoded = {f"SEL{index}": int(index == select) for index in range(4)}
        return {"Y": values[f"D{select}"], **decoded}
    if name == "alu8":
        a, b = _word(values, "A", 8), _word(values, "B", 8)
        s0, s1, cin = values["S0"], values["S1"], values["CIN"]
        operation = (s1 << 1) | s0
        if operation == 0:
            result, carry = a & b, 0
        elif operation == 1:
            result, carry = a | b, 0
        else:
            operand_b = b ^ (0xFF if s0 else 0)
            carry_in = cin if not s0 else 1 - cin
            total = a + operand_b + carry_in
            result, carry = total & 0xFF, (total >> 8) & 1
        return {**_word_outputs("Y", result, 8), "COUT": carry}
    if name == "ripple_adder8":
        total = _word(values, "A", 8) + _word(values, "B", 8) + values["CIN"]
        return {**_word_outputs("Y", total & 0xFF, 8), "COUT": (total >> 8) & 1}
    raise AssertionError(f"missing combinational reference for {name}")


def _validated_state(name: str, state: Mapping[str, Any]) -> dict[str, int]:
    definition = _definition(name)
    if definition["kind"] != "sequential":
        raise ValueError(f"{name} has no sequential state")
    if not isinstance(state, Mapping):
        raise TypeError("state must map state-bit names to bits")
    expected = set(definition["state"])
    supplied = set(state)
    if expected != supplied:
        raise ValueError(f"state bits mismatch; missing={sorted(expected - supplied)}, extra={sorted(supplied - expected)}")
    result: dict[str, int] = {}
    for pin in definition["state"]:
        value = state[pin]
        if isinstance(value, bool):
            result[pin] = int(value)
        elif isinstance(value, int) and value in (0, 1):
            result[pin] = value
        else:
            raise ValueError(f"{pin} state must be a single bit, got {value!r}")
    return result


def evaluate_reference(name: str, inputs: Mapping[str, Any], state: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Evaluate the independent reference outputs; sequential state is never inferred."""
    definition = _definition(name)
    if definition["kind"] == "combinational":
        if state is not None:
            raise ValueError(f"{name} is combinational and does not accept state")
        return evaluate_combinational(name, inputs)
    if state is None:
        raise ValueError(f"{name} requires an explicit state; RTL power-up state is unspecified")
    _validated_inputs(name, inputs)
    return _validated_state(name, state)


def transition_reference(name: str, state: Mapping[str, Any], inputs: Mapping[str, Any], *, rising_edge: bool) -> dict[str, int]:
    """Apply the documented positive-edge transition to a supplied prior state."""
    definition = _definition(name)
    if definition["kind"] != "sequential":
        raise ValueError(f"{name} is combinational and has no state transition")
    current = _validated_state(name, state)
    values = _validated_inputs(name, inputs)
    if not isinstance(rising_edge, bool):
        raise TypeError("rising_edge must be bool")
    if not rising_edge:
        return current
    if name == "counter":
        count = _word(current, "Q", 4)
        next_count = 0 if values["RESET"] else (count + 1) & 0xF
        return _word_outputs("Q", next_count, 4)
    if name == "shift_register4":
        return {"Q0": values["DIN"], "Q1": current["Q0"], "Q2": current["Q1"], "Q3": current["Q2"]}
    raise AssertionError(f"missing sequential transition for {name}")


def _expected_cycle_sequence(name: str) -> list[dict[str, Any]]:
    if name not in ("counter", "shift_register4"):
        return []
    state = {f"Q{bit}": 0 for bit in range(4)}
    if name == "counter":
        cycles = [{"RESET": 1}, *({"RESET": 0} for _ in range(17))]
    else:
        cycles = [{"DIN": value} for value in (1, 0, 1, 1, 0, 0)]
    records = []
    for cycle, external in enumerate(cycles):
        inputs_low = {"CLK": 0, **external}
        inputs_high = {"CLK": 1, **external}
        before = dict(state)
        state = transition_reference(name, state, inputs_high, rising_edge=True)
        records.append({
            "cycle": cycle,
            "test_initial_state_only": cycle == 0,
            "state_before": before,
            "external_inputs": dict(external),
            "clock_pin_sequence": [0, 1],
            "outputs_after_rising_edge": dict(state),
            "state_after": dict(state),
            "reference_inputs_low": inputs_low,
        })
    return records


def _output_values(netlist: dict[str, Any], values: dict[str, int]) -> dict[str, int]:
    return {port: values[port] for port, direction in netlist["ports"].items()
            if direction == "output"}


def _settle_canonical(netlist: dict[str, Any], inputs: Mapping[str, int], state: Mapping[str, Mapping[str, int]], specs: Mapping[str, dict[str, Any]]) -> dict[str, int]:
    values = {"VDD": 1, "VSS": 0, **{pin: int(bool(value)) for pin, value in inputs.items()}}
    instances = netlist.get("instances", [])
    for _ in range(len(instances) + 1):
        changed = False
        for instance in instances:
            cell = instance["cell"]
            spec = specs.get(cell)
            if spec is None:
                raise BenchmarkError(f"no existing logic evaluator specification for mapped cell {cell}")
            connections = instance["connections"]
            if spec.get("sequential"):
                seq = spec["sequential"]
                q = seq["q"]
                if q not in connections:
                    raise BenchmarkError(f"mapped storage cell {cell} has no {q} connection")
                current = state.get(instance["name"], {})
                outputs = {q: current.get(q, 0)}
            else:
                if any(pin not in connections or connections[pin] not in values for pin in spec["inputs"]):
                    continue
                pin_values = {pin: values[connections[pin]] for pin in spec["inputs"]}
                outputs = logic.evaluate(spec, pin_values)
            for pin, result in outputs.items():
                target = connections.get(pin)
                if target is None:
                    continue
                bit = int(bool(result))
                if values.get(target) != bit:
                    values[target] = bit
                    changed = True
        if not changed:
            break
    missing = [port for port, direction in netlist["ports"].items()
               if direction == "output" and port not in values]
    if missing:
        raise BenchmarkError(f"mapped outputs did not settle: {missing}")
    return values


def evaluate_mapped(netlist: dict[str, Any], inputs: Mapping[str, int]) -> dict[str, int]:
    """Evaluate a combinational canonical netlist using existing ``logic.evaluate`` cell models."""
    specs = logic.cell_specs()
    if any(specs.get(instance["cell"], {}).get("sequential") for instance in netlist.get("instances", [])):
        raise ValueError("sequential mapped netlist requires step_mapped and explicit state")
    return _output_values(netlist, _settle_canonical(netlist, inputs, {}, specs))


def _initial_mapped_state(netlist: dict[str, Any], *, q_value: int = 0) -> dict[str, dict[str, int]]:
    state: dict[str, dict[str, int]] = {}
    for instance in netlist.get("instances", []):
        spec = logic.cell_specs().get(instance["cell"])
        if spec and spec.get("sequential"):
            seq = spec["sequential"]
            state[instance["name"]] = {
                seq["q"]: q_value,
                f"__prev_{seq['clock']}": 0,
            }
    return state


def step_mapped(netlist: dict[str, Any], inputs: Mapping[str, int], state: Mapping[str, Mapping[str, int]]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Evaluate one input sample and update DFF state via ``logic.evaluate`` edge semantics."""
    specs = logic.cell_specs()
    pre_edge = _settle_canonical(netlist, inputs, state, specs)
    next_state = {name: dict(values) for name, values in state.items()}
    for instance in netlist.get("instances", []):
        spec = specs.get(instance["cell"])
        if not spec or not spec.get("sequential"):
            continue
        seq = spec["sequential"]
        connections = instance["connections"]
        pin_values = {pin: pre_edge[connections[pin]] for pin in spec["inputs"]}
        current = dict(state.get(instance["name"], {}))
        current.setdefault(seq["q"], 0)
        current.setdefault(f"__prev_{seq['clock']}", 0)
        result = logic.evaluate(spec, pin_values, current)
        current[seq["q"]] = result[seq["q"]]
        current[f"__prev_{seq['clock']}"] = pin_values[seq["clock"]]
        next_state[instance["name"]] = current
    post_edge = _settle_canonical(netlist, inputs, next_state, specs)
    return _output_values(netlist, post_edge), next_state


def _combinational_vectors(name: str) -> list[dict[str, int]]:
    definition = _definition(name)
    if name == "tiny_and":
        return [dict(zip(definition["inputs"], vector)) for vector in itertools.product((0, 1), repeat=2)]
    if name == "inv_nand2_aoi21":
        return [dict(zip(definition["inputs"], vector)) for vector in itertools.product((0, 1), repeat=3)]
    if name == "mux4_decoder":
        return [dict(zip(definition["inputs"], vector)) for vector in itertools.product((0, 1), repeat=6)]
    if name == "alu8":
        edge_words = (0, 1, 2, 0x7F, 0x80, 0xFE, 0xFF, 0x55, 0xAA)
        vectors: list[dict[str, int]] = []
        for a, b, s1, s0, cin in itertools.product(edge_words, edge_words, (0, 1), (0, 1), (0, 1)):
            vectors.append({**_word_outputs("A", a, 8), **_word_outputs("B", b, 8), "S0": s0, "S1": s1, "CIN": cin})
        rng = random.Random(0xA18)
        for _ in range(256):
            a, b = rng.randrange(256), rng.randrange(256)
            vectors.append({**_word_outputs("A", a, 8), **_word_outputs("B", b, 8), "S0": rng.randrange(2), "S1": rng.randrange(2), "CIN": rng.randrange(2)})
        return vectors
    if name == "ripple_adder8":
        words = (0, 1, 2, 3, 0x7F, 0x80, 0xFE, 0xFF, 0x55, 0xAA)
        vectors = [
            {**_word_outputs("A", a, 8), **_word_outputs("B", b, 8), "CIN": cin}
            for a, b, cin in itertools.product(words, words, (0, 1))
        ]
        rng = random.Random(0xADD8)
        for _ in range(256):
            vectors.append({**_word_outputs("A", rng.randrange(256), 8),
                            **_word_outputs("B", rng.randrange(256), 8), "CIN": rng.randrange(2)})
        return vectors
    raise AssertionError(f"missing test vectors for {name}")


def check_mapped_semantics(name: str, netlist: dict[str, Any]) -> dict[str, Any]:
    """Compare mapped cells against the independent reference on fixed test vectors."""
    definition = _definition(name)
    if definition["kind"] == "combinational":
        vectors = _combinational_vectors(name)
        for index, inputs in enumerate(vectors):
            expected = evaluate_combinational(name, inputs)
            observed = evaluate_mapped(netlist, inputs)
            if observed != expected:
                raise BenchmarkError(f"{name} mapped semantics mismatch at vector {index}: inputs={inputs}, expected={expected}, observed={observed}")
        return {
            "status": "passed",
            "method": "existing logic.evaluate cell models over canonical mapped netlist",
            "reference": "independent evaluate_combinational implementation",
            "vectors_checked": len(vectors),
        }

    state = {pin: 0 for pin in definition["state"]}
    mapped_state = _initial_mapped_state(netlist, q_value=0)
    prior_clock = 0
    samples = 0
    cycles = _expected_cycle_sequence(name)
    for record in cycles:
        external = record["external_inputs"]
        for clock in record["clock_pin_sequence"]:
            inputs = {"CLK": clock, **external}
            mapped_outputs, mapped_state = step_mapped(netlist, inputs, mapped_state)
            rising_edge = prior_clock == 0 and clock == 1
            state = transition_reference(name, state, inputs, rising_edge=rising_edge)
            expected = evaluate_reference(name, inputs, state)
            if mapped_outputs != expected:
                raise BenchmarkError(f"{name} mapped state mismatch at sample {samples}: inputs={inputs}, expected={expected}, observed={mapped_outputs}")
            prior_clock = clock
            samples += 1
    return {
        "status": "passed",
        "method": "existing logic.evaluate cell models over canonical mapped netlist with explicit clock-level samples",
        "reference": "independent evaluate_reference and transition_reference implementations",
        "initial_state": {pin: 0 for pin in definition["state"]},
        "initial_state_scope": "verification sequence only; not an RTL power-up guarantee",
        "cycles_checked": len(cycles),
        "samples_checked": samples,
    }


def _is_unavailable_cell_error(exc: ValueError) -> bool:
    text = str(exc)
    return ("is not eligible in the physical_qualified view" in text
            or "is not eligible in the physical_qualified" in text)


def map_benchmark(name: str, out: str | Path, manifest_path: str | Path | None = None, *, library_view: str = "logical_reference") -> dict[str, Any]:
    """Map and independently check logical and accepted-physical benchmark views.

    The accepted-physical path uses combinational Yosys/ABC Genlib area mapping
    from accepted bounding-box costs. Sequential cells stay outside ABC.
    """
    if library_view not in ("logical_reference", "accepted_physical"):
        raise ValueError("library_view must be 'logical_reference' or 'accepted_physical'")
    output_dir = Path(out)
    source = write_benchmark(name, output_dir)
    raw_dir = output_dir / "yosys"
    logical_workdir = raw_dir / "logical_reference"
    logical_netlist = mapping.synthesize_rtl(
        source["rtl_path"], top=source["top"], library_view="logical_reference",
        workdir=logical_workdir,
    )
    raw_yosys_json = logical_workdir / "mapped.json"
    logical_path = output_dir / "mapped.logical_reference.json"
    mapping.export_netlist(logical_netlist, logical_path)
    semantic = check_mapped_semantics(name, logical_netlist)
    semantic_path = output_dir / "semantic_check.json"
    _json_write(semantic_path, semantic)

    result: dict[str, Any] = {
        "benchmark": name,
        "top": source["top"],
        "requested_library_view": library_view,
        "status": "mapped",
        "mapped_path": str(logical_path) if library_view == "logical_reference" else None,
        "canonical": logical_netlist if library_view == "logical_reference" else None,
        "logical_mapped_path": str(logical_path),
        "logical_canonical": logical_netlist,
        "raw_yosys_json_path": str(raw_yosys_json),
        "semantic_check_path": str(semantic_path),
        "semantic_check": semantic,
        "source_evidence": {
            "fixture_path": source["fixture_path"],
            "rtl_path": source["rtl_path"],
            "source_evidence_path": source["source_evidence_path"],
            "reference_path": source["reference_path"],
        },
        "physical_claims": {"routed_area": None, "timing_feasibility": None, "qualification": None},
    }
    if library_view == "accepted_physical":
        physical_path = output_dir / "mapped.accepted_physical.json"
        physical_workdir = raw_dir / "accepted_physical_area"
        physical_raw_json = physical_workdir / "mapped.json"
        genlib_path = physical_workdir / "accepted_area.genlib"
        try:
            physical_netlist = mapping.synthesize_rtl(
                source["rtl_path"], top=source["top"],
                library_view="physical_qualified", manifest_path=manifest_path,
                workdir=physical_workdir, area_only_abc=True,
            )
        except ValueError as exc:
            if not _is_unavailable_cell_error(exc):
                raise
            result.update({
                "status": "unavailable_cell",
                "mapped_path": None,
                "canonical": None,
                "limitation": str(exc),
                "manifest_path": str(manifest_path) if manifest_path is not None else None,
                "physical_mapping": {
                    "method": "yosys_abc_genlib_area_only",
                    "genlib_path": str(genlib_path),
                    "raw_yosys_json_path": str(physical_raw_json),
                    "area_cost_basis": "accepted physical bounding-box area_um2",
                    "timing_model": None,
                },
            })
        else:
            mapping.export_netlist(physical_netlist, physical_path)
            physical_semantic = check_mapped_semantics(name, physical_netlist)
            physical_semantic_path = output_dir / "semantic_check.accepted_physical.json"
            _json_write(physical_semantic_path, physical_semantic)
            cell_counts: dict[str, int] = {}
            for instance in physical_netlist["instances"]:
                cell = str(instance["cell"])
                cell_counts[cell] = cell_counts.get(cell, 0) + 1
            physical_area = mapping.area(
                physical_netlist, manifest_path=manifest_path)
            result.update({
                "mapped_path": str(physical_path),
                "canonical": physical_netlist,
                "manifest_path": str(manifest_path) if manifest_path is not None else None,
                "physical_semantic_check_path": str(physical_semantic_path),
                "physical_semantic_check": physical_semantic,
                "physical_mapping": {
                    "method": "yosys_abc_genlib_area_only",
                    "genlib_path": str(genlib_path),
                    "raw_yosys_json_path": str(physical_raw_json),
                    "area_cost_basis": "accepted physical bounding-box area_um2",
                    "cell_area_proxy_um2": physical_area,
                    "cell_counts": dict(sorted(cell_counts.items())),
                    "abc_script": "strash; map -a",
                    "genlib_pin_timing_fields": "zero placeholders; not electrical data",
                    "timing_model": None,
                },
            })
    evidence_path = output_dir / "mapping_evidence.json"
    summary = {key: value for key, value in result.items()
               if key not in ("canonical", "logical_canonical")}
    summary["canonical_path"] = result["mapped_path"]
    summary["logical_canonical_path"] = result["logical_mapped_path"]
    _json_write(evidence_path, summary)
    result["mapping_evidence_path"] = str(evidence_path)
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list deterministic benchmark names")

    write_parser = subparsers.add_parser("write", help="write one RTL fixture and its references")
    write_parser.add_argument("name", choices=list_benchmarks())
    write_parser.add_argument("--out", required=True)

    map_parser = subparsers.add_parser("map", help="run Yosys, mapping, and independent semantic checks")
    map_parser.add_argument("name", choices=list_benchmarks())
    map_parser.add_argument("--out", required=True)
    map_parser.add_argument("--manifest")
    map_parser.add_argument("--library-view", choices=("logical_reference", "accepted_physical"), default="logical_reference")

    all_parser = subparsers.add_parser("all", help="map all benchmarks under an output root")
    all_parser.add_argument("--out", required=True)
    all_parser.add_argument("--manifest")
    all_parser.add_argument("--library-view", choices=("logical_reference", "accepted_physical"), default="logical_reference")

    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(list_benchmarks(), indent=2))
        return 0
    if args.command == "write":
        print(json.dumps(write_benchmark(args.name, args.out), indent=2, sort_keys=True))
        return 0
    if args.command == "map":
        result = map_benchmark(args.name, args.out, args.manifest, library_view=args.library_view)
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("canonical", "logical_canonical")}, indent=2, sort_keys=True))
        return 0 if result["status"] == "mapped" else 2
    results = []
    for name in list_benchmarks():
        result = map_benchmark(name, Path(args.out) / name, args.manifest, library_view=args.library_view)
        results.append({key: result.get(key) for key in (
            "benchmark", "status", "mapped_path", "limitation",
            "semantic_check_path", "physical_semantic_check_path",
            "physical_mapping")})
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(result["status"] == "mapped" for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(_main())
