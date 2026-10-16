#!/usr/bin/env python3
"""Collect fixed-run F0/F1/F2 evidence for B4SOI depletion-mode candidates."""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import b4soi_depletion as common
import regress_b4soi_stage1_iv as iv


DEFAULT_OUT = common.ROOT / "build" / "ngspice-depletion"
DEFAULT_STEP_V = 0.001
FREQ_HZ = 1000.0
AC_AMP_V = 1.0
RAMP_STOP_S = 1e-3
RAMP_STEP_S = 2e-6
CAL_CAP_F = 100e-15
TERM_INDEX = {name: index for index, name in enumerate(common.TERMINALS)}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?"


def _relative(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


def _relative_evidence(evidence, out):
    for field in ("deck", "log", "data"):
        if evidence.get(field):
            evidence[field] = _relative(evidence[field], out)
    return evidence


def _status_counts(items):
    counts = Counter(item.get("status", "inconclusive") for item in items)
    return {
        "total": len(items),
        "observed": counts.get("observed", 0),
        "failed": counts.get("failed", 0),
        "inconclusive": counts.get("inconclusive", 0),
    }


def _value_from_token(token):
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError:
        return token
    return value if math.isfinite(value) else token


def _showmod_values(log_path):
    """Parse exposed numeric model fields while retaining the untouched full log."""
    fields = {name.upper() for name in common.FD_INPUTS}
    values = {}
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        for match in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*(" + NUMBER + r")\b", line
        ):
            key = match.group(1).upper()
            if key in fields:
                value = _value_from_token(match.group(2))
                if isinstance(value, float):
                    values[key] = value
        # Accept showmod's two-column parameter/value form as well.
        match = re.match(
            r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+(" + NUMBER + r")\s*$", line
        )
        if match:
            key = match.group(1).upper()
            if key in fields:
                value = _value_from_token(match.group(2))
                if isinstance(value, float):
                    values[key] = value
    return values


def _inventory(candidate, device, source_params, effective):
    model = "nch" if device == "nmos" else "pch"
    requested_mode = candidate["nmode"] if device == "nmos" else candidate["pmode"]
    entries = []
    for name in common.FD_INPUTS:
        key = name.upper()
        base_explicit = key in source_params[model]
        if key == "SOIMOD":
            candidate_explicit = True
            source_value = source_params[model].get(key)
            candidate_value = requested_mode
            source_kind = "generated_candidate_explicit_mode"
        else:
            candidate_explicit = base_explicit
            source_value = source_params[model].get(key)
            candidate_value = source_value
            source_kind = "base_card_explicit_inherited" if base_explicit else "not_explicit_in_base_card"
        observed = effective.get(key)
        if observed is not None:
            value_state = "effective_value_exposed_by_showmod"
            effective_origin = (
                "explicit_candidate_or_base_card_input"
                if candidate_explicit else "simulator_default_or_derived_value"
            )
        elif candidate_explicit:
            value_state = "source_explicit_but_effective_value_not_exposed"
            effective_origin = "not_exposed_by_showmod"
        else:
            value_state = "not_exposed_absent_or_internally_derived_unresolved"
            effective_origin = "not_exposed_by_showmod"
        entries.append({
            "parameter": key,
            "base_card_explicit": base_explicit,
            "candidate_explicit": candidate_explicit,
            "source_provenance": source_kind,
            "base_card_value": source_value,
            "candidate_input_value": candidate_value,
            "effective_value": observed,
            "effective_origin": effective_origin,
            "value_state": value_state,
        })
    return entries


def _assertion(parameter, expected, effective):
    actual = effective.get(parameter.upper())
    if actual is None:
        return {
            "parameter": parameter.upper(), "expected": expected,
            "actual": None, "status": "not_observable",
            "reason": "field was not exposed in retained showmod output",
        }
    return {
        "parameter": parameter.upper(), "expected": expected,
        "actual": actual,
        "status": "observed" if math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
        else "failed",
    }


def _op_deck(candidate, model, gate_v, drain_v, name, inventory=False):
    lines = common.header(candidate["library"], "F0 operating-point / full showmod inventory: " + name)
    lines += [
        "Vg g 0 %s" % common.fmt(gate_v),
        "Vd d 0 %s" % common.fmt(drain_v),
        "Vs s 0 0",
        "Ve e 0 0",
        "MTEST d g s e %s W=%su L=%su" % (model, common.fmt(common.W_UM), common.fmt(common.L_UM)),
    ]
    lines += common.control([
        "op",
        *(["showmod mtest"] if inventory else []),
        "wrdata %s.dat v(g) v(d) v(s) v(e) i(Vd)" % name,
    ])
    return lines


