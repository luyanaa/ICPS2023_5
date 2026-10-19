#!/usr/bin/env python3
"""Experimental, single-corner Liberty rendering from Xyce evidence.

This module is deliberately *not* a Liberty characterization engine.  It only
serializes complete, already measured per-cell evidence produced by the
experimental Xyce 7.10 / BSIMSOI 4.5.0 bridge (`xyce_characterization.py`
measures it).  Evidence is accepted only for
the configured bridge binding at exactly 3.0 V and 25 C.  The resulting file
has one measured load/slew grid and contains no interpolation, extrapolation,
power model, or sequential setup/hold model.  Sequential cells are explicitly
reported as excluded and are not rendered.

Evidence JSON (one ``CELL.json`` per manifest cell) has this shape::

    {"schema": "ICPS2023_5_xyce_characterization_v1", "cell": "INV_X1",
     "binding": {"engine": "xyce", "engine_version": "Xyce 7.10 / BSIMSOI 4.5.0",
                  "engine_level": 70450, "section": "b4soi",
                  "model_sha256": "...", "model_include_sha256": "..."},
     "conditions": {"voltage_v": 3.0, "temperature_c": 25.0, "mode": "nominal"},
     "scope": {"loads_ff": [5.0], "slews_ns": [2.0]},
     "arcs": [{"input": "A", "output": "Y", "condition": {},
               "sense": "negative_unate", "load_ff": 5.0, "slew_ns": 2.0,
               "rise_delay_ns": 1.0, "fall_delay_ns": 1.1,
               "rise_transition_ns": 0.4, "fall_transition_ns": 0.5}]}

``condition`` contains the fixed input values used to sensitize the arc (the
changing input is omitted).  Every sensitized input/output/condition pair must
have every declared load/slew point.  This strict contract is what prevents a
consumer from accidentally treating a sparse measurement as a characterized
corner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

try:
    from . import logic, model_binding
except ImportError:  # direct invocation from libs.tech/stdcell
    import logic  # type: ignore
    import model_binding  # type: ignore


SCHEMA = "ICPS2023_5_xyce_characterization_v1"
EXPECTED_ENGINE = "xyce"
EXPECTED_ENGINE_VERSION = "Xyce 7.10 / BSIMSOI 4.5.0"
EXPECTED_ENGINE_LEVEL = 70450
EXPECTED_VOLTAGE = 3.0
EXPECTED_TEMPERATURE = 25.0


class ExperimentalLibertyError(ValueError):
    """Evidence cannot support the experimental Liberty artifact."""


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ExperimentalLibertyError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ExperimentalLibertyError(f"{label} must be a finite number") from error
    if not math.isfinite(result) or (positive and result <= 0):
        raise ExperimentalLibertyError(f"{label} must be {'positive and ' if positive else ''}finite")
    return result


def _same_number(value: Any, expected: float, label: str) -> None:
    if not math.isclose(_number(value, label), expected, rel_tol=0, abs_tol=1e-9):
        raise ExperimentalLibertyError(f"{label} must be exactly {expected:g}")


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ExperimentalLibertyError(f"configured model artifact is unavailable: {path}") from error


def _load_manifest(path: str | Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentalLibertyError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cells"), list):
        raise ExperimentalLibertyError("manifest must contain a cells list")
    records: dict[str, dict[str, Any]] = {}
    for record in manifest["cells"]:
        if not isinstance(record, dict) or not isinstance(record.get("cell"), str):
            raise ExperimentalLibertyError("manifest contains a malformed cell record")
        if record.get("status") != "strict_physical_pass":
            continue
        name = record["cell"]
        if name in records:
            raise ExperimentalLibertyError(f"manifest contains duplicate accepted cell {name}")
        spec = record.get("source_spec", record.get("physical_spec"))
        if not isinstance(spec, dict) or spec.get("name") != name:
            raise ExperimentalLibertyError(f"accepted cell {name} has no canonical source_spec")
        if (not isinstance(spec.get("inputs"), list) or not isinstance(spec.get("outputs"), list) or
                not isinstance(spec.get("pins"), list) or not isinstance(spec.get("function"), dict)):
            raise ExperimentalLibertyError(f"accepted cell {name} has incomplete logical specification")
        records[name] = {"record": record, "spec": spec}
    if not records:
        raise ExperimentalLibertyError(
            "experimental Liberty requires at least one strict published cell")
    return manifest, records


def _configured_binding(config_path: str | Path | None = None) -> dict[str, Any]:
    try:
        configured = model_binding.load(config_path)
    except Exception as error:
        raise ExperimentalLibertyError(f"cannot load configured model binding: {error}") from error
    # This path is intentionally independent of config defaults: a nominal
    # ngspice card must never become an experimental timing artifact.
    if (configured.get("engine") != EXPECTED_ENGINE or
            configured.get("engine_level") != EXPECTED_ENGINE_LEVEL or
            configured.get("engine_version") != EXPECTED_ENGINE_VERSION):
        raise ExperimentalLibertyError("configured model is not the Xyce 7.10/BSIMSOI 4.5.0 bridge")
    return {
        "engine": configured["engine"], "engine_version": configured["engine_version"],
        "engine_level": configured["engine_level"], "section": configured.get("section", "b4soi"),
        "model_sha256": _digest(Path(configured["library_path"])),
        "model_include_sha256": _digest(Path(configured["include_path"])),
    }


def _validate_binding(raw: Any, configured: dict[str, Any], cell: str) -> None:
    if not isinstance(raw, dict):
        raise ExperimentalLibertyError(f"{cell}: evidence binding is missing")
    for key in ("engine", "engine_version", "engine_level", "section",
                "model_sha256", "model_include_sha256"):
        if key not in raw:
            raise ExperimentalLibertyError(f"{cell}: evidence binding lacks {key}")
        if raw[key] != configured[key]:
            raise ExperimentalLibertyError(f"{cell}: evidence {key} does not match configured model")


def _condition_key(condition: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    if not isinstance(condition, dict):
        raise ExperimentalLibertyError("arc condition must be an object")
    result = []
    for pin, value in condition.items():
        if not isinstance(pin, str) or not isinstance(value, int) or isinstance(value, bool) or value not in (0, 1):
            raise ExperimentalLibertyError("arc conditions must map pin names to 0 or 1")
        result.append((pin, value))
    return tuple(sorted(result))


def _condition_text(condition: tuple[tuple[str, int], ...]) -> str:
    return " & ".join(pin if value else f"!{pin}" for pin, value in condition) or "1'b1"


def _expected_arcs(spec: dict[str, Any]) -> dict[tuple[str, str, tuple[tuple[str, int], ...]], str]:
    if spec.get("sequential"):
        return {}
    inputs, outputs = spec["inputs"], spec["outputs"]
    expected: dict[tuple[str, str, tuple[tuple[str, int], ...]], str] = {}
    for changing in inputs:
        others = [pin for pin in inputs if pin != changing]
        # Exhaustive Boolean sensitization is deterministic and agrees with
        # characterize.characterize_arcs' fixed-condition contract.
        import itertools
        for bits in itertools.product((0, 1), repeat=len(others)):
            condition = tuple(zip(others, bits))
            low_values = dict(condition, **{changing: 0})
            high_values = dict(condition, **{changing: 1})
            low = logic.evaluate(spec, low_values)
            high = logic.evaluate(spec, high_values)
            for output in outputs:
                if low[output] != high[output]:
                    expected[(changing, output, tuple(sorted(condition)))] = (
                        "positive_unate" if low[output] == 0 else "negative_unate")
    return expected


def _load_evidence(path: Path, cell: str) -> dict[str, Any]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentalLibertyError(f"{cell}: cannot read evidence: {error}") from error
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA or evidence.get("cell") != cell:
        raise ExperimentalLibertyError(f"{cell}: unsupported evidence schema or cell name")
    return evidence


def validate_evidence(manifest_path: str | Path, evidence_dir: str | Path,
                      *, config_path: str | Path | None = None,
                      binding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate all accepted cells and return normalized data for rendering."""
    _, records = _load_manifest(manifest_path)
    configured = dict(binding or _configured_binding(config_path))
    if (configured.get("engine") != EXPECTED_ENGINE or configured.get("engine_level") != EXPECTED_ENGINE_LEVEL or
            configured.get("engine_version") != EXPECTED_ENGINE_VERSION):
        raise ExperimentalLibertyError("only the configured Xyce 7.10/BSIMSOI 4.5.0 binding is accepted")
    root = Path(evidence_dir)
    normalized: dict[str, Any] = {}
    excluded: list[str] = []
    for cell in sorted(records):
        spec = records[cell]["spec"]
        evidence_path = root / f"{cell}.json"
        if not evidence_path.is_file():
            nested = root / cell / f"{cell}.json"
            evidence_path = nested if nested.is_file() else evidence_path
        evidence = _load_evidence(evidence_path, cell)
        _validate_binding(evidence.get("binding"), configured, cell)
        conditions = evidence.get("conditions")
        if not isinstance(conditions, dict):
            raise ExperimentalLibertyError(f"{cell}: conditions are missing")
        _same_number(conditions.get("voltage_v"), EXPECTED_VOLTAGE, f"{cell} voltage")
        _same_number(conditions.get("temperature_c"), EXPECTED_TEMPERATURE, f"{cell} temperature")
        if conditions.get("mode") != "nominal":
            raise ExperimentalLibertyError(f"{cell}: only nominal mode is accepted")
        scope = evidence.get("scope")
        if not isinstance(scope, dict) or not isinstance(scope.get("loads_ff"), list) or not isinstance(scope.get("slews_ns"), list):
            raise ExperimentalLibertyError(f"{cell}: load/slew scope is missing")
        loads = tuple(sorted({_number(x, f"{cell} load", positive=True) for x in scope["loads_ff"]}))
        slews = tuple(sorted({_number(x, f"{cell} slew", positive=True) for x in scope["slews_ns"]}))
        if not loads or not slews:
            raise ExperimentalLibertyError(f"{cell}: load/slew scope must not be empty")
        expected = _expected_arcs(spec)
        if spec.get("sequential"):
            if evidence.get("sequential_excluded") is not True or evidence.get("arcs") not in ([], None):
                raise ExperimentalLibertyError(f"{cell}: sequential cells must be explicitly excluded with no arcs")
            excluded.append(cell)
            normalized[cell] = {"spec": spec, "arcs": [], "loads": loads, "slews": slews}
            continue
        arcs = evidence.get("arcs")
        if not isinstance(arcs, list):
            raise ExperimentalLibertyError(f"{cell}: complete arcs list is missing")
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}
        for index, arc in enumerate(arcs):
            if not isinstance(arc, dict):
                raise ExperimentalLibertyError(f"{cell}: arc {index} is malformed")
            input_pin, output_pin = arc.get("input"), arc.get("output")
            if (not isinstance(input_pin, str) or input_pin not in spec["inputs"] or
                    not isinstance(output_pin, str) or output_pin not in spec["outputs"]):
                raise ExperimentalLibertyError(f"{cell}: arc {index} names an unsupported pin")
            condition = _condition_key(arc.get("condition", {}))
            condition_pins = {pin for pin, _ in condition}
            if input_pin in condition_pins or not condition_pins.issubset(set(spec["inputs"])):
                raise ExperimentalLibertyError(f"{cell}: arc {index} has an invalid sensitization condition")
            key = (input_pin, output_pin, condition)
            if key not in expected or arc.get("sense") != expected[key]:
                raise ExperimentalLibertyError(f"{cell}: arc {index} is not a complete sensitized Xyce arc")
            load = _number(arc.get("load_ff"), f"{cell} arc load", positive=True)
            slew = _number(arc.get("slew_ns"), f"{cell} arc slew", positive=True)
            if load not in loads or slew not in slews:
                raise ExperimentalLibertyError(f"{cell}: arc uses unsupported load/slew input")
            identity = key + (load, slew)
            if identity in seen:
                raise ExperimentalLibertyError(f"{cell}: duplicate arc measurement")
            for field in ("rise_delay_ns", "fall_delay_ns", "rise_transition_ns", "fall_transition_ns"):
                _number(arc.get(field), f"{cell} {field}", positive=True)
            seen[identity] = dict(arc, condition_key=condition, load_ff=load, slew_ns=slew)
        required = {(key + (load, slew)) for key in expected for load in loads for slew in slews}
        if set(seen) != required:
            missing = len(required - set(seen))
            extra = len(set(seen) - required)
            raise ExperimentalLibertyError(f"{cell}: incomplete arc evidence (missing={missing}, extra={extra})")
        normalized[cell] = {"spec": spec, "arcs": list(seen.values()), "loads": loads, "slews": slews}
    return {"schema": SCHEMA, "binding": configured, "cells": normalized, "excluded_sequential": excluded}


