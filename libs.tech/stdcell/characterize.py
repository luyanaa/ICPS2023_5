#!/usr/bin/env python3
"""Model-based, postlayout logic levels and arcs; no silicon timing claim."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
import re
import shutil

from verify import MODELS, run_command, sha256

VDD = 3.0
MODEL_CARD = MODELS.parent / "models/icps2023_5_b4soi.inc"
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


class CharacterizationError(ValueError):
    pass


def _run(deck, output_dir):
    ngspice = shutil.which("ngspice")
    if ngspice is None:
        raise CharacterizationError("ngspice required; use root Nix devShell")
    path = Path(output_dir) / "bench.cir"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(deck) + "\n", encoding="utf-8")
    return run_command([ngspice, "-b", path], path.parent / "ngspice.log", cwd=path.parent)


def _header(spec, subcircuit):
    pins = spec["pins"]
    return ["* ICPS2023_5 postlayout characterization; provisional nominal compact model",
            '.include "%s"' % Path(subcircuit).resolve(),
            ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500",
            "VPOWER VDD 0 %.12g" % VDD, "VSUB SUB 0 0",
            "XDUT %s SUB %s" % (" ".join("0" if pin == "VSS" else pin for pin in pins), spec["name"])]


def truth_table(spec, postlayout, output_dir, evaluate):
    """DC circuit operating points compared with independent Boolean truth."""
    if spec.get("sequential"):
        raise CharacterizationError("DC truth table is for combinational cells")
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
    text = _run(deck, output_dir)
    records = []
    for index, bits in enumerate(rows):
        match = re.search(r"TRUTH_ROW_%d\s*\n(.*?)(?=TRUTH_ROW_|\Z)" % index, text, re.S)
        if match is None:
            raise CharacterizationError("missing operating point %d" % index)
        values = {name.casefold(): float(value) for name, value in
                  re.findall(r"^\s*(v\([^)]+\)|i\([^)]+\))\s*=\s*(%s)" % NUMBER, match.group(1), re.M)}
        expected = evaluate(spec, dict(zip(inputs, bits)))
        observed = {}
        for output in outputs:
            voltage = values.get("v(%s)" % output.casefold())
            if voltage is None or not math.isfinite(voltage):
                raise CharacterizationError("missing/nonfinite output %s in truth row %d" % (output, index))
            good = voltage >= .9 * VDD if expected[output] else voltage <= .1 * VDD
            if not good:
                raise CharacterizationError("%s truth %s: %s=%.6g V expected %d" %
                                            (spec["name"], bits, output, voltage, expected[output]))
            observed[output] = voltage
        records.append({"inputs": dict(zip(inputs, bits)), "expected": expected,
                        "voltage_v": observed, "supply_current_a": values.get("i(vpower)")})
    return records


def _measurements(text):
    result = {name.casefold(): float(value) for name, value in re.findall(
        r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(%s)" % NUMBER, text, re.M)}
    if not all(math.isfinite(value) for value in result.values()):
        raise CharacterizationError("nonfinite SPICE measurement")
    return result


def characterize_arcs(spec, postlayout, output_dir, evaluate, *, load_ff=5.0, slew_ns=2.0):
    """Sensitized truth-table arcs at explicit load/slew, including VTC noise margins."""
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
                         ".measure tran out_rise TRIG v(%s) VAL=%.12g RISE=1 TARG v(%s) VAL=%.12g RISE=1" %
                         (output, .2*VDD, output, .8*VDD),
                         ".measure tran out_fall TRIG v(%s) VAL=%.12g FALL=1 TARG v(%s) VAL=%.12g FALL=1" %
                         (output, .8*VDD, output, .2*VDD),
                         ".measure tran settled_high FIND v(%s) AT=%s" % (output, "50n" if invert else "350n"),
                         ".measure tran settled_low FIND v(%s) AT=%s" % (output, "350n" if invert else "50n"),
                         ".end"]
                measured = _measurements(_run(deck, folder))
                required = ("d_up", "d_down", "out_rise", "out_fall", "settled_high", "settled_low")
                if not all(name in measured for name in required):
                    raise CharacterizationError("missing timing measurement for %s -> %s %s" %
                                                (input_pin, output, base))
                if measured["settled_high"] < .9*VDD or measured["settled_low"] > .1*VDD:
                    raise CharacterizationError("unsettled or degraded logic swing")
                if any(measured[name] <= 0 for name in required[:4]):
                    raise CharacterizationError("nonpositive cell delay/transition")
                rise_ns = measured["d_down" if invert else "d_up"] * 1e9
                fall_ns = measured["d_up" if invert else "d_down"] * 1e9
                vtc = _header(spec, postlayout)
                vtc += ["V%s %s 0 %.12g" % (pin, pin, base[pin] * VDD) for pin in others]
                vtc += ["V%s %s 0 0" % (input_pin, input_pin), ".control", "set noaskquit",
                        "dc V%s 0 %.12g .01" % (input_pin, VDD),
                        "wrdata transfer.dat v(%s)" % output, "quit", ".endc", ".end"]
                _run(vtc, folder / "vtc")
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
                             "rise_transition_ns": measured["out_rise"]*1e9,
                             "fall_transition_ns": measured["out_fall"]*1e9,
                             "noise_margin": margin, "load_ff": load_ff, "input_slew_ns": slew_ns})
    return arcs


def characterize_cell(spec, postlayout, output_dir, evaluate):
    output_dir = Path(output_dir)
    truth = truth_table(spec, postlayout, output_dir / "truth", evaluate)
    arcs = characterize_arcs(spec, postlayout, output_dir / "arcs", evaluate)
    worst_ns = max((max(arc["rise_delay_ns"], arc["fall_delay_ns"]) for arc in arcs), default=None)
    result = {"name": spec["name"], "voltage_v": VDD, "temperature_c": 25,
              "truth": truth, "arcs": arcs, "worst_delay_ns": worst_ns,
              "model_sha256": sha256(MODEL_CARD), "mode": "nominal_SOIMOD_0",
              "calibration": "model_based_unqualified_geometry_scaling_and_wire_coupling",
              "logic_levels_passed": True, "timing_objective": "feasibility_only"}
    (output_dir / "characterization.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