def run_f0(candidates, source_params, ngspice, work, out, timeout):
    runs = []
    for candidate in candidates:
        for device, polarity in (("nmos", 1.0), ("pmos", -1.0)):
            model = "nch" if device == "nmos" else "pch"
            mode = candidate["nmode"] if device == "nmos" else candidate["pmode"]
            name = "%s_%s_op" % (candidate["id"], device)
            gate_v = polarity * 1.5
            drain_v = polarity * 0.2
            record = {
                "candidate_id": candidate["id"], "base": candidate["base"],
                "device": device, "model": model, "requested_soimod": mode,
                "bias_V": {"vg": gate_v, "vd": drain_v, "vs": 0.0, "ve": 0.0},
                "geometry_um": {"w": common.W_UM, "l": common.L_UM},
                "status": "failed",
            }
            try:
                run = common.run_deck(
                    ngspice, work, name, _op_deck(candidate, model, gate_v, drain_v, name),
                    vectors=5, timeout=timeout,
                )
                evidence = _relative_evidence(run["evidence"], out)
                record["evidence"] = evidence
                record["status"] = evidence["status"]
                if evidence["status"] == "observed" and len(run["axis"]) != 1:
                    record["status"] = "failed"
                    evidence["reason"] = "expected one operating-point wrdata row"
                inventory_name = name + "_showmod"
                inventory_run = common.run_deck(
                    ngspice, work, inventory_name,
                    _op_deck(candidate, model, gate_v, drain_v, inventory_name, inventory=True),
                    vectors=5, timeout=timeout,
                )
                inventory_evidence = _relative_evidence(inventory_run["evidence"], out)
                record["inventory_evidence"] = inventory_evidence
                log_path = Path(inventory_evidence["log"])
                if not log_path.is_absolute():
                    log_path = Path(out) / log_path
                effective = _showmod_values(log_path)
                record["showmod_log"] = inventory_evidence["log"]
                record["unqueryable_fields"] = re.findall(
                    r"(?m)^\s*(\w+)\s+<<NAN,\s*error\s*=\s*\d+>>",
                    log_path.read_text(encoding="utf-8", errors="replace"),
                )
                record["effective_inventory"] = _inventory(
                    candidate, device, source_params[candidate["base"]], effective
                )
                assertions = [
                    _assertion("SOIMOD", float(mode), effective),
                    _assertion("FDMOD", 0.0, effective),
                ]
                record["assertions"] = assertions
                if any(assertion["status"] == "failed" for assertion in assertions):
                    record["status"] = "failed"
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                record["status"] = "failed"
                record["reason"] = str(error)
            runs.append(record)
    return {
        "purpose": "Operating point and retained full showmod inventory; not calibration.",
        "showmod_retention": "The complete simulator output is retained at each evidence.log path.",
        "assertion_policy": "Requested SOIMOD and inherited FDMOD=0 are checked only when showmod exposes them.",
        "runs": runs,
        "status_counts": _status_counts(runs),
        "inventory_status_counts": _status_counts(
            [record.get("inventory_evidence", {"status": "inconclusive"}) for record in runs]),
    }


def run_f0_body_syntax(candidates, ngspice, work, out, timeout):
    """Optional P is simulator syntax only, never an ordinary-device body tie."""
    records = []
    for candidate in candidates:
        for device, model, polarity in (("nmos", "nch", 1.0), ("pmos", "pch", -1.0)):
            name = "%s_%s_optional_p_syntax" % (candidate["id"], device)
            deck = common.header(candidate["library"], "F0 fifth-P syntax diagnostic only") + [
                "Vg g 0 %s" % common.fmt(polarity * 1.5),
                "Vd d 0 %s" % common.fmt(polarity * 0.2),
                "Vs s 0 0", "Ve e 0 0", "Vp p 0 0",
                "MTEST d g s e p %s W=%su L=%su" % (
                    model, common.fmt(common.W_UM), common.fmt(common.L_UM)),
            ] + common.control([
                "op", "wrdata %s.dat v(g) v(d) v(s) v(e) v(p) i(Vp)" % name,
            ])
            run = common.run_deck(ngspice, work, name, deck, vectors=6, timeout=timeout)
            record = {
                "candidate_id": candidate["id"], "device": device,
                "status": run["evidence"]["status"],
                "evidence": _relative_evidence(run["evidence"], out),
                "terminals": ["D", "G", "S", "E", "P"],
                "interpretation": (
                    "Direct simulator syntax only. This intentional diagnostic P contact is "
                    "not inserted into any ordinary-device bench. Physical LVS supports only "
                    "the NMOS body-tie class; postlayout assembly supports neither body-contact class. "
                    "Ideal-FD mode may ignore body-contact equations."
                ),
            }
            if record["status"] == "observed":
                if len(run["axis"]) != 1:
                    record.update(status="failed", reason="expected exactly one OP row")
                else:
                    record["body_contact_current_A"] = run["vectors"][5][0]
            records.append(record)
    return {"runs": records, "status_counts": _status_counts(records)}


def _sweep_bounds(points, step):
    start = math.floor(min(axis for axis, _ in points) / step) * step
    stop = math.ceil(max(axis for axis, _ in points) / step) * step
    if start == stop:
        stop = start + step
    return start, stop


def _adapt_iv_deck(candidate, key, points, step, name):
    old_deck, _start = iv.make_sweep_deck(key, points, step, candidate["library"])
    model = "nch" if key[0] == "nmos" else "pch"
    deck = common.header(candidate["library"], "F1 fixed I-V curve: %s / %s" % (candidate["id"], key[2]))
    for line in old_deck.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith(".lib ") or stripped.startswith(".options "):
            continue
        if stripped.lower() == "set wr_vecnames":
            continue
        if stripped.startswith("VDS "):
            deck.append(line)
            deck.append("Ve e 0 0")
        elif stripped.startswith("MTEST "):
            deck.append("MTEST drain gate 0 e %s L=%su W=%su" % (
                model, common.fmt(common.L_UM), common.fmt(common.W_UM)
            ))
        elif stripped.startswith("wrdata "):
            deck.append("wrdata %s.dat i(VDS)" % name)
        else:
            deck.append(line)
    return deck


def _curve_metadata(key, candidate):
    device, family, curve, bias_axis, bias = key
    return {
        "candidate_id": candidate["id"], "base": candidate["base"],
        "nmode": candidate["nmode"], "pmode": candidate["pmode"],
        "device": device, "family": family, "curve": curve,
        "bias_axis": bias_axis, "bias_V": bias,
    }


def _metric_name(family):
    return "relative_rms_percent" if family == "output" else "log10_current_rms_decades"


