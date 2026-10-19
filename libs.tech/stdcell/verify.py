#!/usr/bin/env python3
"""Canonical, strict physical checks for generated ICPS2023_5 1M layouts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from xml.etree import ElementTree

import klayout.db as pya

try:
    from . import model_binding
except ImportError:  # direct import from the stdcell tool directory
    import model_binding  # type: ignore

try:
    from .geometry import soi_edge_protection
except ImportError:  # direct import from the stdcell tool directory
    from geometry import soi_edge_protection  # type: ignore




ROOT = Path(__file__).resolve().parents[2]
PROCESS_PROFILE_RB = ROOT / "libs.tech/klayout/process_profile.rb"
PROCESS_PROFILES = ROOT / "libs.tech/klayout/process_profiles.json"
GEOMETRY = ROOT / "libs.tech/stdcell/geometry.py"
PROFILE = "icps2023_5_1m"
DRC = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
PEX_EVIDENCE = ROOT / "libs.tech/klayout/pex/pex_evidence.py"
XYCE_ADAPTER = ROOT / "libs.tech/ngspice/tools/xyce_b4soi.py"
ALLOWED_LAYERS = {(1, 0), (2, 0), (3, 0), (5, 0), (7, 0), (8, 0),
                  (13, 0), (14, 0), (18, 0), (19, 0), (21, 0), (100, 0)}
_pex_loader = importlib.util.spec_from_file_location("icps_pex_evidence", PEX_EVIDENCE)
_pex_evidence_module = importlib.util.module_from_spec(_pex_loader)
_pex_loader.loader.exec_module(_pex_evidence_module)


def _model_reference(output_dir: Path, binding: dict) -> tuple[Path, dict]:
    """Return the SPICE library reference the assembled netlist must use.

    The configured card is engine-scoped.  An Xyce binding is materialised as a
    ported card plus a generated selector inside the evidence directory, so the
    assembled postlayout netlist is directly runnable by the engine the card was
    qualified on; an ngspice binding uses the checked-in selector unchanged.
    """
    record = {
        "engine": binding["engine"],
        "engine_version": binding["engine_version"],
        "engine_level": binding["engine_level"],
        "engine_path": binding["engine_path"],
        "scope": binding["scope"],
        "library": binding["library"],
        "include": binding["include"],
        "library_sha256": sha256(binding["library_path"]),
        "include_sha256": sha256(binding["include_path"]),
    }
    if binding["engine"] != "xyce":
        return binding["library_path"], record
    loader = importlib.util.spec_from_file_location("icps_xyce_b4soi", XYCE_ADAPTER)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    card = output_dir / "model_card_xyce.inc"
    card.write_text(module.port_card(
        Path(binding["include_path"]).read_text(encoding="utf-8"),
        binding["engine_level"]), encoding="utf-8")
    reference = output_dir / "model_reference.lib.spice"
    reference.write_text(
        "* Evidence-local selector for the %s binding.\n"
        "* Ported card LEVEL=%d; run this netlist with %s only.\n"
        ".lib %s\n.include \"%s\"\n.endl %s\n" % (
            binding["engine_version"], binding["engine_level"],
            binding["engine"] if binding["engine_path"] is None else binding["engine_path"],
            binding["section"], card, binding["section"]), encoding="utf-8")
    record["ported_card"] = str(card)
    record["ported_card_sha256"] = sha256(card)
    record["reference"] = str(reference)
    return reference, record


class VerificationError(RuntimeError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _comment_records(path, prefix):
    marker = "* %s " % prefix
    records = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith(marker):
            try:
                value = json.loads(line[len(marker):])
            except json.JSONDecodeError as error:
                raise ValueError("%s line %d has malformed %s JSON" %
                                 (path, line_number, prefix)) from error
            if not isinstance(value, dict):
                raise ValueError("%s line %d %s must be a JSON object" %
                                 (path, line_number, prefix))
            records.append(value)
    return records


def _reliability_network(path, top):
    source = ROOT / "libs.tech/ngspice/tools/reliability_netlist.py"
    loader = importlib.util.spec_from_file_location("icps_pex_compare_netlist", source)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module.read_network(path, top=top)


def _close(a, b, *, rtol=1e-6, atol=1e-12):
    try:
        return math.isclose(float(a), float(b), rel_tol=rtol, abs_tol=atol)
    except (TypeError, ValueError):
        return False


def _device_map(records, mode, differences):
    indexed = {}
    for record in records:
        try:
            key = (str(record["polarity"]).casefold(),
                   round(float(record["x_um"]), 6),
                   round(float(record["y_um"]), 6))
        except (KeyError, TypeError, ValueError):
            differences.append({"kind": "malformed_mos_map", "mode": mode,
                                "record": record})
            continue
        if key in indexed:
            differences.append({"kind": "duplicate_mos_geometry", "mode": mode,
                                "key": key})
        indexed[key] = record
    return indexed


def _contact_owners(records):
    owners = {}
    for record in records:
        try:
            device_key = "%s@%.6f,%.6f" % (
                str(record.get("polarity", "")).upper(),
                float(record["x_um"]), float(record["y_um"]))
        except (KeyError, TypeError, ValueError):
            continue
        for terminal in ("drain", "source"):
            contact = record.get(terminal + "_contact")
            if contact is not None:
                owners.setdefault(str(contact), []).append(device_key + ":" + terminal)
    return sorted(sorted(items) for items in owners.values())



def _terminal_partition(indexed, mode, not_comparable):
    nodes = {}
    for key, record in indexed.items():
        device = "%s@%.6f,%.6f" % key
        for terminal in ("drain", "gate", "source", "e"):
            node = record.get(terminal)
            if not isinstance(node, str) or not node.strip():
                not_comparable.append(
                    "%s MOS %s has no explicit %s terminal node" % (mode, device, terminal))
                continue
            nodes.setdefault(node.strip().casefold(), []).append(device + ":" + terminal)
    return sorted(tuple(sorted(roles)) for roles in nodes.values())

def _map_tin(records, mode, differences):
    indexed = {}
    for record in records:
        try:
            center = (round(float(record["x_um"]), 6), round(float(record["y_um"]), 6))
            endpoints = sorted((round(float(item["x_um"]), 6),
                                round(float(item["y_um"]), 6))
                               for item in record["endpoints"])
        except (KeyError, TypeError, ValueError):
            differences.append({"kind": "malformed_r_tin_map", "mode": mode,
                                "record": record})
            continue
        if len(endpoints) != 2 or center in indexed:
            differences.append({"kind": "ambiguous_r_tin_geometry", "mode": mode,
                                "center_um": center})
        indexed[center] = endpoints
    return indexed


def _rc_summary(network):
    resistors = {}
    for branch in network["resistors"]:
        material = str(branch.get("material", "UNKNOWN")).upper()
        resistors.setdefault(material, []).append(float(branch["resistance_ohm"]))
    return {
        "resistor_count": len(network["resistors"]),
        "resistance_by_material_ohm": {
            key: sorted(values) for key, values in sorted(resistors.items())
        },
        "capacitor_count": len(network["capacitors"]),
        "capacitance_values_f": sorted(float(item["capacitance_f"])
                                       for item in network["capacitors"]),
    }


def _compare_numeric_lists(design, fab, *, label, rtol, atol, differences):
    if len(design) != len(fab):
        differences.append({"kind": label + "_count", "design": len(design), "fab": len(fab)})
        return
    mismatches = [{"index": index, "design": left, "fab": right}
                  for index, (left, right) in enumerate(zip(design, fab))
                  if not _close(left, right, rtol=rtol, atol=atol)]
    if mismatches:
        differences.append({"kind": label + "_values", "mismatches": mismatches})


def compare_pex_modes(design_pex, fab_pex, top, design_assembled=None, fab_assembled=None):
    """Compare PEX evidence by physical roles/geometry, never generated node names."""
    result = {
        "status": "NOT_COMPARABLE",
        "node_name_equality_used": False,
        "scope": "MOS terminal/contact ownership and physical geometry plus extracted R/C summaries; not arbitrary topology or calibrated RC equivalence",
        "tolerances": {
            "physical_coordinate_um": 1e-6,
            "resistance_relative": 1e-6,
            "resistance_absolute_ohm": 1e-12,
            "capacitance_relative": 1e-6,
            "capacitance_absolute_f": 1e-30,
            "basis": "physical maps are emitted to 1e-6 um; SPICE R/C serialization uses six significant digits",
        },
        "differences": [],
        "not_comparable_reasons": [],
    }
    differences = result["differences"]
    try:
        pex_paths = {"design": Path(design_pex), "fab": Path(fab_pex)}
        access = {mode: _comment_records(path, "MOS_ACCESS_RESULT")
                  for mode, path in pex_paths.items()}
        expected_layers = {"NAREA", "PAREA"}
        for mode in ("design", "fab"):
            rows = access[mode]
            layers = [str(item.get("layer", "")).upper() for item in rows]
            if len(rows) != 2 or set(layers) != expected_layers:
                result["not_comparable_reasons"].append(
                    "%s PEX must provide one MOS access summary for each of NAREA and PAREA" % mode)
                continue
            access[mode] = {str(item["layer"]).upper(): item for item in rows}
        for mode in ("design", "fab"):
            records = access[mode]
            if not isinstance(records, dict):
                continue
            for layer, record in records.items():
                if record.get("passed") is not True or record.get("failures") != []:
                    result["not_comparable_reasons"].append(
                        "%s %s MOS access summary is not a passing empty-failure report" %
                        (mode, layer))
        if not result["not_comparable_reasons"]:
            access_fields = ("gates", "bounded", "unbounded", "ambiguous", "orphaned",
                             "failures", "passed")
            for layer in sorted(expected_layers):
                left, right = access["design"][layer], access["fab"][layer]
                for field in access_fields:
                    if left.get(field) != right.get(field):
                        differences.append({"kind": "mos_access_summary",
                                            "layer": layer, "field": field,
                                            "design": left.get(field), "fab": right.get(field)})

        mos_maps = {mode: _comment_records(path, "MOS_MAP")
                    for mode, path in pex_paths.items()}
        design_net = _reliability_network(design_pex, top + "_pex")
        fab_net = _reliability_network(fab_pex, top + "_pex")
        if not mos_maps["design"] and not mos_maps["fab"]:
            if design_net["mos"] or fab_net["mos"]:
                result["not_comparable_reasons"].append("MOS devices exist but MOS_MAP evidence is absent")
        else:
            left = _device_map(mos_maps["design"], "design", differences)
            right = _device_map(mos_maps["fab"], "fab", differences)
            if set(left) != set(right):
                differences.append({"kind": "mos_geometry_set",
                                    "design": sorted(left), "fab": sorted(right)})
            for key in sorted(set(left) & set(right)):
                a, b = left[key], right[key]
                for field in ("axis", "drain_side", "source_side", "e",
                              "drain_contact_cuts", "source_contact_cuts"):
                    if a.get(field) != b.get(field):
                        differences.append({"kind": "mos_terminal_geometry",
                                            "device_geometry": key, "field": field,
                                            "design": a.get(field), "fab": b.get(field)})
                for field in ("gate_x_um", "gate_y_um"):
                    if not _close(a.get(field), b.get(field), rtol=0,
                                  atol=result["tolerances"]["physical_coordinate_um"]):
                        differences.append({"kind": "mos_gate_geometry",
                                            "device_geometry": key, "field": field,
                                            "design": a.get(field), "fab": b.get(field)})
                for terminal in ("drain", "gate", "source", "e"):
                    if not isinstance(a.get(terminal), str) or not isinstance(b.get(terminal), str):
                        result["not_comparable_reasons"].append(
                            "%s MOS %s has no explicit %s terminal mapping" %
                            ("design/fab", key, terminal))
                for mode, record in (("design", a), ("fab", b)):
                    if (isinstance(record.get("e"), str) and
                            isinstance(record.get("source"), str) and
                            record["e"].casefold() == record["source"].casefold()):
                        differences.append({"kind": "mos_e_aliases_source",
                                            "mode": mode, "device_geometry": key})
            terminal_partition = {
                "design": _terminal_partition(left, "design", result["not_comparable_reasons"]),
                "fab": _terminal_partition(right, "fab", result["not_comparable_reasons"]),
            }
            result["mos_terminal_partition"] = terminal_partition
            if terminal_partition["design"] != terminal_partition["fab"]:
                differences.append({"kind": "mos_terminal_connectivity_partition",
                                    **terminal_partition})
            if _contact_owners(mos_maps["design"]) != _contact_owners(mos_maps["fab"]):
                differences.append({"kind": "mos_contact_ownership",
                                    "design": _contact_owners(mos_maps["design"]),
                                    "fab": _contact_owners(mos_maps["fab"])})

        tin_maps = {mode: _map_tin(_comment_records(path, "R_TIN_MAP"), mode, differences)
                    for mode, path in pex_paths.items()}
        if set(tin_maps["design"]) != set(tin_maps["fab"]):
            differences.append({"kind": "r_tin_geometry_set",
                                "design": sorted(tin_maps["design"]),
                                "fab": sorted(tin_maps["fab"])})
        for center in sorted(set(tin_maps["design"]) & set(tin_maps["fab"])):
            if tin_maps["design"][center] != tin_maps["fab"][center]:
                differences.append({"kind": "r_tin_endpoint_geometry",
                                    "center_um": center,
                                    "design": tin_maps["design"][center],
                                    "fab": tin_maps["fab"][center]})

        design_rc, fab_rc = _rc_summary(design_net), _rc_summary(fab_net)
        result["rc_summary"] = {"design": design_rc, "fab": fab_rc}
        if design_rc["resistor_count"] != fab_rc["resistor_count"]:
            differences.append({"kind": "pex_resistor_count",
                                "design": design_rc["resistor_count"],
                                "fab": fab_rc["resistor_count"]})
        material_keys = set(design_rc["resistance_by_material_ohm"]) | set(
            fab_rc["resistance_by_material_ohm"])
        for material in sorted(material_keys):
            _compare_numeric_lists(
                design_rc["resistance_by_material_ohm"].get(material, []),
                fab_rc["resistance_by_material_ohm"].get(material, []),
                label="resistor_values_" + material,
                rtol=result["tolerances"]["resistance_relative"],
                atol=result["tolerances"]["resistance_absolute_ohm"],
                differences=differences)
        _compare_numeric_lists(
            design_rc["capacitance_values_f"], fab_rc["capacitance_values_f"],
            label="capacitance_values",
            rtol=result["tolerances"]["capacitance_relative"],
            atol=result["tolerances"]["capacitance_absolute_f"],
            differences=differences)

        if design_assembled is not None and fab_assembled is not None:
            assembled = {
                "design": _reliability_network(design_assembled, top),
                "fab": _reliability_network(fab_assembled, top),
            }

            def tin_bodies(network):
                return sorted(float(item["resistance_ohm"])
                              for item in network["resistor_maps"]
                              if item.get("ownership") == "lvs_device" and
                              str(item.get("material", "")).upper() == "TIN")

            def mos_geometry(network, mode):
                annotations = {
                    item.get("name", "").casefold(): item
                    for item in network["mos_device_maps"]
                    if isinstance(item.get("name"), str)
                }
                geometry = {}
                for device in network["mos"]:
                    annotation = annotations.get(device["name"].casefold())
                    layout_device = annotation.get("layout_device") if annotation else None
                    if not isinstance(layout_device, str) or not layout_device:
                        result["not_comparable_reasons"].append(
                            "%s assembled MOS %s lacks a physical layout-device geometry map" %
                            (mode, device["name"]))
                        continue
                    if (isinstance(device.get("e"), str) and isinstance(device.get("s"), str) and
                            device["e"].casefold() == device["s"].casefold()):
                        differences.append({"kind": "assembled_mos_e_aliases_source",
                                            "mode": mode, "instance": device["name"],
                                            "layout_device": layout_device})
                    parameters = device.get("parameters", {})
                    geometry[layout_device.casefold()] = {
                        "layout_device": layout_device,
                        "parameters": dict(sorted(parameters.items())),
                    }
                return geometry

            _compare_numeric_lists(
                tin_bodies(assembled["design"]), tin_bodies(assembled["fab"]),
                label="lvs_owned_r_tin_body_resistance",
                rtol=result["tolerances"]["resistance_relative"],
                atol=result["tolerances"]["resistance_absolute_ohm"],
                differences=differences)
            geometry_by_mode = {
                mode: mos_geometry(network, mode) for mode, network in assembled.items()
            }
            if set(geometry_by_mode["design"]) != set(geometry_by_mode["fab"]):
                differences.append({"kind": "assembled_mos_geometry_set",
                                    "design": sorted(geometry_by_mode["design"]),
                                    "fab": sorted(geometry_by_mode["fab"])})
            for key in sorted(set(geometry_by_mode["design"]) & set(geometry_by_mode["fab"])):
                left, right = geometry_by_mode["design"][key], geometry_by_mode["fab"][key]
                if left["parameters"] != right["parameters"]:
                    differences.append({"kind": "assembled_mos_geometry_parameters",
                                        "layout_device": left["layout_device"],
                                        "design": left["parameters"], "fab": right["parameters"]})
            result["assembled_counts"] = {
                mode: {key: len(network[field]) for key, field in (
                    ("mos", "mos"), ("resistors", "resistors"), ("capacitors", "capacitors"))}
                for mode, network in assembled.items()
            }
            for key in ("mos", "resistors", "capacitors"):
                left = result["assembled_counts"]["design"][key]
                right = result["assembled_counts"]["fab"][key]
                if left != right:
                    differences.append({"kind": "assembled_" + key + "_count",
                                        "design": left, "fab": right})
        if result["not_comparable_reasons"]:
            result["status"] = "NOT_COMPARABLE"
        elif differences:
            result["status"] = "DIFFERENT"
        else:
            result["status"] = "COMPARABLE_WITHIN_TOLERANCE"
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        result["not_comparable_reasons"].append(str(error))
    return result



def verification_deck_hashes():
    """Return the files whose changes invalidate physical/Xyce evidence."""
    tools = ROOT / "libs.tech/ngspice/tools"
    return {
        "drc": sha256(DRC),
        "lvs": sha256(LVS),
        "pex": sha256(PEX),
        "fab_prep": sha256(FAB_PREP),
        "process_profile": sha256(PROCESS_PROFILE_RB),
        "process_profiles": sha256(PROCESS_PROFILES),
        "assembler": sha256(ASSEMBLER),
        "verify": sha256(Path(__file__)),
        "geometry": sha256(GEOMETRY),
        "reliability_driver": sha256(tools / "postlayout_reliability.py"),
        "reliability_policy": sha256(tools.parent / "reliability_policy.json"),
        "postlayout_erc": sha256(tools / "check_postlayout_erc.py"),
        "power_integrity": sha256(tools / "analyze_power_integrity.py"),
        "waveform_integration": sha256(tools / "waveint.py"),
        "voltage_thermal": sha256(tools / "analyze_voltage_thermal.py"),
        "reliability_netlist": sha256(tools / "reliability_netlist.py"),
        "io_dfm": sha256(ROOT / "libs.tech/stdcell/io_dfm.py"),
    }



def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = PROFILE
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def run_command(command, log_path, *, cwd=None, timeout=300):
    try:
        result = subprocess.run([str(part) for part in command], capture_output=True,
                                text=True, cwd=cwd, env=environment(), timeout=timeout)
    except subprocess.TimeoutExpired as error:
        # subprocess exposes partial output as bytes even with text=True.
        parts = [part.decode("utf-8", errors="replace") if isinstance(part, bytes)
                 else (part or "") for part in (error.stdout, error.stderr)]
        Path(log_path).write_text("".join(parts), encoding="utf-8")
        raise
    text = result.stdout + result.stderr
    Path(log_path).write_text(text, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("command failed (%d): %s; log %s" %
                           (result.returncode, command[0], log_path))
    return text


def _preflight(gds_path, top_name):
    layout = pya.Layout()
    layout.read(str(gds_path))
    top = layout.cell(top_name)
    if top is None:
        raise ValueError("GDS has no top cell %s" % top_name)
    for index in layout.layer_indices():
        info = layout.get_info(index)
        if not top.begin_shapes_rec(index).at_end() and (info.layer, info.datatype) not in ALLOWED_LAYERS:
            raise ValueError("generated 1M layout uses unsupported layer %d/%d" %
                             (info.layer, info.datatype))
    def region(layer):
        index = layout.find_layer(pya.LayerInfo(layer, 0))
        return pya.Region() if index is None else pya.Region(top.begin_shapes_rec(index))
    forbidden = region(7) & region(5) & region(3)
    if not forbidden.is_empty():
        raise ValueError(
            "CNT overlaps TiN and SOI: direct TiN/diffusion contact is unqualified")
    protection = soi_edge_protection(layout, top, layer_mode="design")
    return {"dbu_um": layout.dbu, "bbox_um": [value * layout.dbu for value in
            (top.bbox().left, top.bbox().bottom, top.bbox().right, top.bbox().top)],
            "soi_edge_protection": protection}



def _erc_config(gds_path, top_name, lvs_path, pex_path, assembled_path):
    """Build the post-layout ERC config from the cell's own extraction output.

    Every MOS terminal net is anchored, including unlabelled internal nets
    (identified by their extracted node id).  Without that, a series stack's
    internal diffusion would be reported as an unassigned conductor island.
    """
    ports = _comment_records(pex_path, "PORT_MAP")
    mos_maps = _comment_records(pex_path, "MOS_MAP")
    maps = _comment_records(assembled_path, "ASSEMBLY_NODE_MAP")
    pex_to_asm = {str(item.get("pex")): str(item.get("assembled")) for item in maps}
    lvs_text = Path(lvs_path).read_text(encoding="utf-8")
    node_to_name = {node: name for node, name in
                    re.findall(r"^\* net (\S+) (\S+)", lvs_text, re.MULTILINE)}
    instances = {}
    for line in lvs_text.splitlines():
        match = re.match(r"^(M\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+(NMOS|PMOS)\b",
                         line, re.IGNORECASE)
        if match:
            instance = match.group(1)
            key = instance[1:] if instance[:1].casefold() == "m" else instance
            instances[key.casefold()] = {
                "d": match.group(2), "g": match.group(3), "s": match.group(4)}
    lvs_devices = {}
    for match in re.finditer(
            r"^\* device instance (\S+) \S+ \S+ ([+-]?[\d.]+),([+-]?[\d.]+) (NMOS|PMOS)",
            lvs_text, re.MULTILINE | re.IGNORECASE):
        instance, x_um, y_um, polarity = match.groups()
        nets = instances.get(instance.casefold())
        if nets is None:
            raise ValueError("LVS device %s has no MOS instance line" % instance)
        key = "%s@%.6f,%.6f" % (polarity.upper(), float(x_um), float(y_um))
        lvs_devices[key.casefold()] = nets

    rows_by_node = {}

    def add(node, role, pex_node, assembled_node, anchor):
        lvs_node = str(node)
        entry = rows_by_node.setdefault(lvs_node, {
            "net": node_to_name.get(lvs_node, lvs_node),
            "lvs_node": lvs_node,
            "pex_nodes": set(), "assembled_nodes": set(), "anchors": [],
        })
        if pex_node:
            entry["pex_nodes"].add(str(pex_node))
            if str(pex_node) in pex_to_asm:
                entry["assembled_nodes"].add(pex_to_asm[str(pex_node)])
        if anchor is not None:
            entry["anchors"].append(anchor)

    for mapping in mos_maps:
        nets = lvs_devices.get(str(mapping.get("device", "")).casefold())
        if nets is None:
            raise ValueError("PEX MOS_MAP %s has no matching LVS device" %
                             mapping.get("device"))
        for role in ("drain", "gate", "source"):
            pex_node = mapping.get(role)
            if role == "gate":
                anchor = {"kind": "pin", "name": nets["g"], "layer": "TIN",
                          "point_um": [mapping.get("gate_x_um"),
                                       mapping.get("gate_y_um")]}
            else:
                prefix = "source" if role == "source" else "drain"
                anchor = {"kind": "pin", "name": nets["d" if role == "drain" else "s"],
                          "layer": "ML1",
                          "point_um": [mapping.get("%s_x_um" % prefix),
                                       mapping.get("%s_y_um" % prefix)]}
            add(nets["d" if role == "drain" else "g" if role == "gate" else "s"],
                role, pex_node, pex_to_asm.get(str(pex_node)), anchor)

    # Pin-only nets (if any) still need an anchor for the island screen.
    for logical in sorted({str(item.get("logical")) for item in ports}):
        node = next((key for key, name in node_to_name.items()
                     if name.casefold() == logical.casefold()), None)
        if node is None:
            continue
        for item in ports:
            if item.get("logical") != logical:
                continue
            add(node, "pin", item.get("physical"), pex_to_asm.get(str(item.get("physical"))),
                {"kind": "pin", "name": logical, "layer": item["layer"],
                 "point_um": [item["x_um"], item["y_um"]]})

    rows = []
    for entry in rows_by_node.values():
        entry["pex_nodes"] = sorted(entry["pex_nodes"])
        entry["assembled_nodes"] = sorted(entry["assembled_nodes"])
        # De-duplicate identical anchors.
        unique, seen = [], set()
        for anchor in entry["anchors"]:
            key = (anchor["layer"], tuple(anchor["point_um"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(anchor)
        entry["anchors"] = unique
        if entry["pex_nodes"] and entry["assembled_nodes"] and entry["anchors"]:
            rows.append(entry)
    return {
        "gds": str(gds_path), "gds_top": top_name,
        "lvs": str(lvs_path), "pex": str(pex_path),
        "assembled": str(assembled_path), "top": top_name,
        "subcircuits": {"lvs": top_name, "pex": top_name + "_pex",
                        "assembled": top_name},
        "layer_mode": "design",
        "contact_policy": "strict",
        "expected_logical_associations": rows,
    }


def _run_postlayout_erc(config, output_dir):
    tools_dir = ROOT / "libs.tech/ngspice/tools"
    spec = importlib.util.spec_from_file_location(
        "icps_check_postlayout_erc", tools_dir / "check_postlayout_erc.py")
    module = importlib.util.module_from_spec(spec)
    added = False
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
        added = True
    try:
        spec.loader.exec_module(module)
        report = module.run_erc(config)
    finally:
        if added and sys.path and sys.path[0] == str(tools_dir):
            sys.path.pop(0)
    path = Path(output_dir) / "postlayout_erc.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    required = ("gds_geometry", "illegal_contacts", "map_correspondence",
                "expected_logical_associations", "logical_shorts_and_opens",
                "body_state_and_local_ties", "unassigned_conductor_islands")
    problems = [name for name in required
                if report["checks"].get(name, {}).get("status") != "PASS"]
    failed = [name for name, check in report["checks"].items()
              if check.get("status") == "FAIL"]
    if problems or failed:
        raise RuntimeError("post-layout ERC is not clean: problems=%s failures=%s "
                           "(overall %s); see %s"
                           % (problems or [], failed or [],
                              report.get("status"), path))
    unconfigured = [name for name, check in report["checks"].items()
                    if check.get("status") == "BLOCKED"]
    return {"passed": True, "status": "CLEAN_REQUIRED_CHECKS",
            "overall_status": report.get("status"),
            "required_checks": list(required),
            "unconfigured_screens": unconfigured,
            "report": str(path)}


def _record_reliability(evidence, output_dir, reliability_config, physical_artifacts):
    report_path = output_dir / "reliability.json"
    tools_dir = ROOT / "libs.tech/ngspice/tools"
    sys.path.insert(0, str(tools_dir))
    try:
        loader = importlib.util.spec_from_file_location(
            "icps_postlayout_reliability", tools_dir / "postlayout_reliability.py")
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        report = module.run_reliability(
            reliability_config, physical_artifacts=physical_artifacts)
    except Exception as error:
        topic_names = ("rc_coverage", "body_backgate", "voltage_limits", "pvt_timing",
                       "electromigration", "electrothermal", "antenna", "pad_esd_eos")
        status = "BLOCKED" if isinstance(error, ImportError) else "FAIL"
        report = {
            "status": status, "screening_status": status,
            "error": "integrated reliability execution failed (%s): %s" %
                     (type(error).__name__, error),
            "topics": {
                name: {"status": "BLOCKED", "screen_status": "BLOCKED",
                       "reason": "integrated reliability evidence is unavailable"}
                for name in topic_names
            },
            "manufacturing_acceptance": {
                "status": "BLOCKED", "accepted": False,
                "reason": "a complete policy-backed report is unavailable; manufacturing acceptance remains blocked",
            },
        }
    finally:
        if sys.path and sys.path[0] == str(tools_dir):
            sys.path.pop(0)
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    evidence["reliability_screening_status"] = report.get("screening_status", "BLOCKED")
    evidence["reliability_screening"] = {
        "status": evidence["reliability_screening_status"],
        "report": str(report_path),
    }
    evidence["manufacturing_acceptance"] = report.get(
        "manufacturing_acceptance",
        {"status": "BLOCKED", "accepted": False,
         "reason": "qualification evidence is unavailable"})

def verify_physical(gds_path, reference_path, top_name, output_dir, *,
                    reliability_config=None):
    """Return strict physical evidence, with reliability status kept separate."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    gds_path, reference_path = Path(gds_path).resolve(), Path(reference_path).resolve()
    fab_gds = output_dir / "protected_fab.gds"
    extracted = output_dir / "extracted.cir"
    fab_extracted = output_dir / "fab.extracted.cir"
    pex_path = output_dir / "parasitics.spice"
    fab_pex_path = output_dir / "fab.parasitics.spice"
    assembled = output_dir / "postlayout.spice"
    fab_assembled = output_dir / "fab.postlayout.spice"
    klayout = shutil.which("klayout")

    def artifact_inputs():
        paths = {"gds": gds_path, "lvs": extracted, "pex": pex_path,
                 "assembled": assembled, "fab_gds": fab_gds}
        records = {
            role: ({"path": str(path), "sha256": sha256(path)} if path.is_file()
                   else {"path": str(path)})
            for role, path in paths.items()
        }
        records["top"] = top_name
        return records

    evidence = {
        "top": top_name, "process_profile": PROFILE, "strict_drc": False,
        "strict_lvs": False, "pex": False, "assembly": False,
        "passed": False, "scope": "physical repository checks only; reliability and manufacturing acceptance are independent",
        "gds_sha256": sha256(gds_path) if gds_path.is_file() else None,
        "reference_sha256": sha256(reference_path) if reference_path.is_file() else None,
        "deck_sha256": verification_deck_hashes(),
        "soi_edge_protection": {
            "passed": False, "design_geometry_verified": False,
            "design_drc_passed": False, "design_lvs_passed": False,
            "fabrication_verified": False,
            "scope": "all_true_tin_soi_crossings",
            "experimental_tin_soi_step_coverage_min_um": 2.0},
        "fabrication": {
            "passed": False, "fab_gds": None, "fab_gds_sha256": None,
            "fab_drc_run": False, "fab_drc_markers": None,
            "strict_drc": False, "strict_lvs": False,
            "pex": False, "assembly": False,
            "soi_edge_protection": None},
    }
    try:
        if klayout is None:
            raise RuntimeError("KLayout executable required; use the root Nix devShell")
        geometry = _preflight(gds_path, top_name)
        evidence["geometry"] = geometry
        protection = geometry["soi_edge_protection"]
        evidence["soi_edge_protection"]["design"] = protection
        evidence["soi_edge_protection"]["design_geometry_verified"] = True
        drc_report = output_dir / "strict.drc.lyrdb"
        run_command([klayout, "-b", "-r", DRC, "-rd", "input=" + str(gds_path),
                     "-rd", "top=" + top_name, "-rd", "report=" + str(drc_report),
                     "-rd", "process_profile=" + PROFILE, "-rd", "strict=true",
                     "-rd", "edge_redundancy=true"],
                    output_dir / "drc.log", cwd=output_dir)
        report_root = ElementTree.parse(drc_report).getroot()
        items = report_root.find("items")
        if items is None:
            raise RuntimeError("DRC report has no items collection")
        markers = [item.findtext("category", "") for item in items.findall("item")]
        evidence["drc_markers"] = markers
        if markers:
            raise RuntimeError("strict DRC failed: %d markers" % len(markers))
        evidence["strict_drc"] = True
        evidence["soi_edge_protection"]["design_drc_passed"] = True
        run_command([klayout, "-b", "-r", FAB_PREP,
                     "-rd", "input=" + str(gds_path),
                     "-rd", "output=" + str(fab_gds),
                     "-rd", "top=" + top_name,
                     "-rd", "process_profile=" + PROFILE],
                    output_dir / "fab_prep.log", cwd=output_dir)
        fab_layout = pya.Layout()
        fab_layout.read(str(fab_gds))
        fab_top = fab_layout.cell(top_name)
        if fab_top is None:
            raise ValueError("fab-prepared GDS has no top cell %s" % top_name)
        fab_protection = soi_edge_protection(
            fab_layout, fab_top, layer_mode="fab")
        fabrication = evidence["fabrication"]
        fabrication.update(
            fab_gds=str(fab_gds), fab_gds_sha256=sha256(fab_gds),
            fab_prep_sha256=sha256(FAB_PREP),
            soi_edge_protection=fab_protection,
            layer_mode="fab", edge_redundancy=True)
        fab_drc_report = output_dir / "strict.fab.drc.lyrdb"
        run_command([klayout, "-b", "-r", DRC, "-rd", "input=" + str(fab_gds),
                     "-rd", "top=" + top_name, "-rd", "report=" + str(fab_drc_report),
                     "-rd", "process_profile=" + PROFILE, "-rd", "layer_mode=fab",
                     "-rd", "strict=true", "-rd", "edge_redundancy=true"],
                    output_dir / "fab_drc.log", cwd=output_dir)
        fabrication["fab_drc_run"] = True
        fab_report_root = ElementTree.parse(fab_drc_report).getroot()
        fab_items = fab_report_root.find("items")
        if fab_items is None:
            raise RuntimeError("fab DRC report has no items collection")
        fab_markers = [item.findtext("category", "")
                       for item in fab_items.findall("item")]
        fabrication["fab_drc_markers"] = fab_markers
        if fab_markers:
            raise RuntimeError("strict fab DRC failed: %d markers" % len(fab_markers))
        fabrication["strict_drc"] = True
        fab_lvs_log = run_command(
            [klayout, "-b", "-r", LVS, "-rd", "input=" + str(fab_gds),
             "-rd", "top=" + top_name, "-rd", "schematic=" + str(reference_path),
             "-rd", "report=" + str(output_dir / "strict.fab.lvs.lyrdb"),
             "-rd", "output=" + str(fab_extracted), "-rd", "filters=off",
             "-rd", "process_profile=" + PROFILE, "-rd", "layer_mode=fab"],
            output_dir / "fab_lvs.log", cwd=output_dir)
        if "ICPS2023_5 LVS MATCH (mismatches=0)" not in fab_lvs_log:
            raise RuntimeError("strict fab LVS mismatch; see %s" %
                               (output_dir / "fab_lvs.log"))
        fabrication["strict_lvs"] = True
        fabrication["lvs_match"] = True
        fabrication["extracted_spice"] = str(fab_extracted)
        fabrication["lvs_sha256"] = sha256(fab_extracted)

        lvs_log = run_command([klayout, "-b", "-r", LVS, "-rd", "input=" + str(gds_path),
                               "-rd", "top=" + top_name, "-rd", "schematic=" + str(reference_path),
                               "-rd", "report=" + str(output_dir / "strict.lvs.lyrdb"),
                               "-rd", "output=" + str(extracted), "-rd", "filters=off",
                               "-rd", "process_profile=" + PROFILE],
                              output_dir / "lvs.log", cwd=output_dir)
        if "ICPS2023_5 LVS MATCH (mismatches=0)" not in lvs_log:
            raise RuntimeError("strict LVS mismatch; see %s" % (output_dir / "lvs.log"))
        evidence["soi_edge_protection"]["design_lvs_passed"] = True
        evidence["soi_edge_protection"].update(
            fabrication_verified=True, fab=fab_protection, passed=True)
        evidence["strict_lvs"] = True
        evidence["extracted_spice"] = str(extracted)
        evidence["lvs_sha256"] = sha256(extracted)

        model_reference, model_record = _model_reference(output_dir, model_binding.load())
        evidence["model_binding"] = model_record
        loader = importlib.util.spec_from_file_location("icps_stdcell_assembly", ASSEMBLER)
        assembly_module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(assembly_module)
        expected_access_layers = {"NAREA", "PAREA"}

        def access_evidence(path, mode):
            try:
                records = _comment_records(path, "MOS_ACCESS_RESULT")
                errors = []
            except (OSError, ValueError) as error:
                records, errors = [], [str(error)]
            layers = [str(row.get("layer", "")).upper() for row in records]
            if len(records) != 2 or set(layers) != expected_access_layers:
                errors.append("%s PEX must emit one MOS access summary for each of NAREA and PAREA" %
                              mode)
            required_fields = {"layer", "gates", "bounded", "unbounded", "ambiguous",
                               "orphaned", "failures", "passed"}
            for row in records:
                missing = required_fields - set(row)
                if missing:
                    errors.append("%s MOS access record missing fields %s" %
                                  (mode, sorted(missing)))
                for field in ("gates", "bounded", "unbounded", "ambiguous", "orphaned"):
                    value = row.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        errors.append("%s %s MOS access %s must be a non-negative integer" %
                                      (mode, row.get("layer"), field))
                if row.get("failures") != []:
                    errors.append("%s %s MOS access reports failures %r" %
                                  (mode, row.get("layer"), row.get("failures")))
                if row.get("passed") is not True:
                    errors.append("%s %s MOS access summary did not pass" %
                                  (mode, row.get("layer")))
            return records, {"passed": not errors, "required_layers": sorted(expected_access_layers),
                             "errors": errors}

        run_command(
            [sys.executable, PEX, "--input", gds_path, "--top", top_name,
             "--output", pex_path, "--process-profile", PROFILE],
            output_dir / "pex.log", cwd=output_dir)
        evidence["mos_access"], evidence["mos_access_validation"] = access_evidence(
            pex_path, "design")
        design_access_valid = evidence["mos_access_validation"]["passed"]
        evidence["pex"] = True
        evidence["pex_evidence"] = _pex_evidence_module.collect_pex_evidence(
            pex_path, gds_path, PROFILE, PEX, layer_mode="design", upper_metal_layers=[])
        evidence["assembly_stats"] = assembly_module.assemble(
            extracted, pex_path, reference_path, top_name, assembled,
            model_library=model_reference)
        evidence["assembly"] = True
        evidence["postlayout_spice"] = str(assembled)
        evidence["pex_sha256"] = sha256(pex_path)
        evidence["pex_spice"] = str(pex_path)
        evidence["assembled_sha256"] = sha256(assembled)
        erc_config = _erc_config(gds_path, top_name, extracted, pex_path, assembled)
        evidence["postlayout_erc"] = _run_postlayout_erc(erc_config, output_dir)

        run_command(
            [sys.executable, PEX, "--input", fab_gds, "--top", top_name,
             "--output", fab_pex_path, "--process-profile", PROFILE,
             "--layer-mode", "fab"],
            output_dir / "fab_pex.log", cwd=output_dir)
        fabrication["mos_access"], fabrication["mos_access_validation"] = access_evidence(
            fab_pex_path, "fab")
        fab_access_valid = fabrication["mos_access_validation"]["passed"]
        fabrication["pex"] = True
        fabrication["pex_sha256"] = sha256(fab_pex_path)
        fabrication["fab_pex_spice"] = str(fab_pex_path)
        fabrication["pex_evidence"] = _pex_evidence_module.collect_pex_evidence(
            fab_pex_path, fab_gds, PROFILE, PEX, layer_mode="fab", upper_metal_layers=[])
        fabrication["assembly_stats"] = assembly_module.assemble(
            fab_extracted, fab_pex_path, reference_path, top_name, fab_assembled,
            model_library=model_reference)
        fabrication["assembly"] = True
        fabrication["assembled_spice"] = str(fab_assembled)
        fabrication["assembled_sha256"] = sha256(fab_assembled)
        evidence["pex_mode_comparison"] = compare_pex_modes(
            pex_path, fab_pex_path, top_name, assembled, fab_assembled)
        fabrication["pex_mode_comparison"] = evidence["pex_mode_comparison"]
        evidence["fabrication"]["fab_gds_sha256"] = sha256(fab_gds)
        if not design_access_valid or not fab_access_valid:
            raise RuntimeError("design/fab PEX access-summary validation failed: %s" %
                               json.dumps({
                                   "design": evidence["mos_access_validation"],
                                   "fab": fabrication["mos_access_validation"],
                               }, sort_keys=True))
        if evidence["pex_mode_comparison"]["status"] != "COMPARABLE_WITHIN_TOLERANCE":
            raise RuntimeError("design/fab PEX correspondence was not established: %s" %
                               json.dumps(evidence["pex_mode_comparison"], sort_keys=True))
        fabrication["passed"] = True
        evidence["passed"] = True
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, ElementTree.ParseError) as error:
        evidence["passed"] = False
        evidence["error"] = str(error)
        _record_reliability(evidence, output_dir, reliability_config, artifact_inputs())
        (output_dir / "physical.json").write_text(json.dumps(evidence, indent=2) + "\n")
        raise VerificationError(str(error), evidence) from error
    _record_reliability(evidence, output_dir, reliability_config, artifact_inputs())
    (output_dir / "physical.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence
