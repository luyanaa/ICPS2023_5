#!/usr/bin/env python3
"""Export validated structural Verilog views for the accepted physical cells.

This exporter emits black-box cell declarations and a structural mapped design;
it does not invent behavioral models, placement sites, or a process-qualified
Liberty library. Optional nominal model metadata is emitted only when complete
characterization evidence is supplied for every used cell.
"""
from __future__ import annotations

import argparse
import itertools
import json
import hashlib
import math
from pathlib import Path
import re
import sys
from typing import Any

try:
    from . import library as library_view
    from . import logic, mapping
except ImportError:  # direct invocation from libs.tech/stdcell
    import library as library_view  # type: ignore
    import logic  # type: ignore
    import mapping  # type: ignore


DEFAULT_MANIFEST = library_view.DEFAULT_MANIFEST
SCHEMA = "ICPS2023_5_stdcell_export_views_v1"
MODEL_SCHEMA = "ICPS2023_5_model_based_metadata_v1"
_SIMPLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
_ESCAPED_IDENTIFIER = re.compile(r"[A-Za-z0-9_$./:+@-]+\Z")
_MODEL_CALIBRATION = "model_based_unqualified_geometry_scaling_and_wire_coupling"

QUALIFICATION_LIMITS = [
    "The physical library is experimental_partial and covers only manifest cells marked strict_physical_pass.",
    "Checks establish repository-rule conformance, not foundry signoff or manufacturing qualification.",
    "No placement site, row, or production place-and-route flow is qualified.",
    "Only native DIFF/TiN/CNT/ML1/NAREA/PAREA and boundary/text layers are in scope; Poly, VIA1/ML2, and synthetic routing layers are unsupported.",
    "Direct TiN-to-diffusion contact is unqualified; body ties are usable only when individually verified.",
    "I/O/ESD and SRAM remain deferred.",
    "Cell area is not routed-core area; this exporter does not estimate whitespace, routing channels, PDN, clock, repair, or other physical-cell overhead.",
    "Model-based arc metadata, when emitted, is limited to supplied nominal 3.0 V/25 C and measured arc-specific load/slew; no PVT extrapolation, power/capacitance, sequential setup/hold, or signoff timing claim.",
]

_LIBERTY_REFUSAL = {
    "status": "experimental_only",
    "emitted": False,
    "reason_codes": [
        "single_corner_xyce_bridge",
        "no_pvt_sweep",
        "no_sequential_setup_hold",
        "no_process_signoff",
    ],
    "message": "Use the explicit experimental Xyce Liberty artifact only within its declared 3 V / 25 C evidence scope; no signoff claim.",
}


class ExportValidationError(ValueError):
    """A structural view cannot be proven to belong to the accepted library."""


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ExportValidationError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ExportValidationError(f"{label} must be a finite number") from error
    if not math.isfinite(number) or (positive and number <= 0):
        constraint = "positive and finite" if positive else "finite"
        raise ExportValidationError(f"{label} must be {constraint}")
    return number


def _verilog_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ExportValidationError(f"{label} must be a non-empty, whitespace-free Verilog identifier")
    if _SIMPLE_IDENTIFIER.fullmatch(value):
        return value
    if not _ESCAPED_IDENTIFIER.fullmatch(value):
        raise ExportValidationError(f"{label} contains characters outside the supported escaped-identifier set")
    return "\\" + value + " "