def _curve_metric(key, points, sim_axis, sim_current):
    predicted = [abs(iv.interpolate(sim_axis, sim_current, axis)) for axis, _ in points]
    if any(value <= 0.0 or not math.isfinite(value) for value in predicted):
        raise ValueError("simulated curve contains zero/non-positive sampled current")
    measured = [current for _, current in points]
    if key[1] == "output":
        errors = [(simulation - observation) / observation
                  for simulation, observation in zip(predicted, measured)]
        metric = math.sqrt(sum(error * error for error in errors) / len(errors)) * 100.0
    else:
        errors = [math.log10(simulation) - math.log10(observation)
                  for simulation, observation in zip(predicted, measured)]
        metric = math.sqrt(sum(error * error for error in errors) / len(errors))
    point_errors = [
        {
            "axis_V": axis, "measured_current_magnitude_A": observation,
            "predicted_current_magnitude_A": simulation,
            "relative_error" if key[1] == "output" else "log10_error_decades": error,
        }
        for (axis, observation), simulation, error in zip(points, predicted, errors)
    ]
    return metric, point_errors, predicted


def run_f1(candidates, curves, baseline, ngspice, work, out, step, timeout):
    runs = []
    prediction_lookup = {}
    for candidate in candidates:
        for key, points in sorted(curves.items()):
            safe_curve = re.sub(r"[^A-Za-z0-9_-]+", "_", key[2])
            name = "%s_%s" % (candidate["id"], safe_curve)
            meta = _curve_metadata(key, candidate)
            meta.update({"status": "failed", "samples": len(points), "sweep_step_V": step})
            try:
                deck = _adapt_iv_deck(candidate, key, points, step, name)
                _start, stop = _sweep_bounds(points, step)
                run = common.run_deck(
                    ngspice, work, name, deck, vectors=1, stop=stop, timeout=timeout,
                )
                evidence = _relative_evidence(run["evidence"], out)
                meta["evidence"] = evidence
                meta["status"] = evidence["status"]
                if evidence["status"] == "observed":
                    sim_current = [abs(value) for value in run["vectors"][0]]
                    metric, point_errors, predicted = _curve_metric(
                        key, points, run["axis"], sim_current
                    )
                    metric_name = _metric_name(key[1])
                    meta["metric_name"] = metric_name
                    meta["metric_value"] = metric
                    meta["point_errors"] = point_errors
                    prediction_lookup[(candidate["id"], key[2])] = predicted
                else:
                    meta["reason"] = evidence.get("reason", "simulator did not produce an observed run")
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                meta["status"] = "failed"
                meta["reason"] = str(error)
            runs.append(meta)

    run_by_key = {(run["candidate_id"], run["curve"]): run for run in runs}
    baseline_checks = []
    expected_curves = baseline.get("curves", {})
    tolerance_map = baseline.get("metric_absolute_tolerance", {})
    for base_id in ("frozen_n0_p0", "overlap_n0_p0"):
        for key, _points in sorted(curves.items()):
            curve = key[2]
            actual = run_by_key[(base_id, curve)]
            check = {"candidate_id": base_id, "curve": curve, "comparison": "frozen_baseline"}
            expected = expected_curves.get(curve)
            metric_name = _metric_name(key[1])
            if actual.get("status") != "observed" or expected is None:
                check.update(status="inconclusive", reason="curve result or frozen baseline metric unavailable")
            else:
                tolerance = float(tolerance_map[metric_name])
                delta = abs(actual["metric_value"] - float(expected[metric_name]))
                sample_match = actual["samples"] == int(expected["samples"])
                check.update({
                    "status": "observed" if sample_match and delta <= tolerance else "failed",
                    "metric_name": metric_name, "actual": actual["metric_value"],
                    "baseline": float(expected[metric_name]), "absolute_delta": delta,
                    "absolute_tolerance": tolerance, "samples_match": sample_match,
                })
            baseline_checks.append(check)

    overlap_parity = []
    curve_names = sorted(key[2] for key in curves)
    for curve in curve_names:
        frozen = prediction_lookup.get(("frozen_n0_p0", curve))
        overlap = prediction_lookup.get(("overlap_n0_p0", curve))
        check = {
            "curve": curve, "comparison": "mode0_frozen_vs_overlap_sampled_current",
            "relative_tolerance": 1e-8,
        }
        if frozen is None or overlap is None or len(frozen) != len(overlap):
            check.update(status="inconclusive", reason="one or both mode0 curves were not observed")
        else:
            relative = [
                abs(a - b) / max(abs(a), abs(b), 1e-300)
                for a, b in zip(frozen, overlap)
            ]
            maximum = max(relative, default=0.0)
            check.update({
                "status": "observed" if maximum <= check["relative_tolerance"] else "failed",
                "sample_count": len(frozen), "max_relative_current_delta": maximum,
            })
        overlap_parity.append(check)

    return {
        "purpose": "Per-curve fixed I-V evaluations against digitized graph samples; not FD validation.",
        "curve_count_per_candidate": len(curves),
        "total_curve_runs": len(runs),
        "metric_conventions": {
            "output": "relative RMS current-magnitude error in percent",
            "transfer": "RMS log10 current-magnitude error in decades",
            "pmos_bias": "signed digitized bias and sweep axes are preserved; drain current is compared by magnitude",
            "fit": "none",
        },
        "runs": runs,
        "mode0_frozen_baseline_checks": baseline_checks[:len(curves)],
        "mode0_overlap_baseline_checks": baseline_checks[len(curves):],
        "mode0_frozen_overlap_parity_checks": overlap_parity,
        "status_counts": {
            "curve_runs": _status_counts(runs),
            "baseline_checks": _status_counts(baseline_checks),
            "frozen_overlap_parity_checks": _status_counts(overlap_parity),
        },
    }


