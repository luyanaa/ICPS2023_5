"""Validated views of the experimental ICPS2023_5 standard-cell library."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - supplied by the pinned KLayout toolchain
    from klayout import db  # type: ignore

try:
    from . import logic, verify
except ImportError:  # direct import from the stdcell tool directory
    import logic  # type: ignore
    import verify  # type: ignore

try:
    from . import model_binding
except ImportError:  # direct import from the stdcell tool directory
    import model_binding  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs.ref/ICPS2023_5_gen_sc"
DEFAULT_MANIFEST = LIBRARY / "doc/cell_manifest.json"
PROFILE = "icps2023_5_1m"
BOUNDARY_LAYER = (100, 0)
PHYSICAL_LAYERS = ((1, 0), (3, 0), (5, 0), (7, 0), (8, 0), (18, 0), (19, 0))
# Experimental placement-row contract shared with LibreLane
# (libs.tech/librelane/experimental_1m/icps2023_5_1m.tech.lef declares the same
# SITE).  Cells are height-normalised to it; a cell of another height declares
# no site and is therefore not row-placeable rather than silently mis-declared.
ROW_SITE_NAME = "ICPS2023_5_ROW500"
ROW_SITE_HEIGHT_UM = 500.0


class LibraryValidationError(ValueError):
    """A published physical cell is absent, stale, or internally inconsistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise LibraryValidationError(f"required library artifact is unavailable: {path}") from error


def _current_provenance() -> dict[str, Any]:
    binding = model_binding.load()
    files = {
        "logic_sha256": ROOT / "libs.tech/stdcell/logic.py",
        "generator_sha256": ROOT / "libs.tech/stdcell/geometry.py",
        "config_sha256": ROOT / "libs.tech/stdcell/config.json",
        **model_binding.provenance_files(),
    }
    current = {name: _file_digest(path) for name, path in files.items()}
    generator_files = {
        "geometry.py": current["generator_sha256"],
        "channel_geometry.py": _file_digest(ROOT / "libs.tech/stdcell/channel_geometry.py"),
    }
    current["generator_files_sha256"] = generator_files
    current["generator_sha256"] = _digest(generator_files)
    current["deck_sha256"] = verify.verification_deck_hashes()
    current["source_specs_sha256"] = _digest(logic.cell_specs())
    current["model_binding"] = {**model_binding.engine_record(),
                                "library_sha256": current["model_sha256"],
                                "include_sha256": current["model_include_sha256"]}
    return current


def _logical_reference_text(specs: dict[str, dict[str, Any]]) -> str:
    lines = ["* ICPS2023_5 canonical 1M standard-cell reference", ".GLOBAL SUB"]
    for name, spec in specs.items():
        lines.append(f".SUBCKT {name} {' '.join(spec['pins'])}")
        for mos in spec["mos"]:
            model = "nch" if mos["polarity"] == "n" else "pch"
            instance = str(mos["name"])
            if not instance.upper().startswith("M"):
                instance = "M_" + instance
            lines.append(
                f"{instance} {mos['d']} {mos['g']} {mos['s']} SUB {model} "
                f"W={mos['w_um'] * 1e-6:.12g} L={mos['l_um'] * 1e-6:.12g}")
        lines.append(f".ENDS {name}")
    return "\n".join(lines) + "\n"


def _num(value: float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".") or "0"