def _load_accepted_library(manifest_path: str | Path | None
                           ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Load strict-checked publication data and cross-check every accepted pin."""
    manifest = library_view.load_library(manifest_path, check=True)
    canonical = logic.cell_specs()
    accepted_specs: dict[str, dict[str, Any]] = {}
    accepted_records: dict[str, dict[str, Any]] = {}
    for record in manifest["cells"]:
        if record.get("status") != "strict_physical_pass":
            continue
        name = record.get("cell")
        if not isinstance(name, str) or name not in canonical:
            raise ExportValidationError(f"accepted manifest contains unknown cell {name!r}")
        spec = record.get("physical_spec")
        if not isinstance(spec, dict) or spec.get("name") != name:
            raise ExportValidationError(f"accepted physical spec is missing or misnamed for {name}")
        pins = spec.get("pins")
        inputs, outputs = spec.get("inputs"), spec.get("outputs")
        if (not isinstance(pins, list) or not isinstance(inputs, list) or
                not isinstance(outputs, list) or len(set(pins)) != len(pins)):
            raise ExportValidationError(f"physical pin declaration is malformed for {name}")
        expected_order = list(inputs) + list(outputs) + ["VDD", "VSS"]
        if pins != expected_order:
            raise ExportValidationError(f"physical spec pin order is inconsistent for {name}")
        manifest_pins = record.get("pins")
        if not isinstance(manifest_pins, dict) or set(manifest_pins) != set(pins):
            raise ExportValidationError(f"manifest pin set disagrees with accepted spec for {name}")
        directions = {pin: "INPUT" for pin in inputs}
        directions.update({pin: "OUTPUT" for pin in outputs})
        directions.update({"VDD": "INOUT", "VSS": "INOUT"})
        for pin in pins:
            pin_record = manifest_pins.get(pin)
            if not isinstance(pin_record, dict) or pin_record.get("direction") != directions[pin]:
                raise ExportValidationError(f"manifest direction for {name}.{pin} disagrees with accepted spec")
            _verilog_identifier(pin, f"{name} pin")
        physical = record.get("physical")
        if (not isinstance(physical, dict) or physical.get("passed") is not True or
                physical.get("strict_drc") is not True or physical.get("strict_lvs") is not True or
                physical.get("pex") is not True or physical.get("assembly") is not True):
            raise ExportValidationError(f"strict physical evidence is incomplete for {name}")
        accepted_specs[name] = spec
        accepted_records[name] = record
    if not accepted_specs:
        raise ExportValidationError("accepted manifest contains no strict_physical_pass cells")
    return accepted_specs, accepted_records, manifest


def _validate_mapped_netlist(netlist: Any, specs: dict[str, dict[str, Any]]
                             ) -> tuple[str, dict[str, str], list[dict[str, Any]], list[str]]:
    if not isinstance(netlist, dict):
        raise ExportValidationError("mapped netlist must be a canonical object")
    name = netlist.get("name")
    _verilog_identifier(name, "top module name")
    ports = netlist.get("ports")
    instances = netlist.get("instances")
    nets = netlist.get("nets", [])
    if not isinstance(ports, dict) or not isinstance(instances, list) or not isinstance(nets, list):
        raise ExportValidationError("mapped netlist requires ports, instances, and nets collections")
    normalized_ports: dict[str, str] = {}
    for port, direction in ports.items():
        _verilog_identifier(port, "top-level port")
        if direction not in ("input", "output", "inout"):
            raise ExportValidationError(f"invalid direction {direction!r} for top-level port {port!r}")
        normalized_ports[port] = direction
    _verilog_identifier_set = [_verilog_identifier(name, "top-level port") for name in normalized_ports]
    if len(set(_verilog_identifier_set)) != len(_verilog_identifier_set):
        raise ExportValidationError("top-level ports collide after Verilog identifier encoding")

    used_instance_names: set[str] = set()
    all_nets: set[str] = set(normalized_ports)
    normalized_instances: list[dict[str, Any]] = []
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict):
            raise ExportValidationError(f"instance {index} is not an object")
        cell, inst_name, connections = instance.get("cell"), instance.get("name"), instance.get("connections")
        if cell not in specs:
            raise ExportValidationError(f"instance {inst_name!r} uses unaccepted cell {cell!r}")
        inst_id = _verilog_identifier(inst_name, "instance name")
        if inst_id in used_instance_names or inst_id == _verilog_identifier(name, "top module name"):
            raise ExportValidationError(f"duplicate or conflicting instance name {inst_name!r}")
        used_instance_names.add(inst_id)
        if not isinstance(connections, dict):
            raise ExportValidationError(f"connections for instance {inst_name!r} must be an object")
        expected_pins = specs[cell]["pins"]
        if set(connections) != set(expected_pins):
            missing = sorted(set(expected_pins) - set(connections))
            extra = sorted(set(connections) - set(expected_pins))
            raise ExportValidationError(
                f"pin mismatch for {cell} instance {inst_name!r}: missing={missing}, extra={extra}")
        normalized_connections: dict[str, str] = {}
        for pin in expected_pins:
            value = connections[pin]
            _verilog_identifier(value, f"connection {inst_name}.{pin}")
            if value in ("0", "1") or value.startswith("{"):
                raise ExportValidationError(f"non-scalar/unmapped net {value!r} at {inst_name}.{pin}")
            normalized_connections[pin] = value
            all_nets.add(value)
        for rail in ("VDD", "VSS"):
            if normalized_connections.get(rail) == rail and rail not in normalized_ports:
                raise ExportValidationError(f"{rail} must be an explicit top-level port when used as a supply net")
        normalized_instances.append({"name": inst_name, "cell": cell,
                                     "connections": normalized_connections})
    for value in nets:
        _verilog_identifier(value, "net name")
        if value in ("0", "1"):
            raise ExportValidationError(f"unmapped constant net {value!r}")
        all_nets.add(value)
    net_ids = {_verilog_identifier(value, "net name") for value in all_nets}
    if len(net_ids) != len(all_nets):
        raise ExportValidationError("net names collide after Verilog identifier encoding")
    if used_instance_names & net_ids:
        raise ExportValidationError("instance names must not collide with top-level ports or nets")
    return name, normalized_ports, normalized_instances, sorted(all_nets)


def _qualification_comment() -> list[str]:
    return ["// Experimental ICPS2023_5 1M structural view; repository-rule conformance only."] + [
        "// Qualification limit: " + limit for limit in QUALIFICATION_LIMITS
    ]


def _render_cell_blackboxes(specs: dict[str, dict[str, Any]],
                            records: dict[str, dict[str, Any]]) -> str:
    lines = _qualification_comment()
    lines += ["// Empty declarations are black boxes, not behavioral cell models.", ""]
    for cell in sorted(specs):
        spec, record = specs[cell], records[cell]
        pin_info = record["pins"]
        pins = spec["pins"]
        lines.append("(* blackbox *) module %s (%s);" %
                     (_verilog_identifier(cell, "cell name"),
                      ", ".join(_verilog_identifier(pin, "cell pin") for pin in pins)))
        for direction in ("INPUT", "OUTPUT", "INOUT"):
            members = [pin for pin in pins if pin_info[pin]["direction"] == direction]
            if members:
                lines.append("  %s %s;" % (direction.lower(), ", ".join(
                    _verilog_identifier(pin, "cell pin") for pin in members)))
        lines.append("endmodule")
        lines.append("")
    return "\n".join(lines)


def _render_mapped_design(netlist: Any, specs: dict[str, dict[str, Any]]
                          ) -> tuple[str, dict[str, Any]]:
    name, ports, instances, nets = _validate_mapped_netlist(netlist, specs)
    lines = _qualification_comment()
    lines += ["// Generated only from a physical_qualified mapping validated against the accepted manifest.",
              "module %s (%s);" % (_verilog_identifier(name, "top module name"),
                                   ", ".join(_verilog_identifier(port, "top-level port") for port in ports))]
    for direction in ("input", "output", "inout"):
        members = [port for port, value in ports.items() if value == direction]
        if members:
            lines.append("  %s %s;" % (direction, ", ".join(
                _verilog_identifier(port, "top-level port") for port in members)))
    internal_nets = [net for net in nets if net not in ports]
    if internal_nets:
        lines.append("  wire %s;" % ", ".join(
            _verilog_identifier(net, "net name") for net in internal_nets))
    if instances:
        lines.append("")
    for instance in instances:
        cell, inst_name, connections = instance["cell"], instance["name"], instance["connections"]
        ordered_pins = specs[cell]["pins"]
        pin_bindings = [".%s(%s)" % (_verilog_identifier(pin, "cell pin"),
                                      _verilog_identifier(connections[pin], "net name"))
                        for pin in ordered_pins]
        lines.append("  %s %s (%s);" % (_verilog_identifier(cell, "cell name"),
                                        _verilog_identifier(inst_name, "instance name"),
                                        ", ".join(pin_bindings)))
    lines.append("endmodule")
    canonical = {"name": name, "ports": ports, "instances": instances, "nets": nets}
    return "\n".join(lines) + "\n", canonical


def _expected_arcs(spec: dict[str, Any]) -> dict[tuple[str, str, tuple[tuple[str, int], ...]], str]:
    expected: dict[tuple[str, str, tuple[tuple[str, int], ...]], str] = {}
    for input_pin in spec["inputs"]:
        others = [pin for pin in spec["inputs"] if pin != input_pin]
        for values in itertools.product((0, 1), repeat=len(others)):
            condition = dict(zip(others, values))
            low = logic.evaluate(spec, {**condition, input_pin: 0})
            high = logic.evaluate(spec, {**condition, input_pin: 1})
            for output in spec["outputs"]:
                if low[output] != high[output]:
                    key = (input_pin, output, tuple(sorted(condition.items())))
                    expected[key] = "negative_unate" if low[output] > high[output] else "positive_unate"
    return expected


def _validate_characterization(path: Path, cell: str, spec: dict[str, Any],
                               record: dict[str, Any], manifest: dict[str, Any]
                               ) -> dict[str, Any]:
    try:
        evidence_bytes = path.read_bytes()
        evidence = json.loads(evidence_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise ExportValidationError(f"cannot read characterization evidence {path}") from error
    if not isinstance(evidence, dict):
        raise ExportValidationError("characterization evidence must be a JSON object")
    expected_model = manifest.get("provenance", {}).get("model_sha256")
    if record.get("fingerprints", {}).get("model_sha256") != expected_model:
        raise ExportValidationError("accepted cell model fingerprint disagrees with manifest provenance")
    if evidence.get("name") != cell:
        raise ExportValidationError(f"characterization name does not match {cell}")
    if evidence.get("model_sha256") != expected_model:
        raise ExportValidationError("characterization model fingerprint does not match accepted manifest")
    if (evidence.get("mode") != "nominal_SOIMOD_0" or
            evidence.get("calibration") != _MODEL_CALIBRATION or
            evidence.get("logic_levels_passed") is not True or
            evidence.get("timing_objective") != "feasibility_only"):
        raise ExportValidationError("characterization mode, calibration, or logic-level evidence is incomplete")
    if _finite_number(evidence.get("voltage_v"), f"{cell} voltage") != 3.0:
        raise ExportValidationError("characterization voltage is not the documented nominal 3.0 V")
    if _finite_number(evidence.get("temperature_c"), f"{cell} temperature") != 25.0:
        raise ExportValidationError("characterization temperature is not the documented 25 C")
    if spec.get("sequential"):
        raise ExportValidationError("sequential characterization is not exported as combinational arc metadata")

    truth = evidence.get("truth")
    input_rows = list(itertools.product((0, 1), repeat=len(spec["inputs"])))
    if not isinstance(truth, list) or len(truth) != len(input_rows):
        raise ExportValidationError("truth-table evidence does not cover every input combination")
    for row, bits in zip(truth, input_rows):
        inputs = dict(zip(spec["inputs"], bits))
        expected = logic.evaluate(spec, inputs)
        if not isinstance(row, dict) or row.get("inputs") != inputs or row.get("expected") != expected:
            raise ExportValidationError("truth-table evidence disagrees with the accepted Boolean spec")
        observed = row.get("voltage_v")
        if not isinstance(observed, dict) or set(observed) != set(spec["outputs"]):
            raise ExportValidationError("truth-table evidence is missing output voltages")
        for output, expected_bit in expected.items():
            voltage = _finite_number(observed[output], f"{cell}.{output} truth voltage")
            if (expected_bit and voltage < 2.7) or (not expected_bit and voltage > 0.3):
                raise ExportValidationError("truth-table output voltage does not meet the characterized logic-level threshold")

    arcs = evidence.get("arcs")
    expected_arcs = _expected_arcs(spec)
    if not isinstance(arcs, list) or len(arcs) != len(expected_arcs):
        raise ExportValidationError("characterization arcs do not cover every sensitizable input/output condition")
    seen: set[tuple[str, str, tuple[tuple[str, int], ...]]] = set()
    safe_arcs: list[dict[str, Any]] = []
    for arc in arcs:
        if not isinstance(arc, dict):
            raise ExportValidationError("characterization arc is malformed")
        condition = arc.get("condition")
        if not isinstance(condition, dict) or any(value not in (0, 1) for value in condition.values()):
            raise ExportValidationError("arc condition must contain binary input values")
        try:
            key = (arc.get("input"), arc.get("output"), tuple(sorted(condition.items())))
        except TypeError as error:
            raise ExportValidationError("arc condition contains invalid pins") from error
        if key not in expected_arcs or key in seen:
            raise ExportValidationError("characterization contains an unexpected or duplicate arc")
        if arc.get("sense") != expected_arcs[key]:
            raise ExportValidationError("characterization arc sense disagrees with accepted Boolean function")
        seen.add(key)
        safe_arc = {"input": key[0], "output": key[1], "condition": dict(key[2]),
                    "sense": arc["sense"]}
        for field in ("rise_delay_ns", "fall_delay_ns", "rise_transition_ns", "fall_transition_ns",
                      "load_ff", "input_slew_ns"):
            safe_arc[field] = _finite_number(arc.get(field), f"{cell} {field}", positive=True)
        noise = arc.get("noise_margin")
        if not isinstance(noise, dict) or not all(name in noise for name in ("vil_v", "vih_v", "nml_v", "nmh_v")):
            raise ExportValidationError("characterization arc is missing noise-margin measurements")
        safe_arc["noise_margin"] = {name: _finite_number(noise[name], f"{cell} {name}")
                                     for name in ("vil_v", "vih_v", "nml_v", "nmh_v")}
        if safe_arc["noise_margin"]["nml_v"] <= 0 or safe_arc["noise_margin"]["nmh_v"] <= 0:
            raise ExportValidationError("characterization has non-positive noise margin")
        if (not 0 <= safe_arc["noise_margin"]["vil_v"] <= 3.0 or
                not 0 <= safe_arc["noise_margin"]["vih_v"] <= 3.0 or
                safe_arc["noise_margin"]["vil_v"] > safe_arc["noise_margin"]["vih_v"]):
            raise ExportValidationError("characterization has invalid unit-gain threshold voltages")
        safe_arcs.append(safe_arc)
    if seen != set(expected_arcs):
        raise ExportValidationError("characterization is missing a sensitizable input/output arc")
    physical = record["physical"]
    return {
        "cell": cell,
        "pins": {pin: record["pins"][pin]["direction"] for pin in spec["pins"]},
        "function": spec["function"],
        "model_sha256": expected_model,
        "characterization_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "physical_evidence": {
            "process_profile": physical["process_profile"],
            "strict_drc": physical["strict_drc"],
            "strict_lvs": physical["strict_lvs"],
            "pex": physical["pex"],
            "assembly": physical["assembly"],
            "gds_sha256": physical["gds_sha256"],
            "pex_sha256": physical["pex_sha256"],
        },
        "voltage_v": 3.0,
        "temperature_c": 25,
        "timing_objective": "feasibility_only",
        "arcs": sorted(safe_arcs, key=lambda arc: (arc["input"], arc["output"],
                                                    tuple(sorted(arc["condition"].items())))),
    }


def _model_metadata(used_cells: list[str], specs: dict[str, dict[str, Any]],
                    records: dict[str, dict[str, Any]], manifest: dict[str, Any],
                    characterization_dir: str | Path | None) -> dict[str, Any]:
    source_dir = Path(characterization_dir) if characterization_dir is not None else None
    if not used_cells:
        reason = "mapped_design_contains_no_cells"
        per_cell: dict[str, Any] = {}
    elif source_dir is None:
        reason = "characterization_evidence_not_supplied"
        per_cell = {cell: {"status": "refused", "reason_codes": [reason]} for cell in used_cells}
    else:
        per_cell = {}
        failures: list[str] = []
        models: dict[str, Any] = {}
        for cell in used_cells:
            evidence_path = source_dir / cell / "characterization.json"
            try:
                models[cell] = _validate_characterization(
                    evidence_path, cell, specs[cell], records[cell], manifest)
                per_cell[cell] = {"status": "accepted", "evidence": str(evidence_path)}
            except (ExportValidationError, OSError, TypeError, ValueError) as error:
                code = ("characterization_evidence_missing" if not evidence_path.is_file()
                        else "characterization_evidence_invalid")
                per_cell[cell] = {"status": "refused", "reason_codes": [code],
                                  "message": str(error)}
                failures.append(cell)
        if not failures:
            return {
                "schema": MODEL_SCHEMA,
                "status": "emitted_limited_model_based_metadata",
                "artifact_kind": "nominal_postlayout_combinational_characterization",
                "not_a_liberty_library": True,
                "scope": "Only supplied per-cell nominal compact-model truth and sensitizable arcs at their measured load/slew; no interpolation or extrapolation.",
                "cells": models,
                "liberty": dict(_LIBERTY_REFUSAL),
                "qualification_limits": list(QUALIFICATION_LIMITS),
            }
        reason = "characterization_evidence_incomplete_or_invalid"
    return {
        "schema": MODEL_SCHEMA,
        "status": "refused",
        "artifact": None,
        "reason_codes": [reason],
        "per_cell": per_cell,
        "liberty": dict(_LIBERTY_REFUSAL),
        "qualification_limits": list(QUALIFICATION_LIMITS),
    }


def _write_refusal(output_dir: Path, error: Exception, manifest_path: str | Path | None) -> dict[str, Any]:
    result = {
        "schema": SCHEMA,
        "status": "refused",
        "reason_codes": ["view_validation_or_export_failed"],
        "message": str(error),
        "manifest": str(manifest_path if manifest_path is not None else DEFAULT_MANIFEST),
        "views": {},
        "qualification_limits": list(QUALIFICATION_LIMITS),
        "liberty": dict(_LIBERTY_REFUSAL),
    }
    _json_write(output_dir / "export_status.json", result)
    refusal = {
        "schema": MODEL_SCHEMA,
        "status": "refused",
        "artifact": None,
        "reason_codes": ["view_validation_or_export_failed"],
        "message": str(error),
        "liberty": dict(_LIBERTY_REFUSAL),
        "qualification_limits": list(QUALIFICATION_LIMITS),
    }
    _json_write(output_dir / "model_metadata.json", refusal)
    return result


def export_views(netlist_source: str | Path | dict[str, Any], output_dir: str | Path, *,
                 top: str | None = None, manifest_path: str | Path | None = None,
                 characterization_dir: str | Path | None = None) -> dict[str, Any]:
    """Map and export a design using only manifest-accepted physical cells.

    The output directory is caller-owned and receives ``cell_blackboxes.v``,
    ``mapped_design.v``, ``model_metadata.json`` (or explicit refusal evidence),
    and ``export_status.json``. A Liberty file is never emitted.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    try:
        specs, records, manifest = _load_accepted_library(manifest_path)
        mapped = mapping.map_yosys_netlist(
            netlist_source, top=top, library_view="physical_qualified",
            manifest_path=manifest_path, specs=specs)
        design_text, canonical = _render_mapped_design(mapped, specs)
        cells_text = _render_cell_blackboxes(specs, records)
        used_cells = sorted({instance["cell"] for instance in canonical["instances"]})
        model_metadata = _model_metadata(used_cells, specs, records, manifest, characterization_dir)
        cell_area_um2 = mapping.area(canonical, manifests=records)
        manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST
        manifest_sha256 = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
        # Render/validate every output before publishing any success status.
        cells_path = output_path / "cell_blackboxes.v"
        design_path = output_path / "mapped_design.v"
        model_path = output_path / "model_metadata.json"
        cells_path.write_text(cells_text, encoding="utf-8")
        design_path.write_text(design_text, encoding="utf-8")
        _json_write(model_path, model_metadata)
        result = {
            "schema": SCHEMA,
            "status": "passed",
            "manifest": str(manifest_path if manifest_path is not None else DEFAULT_MANIFEST),
            "manifest_sha256": manifest_sha256,
            "manifest_status": manifest.get("status"),
            "library": manifest.get("library"),
            "process_profile": library_view.PROFILE,
            "mapped_module": canonical["name"],
            "accepted_cells": sorted(specs),
            "used_cells": used_cells,
            "instance_count": len(canonical["instances"]),
            "validation": {
                "accepted_manifest_provenance": True,
                "strict_physical_evidence": True,
                "cell_membership_against_manifest": True,
                "cell_and_design_pins_against_manifest": True,
            },
            "views": {
                "cell_blackboxes": {
                    "path": cells_path.name, "cell_count": len(specs),
                    "sha256": hashlib.sha256(cells_text.encode("utf-8")).hexdigest(),
                },
                "mapped_design": {
                    "path": design_path.name, "unique_cell_count": len(used_cells),
                    "sha256": hashlib.sha256(design_text.encode("utf-8")).hexdigest(),
                },
                "model_metadata": {
                    "path": model_path.name, "status": model_metadata["status"],
                    "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                },
            },
            "metrics": {
                "A_cell_total_um2": cell_area_um2,
                "A_routed_core_um2": None,
                "A_payload_um2": None,
                "routed_core_measured": False,
                "area_scope": "sum of accepted cell geometry only; not an array or routed-core area estimate",
            },
            "liberty": dict(_LIBERTY_REFUSAL),
            "qualification_limits": list(QUALIFICATION_LIMITS),
        }
        _json_write(output_path / "export_status.json", result)
        return result
    except Exception as error:
        return _write_refusal(output_path, error, manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", help="Yosys JSON or canonical mapping JSON")
    parser.add_argument("--top", help="Yosys module name when the input contains multiple modules")
    parser.add_argument("--manifest", help="accepted physical cell manifest (defaults to published library)")
    parser.add_argument("--characterization-dir", help="optional per-cell evidence directory: CELL/characterization.json")
    parser.add_argument("--out-dir", required=True, help="destination directory for generated views and evidence")
    args = parser.parse_args(argv)
    status = export_views(args.netlist, args.out_dir, top=args.top,
                          manifest_path=args.manifest,
                          characterization_dir=args.characterization_dir)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status.get("status") == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
