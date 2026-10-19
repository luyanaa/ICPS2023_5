#!/usr/bin/env python3
"""Validate explicit frame-pad to net assignments.

Pad labels in the frame libraries are physical identifiers only.  This module
accepts a caller-supplied mapping and never derives a net from a label,
pad position, or pad order.  A LibreLane/actual-flow caller should pass
``actual_flow=True`` so that an absent mapping fails closed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any


class PadMappingError(ValueError):
    """An explicit pad/net mapping is absent or does not meet its contract."""


_PAD_ID = re.compile(r"PAD_[0-9]{2}\Z")
_PINS_HEADER = re.compile(r"^\s*PINS\s+(\d+)\s*;\s*$", re.MULTILINE)
_PIN_ENTRY = re.compile(r"^\s*-\s+(\S+)\s+\+\s+NET\s+(\S+)\b", re.MULTILINE)


def _read_mapping(source: Any) -> Mapping[Any, Any] | None:
    """Return mapping data from a dict-like object, JSON text, or JSON file."""
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source
    if isinstance(source, Path):
        source_name = str(source)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PadMappingError(
                f"cannot read pad/net mapping JSON {source_name!r}: {error}") from error
    elif isinstance(source, str):
        text = source.strip()
        if not text:
            raise PadMappingError("pad/net mapping JSON must be a non-empty object")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            path = Path(source)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise PadMappingError(
                    "pad/net mapping must be a dict, JSON object, or JSON file path"
                ) from error
    else:
        raise PadMappingError(
            "pad/net mapping must be a dict, JSON object, or JSON file path"
        )
    if not isinstance(value, Mapping):
        raise PadMappingError("pad/net mapping JSON must contain an object")
    return value


def _def_pad_ids(def_path: str | Path) -> set[str]:
    path = Path(def_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PadMappingError(f"cannot read pad contract DEF {str(path)!r}: {error}") from error

    header = _PINS_HEADER.search(text)
    if header is None:
        raise PadMappingError(
            f"pad contract DEF {str(path)!r} has no PINS section; expected a 28- or 56-pad template"
        )
    pins_start = header.end()
    pins_end_match = re.search(r"^\s*END PINS\s*$", text[pins_start:], re.MULTILINE)
    if pins_end_match is None:
        raise PadMappingError(f"pad contract DEF {str(path)!r} has an unterminated PINS section")
    pins_text = text[pins_start:pins_start + pins_end_match.start()]
    declared = int(header.group(1))
    entries = _PIN_ENTRY.findall(pins_text)
    identifiers = [pin_id for pin_id, _net in entries]
    if declared not in (28, 56):
        raise PadMappingError(
            f"pad contract DEF {str(path)!r} declares {declared} pins; expected the 28-pad 1x1 or 56-pad 2x2 contract"
        )
    if len(identifiers) != declared or len(set(identifiers)) != declared:
        raise PadMappingError(
            f"pad contract DEF {str(path)!r} declares {declared} pins but its PINS entries are incomplete or duplicated"
        )
    expected = {f"PAD_{index:02d}" for index in range(declared)}
    actual = set(identifiers)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        extra = ", ".join(sorted(actual - expected)) or "none"
        raise PadMappingError(
            f"pad contract DEF {str(path)!r} must use PAD_00..PAD_{declared - 1:02d}; "
            f"missing: {missing}; unexpected: {extra}"
        )
    return actual


def validate_pad_mapping(
    mapping: Mapping[Any, Any] | str | Path | None,
    *,
    def_path: str | Path | None = None,
    actual_flow: bool = False,
) -> dict[str, str] | None:
    """Validate and return a deterministic explicit pad-to-net mapping.

    ``mapping`` may be a mapping, JSON object text, or path to a JSON object.
    ``None`` is retained as an optional/no-mapping result for geometry-only
    callers, but is rejected when ``actual_flow=True``.  A supplied ``def_path``
    must be a canonical 28-pad (1x1) or 56-pad (2x2) DEF template; mapping IDs
    are checked against that template.  No pad labels, positions, or ordering
    are consulted to infer net names.
    """
    data = _read_mapping(mapping)
    if data is None:
        if actual_flow:
            raise PadMappingError(
                "explicit pad/net mapping is required for an actual flow; "
                "pass a PAD_XX-to-net mapping (and optionally def_path)"
            )
        return None
    if not data:
        raise PadMappingError(
            "pad/net mapping must not be empty; provide at least one explicit PAD_XX-to-net assignment"
        )

    invalid_ids = sorted(
        (repr(pad_id) for pad_id in data
         if not isinstance(pad_id, str) or not pad_id or _PAD_ID.fullmatch(pad_id) is None
         or int(pad_id[4:]) > 55)
    )
    if invalid_ids:
        raise PadMappingError(
            "pad/net mapping contains invalid/empty pad ID(s): " + ", ".join(invalid_ids) +
            "; expected identifiers such as PAD_00"
        )

    invalid_nets = sorted(
        repr(pad_id) for pad_id, net_name in data.items()
        if not isinstance(net_name, str) or not net_name or net_name != net_name.strip()
    )
    if invalid_nets:
        raise PadMappingError(
            "pad/net mapping has empty or non-string net name(s) for pad ID(s): " +
            ", ".join(invalid_nets) + "; each net name must be a nonempty string"
        )

    allowed_ids = _def_pad_ids(def_path) if def_path is not None else None
    if allowed_ids is not None:
        unknown = sorted(set(data) - allowed_ids)
        if unknown:
            expected = f"PAD_00..PAD_{len(allowed_ids) - 1:02d}"
            raise PadMappingError(
                "pad/net mapping references unknown pad ID(s): " + ", ".join(unknown) +
                f"; supplied DEF contract permits {expected}"
            )

    return {pad_id: data[pad_id] for pad_id in sorted(data)}
