#!/usr/bin/env python3
"""F3 transient and F4 substrate-bias sensitivity audit for B4SOI SOIMOD modes.

All candidate libraries are SOIMOD-only sensitivities. The results are simulator
observations, not calibration, hardware qualification, or a model selection.
"""

from __future__ import annotations

import argparse
import bisect
from collections import Counter
import math
from pathlib import Path
import shutil

import b4soi_depletion as common


STOP_S = 1e-6
SWITCH_S = 100e-9
TAIL_WINDOW_S = 100e-9
TAIL_INTERVAL_S = 5e-12
SETTLE_TOLERANCE_V = 0.02 * common.VDD
CONSTANT_CURRENT_A = 1e-6
INTEGRATIONS = (
    {"id": "trap_20ps", "method": "trap", "maxstep_s": 20e-12},
    {"id": "trap_10ps", "method": "trap", "maxstep_s": 10e-12},
    {"id": "gear_20ps", "method": "gear", "maxstep_s": 20e-12},
)
T7_LOADS = (
    {"id": "5fF", "capacitance_F": 5e-15},
    {"id": "457fF_representative_lumped", "capacitance_F": 457e-15},
    {"id": "1pF", "capacitance_F": 1e-12},
)
INVERTER_LOADS = (
    {"id": "small_explicit_5fF", "kind": "capacitor", "capacitance_F": 5e-15},
    {"id": "representative_lumped_457fF", "kind": "capacitor", "capacitance_F": 457e-15},
    {"id": "real_fo1_gate_pair", "kind": "fo1", "capacitance_F": None},
)
SUBSTRATE_BIASES_V = (-0.1, 0.0, 0.1)
DRAIN_BIASES_V = {"nmos": (0.05, 1.0), "pmos": (-0.05, -1.0)}


MISSING_PROCESS_INPUTS = {
    "revision_specific_silicon_data": [
        "measured NMOS and PMOS ID-VG and ID-VD curves for this process revision",
        "those transfer/output curves at controlled substrate/back-gate E bias, including E=0 and both signed biases",
        "device geometry, lot/corner, temperature, and measurement conditions for each silicon curve",
    ],
    "backgate_and_capacitance_data": [
        "revision-specific back-gate/body-bias response and C-V measurements for both polarities",
        "measured VFB, VFBB, and VFBZB / work-function or flatband calibration data",
    ],
    "approved_reliability_bounds": [
        "manufacturer-approved substrate/back-gate E operating and reliability limits",
        "approved source/body junction-bias, VGS, and VDS limits over temperature, duration, and process corners",
    ],
    "unqualified_process_model_inputs": [
        "per-polarity NCH, NPEAK, NSUB, and NGATE channel-doping inputs",
        "revision-specific VTH0, K1, K2 and flatband/back-gate parameters VFB, VFBB, VFBZB",
        "TSI, TBOX, XJ, VBSA, K1B, K2B, DK2B, DVBD0, DVBD1, VBS0PD, and VBS0FD calibration",
        "NOFFFD, VOFFFD, MOINFD, LPE0 and the supported FDMOD=1 CDSBS/VSCE formulation and calibration",
    ],
}


def _spice(value: float) -> str:
    return common.fmt(value)


def _candidate_index(candidates: list[dict]) -> dict[tuple[str, int, int], dict]:
    return {(item["base"], item["nmode"], item["pmode"]): item for item in candidates}


def _mode_candidate(index: dict[tuple[str, int, int], dict], base: str, mode: int) -> dict:
    return index[(base, mode, mode)]


def _single_switch_deck(candidate: dict, polarity: str, load_f: float,
                        integration: dict, name: str) -> list[str]:
    method = integration["method"]
    step = integration["maxstep_s"]
    if polarity == "nmos":
        gate_pulse = "PULSE(0 %s %s 2n 2n 400n 1u)" % (_spice(common.VDD), _spice(SWITCH_S))
        transistor = "M1 DRAIN G 0 SUB nch W=%su L=%su" % (common.W_UM, common.L_UM)
        initial_output = common.VDD
    else:
        gate_pulse = "PULSE(%s 0 %s 2n 2n 400n 1u)" % (_spice(common.VDD), _spice(SWITCH_S))
        transistor = "M1 DRAIN G VDD SUB pch W=%su L=%su" % (common.W_UM, common.L_UM)
        initial_output = 0.0
    return common.header(candidate["library"],
                         "F3 T7 %s single-switch free-output, load=%s F" %
                         (polarity, _spice(load_f)), method=method) + [
        "VDD VDD 0 %s" % _spice(common.VDD),
        "VSUB SUB 0 0",
        "VG G 0 %s" % gate_pulse,
        transistor,
        "VDSENSE OUT DRAIN 0",
        "CLOAD OUT 0 %s" % _spice(load_f),
        ".ic v(OUT)=%s" % _spice(initial_output),
    ] + common.control([
        "set filetype=ascii",
        "tran %s %s 0 %s" % (_spice(step), _spice(STOP_S), _spice(step)),
        "wrdata %s.dat v(OUT) v(G) i(VG) i(VDSENSE)" % name,
    ])


def _inverter_deck(candidate: dict, load: dict, integration: dict, name: str) -> list[str]:
    method = integration["method"]
    step = integration["maxstep_s"]
    lines = common.header(candidate["library"],
                          "F3 inverter %s / %s" % (candidate["id"], load["id"]),
                          method=method) + [
        "VPWR VDD 0 %s" % _spice(common.VDD),
        "VSUB SUB 0 0",
        "VIN IN 0 PULSE(0 %s %s 2n 2n 400n 1u)" %
        (_spice(common.VDD), _spice(SWITCH_S)),
        "MP OUT IN VDD SUB pch W=%su L=%su" % (common.W_UM, common.L_UM),
        "MN OUT IN 0 SUB nch W=%su L=%su" % (common.W_UM, common.L_UM),
    ]
    if load["kind"] == "capacitor":
        lines.append("CLOAD OUT 0 %s" % _spice(load["capacitance_F"]))
        vectors = "v(IN) v(OUT) i(VIN) i(VPWR)"
    else:
        # A real FO1 load is the next inverter's gate pair; its two E terminals
        # share SUB, and its output is allowed to resolve normally.
        lines += [
            "MP2 FO1OUT OUT VDD SUB pch W=%su L=%su" % (common.W_UM, common.L_UM),
            "MN2 FO1OUT OUT 0 SUB nch W=%su L=%su" % (common.W_UM, common.L_UM),
        ]
        vectors = "v(IN) v(OUT) i(VIN) i(VPWR) v(FO1OUT)"
    return lines + common.control([
        "set filetype=ascii",
        "tran %s %s 0 %s" % (_spice(step), _spice(STOP_S), _spice(step)),
        "wrdata %s.dat %s" % (name, vectors),
    ])