def _bias_cases(polarity):
    return [
        {
            "case_id": "off_gate_forward_0p2",
            "region": "off_gate_bias",
            "vgs": 0.0, "vd": polarity * 0.2, "vs": 0.0,
            "mirror": True,
        },
        {
            "case_id": "common_gate_vgs_plus_vds_0p2",
            "region": "small_vds_common_gate_bias",
            "vgs": polarity * 1.5, "vd": 0.2, "vs": 0.0,
            "mirror": True,
        },
        {
            "case_id": "common_gate_vgs_minus_vds_0p2",
            "region": "small_vds_common_gate_bias",
            "vgs": polarity * 1.5, "vd": -0.2, "vs": 0.0,
            "mirror": True,
        },
        {
            "case_id": "saturation_operating_polarity",
            "region": "saturation_intended_operating_polarity",
            "vgs": polarity * 3.0, "vd": polarity * 3.0, "vs": 0.0,
            "mirror": True,
        },
        {
            "case_id": "qs_endpoint_vds_zero",
            "region": "quasi_static_derivative_comparison_endpoint",
            "vgs": polarity * 3.0, "vd": 0.0, "vs": 0.0,
            "mirror": False,
        },
    ]


def _ac_deck(candidate, model, bias, name):
    deck = common.header(candidate["library"], "F2 signed 4x4 AC terminal matrix: " + name)
    for drive in common.TERMINALS:
        nodes = {terminal: terminal + "_" + drive for terminal in common.TERMINALS}
        dc = {"g": bias["vgs"], "d": bias["vd"], "s": bias["vs"], "e": bias["ve"]}
        for terminal in common.TERMINALS:
            source = "V%s_%s" % (drive, terminal)
            deck.append("%s %s 0 %s ac %s" % (
                source, nodes[terminal], common.fmt(dc[terminal]),
                common.fmt(AC_AMP_V if terminal == drive else 0.0),
            ))
        deck.append("M%s %s %s %s %s %s W=%su L=%su" % (
            drive, nodes["d"], nodes["g"], nodes["s"], nodes["e"], model,
            common.fmt(common.W_UM), common.fmt(common.L_UM),
        ))
    currents = " ".join(
        "i(V%s_%s)" % (drive, terminal)
        for drive in common.TERMINALS for terminal in common.TERMINALS
    )
    deck += common.control([
        "set filetype=ascii",
        "ac lin 1 %s %s" % (common.fmt(FREQ_HZ), common.fmt(FREQ_HZ)),
        "wrdata %s.dat %s" % (name, currents),
    ])
    return deck


def _matrix_residuals(matrix):
    rows = {}
    columns = {}
    tiny = 1e-300
    for index, terminal in enumerate(common.TERMINALS):
        row_sum = sum(matrix[index])
        row_scale = sum(abs(value) for value in matrix[index])
        column = [matrix[row][index] for row in range(len(common.TERMINALS))]
        column_sum = sum(column)
        column_scale = sum(abs(value) for value in column)
        rows[terminal] = {
            "absolute_residual_fF": abs(row_sum),
            "signed_residual_fF": row_sum,
            "normalized_residual": abs(row_sum) / max(row_scale, tiny),
        }
        columns[terminal] = {
            "absolute_residual_fF": abs(column_sum),
            "signed_residual_fF": column_sum,
            "normalized_residual": abs(column_sum) / max(column_scale, tiny),
        }
    return {"row_sums": rows, "column_sums": columns}


def _complex_json(value):
    return {"real": value.real, "imag": value.imag}


def _run_ac_matrix(candidate, device, polarity, case, ngspice, work, out, timeout, orientation):
    model = "nch" if device == "nmos" else "pch"
    case_id = case["case_id"]
    if orientation == "ds_swapped":
        case_id += "_ds_swapped"
    name = "%s_%s_%s" % (candidate["id"], device, case_id)
    bias = {"vgs": case["vgs"], "vd": case["vd"], "vs": case["vs"], "ve": 0.0}
    record = {
        "candidate_id": candidate["id"], "base": candidate["base"],
        "device": device, "polarity_sign": polarity,
        "case_id": case_id, "base_case_id": case["case_id"],
        "orientation": orientation, "region_intent": case["region"],
        "bias_V": {"vg": bias["vgs"], "vd": bias["vd"],
                   "vs": bias["vs"], "ve": bias["ve"]},
        "frequency_Hz": FREQ_HZ, "status": "failed",
    }
    try:
        run = common.run_deck(
            ngspice, work, name, _ac_deck(candidate, model, bias, name),
            vectors=16, complex_data=True, timeout=timeout,
        )
        evidence = _relative_evidence(run["evidence"], out)
        record["evidence"] = evidence
        record["status"] = evidence["status"]
        if evidence["status"] != "observed":
            record["reason"] = evidence.get("reason", "AC run was not observed")
            return record
        if len(run["axis"]) != 1 or not math.isclose(run["axis"][0], FREQ_HZ, rel_tol=1e-9, abs_tol=1e-9):
            record["status"] = "failed"
            record["reason"] = "expected exactly one AC row at 1 kHz"
            evidence["reason"] = record["reason"]
            return record
        y = [[0j for _ in common.TERMINALS] for _ in common.TERMINALS]
        for drive_index in range(len(common.TERMINALS)):
            for terminal_index in range(len(common.TERMINALS)):
                # Source branch current is positive out of the driven node; its negative is current into the device.
                y[terminal_index][drive_index] = -run["vectors"][drive_index * 4 + terminal_index][0] / AC_AMP_V
        omega = 2.0 * math.pi * FREQ_HZ
        capacitance = [[y[row][col].imag / omega * 1e15
                        for col in range(len(common.TERMINALS))]
                       for row in range(len(common.TERMINALS))]
        record["current_convention"] = "I_terminal_into_device = -I(Vterminal); Y=I_into_device/Vdrive"
        record["matrix_order"] = {"rows": list(common.TERMINALS), "columns": list(common.TERMINALS)}
        record["admittance_S"] = [[_complex_json(y[row][col]) for col in range(4)] for row in range(4)]
        record["capacitance_fF_signed"] = capacitance
        record["kcl_residuals"] = _matrix_residuals(capacitance)
        record["capacitance_sign_convention"] = "Cij = imag(Yij)/(2*pi*f), signed; no elementwise absolute values"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        record["status"] = "failed"
        record["reason"] = str(error)
    return record


