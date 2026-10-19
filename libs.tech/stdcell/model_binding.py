#!/usr/bin/env python3
"""Single source of truth for the standard-cell library's SPICE model binding.

Two bindings exist in this repository and they must not be mixed silently:

* ``NOMINAL`` -- the frozen stage-1 PDK card (ngspice BSIMSOI 4.4, LEVEL=58,
  SOIMOD=0).  It remains the nominal PDK model (AGENTS.md 1.2) and is the model
  the ``.control``-based characterization benches in this directory execute.
* ``load()`` -- the model recorded in ``config.json``'s ``model`` block.  It is
  the binding used by the *library evidence* path
  (``library._current_provenance``, ``verify.verify_physical``,
  ``search_area``): the experimental ideal-FD bridge card, which is
  version-limited to Xyce 7.10 / BSIMSOI 4.5.0 (W24/L6, 3 V, 25 °C, 4-terminal
  D/G/S/E).  ngspice BSIMSOI 4.4 must not accept or be mixed with that card
  (documented 5-11 % cross-version divergence).

The module therefore never returns a card for the *other* engine: an Xyce
binding carries the port instructions (LEVEL token, comment form) that
``libs.tech/ngspice/tools/xyce_b4soi.py`` applies.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.json")

NOMINAL = {
    "library": "libs.tech/ngspice/icps2023_5.lib.spice",
    "section": "b4soi",
    "include": "libs.tech/ngspice/models/icps2023_5_b4soi.inc",
    "engine": "ngspice",
    "engine_path": None,
    "engine_version": "ngspice BSIMSOI 4.4 (LEVEL=58)",
    "engine_level": 58,
    "scope": "stage-1 nominal PDK card; nominal model of this repository",
    "provenance": "frozen stage-1 reference retained by the compatibility selector",
}


class ModelBindingError(ValueError):
    """The configured model binding is missing, malformed, or unresolvable."""


def _repository_path(relative: Any, field: str, root: Path) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ModelBindingError(f"model.{field} must be a nonempty repository path")
    path = Path(relative)
    if path.is_absolute():
        raise ModelBindingError(
            f"model.{field} must be repository-relative for portability, got {relative!r}")
    resolved = (root / path).resolve()
    if not resolved.is_file():
        raise ModelBindingError(f"model.{field} is missing: {relative}")
    return resolved


def load(config_path: str | Path | None = None, *,
         root: str | Path | None = None) -> dict[str, Any]:
    """Resolve the model binding declared by a config (NOMINAL when absent)."""
    root_path = ROOT if root is None else Path(root).resolve()
    path = DEFAULT_CONFIG if config_path is None else Path(config_path).resolve()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelBindingError(f"cannot read config {path}: {error}") from error
    if not isinstance(config, dict):
        raise ModelBindingError("config must be a JSON object")
    block = config.get("model", NOMINAL)
    if not isinstance(block, dict):
        raise ModelBindingError("config model must be an object")
    engine = block.get("engine", NOMINAL["engine"])
    if engine not in ("ngspice", "xyce"):
        raise ModelBindingError(f"model.engine must be 'ngspice' or 'xyce', got {engine!r}")
    level = block.get("engine_level", NOMINAL["engine_level"])
    if isinstance(level, bool) or not isinstance(level, int) or level < 1:
        raise ModelBindingError("model.engine_level must be a positive integer")
    library = _repository_path(block.get("library"), "library", root_path)
    include = _repository_path(block.get("include"), "include", root_path)
    engine_path = block.get("engine_path")
    if engine == "xyce" and not isinstance(engine_path, str):
        raise ModelBindingError("model.engine_path is required for the Xyce engine")
    section = block.get("section", "b4soi")
    if not isinstance(section, str) or not section:
        raise ModelBindingError("model.section must be a nonempty SPICE library section")
    return {
        "config": str(path),
        "library": block.get("library"),
        "library_path": library,
        "include": block.get("include"),
        "include_path": include,
        "section": section,
        "engine": engine,
        "engine_path": engine_path,
        "engine_version": block.get("engine_version"),
        "engine_level": level,
        "scope": block.get("scope", ""),
        "provenance": block.get("provenance", ""),
    }


def nominal(root: str | Path | None = None) -> dict[str, Any]:
    """Resolve the frozen nominal stage-1 binding without reading a config."""
    root_path = ROOT if root is None else Path(root).resolve()
    return {"config": None,
            "library": NOMINAL["library"],
            "library_path": _repository_path(NOMINAL["library"], "library", root_path),
            "include": NOMINAL["include"],
            "include_path": _repository_path(NOMINAL["include"], "include", root_path),
            **{key: NOMINAL[key] for key in
               ("section", "engine", "engine_path", "engine_version",
                "engine_level", "scope", "provenance")}}


def provenance_files(config_path: str | Path | None = None) -> dict[str, Path]:
    """The two files the library provenance hashes for the configured binding."""
    binding = load(config_path)
    return {"model_sha256": binding["library_path"],
            "model_include_sha256": binding["include_path"]}


def engine_record(config_path: str | Path | None = None) -> dict[str, Any]:
    """Self-describing engine record stored in the publication provenance."""
    binding = load(config_path)
    return {
        "engine": binding["engine"],
        "engine_version": binding["engine_version"],
        "engine_level": binding["engine_level"],
        "scope": binding["scope"],
        "library": binding["library"],
        "include": binding["include"],
    }