def _transfer_deck(candidate: dict, polarity: str, substrate_v: float,
                   drain_v: float, name: str) -> tuple[list[str], float]:
    if polarity == "nmos":
        model = "nch"
        gate_start, gate_stop = 0.0, common.VDD
    else:
        model = "pch"
        gate_start, gate_stop = -common.VDD, 0.0
    lines = common.header(candidate["library"],
                          "F4 simulator-only transfer %s E=%s VDS=%s V" %
                          (polarity, _spice(substrate_v), _spice(drain_v))) + [
        "VD D 0 %s" % _spice(drain_v),
        "VG G 0 0",
        "VS S 0 0",
        "VE SUB 0 %s" % _spice(substrate_v),
        "M1 D G S SUB %s W=%su L=%su" % (model, common.W_UM, common.L_UM),
    ] + common.control([
        "set filetype=ascii",
        "dc VG %s %s 0.01" % (_spice(gate_start), _spice(gate_stop)),
        "wrdata %s.dat v(G) i(VD) i(VG)" % name,
    ])
    return lines, gate_stop


def _crossing_events(axis: list[float], values: list[float], level: float) -> list[dict]:
    """Report sign-changing crossings, using the first exact-level sample."""
    events = []
    last_index = None
    last_side = 0
    equality_index = None
    for index, value in enumerate(values):
        side = (value > level) - (value < level)
        if side == 0:
            if equality_index is None:
                equality_index = index
            continue
        if last_side and side != last_side:
            if equality_index is not None:
                crossing_axis = axis[equality_index]
            else:
                previous_value = values[last_index]
                fraction = (level - previous_value) / (value - previous_value)
                crossing_axis = axis[last_index] + fraction * (axis[index] - axis[last_index])
            events.append({"time_s": crossing_axis,
                           "direction": "rising" if side > last_side else "falling"})
        last_index = index
        last_side = side
        equality_index = None
    return events


def _constant_current_threshold(axis: list[float], values: list[float], level: float) -> dict:
    """Enumerate piecewise-linear roots; an equality interval is nonunique."""
    roots = {item["time_s"]: item["direction"]
             for item in _crossing_events(axis, values, level)}
    for point, value in zip(axis, values):
        if value == level:
            roots.setdefault(point, "exact_sample")
    equality_interval = any(a == level and b == level for a, b in zip(values, values[1:]))
    unique = len(roots) == 1 and not equality_interval
    return {
        "constant_current_crossings": [
            {"VGS_V": point,
             "direction": ("sample_equals_reference" if direction == "exact_sample" else
                           "increasing_abs_drain_current" if direction == "rising" else
                           "decreasing_abs_drain_current")}
            for point, direction in sorted(roots.items())],
        "constant_current_status": (
            "nonunique_equality_interval" if equality_interval else
            "bracketed_single" if unique else
            "multiple_crossings" if roots else "not_bracketed_in_sweep"),
        "constant_current_VGS_V": next(iter(roots)) if unique else None,
    }


def _sample(axis: list[float], values: list[float], at: float) -> float:
    right = bisect.bisect_right(axis, at)
    if right == 0:
        return values[0]
    if right >= len(axis):
        return values[-1]
    left = right - 1
    fraction = (at - axis[left]) / (axis[right] - axis[left])
    return values[left] + fraction * (values[right] - values[left])


def _uniform_samples(axis: list[float], values: list[float], start: float,
                     stop: float) -> tuple[list[float], list[float]]:
    count = int(round((stop - start) / TAIL_INTERVAL_S))
    uniform_axis = [start + i * TAIL_INTERVAL_S for i in range(count + 1)]
    return uniform_axis, [_sample(axis, values, point) for point in uniform_axis]


def _tail_summary(axis: list[float], values: list[float], start: float,
                  stop: float, crossing_level: float = 0.0) -> dict:
    uniform_axis, uniform_values = _uniform_samples(axis, values, start, stop)
    mean = sum(uniform_values) / len(uniform_values)
    rms = math.sqrt(sum((value - mean) ** 2 for value in uniform_values) / len(uniform_values))
    return {
        "mean": mean,
        "rms_about_mean": rms,
        "peak_to_peak": max(uniform_values) - min(uniform_values),
        "sample_count": len(uniform_values),
        "crossing_count": len(_crossing_events(uniform_axis, uniform_values, crossing_level)),
    }


def _settling(axis: list[float], values: list[float], switch_time: float | None) -> dict:
    target = values[-1]
    last_outside = None
    for index in range(len(values) - 1, -1, -1):
        if abs(values[index] - target) > SETTLE_TOLERANCE_V:
            last_outside = index
            break
    settled_index = 0 if last_outside is None else last_outside + 1
    settled = settled_index < len(axis)
    settle_time = axis[settled_index] if settled else None
    return {
        "target_final_voltage_V": target,
        "tolerance_V": SETTLE_TOLERANCE_V,
        "criterion": "first saved sample after the last out-of-band sample; all remaining saved values through stop are within final voltage +/- 2% VDD",
        "settling_time_s": settle_time,
        "settling_after_switch_s": (None if settle_time is None or switch_time is None
                                    else max(0.0, settle_time - switch_time)),
    }