def _compare_mirror(original, swapped):
    check = {
        "candidate_id": original["candidate_id"], "device": original["device"],
        "base_case_id": original["base_case_id"],
        "comparison": "actual D/S applied-voltage exchange; permute D/S row and column labels",
    }
    if original.get("status") != "observed" or swapped.get("status") != "observed":
        check.update(status="inconclusive", reason="one or both AC matrices were not observed")
        return check
    permutation = [TERM_INDEX[name] for name in ("g", "s", "d", "e")]
    expected_c = original["capacitance_fF_signed"]
    actual_c = swapped["capacitance_fF_signed"]
    delta_c = [[
        actual_c[row][col] - expected_c[permutation[row]][permutation[col]]
        for col in range(4)
    ] for row in range(4)]
    max_abs_c = max(abs(value) for row in delta_c for value in row)
    scale_c = max(abs(value) for row in expected_c for value in row)
    expected_y = original["admittance_S"]
    actual_y = swapped["admittance_S"]
    max_abs_y = 0.0
    scale_y = 0.0
    for row in range(4):
        for col in range(4):
            lhs = actual_y[row][col]
            rhs = expected_y[permutation[row]][permutation[col]]
            difference = complex(lhs["real"] - rhs["real"], lhs["imag"] - rhs["imag"])
            max_abs_y = max(max_abs_y, abs(difference))
            scale_y = max(scale_y, abs(complex(rhs["real"], rhs["imag"])))
    check.update({
        "status": "observed",
        "permutation": {"d": "s", "s": "d", "g": "g", "e": "e"},
        "capacitance_delta_fF_signed": delta_c,
        "max_absolute_capacitance_delta_fF": max_abs_c,
        "normalized_max_capacitance_delta": max_abs_c / max(scale_c, 1e-300),
        "max_absolute_admittance_delta_S": max_abs_y,
        "normalized_max_admittance_delta": max_abs_y / max(scale_y, 1e-300),
        "interpretation": "quantitative symmetry oracle only; not reciprocity and no same-VGS +/-VDS shortcut",
    })
    return check


def _ramp_deck(candidate, model, vds, endpoint, name):
    lines = common.header(candidate["library"], "F2 fixed single-attempt quasi-static gate ramp: " + name)
    lines += [
        "Vg g 0 PWL(0 0 %s %s)" % (common.fmt(RAMP_STOP_S), common.fmt(endpoint)),
        "Vd d 0 %s" % common.fmt(vds),
        "Vs s 0 0",
        "Ve e 0 0",
        "MTEST d g s e %s W=%su L=%su" % (
            model, common.fmt(common.W_UM), common.fmt(common.L_UM)
        ),
    ]
    lines += common.control([
        "set filetype=ascii",
        "tran %s %s" % (common.fmt(RAMP_STEP_S), common.fmt(RAMP_STOP_S)),
        "wrdata %s.dat v(g) i(Vg) i(Vd) i(Vs) i(Ve)" % name,
    ])
    return lines


def _integrate_branch_currents(times, branch_currents_into_device):
    charges = [[0.0] * len(branch_currents_into_device[0])]
    for index in range(1, len(times)):
        dt = times[index] - times[index - 1]
        charges.append([
            charges[-1][branch] + 0.5 * (
                branch_currents_into_device[index][branch]
                + branch_currents_into_device[index - 1][branch]
            ) * dt
            for branch in range(4)
        ])
    return charges


def _ramp_kcl(branch_currents, charges):
    max_residual = 0.0
    max_scale = 0.0
    for row in branch_currents:
        max_residual = max(max_residual, abs(sum(row)))
        max_scale = max(max_scale, sum(abs(value) for value in row))
    integral_sum = sum(charges[-1])
    integral_scale = sum(abs(value) for value in charges[-1])
    return {
        "max_instantaneous_absolute_residual_A": max_residual,
        "max_instantaneous_normalized_residual": max_residual / max(max_scale, 1e-300),
        "integrated_signed_residual_C": integral_sum,
        "integrated_absolute_residual_C": abs(integral_sum),
        "integrated_normalized_residual": abs(integral_sum) / max(integral_scale, 1e-300),
    }