def _lef_text(record: dict[str, Any]) -> str:
    name = record["cell"]
    width, height = record["cell_width_um"], record["cell_height_um"]
    source = record["source_spec"]
    inputs, outputs = set(source["inputs"]), set(source["outputs"])
    lines = ["VERSION 5.8 ;", f"MACRO {name}", "  CLASS CORE ;",
             "  ORIGIN 0 0 ;",
             f"  SIZE {_num(width)} BY {_num(height)} ;"]
    if math.isclose(height, ROW_SITE_HEIGHT_UM, rel_tol=0, abs_tol=1e-9):
        lines.append(f"  SITE {ROW_SITE_NAME} ;")
    for pin, data in record["pins"].items():
        if pin == "VDD":
            direction, use = "INOUT", "POWER"
        elif pin == "VSS":
            direction, use = "INOUT", "GROUND"
        elif pin in inputs and pin in outputs:
            direction, use = "INOUT", "SIGNAL"
        elif pin in outputs:
            direction, use = "OUTPUT", "SIGNAL"
        else:
            direction, use = "INPUT", "SIGNAL"
        lines.extend([f"  PIN {pin}", f"    DIRECTION {direction} ;",
                      f"    USE {use} ;", "    PORT", "      LAYER ML1 ;"])
        for x1, y1, x2, y2 in data["windows_um"]:
            lines.append(f"        RECT {_num(x1)} {_num(y1)} {_num(x2)} {_num(y2)} ;")
        lines.extend(["    END", f"  END {pin}"])
    lines.append("  OBS")
    if record["obstructions_um"]:
        lines.append("    LAYER ML1 ;")
        for x1, y1, x2, y2 in record["obstructions_um"]:
            lines.append(f"      RECT {_num(x1)} {_num(y1)} {_num(x2)} {_num(y2)} ;")
    lines.extend(["  END", f"END {name}", ""])
    return "\n".join(lines)


def librelane_lef_text(record: dict[str, Any]) -> str:
    """The same cell with the physical ML1 layer named by the router alias.

    The experimental LibreLane technology LEF declares the routing layer as
    `M1`; the canonical LEF keeps the physical process name `ML1`.  Only the
    layer name changes, exactly like the frame library's `*_librelane.lef`.
    """
    text = _lef_text(record)
    if "LAYER ML1" not in text:
        raise LibraryValidationError(
            f"{record.get('cell')} has no ML1 geometry to alias")
    return text.replace("LAYER ML1", "LAYER M1")