def _transient_metrics(axis: list[float], vectors: list[list[float]],
                       *, switch_signal_index: int, has_fo1_output: bool = False) -> dict:
    if has_fo1_output:
        v_in, v_out, i_in, i_supply, v_fo1_out = vectors
    else:
        v_in, v_out, i_in, i_supply = vectors
        v_fo1_out = None
    tail_start = STOP_S - TAIL_WINDOW_S
    output_tail = _tail_summary(axis, v_out, tail_start, STOP_S, common.VDD / 2.0)
    supply_tail = _tail_summary(axis, i_supply, tail_start, STOP_S)
    input_tail = _tail_summary(axis, i_in, tail_start, STOP_S)
    half = common.VDD / 2.0
    input_events = _crossing_events(axis, v_in, half)
    output_events = _crossing_events(axis, v_out, half)
    output_falling = next((event for event in output_events if event["direction"] == "falling"), None)
    gate_events = _crossing_events(axis, vectors[switch_signal_index], half)
    switch_event = gate_events[-1] if gate_events else None
    metrics = {
        "full_excursion": {
            "input_voltage_min_V": min(v_in), "input_voltage_max_V": max(v_in),
            "output_voltage_min_V": min(v_out), "output_voltage_max_V": max(v_out),
            "input_current_min_A": min(i_in), "input_current_max_A": max(i_in),
            "supply_current_min_A": min(i_supply), "supply_current_max_A": max(i_supply),
        },
        "final": {
            "input_voltage_V": v_in[-1], "output_voltage_V": v_out[-1],
            "input_current_A": i_in[-1], "supply_current_A": i_supply[-1],
            "fo1_stage_output_voltage_V": None if v_fo1_out is None else v_fo1_out[-1],
        },
        "tail_uniform": {
            "start_s": tail_start, "end_s": STOP_S,
            "sample_interval_s": TAIL_INTERVAL_S,
            "output_voltage_mean_V": output_tail["mean"],
            "output_voltage_rms_about_mean_V": output_tail["rms_about_mean"],
            "output_voltage_peak_to_peak_V": output_tail["peak_to_peak"],
            "output_half_supply_crossings": output_tail["crossing_count"],
            "supply_current_mean_A": supply_tail["mean"],
            "supply_current_rms_about_mean_A": supply_tail["rms_about_mean"],
            "supply_current_peak_to_peak_A": supply_tail["peak_to_peak"],
            "supply_current_zero_crossings": supply_tail["crossing_count"],
            "input_current_mean_A": input_tail["mean"],
            "input_current_rms_about_mean_A": input_tail["rms_about_mean"],
            "input_current_peak_to_peak_A": input_tail["peak_to_peak"],
            "uniform_sample_count": output_tail["sample_count"],
        },
        "crossings": {
            "input_half_supply": input_events,
            "output_half_supply": output_events,
            "output_falling_half_supply_time_s": None if output_falling is None else output_falling["time_s"],
            "input_voltage_at_output_falling_half_supply_V": (
                None if output_falling is None else _sample(axis, v_in, output_falling["time_s"])),
            "input_or_gate_switch_time_s": None if switch_event is None else switch_event["time_s"],
        },
        "settling": _settling(axis, v_out,
                              None if switch_event is None else switch_event["time_s"]),
        "interpretation": (
            "Current zero-crossings/sign changes are numerical observations only; they do not establish stable oscillation."
        ),
    }
    if v_fo1_out is not None:
        metrics["full_excursion"]["fo1_stage_output_voltage_min_V"] = min(v_fo1_out)
        metrics["full_excursion"]["fo1_stage_output_voltage_max_V"] = max(v_fo1_out)
    return metrics


def _t7_metrics(axis: list[float], vectors: list[list[float]], polarity: str) -> dict:
    v_out, v_gate, i_gate, i_drain = vectors
    tail_start = STOP_S - TAIL_WINDOW_S
    output_tail = _tail_summary(axis, v_out, tail_start, STOP_S, common.VDD / 2.0)
    drain_tail = _tail_summary(axis, i_drain, tail_start, STOP_S)
    gate_tail = _tail_summary(axis, i_gate, tail_start, STOP_S)
    output_events = _crossing_events(axis, v_out, common.VDD / 2.0)
    gate_events = _crossing_events(axis, v_gate, common.VDD / 2.0)
    switch_event = gate_events[-1] if gate_events else None
    return {
        "initial_sample": {
            "time_s": axis[0],
            "output_voltage_V": v_out[0],
            "expected_output_voltage_V": common.VDD if polarity == "nmos" else 0.0,
        },
        "full_excursion": {
            "output_voltage_min_V": min(v_out), "output_voltage_max_V": max(v_out),
            "gate_voltage_min_V": min(v_gate), "gate_voltage_max_V": max(v_gate),
            "gate_current_min_A": min(i_gate), "gate_current_max_A": max(i_gate),
            "drain_current_min_A": min(i_drain), "drain_current_max_A": max(i_drain),
        },
        "final": {
            "output_voltage_V": v_out[-1], "gate_voltage_V": v_gate[-1],
            "gate_current_A": i_gate[-1], "drain_current_A": i_drain[-1],
        },
        "tail_uniform": {
            "start_s": tail_start, "end_s": STOP_S,
            "sample_interval_s": TAIL_INTERVAL_S,
            "output_voltage_mean_V": output_tail["mean"],
            "output_voltage_rms_about_mean_V": output_tail["rms_about_mean"],
            "output_voltage_peak_to_peak_V": output_tail["peak_to_peak"],
            "output_half_supply_crossings": output_tail["crossing_count"],
            "drain_current_mean_A": drain_tail["mean"],
            "drain_current_rms_about_mean_A": drain_tail["rms_about_mean"],
            "drain_current_peak_to_peak_A": drain_tail["peak_to_peak"],
            "drain_current_zero_crossings": drain_tail["crossing_count"],
            "gate_current_mean_A": gate_tail["mean"],
            "gate_current_rms_about_mean_A": gate_tail["rms_about_mean"],
            "gate_current_peak_to_peak_A": gate_tail["peak_to_peak"],
            "uniform_sample_count": output_tail["sample_count"],
        },
        "crossings": {
            "output_half_supply": output_events,
            "gate_half_supply": gate_events,
        },
        "settling": _settling(axis, v_out,
                              None if switch_event is None else switch_event["time_s"]),
        "interpretation": (
            "Current zero-crossings/sign changes are numerical observations only; they do not establish stable oscillation."
        ),
        "polarity": polarity,
    }