def _run_ramp(candidate, device, polarity, vds, vds_label, ngspice, work, out, timeout):
    model = "nch" if device == "nmos" else "pch"
    name = "%s_%s_gate_ramp_%s" % (candidate["id"], device, vds_label)
    endpoint = polarity * common.VDD
    record = {
        "candidate_id": candidate["id"], "base": candidate["base"],
        "device": device, "vds_label": vds_label,
        "bias_V": {"vg_start": 0.0, "vg_end": endpoint, "vd": vds, "vs": 0.0, "ve": 0.0},
        "gate_ramp_time_s": RAMP_STOP_S, "fixed_transient_step_s": RAMP_STEP_S,
        "status": "failed",
    }
    try:
        run = common.run_deck(
            ngspice, work, name, _ramp_deck(candidate, model, vds, endpoint, name),
            vectors=5, stop=RAMP_STOP_S, timeout=timeout,
        )
        evidence = _relative_evidence(run["evidence"], out)
        record["evidence"] = evidence
        record["status"] = evidence["status"]
        if evidence["status"] != "observed":
            record["reason"] = evidence.get("reason", "ramp run was not observed")
            return record
        time_s = run["axis"]
        gate_v = run["vectors"][0]
        branch_raw = [[run["vectors"][branch + 1][sample] for branch in range(4)]
                      for sample in range(len(time_s))]
        branch_into_device = [[-value for value in sample] for sample in branch_raw]
        endpoint_error = abs(gate_v[-1] - endpoint)
        gate_monotone = all(
            (right > left if endpoint > 0.0 else right < left)
            for left, right in zip(gate_v, gate_v[1:])
        )
        finite_endpoint = (
            len(time_s) >= 2 and abs(time_s[0]) <= 1e-12
            and abs(time_s[-1] - RAMP_STOP_S) <= 1e-12
            and abs(gate_v[0]) <= 1e-9 and endpoint_error <= 1e-6
            and gate_monotone
        )
        record["endpoint_checks"] = {
            "sample_count": len(time_s), "time_start_s": time_s[0],
            "time_end_s": time_s[-1], "gate_start_V": gate_v[0],
            "gate_end_V": gate_v[-1], "gate_endpoint_error_V": endpoint_error,
            "gate_monotone_in_requested_direction": gate_monotone,
            "finite_endpoint_observed": finite_endpoint,
        }
        if not finite_endpoint:
            record["status"] = "failed"
            record["reason"] = "transient did not provide finite monotone 0-to-endpoint gate data"
            evidence["reason"] = record["reason"]
            return record
        charges = _integrate_branch_currents(time_s, branch_into_device)
        final_charges = charges[-1]
        branch_records = {}
        for index, terminal in enumerate(common.TERMINALS):
            branch_records[terminal] = {
                "raw_integral_C": final_charges[index],
                "raw_integral_fC": final_charges[index] * 1e15,
                "includes_conduction_at_nonzero_vds": terminal in ("d", "s") and vds != 0.0,
            }
        record["branch_charge_integrals"] = branch_records
        record["kcl"] = _ramp_kcl(branch_into_device, charges)
        record["charge_labeling"] = (
            "Raw four-source branch-current integrals; Qd/Qs include channel conduction at nonzero VDS, not displacement-only."
        )
        delta_v = gate_v[-1] - gate_v[-2]
        record["endpoint_dQg_dV_F"] = (
            (charges[-1][TERM_INDEX["g"]] - charges[-2][TERM_INDEX["g"]]) / delta_v
            if delta_v != 0.0 else None
        )
    except (OSError, ValueError, subprocess.SubprocessError, IndexError) as error:
        record["status"] = "failed"
        record["reason"] = str(error)
    return record


def _calibration_deck(name):
    return [
        "* F2 integration-sign check: ideal 100 fF capacitor",
        ".options temp=25 tnom=25 method=trap reltol=1e-4 abstol=1e-12 itl4=2000",
        "Vr n 0 PWL(0 0 %s 1)" % common.fmt(RAMP_STOP_S),
        "Cx n 0 %s" % common.fmt(CAL_CAP_F),
    ] + common.control([
        "set filetype=ascii",
        "tran %s %s" % (common.fmt(RAMP_STEP_S), common.fmt(RAMP_STOP_S)),
        "wrdata %s.dat i(Vr)" % name,
    ])


def run_charge_calibration(ngspice, work, out, timeout):
    name = "f2_capacitor_sign_calibration"
    record = {
        "name": "100fF_to_ground_0_to_1V_ramp",
        "expected_charge_C": CAL_CAP_F,
        "expected_sign": "positive current-into-device integral",
        "status": "failed",
    }
    try:
        run = common.run_deck(
            ngspice, work, name, _calibration_deck(name), vectors=1,
            stop=RAMP_STOP_S, timeout=timeout,
        )
        evidence = _relative_evidence(run["evidence"], out)
        record["evidence"] = evidence
        record["status"] = evidence["status"]
        if evidence["status"] != "observed":
            record["reason"] = evidence.get("reason", "calibration run was not observed")
            return record
        branch_current_into_device = [-value for value in run["vectors"][0]]
        charge = 0.0
        for index in range(1, len(run["axis"])):
            charge += 0.5 * (
                branch_current_into_device[index] + branch_current_into_device[index - 1]
            ) * (run["axis"][index] - run["axis"][index - 1])
        record["integrated_charge_C"] = charge
        record["integrated_charge_fC"] = charge * 1e15
        record["relative_magnitude_error"] = abs(abs(charge) - CAL_CAP_F) / CAL_CAP_F
        record["sign_check"] = "positive" if charge > 0.0 else "failed"
        if charge <= 0.0:
            record["status"] = "failed"
            record["reason"] = "100fF calibration charge sign was not positive"
    except (OSError, ValueError, subprocess.SubprocessError, IndexError) as error:
        record["status"] = "failed"
        record["reason"] = str(error)
    return record


