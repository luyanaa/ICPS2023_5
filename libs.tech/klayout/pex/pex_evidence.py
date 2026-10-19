"""Evidence contract for the experimental KLayout PEX parasitic-SPICE path.

This module records a concrete extraction artifact and its provenance.  The
artifact is parasitics-only SPICE; it is deliberately not a SPEF/OpenRCX
closure result and therefore always reports both timing capability flags as
false.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[3]
PROFILE_PATH = ROOT / "libs.tech/klayout/process_profiles.json"
PEX_TOOL_RELATIVE = "libs.tech/klayout/pex/icps2023_5_pex.py"


class PexEvidenceError(ValueError):
    """The PEX artifact or its provenance violates the evidence contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PexEvidenceError("malformed PEX provenance: %s must be a non-empty string" % name)
    return value.strip()


def _profiles() -> dict:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        profiles = data["profiles"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PexEvidenceError("cannot read KLayout process profiles") from error
    if not isinstance(profiles, dict):
        raise PexEvidenceError("malformed KLayout process profiles")
    return profiles


def _normalise_layers(layers: Iterable[str], mode: str, profile: dict) -> list[str]:
    if isinstance(layers, (str, bytes)):
        raise PexEvidenceError("malformed PEX provenance: upper_metal_layers must be a sequence")
    try:
        values = list(layers)
    except TypeError as error:
        raise PexEvidenceError("malformed PEX provenance: upper_metal_layers must be a sequence") from error
    if any(not isinstance(layer, str) or not layer.strip() for layer in values):
        raise PexEvidenceError("malformed PEX provenance: upper_metal_layers contains a non-string")
    values = sorted(set(layer.strip() for layer in values))
    try:
        configured = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))["upper_metal_layers"][mode]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PexEvidenceError("malformed KLayout upper-metal profile data") from error
    known = set(configured)
    unknown = sorted(set(values) - known)
    if unknown:
        raise PexEvidenceError("unsupported upper-metal layers: %s" % ", ".join(unknown))
    permitted = profile.get("permitted_upper_layers")
    if not isinstance(permitted, list) or any(not isinstance(layer, str) for layer in permitted):
        raise PexEvidenceError("malformed process profile permitted_upper_layers")
    mismatch = sorted(set(values) - set(permitted))
    if mismatch:
        raise PexEvidenceError(
            "upper-metal/profile mismatch: %s is not permitted by the selected profile"
            % ", ".join(mismatch)
        )
    return values


def _artifact_metadata(text: str) -> dict[str, object]:
    """Parse the metadata emitted by ``icps2023_5_pex.py``."""
    if "* ICPS2023_5 (minimal Fab SOI-CMOS) parasitics" not in text:
        raise PexEvidenceError("PEX artifact is missing the ICPS2023_5 parasitic-SPICE marker")
    layout_match = re.search(r"^\* layout: (\S+)  top cell: (\S+)  layer_mode: (\S+)$", text, re.MULTILINE)
    profile_match = re.search(r"^\* process profile: (\S+)$", text, re.MULTILINE)
    tool_match = re.search(r"^\* extracted by (\S+)$", text, re.MULTILINE)
    upper_match = re.search(r"^\* upper-metal layers: (\[.*\])$", text, re.MULTILINE)
    if not all((layout_match, profile_match, tool_match, upper_match)):
        raise PexEvidenceError("PEX artifact has incomplete or malformed provenance metadata")
    try:
        upper_layers = json.loads(upper_match.group(1))
    except json.JSONDecodeError as error:
        raise PexEvidenceError("PEX artifact has malformed upper-metal provenance") from error
    if not isinstance(upper_layers, list):
        raise PexEvidenceError("PEX artifact upper-metal provenance must be a list")
    return {
        "layout": layout_match.group(1),
        "top": layout_match.group(2),
        "layer_mode": layout_match.group(3),
        "process_profile": profile_match.group(1),
        "tool": tool_match.group(1),
        "upper_metal_layers": upper_layers,
    }


def collect_pex_evidence(
    artifact_path: str | Path,
    layout_path: str | Path,
    process_profile: str,
    tool_path: str | Path,
    *,
    layer_mode: str = "design",
    upper_metal_layers: Iterable[str] = (),
) -> dict[str, object]:
    """Validate a PEX artifact and return immutable provenance evidence.

    ``upper_metal_layers`` is the caller's layout observation.  The same list
    is required in the artifact metadata, preventing an evidence record from
    silently pairing an artifact with a different profile/layout observation.
    """
    artifact = Path(artifact_path).resolve()
    layout = Path(layout_path).resolve()
    tool = Path(tool_path).resolve()
    profile_name = _required_text(process_profile, "process_profile")
    mode = _required_text(layer_mode, "layer_mode")
    if mode not in ("design", "fab"):
        raise PexEvidenceError("unsupported PEX layer_mode: %s" % mode)
    profiles = _profiles()
    if profile_name not in profiles or not isinstance(profiles[profile_name], dict):
        raise PexEvidenceError("unknown or malformed PEX process profile: %s" % profile_name)
    profile = profiles[profile_name]
    observed_layers = _normalise_layers(upper_metal_layers, mode, profile)
    for path, name in ((artifact, "artifact"), (layout, "layout"), (tool, "tool")):
        if not path.is_file():
            raise PexEvidenceError("missing PEX %s: %s" % (name, path))
    try:
        text = artifact.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PexEvidenceError("cannot read PEX artifact: %s" % artifact) from error
    metadata = _artifact_metadata(text)
    if metadata["process_profile"] != profile_name:
        raise PexEvidenceError("PEX artifact/profile provenance mismatch")
    if metadata["layer_mode"] != mode:
        raise PexEvidenceError("PEX artifact/layer-mode provenance mismatch")
    artifact_layout = Path(str(metadata["layout"])).resolve()
    if artifact_layout != layout:
        raise PexEvidenceError("PEX artifact/layout provenance mismatch")
    expected_tool = PEX_TOOL_RELATIVE
    if metadata["tool"] != expected_tool:
        raise PexEvidenceError("PEX artifact/tool provenance mismatch")
    try:
        tool_relative = tool.relative_to(ROOT).as_posix()
    except ValueError as error:
        raise PexEvidenceError("unexpected PEX tool path: %s" % tool) from error
    if tool_relative != expected_tool:
        raise PexEvidenceError("unexpected PEX tool path: %s" % tool)
    return {
        "artifact_path": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "source_layout": str(layout),
        "source_layout_sha256": _sha256(layout),
        "process_profile": profile_name,
        "layer_mode": mode,
        "upper_metal_layers": observed_layers,
        "tool": str(tool),
        "tool_sha256": _sha256(tool),
        "parasitics_only_spice": True,
        "timing_spef_available": False,
        "openrcx_qualified": False,
    }


# Descriptive alias for callers that want to make validation explicit.
validate_pex_artifact = collect_pex_evidence
