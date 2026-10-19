#!/usr/bin/env python3
"""Model-based, postlayout logic levels and arcs; no silicon timing claim.

Model scope: the ``.control``-based benches in this module execute the *nominal*
stage-1 PDK card in ngspice (BSIMSOI 4.4, LEVEL=58).  They are functional
geometry/logic checks, not model acceptance.  The library evidence path
(``verify.verify_physical``) binds the experimental ideal-FD bridge card, which
is version-limited to Xyce 7.10 / BSIMSOI 4.5.0; running that card here, or
accepting its results from an ngspice run, is forbidden
(``libs.tech/stdcell/model_binding.py``, AGENTS.md 1.2).

The combinational arc bench deliberately keeps its fixed external PULSE slew
and ideal lumped ``CLOAD`` as nominal feasibility stimuli.  Those stimuli are
not the FD-bridge FO/RO timing definition and MUST NOT be promoted to a
Liberty pin capacitance, a universal driver model, or a silicon frequency.
The version-scoped FD-bridge FO/load and closed-loop RO evidence lives in
``libs.tech/ngspice/tools/audit_b4soi_bridge_*`` and uses Xyce directly.
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

import model_binding
import bench_engine
from verify import run_command, sha256

VDD = 3.0
NOMINAL = model_binding.nominal()
MODELS = NOMINAL["library_path"]        # nominal bench selector (stage-1 card)
MODEL_CARD = NOMINAL["include_path"]
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


class CharacterizationError(ValueError):
    pass


def _bench(output_dir, subcircuit, bench=None, *, integration_method="trap"):
    """Resolve the bench: explicit binding, else pair the engine to the netlist's own card."""
    if bench is None:
        bench = bench_engine.bench_for_netlist(subcircuit, work=Path(output_dir),
                                               method=integration_method)
    return bench


def _run(deck, output_dir, bench=None):
    """Run a bench deck: bound engine (translated) or ngspice as before."""
    if bench is not None and bench.engine != "ngspice":
        return bench_engine.run_legacy_deck(bench, deck, name="bench",
                                            output_dir=output_dir)
    ngspice = shutil.which("ngspice")
    if ngspice is None:
        raise CharacterizationError("ngspice required; use root Nix DevShell")
    path = Path(output_dir) / "bench.cir"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(deck) + "\n", encoding="utf-8")
    return run_command([ngspice, "-b", path], path.parent / "ngspice.log", cwd=path.parent)


def _header(spec, subcircuit, *, integration_method="trap"):
    if integration_method not in {"trap", "gear"}:
        raise CharacterizationError("integration_method must be trap or gear")
    pins = spec["pins"]
    return ['.include "%s"' % Path(subcircuit).resolve(),
            "VPOWER VDD 0 %.12g" % VDD, "VSUB SUB 0 0",
            "XDUT %s SUB %s" % (" ".join("0" if pin == "VSS" else pin for pin in pins), spec["name"])]


def truth_table(spec, postlayout, output_dir, evaluate, *, bench=None, strict=True):
    """DC circuit operating points compared with independent Boolean truth."""
    if spec.get("sequential"):
        raise CharacterizationError("DC truth table is for combinational cells")
    bench = _bench(output_dir, postlayout, bench)
    inputs, outputs = spec["inputs"], spec["outputs"]
    rows = list(itertools.product((0, 1), repeat=len(inputs)))
    deck = _header(spec, postlayout)
    deck += ["V%s %s 0 0" % (pin, pin) for pin in inputs]
    deck += [".control", "set noaskquit"]
    for index, bits in enumerate(rows):
        deck += ["alter V%s %.12g" % (pin, bit * VDD) for pin, bit in zip(inputs, bits)]
        deck += ["op", "echo TRUTH_ROW_%d" % index]
        deck += ["print v(%s)" % pin for pin in outputs]
        deck += ["print i(VPOWER)"]
    deck += ["quit", ".endc", ".end"]
    text = _run(deck, output_dir, bench)
    records = []
    for index, bits in enumerate(rows):
        match = re.search(r"TRUTH_ROW_%d\s*\n(.*?)(?=TRUTH_ROW_|\Z)" % index, text, re.S)
        if match is None:
            raise CharacterizationError("missing operating point %d" % index)
        values = {name.casefold(): float(value) for name, value in
                  re.findall(r"^\s*(v\([^)]+\)|i\([^)]+\))\s*=\s*(%s)" % NUMBER, match.group(1), re.M)}
        expected = evaluate(spec, dict(zip(inputs, bits)))
        observed = {}
        logic_ok = {}
        for output in outputs:
            voltage = values.get("v(%s)" % output.casefold())
            if voltage is None or not math.isfinite(voltage):
                raise CharacterizationError("missing/nonfinite output %s in truth row %d" % (output, index))
            good = voltage >= .9 * VDD if expected[output] else voltage <= .1 * VDD
            if not good and strict:
                raise CharacterizationError("%s truth %s: %s=%.6g V expected %d" %
                                            (spec["name"], bits, output, voltage, expected[output]))
            observed[output] = voltage
            logic_ok[output] = good
        records.append({"inputs": dict(zip(inputs, bits)), "expected": expected,
                        "voltage_v": observed, "logic_ok": logic_ok,
                        "passed": all(logic_ok.values()),
                        "supply_current_a": values.get("i(vpower)")})
    return records