def _resolve_artifact(library_root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise LibraryValidationError("published view path is missing")
    path = (library_root / relative).resolve()
    try:
        path.relative_to(library_root.resolve())
    except ValueError as error:
        raise LibraryValidationError(f"view path escapes the library: {relative}") from error
    return path


def _read_gds_boundary(gds_path: Path, cell_name: str) -> tuple[float, float]:
    layout = db.Layout()
    try:
        layout.read(str(gds_path))
    except Exception as error:
        raise LibraryValidationError(f"cannot read GDS for {cell_name}: {error}") from error
    top = layout.cell(cell_name)
    if top is None:
        raise LibraryValidationError(f"GDS for {cell_name} has no top cell named {cell_name}")
    boundary_index = layout.find_layer(db.LayerInfo(*BOUNDARY_LAYER))
    if boundary_index is None:
        raise LibraryValidationError(f"GDS for {cell_name} has no reserved boundary on 100/0")
    boundaries = list(top.shapes(boundary_index).each())
    if len(boundaries) != 1 or not boundaries[0].is_box():
        raise LibraryValidationError(f"GDS for {cell_name} must contain one rectangular 100/0 boundary")
    boundary = boundaries[0].box
    coords = tuple(value * layout.dbu for value in
                   (boundary.left, boundary.bottom, boundary.right, boundary.top))
    if abs(coords[0]) > layout.dbu / 2 or abs(coords[1]) > layout.dbu / 2:
        raise LibraryValidationError(f"GDS boundary for {cell_name} must start at (0,0)")
    width, height = coords[2] - coords[0], coords[3] - coords[1]
    if width <= 0 or height <= 0:
        raise LibraryValidationError(f"GDS boundary for {cell_name} has nonpositive dimensions")
    for layer_info in PHYSICAL_LAYERS:
        index = layout.find_layer(db.LayerInfo(*layer_info))
        if index is None:
            continue
        for shape in top.shapes(index).each():
            if not shape.is_box():
                raise LibraryValidationError(
                    f"GDS for {cell_name} contains nonrectangular physical geometry on {layer_info[0]}/0")
            box = shape.box
            left, bottom, right, upper = (value * layout.dbu for value in
                                          (box.left, box.bottom, box.right, box.top))
            if (left < coords[0] - layout.dbu / 2 or bottom < coords[1] - layout.dbu / 2 or
                    right > coords[2] + layout.dbu / 2 or upper > coords[3] + layout.dbu / 2):
                raise LibraryValidationError(
                    f"physical geometry for {cell_name} extends outside its 100/0 boundary")
    try:
        verify.soi_edge_protection(layout, top)
    except ValueError as error:
        raise LibraryValidationError(f"SOI-edge protection failed for {cell_name}: {error}") from error
    return width, height


def _validate_physical_spec(record: dict[str, Any], current_spec: dict[str, Any]) -> None:
    source_spec = record.get("source_spec")
    physical_spec = record.get("physical_spec")
    if source_spec != current_spec:
        raise LibraryValidationError(f"source specification for {record.get('cell')} is stale")
    if not isinstance(physical_spec, dict):
        raise LibraryValidationError(f"physical device specification missing for {record.get('cell')}")
    if any(physical_spec.get(key) != value for key, value in source_spec.items() if key != "mos"):
        raise LibraryValidationError(f"physical logical pins/function drifted for {record['cell']}")
    parameters = record.get("parameters")
    if not isinstance(parameters, dict):
        raise LibraryValidationError(f"generator parameters missing for {record['cell']}")
    fingers = parameters.get("fingers")
    if not isinstance(fingers, int) or fingers < 1:
        raise LibraryValidationError(f"invalid finger count for {record['cell']}")
    width_um = parameters.get("width_um")
    if width_um is not None and (
            not isinstance(width_um, (int, float)) or not math.isfinite(width_um) or width_um <= 0):
        raise LibraryValidationError(f"invalid selected total MOS width for {record['cell']}")
    source_devices = {mos["name"]: mos for mos in source_spec["mos"]}
    generator_manifest = record.get("generator_manifest")
    if not isinstance(generator_manifest, dict):
        raise LibraryValidationError(f"full generator manifest missing for {record['cell']}")
    folding = generator_manifest.get("folding", {})
    variant = generator_manifest.get("variant", {})
    architecture = parameters.get("architecture")
    if (architecture not in {"compact_rows", "channel"} or
            generator_manifest.get("architecture") != architecture or
            variant.get("architecture") != architecture):
        raise LibraryValidationError(f"layout architecture is absent or inconsistent for {record['cell']}")
    if (generator_manifest.get("requested_height_um") != parameters.get("height_um") or
            generator_manifest.get("route_channel_um") != parameters.get("route_channel_um") or
            folding.get("requested_parallel_fingers") != fingers or
            variant.get("mirror") != parameters.get("mirror") or
            variant.get("pin_order") != parameters.get("pin_order")):
        raise LibraryValidationError(f"generator parameters disagree with geometry manifest for {record['cell']}")
    if (generator_manifest.get("width_um") != record.get("cell_width_um") or
            generator_manifest.get("height_um") != record.get("cell_height_um") or
            generator_manifest.get("bbox_um") != record.get("bbox_um") or
            generator_manifest.get("boundary_um") != record.get("boundary_um") or
            generator_manifest.get("obstructions_um") != record.get("obstructions_um") or
            generator_manifest.get("active_regions_um") is None or
            not isinstance(generator_manifest.get("routing", {}).get("shapes"), dict)):
        raise LibraryValidationError(f"published generator geometry is incomplete for {record['cell']}")
    device_fields = ("name", "polarity", "d", "g", "s", "w_um", "l_um")
    raw_devices = generator_manifest.get("devices")
    if (not isinstance(raw_devices, list) or
            any(not isinstance(device, dict) or
                any(key not in device for key in device_fields) for device in raw_devices)):
        raise LibraryValidationError(f"generator devices are incomplete for {record['cell']}")
    generated_devices = [{key: device[key] for key in device_fields} for device in raw_devices]
    if physical_spec["mos"] != generated_devices:
        raise LibraryValidationError(f"physical spec does not match generated devices for {record['cell']}")
    expected_count = len(source_devices) * fingers
    mos_list = physical_spec.get("mos")
    if not isinstance(mos_list, list) or len(mos_list) != expected_count:
        raise LibraryValidationError(f"physical device count for {record['cell']} disagrees with folding")
    for mos in mos_list:
        if not isinstance(mos, dict):
            raise LibraryValidationError(f"invalid physical MOS entry for {record['cell']}")
        base = re.sub(r"_f[0-9]+$", "", str(mos.get("name", "")))
        expected = source_devices.get(base)
        if expected is None:
            raise LibraryValidationError(f"physical MOS name {mos.get('name')} is not in the source spec")
        for key in ("polarity", "d", "g", "s", "l_um"):
            if mos.get(key) != expected[key]:
                raise LibraryValidationError(
                    f"physical MOS {mos.get('name')} {key} differs from its accepted source device")
        width = mos.get("w_um")
        if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
            raise LibraryValidationError(f"physical MOS {mos.get('name')} has invalid width")
    for name, source_mos in source_devices.items():
        folded = [mos for mos in mos_list
                  if re.sub(r"_f[0-9]+$", "", str(mos.get("name", ""))) == name]
        if len(folded) != fingers:
            raise LibraryValidationError(f"physical folding for {record['cell']}/{name} is incomplete")
        expected_total_width = source_mos["w_um"] if width_um is None else width_um
        if any(mos["w_um"] < 13.0 for mos in folded):
            raise LibraryValidationError(
                f"physical finger width for {record['cell']}/{name} is below 13um")
        if not math.isclose(sum(mos["w_um"] for mos in folded), expected_total_width,
                            rel_tol=0, abs_tol=0.001):
            raise LibraryValidationError(
                f"physical folding for {record['cell']}/{name} does not preserve selected total width")


def _validate_cell(record: dict[str, Any], current_spec: dict[str, Any],
                    library_root: Path, provenance: dict[str, Any],
                    logical_spice_sha256: str) -> None:
    name = record.get("cell")
    if record.get("status") != "strict_physical_pass":
        raise LibraryValidationError(f"cell {name} is not strictly physically accepted")
    if (record.get("qualification") != "experimental" or
            record.get("boundary_source") != "generator" or
            record.get("boundary_layer") != [100, 0] or
            record.get("site_width_um", "missing") is not None):
        raise LibraryValidationError(f"cell {name} has an invalid placement/qualification contract")
    _validate_physical_spec(record, current_spec)
    fingerprints = record.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise LibraryValidationError(f"provenance fingerprints missing for {name}")
    expected_fingerprints = {
        "source_spec_sha256": _digest(current_spec),
        "logic_sha256": provenance["logic_sha256"],
        "generator_sha256": provenance["generator_sha256"],
        "config_sha256": provenance["config_sha256"],
        "model_sha256": provenance["model_sha256"],
        "model_include_sha256": provenance["model_include_sha256"],
        "deck_sha256": provenance["deck_sha256"],
        "parameters_sha256": _digest(record["parameters"]),
        "physical_spec_sha256": _digest(record["physical_spec"]),
        "logical_spice_sha256": logical_spice_sha256,
    }
    for key, expected in expected_fingerprints.items():
        if fingerprints.get(key) != expected:
            raise LibraryValidationError(f"{name} has stale {key} provenance")

    try:
        width = float(record["cell_width_um"])
        height = float(record["cell_height_um"])
        area = float(record["area_um2"])
        bbox = [float(value) for value in record["bbox_um"]]
    except (KeyError, TypeError, ValueError) as error:
        raise LibraryValidationError(f"cell dimensions are incomplete for {name}") from error
    if (width <= 0 or height <= 0 or not math.isclose(area, width * height, rel_tol=0, abs_tol=1e-7) or
            bbox != [0.0, 0.0, width, height] or
            record.get("width_um") != width or record.get("height_um") != height or
            record.get("area_um2") != width * height):
        raise LibraryValidationError(f"cell dimensions/area are inconsistent for {name}")
    if record.get("boundary_um") != [0.0, 0.0, width, height]:
        raise LibraryValidationError(f"reserved boundary record is inconsistent for {name}")

    views = record.get("views")
    if (not isinstance(views, dict) or
            not {"gds", "lef", "spice"}.issubset(views) or
            not set(views).issubset({"gds", "lef", "spice", "lef_librelane"})):
        raise LibraryValidationError(f"physical GDS/LEF/SPICE paths are incomplete for {name}")
    paths = {key: _resolve_artifact(library_root, value) for key, value in views.items()}
    view_hashes = {key: _file_digest(path) for key, path in paths.items()}
    for view, fingerprint_key in (("gds", "gds_sha256"), ("lef", "lef_sha256"),
                                  ("spice", "spice_sha256")):
        if fingerprints.get(fingerprint_key) != view_hashes[view]:
            raise LibraryValidationError(f"{name} {view.upper()} does not match its published fingerprint")
    if fingerprints.get("views_sha256") != _digest(view_hashes):
        raise LibraryValidationError(f"{name} combined view fingerprint is stale")

    gds_width, gds_height = _read_gds_boundary(paths["gds"], name)
    if not math.isclose(gds_width, width, rel_tol=0, abs_tol=0.001) or not math.isclose(
            gds_height, height, rel_tol=0, abs_tol=0.001):
        raise LibraryValidationError(f"GDS boundary dimensions disagree with the manifest for {name}")

    pins = record.get("pins")
    generator_pins = record["generator_manifest"].get("pins")
    if (not isinstance(pins, dict) or set(pins) != set(current_spec["pins"]) or
            list(pins) != record["parameters"]["pin_order"]):
        raise LibraryValidationError(f"pin windows/order are missing or changed for {name}")
    if (not isinstance(generator_pins, dict) or list(generator_pins) != list(pins) or
            any(generator_pins.get(pin) != data.get("windows_um")
                for pin, data in pins.items() if isinstance(data, dict))):
        raise LibraryValidationError(f"pin windows disagree with generator geometry for {name}")
    for pin, data in pins.items():
        windows = data.get("windows_um") if isinstance(data, dict) else None
        if not isinstance(windows, list) or not windows:
            raise LibraryValidationError(f"pin {pin} has no physical ML1 window in {name}")
        for window in windows:
            if (not isinstance(window, list) or len(window) != 4 or
                    any(not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in window)):
                raise LibraryValidationError(f"invalid ML1 pin window for {name}/{pin}")
            x1, y1, x2, y2 = window
            if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x1 >= x2 or y1 >= y2:
                raise LibraryValidationError(f"ML1 pin window escapes the boundary for {name}/{pin}")
    obstructions = record.get("obstructions_um")
    if not isinstance(obstructions, list):
        raise LibraryValidationError(f"real ML1 obstructions missing for {name}")
    for rect in obstructions:
        if (not isinstance(rect, list) or len(rect) != 4 or
                any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in rect) or
                rect[0] < 0 or rect[1] < 0 or rect[2] > width or rect[3] > height or
                rect[0] >= rect[2] or rect[1] >= rect[3]):
            raise LibraryValidationError(f"ML1 obstruction escapes the boundary for {name}")
    try:
        lef = paths["lef"].read_text(encoding="utf-8")
    except OSError as error:
        raise LibraryValidationError(f"cannot read LEF for {name}") from error
    if lef != _lef_text(record):
        raise LibraryValidationError(f"LEF SIZE/ORIGIN/pins/OBS do not match geometry for {name}")
    alias_relative = paths.get("lef_librelane")
    if alias_relative is not None:
        try:
            alias = Path(alias_relative).read_text(encoding="utf-8")
        except OSError as error:
            raise LibraryValidationError(f"cannot read LibreLane alias LEF for {name}") from error
        if alias != librelane_lef_text(record):
            raise LibraryValidationError(
                f"LibreLane alias LEF is not the canonical geometry with the M1 alias for {name}")
        if "LAYER ML1" in alias:
            raise LibraryValidationError(f"LibreLane alias LEF still names ML1 for {name}")
    try:
        spice = paths["spice"].read_text(encoding="utf-8")
    except OSError as error:
        raise LibraryValidationError(f"cannot read physical SPICE for {name}") from error
    if spice != _logical_reference_text({name: record["physical_spec"]}):
        raise LibraryValidationError(f"physical SPICE does not match accepted MOS dimensions for {name}")
    physical = record.get("physical")
    if (not isinstance(physical, dict) or physical.get("process_profile") != PROFILE or
            physical.get("strict_drc") is not True or physical.get("strict_lvs") is not True or
            physical.get("pex") is not True or physical.get("assembly") is not True or
            physical.get("passed") is not True):
        raise LibraryValidationError(f"strict physical evidence is incomplete for {name}")
    protection = physical.get("soi_edge_protection")
    if not isinstance(protection, dict) or protection.get("passed") is not True:
        raise LibraryValidationError(f"SOI-edge protection evidence is incomplete for {name}")
    fabrication = physical.get("fabrication")
    if not isinstance(fabrication, dict) or fabrication.get("passed") is not True:
        raise LibraryValidationError(f"final-mask protection evidence is incomplete for {name}")