def run_f2(candidates, ngspice, work, out, timeout):
    ac_records = []
    ac_lookup = {}
    mirror_checks = []
    for candidate in candidates:
        for device, polarity in (("nmos", 1.0), ("pmos", -1.0)):
            for case in _bias_cases(polarity):
                primary = _run_ac_matrix(
                    candidate, device, polarity, case, ngspice, work, out, timeout, "primary"
                )
                ac_records.append(primary)
                ac_lookup[(candidate["id"], device, case["case_id"])] = primary
                if case["mirror"] and not math.isclose(case["vd"], case["vs"], rel_tol=0.0, abs_tol=0.0):
                    swapped_case = dict(case)
                    swapped_case["vd"], swapped_case["vs"] = case["vs"], case["vd"]
                    swapped = _run_ac_matrix(
                        candidate, device, polarity, swapped_case,
                        ngspice, work, out, timeout, "ds_swapped",
                    )
                    ac_records.append(swapped)
                    ac_lookup[(candidate["id"], device, case["case_id"] + "_ds_swapped")] = swapped
                    mirror_checks.append(_compare_mirror(primary, swapped))

    ramps = []
    ramp_lookup = {}
    for candidate in candidates:
        for device, polarity in (("nmos", 1.0), ("pmos", -1.0)):
            for vds, label in ((0.0, "vds_zero"), (polarity * common.VDD, "operating_polarity_vds")):
                ramp = _run_ramp(
                    candidate, device, polarity, vds, label,
                    ngspice, work, out, timeout,
                )
                ramps.append(ramp)
                ramp_lookup[(candidate["id"], device, label)] = ramp

    derivative_checks = []
    for candidate in candidates:
        for device, polarity in (("nmos", 1.0), ("pmos", -1.0)):
            for label, ac_case_id in (
                ("vds_zero", "qs_endpoint_vds_zero"),
                ("operating_polarity_vds", "saturation_operating_polarity"),
            ):
                ramp = ramp_lookup[(candidate["id"], device, label)]
                matrix = ac_lookup.get((candidate["id"], device, ac_case_id))
                check = {
                    "candidate_id": candidate["id"], "device": device,
                    "ramp_vds_label": label,
                    "comparison": "endpoint finite-difference dQg/dV versus signed AC Cgg at the same bias",
                }
                if ramp.get("status") != "observed" or ramp.get("endpoint_dQg_dV_F") is None or \
                        matrix is None or matrix.get("status") != "observed":
                    check.update(status="inconclusive", reason="ramp derivative or matching AC Cgg unavailable")
                else:
                    slope_fF = ramp["endpoint_dQg_dV_F"] * 1e15
                    cgg_fF = matrix["capacitance_fF_signed"][TERM_INDEX["g"]][TERM_INDEX["g"]]
                    check.update({
                        "status": "observed", "dQg_dV_fF": slope_fF,
                        "signed_AC_Cgg_fF": cgg_fF,
                        "difference_fF": slope_fF - cgg_fF,
                        "normalized_difference": (slope_fF - cgg_fF)
                        / max(abs(slope_fF), abs(cgg_fF), 1e-300),
                        "matching_ac_case_id": ac_case_id,
                    })
                derivative_checks.append(check)

    calibration = run_charge_calibration(ngspice, work, out, timeout)
    return {
        "purpose": "Signed 4x4 AC admittance/capacitance, actual D/S exchange, raw QS branch integrals, and charge-sign evidence.",
        "configuration": {
            "terminals": list(common.TERMINALS), "geometry_um": {"w": common.W_UM, "l": common.L_UM},
            "temperature_C": 25, "frequency_Hz": FREQ_HZ,
            "AC_drive_V": AC_AMP_V, "source_and_e_reference_V": 0.0,
            "nmos_gate_sweep_V": "0 to +3", "pmos_gate_sweep_V": "0 to -3",
            "common_gate_bias_for_signed_small_vds_pair_V": {"nmos": 1.5, "pmos": -1.5},
            "small_signed_vds_cases_V": [0.2, -0.2],
            "matrix_convention": "rows are terminal current into device; columns are driven terminal; Cij=imag(Yij)/(2*pi*f), signed",
            "mirror_convention": "swap the applied D and S voltage values and permute D/S labels in the full matrices; no reciprocity assumption",
            "charge_ramp": "single fixed 2us-step, 1ms run; no convergence ladder or DC-current subtraction",
            "defaults": "all inherited defaults and uncalibrated SOI parameters remain unqualified",
        },
        "ac_matrices": ac_records,
        "d_source_exchange_checks": mirror_checks,
        "charge_ramps": ramps,
        "charge_sign_calibration": calibration,
        "charge_derivative_checks": derivative_checks,
        "status_counts": {
            "ac_matrices": _status_counts(ac_records),
            "d_source_exchange_checks": _status_counts(mirror_checks),
            "charge_ramps": _status_counts(ramps),
            "charge_sign_calibration": _status_counts([calibration]),
            "charge_derivative_checks": _status_counts(derivative_checks),
        },
    }