def _run_transient(ngspice: str, work: Path, name: str, deck: list[str],
                   timeout_s: float, *, vectors: int, metric_kind: str,
                   polarity: str | None = None, fo1: bool = False) -> dict:
    result = common.run_deck(ngspice, work, name, deck, vectors=vectors,
                             stop=STOP_S, timeout=timeout_s)
    row = {"id": name, "evidence": result["evidence"]}
    if result["evidence"]["status"] == "observed":
        if metric_kind == "t7":
            row["metrics"] = _t7_metrics(result["axis"], result["vectors"], polarity)
        else:
            row["metrics"] = _transient_metrics(result["axis"], result["vectors"],
                                                switch_signal_index=0, has_fo1_output=fo1)
    return row


def _run_t7(ngspice: str, work: Path, candidates: dict, timeout_s: float) -> list[dict]:
    rows = []
    for base in ("frozen", "overlap"):
        for polarity in ("nmos", "pmos"):
            for mode in common.MODES:
                candidate = _mode_candidate(candidates, base, mode)
                for load in T7_LOADS:
                    for integration in INTEGRATIONS:
                        name = "t7_%s_%s_m%d_load_%s_%s" % (
                            base, polarity, mode, load["id"], integration["id"])
                        deck = _single_switch_deck(candidate, polarity, load["capacitance_F"],
                                                   integration, name)
                        row = _run_transient(ngspice, work, name, deck, timeout_s,
                                             vectors=4, metric_kind="t7", polarity=polarity)
                        row.update({"base": base, "polarity": polarity, "mode": mode,
                                    "candidate_id": candidate["id"],
                                    "load_id": load["id"],
                                    "load_capacitance_F": load["capacitance_F"],
                                    "integration": integration})
                        rows.append(row)
    return rows


def _run_inverters(ngspice: str, work: Path, candidates: list[dict],
                   timeout_s: float) -> list[dict]:
    selected = [candidate for candidate in candidates
                if (candidate["base"] == "frozen" and candidate["nmode"] == candidate["pmode"])
                or candidate["base"] == "overlap"]
    rows = []
    for candidate in selected:
        for load in INVERTER_LOADS:
            for integration in INTEGRATIONS:
                name = "inv_%s_load_%s_%s" % (candidate["id"], load["id"], integration["id"])
                deck = _inverter_deck(candidate, load, integration, name)
                row = _run_transient(ngspice, work, name, deck, timeout_s,
                                     vectors=5 if load["kind"] == "fo1" else 4,
                                     metric_kind="inverter", fo1=load["kind"] == "fo1")
                row.update({"base": candidate["base"], "nmode": candidate["nmode"],
                            "pmode": candidate["pmode"], "candidate_id": candidate["id"],
                            "load_id": load["id"], "load_kind": load["kind"],
                            "load_capacitance_F": load["capacitance_F"],
                            "integration": integration})
                rows.append(row)
    return rows


def _run_f4(ngspice: str, work: Path, candidate_index: dict,
            timeout_s: float) -> tuple[list[dict], list[dict]]:
    rows = []
    for base in ("frozen", "overlap"):
        for polarity in ("nmos", "pmos"):
            for mode in common.MODES:
                candidate = _mode_candidate(candidate_index, base, mode)
                for drain_v in DRAIN_BIASES_V[polarity]:
                    for substrate_v in SUBSTRATE_BIASES_V:
                        name = "f4_%s_%s_m%d_e_%s_vds_%s" % (
                            base, polarity, mode, _spice(substrate_v).replace("-", "m"),
                            _spice(drain_v).replace("-", "m"))
                        deck, gate_stop = _transfer_deck(candidate, polarity, substrate_v,
                                                         drain_v, name)
                        result = common.run_deck(ngspice, work, name, deck, vectors=3,
                                                 stop=gate_stop, timeout=timeout_s)
                        row = {"id": name, "base": base, "polarity": polarity,
                               "mode": mode, "candidate_id": candidate["id"],
                               "substrate_bias_V": substrate_v, "drain_bias_V": drain_v,
                               "source_bias_V": 0.0, "gate_sweep_start_V": -common.VDD if polarity == "pmos" else 0.0,
                               "gate_sweep_stop_V": 0.0 if polarity == "pmos" else common.VDD,
                               "constant_current_reference_A": CONSTANT_CURRENT_A,
                               "evidence": result["evidence"]}
                        if result["evidence"]["status"] == "observed":
                            axis, vectors = result["axis"], result["vectors"]
                            gate_v, drain_current, gate_current = vectors
                            absolute_drain = [abs(current) for current in drain_current]
                            threshold = _constant_current_threshold(axis, absolute_drain, CONSTANT_CURRENT_A)
                            row["transfer"] = {
                                "sweep_axis_gate_voltage_min_V": min(gate_v),
                                "sweep_axis_gate_voltage_max_V": max(gate_v),
                                "drain_current_min_A": min(absolute_drain),
                                "drain_current_max_A": max(absolute_drain),
                                "gate_current_min_A": min(gate_current),
                                "gate_current_max_A": max(gate_current),
                                **threshold,
                                "interpretation": "Only a unique root is reported; missing/multiple/interval roots are inconclusive, not 0 V.",
                            }
                        rows.append(row)
    sensitivity_rows = _f4_sensitivity(rows)
    return rows, sensitivity_rows


