#!/usr/bin/env python3
"""Shared inputs and strict ngspice execution for the depletion-mode audit.

Generated cards are SOIMOD-only sensitivities, not migrated/calibrated FD models.
Neither the frozen fit nor the charge-only overlap candidate is edited here.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import regress_b4soi_stage1_iv as iv

ROOT = Path(__file__).resolve().parents[3]
FROZEN = iv.MODEL_CARD
OVERLAP = ROOT / "libs.tech/ngspice/models/b4soi_overlap_candidate.inc"
BASES = {"frozen": FROZEN, "overlap": OVERLAP}
MODES = (0, 1, 2)
MODE_NAMES = {0: "PD reference", 1: "unified sensitivity", 2: "ideal-FD sensitivity"}
TERMINALS = ("g", "d", "s", "e")
W_UM, L_UM, VDD = 24.0, 6.0, 3.0
# Inventory names, not assertions that a simulator exposes/calibrates every field.
FD_INPUTS = (
    "soimod", "fdmod", "nch", "npeak", "nsub", "ngate", "vth0", "k1", "k2",
    "tox", "toxm", "tsi", "tbox", "xj", "vbsa", "nofffd", "vofffd", "k1b",
    "k2b", "dk2b", "dvbd0", "dvbd1", "moinfd", "vbs0pd", "vbs0fd", "vfb",
    "vfbb", "vfbzb", "vsce", "cdsbs", "lpe0",
)
ERROR_RE = re.compile(
    r"(?im)(?:\bfatal\b|\berror\b|unrecogni[sz]ed|unknown parameter|"
    r"timestep too small|simulation(?:s)? aborted|no such vector|"
    r"can't open|cannot open|no circuit loaded|singular matrix|"
    r"doanalyses:.*failed|not available|no matching instances or models)"
)


def fmt(value: float) -> str:
    return "%.15g" % value


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): sha256(path)
            for path in (FROZEN, OVERLAP, iv.BASELINE, iv.MEASUREMENTS)}


def save_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                      encoding="utf-8")


def generate_candidates(out: str | Path, *, mixed: bool = False) -> list[dict]:
    """Write self-contained .lib b4soi decks; only SOIMOD differs from each base.

    Mixed N/P modes are numerical experiments, not an assertion that either
    polarity's depletion behavior is known. The original model names are retained.
    """
    baseline = json.loads(iv.BASELINE.read_text(encoding="utf-8"))
    iv.verify_frozen_inputs(baseline, FROZEN, iv.MEASUREMENTS)
    frozen = iv.model_parameters(FROZEN)
    overlap = iv.model_parameters(OVERLAP)
    for model, cov in (("nch", 5e-9), ("pch", 6.8e-9)):
        expected = {**frozen[model], "CGSO": cov, "CGDO": cov}
        if overlap.get(model) != expected:
            raise ValueError("overlap base changed outside its charge-only contract: " + model)
    target = Path(out).resolve()
    target.mkdir(parents=True, exist_ok=True)
    records = []
    pairs = [(n, p) for n in MODES for p in MODES if mixed or n == p]
    for base, source in BASES.items():
        text = source.read_text(encoding="utf-8")
        blocks = {match.group(1).casefold(): match.group(0)
                  for match in iv.MODEL_RE.finditer(text)}
        if set(blocks) != {"nch", "pch"}:
            raise ValueError("unexpected candidate model set: " + base)
        for nmode, pmode in pairs:
            name = "%s_n%d_p%d" % (base, nmode, pmode)
            models = []
            deltas = {}
            for model, mode in (("nch", nmode), ("pch", pmode)):
                block, count = re.subn(r"\bSOIMOD\s*=\s*0\b", "SOIMOD=%d" % mode,
                                       blocks[model], flags=re.IGNORECASE)
                if count != 1:
                    raise ValueError("expected one SOIMOD=0 in " + model)
                models.append(block)
                deltas[model] = {} if mode == 0 else {"SOIMOD": {"from": 0, "to": mode}}
            path = target / (name + ".lib.spice")
            path.write_text(
                "* ICPS2023_5 depletion audit: unmigrated SOIMOD sensitivity.\n"
                "* Base: %s; no fitting, body ties, or default overrides.\n"
                "* N/P channel doping and back-gate/FD parameters are uncalibrated.\n"
                "* Explicit selection only; not included by the nominal library.\n"
                ".lib b4soi\n%s\n.endl b4soi\n"
                % (source.relative_to(ROOT), "\n".join(models)), encoding="utf-8")
            records.append({"id": name, "base": base, "nmode": nmode, "pmode": pmode,
                            "library": str(path), "sha256": sha256(path),
                            "deltas_from_base": deltas, "qualified": False})
    save_json(target / "manifest.json", {"sources": source_hashes(), "candidates": records,
              "migration": "SOIMOD-only sensitivity; FD inputs inherited, not calibrated",
              "fd_input_inventory": FD_INPUTS,
              "derived_unknowns": ["back-gate flatband VFBb and work-function assumptions",
                                   "FDMOD=1 CDSBS/VSCE formulation (not enabled)"]})
    return records


def header(library: str | Path, title: str, *, method: str = "trap") -> list[str]:
    lib = Path(library).resolve().as_posix().replace('"', '\\"')
    return ["* " + title, '.lib "%s" b4soi' % lib,
            ".options temp=25 tnom=25 method=%s reltol=1e-4 abstol=1e-12 itl4=2000" % method]


def control(commands: list[str]) -> list[str]:
    return [".control", "set wr_singlescale", "set numdgt=15", *commands,
            "quit", ".endc", ".end", ""]


def read_data(path: Path, vectors: int, *, complex_data: bool = False) -> tuple[list, list]:
    """Reject malformed/nonfinite numeric output rather than accepting a partial run."""
    width = 1 + vectors * (2 if complex_data else 1)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = [float(field.replace("D", "E").replace("d", "e")) for field in line.split()]
        if len(values) != width or not all(math.isfinite(value) for value in values):
            raise ValueError("malformed or non-finite wrdata row in " + str(path))
        rows.append(values)
    if not rows:
        raise ValueError("missing numeric wrdata rows in " + str(path))
    axis = [row[0] for row in rows]
    if complex_data:
        columns = [[complex(row[1 + 2 * k], row[2 + 2 * k]) for row in rows]
                   for k in range(vectors)]
    else:
        columns = [[row[1 + k] for row in rows] for k in range(vectors)]
    return axis, columns


def run_deck(ngspice: str, work: str | Path, name: str, lines: list[str], *,
             vectors: int, stop: float | None = None, complex_data: bool = False,
             timeout: float = 60.0) -> dict:
    """Run once, retaining deck/log/data and failures, with no convergence fallback.

    Caller writes `wrdata NAME.dat ...`. A partial data file or ngspice's zero
    exit status after an aborted analysis never qualifies as an observed result.
    Axis/columns are returned separately from the serializable `evidence` record.
    """
    folder = Path(work).resolve() / name
    folder.mkdir(parents=True, exist_ok=True)
    deck, data, log = (folder / (name + suffix) for suffix in (".cir", ".dat", ".log"))
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data.unlink(missing_ok=True)
    evidence = {"status": "failed", "deck": str(deck), "log": str(log),
                "data": str(data), "deck_sha256": sha256(deck), "timeout_s": timeout}
    try:
        result = subprocess.run([ngspice, "-b", str(deck)], cwd=folder, text=True,
                                errors="replace", capture_output=True, timeout=timeout, check=False)
        text = result.stdout + "\n" + result.stderr
        evidence["returncode"] = result.returncode
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        text = decode(error.stdout) + "\n" + decode(error.stderr)
        log.write_text(text, encoding="utf-8")
        evidence.update(status="inconclusive", reason="simulator timeout")
        return {"evidence": evidence, "axis": [], "vectors": []}
    log.write_text(text, encoding="utf-8")
    evidence["warnings"] = [line.strip() for line in text.splitlines() if "warning" in line.lower()]
    problems = [line.strip() for line in text.splitlines() if ERROR_RE.search(line)]
    if result.returncode != 0 or problems:
        evidence["reason"] = "; ".join(problems) or "nonzero simulator exit status"
        return {"evidence": evidence, "axis": [], "vectors": []}
    try:
        axis, columns = read_data(data, vectors, complex_data=complex_data)
        if stop is not None:
            tolerance = max(1e-15, abs(stop - axis[0]) * 1e-6)
            evidence["endpoint_tolerance_axis_units"] = tolerance
            if len(axis) < 2 or abs(axis[-1] - stop) > tolerance:
                raise ValueError("analysis truncated before requested endpoint")
            if any(b <= a for a, b in zip(axis, axis[1:])):
                raise ValueError("analysis axis is not strictly increasing")
    except (OSError, ValueError) as error:
        evidence["reason"] = str(error)
        return {"evidence": evidence, "axis": [], "vectors": []}
    evidence.update(status="observed", rows=len(axis), first_axis=axis[0], last_axis=axis[-1])
    return {"evidence": evidence, "axis": axis, "vectors": columns}


def simulator_version(ngspice: str) -> str:
    result = subprocess.run([ngspice, "-v"], capture_output=True, text=True, check=True)
    return result.stdout.strip()