def load_library(manifest_path: str | Path | None = None, *, check: bool = True) -> dict[str, Any]:
    """Load the publication manifest, rejecting any stale physical view by default."""
    path = Path(manifest_path).resolve() if manifest_path is not None else DEFAULT_MANIFEST
    if not path.is_file():
        raise LibraryValidationError(f"standard-cell manifest is missing: {path}")
    library_root = path.parent.parent if path.parent.name == "doc" else path.parent
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LibraryValidationError(f"cannot read standard-cell manifest: {path}") from error
    if not isinstance(manifest, dict) or manifest.get("library") != "ICPS2023_5_gen_sc":
        raise LibraryValidationError("manifest does not describe ICPS2023_5_gen_sc")
    records = manifest.get("cells")
    if not isinstance(records, list):
        raise LibraryValidationError("manifest cells must be a list")
    specs = logic.cell_specs()
    by_name = {record.get("cell"): record for record in records if isinstance(record, dict)}
    if len(by_name) != len(records):
        raise LibraryValidationError("manifest contains duplicate or malformed cell records")
    if not check:
        # A read-only load must also succeed while a publication that adds a new
        # canonical spec is being prepared; the full canonical-set check below
        # stays in force for every validated load.
        return manifest
    if set(by_name) != set(specs):
        raise LibraryValidationError(
            f"manifest must retain exactly the {len(specs)} canonical cell records")

    current = _current_provenance()
    if manifest.get("status") != "experimental_partial":
        raise LibraryValidationError("manifest is not explicitly marked experimental_partial")
    provenance = manifest.get("provenance")
    if provenance != current:
        raise LibraryValidationError("library source/generator/config/model/deck provenance is stale")
    if manifest.get("source_specs_sha256") != current["source_specs_sha256"]:
        raise LibraryValidationError("canonical source specifications have changed")
    spice_relative = manifest.get("spice_file")
    logical_spice = _resolve_artifact(library_root, spice_relative)
    logical_spice_hash = _file_digest(logical_spice)
    if manifest.get("spice_file_sha256") != logical_spice_hash:
        raise LibraryValidationError("whole-library logical SPICE fingerprint is stale")
    try:
        spice_text = logical_spice.read_text(encoding="utf-8")
    except OSError as error:
        raise LibraryValidationError("whole-library logical SPICE is unavailable") from error
    if spice_text != _logical_reference_text(specs):
        raise LibraryValidationError("whole-library logical SPICE does not match the 26 canonical specs")

    for name, record in by_name.items():
        if record.get("status") == "not_published":
            continue
        _validate_cell(record, specs[name], library_root, provenance, logical_spice_hash)
    return manifest


def physical_specs(manifest_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Return accepted physical cell specs with the published device dimensions."""
    manifest = load_library(manifest_path)
    return {record["cell"]: record["physical_spec"] for record in manifest["cells"]
            if record["status"] == "strict_physical_pass"}


def physical_manifests(manifest_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the complete accepted physical publication record for each cell."""
    manifest = load_library(manifest_path)
    return {record["cell"]: record for record in manifest["cells"]
            if record["status"] == "strict_physical_pass"}


__all__ = ["load_library", "physical_specs", "physical_manifests"]
