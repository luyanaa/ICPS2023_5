#!/usr/bin/env python3
"""Assemble KLayout LVS devices and PEX RC with explicit MOS terminal maps."""

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_LIBRARY = Path(__file__).resolve().parents[1] / "icps2023_5.lib.spice"
# PDK_minimalFabSOICMOS.pdf p.8 "Table of Sheet & Contact Resistance":
# measured resistance of ONE Al-diffusion contact opening.  The gate-side
# Al-TiN contact (0.9 ohm) is PEX-owned (CNT_TIN via model) and must NOT be
# added here.  The MOS core keeps its fitted RDSW; this is the explicit
# physical contact term, attached per opening count N_cnt (AGENTS.md 4.2).
CONTACT_RESISTANCE_OHM = {"NMOS": 150.0, "PMOS": 200.0}
DEVICE_COMMENT = re.compile(
    r"^\*\s+device instance\s+\S+\s+(\S+)\s+\S+\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+(NMOS4|NMOS|PMOS)\s*$",
    re.IGNORECASE,
)
NET_COMMENT = re.compile(r"^\*\s+net\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
SAFE_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


class AssemblyError(ValueError):
    """An input contract required for safe post-layout assembly is missing."""


def require(condition, message):
    if not condition:
        raise AssemblyError(message)


def _logical_spice_lines(path):
    """Join SPICE continuation lines before parsing named subcircuits."""
    logical = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if raw.lstrip().startswith("+") and logical:
            logical[-1] += " " + raw.lstrip()[1:].strip()
        else:
            logical.append(raw)
    return logical


def read_reference(path, top, substrate_net):
    pins = None
    global_nets = set()
    for raw in _logical_spice_lines(path):
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        fields = line.split()
        directive = fields[0].casefold()
        if directive == ".global":
            global_nets.update(net.casefold() for net in fields[1:])
        elif directive == ".subckt" and len(fields) >= 2 and fields[1].casefold() == top.casefold():
            require(pins is None, "reference contains duplicate .SUBCKT %s" % top)
            pins = fields[2:]
    require(pins is not None, "reference has no .SUBCKT %s" % top)
    require(substrate_net.casefold() in global_nets,
            "reference must declare .GLOBAL %s for MOS E terminals" % substrate_net)
    folded = [pin.casefold() for pin in pins]
    require(len(folded) == len(set(folded)), "reference .SUBCKT pins are not unique")
    for pin in pins:
        require(SAFE_NAME.fullmatch(pin) is not None,
                "unsupported SPICE top-level pin name %r" % pin)
    return pins




def read_lvs(path, top, reference_pins):
    lines = _logical_spice_lines(path)
    header_nodes = None
    net_names = {}
    devices = []
    tin_resistors = []
    current_device = None
    current_tin_tag = None
    in_top = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("* ICPS2023_5_R_TIN "):
            try:
                current_tin_tag = json.loads(line[len("* ICPS2023_5_R_TIN "):])
            except json.JSONDecodeError as error:
                raise AssemblyError("malformed ICPS2023_5_R_TIN metadata") from error
            require(isinstance(current_tin_tag, dict) and
                    {"instance", "a", "b", "r_ohm", "x_um", "y_um"} <= set(current_tin_tag),
                    "malformed ICPS2023_5_R_TIN metadata: %r" % current_tin_tag)
            require(isinstance(current_tin_tag["instance"], str) and
                    isinstance(current_tin_tag["a"], str) and
                    isinstance(current_tin_tag["b"], str),
                    "ICPS2023_5_R_TIN instance/endpoints must be strings")
            try:
                resistance = float(current_tin_tag["r_ohm"])
                x_um = float(current_tin_tag["x_um"])
                y_um = float(current_tin_tag["y_um"])
            except (TypeError, ValueError) as error:
                raise AssemblyError("ICPS2023_5_R_TIN has non-numeric geometry/value") from error
            require(math.isfinite(resistance) and resistance > 0 and
                    math.isfinite(x_um) and math.isfinite(y_um),
                    "ICPS2023_5_R_TIN has invalid geometry/value")
            current_tin_tag.update(r_ohm=resistance, x_um=x_um, y_um=y_um)
            continue
        if line.startswith("*"):
            match = NET_COMMENT.match(line)
            if match:
                net_names[match.group(1)] = match.group(2)
                continue
            match = DEVICE_COMMENT.match(line)
            if match:
                orientation, x_text, y_text, polarity = match.groups()
                current_device = {
                    "orientation": orientation.casefold(),
                    "polarity": polarity.upper(),
                    "x_um": float(x_text),
                    "y_um": float(y_text),
                }
            continue

        fields = line.split()
        directive = fields[0].casefold()
        if directive == ".subckt" and len(fields) >= 2 and fields[1].casefold() == top.casefold():
            require(header_nodes is None, "LVS output contains duplicate .SUBCKT %s" % top)
            header_nodes = fields[2:]
            in_top = True
            continue
        if directive == ".ends":
            in_top = False
            continue
        if not in_top or directive.startswith("."):
            continue
        if fields[0][0].casefold() == "m":
            require(current_tin_tag is None,
                    "R_TIN metadata is not followed by its resistor instance")
            require(current_device is not None,
                    "LVS MOS %s has no preceding device geometry comment" % fields[0])
            require(len(fields) >= 7,
                    "LVS MOS %s does not have D/G/S/body/model/parameters" % fields[0])
            polarity = fields[5].upper()
            body_tied = polarity == "NMOS4"
            require(polarity in ("NMOS", "PMOS", "NMOS4"),
                    "unsupported extracted MOS model %s" % fields[5])
            require(polarity == current_device["polarity"] or
                    (body_tied and current_device["polarity"] == "NMOS"),
                    "LVS MOS model and geometry comment disagree for %s" % fields[0])
            device = dict(current_device)
            device.update(
                name=fields[0],
                nets=fields[1:5],
                model="NMOS4" if body_tied else polarity,
                parameters=fields[6:],
            )
            if body_tied:
                device["polarity"] = "NMOS4"
                device["body"] = fields[4]
            device["key"] = "%s@%.6f,%.6f" % (
                device["polarity"], device["x_um"], device["y_um"]
            )
            devices.append(device)
            current_device = None
        elif fields[0][0].casefold() == "r":
            require(current_tin_tag is not None,
                    "unsupported extracted LVS resistor %s; only tagged R_TIN is accepted"
                    % fields[0])
            require(fields[0].casefold() == current_tin_tag["instance"].casefold(),
                    "ICPS2023_5_R_TIN metadata instance does not match %s" % fields[0])
            require(len(fields) >= 3,
                    "malformed LVS R_TIN element: %s" % line)
            line_resistance = None
            for token in fields[3:]:
                key, equals, value = token.partition("=")
                if equals and key.casefold() not in ("r", "resistance"):
                    continue
                candidate = value if equals else token
                try:
                    line_resistance = float(candidate)
                except ValueError:
                    continue
                break
            require(line_resistance is None or
                    (math.isfinite(line_resistance) and line_resistance > 0 and
                     math.isclose(line_resistance, current_tin_tag["r_ohm"],
                                  rel_tol=1e-6, abs_tol=1e-9)),
                    "LVS R_TIN metadata resistance disagrees with instance %s" % fields[0])
            record = dict(current_tin_tag)
            record.update(name=fields[0], nets=fields[1:3])
            record["key"] = "R_TIN@%.6f,%.6f" % (record["x_um"], record["y_um"])
            tin_resistors.append(record)
            current_tin_tag = None
        else:
            raise AssemblyError(
                "unsupported extracted LVS element %s; only MOS and tagged R_TIN are accepted"
                % fields[0]
            )

    require(current_tin_tag is None, "unused ICPS2023_5_R_TIN metadata record")
    require(header_nodes is not None, "LVS output has no .SUBCKT %s" % top)
    require(devices or tin_resistors, "LVS output contains no supported MOS/R_TIN devices")
    require(len(header_nodes) == len(reference_pins),
            "LVS top-level pin count differs from the reference")
    missing_names = [node for node in header_nodes if node not in net_names]
    require(not missing_names,
            "LVS top-level pin nodes have no * net names: %s" % missing_names)
    exported_names = [net_names[node].casefold() for node in header_nodes]
    require(set(exported_names) == {pin.casefold() for pin in reference_pins},
            "LVS extracted pin names do not match the reference: %s" % sorted(exported_names))
    pin_nodes = {}
    for node in header_nodes:
        pin_nodes[net_names[node].casefold()] = node

    for device in devices:
        require(device["orientation"] == "r0",
                "unsupported MOS orientation %s for %s; D/S mapping is qualified for R0 golden cells"
                % (device["orientation"], device["key"]))
        if device["model"] == "NMOS4":
            # Five-terminal schematic: LVS projects E out and keeps the body
            # tie as the fourth serialized net; it must not equal the source.
            require(device["body"] == device["nets"][3] and
                    device["body"] != device["nets"][2],
                    "NMOS4 body net is not the extracted fourth terminal for %s"
                    % device["key"])
        else:
            require(device["nets"][3] == device["nets"][2],
                    "extracted MOS body node is not source-tied for %s" % device["key"])
        params = {item.split("=", 1)[0].casefold(): item.split("=", 1)[1]
                  for item in device["parameters"] if "=" in item}
        require("l" in params and "w" in params,
                "LVS MOS %s lacks extracted L/W" % device["key"])
        require(float(params["l"].rstrip("uU")) > 0 and
                float(params["w"].rstrip("uU")) > 0,
                "LVS MOS %s has non-positive L/W" % device["key"])
    keys = [device["key"] for device in devices]
    require(len(keys) == len(set(keys)), "duplicate LVS MOS geometry keys")
    tin_keys = [device["key"] for device in tin_resistors]
    require(len(tin_keys) == len(set(tin_keys)), "duplicate LVS R_TIN geometry keys")
    return {
        "net_names": net_names, "pin_nodes": pin_nodes, "devices": devices,
        "tin_resistors": tin_resistors,
    }


def read_pex(path, top):
    port_maps = []
    mos_maps = []
    tin_resistor_maps = []
    resistor_maps = []
    elements = []
    pex_ports = None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        metadata_prefixes = (
            ("* PORT_MAP ", port_maps),
            ("* MOS_MAP ", mos_maps),
            ("* R_TIN_MAP ", tin_resistor_maps),
            ("* RESISTOR_MAP ", resistor_maps),
        )
        matched = False
        for prefix, records in metadata_prefixes:
            if line.startswith(prefix):
                try:
                    record = json.loads(line[len(prefix):])
                except json.JSONDecodeError as error:
                    raise AssemblyError("malformed %s metadata" % prefix[2:-1]) from error
                require(isinstance(record, dict),
                        "%s metadata record must be an object" % prefix[2:-1])
                records.append(record)
                matched = True
                break
        if matched:
            continue
        if line.startswith("*"):
            continue
        fields = line.split()
        directive = fields[0].casefold()
        if directive == ".subckt":
            require(len(fields) >= 3 and fields[1].casefold() == (top + "_pex").casefold(),
                    "PEX .SUBCKT does not match %s_pex" % top)
            require(pex_ports is None, "PEX output contains duplicate .SUBCKT")
            pex_ports = fields[2:]
        elif directive == ".ends":
            continue
        elif fields[0][0].casefold() in ("r", "c"):
            require(len(fields) == 4, "malformed PEX element: %s" % line)
            try:
                value = float(fields[3])
            except ValueError as error:
                raise AssemblyError("PEX element has non-numeric value: %s" % line) from error
            require(math.isfinite(value) and value > 0,
                    "PEX element must have a positive finite value: %s" % line)
            elements.append({"name": fields[0], "kind": fields[0][0].upper(),
                             "a": fields[1], "b": fields[2],
                             "value": fields[3], "numeric_value": value})
        else:
            raise AssemblyError("unsupported PEX element: %s" % line)
    require(pex_ports is not None, "PEX output has no .SUBCKT")
    require(pex_ports[-1].casefold() == "sub" and
            sum(port.casefold() == "sub" for port in pex_ports) == 1,
            "PEX special SUB return must appear once as the final subcircuit node")
    require(port_maps, "PEX output has no PORT_MAP records")
    require(mos_maps or tin_resistor_maps,
            "PEX output has neither MOS_MAP nor R_TIN_MAP records")
    for record in port_maps:
        require({"physical", "logical", "layer", "x_um", "y_um"} <= set(record),
                "malformed PORT_MAP record: %r" % record)
        require(record["physical"] in pex_ports[:-1],
                "PORT_MAP physical node is not a PEX subcircuit port: %s" % record["physical"])
    for record in mos_maps:
        require({"device", "polarity", "axis", "drain", "gate", "source",
                 "drain_contact", "source_contact", "e", "x_um", "y_um"} <= set(record),
                "malformed MOS_MAP record: %r" % record)
        require(record["axis"] == "x" and record.get("drain_side") == "positive" and
                record.get("source_side") == "negative",
                "unsupported MOS_MAP terminal axis/order for %s" % record.get("device"))
        require(record["e"].casefold() == "sub",
                "MOS_MAP E terminal must remain distinct on PEX SUB")
        if record.get("polarity", "").upper() == "NMOS4":
            require(isinstance(record.get("body"), str) and record["body"],
                    "NMOS4 MOS_MAP must name its PEX body node")
        elif "body" in record:
            require(False, "MOS_MAP body is supported only for NMOS4 devices")
        for field in ("drain_contact_cuts", "source_contact_cuts"):
            if field in record:
                cuts = record[field]
                require(isinstance(cuts, int) and not isinstance(cuts, bool)
                        and cuts >= 1,
                        "MOS_MAP %s must be a positive integer for %s: %r"
                        % (field, record.get("device"), cuts))
    map_keys = [record["device"] for record in mos_maps]
    require(len(map_keys) == len(set(map_keys)), "duplicate MOS_MAP device keys")
    tin_keys = [record.get("device") for record in tin_resistor_maps]
    require(all(isinstance(key, str) for key in tin_keys) and
            len(tin_keys) == len(set(tin_keys)),
            "malformed or duplicate R_TIN_MAP geometry keys")
    for record in tin_resistor_maps:
        require({"device", "x_um", "y_um", "endpoints"} <= set(record) and
                isinstance(record["endpoints"], list) and len(record["endpoints"]) == 2,
                "malformed R_TIN_MAP record: %r" % record)
        try:
            record["x_um"] = float(record["x_um"])
            record["y_um"] = float(record["y_um"])
        except (TypeError, ValueError) as error:
            raise AssemblyError("R_TIN_MAP has non-numeric geometry") from error
        require(math.isfinite(record["x_um"]) and math.isfinite(record["y_um"]),
                "R_TIN_MAP has non-finite geometry")
        for endpoint in record["endpoints"]:
            require(isinstance(endpoint, dict) and
                    {"node", "x_um", "y_um"} <= set(endpoint) and
                    isinstance(endpoint["node"], str),
                    "malformed R_TIN_MAP endpoints for %s" % record["device"])
            try:
                endpoint["x_um"] = float(endpoint["x_um"])
                endpoint["y_um"] = float(endpoint["y_um"])
            except (TypeError, ValueError) as error:
                raise AssemblyError("R_TIN_MAP endpoint has non-numeric geometry") from error
            require(math.isfinite(endpoint["x_um"]) and
                    math.isfinite(endpoint["y_um"]),
                    "R_TIN_MAP endpoint has non-finite geometry")

    allowed_materials = {
        "ML1", "TIN", "NAREA", "PAREA", "CNT_TIN", "CNT_NAREA",
        "CNT_PAREA", "UNKNOWN/MIXED",
    }
    map_by_name = {}
    for record in resistor_maps:
        require({"name", "a", "b", "resistance_ohm", "material",
                 "a_location_um", "b_location_um", "geometry_qualification"} <= set(record),
                "malformed RESISTOR_MAP record: %r" % record)
        require(record["material"] in allowed_materials,
                "unsupported RESISTOR_MAP material %r" % record.get("material"))
        require(isinstance(record["name"], str) and isinstance(record["a"], str) and
                isinstance(record["b"], str) and
                isinstance(record["geometry_qualification"], str) and
                record["geometry_qualification"],
                "malformed RESISTOR_MAP identity/qualification: %r" % record)
        try:
            resistance = float(record["resistance_ohm"])
        except (TypeError, ValueError) as error:
            raise AssemblyError("RESISTOR_MAP has non-numeric resistance") from error
        require(math.isfinite(resistance) and resistance > 0,
                "RESISTOR_MAP resistance must be positive finite")
        record["resistance_ohm"] = resistance
        for field in ("a_location_um", "b_location_um"):
            location = record[field]
            require(location is None or
                    (isinstance(location, list) and len(location) == 2 and
                     all(isinstance(value, (int, float)) and math.isfinite(value)
                         for value in location)),
                    "RESISTOR_MAP %s must be a finite XY pair or null" % field)
        key = record["name"].casefold()
        require(key not in map_by_name, "duplicate RESISTOR_MAP element %s" % record["name"])
        map_by_name[key] = record

    resistor_elements = [element for element in elements if element["kind"] == "R"]
    resistor_names = [element["name"].casefold() for element in resistor_elements]
    require(len(resistor_names) == len(set(resistor_names)),
            "duplicate PEX resistor instance names")
    require(set(map_by_name) == set(resistor_names),
            "RESISTOR_MAP/PEX resistor mismatch; unmapped=%s unused=%s"
            % (sorted(set(resistor_names) - set(map_by_name)),
               sorted(set(map_by_name) - set(resistor_names))))
    for element in resistor_elements:
        record = map_by_name[element["name"].casefold()]
        require((record["a"], record["b"]) == (element["a"], element["b"]),
                "RESISTOR_MAP endpoints disagree with PEX element %s" % element["name"])
        require(math.isclose(record["resistance_ohm"], element["numeric_value"],
                             rel_tol=1e-6, abs_tol=1e-9),
                "RESISTOR_MAP resistance disagrees with PEX element %s" % element["name"])

    nodes = set(pex_ports)
    for element in elements:
        nodes.update((element["a"], element["b"]))
    for record in mos_maps:
        nodes.update((record["drain"], record["gate"], record["source"]))
        if isinstance(record.get("body"), str):
            nodes.add(record["body"])
    for record in tin_resistor_maps:
        nodes.update(endpoint["node"] for endpoint in record["endpoints"])
    require(all(record["physical"] in nodes for record in port_maps),
            "PORT_MAP references a node absent from PEX network")
    require(all(record["drain"] in nodes and record["gate"] in nodes and
                record["source"] in nodes for record in mos_maps),
            "MOS_MAP references a node absent from PEX network")
    return {"ports": pex_ports, "port_maps": port_maps,
            "mos_maps": mos_maps, "tin_resistor_maps": tin_resistor_maps,
            "resistor_maps": resistor_maps, "elements": elements, "nodes": nodes}


def assemble(lvs_path, pex_path, schematic_path, top, output_path,
             model_library=DEFAULT_MODEL_LIBRARY, substrate_net="SUB",
             substrate_pin="E_SUB", require_multiple_terminals=False):
    pins = read_reference(schematic_path, top, substrate_net)
    require(substrate_pin.casefold() not in {pin.casefold() for pin in pins},
            "substrate pin %s collides with a physical top-level pin; choose --substrate-pin explicitly"
            % substrate_pin)
    require(SAFE_NAME.fullmatch(substrate_pin) is not None,
            "unsupported substrate pin name %r" % substrate_pin)
    lvs = read_lvs(lvs_path, top, pins)
    pex = read_pex(pex_path, top)

    pin_tokens = {}
    for pin in pins:
        candidates = {
            record["physical"] for record in pex["port_maps"]
            if record["logical"].casefold() == pin.casefold()
            and record["layer"].upper() == "ML1"
        }
        require(len(candidates) == 1,
                "top-level pin %s needs exactly one ML1 PORT_MAP anchor; found %s"
                % (pin, sorted(candidates)))
        pin_tokens[pin] = next(iter(candidates))
    map_logicals = {
        record["logical"].casefold() for record in pex["port_maps"]
        if record["layer"].upper() == "ML1"
    }
    require(map_logicals == {pin.casefold() for pin in pins},
            "ML1 PORT_MAP labels differ from LVS top-level pins")
    alias = {physical: pin for pin, physical in pin_tokens.items()}
    alias["SUB"] = substrate_pin
    for pin, physical in pin_tokens.items():
        require(pin.casefold() not in {node.casefold() for node in pex["nodes"]}
                or pin == physical,
                "top-level pin %s collides with a different PEX node" % pin)
    require(substrate_pin.casefold() not in {
        node.casefold() for node in pex["nodes"] if node.casefold() != "sub"
    }, "substrate formal collides with a PEX node")

    maps_by_key = {record["device"]: record for record in pex["mos_maps"]}
    lvs_by_key = {device["key"]: device for device in lvs["devices"]}
    require(set(maps_by_key) == set(lvs_by_key),
            "MOS_MAP/LVS device geometry mismatch; unmapped LVS=%s unmapped PEX=%s"
            % (sorted(set(lvs_by_key) - set(maps_by_key)),
               sorted(set(maps_by_key) - set(lvs_by_key))))
    tin_maps_by_key = {record["device"]: record for record in pex["tin_resistor_maps"]}
    lvs_tin_by_key = {device["key"]: device for device in lvs["tin_resistors"]}
    require(set(tin_maps_by_key) == set(lvs_tin_by_key),
            "R_TIN_MAP/LVS device geometry mismatch; unmapped LVS=%s unmapped PEX=%s"
            % (sorted(set(lvs_tin_by_key) - set(tin_maps_by_key)),
               sorted(set(tin_maps_by_key) - set(lvs_tin_by_key))))
    for key, mapping in tin_maps_by_key.items():
        device = lvs_tin_by_key[key]
        require(math.isclose(mapping["x_um"], device["x_um"], abs_tol=1e-6) and
                math.isclose(mapping["y_um"], device["y_um"], abs_tol=1e-6),
                "R_TIN_MAP/LVS geometry center mismatch for %s" % key)
        endpoints = mapping["endpoints"]
        require(endpoints[0]["node"] != endpoints[1]["node"],
                "R_TIN_MAP endpoints collapse to one PEX node for %s" % key)

    contact_nodes = defaultdict(set)
    contact_owners = defaultdict(list)
    for device in lvs["devices"]:
        mapping = maps_by_key[device["key"]]
        require(mapping["polarity"].upper() == device["model"],
                "MOS_MAP polarity mismatch for %s" % device["key"])
        require(mapping["source_contact"] != mapping["drain_contact"],
                "MOS source/drain claim the same contact for %s" % device["key"])
        contact_nodes[mapping["drain_contact"]].add(mapping["drain"])
        contact_nodes[mapping["source_contact"]].add(mapping["source"])
        contact_owners[mapping["drain_contact"]].append((device["key"], "D"))
        contact_owners[mapping["source_contact"]].append((device["key"], "S"))
    duplicate_contact_nodes = [key for key, mapped in contact_nodes.items() if len(mapped) != 1]
    require(not duplicate_contact_nodes,
            "MOS contact ownership maps one cut to multiple PEX nodes: %s"
            % sorted(duplicate_contact_nodes))

    node_alias = {node: alias.get(node, node) for node in pex["nodes"]}
    graph = defaultdict(set)
    graph_nodes = {node_alias[node] for node in pex["nodes"]}
    for element in pex["elements"]:
        a = node_alias[element["a"]]
        b = node_alias[element["b"]]
        graph.setdefault(a, set())
        graph.setdefault(b, set())
        if element["kind"] == "R":
            graph[a].add(b)
            graph[b].add(a)
    component_by_node = {}
    component_index = 0
    for node in graph_nodes:
        if node in component_by_node:
            continue
        stack = [node]
        component_by_node[node] = component_index
        while stack:
            current = stack.pop()
            for neighbor in graph[current]:
                if neighbor not in component_by_node:
                    component_by_node[neighbor] = component_index
                    stack.append(neighbor)
        component_index += 1

    anchors_by_lvs_net = defaultdict(list)
    for pin, physical in pin_tokens.items():
        net_id = lvs["pin_nodes"][pin.casefold()]
        node = node_alias[physical]
        anchors_by_lvs_net[net_id].append(("pin:%s" % pin, node))
    for device in lvs["devices"]:
        mapping = maps_by_key[device["key"]]
        for role, lvs_index in (("drain", 0), ("gate", 1), ("source", 2)):
            node = node_alias[mapping[role]]
            anchors_by_lvs_net[device["nets"][lvs_index]].append(
                ("mos:%s:%s" % (device["key"], role), node)
            )
        if device["model"] == "NMOS4":
            # The explicit P terminal is a first-class net anchor: the same
            # component-level open/short checks must cover it, not just the
            # D/G/S terminals. nets[3] is the extracted body tie net.
            body_node = node_alias[mapping["body"]]
            anchors_by_lvs_net[device["nets"][3]].append(
                ("mos:%s:body" % device["key"], body_node)
            )
    for device in lvs["tin_resistors"]:
        mapping = tin_maps_by_key[device["key"]]
        for index, endpoint in enumerate(mapping["endpoints"]):
            node = node_alias[endpoint["node"]]
            anchors_by_lvs_net[device["nets"][index]].append(
                ("rtin:%s:endpoint-%d" % (device["key"], index), node)
            )

    component_net_ids = defaultdict(set)
    for net_id, anchors in anchors_by_lvs_net.items():
        components = {component_by_node[node] for _label, node in anchors}
        require(len(components) == 1,
                "PEX open: LVS net %s has physical terminals in disconnected PEX components: %s"
                % (net_id, [label for label, _node in anchors]))
        component_net_ids[next(iter(components))].add(net_id)
    shorts = [sorted(net_ids) for net_ids in component_net_ids.values() if len(net_ids) > 1]
    require(not shorts, "PEX short: one resistive component contains distinct LVS nets %s" % shorts)

    anchored_components = {
        component_by_node[node]
        for anchors in anchors_by_lvs_net.values()
        for _label, node in anchors
    }
    anchored_components.add(component_by_node[node_alias["SUB"]])
    floating = [sorted(node for node in graph_nodes
                       if component_by_node[node] == index)
                for index in range(component_index)
                if index not in anchored_components]
    require(not floating, "floating PEX components are not attached to LVS pins/MOS/SUB: %s" % floating)

    multi_terminal_nets = 0
    for net_id, anchors in anchors_by_lvs_net.items():
        mos_nodes = {node for label, node in anchors if label.startswith("mos:")}
        if len(mos_nodes) > 1:
            multi_terminal_nets += 1
    require(not require_multiple_terminals or multi_terminal_nets > 0,
            "no LVS circuit net has multiple distinct physical MOS terminals")

    model_path = Path(model_library).resolve()
    require(model_path.is_file(), "BSIMSOI model library does not exist: %s" % model_path)
    lines = [
        "* Assembled from KLayout LVS devices, PEX RC, and physical terminal maps",
        '.lib "%s" b4soi' % model_path.as_posix().replace('"', '\\"'),
        "",
        ".SUBCKT %s %s" % (top, " ".join(pins + [substrate_pin])),
        "* PEX resistance and eligible passive-diffusion capacitance",
    ]
    for pex_node in sorted(pex["nodes"]):
        lines.append("* ASSEMBLY_NODE_MAP " + json.dumps(
            {"pex": pex_node, "assembled": node_alias[pex_node]},
            sort_keys=True, separators=(",", ":")))
    for index, device in enumerate(lvs["devices"], 1):
        record = {"name": "M%04d" % index, "layout_device": device["key"],
                  "body_state": ("body_tied" if device["model"] == "NMOS4"
                                 else "floating_body"), "e": substrate_pin}
        if device["model"] == "NMOS4":
            record["p"] = node_alias[maps_by_key[device["key"]]["body"]]
        lines.append("* MOS_DEVICE_MAP " + json.dumps(
            record, sort_keys=True, separators=(",", ":")))

    assembled_resistor_maps = []
    for record in pex["resistor_maps"]:
        assembled = dict(record)
        assembled["pex_a"], assembled["pex_b"] = record["a"], record["b"]
        assembled["a"] = node_alias[record["a"]]
        assembled["b"] = node_alias[record["b"]]
        assembled_resistor_maps.append(assembled)
    for index, device in enumerate(lvs["tin_resistors"], 1):
        mapping = tin_maps_by_key[device["key"]]
        endpoints = mapping["endpoints"]
        assembled_resistor_maps.append(
            {
                "name": "RTIN%04d" % index,
                "a": node_alias[endpoints[0]["node"]],
                "b": node_alias[endpoints[1]["node"]],
                "resistance_ohm": device["r_ohm"],
                "material": "TIN",
                "ownership": "lvs_device",
                "a_location_um": [endpoints[0]["x_um"], endpoints[0]["y_um"]],
                "b_location_um": [endpoints[1]["x_um"], endpoints[1]["y_um"]],
                "geometry_qualification": "explicit LVS R_TIN body; body excluded from PEX network",
                "source_lvs_instance": device["name"],
                "layout_device": device["key"],
            }
        )
    for record in assembled_resistor_maps:
        lines.append("* RESISTOR_MAP " + json.dumps(
            record, sort_keys=True, separators=(",", ":")))

    r_index = 0
    c_index = 0
    for element in pex["elements"]:
        a = node_alias[element["a"]]
        b = node_alias[element["b"]]
        if element["kind"] == "R":
            r_index += 1
            name = element["name"]
        else:
            c_index += 1
            name = "CPEX%04d" % c_index
        lines.append("%s %s %s %s" % (name, a, b, element["value"]))
    for index, device in enumerate(lvs["tin_resistors"], 1):
        mapping = tin_maps_by_key[device["key"]]
        endpoints = mapping["endpoints"]
        lines.append(
            "RTIN%04d %s %s %.6g"
            % (index, node_alias[endpoints[0]["node"]],
               node_alias[endpoints[1]["node"]], device["r_ohm"])
        )
    lines.append("* LVS-extracted MOS devices with explicit BSIMSOI E=SUB terminal")
    lines.append("* Explicit contact resistors R_cnt = R_unit/N_cnt sit between")
    lines.append("* each PEX node and its MOS D/S terminal (p.8 measured openings;")
    lines.append("* the gate side is PEX-owned CNT_TIN and is not repeated here)")
    lines.append("* RDSW_RCNT_SEPARATION_UNCALIBRATED: the fitted MOS has not")
    lines.append("* been contact-deembedded; adding RCNT is a model assumption,")
    lines.append("* not proof of independent physical resistance ownership.")
    contact_resistor_count = 0
    for index, device in enumerate(lvs["devices"], 1):
        mapping = maps_by_key[device["key"]]
        nmos4 = device["model"] == "NMOS4"
        model = "nch" if device["model"] in ("NMOS", "NMOS4") else "pch"
        r_unit = CONTACT_RESISTANCE_OHM["NMOS" if nmos4 else device["model"]]
        nodes = [node_alias[mapping[role]] for role in ("drain", "gate", "source")]
        body_node = None
        if nmos4:
            body_node = node_alias[mapping["body"]]
            require(body_node not in nodes,
                    "NMOS4 body node aliases a D/G/S terminal for %s" % device["key"])
        # Explicit contact access: one lumped resistor per D/S terminal,
        # R_cnt = R_unit / N_cnt with N_cnt the parallel CNT cut count that
        # PEX reported for that terminal.  The MOS terminal moves onto a
        # private internal node so the contact term is visible and editable
        # in the assembled deck instead of hiding inside RDSW.
        for role, cuts_field in (("drain", "drain_contact_cuts"),
                                 ("source", "source_contact_cuts")):
            n_cuts = mapping.get(cuts_field, 1)
            internal_node = "M%04d_%s" % (index, role.upper())
            r_cnt = r_unit / n_cuts
            contact_resistor_count += 1
            # ngspice treats trailing '*' text as a model parameter; keep the
            # provenance on its own comment line, which both engines accept.
            lines.append(
                "* %s %s contact, N_cnt=%d, R_unit=%.0f ohm"
                % (device["key"], role.upper(), n_cuts, r_unit)
            )
            rcnt_name = "RCNT%04d" % contact_resistor_count
            rcnt_a = nodes[0 if role == "drain" else 2]
            material = "CNT_NAREA" if model == "nch" else "CNT_PAREA"
            lines.append("* RESISTOR_MAP " + json.dumps(
                {
                    "name": rcnt_name,
                    "a": rcnt_a,
                    "b": internal_node,
                    "resistance_ohm": r_cnt,
                    "material": material,
                    "a_location_um": None,
                    "b_location_um": None,
                    "geometry_qualification":
                        "aggregate contact approximation; assumes identical parallel CNT openings",
                    "parallel_cuts": n_cuts,
                    "unit_contact_resistance_ohm": r_unit,
                    "ownership": "assembled_mos_contact",
                    "fit_separation_status": "RDSW_RCNT_SEPARATION_UNCALIBRATED",
                },
                sort_keys=True, separators=(",", ":")))
            lines.append("%s %s %s %.6g"
                         % (rcnt_name, rcnt_a, internal_node, r_cnt))
            nodes[0 if role == "drain" else 2] = internal_node
        if nmos4:
            lines.append("%s %s %s %s %s %s %s %s" % (
                "M%04d" % index, nodes[0], nodes[1], nodes[2], substrate_pin,
                body_node, model, " ".join(device["parameters"])))
        else:
            lines.append("%s %s %s %s %s %s %s" % (
                "M%04d" % index, nodes[0], nodes[1], nodes[2], substrate_pin,
                model, " ".join(device["parameters"])))
    lines.extend([".ENDS %s" % top, ""])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")

    return {
        "mos": len(lvs["devices"]),
        "resistors": r_index + len(lvs["tin_resistors"]),
        "pex_resistors": r_index,
        "capacitors": c_index,
        "contact_resistors": contact_resistor_count,
        "multi_terminal_nets": multi_terminal_nets,
        "contacts": len(contact_nodes),
        "shared_contacts": sum(len(owners) > 1 for owners in contact_owners.values()),
        "pins": pins,
        "substrate_pin": substrate_pin,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lvs", required=True, help="KLayout extracted SPICE netlist")
    parser.add_argument("--pex", required=True,
                        help="PEX SPICE with PORT_MAP/MOS_MAP/R_TIN_MAP/RESISTOR_MAP")
    parser.add_argument("--schematic", required=True, help="LVS reference SPICE netlist")
    parser.add_argument("--top", required=True, help="top circuit name")
    parser.add_argument("--output", required=True, help="assembled SPICE output")
    parser.add_argument("--model-library", default=str(DEFAULT_MODEL_LIBRARY))
    parser.add_argument("--substrate-net", default="SUB",
                        help="global LVS reference net used for MOS E, default SUB")
    parser.add_argument("--substrate-pin", default="E_SUB",
                        help="explicit assembled E-terminal pin, default E_SUB")
    parser.add_argument("--require-multiple-terminals", action="store_true",
                        help="require at least one logical net with multiple physical MOS anchors")
    args = parser.parse_args()
    try:
        result = assemble(
            args.lvs, args.pex, args.schematic, args.top, args.output,
            args.model_library, args.substrate_net, args.substrate_pin,
            args.require_multiple_terminals,
        )
    except (AssemblyError, OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    print(
        "assembled %s: MOS=%d R=%d C=%d contact_R=%d MOS_contacts=%d "
        "shared_contacts=%d multi_terminal_nets=%d output=%s"
        % (args.top, result["mos"], result["resistors"], result["capacitors"],
           result["contact_resistors"], result["contacts"],
           result["shared_contacts"], result["multi_terminal_nets"], args.output)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
