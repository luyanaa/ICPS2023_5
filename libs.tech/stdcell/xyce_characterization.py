#!/usr/bin/env python3
"""Produce the experimental Xyce single-corner standard-cell Arc evidence.

This is the producer the Liberty evidence path was missing: it turns the
accepted-cell manifest plus per-cell *physical* evidence into the per-cell
records that ``experimental_liberty.py`` validates and renders.

    manifest (libs.ref/ICPS2023_5_gen_sc/doc/cell_manifest.json)
      + physical evidence (<physical-root>/<CELL>/physical.json,
        <physical-root>/<CELL>/postlayout.spice, produced here through
        ``verify.verify_physical`` when it is missing or belongs to another
        layout, or by ``verify.py``/``make_gen_sc.py`` directly)
      -> <out>/<CELL>/<CELL>.json          schema ICPS2023_5_xyce_characterization_v1
         <out>/<CELL>/run/**               raw benches, measures, VTC data
         <out>/characterization_summary.json

Binding rules (AGENTS.md 1.2 and the library README):

* Only the **configured** binding is accepted, and it must be the experimental
  ideal-FD bridge card version-limited to Xyce 7.10 / BSIMSOI 4.5.0.  The
  nominal stage-1 card is refused here: it is the ngspice characterization
  binding of ``characterize.py``, never a source for this evidence.
* Every cell is measured at exactly one nominal point (3.0 V, 25 C, 5 fF load,
  2 ns input slew) with the given integration method.  No interpolation is
  performed anywhere; a cell without complete physical evidence is refused.
* Sequential cells are recorded as explicit exclusions: the combinational
  sensitized-arc contract cannot express setup/hold or state timing.

The produced directory is self-checked with
``experimental_liberty.validate_evidence`` before this tool reports success, so
a run the renderer would reject fails here instead of silently publishing a
partial Liberty.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from . import bench_engine, characterize, experimental_liberty, logic, model_binding
except ImportError:  # direct invocation from libs.tech/stdcell
    import bench_engine  # type: ignore
    import characterize  # type: ignore
    import experimental_liberty  # type: ignore
    import logic  # type: ignore
    import model_binding  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs.ref" / "ICPS2023_5_gen_sc"
DEFAULT_MANIFEST = LIBRARY / "doc" / "cell_manifest.json"
SCHEMA = experimental_liberty.SCHEMA
LOAD_FF = 5.0
SLEW_NS = 2.0
INTEGRATION_METHOD = "gear"
EXPECTED_ENGINE = experimental_liberty.EXPECTED_ENGINE
EXPECTED_ENGINE_VERSION = experimental_liberty.EXPECTED_ENGINE_VERSION
EXPECTED_ENGINE_LEVEL = experimental_liberty.EXPECTED_ENGINE_LEVEL
PHYSICAL_CHECKS = ("strict_drc", "strict_lvs", "pex", "assembly")
SEQUENTIAL_REASON = (
    "Sequential setup/hold and state timing are not represented by this "
    "combinational arc contract.")
ARC_MEASUREMENT_FIELDS = ("rise_delay_ns", "fall_delay_ns",
                          "rise_transition_ns", "fall_transition_ns")


class XyceCharacterizationError(ValueError):
    """The requested evidence cannot be produced or would not be accepted."""


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise XyceCharacterizationError(f"{field} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise XyceCharacterizationError(f"{field} must be a finite positive number")
    return number


def _display_path(path: Path, root: Path | None = None) -> str:
    """Record in-repository artifacts relative to the repository root."""
    resolved = Path(path).resolve()
    base = (root or ROOT).resolve()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return str(resolved)


def evidence_binding(configured: Mapping[str, Any]) -> dict[str, Any]:
    """The six binding fields the renderer compares field-by-field."""
    if (configured.get("engine") != EXPECTED_ENGINE or
            configured.get("engine_version") != EXPECTED_ENGINE_VERSION or
            configured.get("engine_level") != EXPECTED_ENGINE_LEVEL):
        raise XyceCharacterizationError(
            "only the configured %s / %s binding can produce this evidence" %
            (EXPECTED_ENGINE_VERSION, EXPECTED_ENGINE))
    library = Path(configured["library_path"])
    include = Path(configured["include_path"])
    for path in (library, include):
        if not path.is_file():
            raise XyceCharacterizationError("configured model artifact is missing: %s" % path)
    sha256 = characterize.sha256

    return {
        "engine": configured["engine"],
        "engine_version": configured["engine_version"],
        "engine_level": configured["engine_level"],
        "section": configured.get("section", "b4soi"),
        "model_sha256": sha256(library),
        "model_include_sha256": sha256(include),
    }


def arc_record(arc: Mapping[str, Any]) -> dict[str, Any]:
    """Narrow one ``characterize.characterize_arcs`` record to the renderer contract."""
    if not isinstance(arc, Mapping):
        raise XyceCharacterizationError("arc must be an object")
    input_pin, output_pin = arc.get("input"), arc.get("output")
    if not isinstance(input_pin, str) or not input_pin:
        raise XyceCharacterizationError("arc input pin is missing")
    if not isinstance(output_pin, str) or not output_pin:
        raise XyceCharacterizationError("arc output pin is missing")
    condition = arc.get("condition", {})
    if not isinstance(condition, Mapping) or any(
            not isinstance(pin, str) or isinstance(bit, bool) or bit not in (0, 1)
            for pin, bit in condition.items()):
        raise XyceCharacterizationError("arc condition must map pin names to 0 or 1")
    sense = arc.get("sense")
    if sense not in ("positive_unate", "negative_unate"):
        raise XyceCharacterizationError("arc sense must be positive_unate or negative_unate")
    record = {
        "input": input_pin,
        "output": output_pin,
        "condition": {pin: int(bit) for pin, bit in condition.items()},
        "sense": sense,
        "load_ff": _positive(arc.get("load_ff"), f"{input_pin}->{output_pin} load"),
        "slew_ns": _positive(arc.get("input_slew_ns"), f"{input_pin}->{output_pin} slew"),
    }
    for field in ARC_MEASUREMENT_FIELDS:
        record[field] = _positive(arc.get(field), f"{input_pin}->{output_pin} {field}")
    return record


def ensure_physical_evidence(physical_root: str | Path, cell: str, *,
                             rebuild: bool = False) -> Path:
    """Produce (or refresh) one cell's physical evidence from the published views.

    ``verify.py`` owns the strict DRC/LVS/PEX/assembly sequence; this wrapper
    only decides *when* it has to run. Old reports without the SOI-edge and
    final-mask gates cannot be reused even when their GDS fingerprint matches.
    """
    import verify
    current_decks = verify.verification_deck_hashes()

    directory = Path(physical_root).resolve() / cell
    record_path = directory / "physical.json"
    gds = LIBRARY / "gds" / f"{cell}.gds"
    reference = LIBRARY / "spice" / "physical" / f"{cell}.spice"
    for path in (gds, reference):
        if not path.is_file():
            raise XyceCharacterizationError(
                f"{cell}: published view is missing: {path.relative_to(ROOT)}")
    if not rebuild and record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = None
        if (isinstance(record, dict) and record.get("passed") is True and
                record.get("gds_sha256") == characterize.sha256(gds) and
                record.get("reference_sha256") == characterize.sha256(reference) and
                record.get("deck_sha256") == current_decks and
                record.get("soi_edge_protection", {}).get("passed") is True and
                record.get("fabrication", {}).get("passed") is True):
            return directory
    try:
        verify.verify_physical(gds, reference, cell, directory)
    except verify.VerificationError as error:
        raise XyceCharacterizationError(f"{cell}: physical evidence failed: {error}") from error
    return directory


def physical_evidence(physical_root: str | Path, cell: str,
                      root: Path | None = None) -> dict[str, Any]:
    """Load and gate the per-cell physical evidence this run depends on."""
    directory = Path(physical_root) / cell
    record_path = directory / "physical.json"
    netlist = directory / "postlayout.spice"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise XyceCharacterizationError(f"{cell}: cannot read {record_path}: {error}") from error
    if not isinstance(record, dict):
        raise XyceCharacterizationError(f"{cell}: physical evidence must be an object")
    failed = [check for check in PHYSICAL_CHECKS if record.get(check) is not True]
    if failed:
        raise XyceCharacterizationError(
            f"{cell}: physical evidence did not pass {', '.join(failed)}")
    for key in ("soi_edge_protection", "fabrication"):
        if not isinstance(record.get(key), dict) or record[key].get("passed") is not True:
            raise XyceCharacterizationError(f"{cell}: physical evidence did not pass {key}")
    if not netlist.is_file():
        raise XyceCharacterizationError(f"{cell}: assembled netlist is missing: {netlist}")
    return {
        "directory": directory,
        "record": record,
        "netlist": netlist,
        "provenance": {
            "physical_evidence": _display_path(record_path, root),
            "postlayout_spice": _display_path(netlist, root),
        },
    }


def cell_record(cell: str, *, binding: Mapping[str, Any], integration_method: str,
                arcs: Sequence[Mapping[str, Any]], provenance: Mapping[str, Any],
                loads_ff: Sequence[float] = (LOAD_FF,),
                slews_ns: Sequence[float] = (SLEW_NS,),
                sequential: bool = False) -> dict[str, Any]:
    """Assemble one renderer-validatable evidence record."""
    if integration_method not in ("trap", "gear"):
        raise XyceCharacterizationError("integration_method must be trap or gear")
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "cell": cell,
        "binding": dict(binding),
        "conditions": {
            "mode": "nominal",
            "voltage_v": characterize.VDD,
            "temperature_c": 25.0,
            "integration_method": integration_method,
        },
        "scope": {
            "loads_ff": sorted({_positive(load, "load") for load in loads_ff}),
            "slews_ns": sorted({_positive(slew, "slew") for slew in slews_ns}),
        },
        "provenance": dict(provenance),
        "arcs": [arc_record(arc) for arc in arcs],
    }
    if sequential:
        if record["arcs"]:
            raise XyceCharacterizationError(f"{cell}: sequential cells must carry no arcs")
        record["sequential_excluded"] = True
        record["provenance"].setdefault("reason", SEQUENTIAL_REASON)
    return record


def characterize_cells(manifest_path: str | Path, physical_root: str | Path,
                       output_dir: str | Path, *,
                       config_path: str | Path | None = None,
                       cells: Sequence[str] | None = None,
                       integration_method: str = INTEGRATION_METHOD,
                       timeout: float = 900.0,
                       rebuild_physical: bool = False,
                       clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Measure every selected accepted cell and write the evidence directory."""
    manifest, records = experimental_liberty._load_manifest(manifest_path)
    del manifest
    selected = sorted(records) if not cells else sorted(set(cells))
    unknown = sorted(set(selected) - set(records))
    if unknown:
        raise XyceCharacterizationError(
            "not accepted cells of this manifest: %s" % ", ".join(unknown))
    binding = model_binding.load(config_path)
    evidence_binding_record = evidence_binding(binding)
    output = Path(output_dir)
    started = clock()
    results: list[dict[str, Any]] = []
    bench: Any = None
    for cell in selected:
        spec = records[cell]["spec"]
        ensure_physical_evidence(physical_root, cell, rebuild=rebuild_physical)
        physical = physical_evidence(physical_root, cell)
        cell_dir = output / cell
        cell_dir.mkdir(parents=True, exist_ok=True)
        if spec.get("sequential"):
            # The ported card is still materialised so a reader can see which
            # model the exclusion was recorded against.
            bench = bench_engine.bench_for(binding=binding, work=cell_dir / "bench",
                                           method=integration_method, timeout=timeout)
            record = cell_record(cell, binding=evidence_binding_record,
                                 integration_method=integration_method, arcs=[],
                                 provenance=physical["provenance"], sequential=True)
            status = "excluded_sequential"
        else:
            if not bench_engine.available(binding["engine"]):
                raise XyceCharacterizationError(
                    "%s is required for this evidence and is not available" % binding["engine"])
            bench = bench_engine.bench_for(binding=binding, work=cell_dir / "bench",
                                           method=integration_method, timeout=timeout)
            measured = characterize.characterize_cell(
                spec, physical["netlist"], cell_dir / "run", logic.evaluate, bench=bench)
            record = cell_record(cell, binding=evidence_binding_record,
                                 integration_method=integration_method,
                                 arcs=measured["arcs"], provenance=physical["provenance"])
            status = "measured"
            record["provenance"]["characterization_json"] = _display_path(
                cell_dir / "run" / "characterization.json")
        (cell_dir / f"{cell}.json").write_text(json.dumps(record, indent=2) + "\n",
                                               encoding="utf-8")
        worst = max((max(arc["rise_delay_ns"], arc["fall_delay_ns"])
                     for arc in record["arcs"]), default=None)
        results.append({"cell": cell, "status": status, "arc_count": len(record["arcs"]),
                        "worst_delay_ns": worst, "conditions": record["conditions"]})
        print("%-16s %-19s arcs=%3d worst=%s" % (
            cell, status, len(record["arcs"]),
            "n/a" if worst is None else "%.6g ns" % worst), flush=True)
    summary = {
        "schema": SCHEMA,
        "binding": evidence_binding_record,
        "conditions": {"mode": "nominal", "voltage_v": characterize.VDD,
                       "temperature_c": 25.0, "integration_method": integration_method},
        "scope": {"loads_ff": [LOAD_FF], "slews_ns": [SLEW_NS]},
        "elapsed_s": round(clock() - started, 3),
        "cells_selected": selected,
        "results": results,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "characterization_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def validate(output_dir: str | Path, manifest_path: str | Path = DEFAULT_MANIFEST,
             *, config_path: str | Path | None = None) -> dict[str, Any]:
    """Fail closed: the renderer must accept the produced directory."""
    return experimental_liberty.validate_evidence(manifest_path, output_dir,
                                                  config_path=config_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce experimental Xyce single-corner standard-cell evidence")
    parser.add_argument("physical_root", type=Path,
                        help="directory of per-cell physical evidence from verify.py")
    parser.add_argument("output_dir", type=Path, help="evidence directory to (re)write")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--config", type=Path, default=None,
                        help="model-binding config; defaults to libs.tech/stdcell/config.json")
    parser.add_argument("--cell", action="append", default=[], metavar="CELL",
                        help="limit the run to this accepted cell (repeatable)")
    parser.add_argument("--integration-method", choices=("gear", "trap"),
                        default=INTEGRATION_METHOD)
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="per-simulation timeout in seconds")
    parser.add_argument("--skip-validation", action="store_true",
                        help="skip the renderer self-check (partial runs only)")
    parser.add_argument("--rebuild-physical", action="store_true",
                        help="re-run strict DRC/LVS/PEX/assembly instead of reusing matching evidence")
    arguments = parser.parse_args(argv)
    try:
        summary = characterize_cells(
            arguments.manifest, arguments.physical_root, arguments.output_dir,
            config_path=arguments.config, cells=arguments.cell,
            integration_method=arguments.integration_method, timeout=arguments.timeout,
            rebuild_physical=arguments.rebuild_physical)
        if not arguments.skip_validation:
            validate(arguments.output_dir, arguments.manifest, config_path=arguments.config)
            print("verified: the renderer accepts %s" % Path(arguments.output_dir))
    except (XyceCharacterizationError, experimental_liberty.ExperimentalLibertyError,
            bench_engine.BenchError, characterize.CharacterizationError,
            model_binding.ModelBindingError, OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    del summary
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
