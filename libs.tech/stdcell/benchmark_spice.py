"""Nominal-model postlayout electrical checks for routed RTL benchmarks.

Large combinational designs use bounded, source-driven vector coverage. Stateful
benchmarks are exercised with actual clock/data waveforms and no forced state.
These checks are functional feasibility evidence, not timing or process
qualification.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

try:
    from . import benchmarks, characterize
    from .verify import sha256
except ImportError:  # loaded with libs.tech/stdcell on sys.path
    import benchmarks  # type: ignore[no-redef]
    import characterize  # type: ignore[no-redef]
    from verify import sha256  # type: ignore[no-redef]


VDD = characterize.VDD
COMBINATIONAL_SLOT_NS = 2000.0
COMBINATIONAL_SAMPLE_NS = 1500.0
INPUT_SLEW_NS = 2.0
# Match the independently exercised leaf-storage slew. Application closure is
# checked separately; no clock-frequency or arbitrary-slew qualification follows.
STATE_INPUT_SLEW_NS = 2.0
STATE_PERIOD_NS = 2000.0
STATE_HALF_PERIOD_NS = 1000.0
STATE_SAMPLE_NS = 500.0
STATE_LOAD_FF = 10.0
STATE_HOLD_TIMES_US = (10.0, 1000.0)


class BenchmarkCharacterizationError(characterize.CharacterizationError):
    """Raised when routed benchmark electrical behavior fails its contract."""


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _provenance(name: str, spec: dict[str, Any], postlayout: str | Path) -> dict[str, Any]:
    definition = benchmarks._definition(name)
    source = Path(benchmarks.__file__).resolve().parent / "benchmarks" / definition["source"]
    return {
        "benchmark": name,
        "top_cell": spec["name"],
        "source": {"path": str(source), "sha256": sha256(source)},
        "reference_implementation": {
            "path": str(Path(benchmarks.__file__).resolve()),
            "sha256": sha256(Path(benchmarks.__file__)),
        },
        "postlayout": {"path": str(Path(postlayout).resolve()), "sha256": sha256(Path(postlayout))},
        "assembly_formal_pin_order": list(spec["pins"]),
        "model_library": {"path": str(characterize.MODELS.resolve()), "sha256": sha256(characterize.MODELS)},
        "model_card": {"path": str(characterize.MODEL_CARD.resolve()), "sha256": sha256(characterize.MODEL_CARD)},
        "characterizer": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "nominal_conditions": {"voltage_v": VDD, "temperature_c": 25},
        "qualification_scope": "nominal electrical results for supplied SPICE; routing provenance is separate; no frame/IO/process qualification",
    }


def _validate_spec(name: str, spec: dict[str, Any]) -> None:
    definition = benchmarks._definition(name)
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
        raise BenchmarkCharacterizationError("benchmark spec requires a SPICE top-cell name")
    pins = spec.get("pins")
    inputs, outputs = spec.get("inputs"), spec.get("outputs")
    if not isinstance(pins, list) or not isinstance(inputs, list) or not isinstance(outputs, list):
        raise BenchmarkCharacterizationError("benchmark spec requires formal pins, inputs, and outputs")
    if len(pins) != len(set(pin.casefold() for pin in pins)):
        raise BenchmarkCharacterizationError("assembly formal pin order contains duplicate pins")
    if any(pin not in pins for pin in (*inputs, *outputs, "VDD", "VSS")):
        raise BenchmarkCharacterizationError("assembly formal pins do not include benchmark ports and supplies")
    if set(inputs) != set(definition["inputs"]):
        raise BenchmarkCharacterizationError("benchmark input pins do not match the independent reference")
    if set(outputs) != set(definition["outputs"]):
        raise BenchmarkCharacterizationError("benchmark output pins do not match the independent reference")


def _waveform(pin: str, events: list[tuple[float, int]]) -> str:
    return "V%s %s 0 PWL(%s)" % (
        pin, pin, " ".join("%.12gn %.12g" % (time, bit * VDD) for time, bit in events))


def _input_events(vectors: list[dict[str, int]], pin: str,
                  slot_ns: float) -> list[tuple[float, int]]:
    events: list[tuple[float, int]] = [(0.0, int(vectors[0][pin]))]
    previous = int(vectors[0][pin])
    for index, vector in enumerate(vectors[1:], 1):
        value = int(vector[pin])
        if value != previous:
            boundary = index * slot_ns
            events.extend(((boundary - INPUT_SLEW_NS, previous), (boundary, value)))
            previous = value
    return events


def _clock_events(rises: list[float], *, falling_after: list[bool]) -> list[tuple[float, int]]:
    events: list[tuple[float, int]] = [(0.0, 0)]
    for rise, has_fall in zip(rises, falling_after):
        events.extend(((rise - STATE_INPUT_SLEW_NS, 0), (rise, 1)))
        if has_fall:
            fall = rise + STATE_HALF_PERIOD_NS
            events.extend(((fall - STATE_INPUT_SLEW_NS, 1), (fall, 0)))
    return events


def _edge_input_events(values: list[int], rises: list[float]) -> list[tuple[float, int]]:
    events: list[tuple[float, int]] = [(0.0, int(values[0]))]
    previous = int(values[0])
    for value, rise in zip(values[1:], rises[1:]):
        value = int(value)
        if value != previous:
            stable_at = rise - STATE_SAMPLE_NS
            events.extend(((stable_at - STATE_INPUT_SLEW_NS, previous), (stable_at, value)))
            previous = value
    return events


def _require_measurement(values: dict[str, float], name: str, context: str) -> float:
    voltage = values.get(name.casefold())
    if voltage is None or not math.isfinite(voltage):
        raise BenchmarkCharacterizationError("missing/nonfinite %s measurement %s" % (context, name))
    return voltage


def _check_logic_level(pin: str, voltage: float, expected: int, context: str) -> None:
    passed = voltage >= 0.9 * VDD if expected else voltage <= 0.1 * VDD
    if not passed:
        raise BenchmarkCharacterizationError(
            "%s: %s=%.9g V expected %d" % (context, pin, voltage, expected))


def _write_result(output_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    _write_json(output_dir / "characterization.json", result)
    return result


def _characterize_exhaustive(name: str, spec: dict[str, Any], postlayout: str | Path,
                             output_dir: Path, evaluate: Callable[..., Any]) -> dict[str, Any]:
    # The benchmark reference is intentionally separate from the mapped-cell evaluator.
    def reference(_spec: dict[str, Any], inputs: dict[str, int]) -> dict[str, int]:
        expected = benchmarks.evaluate_combinational(name, inputs)
        if set(expected) != set(spec["outputs"]):
            raise BenchmarkCharacterizationError("reference outputs do not match the routed top")
        return expected

    result = characterize.characterize_cell(spec, postlayout, output_dir, reference)
    result.update(_provenance(name, spec, postlayout))
    result.update({
        "benchmark": name,
        "truth_table_scope": "exhaustive over all %d input combinations" % (1 << len(spec["inputs"])),
        "timing_scope": "existing sensitized-cell arcs only; no benchmark clock period or fMAX claim",
    })
    return result


def _characterize_bounded_combinational(name: str, spec: dict[str, Any], postlayout: str | Path,
                                        output_dir: Path) -> dict[str, Any]:
    vectors = benchmarks._combinational_vectors(name)
    if not vectors:
        raise BenchmarkCharacterizationError("bounded benchmark vector set is empty")
    if any(set(vector) != set(spec["inputs"]) for vector in vectors):
        raise BenchmarkCharacterizationError("bounded vectors do not match assembly input pins")

    deck = characterize._header(spec, postlayout)
    deck.extend(_waveform(pin, _input_events(vectors, pin, COMBINATIONAL_SLOT_NS))
                for pin in spec["inputs"])
    deck.extend("CLOAD_%s %s 0 %.12gf" % (pin, pin, 5.0) for pin in spec["outputs"])
    saved = " ".join([*("v(%s)" % pin for pin in spec["outputs"]), "i(VPOWER)"])
    deck += [".save %s" % saved]
    deck += [".tran 5n %.12gn 0 50n" % (len(vectors) * COMBINATIONAL_SLOT_NS)]

    measurements: list[tuple[str, int, str]] = []
    for index, inputs in enumerate(vectors):
        sample_time = index * COMBINATIONAL_SLOT_NS + COMBINATIONAL_SAMPLE_NS
        for output_index, pin in enumerate(spec["outputs"]):
            measure = "v%04d_%02d" % (index, output_index)
            deck.append(".measure tran %s FIND v(%s) AT=%.12gn" % (measure, pin, sample_time))
            measurements.append((measure, index, pin))
        deck.append(".measure tran i%04d FIND i(VPOWER) AT=%.12gn" % (index, sample_time))

    deck += [".end"]
    text = characterize._run(deck, output_dir)
    values = characterize._measurements(text)
    records: list[dict[str, Any]] = []
    result_by_index: dict[int, dict[str, float]] = {}
    for measure, index, pin in measurements:
        result_by_index.setdefault(index, {})[pin] = _require_measurement(values, measure, "output")
    for index, inputs in enumerate(vectors):
        expected = benchmarks.evaluate_combinational(name, inputs)
        observed = result_by_index.get(index, {})
        if set(observed) != set(spec["outputs"]) or set(expected) != set(spec["outputs"]):
            raise BenchmarkCharacterizationError("missing or mismatched output vector %d" % index)
        for pin in spec["outputs"]:
            _check_logic_level(pin, observed[pin], expected[pin], "%s vector %d" % (name, index))
        current = _require_measurement(values, "i%04d" % index, "supply-current")
        records.append({"index": index, "inputs": dict(inputs), "expected": expected,
                        "voltage_v": observed, "supply_current_a": current, "passed": True})

    result = {
        "name": spec["name"], "benchmark": name, "kind": "combinational",
        "logic_levels_passed": True, "passed": True,
        "method": "source-driven transient vectors on the complete extracted top",
        "coverage": {
            "scope": "bounded deterministic vectors; not exhaustive truth, arcs, timing, or fMAX",
            "vectors_checked": len(vectors), "input_count": len(spec["inputs"]),
            "output_count": len(spec["outputs"]), "exhaustive": False,
            "vector_source": "benchmarks._combinational_vectors",
        },
        "vectors": records,
        "load_ff_per_output": 5.0, "input_slew_ns": INPUT_SLEW_NS,
        "settling_sample_ns": COMBINATIONAL_SAMPLE_NS,
        "vector_slot_ns": COMBINATIONAL_SLOT_NS,
        "timing_objective": "feasibility_only",
        "timing_scope": "settled logic levels only; no fabricated worst delay or fMAX",
        "integration_method": "trap",
        **_provenance(name, spec, postlayout),
    }
    return result


def _schedule_pulse_samples(name: str, rises: list[float], external: dict[str, list[int]],
                            outputs: list[str]) -> tuple[list[str], dict[str, tuple[int | None, str]]]:
    """Build rise/fall measures and independently predicted states."""
    measures: list[str] = []
    expected: dict[str, tuple[int | None, str]] = {}
    state = {pin: 0 for pin in outputs}
    previous_outputs: dict[str, int | None] = {pin: None for pin in outputs}
    flush_count = 4 if name == "shift_register4" else 0
    sample_index = 0
    for cycle, rise in enumerate(rises):
        values = {pin: external[pin][cycle] for pin in external}
        if name == "counter":
            state = benchmarks.transition_reference(name, state, {"CLK": 1, **values}, rising_edge=True)
        elif cycle >= flush_count:
            if cycle == flush_count:
                state = {pin: 0 for pin in outputs}
            state = benchmarks.transition_reference(name, state, {"CLK": 1, **values}, rising_edge=True)
        else:
            known = min(cycle + 1, 4)
            previous_outputs = {pin: (0 if int(pin[1:]) < known else None) for pin in outputs}

        expected_after_rise = state if name == "counter" or cycle >= flush_count else previous_outputs
        for phase, at in (("rise", rise + STATE_SAMPLE_NS),
                          ("fall", rise + STATE_HALF_PERIOD_NS + STATE_SAMPLE_NS)):
            if phase == "fall":
                if name == "counter" or cycle >= flush_count:
                    state = benchmarks.transition_reference(
                        name, state, {"CLK": 0, **values}, rising_edge=False)
                    expected_state: dict[str, int | None] = state
                else:
                    expected_state = previous_outputs
            else:
                expected_state = expected_after_rise
            for output_index, pin in enumerate(outputs):
                measure = "%s_%03d_%s" % (phase, sample_index, pin.casefold())
                measures.append(".measure tran %s FIND v(%s) AT=%.12gn" % (measure, pin, at))
                expected[measure] = (expected_state[pin], "%s cycle %d %s" % (name, cycle, phase))
            sample_index += 1
    return measures, expected


def _verify_state_samples(text: str, expected: dict[str, tuple[int | None, str]],
                          outputs: list[str]) -> list[dict[str, Any]]:
    values = characterize._measurements(text)
    records: list[dict[str, Any]] = []
    for measure, (bit, context) in expected.items():
        voltage = _require_measurement(values, measure, "state output")
        pin = measure.rsplit("_", 1)[-1].upper()
        if bit is not None:
            _check_logic_level(pin, voltage, bit, context)
        records.append({"measurement": measure, "output": pin, "expected": bit,
                        "voltage_v": voltage, "checked": bit is not None,
                        "passed": True if bit is not None else None})
    return records




def _sequential_functional(name: str, spec: dict[str, Any], postlayout: str | Path,
                           output_dir: Path) -> dict[str, Any]:
    if name == "counter":
        reset_values = [1] + [0] * 17
        cycle_inputs = {"RESET": reset_values}
    else:
        cycle_inputs = {"DIN": [0] * 4 + [1, 0, 1, 1, 0, 0]}
    rises = [STATE_HALF_PERIOD_NS + cycle * STATE_PERIOD_NS
             for cycle in range(len(next(iter(cycle_inputs.values()))))]
    clock_events = _clock_events(rises, falling_after=[True] * len(rises))
    sources = {"CLK": clock_events}
    sources.update({pin: _edge_input_events(values, rises) for pin, values in cycle_inputs.items()})

    deck = characterize._header(spec, postlayout, integration_method="gear")
    deck.extend(_waveform(pin, sources[pin]) for pin in [*spec["inputs"]])
    deck.extend("CLOAD_%s %s 0 %.12gf" % (pin, pin, STATE_LOAD_FF) for pin in spec["outputs"])
    saved = " ".join([*("v(%s)" % pin for pin in spec["outputs"]), "i(VPOWER)"])
    deck += [".save %s" % saved]
    measures, expected = _schedule_pulse_samples(name, rises, cycle_inputs, spec["outputs"])
    final_time = rises[-1] + STATE_HALF_PERIOD_NS + STATE_SAMPLE_NS + 100.0
    deck += [".tran 5n %.12gn 0 5n" % final_time, *measures, ".end"]
    text = characterize._run(deck, output_dir / "functional")
    samples = _verify_state_samples(text, expected, spec["outputs"])
    return {
        "cycles_checked": len(rises),
        "samples_checked": sum(sample["checked"] for sample in samples),
        "samples": samples,
        "input_sequence": {pin: list(values) for pin, values in cycle_inputs.items()},
        "period_ns": STATE_PERIOD_NS,
        "high_low_edge_spacing_ns": STATE_HALF_PERIOD_NS,
        "clock_slew_ns": STATE_INPUT_SLEW_NS,
        "integration_method": "gear",
    }


def _retention_experiment(name: str, spec: dict[str, Any], postlayout: str | Path,
                          output_dir: Path, stored_bit: int, stopped_clock: int) -> dict[str, Any]:
    if name == "counter":
        reset_values = [1] + ([0] * 15 if stored_bit else [])
        cycle_inputs = {"RESET": reset_values}
    else:
        cycle_inputs = {"DIN": [stored_bit] * 4}
    rises = [STATE_HALF_PERIOD_NS + cycle * STATE_PERIOD_NS
             for cycle in range(len(next(iter(cycle_inputs.values()))))]
    falling_after = [True] * len(rises)
    if stopped_clock == 1:
        falling_after[-1] = False
    clock_events = _clock_events(rises, falling_after=falling_after)
    sources = {pin: _edge_input_events(values, rises) for pin, values in cycle_inputs.items()}

    last_transition_time = rises[-1] + (STATE_HALF_PERIOD_NS if stopped_clock == 0 else 0.0)
    hold_start = last_transition_time + STATE_SAMPLE_NS
    hold_end_1000 = hold_start + STATE_HOLD_TIMES_US[1] * 1000.0
    hold_pin = "RESET" if name == "counter" else "DIN"
    toggle_events = list(sources[hold_pin])
    value = cycle_inputs[hold_pin][-1]
    toggle_times = [hold_start + offset for offset in (1000.0, 3000.0, 5000.0)]
    for toggle_time in toggle_times:
        value = 1 - value
        toggle_events.extend(((toggle_time - STATE_INPUT_SLEW_NS, 1 - value), (toggle_time, value)))
    sources[hold_pin] = toggle_events

    deck = characterize._header(spec, postlayout, integration_method="gear")
    deck.append(_waveform("CLK", clock_events))
    deck.extend(_waveform(pin, sources[pin]) for pin in cycle_inputs)
    deck.extend("CLOAD_%s %s 0 %.12gf" % (pin, pin, STATE_LOAD_FF) for pin in spec["outputs"])
    saved = " ".join([*("v(%s)" % pin for pin in spec["outputs"]), "i(VPOWER)"])
    deck.append(".save %s" % saved)

    final_measurements: dict[str, tuple[str, int]] = {}
    for index, pin in enumerate(spec["outputs"]):
        measure = "setup_q%d" % index
        deck.append(".measure tran %s FIND v(%s) AT=%.12gn" % (measure, pin, hold_start))
        final_measurements[measure] = (pin, stored_bit)
    windows: dict[str, dict[str, tuple[str, str]]] = {}
    for hold_us in STATE_HOLD_TIMES_US:
        key = "%gus" % hold_us
        window_end = hold_start + hold_us * 1000.0
        names: dict[str, tuple[str, str]] = {}
        for index, pin in enumerate(spec["outputs"]):
            low_name, high_name = "q%d_min_%s" % (index, key), "q%d_max_%s" % (index, key)
            deck.append(".measure tran %s MIN v(%s) FROM=%.12gn TO=%.12gn" %
                        (low_name, pin, hold_start, window_end))
            deck.append(".measure tran %s MAX v(%s) FROM=%.12gn TO=%.12gn" %
                        (high_name, pin, hold_start, window_end))
            names[pin] = (low_name, high_name)
        windows[key] = names
    deck.append(".measure tran hold_current FIND i(VPOWER) AT=%.12gn" % (hold_end_1000 - 1.0))
    # Sources have explicit edge breakpoints; after the last disturbance the
    # circuit is static. Use the leaf-retention ceiling rather than forcing
    # 10,000 extra Newton solves during the 1 ms stopped-clock interval.
    deck += [".tran 20n %.12gn 0 10000n" % (hold_end_1000 + 100.0), ".end"]

    folder = output_dir / ("retain_%s_clk%d" % ("ones" if stored_bit else "zeros", stopped_clock))
    text = characterize._run(deck, folder)
    values = characterize._measurements(text)
    setup_samples = []
    for measure, (pin, expected) in final_measurements.items():
        voltage = _require_measurement(values, measure, "retention setup")
        _check_logic_level(pin, voltage, expected, "retention setup stored_bit=%d" % stored_bit)
        setup_samples.append({"output": pin, "expected": expected, "voltage_v": voltage})

    hold_records = []
    for hold_us in STATE_HOLD_TIMES_US:
        key = "%gus" % hold_us
        outputs: dict[str, dict[str, float]] = {}
        for pin, names in windows[key].items():
            minimum = _require_measurement(values, names[0], "retention minimum")
            maximum = _require_measurement(values, names[1], "retention maximum")
            expected = stored_bit
            passed = minimum >= 0.9 * VDD if expected else maximum <= 0.1 * VDD
            if not passed:
                raise BenchmarkCharacterizationError(
                    "static retention failed: %s %s clk=%d %s min=%.9g max=%.9g V" %
                    (name, hold_us, stopped_clock, pin, minimum, maximum))
            outputs[pin] = {"minimum_v": minimum, "maximum_v": maximum,
                            "expected_bit": expected, "passed": True}
        hold_records.append({"hold_time_us": hold_us, "outputs": outputs, "passed": True})
    supply_current = _require_measurement(values, "hold_current", "retention supply-current")
    return {
        "stored_word": format(sum(stored_bit << bit for bit in range(4)), "04b"),
        "stored_bit": stored_bit, "stopped_clock_level": stopped_clock,
        "hold_start_ns": hold_start, "hold_times_tested_us": list(STATE_HOLD_TIMES_US),
        "clock_stopped": True, "external_control_toggled_while_stopped": True,
        "external_control_pin": hold_pin, "external_control_toggle_times_ns": toggle_times,
        "setup_state_outputs": setup_samples, "hold_windows": hold_records,
        "supply_current_a": supply_current,
        "integration_method": "gear",
        "passed": True,
    }


def _characterize_sequential(name: str, spec: dict[str, Any], postlayout: str | Path,
                             output_dir: Path) -> dict[str, Any]:
    functional = _sequential_functional(name, spec, postlayout, output_dir)
    retention = [
        _retention_experiment(name, spec, postlayout, output_dir, stored_bit, stopped_clock)
        for stored_bit in (0, 1) for stopped_clock in (0, 1)
    ]
    passed = all(case["passed"] for case in retention)
    if not passed:
        raise BenchmarkCharacterizationError("one or more static retention cases failed")
    result = {
        "name": spec["name"], "benchmark": name, "kind": "sequential",
        "logic_levels_passed": True, "passed": True,
        "functional_sequence": functional,
        "retention": retention,
        "preservation_correctness": True, "hold_correctness": True,
        "pulse_correctness": True,
        "clock_period_ns": STATE_PERIOD_NS,
        "tested_clock_high_edge_spacing_ns": STATE_HALF_PERIOD_NS,
        "tested_clock_low_edge_spacing_ns": STATE_HALF_PERIOD_NS,
        "tested_input_setup_ns": STATE_SAMPLE_NS - STATE_INPUT_SLEW_NS / 2,
        "tested_input_hold_after_rising_edge_ns": STATE_PERIOD_NS - STATE_SAMPLE_NS - STATE_INPUT_SLEW_NS / 2,
        "tested_input_transition_slew_ns": STATE_INPUT_SLEW_NS,
        "tested_hold_times_us": list(STATE_HOLD_TIMES_US),
        "tested_static_stored_words": ["0000", "1111"],
        "tested_stopped_clock_levels": [0, 1],
        "timing_scope": (
            "functional correctness only at the stated 2000ns period; exact tested "
            "setup/hold/slew and 1000ns high/low edge spacing are reported, not minimum bounds; "
            "no fMAX or minimum aperture claim"),
        "voltage_v": VDD, "temperature_c": 25, "load_ff_per_output": STATE_LOAD_FF,
        "input_slew_ns": STATE_INPUT_SLEW_NS, "integration_method": "gear",
        "timing_objective": "feasibility_only",
        **_provenance(name, spec, postlayout),
    }
    return result


def characterize_benchmark(name: str, spec: dict[str, Any], postlayout: str | Path,
                           output_dir: str | Path, evaluate: Callable[..., Any]) -> dict[str, Any]:
    """Characterize a routed benchmark top and write ``characterization.json``.

    ``spec['pins']`` is the assembly's formal pin order (excluding the separately
    connected E_SUB terminal), and is passed unchanged to ``characterize._header``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _validate_spec(name, spec)
        definition = benchmarks._definition(name)
        if definition["kind"] == "sequential":
            result = _characterize_sequential(name, spec, postlayout, output_dir)
        elif len(spec["inputs"]) <= 6:
            result = _characterize_exhaustive(name, spec, postlayout, output_dir, evaluate)
        else:
            result = _characterize_bounded_combinational(name, spec, postlayout, output_dir)
        return _write_result(output_dir, result)
    except Exception as error:
        failure = {
            "name": spec.get("name") if isinstance(spec, dict) else None,
            "benchmark": name, "passed": False, "logic_levels_passed": False,
            "error": str(error), "postlayout_path": str(Path(postlayout).resolve()),
            "integration_method": "gear" if name in {"counter", "shift_register4"} else "trap",
        }
        try:
            failure.update(_provenance(name, spec, postlayout))
        except Exception as provenance_error:
            failure["provenance_error"] = str(provenance_error)
        _write_json(output_dir / "characterization.json", failure)
        raise