def _summary_markdown(report):
    f0 = report["F0"]["status_counts"]
    f1 = report["F1"]["status_counts"]
    f2 = report["F2"]["status_counts"]
    return "\n".join([
        "# B4SOI depletion static audit (F0/F1/F2)",
        "",
        "This is fixed-run evidence only. No candidate is selected or promoted; all inherited defaults and FD-related model inputs remain unqualified. Digitized I-V graph comparison is not FD validation.",
        "",
        "## Inputs",
        "",
        "- Simulator: `%s`" % report["simulator_version"].replace("\n", " "),
        "- Diagonal candidates: %d (frozen/overlap × SOIMOD 0/1/2); candidate hashes are in `static.json`." % len(report["candidate_libraries"]),
        "- Raw decks, logs, and data: `static/`; candidate libraries: `candidates/`.",
        "",
        "## Evidence counts",
        "",
        "| Bench | Runs/checks | Observed | Failed | Inconclusive |",
        "|---|---:|---:|---:|---:|",
        "| F0 operating-point inventory | {total} | {observed} | {failed} | {inconclusive} |".format(**f0),
        "| F0 optional-P syntax only | {total} | {observed} | {failed} | {inconclusive} |".format(**report["F0"]["optional_p_syntax"]["status_counts"]),
        "| F0 complete showmod query | {total} | {observed} | {failed} | {inconclusive} |".format(**report["F0"]["inventory_status_counts"]),
        "| F1 I-V curve runs | {total} | {observed} | {failed} | {inconclusive} |".format(**f1["curve_runs"]),
        "| F1 mode-0 baseline checks | {total} | {observed} | {failed} | {inconclusive} |".format(**f1["baseline_checks"]),
        "| F1 frozen/overlap parity | {total} | {observed} | {failed} | {inconclusive} |".format(**f1["frozen_overlap_parity_checks"]),
        "| F2 AC matrices | {total} | {observed} | {failed} | {inconclusive} |".format(**f2["ac_matrices"]),
        "| F2 D/S exchange checks | {total} | {observed} | {failed} | {inconclusive} |".format(**f2["d_source_exchange_checks"]),
        "| F2 charge ramps | {total} | {observed} | {failed} | {inconclusive} |".format(**f2["charge_ramps"]),
        "| F2 dQg/dV vs AC Cgg | {total} | {observed} | {failed} | {inconclusive} |".format(**f2["charge_derivative_checks"]),
        "| F2 capacitor sign calibration | {total} | {observed} | {failed} | {inconclusive} |".format(**f2["charge_sign_calibration"]),
        "",
        "## Conventions and limitations",
        "",
        "- Physical-style benches use D/G/S/E with an explicit E source and no P. Separate F0 fifth-P cases are simulator-syntax diagnostics only, not generic LVS/PEX body-contact qualification.",
        "- F0 retains complete `showmod` output in each run log. SOIMOD and FDMOD assertions are made only when those fields are exposed.",
        "- F1 preserves signed PMOS bias and sweep axes; I-V comparisons use current magnitude and retain per-sample errors.",
        "- F2 reports signed matrix entries and signed charge integrals. KCL residuals are absolute and normalized row/column sums; Qd/Qs at nonzero VDS include conduction.",
        "- F2 D/S exchange compares physically swapped applied terminal voltages with D/S matrix labels permuted; it is not a reciprocity test or same-VGS +/-VDS shortcut.",
        "",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"),
                        help="ngspice executable (default: PATH)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="audit output root (default: build/ngspice-depletion)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="fixed timeout in seconds for each simulator run")
    parser.add_argument("--step-V", type=float, default=DEFAULT_STEP_V,
                        help="maximum I-V DC sweep step before interpolation")
    args = parser.parse_args(argv)
    if not args.ngspice:
        parser.error("ngspice is required")
    if not math.isfinite(args.timeout) or args.timeout <= 0.0:
        parser.error("--timeout must be finite and positive")
    if not math.isfinite(args.step_V) or args.step_V <= 0.0:
        parser.error("--step-V must be finite and positive")

    out = args.out.resolve()
    try:
        generated = common.generate_candidates(out / "candidates", mixed=True)
        candidates = [item for item in generated if item["nmode"] == item["pmode"]]
        if len(candidates) != 6 or any(candidate["nmode"] != candidate["pmode"] for candidate in candidates):
            raise ValueError("expected exactly six independent diagonal frozen/overlap candidates")
        baseline = json.loads(iv.BASELINE.read_text(encoding="utf-8"))
        iv.verify_frozen_inputs(baseline, iv.MODEL_CARD, iv.MEASUREMENTS)
        curves = iv.read_curves(iv.MEASUREMENTS)
        if len(curves) != 12 or len(curves) != int(baseline["curve_count"]):
            raise ValueError("static audit requires all 12 frozen I-V curves")
        source_params = {
            base: iv.model_parameters(common.BASES[base]) for base in common.BASES
        }
        simulator_version = common.simulator_version(args.ngspice)
        static_work = out / "static"
        static_work.mkdir(parents=True, exist_ok=True)
        f0 = run_f0(candidates, source_params, args.ngspice, static_work / "F0", out, args.timeout)
        f0["optional_p_syntax"] = run_f0_body_syntax(
            candidates, args.ngspice, static_work / "F0_P_syntax", out, args.timeout)
        f1 = run_f1(candidates, curves, baseline, args.ngspice, static_work / "F1",
                    out, args.step_V, args.timeout)
        f2 = run_f2(candidates, args.ngspice, static_work / "F2", out, args.timeout)
        candidate_manifest = [
            {**candidate, "library": _relative(candidate["library"], out)}
            for candidate in candidates
        ]
        report = {
            "audit": "b4soi_depletion_static",
            "status": "evidence_complete_with_recorded_failures" if any(
                count["failed"] or count["inconclusive"] for count in (
                    f0["status_counts"], f1["status_counts"]["curve_runs"],
                    f0["optional_p_syntax"]["status_counts"],
                    f0["inventory_status_counts"],
                    f2["status_counts"]["ac_matrices"], f2["status_counts"]["charge_ramps"],
                    f2["status_counts"]["charge_sign_calibration"],
                    f1["status_counts"]["baseline_checks"],
                    f1["status_counts"]["frozen_overlap_parity_checks"],
                    f2["status_counts"]["d_source_exchange_checks"],
                    f2["status_counts"]["charge_derivative_checks"],
                )
            ) else "evidence_complete",
            "candidate_selection": None,
            "qualified": False,
            "source_hashes": common.source_hashes(),
            "simulator_version": simulator_version,
            "candidate_libraries": candidate_manifest,
            "raw_work_directory": "static/",
            "configuration": {
                "mode_pairs": "diagonal only: frozen/overlap × 0/0, 1/1, 2/2",
                "temperature_C": 25, "geometry_um": {"w": common.W_UM, "l": common.L_UM},
                "all_defaults_qualified": False,
            },
            "F0": f0, "F1": f1, "F2": f2,
        }
        common.save_json(out / "static.json", report)
        (out / "static.md").write_text(_summary_markdown(report), encoding="utf-8")
        print(json.dumps({
            "static_json": str(out / "static.json"),
            "static_md": str(out / "static.md"),
            "F0_inventory": f0["inventory_status_counts"],
            "F0": f0["status_counts"],
            "F1": f1["status_counts"],
            "F2": f2["status_counts"],
        }, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("B4SOI depletion static audit could not complete: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