def _fmt(value: float) -> str:
    return f"{value:.12g}"


def render_liberty(validated: dict[str, Any]) -> str:
    """Render validated data; this function performs no filling or interpolation."""
    grids = sorted({(data["slews"], data["loads"]) for data in validated["cells"].values()
                    if not data["spec"].get("sequential")})
    template_names = {grid: "experimental_%02d" % index for index, grid in enumerate(grids)}
    lines = ["/* EXPERIMENTAL ONLY: ICPS2023_5 Xyce single-corner evidence.",
             " * Xyce 7.10 / BSIMSOI 4.5.0, nominal 3.0 V / 25 C.",
             " * Not process-qualified; no PVT/RC/PDN/site/signoff claim.",
             " * Sequential cells are explicitly excluded; setup/hold is absent.", "*/",
             "library (ICPS2023_5_experimental_xyce_single_corner) {",
             "  delay_model : table_lookup;", "  time_unit : \"1ns\";",
             "  voltage_unit : \"1V\";", "  capacitive_load_unit (1, ff);",
             "  nom_voltage : 3;", "  nom_temperature : 25;",
             # The arcs were measured at the 50 % crossing and 20/80 % transition
             # points; state that convention instead of letting a consumer
             # assume defaults.
             "  input_threshold_pct_rise : 50;",
             "  input_threshold_pct_fall : 50;",
             "  output_threshold_pct_rise : 50;",
             "  output_threshold_pct_fall : 50;",
             "  slew_lower_threshold_pct_rise : 20;",
             "  slew_lower_threshold_pct_fall : 20;",
             "  slew_upper_threshold_pct_rise : 80;",
             "  slew_upper_threshold_pct_fall : 80;",
             "  default_operating_conditions : nominal_xyce;",
             "  operating_conditions (nominal_xyce) {", "    process : 1;",
             "    voltage : 3;", "    temperature : 25;", "  }", ""]
    for (slews, loads), name in template_names.items():
        lines += [f"  lu_table_template ({name}) {{",
                  "    variable_1 : input_net_transition;",
                  "    variable_2 : total_output_net_capacitance;",
                  "    index_1 (\"%s\");" % ", ".join(_fmt(x) for x in slews),
                  "    index_2 (\"%s\");" % ", ".join(_fmt(x) for x in loads),
                  "  }", ""]
    for cell in sorted(validated["cells"]):
        data = validated["cells"][cell]
        spec = data["spec"]
        if spec.get("sequential"):
            continue
        template = template_names[(data["slews"], data["loads"])]
        lines += [f"  cell ({cell}) {{"]
        for pin in spec["inputs"]:
            lines += [f"    pin ({pin}) {{", "      direction : input;", "    }"]
        for pin in ("VDD", "VSS"):
            # Liberty's `pg_pin` group is the power/ground form synthesis and STA
            # both accept.  A `pin (...) { direction : inout; }` power pin makes
            # this build's ABC abort while reading the library, and a signal
            # `input` power pin would surface as an unconnected signal input.
            lines += [f"    pg_pin ({pin}) {{",
                      f'      voltage_name : "{pin}";',
                      f"      pg_type : {'primary_power' if pin == 'VDD' else 'primary_ground'};",
                      "    }"]
        by_output: dict[str, list[dict[str, Any]]] = {output: [] for output in spec["outputs"]}
        for arc in data["arcs"]:
            by_output[arc["output"]].append(arc)
        for output in spec["outputs"]:
            lines += [f"    pin ({output}) {{", "      direction : output;",
                      f"      function : \"{spec['function'][output]}\";"]
            grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
            for arc in by_output[output]:
                grouped.setdefault((arc["input"], arc["condition_key"], arc["sense"]), []).append(arc)
            for (related, condition, sense), arcs in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
                values = {(a["slew_ns"], a["load_ff"]): a for a in arcs}
                lines += ["      timing () {", f"        related_pin : \"{related}\";",
                          f"        timing_sense : {sense};"]
                if condition:
                    lines.append(f"        when : \"{_condition_text(condition)}\";")
                for label, field in (("cell_rise", "rise_delay_ns"),
                                     ("rise_transition", "rise_transition_ns"),
                                     ("cell_fall", "fall_delay_ns"),
                                     ("fall_transition", "fall_transition_ns")):
                    rows = [", ".join(_fmt(values[(slew, load)][field]) for load in data["loads"])
                            for slew in data["slews"]]
                    lines += [f"        {label} ({template}) {{",
                              f"          values ({', '.join(chr(34) + row + chr(34) for row in rows)});", "        }"]
                lines += ["      }"]
            lines += ["    }"]
        lines += ["  }", ""]
    lines += ["}", ""]
    return "\n".join(lines)


def generate_liberty(manifest_path: str | Path, evidence_dir: str | Path,
                     output_path: str | Path | None = None, *,
                     config_path: str | Path | None = None,
                     binding: dict[str, Any] | None = None) -> str:
    """Validate evidence and return deterministic Liberty text, optionally writing it."""
    text = render_liberty(validate_evidence(manifest_path, evidence_dir,
                                             config_path=config_path, binding=binding))
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render experimental Xyce single-corner Liberty evidence")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        generate_liberty(args.manifest, args.evidence_dir, args.output)
    except ExperimentalLibertyError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