def _f4_sensitivity(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["base"], row["polarity"], row["mode"], row["drain_bias_V"])
        groups.setdefault(key, []).append(row)
    summaries = []
    for (base, polarity, mode, drain_v), group in groups.items():
        by_e = {row["substrate_bias_V"]: row for row in group}
        summary = {
            "base": base, "polarity": polarity, "mode": mode,
            "drain_bias_V": drain_v,
            "crossings_by_substrate_bias_V": {},
            "delta_VGS_from_E0_by_substrate_bias_V": {},
            "backgate_sensitivity_dVGS_dE_V_per_V": None,
            "full_VGS_span_across_E_V": None,
        }
        if any(e not in by_e for e in SUBSTRATE_BIASES_V):
            summary.update(status="inconclusive", reason="one or more requested substrate-bias runs are absent")
            summaries.append(summary)
            continue
        bad_statuses = [row for row in group if row["evidence"]["status"] != "observed"]
        if bad_statuses:
            summary.update(status="inconclusive", reason="one or more simulator runs failed, timed out, or were truncated")
            summaries.append(summary)
            continue
        crossing_rows = {e: by_e[e]["transfer"] for e in SUBSTRATE_BIASES_V}
        for e, transfer in crossing_rows.items():
            summary["crossings_by_substrate_bias_V"][_spice(e)] = transfer["constant_current_VGS_V"]
        statuses = [transfer["constant_current_status"] for transfer in crossing_rows.values()]
        if any(status != "bracketed_single" for status in statuses):
            summary.update(status="inconclusive",
                           reason="constant-current crossing is not uniquely bracketed at every E bias; absent crossings are not zero")
            summaries.append(summary)
            continue
        zero = crossing_rows[0.0]["constant_current_VGS_V"]
        minus = crossing_rows[-0.1]["constant_current_VGS_V"]
        plus = crossing_rows[0.1]["constant_current_VGS_V"]
        summary["delta_VGS_from_E0_by_substrate_bias_V"] = {
            _spice(e): crossing_rows[e]["constant_current_VGS_V"] - zero
            for e in SUBSTRATE_BIASES_V
        }
        summary["backgate_sensitivity_dVGS_dE_V_per_V"] = (plus - minus) / 0.2
        summary["full_VGS_span_across_E_V"] = max(minus, zero, plus) - min(minus, zero, plus)
        summary.update(status="observed", interpretation=(
            "Simulator-only finite-difference sensitivity; not a safe-bias limit, calibration, or hardware discriminator."))
        summaries.append(summary)
    return summaries


def _nested(row: dict, *path: str):
    value = row.get("metrics", {})
    for segment in path:
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _comparisons(rows: list[dict], *, kind: str) -> list[dict]:
    grouped: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        if kind == "t7":
            key = (row["base"], row["polarity"], row["mode"], row["load_id"])
        else:
            key = (row["base"], row["nmode"], row["pmode"], row["load_id"])
        grouped.setdefault(key, {})[row["integration"]["id"]] = row
    if kind == "t7":
        fields = {
            "first_sample_output_voltage_V": ("initial_sample", "output_voltage_V"),
            "final_output_voltage_V": ("final", "output_voltage_V"),
            "final_drain_current_A": ("final", "drain_current_A"),
            "tail_output_rms_about_mean_V": ("tail_uniform", "output_voltage_rms_about_mean_V"),
            "tail_output_peak_to_peak_V": ("tail_uniform", "output_voltage_peak_to_peak_V"),
            "tail_drain_current_rms_about_mean_A": ("tail_uniform", "drain_current_rms_about_mean_A"),
            "tail_drain_current_peak_to_peak_A": ("tail_uniform", "drain_current_peak_to_peak_A"),
        }
    else:
        fields = {
            "final_output_voltage_V": ("final", "output_voltage_V"),
            "final_supply_current_A": ("final", "supply_current_A"),
            "tail_output_rms_about_mean_V": ("tail_uniform", "output_voltage_rms_about_mean_V"),
            "tail_output_peak_to_peak_V": ("tail_uniform", "output_voltage_peak_to_peak_V"),
            "tail_supply_current_rms_about_mean_A": ("tail_uniform", "supply_current_rms_about_mean_A"),
            "tail_supply_current_peak_to_peak_A": ("tail_uniform", "supply_current_peak_to_peak_A"),
            "switching_threshold_input_V": ("crossings", "input_voltage_at_output_falling_half_supply_V"),
            "settling_time_s": ("settling", "settling_time_s"),
        }
    comparisons = []
    for key, by_integration in grouped.items():
        reference = by_integration.get("trap_20ps")
        for comparison_id in ("trap_10ps", "gear_20ps"):
            candidate = by_integration.get(comparison_id)
            record = {"case": list(key), "reference_integration": "trap_20ps",
                      "comparison_integration": comparison_id, "deltas": {}}
            if reference is None or candidate is None:
                record.update(status="not_comparable", reason="one integration result is absent")
            elif (reference["evidence"]["status"] != "observed"
                  or candidate["evidence"]["status"] != "observed"):
                record.update(status="not_comparable",
                              reference_status=reference["evidence"]["status"],
                              comparison_status=candidate["evidence"]["status"],
                              reason="both numerical observations are required for a difference")
            else:
                for label, path in fields.items():
                    left = _nested(reference, *path)
                    right = _nested(candidate, *path)
                    record["deltas"][label] = None if left is None or right is None else right - left
                record.update(status="observed", interpretation=(
                    "Numerical integrator/maxstep disagreement is not model calibration or a basis for selecting SOIMOD."))
            comparisons.append(record)
    return comparisons


def _status_counts(groups: dict[str, list[dict]]) -> dict:
    result = {}
    aggregate = Counter()
    for name, rows in groups.items():
        counts = Counter(row["evidence"]["status"] for row in rows)
        result[name] = {status: counts.get(status, 0) for status in ("observed", "failed", "inconclusive")}
        aggregate.update(counts)
    result["aggregate"] = {status: aggregate.get(status, 0)
                            for status in ("observed", "failed", "inconclusive")}
    return result


def _candidate_metadata(candidates: list[dict], out: Path) -> list[dict]:
    rows = []
    for item in candidates:
        library = Path(item["library"])
        try:
            path = library.relative_to(out.resolve()).as_posix()
        except ValueError:
            path = library.as_posix()
        rows.append({"id": item["id"], "base": item["base"],
                     "nmode": item["nmode"], "pmode": item["pmode"],
                     "library": path, "sha256": item["sha256"],
                     "deltas_from_base": item["deltas_from_base"],
                     "qualified": item["qualified"]})
    return rows


def _fmt_number(value, digits: int = 6) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return "%.6g" % value


