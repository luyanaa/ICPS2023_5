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
DEVICE_COMMENT = re.compile(
    r"^\*\s+device instance\s+\S+\s+(\S+)\s+\S+\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+(NMOS|PMOS)\s*$",
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
    current_device = None
    in_top = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        fields = line.split()
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
            require(current_device is not None,
                    "LVS MOS %s has no preceding device geometry comment" % fields[0])
            require(len(fields) >= 7,
                    "LVS MOS %s does not have D/G/S/body/model/parameters" % fields[0])
            polarity = fields[5].upper()
            require(polarity in ("NMOS", "PMOS"),
                    "unsupported extracted MOS model %s" % fields[5])
            require(polarity == current_device["polarity"],
                    "LVS MOS model and geometry comment disagree for %s" % fields[0])
            device = dict(current_device)
            device.update(
                name=fields[0],
                nets=fields[1:5],
                model=polarity,
                parameters=fields[6:],
            )
            device["key"] = "%s@%.6f,%.6f" % (
                polarity, device["x_um"], device["y_um"]
            )
            devices.append(device)
            current_device = None
        else:
            raise AssemblyError(
                "unsupported extracted LVS element %s; this assembler accepts MOS-only inverter/NAND cores"
                % fields[0]
            )

    require(header_nodes is not None, "LVS output has no .SUBCKT %s" % top)
    require(devices, "LVS output contains no MOS devices")
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
    return {"net_names": net_names, "pin_nodes": pin_nodes, "devices": devices}


def read_pex(path, top):
    port_maps = []
    mos_maps = []
    elements = []
    pex_ports = None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("* PORT_MAP "):
            port_maps.append(json.loads(line[len("* PORT_MAP "):]))
            continue
        if line.startswith("* MOS_MAP "):
            mos_maps.append(json.loads(line[len("* MOS_MAP "):]))
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
            value = float(fields[3])
            require(math.isfinite(value) and value > 0,
                    "PEX element must have a positive finite value: %s" % line)
            elements.append({"kind": fields[0][0].upper(), "a": fields[1],
                             "b": fields[2], "value": fields[3]})
        else:
            raise AssemblyError("unsupported PEX element: %s" % line)
    require(pex_ports is not None, "PEX output has no .SUBCKT")
    require(pex_ports[-1].casefold() == "sub" and
            sum(port.casefold() == "sub" for port in pex_ports) == 1,
            "PEX special SUB return must appear once as the final subcircuit node")
    require(port_maps, "PEX output has no PORT_MAP records")
    require(mos_maps, "PEX output has no MOS_MAP records")
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
    map_keys = [record["device"] for record in mos_maps]
    require(len(map_keys) == len(set(map_keys)), "duplicate MOS_MAP device keys")
    nodes = set(pex_ports)
    for element in elements:
        nodes.update((element["a"], element["b"]))
    for record in mos_maps:
        nodes.update((record["drain"], record["gate"], record["source"]))
    require(all(record["physical"] in nodes for record in port_maps),
            "PORT_MAP references a node absent from PEX network")
    require(all(record["drain"] in nodes and record["gate"] in nodes and
                record["source"] in nodes for record in mos_maps),
            "MOS_MAP references a node absent from PEX network")
    return {"ports": pex_ports, "port_maps": port_maps,
            "mos_maps": mos_maps, "elements": elements, "nodes": nodes}


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
    mos_anchor_nodes = set()
    for pin, physical in pin_tokens.items():
        net_id = lvs["pin_nodes"][pin.casefold()]
        node = node_alias[physical]
        anchors_by_lvs_net[net_id].append(("pin:%s" % pin, node))
    for device in lvs["devices"]:
        mapping = maps_by_key[device["key"]]
        for role, lvs_index in (("drain", 0), ("gate", 1), ("source", 2)):
            node = node_alias[mapping[role]]
            anchors_by_lvs_net[device["nets"][lvs_index]].append(
                ("%s:%s" % (device["key"], role), node)
            )
            mos_anchor_nodes.add(node)

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
        mos_nodes = {node for label, node in anchors if not label.startswith("pin:")}
        if len(mos_nodes) > 1:
            multi_terminal_nets += 1
    require(not require_multiple_terminals or multi_terminal_nets > 0,
            "no LVS circuit net has multiple distinct physical MOS terminals")

    model_path = Path(model_library).resolve()
    require(model_path.is_file(), "BSIMSOI model library does not exist: %s" % model_path)
    lines = [
        "* Assembled from KLayout LVS devices, PEX RC, and MOS_MAP anchors",
        '.lib "%s" b4soi' % model_path.as_posix().replace('"', '\\"'),
        "",
        ".SUBCKT %s %s" % (top, " ".join(pins + [substrate_pin])),
        "* PEX resistance and eligible passive-diffusion capacitance",
    ]
    r_index = 0
    c_index = 0
    for element in pex["elements"]:
        a = node_alias[element["a"]]
        b = node_alias[element["b"]]
        if element["kind"] == "R":
            r_index += 1
            name = "RPEX%04d" % r_index
        else:
            c_index += 1
            name = "CPEX%04d" % c_index
        lines.append("%s %s %s %s" % (name, a, b, element["value"]))
    lines.append("* LVS-extracted MOS devices with explicit BSIMSOI E=SUB terminal")
    for index, device in enumerate(lvs["devices"], 1):
        mapping = maps_by_key[device["key"]]
        model = "nch" if device["model"] == "NMOS" else "pch"
        nodes = [node_alias[mapping[role]] for role in ("drain", "gate", "source")]
        lines.append("%s %s %s %s %s %s %s" % (
            "M%04d" % index, nodes[0], nodes[1], nodes[2], substrate_pin,
            model, " ".join(device["parameters"])))
    lines.extend([".ENDS %s" % top, ""])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")

    return {
        "mos": len(lvs["devices"]),
        "resistors": r_index,
        "capacitors": c_index,
        "multi_terminal_nets": multi_terminal_nets,
        "contacts": len(contact_nodes),
        "shared_contacts": sum(len(owners) > 1 for owners in contact_owners.values()),
        "pins": pins,
        "substrate_pin": substrate_pin,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lvs", required=True, help="KLayout extracted SPICE netlist")
    parser.add_argument("--pex", required=True, help="PEX SPICE containing PORT_MAP/MOS_MAP")
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
        "assembled %s: MOS=%d R=%d C=%d MOS_contacts=%d shared_contacts=%d multi_terminal_nets=%d output=%s"
        % (args.top, result["mos"], result["resistors"], result["capacitors"],
           result["contacts"], result["shared_contacts"],
           result["multi_terminal_nets"], args.output)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
