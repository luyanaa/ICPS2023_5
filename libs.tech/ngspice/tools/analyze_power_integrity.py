#!/usr/bin/env python3
"""Sparse extracted-network IR/current analysis with explicit boundaries.

This is a linear resistor/capacitor network analysis, not a transistor solver
or foundry signoff tool. Every source, load-current pair, waveform polarity,
initial state, and numerical choice is an input; no implicit ground, gmin, or
activity-factor current is introduced.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shlex
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, splu

try:
    from . import waveint
    from .reliability_netlist import read_network
except ImportError:  # direct script execution
    import waveint
    from reliability_netlist import read_network


class PowerIntegrityError(ValueError):
    """Input or circuit condition prevents a defensible network solution."""


_REQUIRED_SENSITIVITY_EXCLUSIONS = {"measured_cnt", "fitted_rdsw"}


def _number(value, description: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PowerIntegrityError("%s must be a finite number" % description) from exc
    if not math.isfinite(result):
        raise PowerIntegrityError("%s must be a finite number" % description)
    return result


def _nonempty_text(value, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PowerIntegrityError("%s must be a non-empty string" % description)
    return value.strip()


def _name_key(name: str) -> str:
    return name.casefold()


def _node_name(value, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise PowerIntegrityError("%s must be a node name" % description)
    return value


def _network_nodes(network: dict) -> tuple[list[str], dict[str, str]]:
    nodes = []
    by_key = {}

    def add(value, description):
        name = _node_name(value, description)
        key = _name_key(name)
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = name
            nodes.append(name)
        elif prior != name and description == "network.nodes":
            raise PowerIntegrityError(
                "network.nodes contains case-insensitive duplicate names %r and %r"
                % (prior, name))
        return by_key[key]

    source_nodes = network.get("nodes", [])
    if not isinstance(source_nodes, list):
        raise PowerIntegrityError("network.nodes must be a list")
    for node in source_nodes:
        add(node, "network.nodes")
    for collection, fields in ((network.get("resistors", []), ("a", "b")),
                               (network.get("capacitors", []), ("a", "b"))):
        for element in collection:
            if not isinstance(element, dict):
                raise PowerIntegrityError("network elements must be objects")
            for field in fields:
                add(element.get(field), "network element %s endpoint" % field)
    return nodes, by_key


def _edge_list(network: dict, access_branches=()) -> tuple[list[dict], list[dict]]:
    if not isinstance(network, dict):
        raise PowerIntegrityError("network must be the dict returned by read_network")
    nodes, node_by_key = _network_nodes(network)
    node_set = set(node_by_key)
    resistors = []
    capacitors = []
    resistor_names = set()

    for item in network.get("resistors", []):
        name = _nonempty_text(item.get("name"), "resistor name")
        key = _name_key(name)
        if key in resistor_names:
            raise PowerIntegrityError("duplicate resistor name %r" % name)
        resistor_names.add(key)
        a = _node_name(item.get("a"), "%s.a" % name)
        b = _node_name(item.get("b"), "%s.b" % name)
        resistance = _number(item.get("resistance_ohm"), "%s resistance_ohm" % name)
        if resistance <= 0:
            raise PowerIntegrityError("%s must have positive resistance; zero-ohm ties are not solved" % name)
        if _name_key(a) == _name_key(b):
            raise PowerIntegrityError("%s connects a node to itself" % name)
        resistors.append({**item, "name": name, "a": node_by_key[_name_key(a)],
                          "b": node_by_key[_name_key(b)],
                          "resistance_ohm": resistance,
                          "branch_type": "extracted"})

    for item in network.get("capacitors", []):
        name = _nonempty_text(item.get("name"), "capacitor name")
        a = _node_name(item.get("a"), "%s.a" % name)
        b = _node_name(item.get("b"), "%s.b" % name)
        capacitance = _number(item.get("capacitance_f"), "%s capacitance_f" % name)
        if capacitance < 0:
            raise PowerIntegrityError("%s must have nonnegative capacitance" % name)
        capacitors.append({**item, "name": name,
                           "a": node_by_key[_name_key(a)],
                           "b": node_by_key[_name_key(b)],
                           "capacitance_f": capacitance})

    extracted_by_name = {_name_key(branch["name"]): branch for branch in resistors}
    for branch in access_branches:
        name = _nonempty_text(branch.get("name"), "sensitivity branch name")
        key = _name_key(name)
        if key in resistor_names:
            raise PowerIntegrityError(
                "sensitivity branch %r duplicates an existing resistor name" % name)
        resistor_names.add(key)
        a = _node_name(branch.get("a"), "%s.a" % name)
        b = _node_name(branch.get("b"), "%s.b" % name)
        if _name_key(a) not in node_set or _name_key(b) not in node_set:
            raise PowerIntegrityError(
                "sensitivity branch %s endpoints must already exist in extracted network nodes" % name)
        resistance = _number(branch.get("resistance_ohm"), "%s resistance_ohm" % name)
        if resistance <= 0:
            raise PowerIntegrityError("%s sensitivity resistance must be positive" % name)
        if _name_key(a) == _name_key(b):
            raise PowerIntegrityError("%s connects a node to itself" % name)
        if "series_with" in branch:
            target_name = _nonempty_text(branch["series_with"], "%s series_with" % name)
            target = extracted_by_name.get(_name_key(target_name))
            if target is None:
                raise PowerIntegrityError("%s series_with must name an extracted resistor" % name)
            if {_name_key(a), _name_key(b)} != {
                    _name_key(target["a"]), _name_key(target["b"])}:
                raise PowerIntegrityError("%s series endpoints must match %s" % (name, target_name))
            target.setdefault("extracted_resistance_ohm", target["resistance_ohm"])
            target.setdefault("series_access", []).append(dict(branch))
            target["resistance_ohm"] += resistance
            target["branch_type"] = "conditional_series_access_sensitivity"
            target["material"] = "MIXED"
            target["geometry_qualification"] = "UNCHARACTERIZED_SERIES_ACCESS"
            continue
        resistors.append({**branch, "name": name,
                          "a": node_by_key[_name_key(a)],
                          "b": node_by_key[_name_key(b)],
                          "resistance_ohm": resistance,
                          "branch_type": "hypothetical_access_sensitivity"})

    return nodes, {"resistors": resistors, "capacitors": capacitors}


def _matrices(nodes: list[str], model: dict):
    index = {_name_key(node): i for i, node in enumerate(nodes)}
    n = len(nodes)
    gr, gc, cv, cr, cc, cval = [], [], [], [], [], []
    adj_r, adj_c = [], []
    for branch in model["resistors"]:
        a, b = index[_name_key(branch["a"])], index[_name_key(branch["b"])]
        g = 1.0 / branch["resistance_ohm"]
        gr.extend((a, a, b, b))
        gc.extend((a, b, a, b))
        cv.extend((g, -g, -g, g))
        adj_r.extend((a, b))
        adj_c.extend((b, a))
    for capacitor in model["capacitors"]:
        a, b = index[_name_key(capacitor["a"])], index[_name_key(capacitor["b"])]
        value = capacitor["capacitance_f"]
        if a == b or value == 0:
            continue
        cr.extend((a, a, b, b))
        cc.extend((a, b, a, b))
        cval.extend((value, -value, -value, value))
    gmat = coo_matrix((cv, (gr, gc)), shape=(n, n), dtype=float).tocsr()
    cmat = coo_matrix((cval, (cr, cc)), shape=(n, n), dtype=float).tocsr()
    adjacency = coo_matrix((np.ones(len(adj_r)), (adj_r, adj_c)),
                           shape=(n, n), dtype=np.int8).tocsr()
    return index, gmat, cmat, adjacency


def _fixed_indices(nodes, index, sources):
    fixed = {}
    for node, value in sources.items():
        key = _name_key(_node_name(node, "fixed source node"))
        if key not in index:
            raise PowerIntegrityError("fixed source node %r is absent from the network" % node)
        i = index[key]
        if i in fixed:
            raise PowerIntegrityError("duplicate fixed source node %r" % node)
        fixed[i] = value
    if not fixed:
        raise PowerIntegrityError("at least one explicit fixed-source voltage is required")
    return fixed


def _check_anchoring(adjacency, nodes, fixed_indices):
    count, labels = connected_components(adjacency, directed=False, return_labels=True)
    anchored = {labels[i] for i in fixed_indices}
    floating = [[nodes[i] for i in range(len(nodes)) if labels[i] == component]
                for component in range(count) if component not in anchored]
    if floating:
        previews = [", ".join(group[:8]) + (" …" if len(group) > 8 else "")
                    for group in floating[:8]]
        raise PowerIntegrityError(
            "unreferenced/singular resistive component(s) have no explicit fixed source: %s; "
            "declare real boundary sources or correct the extracted graph (no gmin/ground fallback)"
            % "; ".join(previews))


def _current_pairs(raw_pairs, nodes, index, *, dynamic=False):
    if not isinstance(raw_pairs, list):
        raise PowerIntegrityError("current_pairs must be a list (possibly empty)")
    parsed = []
    names = set()
    for item in raw_pairs:
        if not isinstance(item, dict):
            raise PowerIntegrityError("each current pair must be an object")
        name = _nonempty_text(item.get("name"), "current pair name")
        if _name_key(name) in names:
            raise PowerIntegrityError("duplicate current pair name %r" % name)
        names.add(_name_key(name))
        if dynamic:
            if "pair" not in item or not isinstance(item["pair"], dict):
                raise PowerIntegrityError("dynamic current %s needs an explicit pair object" % name)
            pair = item["pair"]
            from_node = _node_name(pair.get("from_node"), "%s pair.from_node" % name)
            to_node = _node_name(pair.get("to_node"), "%s pair.to_node" % name)
            for source_field in ("source_from_node", "source_to_node"):
                if source_field in pair:
                    source_value = _node_name(pair[source_field],
                                              "%s pair.%s" % (name, source_field))
                    if _name_key(source_value) not in index:
                        raise PowerIntegrityError(
                            "%s has absent pair.%s" % (name, source_field))
            if ("source_from_node" in pair) != ("source_to_node" in pair):
                raise PowerIntegrityError(
                    "%s pair must specify both source_from_node and source_to_node" % name)
            source_node = _node_name(item.get("source_node"), "%s source_node" % name)
            if "current_polarity" not in item:
                raise PowerIntegrityError("dynamic current %s needs explicit current_polarity" % name)
            polarity = item["current_polarity"]
            if polarity not in ("positive_out_of_source", "positive_into_source"):
                raise PowerIntegrityError(
                    "%s current_polarity must be positive_out_of_source or positive_into_source" % name)
            column = _nonempty_text(item.get("column"), "%s CSV column" % name)
            role = item.get("role")
            if role not in ("supply", "device_terminal", "other"):
                raise PowerIntegrityError("dynamic current %s needs role supply/device_terminal/other" % name)
            source_name = _nonempty_text(item.get("source_name"), "%s source_name" % name)
            parsed.append({**item, "name": name, "source_name": source_name,
                           "column": column, "source_node": source_node, "from_node": from_node,
                           "to_node": to_node, "current_polarity": polarity,
                           "role": role})
        else:
            from_node = _node_name(item.get("from_node"), "%s from_node" % name)
            to_node = _node_name(item.get("to_node"), "%s to_node" % name)
            parsed.append({**item, "name": name, "from_node": from_node,
                           "to_node": to_node,
                           "current_a": _number(item.get("current_a"), "%s current_a" % name)})
        if _name_key(from_node) not in index or _name_key(to_node) not in index:
            raise PowerIntegrityError("current pair %s references an absent network node" % name)
        if _name_key(from_node) == _name_key(to_node):
            raise PowerIntegrityError("current pair %s has identical endpoints" % name)
        for source_field in ("source_from_node", "source_to_node"):
            if source_field in item:
                source_value = _node_name(item[source_field],
                                          "%s %s" % (name, source_field))
                if _name_key(source_value) not in index:
                    raise PowerIntegrityError(
                        "current pair %s has absent %s" % (name, source_field))
                item[source_field] = source_value
        if ("source_from_node" in item) != ("source_to_node" in item):
            raise PowerIntegrityError("%s must specify both source_from_node and source_to_node" % name)
    return parsed


def _add_pair_current(vector, pair, current, index):
    vector[index[_name_key(pair["from_node"])]] -= current
    vector[index[_name_key(pair["to_node"])]] += current


def _solve_linear(matrix, rhs, description):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", MatrixRankWarning)
            factor = splu(matrix.tocsc())
            solution = factor.solve(np.asarray(rhs, dtype=float))
    except (MatrixRankWarning, RuntimeError, ValueError) as exc:
        raise PowerIntegrityError(
            "%s is singular; the graph needs explicit physical boundary conditions, not gmin" % description) from exc
    if not np.all(np.isfinite(solution)):
        raise PowerIntegrityError("%s produced non-finite node voltages" % description)
    return solution


def _static_solution(nodes, model, fixed_sources, pairs):
    index, gmat, cmat, adjacency = _matrices(nodes, model)
    fixed = _fixed_indices(nodes, index, fixed_sources)
    _check_anchoring(adjacency, nodes, set(fixed))
    fixed_indices = np.array(sorted(fixed), dtype=int)
    unknown_indices = np.array([i for i in range(len(nodes)) if i not in fixed], dtype=int)
    fixed_values = np.array([fixed[i] for i in fixed_indices], dtype=float)
    injection = np.zeros(len(nodes), dtype=float)
    for pair in pairs:
        _add_pair_current(injection, pair, pair["current_a"], index)
    voltages = np.zeros(len(nodes), dtype=float)
    voltages[fixed_indices] = fixed_values
    if len(unknown_indices):
        guu = gmat[unknown_indices, :][:, unknown_indices]
        guf = gmat[unknown_indices, :][:, fixed_indices]
        rhs = injection[unknown_indices] - guf @ fixed_values
        voltages[unknown_indices] = _solve_linear(guu, rhs, "static conductance matrix")
    outward = gmat @ voltages
    source_injection = np.zeros(len(nodes), dtype=float)
    source_injection[fixed_indices] = outward[fixed_indices] - injection[fixed_indices]
    residual = outward - injection - source_injection
    branch_rows = []
    for branch in model["resistors"]:
        current = (voltages[index[_name_key(branch["a"])]] -
                   voltages[index[_name_key(branch["b"]) ]]) / branch["resistance_ohm"]
        branch_rows.append((branch, float(current)))
    resistor_power = sum(current * current * branch["resistance_ohm"]
                         for branch, current in branch_rows)
    source_power = float(np.dot(voltages, source_injection))
    external_power = float(np.dot(voltages, injection))
    power_residual = source_power + external_power - resistor_power
    return {
        "index": index, "gmat": gmat, "cmat": cmat,
        "fixed": fixed, "fixed_indices": fixed_indices,
        "unknown_indices": unknown_indices,
        "voltages": voltages, "injection": injection,
        "source_injection": source_injection,
        "residual": residual, "branch_rows": branch_rows,
        "source_power": source_power, "external_power": external_power,
        "resistor_power": resistor_power,
        "power_residual": power_residual,
    }


def _material_class(material) -> str:
    if not isinstance(material, str) or not material.strip():
        return "UNKNOWN"
    text = material.casefold().replace("_", " ").strip()
    if text in ("ml1", "metal1", "metal 1", "m1"):
        return "ML1"
    if text in ("tin", "titanium nitride", "cnt tin") or "titanium nitride" in text:
        return "TiN"
    if text == "cnt" or text.startswith("cnt ") or "diffusion contact" in text:
        return "CNT"
    if "diffusion" in text or text in ("narea", "parea", "active", "soi"):
        return "diffusion"
    if text in ("mixed", "unknown"):
        return text.upper()
    return "OTHER"


def _geometry(branch):
    geometry = branch.get("geometry")
    details = {}
    if isinstance(geometry, dict):
        details.update(geometry)
    for key in ("a_location_um", "b_location_um", "width_um", "length_um",
                "thickness_um", "geometry_qualification"):
        if key in branch and branch[key] is not None:
            details[key] = branch[key]
    qualification = details.get("geometry_qualification")
    return details or None, (qualification if isinstance(qualification, str) and qualification
                             else "UNKNOWN")


def _qualified_limits(scenario, branches):
    raw = scenario.get("qualified_branch_limits", [])
    if not isinstance(raw, list):
        raise PowerIntegrityError("qualified_branch_limits must be a list")
    branch_names = {_name_key(branch["name"]): branch["name"] for branch in branches}
    limits = {}
    for item in raw:
        if not isinstance(item, dict):
            raise PowerIntegrityError("each qualified branch limit must be an object")
        name = _nonempty_text(item.get("branch_name"), "qualified limit branch_name")
        key = _name_key(name)
        if key not in branch_names:
            raise PowerIntegrityError("qualified limit references unknown resistor %r" % name)
        if key in limits:
            raise PowerIntegrityError("duplicate qualified limit for %r" % name)
        if item.get("qualified") is not True:
            raise PowerIntegrityError("current limits may be used only when explicitly qualified=true")
        source = _nonempty_text(item.get("source"), "%s limit source" % name)
        value = _number(item.get("peak_current_limit_a"), "%s peak_current_limit_a" % name)
        if value <= 0:
            raise PowerIntegrityError("%s peak_current_limit_a must be positive" % name)
        limits[key] = {"peak_current_limit_a": value, "source": source,
                       "qualification": item.get("qualification")}
    return limits


def _stress_status(branch, peak_current, limits):
    limit = limits.get(_name_key(branch["name"]))
    if limit is None:
        return "UNQUALIFIED_SCREENING", None
    status = ("EXCEEDS_EXPLICIT_QUALIFIED_LIMIT"
              if peak_current > limit["peak_current_limit_a"]
              else "WITHIN_EXPLICIT_QUALIFIED_LIMIT")
    return status, limit


def _rankings(branches, key, magnitude=False):
    return [branch["name"] for branch in sorted(
        branches, key=lambda branch: abs(branch[key]) if magnitude else branch[key], reverse=True)]


def _logical_spice_lines(path):
    pending = ""
    with Path(path).open(encoding="utf-8") as source:
        for raw in source:
            line = raw.strip()
            if not line or line.startswith("*"):
                continue
            if line.startswith("+"):
                pending += " " + line[1:].strip()
            else:
                if pending:
                    yield pending
                pending = line
    if pending:
        yield pending


def _model_rdsw_provenance(network):
    """Read only selected included model-card RDSW values; never flatten models."""
    metadata = network.get("metadata", {})
    netlist_path = Path(network.get("source", "."))
    pending = []
    unresolved = []

    def queue_directive(directive, base, section=None):
        try:
            fields = shlex.split(directive)
        except ValueError:
            return
        if not fields:
            return
        command = fields[0].casefold()
        if command in (".include", ".inc") and len(fields) >= 2:
            path_text, selected = fields[1], section
        elif command == ".lib" and len(fields) >= 3:
            path_text, selected = fields[1], fields[2]
        else:
            return
        model_path = Path(path_text).expanduser()
        if not model_path.is_absolute():
            model_path = base / model_path
        model_path = model_path.resolve()
        if model_path.is_file():
            pending.append((model_path, selected))
        else:
            unresolved.append({"directive": directive, "resolved_path": str(model_path)})

    for directive in metadata.get("directives", []):
        if isinstance(directive, str):
            queue_directive(directive, netlist_path.parent)

    used_models = {str(mos.get("model", "")).casefold()
                   for mos in network.get("mos", []) if mos.get("model")}
    found = []
    visited = set()
    while pending:
        path, target_section = pending.pop()
        visit_key = (str(path), target_section.casefold() if target_section else None)
        if visit_key in visited:
            continue
        visited.add(visit_key)
        try:
            logical_lines = list(_logical_spice_lines(path))
        except OSError:
            unresolved.append({"resolved_path": str(path),
                               "reason": "unreadable model source"})
            continue
        section = None
        for line in logical_lines:
            try:
                fields = shlex.split(line)
            except ValueError:
                continue
            if not fields:
                continue
            command = fields[0].casefold()
            if command == ".lib" and len(fields) == 2:
                section = fields[1]
                continue
            if command == ".endl":
                section = None
                continue
            active = (target_section is None or section is None or
                      section.casefold() == target_section.casefold())
            if not active:
                continue
            if command in (".include", ".inc") or (command == ".lib" and len(fields) >= 3):
                queue_directive(line, path.parent, section or target_section)
            elif command == ".model" and len(fields) >= 2:
                model_name = fields[1]
                if model_name.casefold() not in used_models:
                    continue
                match = re.search(r"\bRDSW\s*=\s*([^\s)]+)", line, re.IGNORECASE)
                if match:
                    found.append({"model": model_name, "value_as_encoded": match.group(1),
                                  "source": str(path),
                                  "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "section": section or target_section,
                                  "interpretation": "model-card parameter as encoded; not converted to an external ohmic branch"})
    return {"model_card_RDSW_parameters": found,
            "model_sources": [{"path": path, "section": section}
                              for path, section in sorted(
                                  visited, key=lambda value: (value[0], value[1] or ""))],
            "unresolved_model_sources": unresolved,
            "model_card_RDSW_status": ("observed_in_selected_model_source" if found
                                       else "UNKNOWN_MODEL_SOURCE_OR_RDSW_VALUE")}


def _access_resistance_provenance(network):
    cnt = []
    for item in network.get("resistors", []):
        name = str(item.get("name", ""))
        material = str(item.get("material", ""))
        if name.casefold().startswith("rcnt") or _material_class(material) == "CNT":
            cnt.append({"name": name, "a": item.get("a"), "b": item.get("b"),
                        "resistance_ohm": item.get("resistance_ohm"),
                        "material": item.get("material", "UNKNOWN"),
                        "provenance": "retained extracted/assembled CNT branch; not re-added"})
    instance_rdsw = []
    for mos in network.get("mos", []):
        parameters = mos.get("parameters", {})
        if not isinstance(parameters, dict):
            continue
        values = {str(key).casefold(): value for key, value in parameters.items()}
        if "rdsw" in values:
            instance_rdsw.append({"name": mos.get("name"), "model": mos.get("model"),
                                  "value_as_encoded": values["rdsw"],
                                  "interpretation": "instance parameter; not converted to an external ohmic branch"})
    model_data = _model_rdsw_provenance(network)
    return {
        "assembled_measured_CNT_resistors": cnt,
        "retained_fitted_RDSW_parameters": instance_rdsw,
        **model_data,
        "additional_access_note": (
            "Unmodeled MOS access/contact resistance is not assumed zero. The linear network uses only supplied extracted R/C; "
            "model-card RDSW is provenance only, and assembled RCNT branches above are retained exactly once."),
    }


def _pair_result(pair, solution, nodes):
    index = solution["index"]
    voltage = solution["voltages"]
    pair_voltage = float(
        voltage[index[_name_key(pair["from_node"])] ] -
        voltage[index[_name_key(pair["to_node"])] ]
    )
    result = {"name": pair["name"], "from_node": pair["from_node"],
              "to_node": pair["to_node"], "current_a": pair["current_a"],
              "pair_voltage_V": pair_voltage,
              "absorbed_power_W": float(pair["current_a"] * pair_voltage)}
    if "source_from_node" in pair:
        source_from = pair["source_from_node"]
        source_to = pair["source_to_node"]
        result.update({"source_from_node": source_from, "source_to_node": source_to,
                       "supply_drop_V": float(voltage[index[_name_key(source_from)]] -
                                              voltage[index[_name_key(pair["from_node"])]]),
                       "return_drop_V": float(voltage[index[_name_key(pair["to_node"])]] -
                                              voltage[index[_name_key(source_to)]])})
    return result


def _static_case(network, scenario, case_name=None, access_branches=()):
    nodes, model = _edge_list(network, access_branches)
    sources_raw = scenario.get("fixed_sources_V")
    if not isinstance(sources_raw, dict) or not sources_raw:
        raise PowerIntegrityError("static scenario requires explicit fixed_sources_V node-to-voltage mapping")
    sources = {_node_name(node, "fixed source node"): _number(value, "%s fixed-source voltage" % node)
               for node, value in sources_raw.items()}
    index, _, _, _ = _matrices(nodes, model)
    pairs = _current_pairs(scenario.get("current_pairs"), nodes, index)
    for pair in pairs:
        for source_field in ("source_from_node", "source_to_node"):
            if source_field in pair and _name_key(pair[source_field]) not in {
                    _name_key(source) for source in sources}:
                raise PowerIntegrityError("%s %s must be an explicitly fixed source" %
                                          (pair["name"], source_field))
    solved = _static_solution(nodes, model, sources, pairs)
    limits = _qualified_limits(scenario, model["resistors"])
    branches = []
    for branch, current in solved["branch_rows"]:
        vdrop = (solved["voltages"][index[_name_key(branch["a"])]] -
                 solved["voltages"][index[_name_key(branch["b"])]] )
        power = current * current * branch["resistance_ohm"]
        status, limit = _stress_status(branch, abs(current), limits)
        geometry, geometry_status = _geometry(branch)
        branches.append({"name": branch["name"], "a": branch["a"], "b": branch["b"],
                         "resistance_ohm": branch["resistance_ohm"],
                         "material": branch.get("material", "UNKNOWN"),
                         "material_classification": _material_class(branch.get("material")),
                         "branch_type": branch["branch_type"],
                         "extracted_resistance_ohm": branch.get("extracted_resistance_ohm"),
                         "series_access": branch.get("series_access", []),
                         "current_limit_scope": ("retained physical resistor current only; added access remains unqualified"
                                                 if branch.get("series_access") else "extracted branch"),
                         "voltage_drop_V": float(vdrop), "current_a_from_a_to_b": current,
                         "peak_current_a": abs(current), "power_W": float(power),
                         "geometry": geometry, "geometry_status": geometry_status,
                         "stress_status": status, "qualified_limit": limit})
    source_currents = {nodes[i]: float(solved["source_injection"][i])
                       for i in solved["fixed_indices"]}
    load_results = [_pair_result(pair, solved, nodes) for pair in pairs]
    voltage_map = {node: float(solved["voltages"][i]) for i, node in enumerate(nodes)}
    scale = max(abs(solved["source_power"]), abs(solved["external_power"]),
                abs(solved["resistor_power"]), 1e-300)
    return {
        "name": case_name or scenario.get("name", "unnamed"),
        "case_type": "conditional_access_sensitivity" if access_branches else "extracted_network_baseline",
        "status": "UNQUALIFIED_SCREENING",
        "model_scope": "current-driven linear resistor network; MOS channels/nonlinear device coupling are not solved",
        "fixed_sources_V": sources,
        "node_voltage_V": voltage_map,
        "source_currents_a_into_network": source_currents,
        "current_pairs": load_results,
        "branches": branches,
        "rankings": {
            "by_power_loss_W": _rankings(branches, "power_W"),
            "by_abs_current_A": _rankings(branches, "current_a_from_a_to_b", magnitude=True),
            "by_abs_voltage_drop_V": _rankings(branches, "voltage_drop_V", magnitude=True),
        },
        "power_balance": {"source_power_W": solved["source_power"],
                           "external_current_pair_power_W": solved["external_power"],
                           "resistor_dissipation_W": solved["resistor_power"],
                           "residual_W": solved["power_residual"],
                           "relative_residual": abs(solved["power_residual"]) / scale},
        "kcl": {"max_abs_residual_A": float(np.max(np.abs(solved["residual"])))},
        "qualification": {
            "current_stress": "qualified only for branches with explicitly qualified limits; otherwise screening only",
            "temperature": "UNASSESSED_NO_TEMPERATURE_MODEL_OR_QUALIFIED_LIMITS",
            "geometry": "explicit source geometry only; absent geometry fields remain UNKNOWN",
        },
        "thermal_input": {
            "times_s": [0.0],
            "branches": [{"name": branch["name"],
                          "material": branch["material"],
                          "resistance_ohm": branch["resistance_ohm"],
                          "current_a": [branch["current_a_from_a_to_b"]]}
                         for branch in branches],
            "device_power_w": None,
        },
    }


def _validate_sensitivity_cases(scenario, network):
    cases = scenario.get("access_sensitivity_cases", [])
    if not isinstance(cases, list):
        raise PowerIntegrityError("access_sensitivity_cases must be a list")
    actual_names = {_name_key(item.get("name", "")) for item in network.get("resistors", [])}
    parsed = []
    names = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PowerIntegrityError("each access sensitivity case must be an object")
        case_name = _nonempty_text(case.get("name"), "access sensitivity case name")
        if _name_key(case_name) in names:
            raise PowerIntegrityError("duplicate access sensitivity case %r" % case_name)
        names.add(_name_key(case_name))
        branches = case.get("branches")
        if not isinstance(branches, list) or not branches:
            raise PowerIntegrityError("sensitivity case %s needs at least one explicit branch" % case_name)
        for branch in branches:
            if not isinstance(branch, dict):
                raise PowerIntegrityError("sensitivity branches must be objects")
            name = _nonempty_text(branch.get("name"), "sensitivity branch name")
            if _name_key(name) in actual_names:
                raise PowerIntegrityError("sensitivity branch %r duplicates an extracted resistor" % name)
            _nonempty_text(branch.get("basis"), "%s sensitivity basis" % name)
            _nonempty_text(branch.get("uncertainty"), "%s sensitivity uncertainty" % name)
            exclusions = branch.get("excludes_existing")
            if not isinstance(exclusions, list) or not _REQUIRED_SENSITIVITY_EXCLUSIONS.issubset(
                    {_name_key(value) for value in exclusions if isinstance(value, str)}):
                raise PowerIntegrityError(
                    "%s must explicitly exclude measured_CNT and fitted_RDSW to prevent double counting" % name)
            if _number(branch.get("resistance_ohm"), "%s sensitivity resistance_ohm" % name) <= 0:
                raise PowerIntegrityError("%s sensitivity resistance must be positive" % name)
        parsed.append((case_name, branches))
    return parsed


def analyze_static(network: dict, scenario: dict) -> dict:
    """Solve one DC scenario and any explicitly requested access sensitivities."""
    if not isinstance(scenario, dict):
        raise PowerIntegrityError("static scenario must be an object")
    baseline = _static_case(network, scenario)
    access = _access_resistance_provenance(network)
    cases = _validate_sensitivity_cases(scenario, network)
    sensitivities = []
    for name, branches in cases:
        result = _static_case(network, scenario, case_name=name, access_branches=branches)
        result["sensitivity_basis"] = [branch["basis"] for branch in branches]
        result["uncertainty"] = [branch["uncertainty"] for branch in branches]
        result["qualification"] = "ILLUSTRATIVE_CONDITIONAL_UNQUALIFIED"
        sensitivities.append(result)
    return {"baseline": baseline,
            "access_resistance_provenance": access,
            "access_sensitivity_status": ("PROVIDED_CONDITIONAL_UNQUALIFIED_CASES"
                                          if sensitivities else "MISSING_EXPLICIT_SENSITIVITY"),
            "access_sensitivity_cases": sensitivities}


def _read_csv(path, scenario):
    time_column = scenario.get("time_column", "time_s")
    if not isinstance(time_column, str) or not time_column:
        raise PowerIntegrityError("time_column must identify the CSV time field (default time_s)")
    try:
        stream = Path(path).open(newline="", encoding="utf-8")
    except OSError as exc:
        raise PowerIntegrityError("cannot open waveform CSV %s: %s" % (path, exc)) from exc
    with stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise PowerIntegrityError("waveform CSV has no header")
        fields = [field.strip() for field in reader.fieldnames]
        if len(fields) != len(set(fields)):
            raise PowerIntegrityError("waveform CSV has duplicate column names")
        rows = []
        for line_number, raw in enumerate(reader, start=2):
            if not raw or all(value is None or not str(value).strip() for value in raw.values()):
                continue
            row = {field.strip(): value for field, value in raw.items() if field is not None}
            rows.append((line_number, row))
    if time_column not in fields:
        raise PowerIntegrityError("waveform CSV lacks time column %r" % time_column)
    if not rows:
        raise PowerIntegrityError("waveform CSV has no samples")
    times = []
    for line_number, row in rows:
        if time_column not in row or row[time_column] is None:
            raise PowerIntegrityError("missing %s at CSV line %d" % (time_column, line_number))
        times.append(_number(row[time_column], "CSV line %d %s" % (line_number, time_column)))
    times = np.asarray(times, dtype=float)
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise PowerIntegrityError("waveform times must be strictly increasing with at least two samples")
    return fields, rows, times


def _column_values(fields, rows, column, description):
    if not isinstance(column, str) or column not in fields:
        raise PowerIntegrityError("%s column %r is absent from waveform CSV" % (description, column))
    values = []
    for line_number, row in rows:
        value = row.get(column)
        if value is None or not str(value).strip():
            raise PowerIntegrityError("missing %s at CSV line %d" % (description, line_number))
        values.append(_number(value, "CSV line %d %s" % (line_number, description)))
    return np.asarray(values, dtype=float)


def _dynamic_sources(specification, fields, rows, nodes):
    if not isinstance(specification, dict) or not specification:
        raise PowerIntegrityError("dynamic analysis requires explicit fixed_sources specifications")
    node_lookup = {_name_key(node): node for node in nodes}
    parsed = {}
    histories = {}
    for node, source in specification.items():
        name = _node_name(node, "fixed source node")
        key = _name_key(name)
        if key not in node_lookup:
            raise PowerIntegrityError("fixed source node %r is absent from the network" % name)
        if key in parsed:
            raise PowerIntegrityError("duplicate fixed source node %r" % name)
        if not isinstance(source, dict):
            raise PowerIntegrityError("fixed source %s needs an explicit kind and value/column" % name)
        kind = source.get("kind")
        if kind == "constant":
            value = _number(source.get("value_V"), "%s value_V" % name)
            values = np.full(len(rows), value, dtype=float)
        elif kind == "waveform":
            values = _column_values(fields, rows, source.get("column"),
                                    "%s fixed-source voltage" % name)
        else:
            raise PowerIntegrityError("fixed source %s kind must be constant or waveform" % name)
        canonical = node_lookup[key]
        parsed[canonical] = float(values[0])
        histories[canonical] = values
    return parsed, histories


def _dynamic_current_data(specification, fields, rows, nodes, index, fixed_sources):
    if not isinstance(specification, list) or not specification:
        raise PowerIntegrityError("dynamic analysis requires actual current_waveforms including supply current")
    pairs = _current_pairs(specification, nodes, index, dynamic=True)
    source_names, columns = set(), set()
    supply_count = 0
    histories = []
    fixed_keys = {_name_key(node) for node in fixed_sources}
    node_keys = {_name_key(node) for node in nodes}
    for pair in pairs:
        name_key = _name_key(pair["source_name"])
        col_key = _name_key(pair["column"])
        if name_key in source_names or col_key in columns:
            raise PowerIntegrityError("each dynamic current source/column may be mapped once; duplicate would double count")
        source_names.add(name_key)
        columns.add(col_key)
        if _name_key(pair["source_node"]) not in node_keys:
            raise PowerIntegrityError("%s source_node is absent from the network" % pair["name"])
        if pair["role"] == "supply":
            supply_count += 1
            if _name_key(pair["source_node"]) not in fixed_keys:
                raise PowerIntegrityError("supply current source_node must be an explicitly fixed source")
        raw = _column_values(fields, rows, pair["column"], "%s current" % pair["source_name"])
        sign = -1.0 if pair["current_polarity"] == "positive_into_source" else 1.0
        histories.append({**pair, "current_a_from_to": sign * raw})
    if supply_count != 1:
        raise PowerIntegrityError("exactly one actual supply-current mapping with role='supply' is required")
    return histories


def _source_vector(nodes, index, source_histories, sample):
    vector = np.zeros(len(nodes), dtype=float)
    for node, values in source_histories.items():
        vector[index[_name_key(node)]] = values[sample]
    return vector


def _injection_history(node_count, index, current_histories):
    count = len(current_histories[0]["current_a_from_to"])
    injections = np.zeros((count, node_count), dtype=float)
    for current in current_histories:
        positive = index[_name_key(current["from_node"])]
        negative = index[_name_key(current["to_node"])]
        values = current["current_a_from_to"]
        injections[:, positive] -= values
        injections[:, negative] += values
    return injections


def _explicit_initial_state(initial, nodes, fixed_nodes, fixed_at_start):
    if not isinstance(initial, dict):
        raise PowerIntegrityError("initial_state must explicitly choose kind=dc or kind=explicit")
    kind = initial.get("kind")
    if kind not in ("dc", "explicit"):
        raise PowerIntegrityError("initial_state kind must be dc or explicit")
    fixed_keys = {_name_key(node) for node in fixed_nodes}
    if kind == "dc":
        if "node_voltages_V" in initial:
            raise PowerIntegrityError("dc initial_state must not also specify node_voltages_V")
        return kind, None
    values = initial.get("node_voltages_V")
    if not isinstance(values, dict):
        raise PowerIntegrityError("explicit initial_state needs node_voltages_V mapping")
    required = {_name_key(node): node for node in nodes if _name_key(node) not in fixed_keys}
    supplied = {_name_key(node): node for node in values}
    unexpected = set(supplied) - set(required)
    missing = set(required) - set(supplied)
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing %s" % ", ".join(required[key] for key in sorted(missing)[:8]))
        if unexpected:
            details.append("not an unknown node %s" % ", ".join(supplied[key] for key in sorted(unexpected)[:8]))
        raise PowerIntegrityError("explicit initial_state node set mismatch: %s" % "; ".join(details))
    vector = np.zeros(len(nodes), dtype=float)
    for i, node in enumerate(nodes):
        if _name_key(node) in fixed_keys:
            vector[i] = fixed_at_start[i]
        else:
            value_key = supplied[_name_key(node)]
            vector[i] = _number(values[value_key], "%s initial voltage" % node)
    return kind, vector


def _window_samples(times, values, start, stop):
    interior = (times > start) & (times < stop)
    selected_times = np.concatenate(([start], times[interior], [stop]))
    selected_values = np.interp(selected_times, times, values)
    return selected_times, selected_values






def _window_metrics(times, values, start, stop):
    values = np.asarray(values, dtype=float)
    integral = waveint.integral(times, values, start, stop)
    selected_times, selected_values = _window_samples(times, values, start, stop)
    positive = float(np.max(selected_values))
    negative = float(np.min(selected_values))
    duration = stop - start
    squared = waveint.integral_square(times, values, start, stop).net
    return {"signed_integral": float(integral.net),
            "average": float(integral.net / duration),
            "average_absolute": float(integral.gross / duration),
            "rms": math.sqrt(squared / duration),
            "peak_positive": positive,
            "peak_negative": negative,
            "peak_absolute": max(abs(positive), abs(negative)),
            "peak_time_s": float(selected_times[int(np.argmax(np.abs(selected_values)))])}


def _recovery_metrics(times, values):
    initial = float(values[0])
    minimum_index = int(np.argmin(values))
    minimum = float(values[minimum_index])
    recovery_time = None
    if minimum >= initial:
        recovery_time = float(times[0])
    else:
        for i in range(minimum_index + 1, len(times)):
            if values[i] >= initial:
                prev = i - 1
                if values[i] == values[prev]:
                    recovery_time = float(times[i])
                else:
                    fraction = (initial - values[prev]) / (values[i] - values[prev])
                    recovery_time = float(times[prev] + fraction * (times[i] - times[prev]))
                break
    return {"initial_voltage_V": initial, "minimum_voltage_V": minimum,
            "minimum_time_s": float(times[minimum_index]),
            "final_voltage_V": float(values[-1]),
            "recovery_to_initial_voltage_time_s": recovery_time,
            "recovered_to_initial_voltage": recovery_time is not None}


def _dynamic_case(network, scenario, times, source_histories, current_histories,
                  initial_kind, explicit_initial, case_name=None, access_branches=()):
    nodes, model = _edge_list(network, access_branches)
    index, gmat, cmat, adjacency = _matrices(nodes, model)
    fixed_at_start = _fixed_indices(nodes, index,
                                    {node: values[0] for node, values in source_histories.items()})
    fixed_indices = np.array(sorted(fixed_at_start), dtype=int)
    unknown_indices = np.array([i for i in range(len(nodes)) if i not in fixed_at_start], dtype=int)
    _check_anchoring(adjacency, nodes, set(fixed_at_start))
    fixed_nodes = {nodes[i]: source_histories[nodes[i]] for i in fixed_indices}
    source_values = np.zeros((len(times), len(nodes)), dtype=float)
    for node, values in fixed_nodes.items():
        source_values[:, index[_name_key(node)]] = values
    injections = _injection_history(len(nodes), index, current_histories)
    voltages = np.zeros((len(times), len(nodes)), dtype=float)
    voltages[:, fixed_indices] = source_values[:, fixed_indices]

    if initial_kind == "explicit":
        voltages[0] = explicit_initial
    elif len(unknown_indices):
        guu = gmat[unknown_indices, :][:, unknown_indices]
        guf = gmat[unknown_indices, :][:, fixed_indices]
        rhs = injections[0, unknown_indices] - guf @ source_values[0, fixed_indices]
        voltages[0, unknown_indices] = _solve_linear(guu, rhs, "initial DC conductance matrix")

    factor_cache = {}
    guu = gmat[unknown_indices, :][:, unknown_indices]
    guf = gmat[unknown_indices, :][:, fixed_indices]
    cuu = cmat[unknown_indices, :][:, unknown_indices]
    cuf = cmat[unknown_indices, :][:, fixed_indices]
    max_kcl_residual = 0.0
    max_power_residual = 0.0
    max_power_scale = 1e-300
    source_mid = np.zeros((len(times) - 1, len(fixed_indices)), dtype=float)
    source_mid_times = (times[:-1] + times[1:]) / 2.0
    for sample in range(1, len(times)):
        dt = float(times[sample] - times[sample - 1])
        if len(unknown_indices):
            a_uu = guu + (2.0 / dt) * cuu
            key = dt
            factor = factor_cache.get(key)
            if factor is None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", MatrixRankWarning)
                        factor = splu(a_uu.tocsc())
                except (MatrixRankWarning, RuntimeError, ValueError) as exc:
                    raise PowerIntegrityError(
                        "trapezoidal RC matrix is singular; floating nodes are not grounded implicitly") from exc
                factor_cache[key] = factor
            b_uu = (2.0 / dt) * cuu - guu
            b_uf = (2.0 / dt) * cuf - guf
            a_uf = guf + (2.0 / dt) * cuf
            rhs = (b_uu @ voltages[sample - 1, unknown_indices] +
                   b_uf @ voltages[sample - 1, fixed_indices] +
                   injections[sample, unknown_indices] + injections[sample - 1, unknown_indices] -
                   a_uf @ source_values[sample, fixed_indices])
            new_unknown = factor.solve(np.asarray(rhs, dtype=float))
            if not np.all(np.isfinite(new_unknown)):
                raise PowerIntegrityError("dynamic RC solve produced non-finite voltages")
            voltages[sample, unknown_indices] = new_unknown
        vmid = 0.5 * (voltages[sample] + voltages[sample - 1])
        dvdt = (voltages[sample] - voltages[sample - 1]) / dt
        imid = 0.5 * (injections[sample] + injections[sample - 1])
        residual = gmat @ vmid + cmat @ dvdt - imid
        if len(unknown_indices):
            max_kcl_residual = max(max_kcl_residual,
                                   float(np.max(np.abs(residual[unknown_indices]))))
        source_mid[sample - 1] = residual[fixed_indices]
        source_power = float(np.dot(vmid[fixed_indices], residual[fixed_indices]))
        load_power = float(np.dot(vmid, imid))
        resistor_power = float(vmid @ (gmat @ vmid))
        capacitor_energy_rate = float(vmid @ (cmat @ dvdt))
        power_residual = source_power + load_power - resistor_power - capacitor_energy_rate
        max_power_residual = max(max_power_residual, abs(power_residual))
        max_power_scale = max(max_power_scale, abs(source_power), abs(load_power),
                              abs(resistor_power), abs(capacitor_energy_rate))

    window = scenario["integration_window_s"]
    start, stop = (_number(window[0], "integration window start"),
                   _number(window[1], "integration window stop"))
    if not times[0] <= start < stop <= times[-1]:
        raise PowerIntegrityError("integration_window_s must lie within waveform times and have positive duration")
    limits = _qualified_limits(scenario, model["resistors"])
    branch_records = []
    branch_currents = {}
    branch_powers = {}
    for branch in model["resistors"]:
        ia = index[_name_key(branch["a"])]
        ib = index[_name_key(branch["b"])]
        current = (voltages[:, ia] - voltages[:, ib]) / branch["resistance_ohm"]
        power = current * current * branch["resistance_ohm"]
        branch_currents[branch["name"]] = current
        branch_powers[branch["name"]] = power
        metrics = _window_metrics(times, current, start, stop)
        energy = (branch["resistance_ohm"] *
                  waveint.integral_square(times, current, start, stop).net)
        status, limit = _stress_status(branch, metrics["peak_absolute"], limits)
        geometry, geometry_status = _geometry(branch)
        branch_records.append({"name": branch["name"], "a": branch["a"], "b": branch["b"],
                              "resistance_ohm": branch["resistance_ohm"],
                              "material": branch.get("material", "UNKNOWN"),
                              "material_classification": _material_class(branch.get("material")),
                              "branch_type": branch["branch_type"],
                              "extracted_resistance_ohm": branch.get("extracted_resistance_ohm"),
                              "series_access": branch.get("series_access", []),
                              "current_limit_scope": ("retained physical resistor current only; added access remains unqualified"
                                                      if branch.get("series_access") else "extracted branch"),
                              "current_a_from_a_to_b": current.tolist(),
                              "peak_current_a": metrics["peak_absolute"],
                              "peak_positive_current_a": metrics["peak_positive"],
                              "peak_negative_current_a": metrics["peak_negative"],
                              "average_current_a": metrics["average"],
                              "rms_current_a": metrics["rms"],
                              "average_absolute_current_a": metrics["average_absolute"],
                              "energy_J": float(energy),
                              "power_W_samples": power.tolist(),
                              "geometry": geometry, "geometry_status": geometry_status,
                              "stress_status": status, "qualified_limit": limit})

    node_histories = {node: voltages[:, i].tolist() for i, node in enumerate(nodes)}
    node_metrics = {}
    for node, values in node_histories.items():
        _, selected = _window_samples(times, np.asarray(values), start, stop)
        selected_times, _ = _window_samples(times, np.asarray(values), start, stop)
        node_metrics[node] = {"minimum_voltage_V": float(np.min(selected)),
                              "minimum_time_s": float(selected_times[int(np.argmin(selected))]),
                              "maximum_voltage_V": float(np.max(selected)),
                              "maximum_time_s": float(selected_times[int(np.argmax(selected))]),
                              "final_voltage_V": float(values[-1])}
    pair_histories = {}
    pair_metrics = {}
    for pair in current_histories:
        vf = voltages[:, index[_name_key(pair["from_node"])]]
        vt = voltages[:, index[_name_key(pair["to_node"])]]
        diff = vf - vt
        pair_histories[pair["name"]] = diff.tolist()
        _, clipped = _window_samples(times, diff, start, stop)
        clipped_times, _ = _window_samples(times, diff, start, stop)
        recovery = _recovery_metrics(clipped_times, clipped)
        record = {"source_name": pair["source_name"], "source_node": pair["source_node"],
                  "role": pair["role"], "column": pair["column"],
                  "current_polarity": pair["current_polarity"],
                  "from_node": pair["from_node"], "to_node": pair["to_node"],
                  "pair_voltage_history_V": diff.tolist(),
                  "pair_voltage_minimum_V": float(np.min(clipped)),
                  "pair_voltage_maximum_V": float(np.max(clipped)),
                  "recovery": recovery}
        source_fields = pair.get("pair", {})
        if "source_from_node" in source_fields and "source_to_node" in source_fields:
            source_diff = (voltages[:, index[_name_key(source_fields["source_from_node"])]] -
                           voltages[:, index[_name_key(source_fields["source_to_node"]) ]])
            record["supply_drop_history_V"] = (source_diff - diff).tolist()
            record["return_drop_history_V"] = (
                voltages[:, index[_name_key(pair["to_node"])]] -
                voltages[:, index[_name_key(source_fields["source_to_node"])]]).tolist()
        pair_metrics[pair["name"]] = record

    source_current_histories = {
        nodes[i]: source_mid[:, position].tolist()
        for position, i in enumerate(fixed_indices)}
    window_branch_energy = sum(branch["energy_J"] for branch in branch_records)
    stress_ranks = {
        "by_I2R_energy_J": _rankings(branch_records, "energy_J"),
        "by_peak_current_A": [branch["name"] for branch in sorted(
            branch_records, key=lambda branch: branch["peak_current_a"], reverse=True)],
        "by_rms_current_A": [branch["name"] for branch in sorted(
            branch_records, key=lambda branch: branch["rms_current_a"], reverse=True)],
    }
    source_specs = {_name_key(node): source
                    for node, source in scenario["fixed_sources"].items()}
    fixed_source_report = {
        node: {"kind": source_specs[_name_key(node)]["kind"],
               "voltage_history_V": values.tolist()}
        for node, values in source_histories.items()}
    result = {
        "name": case_name or scenario.get("name", "unnamed"),
        "case_type": "conditional_access_sensitivity" if access_branches else "extracted_network_baseline",
        "status": "UNQUALIFIED_SCREENING",
        "model_scope": "current-driven linear RC boundary reconstruction; MOS nonlinear channels are not solved or coupled",
        "numerics": {"method": "trapezoidal", "time_grid": "waveform_samples",
                     "interpolation": "linear", "initial_state_kind": initial_kind,
                     "integration_window_s": [start, stop],
                     "window_signal_interpolation": "linear_between_solver_samples",
                     "I2R_energy": "exact integral of squared piecewise-linear branch-current samples"},
        "waveform_provenance": scenario["waveform_provenance"],
        "waveform_csv": str(scenario["waveform_csv"]),
        "time_column": scenario.get("time_column", "time_s"),
        "time_s": times.tolist(),
        "fixed_sources": fixed_source_report,
        "current_sources": [{"source_name": current["source_name"],
                              "source_node": current["source_node"],
                              "role": current["role"], "column": current["column"],
                              "current_polarity": current["current_polarity"],
                              "pair": {"from_node": current["from_node"],
                                       "to_node": current["to_node"]},
                              "current_a_from_to": current["current_a_from_to"].tolist()}
                             for current in current_histories],
        "node_voltage_histories_V": node_histories,
        "node_voltage_metrics": node_metrics,
        "pair_voltage_histories_V": pair_histories,
        "pair_voltage_metrics": pair_metrics,
        "source_current_midpoint_times_s": source_mid_times.tolist(),
        "source_current_midpoint_a_into_network": source_current_histories,
        "branches": branch_records,
        "stress_rankings": stress_ranks,
        "residuals": {"max_abs_dynamic_KCL_A": max_kcl_residual,
                      "max_abs_dynamic_power_balance_W": max_power_residual,
                      "relative_power_balance_residual": max_power_residual / max_power_scale},
        "stress_window_I2R_energy_J": window_branch_energy,
        "qualification": {"current_stress": "qualified only for branches with explicitly qualified limits; otherwise screening only",
                          "temperature": "UNASSESSED_NO_TEMPERATURE_MODEL_OR_QUALIFIED_LIMITS",
                          "geometry": "explicit source geometry only; absent geometry fields remain UNKNOWN"},
        "thermal_input": {"times_s": times.tolist(),
                          "branches": [{"name": branch["name"],
                                        "material": branch["material"],
                                        "resistance_ohm": branch["resistance_ohm"],
                                        "current_a": branch["current_a_from_a_to_b"]}
                                       for branch in branch_records],
                          "device_power_w": None},
    }
    device_power = scenario.get("device_power")
    if device_power is not None:
        if not isinstance(device_power, dict) or device_power.get("units") != "W":
            raise PowerIntegrityError("device_power must specify column, units='W', and provenance")
        result["thermal_input"]["device_power_w"] = _column_values(
            fields, rows, device_power.get("column"), "device power [W]").tolist()
        result["thermal_input"]["device_power_provenance"] = _nonempty_text(
            device_power.get("provenance"), "device_power provenance")
    return result


def analyze_dynamic(network: dict, scenario: dict, *, base_dir=None) -> dict:
    """Solve one actual-current waveform, plus explicit uncertain access cases."""
    if not isinstance(scenario, dict):
        raise PowerIntegrityError("dynamic scenario must be an object")
    if scenario.get("numerical_method") != "trapezoidal":
        raise PowerIntegrityError("numerical_method must explicitly be 'trapezoidal'")
    if scenario.get("time_grid") != "waveform_samples":
        raise PowerIntegrityError("time_grid must explicitly be 'waveform_samples'")
    if scenario.get("interpolation") != "linear":
        raise PowerIntegrityError("interpolation must explicitly be 'linear'")
    provenance = scenario.get("waveform_provenance")
    if not isinstance(provenance, (str, dict)) or not provenance:
        raise PowerIntegrityError("waveform_provenance is mandatory for actual I_VDD/current histories")
    csv_path = Path(scenario.get("waveform_csv", ""))
    if not csv_path.is_absolute() and base_dir is not None:
        csv_path = Path(base_dir) / csv_path
    fields, rows, times = _read_csv(csv_path, scenario)
    nodes, _ = _edge_list(network)
    index = {_name_key(node): i for i, node in enumerate(nodes)}
    source_values, source_histories = _dynamic_sources(
        scenario.get("fixed_sources"), fields, rows, nodes)
    currents = _dynamic_current_data(scenario.get("current_waveforms"), fields, rows,
                                     nodes, index, source_values)
    supply_measurement = [item for item in currents if item["role"] == "supply"]
    if len(supply_measurement) != 1:
        raise PowerIntegrityError("dynamic analysis requires exactly one measured supply current")
    if "integration_window_s" not in scenario or not isinstance(scenario["integration_window_s"], list) or len(scenario["integration_window_s"]) != 2:
        raise PowerIntegrityError("integration_window_s=[start_s,stop_s] is mandatory")
    fixed_first = {node: float(values[0]) for node, values in source_histories.items()}
    # The common initial-state parser requires a canonical vector for fixed nodes.
    fixed_indices = _fixed_indices(nodes, index, fixed_first)
    fixed_vector = np.zeros(len(nodes), dtype=float)
    for i, value in fixed_indices.items():
        fixed_vector[i] = value
    initial_kind, explicit = _explicit_initial_state(
        scenario.get("initial_state"), nodes, fixed_first, fixed_vector)
    base = _dynamic_case(network, scenario, times, source_histories, currents,
                         initial_kind, explicit)
    access = _access_resistance_provenance(network)
    cases = _validate_sensitivity_cases(scenario, network)
    sensitivities = []
    for name, branches in cases:
        result = _dynamic_case(network, scenario, times, source_histories, currents,
                               initial_kind, explicit, case_name=name,
                               access_branches=branches)
        result["sensitivity_basis"] = [branch["basis"] for branch in branches]
        result["uncertainty"] = [branch["uncertainty"] for branch in branches]
        result["qualification"] = "ILLUSTRATIVE_CONDITIONAL_UNQUALIFIED"
        sensitivities.append(result)
    return {"baseline": base,
            "access_resistance_provenance": access,
            "access_sensitivity_status": ("PROVIDED_CONDITIONAL_UNQUALIFIED_CASES"
                                          if sensitivities else "MISSING_EXPLICIT_SENSITIVITY"),
            "access_sensitivity_cases": sensitivities}


def analyze_request(network: dict, request: dict, *, base_dir=None) -> dict:
    """Dispatch named scenarios without choosing implicit currents or boundaries."""
    if not isinstance(request, dict):
        raise PowerIntegrityError("analysis request must be an object")
    analysis_type = request.get("analysis_type")
    scenarios = request.get("scenarios")
    if analysis_type not in ("static", "dynamic"):
        raise PowerIntegrityError("analysis_type must be 'static' or 'dynamic'")
    if not isinstance(scenarios, list) or not scenarios:
        raise PowerIntegrityError("scenarios must contain one or more named scenarios")
    results = []
    for scenario in scenarios:
        if analysis_type == "static":
            results.append(analyze_static(network, scenario))
        else:
            results.append(analyze_dynamic(network, scenario, base_dir=base_dir))
    return {"schema_version": 1, "analysis_type": analysis_type,
            "network": {"name": network.get("name"), "source": network.get("source"),
                        "sha256": network.get("sha256"),
                        "resistor_count": len(network.get("resistors", [])),
                        "capacitor_count": len(network.get("capacitors", [])),
                        "mos_count": len(network.get("mos", [])),
                        "metadata": network.get("metadata", {})},
            "access_resistance_provenance": _access_resistance_provenance(network),
            "status": "UNQUALIFIED_SCREENING_NOT_FOUNDRY_SIGNOFF",
            "scenarios": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", help="JSON with network_path, analysis_type, and scenarios")
    parser.add_argument("-o", "--output", help="write JSON report here instead of stdout")
    args = parser.parse_args(argv)
    request_path = Path(args.input_json)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise PowerIntegrityError("input JSON root must be an object")
        network_path = request.get("network_path")
        if not isinstance(network_path, str) or not network_path:
            raise PowerIntegrityError("input JSON requires network_path to an assembled PEX/SPICE netlist")
        resolved_network = Path(network_path)
        if not resolved_network.is_absolute():
            resolved_network = request_path.resolve().parent / resolved_network
        network = read_network(resolved_network, top=request.get("top"))
        result = analyze_request(network, request, base_dir=request_path.resolve().parent)
        encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            Path(args.output).write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
        return 0
    except (OSError, json.JSONDecodeError, PowerIntegrityError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