def _md_t7(rows: list[dict], comparisons: list[dict]) -> list[str]:
    lines = ["## F3 T7: separate single-switch free-output benches", "",
             "The source is 0 V for NMOS and 3 V for PMOS; the gate pulse starts at 100 ns, has 2 ns edges and 400 ns width, and returns within the 1 µs run. NMOS uses 0→3→0 V and PMOS 3→0→3 V. "
             "Each device has W/L=24/6 µm, explicit common `E=SUB=0 V`, no P terminal, and `.ic v(OUT)=3 V` for NMOS / 0 V for PMOS with normal operating-point startup (no UIC). "
             "All runs stop at 1 µs and pass both transient print step and the fourth `tran` maximum-step argument explicitly.", "",
             "| base | polarity | mode | load | integration | status | first sampled VOUT (V) | VOUT full min…max (V) | final VOUT (V) | final ID (A) | tail VOUT RMS / p-p (V) | tail ID RMS / p-p (A) | crossings VOUT/ID | settle (s) |",
             "|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        m = row.get("metrics", {})
        full, final, tail, settle = (m.get("full_excursion", {}), m.get("final", {}),
                                     m.get("tail_uniform", {}), m.get("settling", {}))
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s…%s | %s | %s | %s / %s | %s / %s | %s / %s | %s |" % (
            row["base"], row["polarity"], row["mode"], row["load_id"],
            row["integration"]["id"], row["evidence"]["status"],
            _fmt_number(m.get("initial_sample", {}).get("output_voltage_V")),
            _fmt_number(full.get("output_voltage_min_V")), _fmt_number(full.get("output_voltage_max_V")),
            _fmt_number(final.get("output_voltage_V")), _fmt_number(final.get("drain_current_A")),
            _fmt_number(tail.get("output_voltage_rms_about_mean_V")),
            _fmt_number(tail.get("output_voltage_peak_to_peak_V")),
            _fmt_number(tail.get("drain_current_rms_about_mean_A")),
            _fmt_number(tail.get("drain_current_peak_to_peak_A")),
            _fmt_number(tail.get("output_half_supply_crossings")),
            _fmt_number(tail.get("drain_current_zero_crossings")),
            _fmt_number(settle.get("settling_time_s"))))
    lines += ["", "### Integrator / max-step numerical differences", "",
              "Deltas are comparison minus trap/20 ps; a missing observation remains unavailable rather than zero. These are numerical sensitivities, not calibration.", "",
              "| base | polarity | mode | load | comparison | status | Δ first VOUT sample (V) | Δ final VOUT (V) | Δ tail VOUT RMS / p-p (V) | Δ tail ID RMS / p-p (A) |",
              "|---|---|---:|---|---|---:|---:|---:|---:|---:|"]
    for row in comparisons:
        case = row["case"]
        delta = row.get("deltas", {})
        lines.append("| %s | %s | %s | %s | %s vs trap/20ps | %s | %s | %s | %s / %s | %s / %s |" % (
            case[0], case[1], case[2], case[3], row["comparison_integration"], row["status"],
            _fmt_number(delta.get("first_sample_output_voltage_V")),
            _fmt_number(delta.get("final_output_voltage_V")),
            _fmt_number(delta.get("tail_output_rms_about_mean_V")),
            _fmt_number(delta.get("tail_output_peak_to_peak_V")),
            _fmt_number(delta.get("tail_drain_current_rms_about_mean_A")),
            _fmt_number(delta.get("tail_drain_current_peak_to_peak_A"))))
    lines += ["", "Simulator failures, timeouts, aborts, and truncated output remain in `dynamic.json` with their deck/log/data paths. "
              "Tail statistics use uniform 5 ps resampling over the final 100 ns. Current zero-crossings/sign flips alone do not prove stable oscillation.", ""]
    return lines


