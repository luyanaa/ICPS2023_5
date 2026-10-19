#!/usr/bin/env python3
"""Evidence-based post-layout topology/body ERC for ICPS2023_5 GDS and SPICE.

The JSON configuration is intentionally explicit: it supplies every source,
load, logical-net association, and path.  This is a research-PDK screen, not
foundry signoff.  MOS channels are never treated as DC conductor edges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from heapq import heappop, heappush
from pathlib import Path
from typing import Any

try:
    from .reliability_netlist import ReliabilityNetlistError, read_network
except ImportError:  # direct invocation from libs.tech/ngspice/tools
    from reliability_netlist import ReliabilityNetlistError, read_network


DESIGN_LAYERS = {
    "DIFF": (3, 0), "TIN": (5, 0), "TINBLK": (5, 1), "CNT": (7, 0),
    "ML1": (8, 0), "VIA1": (9, 0), "ML2": (10, 0), "VIA2": (11, 0),
    "ML3": (12, 0), "TEXT": (13, 0), "RES": (15, 0), "CAP": (16, 0),
    "DIO": (17, 0), "PAREA": (18, 0), "NAREA": (19, 0), "MASK": (20, 0),
    "NCH": (21, 0), "PAD": (50, 0), "EXCLUDE": (63, 63),
}
FAB_LAYERS = {
    "DIFF": (4, 0), "TIN": (5, 0), "TINBLK": (5, 1), "CNT": (6, 0),
    "ML1": (7, 0), "VIA1": (8, 0), "ML2": (9, 0), "VIA2": (11, 0),
    "ML3": (12, 0), "TEXT": (13, 1), "RES": (15, 0), "CAP": (16, 0),
    "DIO": (17, 0), "PAREA": (2, 0), "NAREA": (3, 0), "MASK": (20, 0),
    "NCH": (13, 0), "PAD": (10, 0), "EXCLUDE": (63, 63),
}
_LAYER_TABLES = {"design": DESIGN_LAYERS, "fab": FAB_LAYERS}
_ANCHOR_LAYERS = {"DIFF", "NPLUS", "PPLUS", "TIN", "ML1"}
_UNSUPPORTED_METAL_LAYERS = ("VIA1", "ML2", "VIA2", "ML3")
_CONDUCTOR_LAYERS = ("NPLUS", "PPLUS", "TIN", "ML1")
_DEVICE_COMMENT = re.compile(
    r"^device instance\s+(\S+)\s+(\S+)\s+\S+\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+(NMOS|PMOS)\s*$",
    re.IGNORECASE,
)
_NET_COMMENT = re.compile(r"^net\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
_CHECK_STATES = {"PASS", "FAIL", "BLOCKED", "UNASSESSED"}


class PostlayoutERCError(ValueError):
    """Malformed ERC configuration or unavailable KLayout runtime."""


def _check(status: str, summary: str, **fields: Any) -> dict[str, Any]:
    if status not in _CHECK_STATES:
        raise AssertionError("invalid ERC check state: %s" % status)
    return {"status": status, "summary": summary, **fields}


def _casefold(value: Any) -> str:
    return value.casefold() if isinstance(value, str) else ""


def _layer_name(value: Any) -> str:
    if isinstance(value, str):
        name = value.upper()
        if name in _ANCHOR_LAYERS:
            return name
    raise PostlayoutERCError("anchor layer must be one of %s, got %r" % (sorted(_ANCHOR_LAYERS), value))


def _resolve_layer_mapping(layer_config: Any, layer_mode: Any) -> dict[str, tuple[int, int]]:
    if not isinstance(layer_mode, str) or layer_mode not in _LAYER_TABLES:
        raise PostlayoutERCError("layer_mode must be exactly 'design' or 'fab'")
    resolved = dict(_LAYER_TABLES[layer_mode])
    if layer_config is None:
        return resolved
    if not isinstance(layer_config, dict):
        raise PostlayoutERCError("layers must be an object mapping layer names to [layer, datatype]")
    seen_names: set[str] = set()
    other_mode = "fab" if layer_mode == "design" else "design"
    for name, pair in layer_config.items():
        if not isinstance(name, str):
            raise PostlayoutERCError("layer override names must be strings")
        canonical = name.upper()
        if canonical not in resolved:
            raise PostlayoutERCError("unsupported layer override %r" % name)
        if canonical in seen_names:
            raise PostlayoutERCError("duplicate layer override for %s" % canonical)
        seen_names.add(canonical)
        if not isinstance(pair, list) or len(pair) != 2 or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in pair):
            raise PostlayoutERCError("layer %s must be [nonnegative_integer_layer, nonnegative_integer_datatype]" % name)
        spec = (pair[0], pair[1])
        if spec == _LAYER_TABLES[other_mode][canonical] and spec != resolved[canonical]:
            raise PostlayoutERCError(
                "layer %s override %s conflicts with explicit layer_mode=%s" %
                (canonical, list(spec), layer_mode)
            )
        other_owners = {
            owner for table in _LAYER_TABLES.values()
            for owner, known_spec in table.items()
            if known_spec == spec and owner != canonical
        }
        if other_owners:
            raise PostlayoutERCError(
                "layer %s override %s aliases different physical layer(s) %s" %
                (canonical, list(spec), sorted(other_owners))
            )
        resolved[canonical] = spec
    if len(set(resolved.values())) != len(resolved):
        raise PostlayoutERCError("resolved layer mapping assigns one GDS layer to multiple physical roles")
    return resolved


def _klayout_db():
    try:
        import klayout.db as db
    except ImportError as error:  # pragma: no cover - supplied by pinned KLayout runtime
        raise PostlayoutERCError("KLayout Python module is required to inspect the actual GDS") from error
    return db


def _region_for_shape(shape: Any, transform: Any, db: Any):
    region = db.Region()
    if shape.is_box():
        region.insert(shape.box)
    elif shape.is_polygon():
        region.insert(shape.polygon)
    elif shape.is_path():
        region.insert(shape.path.polygon())
    elif shape.is_simple_polygon():
        region.insert(shape.simple_polygon.to_polygon())
    else:
        return None
    region.transform(transform)
    return region


def _shape_region_and_labels(top: Any, layer_index: int, db: Any,
                             collect_text: bool = False) -> tuple[Any, list[dict[str, Any]], list[Any]]:
    region = db.Region()
    labels: list[dict[str, Any]] = []
    contacts: list[Any] = []
    iterator = top.begin_shapes_rec(layer_index)
    while not iterator.at_end():
        shape = iterator.shape()
        transform = iterator.trans()
        if shape.is_text():
            if collect_text:
                text_trans = transform * shape.text.trans
                point = text_trans.disp
                labels.append({"name": shape.text.string, "point_dbu": [int(point.x), int(point.y)]})
        else:
            shape_region = _region_for_shape(shape, transform, db)
            if shape_region is not None and not shape_region.is_empty():
                region += shape_region
                if contacts is not None:
                    contacts.append(shape_region)
        iterator.next()
    return region.merged(), labels, contacts


def _region_components(region: Any, db: Any) -> list[Any]:
    return [db.Region(polygon) for polygon in region.merged().each()]


def _intersecting_ids(region: Any, components: list[Any]) -> list[int]:
    return [index for index, component in enumerate(components)
            if not (region & component).is_empty()]


def _contained_ids(region: Any, components: list[Any]) -> list[int]:
    return [index for index, component in enumerate(components)
            if (region - component).is_empty()]


def _point_ids(point: tuple[int, int], components: list[Any], db: Any) -> list[int]:
    box = db.Box(point[0] - 1, point[1] - 1, point[0] + 1, point[1] + 1)
    region = db.Region(box)
    return _intersecting_ids(region, components)


def _um_to_dbu(value: Any, dbu_um: float, field: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise PostlayoutERCError("%s must be numeric" % field) from error
    if not math.isfinite(number):
        raise PostlayoutERCError("%s must be finite" % field)
    return int(round(number / dbu_um))


def _bbox_um(region: Any, dbu_um: float) -> list[float]:
    box = region.bbox()
    return [box.left * dbu_um, box.bottom * dbu_um,
            box.right * dbu_um, box.top * dbu_um]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _layout_inputs(path: Path, requested_top: str | None, layer_config: Any, *,
                   layer_mode: str) -> dict[str, Any]:
    layer_ids = _resolve_layer_mapping(layer_config, layer_mode)
    db = _klayout_db()
    layout = db.Layout()
    try:
        layout.read(str(path))
    except Exception as error:
        raise PostlayoutERCError("cannot read GDS %s: %s" % (path, error)) from error
    top_cells = list(layout.top_cells())
    if requested_top is None:
        if len(top_cells) != 1:
            raise PostlayoutERCError("gds_top is required when GDS has %d top cells" % len(top_cells))
        top = top_cells[0]
    else:
        top = layout.cell(requested_top)
        if top is None or not any(cell.cell_index() == top.cell_index() for cell in top_cells):
            raise PostlayoutERCError("GDS has no top cell named %s" % requested_top)
    dbu_um = float(layout.dbu)
    if not math.isfinite(dbu_um) or dbu_um <= 0:
        raise PostlayoutERCError("GDS database unit is not positive and finite")

    index_by_pair = {}
    for index in layout.layer_indices():
        info = layout.get_info(index)
        index_by_pair[(int(info.layer), int(info.datatype))] = index
    raw_regions: dict[str, Any] = {}
    raw_contacts: list[Any] = []
    raw_labels: list[dict[str, Any]] = []
    layer_presence: dict[str, bool] = {}
    for name, pair in layer_ids.items():
        layer_index = index_by_pair.get(pair)
        layer_presence[name] = layer_index is not None
        if layer_index is None:
            raw_regions[name] = db.Region()
            continue
        region, labels, shapes = _shape_region_and_labels(
            top, layer_index, db, collect_text=(name == "TEXT")
        )
        raw_regions[name] = region
        if name == "CNT":
            raw_contacts.extend(shapes)
        if name == "TEXT":
            raw_labels.extend(labels)

    exclusions = raw_regions["MASK"] + raw_regions["EXCLUDE"]
    regions = {
        name: (region if name in ("MASK", "EXCLUDE") else (region - exclusions).merged())
        for name, region in raw_regions.items()
    }
    contacts: list[Any] = []
    for shape in raw_contacts:
        clipped = shape - exclusions
        contacts.extend(db.Region(poly) for poly in clipped.each())
    text_labels = []
    for label in raw_labels:
        point = tuple(label["point_dbu"])
        if not _point_ids(point, _region_components(exclusions, db), db):
            text_labels.append(label)

    diff = regions["DIFF"]
    design_tin = regions["TIN"]
    if layer_mode == "design":
        um2 = _um_to_dbu(2.0, dbu_um, "fab-prep mask bias")
        um4 = _um_to_dbu(4.0, dbu_um, "fab-prep CAP guard")
        prep_gate = design_tin.interacting(diff) - (design_tin & regions["CAP"].sized(um4))
        process_bounds = (diff + prep_gate).extents()
        final_tin = design_tin + (prep_gate.sized(um2) & process_bounds) + regions["TINBLK"]
        dio2 = regions["DIO"].sized(um2) - (regions["PAREA"] + regions["NAREA"])
        n_dope = (regions["NAREA"] & diff).sized(um2) - (
            design_tin - regions["CAP"] + dio2
        )
        p_dope = (regions["PAREA"] & diff).sized(um2) - (
            design_tin - regions["CAP"] + dio2
        )
    else:
        final_tin = design_tin
        n_dope = regions["NAREA"]
        p_dope = regions["PAREA"]
    regions["TIN"] = final_tin.merged()

    # SOI is physical geometry, not a conductor. Only explicitly doped
    # same-polarity regions form ohmic domains; the undoped film, MOS channel,
    # and ambiguous overlap between N+ and P+ remain nonconductive.
    res_body = regions["RES"] & diff
    n_candidate = (n_dope & diff) - res_body
    p_candidate = (p_dope & diff) - res_body
    mixed_implant = n_candidate & p_candidate
    regions["MIXED"] = mixed_implant.merged()
    regions["NPLUS"] = (n_candidate - mixed_implant).merged()
    regions["PPLUS"] = (p_candidate - mixed_implant).merged()

    components = {
        name: _region_components(regions[name], db)
        for name in ("DIFF", "NPLUS", "PPLUS", "TIN", "ML1")
    }
    parents: dict[tuple[str, int], tuple[str, int]] = {
        (name, index): (name, index)
        for name in _CONDUCTOR_LAYERS
        for index in range(len(components[name]))
    }

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parent = parents[node]
        while parent != parents[parent]:
            parents[node] = parents[parents[parent]]
            parent = parents[node]
        parents[node] = parent
        return parent

    def union(first: tuple[str, int], second: tuple[str, int]) -> None:
        a, b = find(first), find(second)
        if a != b:
            parents[max(a, b)] = min(a, b)

    um = lambda value: _um_to_dbu(value, dbu_um, "physical contact dimension")
    cnt_size = um(5.0)
    route_enclosure = um(2.5)
    sd_enclosure = um(2.0)
    gate_core = regions["TIN"] & diff
    contact_report: list[dict[str, Any]] = []
    for index, cut in enumerate(contacts):
        box = cut.bbox()
        exact_size = (
            box.width() == cnt_size and box.height() == cnt_size and
            cut.area() == cnt_size * cnt_size
        )
        diff_hits = _intersecting_ids(cut, components["DIFF"])
        tin_hits = _intersecting_ids(cut, components["TIN"])
        ml1_hits = _intersecting_ids(cut, components["ML1"])
        gate_overlap = not (cut & gate_core).is_empty()
        implant_hits = [name for name in ("PAREA", "NAREA")
                        if not (cut & regions[name]).is_empty()]
        ohmic_hits = {
            name: _intersecting_ids(cut, components[name])
            for name in ("NPLUS", "PPLUS")
        }
        ohmic_types = [name for name, ids in ohmic_hits.items() if ids]
        mixed_overlap = not (cut & regions["MIXED"]).is_empty()
        status = "legal"
        kind = "unclassified"
        reasons: list[str] = []
        touched_roots: list[tuple[str, int]] = []
        if diff_hits:
            kind = "diffusion_contact"
            if gate_overlap:
                reasons.append("CNT overlaps final TiN/DIFF MOS gate geometry")
            if not exact_size:
                reasons.append("diffusion CNT is not an exact 5x5 um cut")
            if len(ohmic_types) != 1:
                reasons.append("diffusion CNT must overlap exactly one effective N+ or P+ ohmic domain")
            if mixed_overlap:
                reasons.append("diffusion CNT overlaps ambiguous mixed N+/P+ implant geometry")
            landing = cut.sized(sd_enclosure)
            diff_cover = _contained_ids(landing, components["DIFF"])
            ml1_cover = _contained_ids(landing, components["ML1"])
            if len(diff_cover) != 1:
                reasons.append("one DIFF component must enclose the complete contact landing by 2.0 um")
            if len(ml1_cover) != 1:
                reasons.append("one ML1 component must enclose the complete diffusion-contact landing by 2.0 um")
            domain_cover: list[int] = []
            domain_name = ohmic_types[0] if len(ohmic_types) == 1 else None
            if domain_name is not None:
                domain_cover = _contained_ids(landing, components[domain_name])
                if len(domain_cover) != 1:
                    reasons.append(
                        "one effective %s domain must enclose the complete contact landing by 2.0 um" %
                        domain_name
                    )
            if not (landing & regions["RES"]).is_empty():
                reasons.append("diffusion contact landing overlaps a RES device body")
            if tin_hits:
                reasons.append("diffusion contact also overlaps TiN; conductor ownership is ambiguous")
            if not reasons and domain_name is not None:
                touched_roots = [(domain_name, domain_cover[0]), ("ML1", ml1_cover[0])]
                union(touched_roots[0], touched_roots[1])
        elif tin_hits or ml1_hits:
            kind = "tin_ml1_via"
            if not exact_size:
                reasons.append("TiN/ML1 CNT is not an exact 5x5 um cut")
            if not tin_hits or not ml1_hits:
                reasons.append("TiN/ML1 CNT must physically touch both conductor layers")
            landing = cut.sized(route_enclosure)
            tin_cover = _contained_ids(landing, components["TIN"])
            ml1_cover = _contained_ids(landing, components["ML1"])
            if len(tin_cover) != 1:
                reasons.append("one TiN component must enclose the CNT by 2.5 um")
            if len(ml1_cover) != 1:
                reasons.append("one ML1 component must enclose the CNT by 2.5 um")
            if not reasons:
                touched_roots = [("TIN", tin_cover[0]), ("ML1", ml1_cover[0])]
                union(touched_roots[0], touched_roots[1])
        else:
            reasons.append("CNT touches no supported conductor/contact stack")
        if reasons:
            status = "illegal"
        contact_report.append({
            "index": index,
            "kind": kind,
            "status": status,
            "bbox_um": _bbox_um(cut, dbu_um),
            "intersects_diff": bool(diff_hits),
            "intersects_tin": bool(tin_hits),
            "intersects_ml1": bool(ml1_hits),
            "intersects_mos_gate_core": gate_overlap,
            "implant_layers": implant_hits,
            "effective_ohmic_domains": ohmic_types,
            "mixed_implant_overlap": mixed_overlap,
            "touched_components": [
                {"layer": layer, "index": component} for layer, component in touched_roots
            ],
            "reason": reasons,
        })

    roots = sorted({find(node) for node in parents})
    root_ids = {root: index for index, root in enumerate(roots)}
    electrical_ids = {
        name: [root_ids[find((name, index))] for index in range(len(components[name]))]
        for name in _CONDUCTOR_LAYERS
    }
    component_rows: list[dict[str, Any]] = []
    for root in roots:
        regions_for_root = []
        layers = []
        for layer_name in _CONDUCTOR_LAYERS:
            for component_index, component in enumerate(components[layer_name]):
                if find((layer_name, component_index)) == root:
                    regions_for_root.append(component)
                    layers.append(layer_name)
        whole = db.Region()
        for region in regions_for_root:
            whole += region
        component_rows.append({
            "component_id": root_ids[root],
            "layers": sorted(set(layers)),
            "bbox_um": _bbox_um(whole, dbu_um),
            "area_um2_by_layer": {
                layer_name: sum(
                    components[layer_name][component_index].area() * dbu_um * dbu_um
                    for component_index in range(len(components[layer_name]))
                    if find((layer_name, component_index)) == root
                ) for layer_name in sorted(set(layers))
            },
        })
    required = ("DIFF", "TIN", "CNT", "ML1", "TEXT")
    missing_required = [name for name in required if not layer_presence[name]]
    missing_required.extend(
        name + "(no geometry)" for name in ("DIFF", "TIN", "CNT", "ML1")
        if regions[name].is_empty()
    )
    if regions["NPLUS"].is_empty() and regions["PPLUS"].is_empty():
        missing_required.append("NPLUS/PPLUS(no effective ohmic domain)")
    unsupported_metal = [
        name for name in _UNSUPPORTED_METAL_LAYERS if not regions[name].is_empty()
    ]
    return {
        "db": db,
        "layout": layout,
        "top": top,
        "top_name": top.name,
        "dbu_um": dbu_um,
        "layer_mode": layer_mode,
        "resolved_layers": {name: list(pair) for name, pair in sorted(layer_ids.items())},
        "regions": regions,
        "components": components,
        "electrical_ids": electrical_ids,
        "component_rows": component_rows,
        "contacts": contact_report,
        "labels": text_labels,
        "layer_presence": layer_presence,
        "unsupported_metal_layers": unsupported_metal,
        "missing_required_layers": sorted(set(missing_required)),
        "required_layers_present": not missing_required,
        "sha256": _sha256(path),
        "method": (
            "separate effective N+/P+ ohmic domains, SOI geometry isolated from the electrical graph, "
            "and only complete legal CNT landings connect domains to ML1 or TiN to ML1; "
            "channels, mixed implants, and unknown film do not conduct"
        ),
    }


def _physical_anchor(anchor: Any, geometry: dict[str, Any], pex: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(anchor, dict):
        return {"status": "BLOCKED", "error": "anchor descriptor must be an object"}
    unsupported = geometry.get("unsupported_metal_layers", [])
    if unsupported:
        return {
            "status": "BLOCKED",
            "error": "electrical anchors are unqualified because upper-metal geometry is unsupported",
            "unsupported_layers": unsupported,
        }
    kind = anchor.get("kind")
    layer: str | None = None
    point_um: list[float] | None = None
    point_dbu: tuple[int, int] | None = None
    if kind == "port":
        name = anchor.get("name")
        if not isinstance(name, str) or not name:
            return {"status": "BLOCKED", "error": "port anchor needs a nonempty name"}
        labels = [item for item in geometry["labels"] if _casefold(item["name"]) == name.casefold()]
        if len(labels) != 1:
            return {"status": "FAIL" if len(labels) > 1 else "BLOCKED",
                    "error": "expected exactly one GDS text anchor named %s; found %d" % (name, len(labels))}
        point_dbu = tuple(labels[0]["point_dbu"])
        point_um = [item * geometry["dbu_um"] for item in point_dbu]
        layer_value = anchor.get("layer")
        if layer_value is None and pex is not None:
            maps = [record for record in pex["port_maps"]
                    if _casefold(record.get("logical")) == name.casefold()]
            layers = {record.get("layer") for record in maps if isinstance(record.get("layer"), str)}
            if len(layers) == 1:
                layer_value = next(iter(layers))
        if layer_value is None:
            return {"status": "BLOCKED", "error": "port anchor needs an explicit layer or unique PEX PORT_MAP layer"}
        try:
            layer = _layer_name(layer_value)
        except PostlayoutERCError as error:
            return {"status": "BLOCKED", "error": str(error)}
    elif kind in ("pin", "body_contact", "mos_terminal"):
        layer_value = anchor.get("layer")
        if layer_value is None:
            return {"status": "BLOCKED", "error": "%s anchor requires explicit layer" % kind}
        try:
            layer = _layer_name(layer_value)
        except PostlayoutERCError as error:
            return {"status": "BLOCKED", "error": str(error)}
        if "point_um" in anchor:
            point_um = anchor["point_um"]
            if not isinstance(point_um, list) or len(point_um) != 2:
                return {"status": "BLOCKED", "error": "point_um must be [x,y]"}
            try:
                point_dbu = tuple(_um_to_dbu(value, geometry["dbu_um"], "anchor point_um") for value in point_um)
            except PostlayoutERCError as error:
                return {"status": "BLOCKED", "error": str(error)}
        elif "point_dbu" in anchor:
            values = anchor["point_dbu"]
            if not isinstance(values, list) or len(values) != 2 or any(
                    isinstance(value, bool) or not isinstance(value, int) for value in values):
                return {"status": "BLOCKED", "error": "point_dbu must be [integer_x,integer_y]"}
            point_dbu = (values[0], values[1])
            point_um = [value * geometry["dbu_um"] for value in values]
        else:
            return {"status": "BLOCKED", "error": "%s anchor needs point_um or point_dbu" % kind}
    elif kind == "mos_gate":
        device = anchor.get("device")
        if not isinstance(device, str) or pex is None:
            return {"status": "BLOCKED", "error": "mos_gate anchor needs device and parsed PEX"}
        matches = [record for record in pex["mos_maps"]
                   if _casefold(record.get("device")) == device.casefold()]
        if len(matches) != 1:
            return {"status": "FAIL" if len(matches) > 1 else "BLOCKED",
                    "error": "expected one MOS_MAP for gate device %s; found %d" % (device, len(matches))}
        record = matches[0]
        if not all(field in record for field in ("gate_x_um", "gate_y_um")):
            return {"status": "BLOCKED", "error": "MOS_MAP lacks gate coordinate for %s" % device}
        point_um = [record["gate_x_um"], record["gate_y_um"]]
        try:
            point_dbu = tuple(_um_to_dbu(value, geometry["dbu_um"], "MOS_MAP gate coordinate")
                              for value in point_um)
        except PostlayoutERCError as error:
            return {"status": "BLOCKED", "error": str(error)}
        layer = "TIN"
    else:
        return {"status": "BLOCKED", "error": "unsupported anchor kind %r" % kind}

    requested_layer = layer
    if layer == "DIFF":
        nplus_ids = _point_ids(point_dbu, geometry["components"]["NPLUS"], geometry["db"])
        pplus_ids = _point_ids(point_dbu, geometry["components"]["PPLUS"], geometry["db"])
        if len(nplus_ids) + len(pplus_ids) != 1:
            return {
                "status": "BLOCKED",
                "error": "SOI island geometry is not a uniquely classified N+/P+ ohmic conductor at this point",
                "kind": kind,
                "name": anchor.get("name", anchor.get("instance", anchor.get("device"))),
                "point_um": point_um,
                "layer": "DIFF",
            }
        layer = "NPLUS" if nplus_ids else "PPLUS"
    ids = _point_ids(point_dbu, geometry["components"][layer], geometry["db"])
    if len(ids) != 1:
        return {
            "status": "FAIL" if not ids else "BLOCKED",
            "error": "anchor is not on exactly one %s conductor component (found %d)" % (layer, len(ids)),
            "kind": kind,
            "name": anchor.get("name", anchor.get("instance", anchor.get("device"))),
            "point_um": point_um,
            "layer": layer,
        }
    root_id = geometry["electrical_ids"][layer][ids[0]]
    if kind == "body_contact":
        error = (
            "an isolated P+ contact does not qualify assembled local body access"
            if layer == "PPLUS" else
            "body_contact anchor is not on an effective P+ ohmic domain"
        )
        return {
            "status": "BLOCKED",
            "error": error,
            "kind": kind,
            "name": anchor.get("name", anchor.get("instance")),
            "point_um": point_um,
            "layer": layer,
            "component_id": root_id,
            "unqualified_boundary": True,
        }
    result = {
        "status": "PASS",
        "kind": kind,
        "name": anchor.get("name", anchor.get("instance", anchor.get("device"))),
        "instance": anchor.get("instance"),
        "pin": anchor.get("pin"),
        "point_um": point_um,
        "layer": layer,
        "component_id": root_id,
        "network_node": anchor.get("network_node"),
    }
    if requested_layer != layer:
        result["requested_layer"] = requested_layer
    return result


def _lvs_comments(network: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    node_names: dict[str, str] = {}
    for comment in network["metadata"]["comments"]:
        match = _NET_COMMENT.match(comment)
        if match:
            node_names[match.group(1).casefold()] = match.group(2)
    devices: dict[str, dict[str, Any]] = {}
    comments = network["metadata"].get("selected_comments", [])
    for comment in comments:
        match = _DEVICE_COMMENT.match(comment)
        if not match:
            continue
        instance, orientation, x_text, y_text, polarity = match.groups()
        key = "%s@%.6f,%.6f" % (polarity.upper(), float(x_text), float(y_text))
        devices[key.casefold()] = {
            "layout_device": key,
            "instance": instance,
            "orientation": orientation.casefold(),
            "polarity": polarity.upper(),
            "x_um": float(x_text),
            "y_um": float(y_text),
        }
    return node_names, devices


def _lvs_instance_key(value: Any) -> str:
    key = _casefold(value)
    return key[1:] if key.startswith("m$") else key


def _unique_index(records: list[dict[str, Any]], field: str, label: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    result: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            errors.append("%s record has no %s: %r" % (label, field, record))
            continue
        key = value.casefold()
        if key in result:
            errors.append("duplicate %s %s" % (label, value))
        else:
            result[key] = record
    return result, errors


def _resistor_device_owned(resistor: dict[str, Any], network: dict[str, Any]) -> bool:
    record = resistor.get("resistor_map", {})
    if record.get("device_owned") is True:
        return True
    owner = record.get("owner", record.get("ownership", record.get("device_owner")))
    if isinstance(owner, str) and owner.casefold() in (
            "icps2023_5_r_tin", "r_tin_device", "native_resistor_device", "lvs_device"):
        return True
    if record.get("_comment_type") in ("ICPS2023_5_R_TIN", "R_TIN_MAP"):
        return True
    name = resistor["name"].casefold()
    for item in network.get("native_resistor_maps", []):
        mapped_name = next((item[key] for key in ("name", "instance", "element")
                            if isinstance(item.get(key), str)), None)
        if isinstance(mapped_name, str) and mapped_name.casefold() == name:
            return True
    return False


def _resistor_maps_audit(network: dict[str, Any], label: str) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    maps, map_errors = _unique_index(network["resistor_maps"], "name", "%s RESISTOR_MAP element" % label)
    errors = list(map_errors)
    missing: list[str] = []
    elements = {item["name"].casefold(): item for item in network["resistors"]}
    if elements and not maps:
        missing.append("%s RESISTOR_MAP records" % label)
    if set(maps) - set(elements):
        errors.append("%s RESISTOR_MAP names absent from circuit: %s" %
                      (label, sorted(set(maps) - set(elements))))
    if set(elements) - set(maps):
        missing.append("%s RESISTOR_MAP entries for %s" % (label, sorted(set(elements) - set(maps))))
    for key, mapping in maps.items():
        element = elements.get(key)
        if element is None:
            continue
        a, b = mapping.get("a"), mapping.get("b")
        if not isinstance(a, str) or not isinstance(b, str):
            missing.append("%s RESISTOR_MAP endpoints for %s" % (label, mapping.get("name")))
        elif {a.casefold(), b.casefold()} != {element["a"].casefold(), element["b"].casefold()}:
            errors.append("%s RESISTOR_MAP endpoints disagree for %s" % (label, mapping.get("name")))
        try:
            mapped_resistance = float(mapping["resistance_ohm"])
        except (KeyError, TypeError, ValueError):
            missing.append("%s RESISTOR_MAP resistance_ohm for %s" % (label, mapping.get("name")))
        else:
            if not math.isfinite(mapped_resistance) or not math.isclose(
                    mapped_resistance, element["resistance_ohm"], rel_tol=1e-9, abs_tol=1e-12):
                errors.append("%s RESISTOR_MAP resistance disagrees for %s" % (label, mapping.get("name")))
        material = mapping.get("material")
        if not isinstance(material, str) or not material.strip():
            missing.append("%s RESISTOR_MAP material for %s" % (label, mapping.get("name")))
        elif material.upper() in ("UNKNOWN", "MIXED"):
            missing.append("%s material ownership for %s is %s" % (label, mapping.get("name"), material.upper()))
    return maps, errors, missing


def _map_audit(networks: dict[str, dict[str, Any] | None], config: dict[str, Any]) -> dict[str, Any]:
    lvs, pex, assembled = (networks.get(key) for key in ("lvs", "pex", "assembled"))
    if not all((lvs, pex, assembled)):
        return _check("BLOCKED", "all three LVS/PEX/assembled netlists are required")
    errors: list[str] = []
    missing: list[str] = []

    pex_port_by_logical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in pex["port_maps"]:
        logical = record.get("logical")
        physical = record.get("physical")
        if not isinstance(logical, str) or not isinstance(physical, str):
            errors.append("malformed PEX PORT_MAP record: %r" % record)
            continue
        pex_port_by_logical[logical.casefold()].append(record)
        if physical.casefold() not in {node.casefold() for node in pex["nodes"]}:
            errors.append("PORT_MAP %s physical node %s is absent from PEX network" % (logical, physical))
        if physical.casefold() not in {node.casefold() for node in pex["ports"]}:
            errors.append("PORT_MAP %s physical node %s is not a PEX subcircuit port" % (logical, physical))
    if not pex["port_maps"]:
        missing.append("PEX PORT_MAP records")
    lvs_names, lvs_devices = _lvs_comments(lvs)
    used_dc_nodes = {node.casefold() for resistor in pex["resistors"]
                     for node in (resistor["a"], resistor["b"])}
    for mapping in pex["mos_maps"]:
        used_dc_nodes.update(_casefold(mapping.get(role)) for role in ("drain", "gate", "source")
                             if isinstance(mapping.get(role), str))
    for record in pex["port_maps"]:
        physical = record.get("physical")
        if isinstance(physical, str) and physical.casefold() not in used_dc_nodes:
            errors.append("PORT_MAP %s physical node %s has no DC resistor/MOS anchor" %
                          (record.get("logical"), physical))
    for node in lvs["ports"]:
        logical = lvs_names.get(node.casefold())
        if logical is None:
            missing.append("LVS * net name for top-level anchor %s" % node)
            continue
        mapped = pex_port_by_logical.get(logical.casefold(), [])
        ml1 = [record for record in mapped if _casefold(record.get("layer")) == "ml1"]
        if len(ml1) != 1:
            missing.append("one ML1 PEX PORT_MAP anchor for LVS logical pin %s" % logical)
        elif not isinstance(ml1[0].get("physical"), str):
            errors.append("malformed ML1 PEX PORT_MAP anchor for %s" % logical)


    pex_resistors, pex_map_errors, pex_map_missing = _resistor_maps_audit(pex, "PEX")
    errors.extend(pex_map_errors)
    missing.extend(pex_map_missing)
    assembly_resistors, assembly_map_errors, assembly_map_missing = _resistor_maps_audit(
        assembled, "assembled"
    )
    errors.extend(assembly_map_errors)
    missing.extend(assembly_map_missing)

    for native in lvs["native_resistor_maps"]:
        name = next((native[key] for key in ("name", "instance", "element")
                     if isinstance(native.get(key), str)), None)
        if name is None:
            missing.append("native LVS resistor ownership record has no instance name")
            continue
        matching = [record for record in assembled["resistor_maps"]
                    if _casefold(record.get("source_lvs_instance")) == name.casefold()]
        if len(matching) != 1:
            missing.append("one assembled RESISTOR_MAP ownership record for native LVS resistor %s" % name)
        elif _casefold(matching[0].get("ownership")) != "lvs_device":
            errors.append("native LVS resistor %s lost explicit lvs_device ownership" % name)

    native_assembly_records = [record for record in assembled["resistor_maps"]
                               if _casefold(record.get("ownership")) == "lvs_device"]
    for record in native_assembly_records:
        if _casefold(record.get("material")) != "tin":
            errors.append("LVS-owned native resistor %s is not classified as TiN" % record.get("name"))

    owner_by_physical: dict[str, set[str]] = defaultdict(set)
    for logical, records in pex_port_by_logical.items():
        for record in records:
            owner_by_physical[_casefold(record.get("physical"))].add(logical)
    aliases = {node: sorted(names) for node, names in owner_by_physical.items() if len(names) > 1}
    if aliases:
        errors.append("one physical PEX port is aliased by distinct logical labels: %s" % aliases)

    pex_mos, mos_errors = _unique_index(pex["mos_maps"], "device", "PEX MOS_MAP device")
    errors.extend(mos_errors)
    if not pex["mos_maps"]:
        missing.append("PEX MOS_MAP records")
    _, lvs_devices = _lvs_comments(lvs)
    if not lvs_devices:
        missing.append("LVS MOS geometry comments")
    elif set(pex_mos) != set(lvs_devices):
        errors.append("LVS/PEX MOS geometry mismatch: LVS-only=%s PEX-only=%s" % (
            sorted(set(lvs_devices) - set(pex_mos)), sorted(set(pex_mos) - set(lvs_devices))))
    for device, mapping in pex_mos.items():
        if _casefold(mapping.get("drain")) == _casefold(mapping.get("source")):
            errors.append("PEX MOS_MAP aliases drain and source for %s" % mapping.get("device"))
        for terminal in ("drain", "gate", "source"):
            node = mapping.get(terminal)
            if not isinstance(node, str) or node.casefold() not in {item.casefold() for item in pex["nodes"]}:
                errors.append("PEX MOS_MAP %s %s node is absent from PEX network" % (mapping.get("device"), terminal))
    for mapping in pex_mos.values():
        for field in ("source_contact", "drain_contact"):
            contact = mapping.get(field)
            if not isinstance(contact, str) or not contact:
                missing.append("MOS_MAP %s for %s" % (field, mapping.get("device")))
        if mapping.get("source_contact") == mapping.get("drain_contact"):
            errors.append("MOS_MAP aliases source and drain contacts for %s" % mapping.get("device"))


    assembly_nodes, assembly_node_errors = _unique_index(
        assembled["assembly_node_maps"], "pex", "ASSEMBLY_NODE_MAP pex node"
    )
    errors.extend(assembly_node_errors)
    if not assembled["assembly_node_maps"]:
        missing.append("ASSEMBLY_NODE_MAP records")
    else:
        pex_referenced = {node.casefold() for node in pex["nodes"]}
        mapped_pex = set(assembly_nodes)
        if pex_referenced - mapped_pex:
            missing.append("ASSEMBLY_NODE_MAP nodes %s" % sorted(pex_referenced - mapped_pex))
        mapped_targets: dict[str, list[str]] = defaultdict(list)
        assembled_nodes = {node.casefold() for node in assembled["nodes"]}
        for pex_node, record in assembly_nodes.items():
            target = record.get("assembled")
            if not isinstance(target, str) or not target:
                errors.append("ASSEMBLY_NODE_MAP %s lacks assembled node" % record.get("pex"))
                continue
            if target.casefold() not in assembled_nodes:
                errors.append("ASSEMBLY_NODE_MAP target %s is absent from assembled circuit" % target)
            mapped_targets[target.casefold()].append(pex_node)
        aliases = {target: sorted(sources) for target, sources in mapped_targets.items() if len(sources) > 1}
        if aliases:
            errors.append("distinct PEX nodes alias to one assembled node: %s" % aliases)

    device_annotations, device_annotation_errors = _unique_index(
        assembled["mos_device_maps"], "name", "MOS_DEVICE_MAP instance"
    )
    errors.extend(device_annotation_errors)
    if not assembled["mos_device_maps"]:
        missing.append("assembled MOS_DEVICE_MAP body/layout annotations")
    assembled_mos_by_name, assembled_mos_errors = _unique_index(assembled["mos"], "name", "assembled MOS")
    errors.extend(assembled_mos_errors)
    if assembled["mos"] and not device_annotations:
        missing.append("assembled MOS-to-layout map correspondence")
    layout_by_assembly_name: dict[str, dict[str, Any]] = {}
    for mos_name, annotation in device_annotations.items():
        device = annotation.get("layout_device")
        if not isinstance(device, str) or not device:
            missing.append("layout_device for assembled MOS %s" % annotation.get("name"))
            continue
        key = device.casefold()
        if key in layout_by_assembly_name:
            errors.append("multiple assembled MOS claim layout device %s" % device)
        layout_by_assembly_name[key] = annotation
        if mos_name not in assembled_mos_by_name:
            errors.append("MOS_DEVICE_MAP name %s is absent from assembled MOS elements" % annotation.get("name"))
    if pex_mos and layout_by_assembly_name and set(layout_by_assembly_name) != set(pex_mos):
        errors.append("assembled/PEX MOS layout mismatch: assembled-only=%s PEX-only=%s" % (
            sorted(set(layout_by_assembly_name) - set(pex_mos)),
            sorted(set(pex_mos) - set(layout_by_assembly_name))))

    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    return _check(status, "LVS/PEX/assembly maps checked without assuming electrical equivalence",
                  errors=errors, missing_evidence=missing,
                  counts={"lvs_mos_geometry": len(lvs_devices), "pex_mos_maps": len(pex_mos),
                          "pex_port_maps": len(pex["port_maps"]),
                          "pex_resistor_maps": len(pex_resistors),
                          "assembly_node_maps": len(assembly_nodes),
                          "assembly_mos_maps": len(device_annotations)})


def _association_rows(config: dict[str, Any], networks: dict[str, dict[str, Any] | None],
                      geometry: dict[str, Any] | None, report: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    rows = config.get("expected_logical_associations")
    if not isinstance(rows, list) or not rows:
        check = _check("BLOCKED", "expected_logical_associations must explicitly map every checked net")
        report["checks"]["expected_logical_associations"] = check
        return {}, check
    if geometry is None:
        check = _check("BLOCKED", "GDS geometry is unavailable")
        report["checks"]["expected_logical_associations"] = check
        return {}, check
    lvs, pex, assembled = (networks.get(key) for key in ("lvs", "pex", "assembled"))
    if not all((lvs, pex, assembled)):
        check = _check("BLOCKED", "LVS, PEX, and assembled graph evidence is required")
        report["checks"]["expected_logical_associations"] = check
        return {}, check
    lvs_names, _ = _lvs_comments(lvs)
    pex_nodes = {node.casefold() for node in pex["nodes"]}
    assembled_nodes = {node.casefold() for node in assembled["nodes"]}
    assembly_map = {record.get("pex", "").casefold(): record.get("assembled", "")
                    for record in assembled["assembly_node_maps"]
                    if isinstance(record.get("pex"), str)}
    resolved: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    missing: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("net"), str) or not row["net"]:
            errors.append("association %d requires a nonempty net name" % index)
            continue
        key = row["net"].casefold()
        if key in resolved:
            errors.append("duplicate logical association for %s" % row["net"])
            continue
        anchors = row.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            missing.append("physical anchors for %s" % row["net"])
            anchors = []
        lvs_node = row.get("lvs_node")
        if not isinstance(lvs_node, str) or not lvs_node:
            missing.append("LVS node for %s" % row["net"])
        elif lvs_node.casefold() not in {node.casefold() for node in lvs["nodes"]}:
            errors.append("expected LVS node %s for %s is absent" % (lvs_node, row["net"]))
        else:
            actual_name = lvs_names.get(lvs_node.casefold())
            if actual_name is None:
                missing.append("LVS * net name for %s node %s" % (row["net"], lvs_node))
            elif actual_name.casefold() != row["net"].casefold():
                errors.append("LVS node %s is named %s, expected %s" % (lvs_node, actual_name, row["net"]))
        pex_values = row.get("pex_nodes")
        assembled_values = row.get("assembled_nodes")
        if not isinstance(pex_values, list) or not pex_values or not all(isinstance(x, str) for x in pex_values):
            missing.append("PEX nodes for %s" % row["net"])
            pex_values = []
        if not isinstance(assembled_values, list) or not assembled_values or not all(
                isinstance(x, str) for x in assembled_values):
            missing.append("assembled nodes for %s" % row["net"])
            assembled_values = []
        logical_port_maps = [record for record in pex["port_maps"]
                             if _casefold(record.get("logical")) == key]
        for port_map in logical_port_maps:
            physical = port_map.get("physical")
            if isinstance(physical, str) and physical.casefold() not in {node.casefold() for node in pex_values}:
                errors.append("PEX PORT_MAP %s points to %s outside configured PEX nodes %s" %
                              (row["net"], physical, pex_values))
        for node in pex_values:
            if node.casefold() not in pex_nodes:
                errors.append("expected PEX node %s for %s is absent" % (node, row["net"]))
        for node in assembled_values:
            if node.casefold() not in assembled_nodes:
                errors.append("expected assembled node %s for %s is absent" % (node, row["net"]))
        if pex_values and assembled_values:
            actual_targets = {assembly_map.get(node.casefold(), "").casefold() for node in pex_values}
            if not all(assembly_map.get(node.casefold()) for node in pex_values):
                missing.append("ASSEMBLY_NODE_MAP for %s nodes %s" % (row["net"], pex_values))
            if actual_targets != {node.casefold() for node in assembled_values}:
                errors.append("assembly map for %s resolves to %s, expected %s" %
                              (row["net"], sorted(actual_targets), sorted(node.casefold() for node in assembled_values)))
        anchor_results = [_physical_anchor(anchor, geometry, pex) for anchor in anchors]
        roots = sorted({item["component_id"] for item in anchor_results if item.get("status") == "PASS"})
        for anchor, result in zip(anchors, anchor_results):
            result["expected"] = anchor
        resolved[key] = {
            "net": row["net"],
            "lvs_node": lvs_node,
            "pex_nodes": pex_values,
            "assembled_nodes": assembled_values,
            "anchors": anchor_results,
            "component_ids": roots,
            "row": row,
        }
        for result in anchor_results:
            if result.get("status") == "FAIL":
                errors.append("%s anchor %s is not on its declared conductor" %
                              (row["net"], result.get("name", result.get("expected"))))
            elif result.get("status") != "PASS":
                missing.append("physical location for %s anchor %s" %
                               (row["net"], result.get("error", result.get("expected"))))
        if len(roots) > 1:
            errors.append("expected logical net %s is physically open across GDS components %s" % (row["net"], roots))
        elif not roots:
            missing.append("actual GDS conductor component for %s" % row["net"])

    shorts: dict[int, list[str]] = defaultdict(list)
    for key, item in resolved.items():
        for component_id in item["component_ids"]:
            shorts[component_id].append(item["net"])
    actual_shorts = {component: sorted(set(nets)) for component, nets in shorts.items() if len(set(nets)) > 1}
    if actual_shorts:
        errors.append("one actual conductor component contains distinct expected nets: %s" % actual_shorts)
    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    check = _check(status, "explicit logical associations compared across LVS/PEX/assembly and actual GDS",
                   errors=errors, missing_evidence=missing,
                   associations={key: {"net": item["net"], "lvs_node": item["lvs_node"],
                                       "pex_nodes": item["pex_nodes"],
                                       "assembled_nodes": item["assembled_nodes"],
                                       "component_ids": item["component_ids"],
                                       "anchors": item["anchors"]}
                                 for key, item in resolved.items()},
                   shorts=[{"component_id": component, "nets": nets}
                           for component, nets in actual_shorts.items()])
    report["checks"]["expected_logical_associations"] = check
    return resolved, check


def _resolve_path_graph(paths: list[dict[str, Any]], geometry: dict[str, Any],
                        pex: dict[str, Any] | None,
                        associations: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    missing: list[str] = []
    for index, path in enumerate(paths):
        if not isinstance(path, dict):
            errors.append("expected_paths[%d] must be an object" % index)
            continue
        source = path.get("source")
        sink = path.get("sink")
        net = path.get("net")
        source_result = _physical_anchor(source, geometry, pex)
        sink_result = _physical_anchor(sink, geometry, pex)
        result = {"name": path.get("name", "path-%d" % index), "net": net,
                  "source": source_result, "sink": sink_result}
        results.append(result)
        for label, anchor_result in (("source", source_result), ("sink", sink_result)):
            if anchor_result.get("status") == "FAIL":
                errors.append("%s of path %s is not on a conductor: %s" %
                              (label, result["name"], anchor_result.get("error")))
            elif anchor_result.get("status") != "PASS":
                missing.append("%s anchor for path %s: %s" %
                               (label, result["name"], anchor_result.get("error")))
        association = associations.get(_casefold(net)) if isinstance(net, str) else None
        if association is None:
            missing.append("explicit logical association for path %s net %r" % (result["name"], net))
        if source_result.get("status") == sink_result.get("status") == "PASS":
            if source_result["component_id"] != sink_result["component_id"]:
                errors.append("path %s is physically open: component %s to %s" %
                              (result["name"], source_result["component_id"], sink_result["component_id"]))
            else:
                result["connected_component_id"] = source_result["component_id"]
                if association is not None and source_result["component_id"] not in association["component_ids"]:
                    errors.append("path %s is physically connected on component %s, not expected net %s components %s" %
                                  (result["name"], source_result["component_id"], association["net"],
                                   association["component_ids"]))
    return results, errors, missing


def _power_paths(config: dict[str, Any], geometry: dict[str, Any], pex: dict[str, Any] | None,
                 associations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sources = config.get("power_sources")
    loads = config.get("expected_load_pins")
    if not isinstance(sources, list) or not sources or not isinstance(loads, list) or not loads:
        return _check("BLOCKED", "explicit power_sources and expected_load_pins are required")
    errors: list[str] = []
    missing: list[str] = []
    paths = []
    for source_index, source in enumerate(sources):
        if not isinstance(source, dict) or not all(isinstance(source.get(field), str) and source[field]
                                                   for field in ("net", "port", "pad")):
            errors.append("power_sources[%d] requires explicit net, port, and pad" % source_index)
            continue
        association = associations.get(source["net"].casefold())
        if association is None:
            missing.append("logical association for power source %s" % source["net"])
        elif pex is not None:
            mapped_ports = [record for record in pex["port_maps"]
                            if _casefold(record.get("logical")) == source["port"].casefold()]
            if len(mapped_ports) != 1:
                missing.append("one explicit PEX PORT_MAP for source port %s" % source["port"])
            elif _casefold(mapped_ports[0].get("physical")) not in {
                    node.casefold() for node in association["pex_nodes"]}:
                errors.append("power source port %s does not map to expected %s PEX nodes" %
                              (source["port"], source["net"]))
        pad_anchor = {"kind": "port", "name": source["pad"], "layer": source.get("layer")}
        source_anchor = _physical_anchor(pad_anchor, geometry, pex)
        if source_anchor.get("status") == "FAIL":
            errors.append("power pad %s: %s" % (source["pad"], source_anchor.get("error")))
        elif source_anchor.get("status") != "PASS":
            missing.append("power pad %s: %s" % (source["pad"], source_anchor.get("error")))
        matching_loads = [row for row in loads if isinstance(row, dict) and
                          _casefold(row.get("net")) == source["net"].casefold()]
        if not matching_loads:
            missing.append("expected cell load pins on %s" % source["net"])
        for load in matching_loads:
            if not all(isinstance(load.get(field), str) and load[field] for field in ("instance", "pin")):
                errors.append("expected_load_pin requires instance and pin: %r" % load)
                continue
            if "point_um" not in load and "point_dbu" not in load:
                missing.append("physical coordinate for %s/%s" % (load["instance"], load["pin"]))
                continue
            load_anchor = {"kind": "pin", "instance": load["instance"], "pin": load["pin"],
                           "layer": load.get("layer"), "point_um": load.get("point_um"),
                           "point_dbu": load.get("point_dbu")}
            load_anchor = {key: value for key, value in load_anchor.items() if value is not None}
            load_result = _physical_anchor(load_anchor, geometry, pex)
            if association is not None:
                registered = any(
                    isinstance(anchor, dict) and _casefold(anchor.get("kind")) == "pin" and
                    _casefold(anchor.get("instance")) == load["instance"].casefold() and
                    _casefold(anchor.get("pin")) == load["pin"].casefold()
                    for anchor in association["row"].get("anchors", [])
                )
                if not registered:
                    missing.append("expected association anchor for power load %s/%s on %s" %
                                   (load["instance"], load["pin"], source["net"]))
            path_status = "BLOCKED"
            if source_anchor.get("status") == load_result.get("status") == "PASS":
                path_status = ("PASS" if source_anchor["component_id"] == load_result["component_id"]
                               else "FAIL")
            elif source_anchor.get("status") == "FAIL" or load_result.get("status") == "FAIL":
                path_status = "FAIL"
            path_result = {"net": source["net"], "source_port": source["port"],
                           "source_pad": source["pad"], "load": load_anchor,
                           "source_component_id": source_anchor.get("component_id"),
                           "load_component_id": load_result.get("component_id"),
                           "status": path_status}
            paths.append(path_result)
            if load_result.get("status") == "FAIL":
                errors.append("power load %s/%s is not on a conductor: %s" %
                              (load["instance"], load["pin"], load_result.get("error")))
            elif load_result.get("status") != "PASS":
                missing.append("power load %s/%s: %s" %
                               (load["instance"], load["pin"], load_result.get("error")))
            elif source_anchor.get("status") == "PASS" and source_anchor["component_id"] != load_result["component_id"]:
                errors.append("power open %s pad %s to %s/%s" %
                              (source["net"], source["pad"], load["instance"], load["pin"]))
    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    return _check(status, "explicit pad-to-each-load conductor continuity; no pad function inferred",
                  paths=paths, errors=errors, missing_evidence=missing)


def _mos_association_check(config: dict[str, Any], networks: dict[str, dict[str, Any] | None],
                           association_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected = config.get("expected_mos_associations")
    lvs, pex, assembled = (networks.get(key) for key in ("lvs", "pex", "assembled"))
    if not isinstance(expected, list) or not expected:
        return _check("BLOCKED", "expected_mos_associations must explicitly declare D/G/S net association")
    if not all((lvs, pex, assembled)):
        return _check("BLOCKED", "LVS/PEX/assembled graphs are required")
    lvs_names, lvs_devices = _lvs_comments(lvs)
    pex_by_device = {record.get("device", "").casefold(): record for record in pex["mos_maps"]
                     if isinstance(record.get("device"), str)}
    annotations = {record.get("name", "").casefold(): record for record in assembled["mos_device_maps"]
                   if isinstance(record.get("name"), str)}
    assembled_mos = {record["name"].casefold(): record for record in assembled["mos"]}
    node_maps = {record.get("pex", "").casefold(): record.get("assembled")
                 for record in assembled["assembly_node_maps"] if isinstance(record.get("pex"), str)}
    r_neighbors: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for resistor in assembled["resistors"]:
        if not resistor["name"].casefold().startswith("rcnt"):
            continue
        a, b = resistor["a"].casefold(), resistor["b"].casefold()
        r_neighbors[a].append((b, resistor))
        r_neighbors[b].append((a, resistor))

    errors: list[str] = []
    missing: list[str] = []
    checked: list[dict[str, Any]] = []
    expected_devices: set[str] = set()
    for row in expected:
        if not isinstance(row, dict) or not isinstance(row.get("device"), str):
            errors.append("each expected MOS association needs layout device key")
            continue
        device_key = row["device"].casefold()
        expected_devices.add(device_key)
        pex_map = pex_by_device.get(device_key)
        if pex_map is None:
            errors.append("expected MOS device %s has no PEX MOS_MAP" % row["device"])
            continue
        lvs_device = lvs_devices.get(device_key)
        if lvs_device is None:
            errors.append("expected MOS device %s has no LVS geometry anchor" % row["device"])
            continue
        if lvs_device["orientation"] != "r0":
            missing.append("qualified D/S orientation for %s" % row["device"])
        actual_lvs_matches = [
            item for item in lvs["mos"]
            if _lvs_instance_key(item.get("name")) ==
            _lvs_instance_key(lvs_device.get("instance"))
        ]
        actual_lvs_mos = actual_lvs_matches[0] if len(actual_lvs_matches) == 1 else None
        if actual_lvs_mos is None:
            missing.append("exactly one LVS MOS instance is required for %s; found %d" %
                           (row["device"], len(actual_lvs_matches)))
        else:
            for role in ("drain", "gate", "source"):
                expected_net = row.get(role + "_net")
                if not isinstance(expected_net, str) or not expected_net:
                    missing.append("expected %s_net for %s" % (role, row["device"]))
                    continue
                node = actual_lvs_mos[{"drain": "d", "gate": "g", "source": "s"}[role]]
                actual_net = lvs_names.get(node.casefold())
                if actual_net is None:
                    missing.append("LVS * net name for %s/%s node %s" % (row["device"], role, node))
                elif actual_net.casefold() != expected_net.casefold():
                    errors.append("LVS %s/%s is on %s, expected %s" %
                                  (row["device"], role, actual_net, expected_net))
                association = association_rows.get(expected_net.casefold())
                if association is None:
                    missing.append("logical association row for MOS %s/%s net %s" %
                                   (row["device"], role, expected_net))
                else:
                    candidate_nodes = {node.casefold() for node in association["row"].get("pex_nodes", [])}
                    if pex_map.get(role) and pex_map[role].casefold() not in candidate_nodes:
                        errors.append("PEX MOS_MAP %s/%s node %s is not among expected %s nodes %s" %
                                      (row["device"], role, pex_map.get(role), expected_net,
                                       sorted(candidate_nodes)))

        actual_assembly = None
        annotation = None
        for name, mapped in annotations.items():
            if _casefold(mapped.get("layout_device")) == device_key:
                annotation = mapped
                actual_assembly = assembled_mos.get(name)
                break
        if annotation is None or actual_assembly is None:
            missing.append("assembled M/DEVICE_MAP for %s" % row["device"])
        else:
            for role, pex_role in (("g", "gate"), ("e", "e")):
                expected_pex = pex_map.get(pex_role)
                mapped_node = node_maps.get(_casefold(expected_pex)) if isinstance(expected_pex, str) else None
                if not mapped_node:
                    missing.append("ASSEMBLY_NODE_MAP for %s %s node" % (row["device"], pex_role))
                elif actual_assembly[role].casefold() != mapped_node.casefold():
                    errors.append("assembled %s/%s terminal %s disagrees with mapped PEX %s node %s" %
                                  (row["device"], role.upper(), actual_assembly[role], pex_role, mapped_node))
            for role in ("drain", "source"):
                mos_terminal = actual_assembly["d" if role == "drain" else "s"].casefold()
                adjacent = r_neighbors.get(mos_terminal, [])
                expected_external = node_maps.get(_casefold(pex_map.get(role)))
                if not expected_external:
                    missing.append("ASSEMBLY_NODE_MAP for %s %s node" % (row["device"], role))
                elif len(adjacent) != 1:
                    missing.append("one explicit RCNT contact path from %s %s terminal" % (row["device"], role))
                elif adjacent[0][0] != expected_external.casefold():
                    errors.append("assembled %s %s contact reaches %s, expected PEX %s node %s" %
                                  (row["device"], role, adjacent[0][0], role, expected_external))
            if actual_assembly["d"].casefold() == actual_assembly["s"].casefold():
                errors.append("assembled MOS %s drain and source alias directly" % row["device"])

        checked.append({"device": row["device"], "lvs_geometry": lvs_device,
                        "pex_map": pex_map, "assembled_instance": actual_assembly})
    if pex_by_device and expected_devices != set(pex_by_device):
        missing.append("explicit expected D/G/S association for PEX MOS devices %s" %
                       sorted(set(pex_by_device) - expected_devices))
    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    return _check(status, "D/G/S/LVS/PEX/assembly correspondence checked; MOS channels are not wire edges",
                  errors=errors, missing_evidence=missing, devices=checked)


def _body_checks(config: dict[str, Any], networks: dict[str, dict[str, Any] | None],
                 geometry: dict[str, Any] | None, associations: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    assembled = networks.get("assembled")
    if assembled is None:
        body = _check("BLOCKED", "assembled MOS body annotations are unavailable")
        env = _check("BLOCKED", "assembled E-terminal network is unavailable")
        return body, env
    body_contacts = config.get("body_contacts", [])
    body_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    missing: list[str] = []
    if not assembled["mos"]:
        missing.append("assembled MOS devices and explicit body-state annotations")
    for mos in assembled["mos"]:
        state = mos.get("body_state")
        device_name = mos.get("layout_device") or mos["name"]
        state_fold = _casefold(state)
        row = {"instance": mos["name"], "layout_device": mos.get("layout_device"),
               "body_state": state, "e": mos["e"], "p": mos["p"]}
        if state_fold == "floating_body":
            if mos["p"] is not None:
                errors.append("%s is declared floating_body but has a local P terminal" % mos["name"])
                row["status"] = "FAIL"
            else:
                row["status"] = "PASS"
                row["interpretation"] = "explicitly declared floating body; FD/PD not inferred"
        elif state_fold == "body_tied":
            if not mos["model"].casefold().startswith(("n", "nch")):
                missing.append("PMOS bodytie qualification is unsupported for %s" % mos["name"])
                row["status"] = "BLOCKED"
            elif mos["p"] is None:
                errors.append("%s is declared body_tied without a fifth P terminal" % mos["name"])
                row["status"] = "FAIL"
            else:
                candidates = [item for item in body_contacts if isinstance(item, dict) and
                              _casefold(item.get("device")) in (mos["name"].casefold(), _casefold(device_name))]
                if len(candidates) != 1:
                    missing.append("exactly one explicit body_contacts entry for %s" % mos["name"])
                    row["status"] = "BLOCKED"
                elif geometry is None:
                    missing.append("GDS body-contact geometry for %s" % mos["name"])
                    row["status"] = "BLOCKED"
                else:
                    contact = candidates[0]
                    point = contact.get("point_um")
                    bias_net = contact.get("bias_net")
                    bias_source = contact.get("bias_source")
                    bias_range = contact.get("bias_range_v")
                    if (not isinstance(point, list) or len(point) != 2 or not isinstance(bias_net, str) or
                            not isinstance(bias_source, str) or not isinstance(bias_range, list) or
                            len(bias_range) != 2):
                        missing.append("body contact coordinates, bias_net, bias_source, and bias_range_v for %s" % mos["name"])
                        row["status"] = "BLOCKED"
                    else:
                        try:
                            point_dbu = tuple(_um_to_dbu(value, geometry["dbu_um"], "body contact point")
                                              for value in point)
                            coords_valid = all(math.isfinite(float(value)) for value in bias_range)
                        except (PostlayoutERCError, TypeError, ValueError):
                            coords_valid = False
                            point_dbu = (0, 0)
                        diff_ids = _point_ids(point_dbu, geometry["components"]["DIFF"], geometry["db"])
                        parea_ids = _point_ids(
                            point_dbu, _region_components(geometry["regions"]["PAREA"], geometry["db"]),
                            geometry["db"]
                        )
                        pplus_ids = _point_ids(point_dbu, geometry["components"]["PPLUS"], geometry["db"])
                        bias = associations.get(bias_net.casefold())
                        contact_root = (geometry["electrical_ids"]["PPLUS"][pplus_ids[0]]
                                        if len(pplus_ids) == 1 else None)
                        if not coords_valid or float(bias_range[0]) > float(bias_range[1]):
                            errors.append("invalid explicit body-bias range for %s" % mos["name"])
                            row["status"] = "FAIL"
                        elif len(diff_ids) != 1 or len(parea_ids) != 1 or len(pplus_ids) != 1:
                            errors.append("%s body contact is not on one effective P+ SOI domain" % mos["name"])
                            row["status"] = "FAIL"
                        elif (bias is None or contact_root not in bias["component_ids"] or
                              mos["p"].casefold() not in {
                                  node.casefold() for node in bias["assembled_nodes"]
                              }):
                            errors.append("%s local P+ contact/terminal is not physically continuous to bias net %s" %
                                          (mos["name"], bias_net))
                            row["status"] = "FAIL"
                        else:
                            row.update({"status": "BLOCKED", "body_contact_um": point,
                                        "bias_net": bias_net, "bias_source": bias_source,
                                        "bias_range_v": bias_range, "p_node": mos["p"],
                                        "component_id": contact_root,
                                        "qualification": "isolated P+ contact does not establish assembled local body access"})
                            missing.append(
                                "%s local body-access qualification is unsupported by isolated P+ contact evidence" %
                                mos["name"]
                            )
        else:
            errors.append("%s has undeclared body_state %r" % (mos["name"], state))
            row["status"] = "FAIL"
        body_rows.append(row)

    body_status = "FAIL" if errors else "BLOCKED" if missing else "PASS"
    body = _check(body_status, "body policy is explicit; absent P never proves a body tie",
                  devices=body_rows, errors=errors, missing_evidence=missing)

    environment = config.get("external_body_environment")
    if not isinstance(environment, dict):
        env = _check("BLOCKED", "external E/SUB physical bias source is unverified; .GLOBAL is not a physical contact")
    else:
        source_port = environment.get("source_port")
        evidence = environment.get("evidence")
        bias_range = environment.get("bias_range_v")
        if not isinstance(source_port, str) or not source_port or not isinstance(evidence, str) or not evidence.strip():
            env = _check("BLOCKED", "external E environment needs explicit source_port and evidence")
        elif source_port.casefold() not in {port.casefold() for port in assembled["ports"]}:
            env = _check("FAIL", "external E source_port %s is not an assembled subcircuit port" % source_port)
        elif not assembled["mos"] or source_port.casefold() not in {
                mos["e"].casefold() for mos in assembled["mos"]}:
            env = _check("FAIL", "external E source_port %s is not the actual MOS E-terminal node" % source_port)
        elif not isinstance(bias_range, list) or len(bias_range) != 2:
            env = _check("BLOCKED", "external E environment needs explicit bias_range_v")
        else:
            try:
                valid_range = all(math.isfinite(float(value)) for value in bias_range) and float(bias_range[0]) <= float(bias_range[1])
            except (TypeError, ValueError):
                valid_range = False
            if not valid_range:
                env = _check("FAIL", "external E bias_range_v must be a finite ordered pair")
            else:
                env = _check("PASS", "external E environment explicitly declared; not foundry-qualified",
                             source_port=source_port, bias_range_v=bias_range, evidence=evidence,
                             qualification="user-declared evidence only; physical/foundry bias qualification remains outside ERC")
    return body, env


def _driven_gate_check(config: dict[str, Any], networks: dict[str, dict[str, Any] | None],
                       geometry: dict[str, Any] | None, associations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pex = networks.get("pex")
    expected = config.get("expected_mos_associations")
    driven = config.get("driven_signal_ports")
    if geometry is None or pex is None:
        return _check("BLOCKED", "actual GDS and PEX MOS gate coordinates are required")
    if not isinstance(driven, list) or not driven:
        return _check("BLOCKED", "driven_signal_ports must explicitly declare driven signal ports")
    if not isinstance(expected, list) or not expected:
        return _check("BLOCKED", "expected MOS gate net associations are required")
    errors: list[str] = []
    missing: list[str] = []
    driven_roots: set[int] = set()
    for port in driven:
        if not isinstance(port, dict) or not isinstance(port.get("port"), str) or not isinstance(port.get("net"), str):
            errors.append("each driven_signal_ports row needs port and net")
            continue
        association = associations.get(port["net"].casefold())
        if association is None:
            missing.append("logical association for driven port %s/%s" % (port["port"], port["net"]))
        else:
            driven_roots.update(association["component_ids"])
    for source in config.get("power_sources", []):
        if isinstance(source, dict) and isinstance(source.get("net"), str):
            association = associations.get(source["net"].casefold())
            if association:
                driven_roots.update(association["component_ids"])

    device_rows = {row.get("device", "").casefold(): row for row in expected if isinstance(row, dict)
                   and isinstance(row.get("device"), str)}
    map_by_device = {row.get("device", "").casefold(): row for row in pex["mos_maps"]
                     if isinstance(row.get("device"), str)}
    gate_data: list[dict[str, Any]] = []
    for device_key, mapping in map_by_device.items():
        expected_device = device_rows.get(device_key)
        if expected_device is None:
            missing.append("expected MOS gate association for %s" % mapping.get("device"))
            continue
        gate_net = expected_device.get("gate_net")
        association = associations.get(_casefold(gate_net)) if isinstance(gate_net, str) else None
        if association is None:
            missing.append("logical gate association for %s" % mapping.get("device"))
            continue
        anchor = _physical_anchor({"kind": "mos_gate", "device": mapping["device"]}, geometry, pex)
        if anchor.get("status") != "PASS":
            if anchor.get("status") == "FAIL":
                errors.append("MOS gate %s: %s" % (mapping.get("device"), anchor.get("error")))
            else:
                missing.append("MOS gate geometry %s: %s" % (mapping.get("device"), anchor.get("error")))
            continue
        gate_root = anchor["component_id"]
        if association["component_ids"] and gate_root not in association["component_ids"]:
            errors.append("MOS gate %s GDS component %s does not match expected net %s components %s" %
                          (mapping.get("device"), gate_root, gate_net, association["component_ids"]))
        gate_data.append({"device": mapping.get("device"), "gate_net": gate_net,
                          "component_id": gate_root, "anchor": anchor,
                          "drain_net": expected_device.get("drain_net"),
                          "source_net": expected_device.get("source_net")})

    # Seed only explicit external signal/power components. Conditional MOS-stage
    # closure is a control-reachability annotation, never an electrical wire edge.
    closure = set(driven_roots)
    changed = True
    while changed:
        changed = False
        for row in gate_data:
            gate_net = row.get("gate_net")
            gate_assoc = associations.get(_casefold(gate_net)) if isinstance(gate_net, str) else None
            gate_roots = set(gate_assoc["component_ids"]) if gate_assoc else set()
            if not gate_roots.intersection(closure):
                continue
            terminal_roots = []
            for role in ("drain_net", "source_net"):
                net = row.get(role)
                association = associations.get(_casefold(net)) if isinstance(net, str) else None
                terminal_roots.append(set(association["component_ids"]) if association else set())
            if len(terminal_roots) != 2:
                continue
            if terminal_roots[0].intersection(closure) and not terminal_roots[1].issubset(closure):
                closure.update(terminal_roots[1])
                changed = True
            if terminal_roots[1].intersection(closure) and not terminal_roots[0].issubset(closure):
                closure.update(terminal_roots[0])
                changed = True

    for row in gate_data:
        if row["component_id"] not in closure:
            errors.append("MOS gate %s net %s is floating/not in explicit driven-set closure" %
                          (row["device"], row["gate_net"]))
        row["driven_by_explicit_seed_or_gated_stage"] = row["component_id"] in closure
    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    return _check(status, "MOS channels are excluded from DC connectivity; closure requires explicit driven seeds",
                  driven_seed_component_ids=sorted(driven_roots),
                  conditional_gated_stage_component_ids=sorted(closure - driven_roots),
                  gates=gate_data, errors=errors, missing_evidence=missing)


def _floating_islands(geometry: dict[str, Any] | None, anchored_components: set[int]) -> dict[str, Any]:
    if geometry is None:
        return _check("BLOCKED", "GDS conductor graph is unavailable")
    islands = [row for row in geometry["component_rows"]
               if any(layer in ("ML1", "TIN") for layer in row["layers"]) and
               row["component_id"] not in anchored_components]
    if islands:
        return _check("UNASSESSED", "unassociated actual ML1/TiN conductor islands remain; no logical net is inferred",
                      islands=islands, count=len(islands))
    return _check("PASS", "every actual ML1/TiN electrical component has an explicit expected anchor", islands=[])


def _dijkstra(network: dict[str, Any], source: str, sink: str,
              include_device_owned: bool = True) -> dict[str, Any] | None:
    graph: dict[str, list[tuple[str, float, dict[str, Any]]]] = defaultdict(list)
    node_names: dict[str, str] = {}
    for resistor in network["resistors"]:
        if not include_device_owned and _resistor_device_owned(resistor, network):
            continue
        a, b = resistor["a"].casefold(), resistor["b"].casefold()
        node_names.setdefault(a, resistor["a"])
        node_names.setdefault(b, resistor["b"])
        graph[a].append((b, resistor["resistance_ohm"], resistor))
        graph[b].append((a, resistor["resistance_ohm"], resistor))
    start, end = source.casefold(), sink.casefold()
    if start == end:
        return {"resistance_ohm": 0.0, "nodes": [source], "branches": []}
    if start not in graph or end not in graph:
        return None
    distance = {start: 0.0}
    previous: dict[str, tuple[str, dict[str, Any]]] = {}
    queue: list[tuple[float, str]] = [(0.0, start)]
    while queue:
        cost, node = heappop(queue)
        if cost != distance.get(node):
            continue
        if node == end:
            break
        for neighbor, resistance, resistor in graph[node]:
            candidate = cost + resistance
            if candidate < distance.get(neighbor, math.inf):
                distance[neighbor] = candidate
                previous[neighbor] = (node, resistor)
                heappush(queue, (candidate, neighbor))
    if end not in distance:
        return None
    route_nodes = [end]
    branches = []
    cursor = end
    while cursor != start:
        prior, resistor = previous[cursor]
        branches.append(resistor)
        route_nodes.append(prior)
        cursor = prior
    route_nodes.reverse()
    branches.reverse()
    return {"resistance_ohm": distance[end],
            "nodes": [node_names.get(node, node) for node in route_nodes],
            "branches": [{"name": item["name"], "resistance_ohm": item["resistance_ohm"],
                          "material": item.get("material", "UNKNOWN"),
                          "device_owned": _resistor_device_owned(item, network),
                          "geometry": item.get("geometry")} for item in branches]}


def _resistance_paths(config: dict[str, Any], networks: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    paths = config.get("expected_paths")
    screen = config.get("high_resistance_screening")
    if not isinstance(paths, list) or not paths:
        return _check("BLOCKED", "expected_paths must explicitly declare physical and extracted-network paths")
    if (not isinstance(screen, dict) or "threshold_ohm" not in screen or
            not isinstance(screen.get("source"), str) or not screen["source"].strip()):
        return _check("UNASSESSED", "no user-supplied high-resistance screen threshold/source; no limit is fabricated")
    try:
        threshold = float(screen["threshold_ohm"])
    except (TypeError, ValueError):
        return _check("FAIL", "high_resistance_screening.threshold_ohm must be numeric")
    if not math.isfinite(threshold) or threshold <= 0:
        return _check("FAIL", "high_resistance_screening.threshold_ohm must be positive and finite")
    errors: list[str] = []
    missing: list[str] = []
    results = []
    for index, row in enumerate(paths):
        if not isinstance(row, dict):
            errors.append("expected_paths[%d] must be an object" % index)
            continue
        network_name = row.get("network")
        source_node = row.get("source_node")
        sink_node = row.get("sink_node")
        if network_name not in ("pex", "assembled") or not isinstance(source_node, str) or not isinstance(sink_node, str):
            missing.append("explicit network/source_node/sink_node for expected path %s" % row.get("name", index))
            continue
        network = networks.get(network_name)
        if network is None:
            missing.append("%s graph for expected path %s" % (network_name, row.get("name", index)))
            continue
        network_nodes = {node.casefold() for node in network["nodes"]}
        if source_node.casefold() not in network_nodes or sink_node.casefold() not in network_nodes:
            missing.append("configured path nodes are absent from %s: %s -> %s" %
                           (network_name, source_node, sink_node))
            continue
        path = _dijkstra(network, source_node, sink_node, include_device_owned=True)
        if path is None:
            errors.append("no explicit R-only extracted path from %s to %s in %s" %
                          (source_node, sink_node, network_name))
            continue
        path["name"] = row.get("name", "path-%d" % index)
        path["network"] = network_name
        path["source_node"] = source_node
        path["sink_node"] = sink_node
        path["threshold_ohm"] = threshold
        path["threshold_source"] = screen["source"]
        path["above_screen_threshold"] = path["resistance_ohm"] > threshold
        if path["above_screen_threshold"]:
            errors.append("path %s resistance %.9g ohm exceeds user screen threshold %.9g ohm" %
                          (path["name"], path["resistance_ohm"], threshold))
        span_um = 0.0
        span_complete = True
        for branch in path["branches"]:
            material = _casefold(branch.get("material"))
            if not material or material in ("unknown", "mixed"):
                missing.append("material classification for %s branch %s" %
                               (path["name"], branch["name"]))
            geometry = branch.get("geometry") or {}
            first, second = geometry.get("a_location_um"), geometry.get("b_location_um")
            if (not isinstance(first, list) or len(first) != 2 or
                    not isinstance(second, list) or len(second) != 2):
                span_complete = False
                continue
            try:
                ax, ay, bx, by = (float(first[0]), float(first[1]),
                                  float(second[0]), float(second[1]))
            except (TypeError, ValueError):
                span_complete = False
                continue
            if not all(math.isfinite(value) for value in (ax, ay, bx, by)):
                span_complete = False
                continue
            span_um += math.hypot(bx - ax, by - ay)
        path["endpoint_span_lower_bound_um"] = span_um if span_complete else None
        path["endpoint_span_is_straight_line_lower_bound"] = span_complete
        if not span_complete:
            missing.append("complete mapped branch endpoint geometry for path %s" % path["name"])
        results.append(path)
    if errors:
        status = "FAIL"
    elif missing:
        status = "BLOCKED"
    else:
        status = "PASS"
    return _check(status, "R-only shortest paths retain all extracted branches including device-owned R_TIN; screen is user-defined",
                  threshold_ohm=threshold, threshold_source=screen["source"], paths=results,
                  errors=errors, missing_evidence=missing,
                  foundry_limit_used=False)


def _contact_check(geometry: dict[str, Any] | None) -> dict[str, Any]:
    if geometry is None:
        return _check("BLOCKED", "actual GDS geometry is unavailable")
    if not geometry["required_layers_present"]:
        absent = sorted(name for name, present in geometry["layer_presence"].items() if not present)
        return _check("BLOCKED", "required native contact/conductor layers are absent", missing_layers=absent,
                      contacts=geometry["contacts"])
    bad = [item for item in geometry["contacts"] if item["status"] != "legal"]
    if bad:
        return _check("FAIL",
                      "an illegal CNT is present; only complete same-polarity diffusion landings or validated TiN/ML1 vias connect",
                      illegal_contacts=bad, contacts=geometry["contacts"])
    if geometry["unsupported_metal_layers"]:
        return _check("BLOCKED", "upper-metal connectivity is unsupported and cannot be qualified",
                      unsupported_layers=geometry["unsupported_metal_layers"], contacts=geometry["contacts"])
    return _check("PASS",
                  "only validated same-polarity CNT landings connect N+/P+ domains to ML1; MOS channels are excluded",
                  method="5x5 um contacts; ML1/TiN 2.5 um enclosure away from DIFF; diffusion/ML1 2.0 um complete landing",
                  illegal_contacts=[], contacts=geometry["contacts"])


def _signal_path_check(config: dict[str, Any], geometry: dict[str, Any] | None,
                       pex: dict[str, Any] | None,
                       associations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paths = config.get("expected_paths")
    if not isinstance(paths, list) or not paths:
        return _check("BLOCKED", "expected_paths must explicitly list signal/power endpoints")
    if geometry is None:
        return _check("BLOCKED", "actual GDS geometry is unavailable")
    results, errors, missing = _resolve_path_graph(paths, geometry, pex, associations)
    status = "FAIL" if errors else "BLOCKED" if missing else "PASS"
    return _check(status, "explicit GDS endpoint paths checked against expected net components",
                  paths=results, errors=errors, missing_evidence=missing)


def run_erc(config: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    """Run configured actual-GDS topology/body checks; absent evidence is BLOCKED."""
    if not isinstance(config, dict):
        raise PostlayoutERCError("configuration root must be a JSON object")
    base = Path(config_path).resolve().parent if config_path is not None else Path.cwd()
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "check_postlayout_erc.py",
        "qualification": "research_PDK_screen_only_not_foundry_signoff",
        "layer_mode": config.get("layer_mode"),
        "checks": {},
        "inputs": {},
        "findings": [],
    }
    required_paths = ("gds", "lvs", "pex", "assembled")
    resolved_paths: dict[str, Path] = {}
    source_errors: list[str] = []
    for key in required_paths:
        value = config.get(key)
        if not isinstance(value, str) or not value:
            source_errors.append("missing explicit %s path" % key)
            report["inputs"][key] = None
            continue
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
        resolved_paths[key] = path
        exists = path.is_file()
        report["inputs"][key] = {"path": str(path), "exists": exists}
        if not exists:
            source_errors.append("%s file is missing: %s" % (key, path))
    report["checks"]["source_files"] = _check(
        "FAIL" if any("file is missing" in item for item in source_errors) else "BLOCKED" if source_errors else "PASS",
        "explicit GDS, LVS, PEX, and assembled source paths",
        errors=source_errors)
    top_name = config.get("top")
    if top_name is not None and not isinstance(top_name, str):
        report["checks"]["configuration"] = _check("FAIL", "top must be a string when supplied")
        top_name = None
    subcircuits = config.get("subcircuits", {})
    if not isinstance(subcircuits, dict):
        report["checks"]["configuration"] = _check("FAIL", "subcircuits must map source roles to subcircuit names")
        subcircuits = {}

    networks: dict[str, dict[str, Any] | None] = {"lvs": None, "pex": None, "assembled": None}
    parse_errors: dict[str, str] = {}
    for role in ("lvs", "pex", "assembled"):
        path = resolved_paths.get(role)
        if path is None or not path.is_file():
            parse_errors[role] = "source path is absent"
            continue
        selected = subcircuits.get(role)
        if selected is not None and not isinstance(selected, str):
            parse_errors[role] = "subcircuits.%s must be a string" % role
            continue
        try:
            networks[role] = read_network(path, selected)
            report["inputs"][role].update({"sha256": networks[role]["sha256"],
                                            "subcircuit": networks[role]["name"]})
        except (ReliabilityNetlistError, OSError, ValueError) as error:
            parse_errors[role] = str(error)
    if parse_errors:
        report["checks"]["netlist_parse"] = _check(
            "FAIL" if any("unsupported" in value or "malformed" in value for value in parse_errors.values()) else "BLOCKED",
            "R/C/M source netlist parsing did not silently omit elements",
            errors=parse_errors)
    else:
        report["checks"]["netlist_parse"] = _check(
            "PASS", "LVS/PEX/assembled circuits parsed with explicit R/C/M elements and hierarchy rejection")

    geometry: dict[str, Any] | None = None
    gds_path = resolved_paths.get("gds")
    if gds_path is not None and gds_path.is_file():
        try:
            gds_top = config.get("gds_top")
            if gds_top is not None and not isinstance(gds_top, str):
                raise PostlayoutERCError("gds_top must be a string when supplied")
            geometry = _layout_inputs(
                gds_path, gds_top, config.get("layers"), layer_mode=config.get("layer_mode")
            )
            report["inputs"]["gds"].update({
                "sha256": geometry["sha256"],
                "top_cell": geometry["top_name"],
                "dbu_um": geometry["dbu_um"],
                "layer_mode": geometry["layer_mode"],
                "resolved_layers": geometry["resolved_layers"],
            })
            if geometry["required_layers_present"] and not geometry["unsupported_metal_layers"]:
                report["checks"]["gds_geometry"] = _check(
                    "PASS", "actual GDS loaded with explicit mode-specific layer mapping",
                    top_cell=geometry["top_name"], layer_mode=geometry["layer_mode"],
                    resolved_layers=geometry["resolved_layers"],
                    layer_presence=geometry["layer_presence"],
                    electrical_components=geometry["component_rows"], method=geometry["method"])
            else:
                report["checks"]["gds_geometry"] = _check(
                    "BLOCKED", "actual GDS geometry has missing required evidence or unsupported upper metal",
                    top_cell=geometry["top_name"], layer_mode=geometry["layer_mode"],
                    resolved_layers=geometry["resolved_layers"],
                    missing_layers=geometry["missing_required_layers"],
                    unsupported_metal_layers=geometry["unsupported_metal_layers"],
                    electrical_components=geometry["component_rows"], method=geometry["method"])
        except (PostlayoutERCError, OSError, ValueError, RuntimeError) as error:
            report["checks"]["gds_geometry"] = _check(
                "BLOCKED", "actual GDS could not be assessed with the required explicit layer mode",
                layer_mode=config.get("layer_mode"), error=str(error))
    else:
        report["checks"]["gds_geometry"] = _check(
            "BLOCKED", "actual GDS path is unavailable or explicit layer_mode=design|fab is absent",
            layer_mode=config.get("layer_mode"))

    report["checks"]["map_correspondence"] = _map_audit(networks, config)
    report["checks"]["illegal_contacts"] = _contact_check(geometry)
    associations, association_check = _association_rows(config, networks, geometry, report)
    report["checks"]["logical_shorts_and_opens"] = association_check
    report["checks"]["power_pad_to_load"] = (
        _power_paths(config, geometry, networks.get("pex"), associations) if geometry is not None else
        _check("BLOCKED", "actual GDS geometry is unavailable")
    )
    report["checks"]["expected_paths"] = _signal_path_check(
        config, geometry, networks.get("pex"), associations)

    mos_check = _mos_association_check(config, networks, associations)
    report["checks"]["mos_terminal_association"] = mos_check
    body_check, environment_check = _body_checks(config, networks, geometry, associations)
    report["checks"]["body_state_and_local_ties"] = body_check
    report["checks"]["external_e_environment"] = environment_check
    report["checks"]["floating_gates_and_driven_closure"] = _driven_gate_check(
        config, networks, geometry, associations)

    anchored_ids = {component for item in associations.values() for component in item["component_ids"]}
    for check_name in ("floating_gates_and_driven_closure",):
        check = report["checks"][check_name]
        for gate in check.get("gates", []):
            if isinstance(gate.get("component_id"), int):
                anchored_ids.add(gate["component_id"])
    report["checks"]["unassigned_conductor_islands"] = _floating_islands(geometry, anchored_ids)
    report["checks"]["high_resistance_paths"] = _resistance_paths(config, networks)

    for name, check in report["checks"].items():
        if check["status"] == "FAIL":
            report["findings"].append({"check": name, "severity": "error", "summary": check["summary"]})
        elif check["status"] in ("BLOCKED", "UNASSESSED"):
            report["findings"].append({"check": name, "severity": "unqualified", "summary": check["summary"]})
    if any(check["status"] == "FAIL" for check in report["checks"].values()):
        report["status"] = "FAIL"
    elif any(check["status"] != "PASS" for check in report["checks"].values()):
        report["status"] = "BLOCKED"
    else:
        report["status"] = "PASS"
    report["limitations"] = [
        "GDS connectivity requires an explicit design or fab layer_mode; the resolved mapping is reported.",
        "N+ and P+ are separate ohmic domains; channels, mixed implants, and unknown SOI film are not DC wires.",
        "A local P+ contact alone does not qualify assembled body access; PMOS fifth-P support remains blocked.",
        "Upper-metal connectivity is unsupported and blocks topology qualification when present.",
        "External E/SUB bias is not proved by .GLOBAL or a net name.",
        "No current-density, lifetime, thermal, FD/PD, or foundry qualification is inferred.",
        "Only explicit configured logical anchors are classified; unassigned geometry is reported unassessed.",
    ]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True,
                        help="JSON config with explicit paths and layer_mode=design|fab, plus configured sources, loads, associations, and paths")
    parser.add_argument("--report", help="optional JSON report path; stdout if omitted")
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        result = run_erc(config, config_path)
    except (OSError, json.JSONDecodeError, PostlayoutERCError) as error:
        parser.error(str(error))
    output = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = config_path.parent / report_path
        report_path.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 1 if result["status"] == "FAIL" else 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    sys.exit(main())