def _measurements(text):
    result = {name.casefold(): float(value) for name, value in re.findall(
        r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(%s)" % NUMBER, text, re.M)}
    if not all(math.isfinite(value) for value in result.values()):
        raise CharacterizationError("nonfinite SPICE measurement")
    return result


def characterize_arcs(spec, postlayout, output_dir, evaluate, *, load_ff=5.0, slew_ns=2.0,
                       bench=None):
    """Sensitized truth-table arcs at explicit load/slew, including VTC noise margins."""
    bench = _bench(output_dir, postlayout, bench)
    arcs = []
    if spec.get("sequential"):
        raise CharacterizationError("sequential timing needs a stateful bench")
    for input_pin in spec["inputs"]:
        others = [pin for pin in spec["inputs"] if pin != input_pin]
        for bits in itertools.product((0, 1), repeat=len(others)):
            base = dict(zip(others, bits))
            low = evaluate(spec, {**base, input_pin: 0})
            high = evaluate(spec, {**base, input_pin: 1})
            for output in spec["outputs"]:
                if low[output] == high[output]:
                    continue
                index = len(arcs)
                folder = Path(output_dir) / ("arc_%03d" % index)
                deck = _header(spec, postlayout)
                deck += ["V%s %s 0 %.12g" % (pin, pin, base[pin] * VDD) for pin in others]
                deck += ["V%s %s 0 PULSE(0 %.12g 100n %.12gn %.12gn 400n 1u)" %
                         (input_pin, input_pin, VDD, slew_ns, slew_ns),
                         "CLOAD %s 0 %.12gf" % (output, load_ff)]
                invert = low[output] > high[output]
                first_edge = "FALL" if invert else "RISE"
                second_edge = "RISE" if invert else "FALL"
                deck += [".tran 0.1n 1u",
                         ".measure tran d_up TRIG v(%s) VAL=%.12g RISE=1 TARG v(%s) VAL=%.12g %s=1" %
                         (input_pin, VDD/2, output, VDD/2, first_edge),
                         ".measure tran d_down TRIG v(%s) VAL=%.12g FALL=1 TARG v(%s) VAL=%.12g %s=1 TD=450n" %
                         (input_pin, VDD/2, output, VDD/2, second_edge),
                         ".measure tran out_rise_start TRIG v(%s) VAL=%.12g %s=1%s TARG v(%s) VAL=%.12g RISE=1" %
                         (input_pin, VDD/2, first_edge, "" if first_edge == "RISE" else " TD=450n", output, .2*VDD),
                         ".measure tran out_rise_end TRIG v(%s) VAL=%.12g %s=1%s TARG v(%s) VAL=%.12g RISE=1" %
                         (input_pin, VDD/2, first_edge, "" if first_edge == "RISE" else " TD=450n", output, .8*VDD),
                         ".measure tran out_fall_start TRIG v(%s) VAL=%.12g %s=1%s TARG v(%s) VAL=%.12g FALL=1" %
                         (input_pin, VDD/2, second_edge, " TD=450n" if second_edge == "FALL" else "", output, .8*VDD),
                         ".measure tran out_fall_end TRIG v(%s) VAL=%.12g %s=1%s TARG v(%s) VAL=%.12g FALL=1" %
                         (input_pin, VDD/2, second_edge, " TD=450n" if second_edge == "FALL" else "", output, .2*VDD),
                         ".measure tran settled_high FIND v(%s) AT=%s" % (output, "50n" if invert else "350n"),
                         ".measure tran settled_low FIND v(%s) AT=%s" % (output, "350n" if invert else "50n"),
                         ".end"]
                measured = _measurements(_run(deck, folder, bench))
                required = ("d_up", "d_down", "out_rise_start", "out_rise_end",
                            "out_fall_start", "out_fall_end", "settled_high", "settled_low")
                if not all(name in measured for name in required):
                    raise CharacterizationError("missing timing measurement for %s -> %s %s" %
                                                (input_pin, output, base))
                if measured["settled_high"] < .9*VDD or measured["settled_low"] > .1*VDD:
                    raise CharacterizationError("unsettled or degraded logic swing")
                if any(measured[name] <= 0 for name in ("d_up", "d_down")):
                    raise CharacterizationError("nonpositive cell delay")
                rise_transition = measured["out_rise_end"] - measured["out_rise_start"]
                fall_transition = measured["out_fall_end"] - measured["out_fall_start"]
                if rise_transition <= 0 or fall_transition <= 0:
                    raise CharacterizationError("nonpositive cell transition")
                rise_ns = measured["d_down" if invert else "d_up"] * 1e9
                fall_ns = measured["d_up" if invert else "d_down"] * 1e9
                vtc = _header(spec, postlayout)
                vtc += ["V%s %s 0 %.12g" % (pin, pin, base[pin] * VDD) for pin in others]
                vtc += ["V%s %s 0 0" % (input_pin, input_pin), ".control", "set noaskquit",
                        "dc V%s 0 %.12g .01" % (input_pin, VDD),
                        "wrdata transfer.dat v(%s)" % output, "quit", ".endc", ".end"]
                _run(vtc, folder / "vtc", bench)
                pairs = []
                for line in (folder / "vtc/transfer.dat").read_text().splitlines():
                    fields = line.split()
                    if len(fields) != 2:
                        raise CharacterizationError("unexpected VTC column count")
                    pairs.append(tuple(float(value) for value in fields))
                high_gain = []
                for before, after in zip(pairs, pairs[1:]):
                    gain = (after[1]-before[1])/(after[0]-before[0])
                    if abs(gain) >= 1.0:
                        high_gain.append((before[0]+after[0])/2)
                if not high_gain:
                    raise CharacterizationError("no unit-gain transition for %s -> %s %s" %
                                                (input_pin, output, base))
                vil, vih = min(high_gain), max(high_gain)
                low_output = min(pairs[0][1], pairs[-1][1])
                high_output = max(pairs[0][1], pairs[-1][1])
                margin = {"vil_v": vil, "vih_v": vih,
                          "nml_v": vil-low_output,
                          "nmh_v": high_output-vih}
                if margin["nml_v"] <= 0 or margin["nmh_v"] <= 0:
                    raise CharacterizationError("nonpositive noise margin for %s -> %s" % (input_pin, output))
                arcs.append({"input": input_pin, "output": output, "condition": base,
                             "sense": "negative_unate" if invert else "positive_unate",
                             "rise_delay_ns": rise_ns, "fall_delay_ns": fall_ns,
                             "rise_transition_ns": rise_transition * 1e9,
                             "fall_transition_ns": fall_transition * 1e9,
                             "noise_margin": margin, "load_ff": load_ff, "input_slew_ns": slew_ns})
    return arcs


def sequential_state(spec, postlayout, output_dir, *, load_ff=10.0,
                     integration_method="trap", bench=None):
    """Exercise transparency/capture and hold without adding internal state loads."""
    state = spec.get("sequential")
    if not state or state["kind"] not in {"latch", "flip_flop"}:
        raise CharacterizationError("capture/hold requires a latch or flip-flop")
    bench = _bench(output_dir, postlayout, bench, integration_method=integration_method)
    data, clock, output = state["data"], state["clock"], state["q"]
    deck = _header(spec, postlayout, integration_method=integration_method)
    deck += [
        "VCLK %s 0 PWL(0 0 .9u 0 1u 3 2u 3 2.1u 0 3u 0 3.1u 3 "
        "4u 3 4.1u 0 5u 0 5.1u 3 6u 3 6.1u 0 7u 0)" % clock,
        "CLOAD %s 0 %.12gf" % (output, load_ff)]
    if state["kind"] == "latch":
        deck.append("VD %s 0 PWL(0 3 1.3u 3 1.4u 0 2.3u 0 2.4u 3 "
                    "3.3u 3 3.4u 0 4.3u 0 4.4u 3 7u 3)" % data)
        samples = (("transparent_high", 1.2, 1), ("transparent_low", 1.8, 0),
                   ("hold_low", 2.8, 0), ("reopen_high", 3.2, 1),
                   ("track_low", 3.8, 0), ("second_hold", 4.8, 0),
                   ("recapture_high", 5.8, 1))
    else:
        deck.append("VD %s 0 PWL(0 3 1.3u 3 1.4u 0 3.3u 0 3.4u 3 7u 3)" % data)
        samples = (("capture_high", 1.2, 1), ("hold_high", 2.8, 1),
                   ("capture_low", 3.2, 0), ("hold_low", 4.8, 0),
                   ("recapture_high", 5.8, 1))
    deck += [".tran 1n 7u"]
    deck += [".measure tran %s FIND v(%s) AT=%.12gu" % (label, output, time)
             for label, time, _ in samples]
    deck.append(".end")
    measured = _measurements(_run(deck, output_dir, bench))
    records = []
    for label, time, expected in samples:
        voltage = measured.get(label)
        if voltage is None or not (voltage >= .9*VDD if expected else voltage <= .1*VDD):
            raise CharacterizationError("%s state %s: %s V expected %d" %
                                        (spec["name"], label, voltage, expected))
        records.append({"state": label, "time_us": time, "expected": expected,
                        "voltage_v": voltage})
    result = {"name": spec["name"], "kind": state["kind"], "samples": records,
              "load_ff": load_ff, "voltage_v": VDD, "temperature_c": 25,
              "model_sha256": sha256(bench.binding["include_path"] if bench else MODEL_CARD),
              "bench_engine": bench.engine if bench else "ngspice",
              "logic_levels_passed": True,
              "postlayout_sha256": sha256(Path(postlayout)),
              "integration_method": integration_method,
              "calibration": "model_based_unqualified_geometry_scaling_and_wire_coupling",
              "scope": "capture_hold_only; setup_hold_and_clock_timing_uncharacterized"}
    (Path(output_dir) / "state.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def characterize_storage(spec, postlayout, output_dir, *, load_ff=10.0,
                         hold_times_us=(10.0, 1000.0), slew_ns=2.0,
                         integration_method="trap", bench=None):
    """Nominal-model static retention and bounded clock-aperture experiments.

    No internal capacitors, resistors, initial conditions, or forced state are
    added. Timing bounds describe the tested model/load/slew, not a Liberty
    corner, metastability guarantee, or manufacturing specification.
    """
    state = spec.get("sequential")
    if not state or state["kind"] not in {"latch", "flip_flop"}:
        raise CharacterizationError("storage characterization requires a static latch or DFF")
    if (not math.isfinite(load_ff) or load_ff <= 0 or
            not math.isfinite(slew_ns) or slew_ns <= 0 or slew_ns > 2):
        raise CharacterizationError("positive load and 0 < slew <= 2ns required for this bench")
    if not hold_times_us or any(not math.isfinite(time) or time < 10 for time in hold_times_us):
        raise CharacterizationError("stopped-clock hold times must be finite and at least 10us")
    output_dir = Path(output_dir)
    bench = _bench(output_dir, postlayout, bench, integration_method=integration_method)
    data, clock, output = state["data"], state["clock"], state["q"]
    basic = sequential_state(spec, postlayout, output_dir / "capture", load_ff=load_ff,
                             integration_method=integration_method, bench=bench)

    def waveform(pin, events):
        return "V%s %s 0 PWL(%s)" % (
            pin, pin, " ".join("%.12gn %.12g" % (time, bit * VDD) for time, bit in events))

    def edge(events, time, before, after):
        events.extend(((time-slew_ns/2, before), (time+slew_ns/2, after)))

    def simulate(folder, data_events, clock_events, end_ns, measures, *, maxstep_ns=2.0):
        deck = _header(spec, postlayout, integration_method=integration_method)
        deck += [waveform(data, data_events), waveform(clock, clock_events),
                 "CLOAD %s 0 %.12gf" % (output, load_ff),
                 ".tran 1n %.12gn 0 %.12gn" % (end_ns, maxstep_ns)]
        deck += measures + [".end"]
        values = _measurements(_run(deck, folder, bench))
        required = [measure.split()[2].casefold() for measure in measures]
        if any(name not in values for name in required):
            raise CharacterizationError("missing storage measurement in %s" % folder)
        return values

    retention = []
    for bit in (0, 1):
        for level in ((0, 1) if state["kind"] == "flip_flop" else (0,)):
            for hold_us in hold_times_us:
                end_ns = 5000.0 + hold_us * 1000.0
                ce = [(0.0, 0)]
                edge(ce, 1000.0, 0, 1)
                if level == 0:
                    edge(ce, 2000.0, 1, 0)
                ce.append((end_ns, level))
                de = [(0.0, bit), (2900.0, bit)]
                edge(de, 3000.0, bit, 1-bit)
                de.append((end_ns, 1-bit))
                folder = output_dir / ("hold_q%d_c%d_%gus" % (bit, level, hold_us))
                values = simulate(
                    folder, de, ce, end_ns,
                    [".measure tran held_min MIN v(%s) FROM=4u TO=%.12gn" % (output, end_ns),
                     ".measure tran held_max MAX v(%s) FROM=4u TO=%.12gn" % (output, end_ns),
                     ".measure tran supply_current FIND i(VPOWER) AT=%.12gn" % (end_ns-1.0)],
                    maxstep_ns=min(10000.0, hold_us * 10.0))
                passed = values["held_min"] >= .9*VDD if bit else values["held_max"] <= .1*VDD
                retention.append({"stored_bit": bit, "stopped_clock_level": level,
                                  "hold_time_us": hold_us, "passed": passed, **values})
                if not passed:
                    raise CharacterizationError("static retention failed: %s" % retention[-1])

    margins = (-32.0, -16.0, -8.0, -4.0, -2.0, -1.0, 0.0,
               1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0)
    apertures = {"setup": [], "hold": [], "high_pulse": [], "low_pulse": []}
    for kind in ("setup", "hold", "high_pulse", "low_pulse"):
        if kind == "low_pulse" and state["kind"] == "latch":
            continue  # Low is the latch's indefinite opaque state, not a capture pulse.
        values_to_try = margins if kind in {"setup", "hold"} else (2., 4., 8., 16., 32., 64., 128., 256.)
        for margin in values_to_try:
            transitions = []
            for captured_bit in (0, 1):
                previous_bit = 1-captured_bit
                ce = [(0.0, 0)]
                edge(ce, 1000., 0, 1)
                edge(ce, 1500., 1, 0)
                de = [(0., previous_bit)]
                if kind in {"setup", "hold"}:
                    if state["kind"] == "flip_flop":
                        edge(ce, 3000., 0, 1)
                        edge(ce, 3500., 1, 0)
                    else:
                        edge(ce, 2500., 0, 1)
                        edge(ce, 3000., 1, 0)
                    if kind == "setup":
                        edge(de, 3000.-margin, previous_bit, captured_bit)
                    else:
                        edge(de, 2000., previous_bit, captured_bit)
                        edge(de, 3000.+margin, captured_bit, previous_bit)
                elif kind == "high_pulse":
                    edge(de, 2500., previous_bit, captured_bit)
                    edge(ce, 3000., 0, 1)
                    edge(ce, 3000.+margin, 1, 0)
                else:
                    edge(ce, 2000., 0, 1)
                    edge(de, 2500., previous_bit, captured_bit)
                    edge(ce, 3000., 1, 0)
                    edge(ce, 3000.+margin, 0, 1)
                ce.append((4000., ce[-1][1]))
                de.append((4000., de[-1][1]))
                folder = output_dir / ("%s_%gns_q%d" % (kind, margin, captured_bit))
                try:
                    values = simulate(
                        folder, de, ce, 4000.,
                        [".measure tran captured FIND v(%s) AT=3.9u" % output])
                except (CharacterizationError, RuntimeError, subprocess.SubprocessError) as error:
                    # Unsafe aperture points may not converge. Preserve a failed
                    # experiment; never promote it to a passing timing bound.
                    transitions.append({
                        "captured_bit": captured_bit, "voltage_v": None, "passed": False,
                        "status": "simulation_failed", "error": str(error),
                        "simulator_log": str(folder / "ngspice.log")
                        if (folder / "ngspice.log").is_file() else None,
                    })
                    continue
                voltage = values["captured"]
                passed = voltage >= .9*VDD if captured_bit else voltage <= .1*VDD
                transitions.append({"captured_bit": captured_bit, "voltage_v": voltage,
                                    "passed": passed, "status": "measured"})
            apertures[kind].append({"margin_ns": margin, "transitions": transitions,
                                    "passed": all(row["passed"] for row in transitions)})

    bounds = {}
    for kind, records in apertures.items():
        if not records:
            bounds[kind] = {"applicable": False, "reason": "latch opaque low can stop indefinitely"}
            continue
        safe = next((index for index in range(len(records))
                     if all(record["passed"] for record in records[index:])), None)
        if safe is None:
            raise CharacterizationError("no passing conservative %s bound in tested range" % kind)
        failing = [record["margin_ns"] for record in records[:safe] if not record["passed"]]
        bounds[kind] = {
            "applicable": True, "conservative_tested_bound_ns": records[safe]["margin_ns"],
            "last_failing_test_ns": max(failing) if failing else None,
            "tested_range_ns": [records[0]["margin_ns"], records[-1]["margin_ns"]],
            "exact_minimum_claimed": False,
            "meaning": "all tested margins at or above this bound captured the expected state",
        }
    result = {
        "name": spec["name"], "kind": state["kind"], "capture": basic,
        "retention": retention, "aperture_experiments": apertures, "timing_bounds": bounds,
        "preservation_correctness": True, "hold_correctness": True, "pulse_correctness": True,
        "passed": True, "load_ff": load_ff, "input_slew_ns": slew_ns,
        "voltage_v": VDD, "temperature_c": 25,
        "model_sha256": sha256(bench.binding["include_path"] if bench else MODEL_CARD),
        "bench_engine": bench.engine if bench else "ngspice",
        "integration_method": integration_method,
        "nonconverged_aperture_policy": "failed experiment; excluded from passing conservative bound",
        "simulation_failures_within_sweep": sum(
            transition["status"] == "simulation_failed"
            for rows in apertures.values() for row in rows
            for transition in row["transitions"]),
        "timing_objective": "feasibility_only",
        "calibration": "nominal_model_only; no PVT, jitter, silicon, or metastability qualification",
        "internal_state_loads_added": False,
        "postlayout_sha256": sha256(Path(postlayout)),
        "characterizer_sha256": sha256(Path(__file__)),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "storage.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def characterize_cell(spec, postlayout, output_dir, evaluate, *, bench=None):
    output_dir = Path(output_dir)
    # A cell with no sensitizable arc (a tie cell) writes no bench artefacts, so
    # the run directory has to exist before the record is written.
    output_dir.mkdir(parents=True, exist_ok=True)
    truth = truth_table(spec, postlayout, output_dir / "truth", evaluate, bench=bench)
    arcs = characterize_arcs(spec, postlayout, output_dir / "arcs", evaluate, bench=bench)
    worst_ns = max((max(arc["rise_delay_ns"], arc["fall_delay_ns"]) for arc in arcs), default=None)
    result = {"name": spec["name"], "voltage_v": VDD, "temperature_c": 25,
              "truth": truth, "arcs": arcs, "worst_delay_ns": worst_ns,
              "model_sha256": sha256(bench.binding["include_path"] if bench else MODEL_CARD),
              "bench_engine": bench.engine if bench else "ngspice", "mode": "nominal_SOIMOD_0",
              "calibration": "model_based_unqualified_geometry_scaling_and_wire_coupling",
              "logic_levels_passed": True, "timing_objective": "feasibility_only"}
    (output_dir / "characterization.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