def _md_inverters(rows: list[dict], comparisons: list[dict]) -> list[str]:
    lines = ["## F3 INV: corrected inverter and FO1 loading", "",
             "The first stage is `MP OUT IN VDD SUB pch` plus `MN OUT IN 0 SUB nch`; `VSUB` drives shared E to 0 V. "
             "The real FO1 case adds a second inverter gate pair driven by OUT, not an estimated capacitor. The 457 fF case is only a representative lumped load, not real FO1.", "",
             "Frozen-base diagonal mode pairs are the reference; overlap-base runs cover all nine independent N/P pairs. "
             "Every path uses the same 0→3 V pulse, 1 µs stop, and the three explicit integration settings.", "",
             "| base | N/P modes | load | integration | status | VOUT min…max (V) | final VOUT (V) | tail RMS / p-p (V) | final supply I (A) | input V at falling VOUT=1.5 V | settling (s) |",
             "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        m = row.get("metrics", {})
        full, final, tail, crossings, settle = (m.get("full_excursion", {}), m.get("final", {}),
                                                m.get("tail_uniform", {}), m.get("crossings", {}),
                                                m.get("settling", {}))
        lines.append("| %s | %s/%s | %s | %s | %s | %s…%s | %s | %s / %s | %s | %s | %s |" % (
            row["base"], row["nmode"], row["pmode"], row["load_id"],
            row["integration"]["id"], row["evidence"]["status"],
            _fmt_number(full.get("output_voltage_min_V")), _fmt_number(full.get("output_voltage_max_V")),
            _fmt_number(final.get("output_voltage_V")),
            _fmt_number(tail.get("output_voltage_rms_about_mean_V")),
            _fmt_number(tail.get("output_voltage_peak_to_peak_V")),
            _fmt_number(final.get("supply_current_A")),
            _fmt_number(crossings.get("input_voltage_at_output_falling_half_supply_V")),
            _fmt_number(settle.get("settling_time_s"))))
    lines += ["", "### Integrator / max-step numerical differences", "",
              "Deltas are comparison minus trap/20 ps. A missing threshold is left unreported (`—`), never replaced by zero. These differences describe numerical sensitivity, not calibration.", "",
              "| base | N/P modes | load | comparison | status | Δ final VOUT (V) | Δ tail RMS / p-p (V) | Δ tail supply-I RMS / p-p (A) | Δ switching threshold VIN (V) | Δ settle (s) |",
              "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for row in comparisons:
        case = row["case"]
        delta = row.get("deltas", {})
        lines.append("| %s | %s/%s | %s | %s vs trap/20ps | %s | %s | %s / %s | %s / %s | %s | %s |" % (
            case[0], case[1], case[2], case[3], row["comparison_integration"], row["status"],
            _fmt_number(delta.get("final_output_voltage_V")),
            _fmt_number(delta.get("tail_output_rms_about_mean_V")),
            _fmt_number(delta.get("tail_output_peak_to_peak_V")),
            _fmt_number(delta.get("tail_supply_current_rms_about_mean_A")),
            _fmt_number(delta.get("tail_supply_current_peak_to_peak_A")),
            _fmt_number(delta.get("switching_threshold_input_V")),
            _fmt_number(delta.get("settling_time_s"))))
    return lines + ["", "Threshold means interpolated input voltage at the first falling output half-supply crossing, when observed. No missing crossing is treated as a threshold value.", ""]


def _md_f4(rows: list[dict], sensitivity: list[dict]) -> list[str]:
    lines = ["## F4: offline substrate-bias sensitivity only", "",
             "All runs use source=0 V, explicit four-terminal devices with E swept through −0.1/0/+0.1 V, and identical polarity-specific VGS/VDS references across modes. "
             "NMOS gate sweeps 0→3 V with VDS=+0.05/+1 V; PMOS sweeps −3→0 V with VDS=−0.05/−1 V. "
             "The 1 µA constant-current VGS crossing is reported only if uniquely bracketed by the sweep.", "",
             "| base | polarity | mode | E (V) | VDS (V) | status | abs(ID) min…max (A) | constant-current crossing VGS (V) | crossing status |",
             "|---|---|---:|---:|---:|---|---:|---:|---|"]
    for row in rows:
        transfer = row.get("transfer", {})
        lines.append("| %s | %s | %s | %s | %s | %s | %s…%s | %s | %s |" % (
            row["base"], row["polarity"], row["mode"], _fmt_number(row["substrate_bias_V"]),
            _fmt_number(row["drain_bias_V"]), row["evidence"]["status"],
            _fmt_number(transfer.get("drain_current_min_A")),
            _fmt_number(transfer.get("drain_current_max_A")),
            _fmt_number(transfer.get("constant_current_VGS_V")),
            transfer.get("constant_current_status", "unavailable")))
    lines += ["", "### Back-gate sensitivity summaries", "",
              "| base | polarity | mode | VDS (V) | status | VGS(E=−0.1/0/+0.1) (V) | ΔVGS(+0.1−0) (V) | ΔVGS(−0.1−0) (V) | finite difference dVGS/dE (V/V) | full span (V) | reason |",
              "|---|---|---:|---:|---|---|---:|---:|---:|---:|---|"]
    for row in sensitivity:
        crossings = row.get("crossings_by_substrate_bias_V", {})
        deltas = row.get("delta_VGS_from_E0_by_substrate_bias_V", {})
        lines.append("| %s | %s | %s | %s | %s | %s / %s / %s | %s | %s | %s | %s | %s |" % (
            row["base"], row["polarity"], row["mode"], _fmt_number(row["drain_bias_V"]),
            row["status"], _fmt_number(crossings.get("-0.1")),
            _fmt_number(crossings.get("0")), _fmt_number(crossings.get("0.1")),
            _fmt_number(deltas.get("0.1")), _fmt_number(deltas.get("-0.1")),
            _fmt_number(row.get("backgate_sensitivity_dVGS_dE_V_per_V")),
            _fmt_number(row.get("full_VGS_span_across_E_V")), row.get("reason", "—")))
    return lines + ["", "Missing or nonunique constant-current crossings and any failed bias run make the corresponding sensitivity inconclusive; they are never imputed as 0 V.", ""]


def _md_failures(groups: dict[str, list[dict]]) -> list[str]:
    failures = [(bench, row) for bench, rows in groups.items()
                for row in rows if row["evidence"]["status"] != "observed"]
    lines = ["## Non-observed runs", ""]
    if not failures:
        return lines + ["No failed, timed-out, aborted, or truncated simulator runs were recorded.", ""]
    lines += ["Every non-observed simulator result is listed; full evidence remains in JSON and retained dynamic deck/log/data files.", "",
              "| bench | run | status | reason | deck | log | data |",
              "|---|---|---|---|---|---|---|"]
    for bench, row in failures:
        evidence = row["evidence"]
        lines.append("| %s | %s | %s | %s | `%s` | `%s` | `%s` |" % (
            bench, row["id"], evidence["status"],
            str(evidence.get("reason", "no reason supplied")).replace("|", "\\|"),
            evidence.get("deck", "—"), evidence.get("log", "—"), evidence.get("data", "—")))
    return lines + [""]


def _markdown(report: dict) -> str:
    sim = report["simulator"]
    lines = ["# B4SOI depletion-mode dynamic audit (F3/F4)", "",
             "This is a numerical sensitivity audit. It does not select or promote a SOIMOD candidate.", "",
             "- Simulator: `%s` — %s" % (sim["binary"], sim["version"].replace("\n", " ")),
             "- Source inputs: frozen card, overlap candidate, baseline manifest and measurements are SHA-256 recorded in `dynamic.json`.",
             "- Candidate libraries: %d, all generated under `%s`; every entry is unqualified and SOIMOD-only." %
             (len(report["candidate_libraries"]), report["candidate_directory"]),
             "- Execution counts: %s" % ", ".join("%s=%s" % (key, value)
                                                   for key, value in report["status_counts"]["aggregate"].items()),
             "", "## Candidate library hashes", "",
             "| id | base | N mode | P mode | SHA-256 | qualified |",
             "|---|---|---:|---:|---|---|"]
    for item in report["candidate_libraries"]:
        lines.append("| %s | %s | %s | %s | `%s` | %s |" % (
            item["id"], item["base"], item["nmode"], item["pmode"], item["sha256"], item["qualified"]))
    lines += ["", "## Execution status counts", "", "| bench | observed | failed | inconclusive |", "|---|---:|---:|---:|"]
    for name in ("t7", "inverter", "f4"):
        counts = report["status_counts"][name]
        lines.append("| %s | %s | %s | %s |" % (name, counts["observed"], counts["failed"], counts["inconclusive"]))
    lines += ["", "Failures are retained with simulator evidence; an observed run means only that the requested numerical deck completed and passed the shared output checks.", ""]
    lines += _md_failures({"F3 T7": report["f3_t7"],
                           "F3 inverter": report["f3_inverter"],
                           "F4 transfer": report["f4_transfers"]})
    lines += ["", "Current branch convention: T7 `i(VDSENSE)` is positive OUT→DRAIN; inverter `i(VPWR)` is positive VDD→0 and `i(VIN)` IN→0; F4 uses `abs(i(VD))` for its constant-current threshold. All reported currents are amperes. Tail RMS is about the uniformly sampled tail mean.", ""]
    lines += _md_t7(report["f3_t7"], report["f3_t7_comparisons"])
    lines += _md_inverters(report["f3_inverter"], report["f3_inverter_comparisons"])
    lines += _md_f4(report["f4_transfers"], report["f4_backgate_sensitivity"])
    lines += ["## Interpretation boundaries", "",
              "- F3 output and current excursions, tail RMS/p-p and sign-crossing counts are simulator observations. Current sign flips alone are not evidence of stable oscillation; no candidate is selected because it lacks oscillation.",
              "- Cross-method/max-step disagreement is numerical sensitivity, not calibration. Frozen and overlap bases plus independent N/P mode combinations remain unqualified.",
              "- FDMOD remains inherited at 0. No FDMOD=1/CDSBS/VSCE formulation is enabled; inherited defaults and parameter inventories are not calibration.",
              "- **F4 hardware qualification is BLOCKED.** The repository has no manufacturer-approved E/source/body reliability bounds; the exact missing input groups are listed below. The ±0.1 V simulator sweep is not a lab-safe or qualified discriminator and is not hardware advice.",
              "- **F5 silicon fit is BLOCKED.** Revision-specific polarity-resolved silicon and back-gate/C-V inputs plus qualified process parameters are missing. The simulator-only sweep cannot establish a silicon fit.",
              "", "### Exact missing process inputs", ""]
    for group, items in report["missing_process_inputs"].items():
        lines.append("- **%s:** %s" % (group.replace("_", " "), "; ".join(items)))
    lines += ["", "Raw decks, logs and wrdata remain in the `dynamic/` directory. JSON contains evidence paths and derived metrics, not raw transient vectors.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default="ngspice", help="ngspice executable (default: ngspice on PATH)")
    parser.add_argument("--out", type=Path, default=common.ROOT / "build" / "ngspice-depletion",
                        help="audit output directory (default: build/ngspice-depletion)")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-deck timeout in seconds")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    binary = shutil.which(args.ngspice)
    if binary is None and Path(args.ngspice).is_file():
        binary = str(Path(args.ngspice).resolve())
    if binary is None:
        parser.error("ngspice executable not found: %s" % args.ngspice)

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    candidate_dir = out / "candidates"
    dynamic_work = out / "dynamic"
    candidates = common.generate_candidates(candidate_dir, mixed=True)
    candidate_index = _candidate_index(candidates)
    simulator = {"binary": binary, "version": common.simulator_version(binary)}

    t7_rows = _run_t7(binary, dynamic_work, candidate_index, args.timeout)
    inverter_rows = _run_inverters(binary, dynamic_work, candidates, args.timeout)
    f4_rows, f4_sensitivity = _run_f4(binary, dynamic_work, candidate_index, args.timeout)
    report = {
        "schema_version": 1,
        "scope": "F3 T7 and corrected inverter transient sensitivity; F4 simulator-only substrate-bias transfer sensitivity",
        "simulator": simulator,
        "sources": common.source_hashes(),
        "candidate_directory": candidate_dir.relative_to(out).as_posix(),
        "candidate_libraries": _candidate_metadata(candidates, out),
        "settings": {
            "physical_supplies_V": [0.0, common.VDD],
            "device_geometry_um": {"W": common.W_UM, "L": common.L_UM},
            "transient_stop_s": STOP_S,
            "gate_pulse_delay_s": SWITCH_S,
            "gate_pulse_edge_s": 2e-9,
            "gate_pulse_width_s": 400e-9,
            "gate_pulse_period_s": 1e-6,
            "transient_integrations": list(INTEGRATIONS),
            "tail_uniform_window_s": TAIL_WINDOW_S,
            "tail_uniform_interval_s": TAIL_INTERVAL_S,
            "settling_band_V": SETTLE_TOLERANCE_V,
            "constant_current_reference_A": CONSTANT_CURRENT_A,
            "t7_loads": list(T7_LOADS),
            "inverter_loads": list(INVERTER_LOADS),
            "substrate_biases_V": list(SUBSTRATE_BIASES_V),
            "drain_biases_V_by_polarity": DRAIN_BIASES_V,
        },
        "current_conventions": {
            "t7_drain_current": "i(VDSENSE), positive branch direction OUT to DRAIN",
            "t7_gate_current": "i(VG), positive branch direction G to 0",
            "inverter_supply_current": "i(VPWR), positive branch direction VDD to 0",
            "inverter_input_current": "i(VIN), positive branch direction IN to 0",
            "f4_constant_current": "abs(i(VD)); the source branch direction is D to 0",
            "tail_rms": "RMS about the arithmetic mean of values linearly interpolated onto a uniform 5 ps grid; saved full-run extrema are also reported",
        },
        "status_counts": _status_counts({"t7": t7_rows, "inverter": inverter_rows, "f4": f4_rows}),
        "f3_t7": t7_rows,
        "f3_t7_comparisons": _comparisons(t7_rows, kind="t7"),
        "f3_inverter": inverter_rows,
        "f3_inverter_comparisons": _comparisons(inverter_rows, kind="inverter"),
        "f4_transfers": f4_rows,
        "f4_backgate_sensitivity": f4_sensitivity,
        "hardware_qualification": {
            "status": "blocked",
            "reason": "Manufacturer-approved E/source/body reliability limits are not present in the repository.",
        },
        "f5_silicon_fit": {
            "status": "blocked",
            "reason": "Revision-specific polarity-resolved silicon, back-gate/C-V, and qualified process inputs are unavailable.",
        },
        "missing_process_inputs": MISSING_PROCESS_INPUTS,
        "interpretation_boundaries": [
            "SOIMOD mode changes are numerical sensitivities; no candidate is selected or promoted.",
            "Current sign changes are not by themselves proof of stable oscillation.",
            "457 fF is a representative lumped load, not real FO1; real FO1 is a second inverter gate pair with shared E.",
            "F4 is offline simulator-only math and provides no hardware-safe/qualified discriminator or advice.",
            "FDMOD remains inherited at 0; no uncalibrated FDMOD=1/CDSBS/VSCE overrides are applied.",
        ],
    }
    common.save_json(out / "dynamic.json", report)
    (out / "dynamic.md").write_text(_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
