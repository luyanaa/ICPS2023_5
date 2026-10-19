#!/usr/bin/env python3
"""Bounded refit of the ICPS2023_5 BSIMSOI "bridge" candidate on digitized PDK data.

Purpose
-------
The stage-1 cards (`models/icps2023_5_b4soi.inc`) fit only VTH0/U0/NFACTOR/K1/
RDSW and leave every strong-field parameter at its ngspice default.  Their PMOS
transfer residual (0.74/0.88 decades of log10|ID|) is absorbed by a non-physical
series resistance (RDSW=46250 ohm*um, ~2 kOhm) and both cards still inherit the
bulk-BSIM3 residue NCH=1.7e17 cm^-3.

This tool builds the AGENTS.md section 4.1 bridge card:

* NCH = 1.0e15 cm^-3 with explicit NGATE=0, so W_d,max >> TSI=95 nm and ngspice
  performs no band-bending NCH reset;
* SOIMOD=2 (ideal-FD), chosen for self-consistency with this card's own premise
  rather than as a claim about the measured device. A fully depleted film
  (W_d,max = 863 nm >> TSI = 95 nm) contradicts the SOIMOD=0 partially-depleted
  premise, and that mismatch is a numerical pathology: the same 120 ns T7 switch
  takes >1e7 steps (>80 MB of output, no completion inside 60 s) at SOIMOD=0
  versus 6.1e3 steps at SOIMOD=2. Unified mode 1 is not a safe substitute - it is
  stable for some parameter sets and stiff for others. Mode 2 and mode 1 give
  identical DC here. The real device's depletion mode stays undetermined;
* K1 derived in closed form from NCH, not fitted;
* strong-field priors inherited from the same-process-line bulk-BSIM3 card
  (`models/legacy_MF_CMOS20240109_bulk3.inc`): UA/UB/UC/VSAT/A0/AGS/KETA/
  PCLM/PDIBLC*;
* PMOS RDSW bounded at the physical access-resistance range (<= 200 ohm*um);
* NFACTOR anchored to the PDK p.7 measured subthreshold slope (71/65 mV/dec);
* a bounded re-optimisation of the remaining parameters on the 459 digitized
  PDK samples.

Parameter provenance is data, not commentary: every parameter carries a `kind`
(`fixed`, `derived`, `prior`, `fitted`) and a `source` string that is emitted
into the generated card and the fit report.  Only `fitted` parameters are moved
by the optimiser, and each carries explicit physical bounds.

NMOS and PMOS are fitted independently because the digitized curves are
independent per polarity.

Usage
-----
  python3 libs.tech/ngspice/tools/fit_b4soi_bridge.py --write-card build/b4soi-bridge/v1.inc
  python3 libs.tech/ngspice/tools/fit_b4soi_bridge.py --evaluate <card.inc>
  python3 libs.tech/ngspice/tools/fit_b4soi_bridge.py --fit
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "libs.tech/ngspice/models"
LEGACY_BULK = MODEL_DIR / "legacy_MF_CMOS20240109_bulk3.inc"
# Two experimental variants are kept.  The ideal-FD card is the more stable
# numerical backend, but "more stable" is NOT evidence that the real device is
# fully depleted, so the unified-PD/FD card is retained alongside it rather than
# deleted.  See audit/b4soi_*_bridge_candidate_verification.md for the open
# FD-validity questions and the qualification table.
SOIMOD_VARIANTS = {"fd": 2, "unified": 1}
DEFAULT_SOIMOD = 2


def tag_for(soimod: int) -> str:
    for tag, value in SOIMOD_VARIANTS.items():
        if value == soimod:
            return tag
    raise ValueError("unsupported SOIMOD variant: %r" % soimod)


def card_path(tag: str) -> Path:
    return MODEL_DIR / ("b4soi_%s_bridge_experimental.inc" % tag)


def lib_path(tag: str) -> Path:
    return ROOT / ("libs.tech/ngspice/b4soi_%s_bridge_experimental.lib.spice" % tag)


def fit_json_path(tag: str) -> Path:
    return ROOT / ("libs.tech/ngspice/fit/b4soi_%s_bridge_fit.json" % tag)


def fit_md_path(tag: str) -> Path:
    return ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_fit.md" % tag)


# The nominal card is NOT replaced by these experiments; it is read only for the
# `--evaluate` comparison and by the historical audits.
FROZEN_CARD = MODEL_DIR / "icps2023_5_b4soi.inc"

# PDK_minimalFabSOICMOS.pdf p.7 "Table of MOSFET Characteristics", Ta=25 C,
# L/W=6/24 um.  Level-1 measurements.
P7_TABLE = {
    "nch": {"vth": 0.25, "ss_mV_per_dec": 71.0, "idsat_uA_per_um": 62.5},
    "pch": {"vth": -0.71, "ss_mV_per_dec": 65.0, "idsat_uA_per_um": 12.9},
}
W_UM = 24.0
L_UM = 6.0
VDD = 3.0
# Subthreshold window used to extract a swing from a simulated sweep.  It must
# sit BELOW the transition region: measured on the v1 scaffold, the local swing
# rises from ~65 mV/dec at 1e-11..1e-9 A to ~122 mV/dec at 1e-7..1e-6 A, so a
# window that reaches 1e-7 averages in the transition and overstates the swing
# by ~14 mV/dec.  The digitized curves reach ~4e-12 A, so this window is inside
# the measured range.
SS_WINDOW_A = (1e-11, 1e-9)

# --------------------------------------------------------------------------
# Physical constants used only for closed-form *derived* parameters.
# --------------------------------------------------------------------------
Q_C = 1.602176634e-19
EPS0_F_PER_M = 8.8541878128e-12
EPS_SI = 11.7
EPS_OX = 3.9
K_B = 1.380649e-23
T_NOM_K = 298.15
NI_M3 = 1.45e16          # intrinsic carrier density, m^-3

# --------------------------------------------------------------------------
# Parameter provenance table.
#
#   kind=fixed    : not a fit target; value is a measurement, a geometry prior
#                   or an explicit modelling decision.
#   kind=derived  : computed in closed form from other physics inputs here.
#   kind=prior    : inherited from the same-process-line bulk-BSIM3 card as a
#                   starting value; bounded re-fit is allowed by AGENTS.md 4.1.
#   kind=fitted   : determined by the bounded optimisation on the 459 samples.
#
# `dep` records the reference the value is quoted against, for auditing.
# --------------------------------------------------------------------------
GEOMETRY = [
    ("LEVEL", 58, "fixed", "ngspice BSIMSOI v4.4 selector (BSIM4SOI). Not BSIM4/LEVEL=54."),
    ("SOIMOD", 2, "fixed", "Ideal-FD equations, chosen for electrostatic self-consistency "
                           "with this card's own AGENTS.md 4.1 premise, NOT as a claim about "
                           "the measured device. NCH=1e15 gives W_d,max=863 nm >> TSI=95 nm, "
                           "so no neutral body region can exist and the SOIMOD=0 "
                           "partially-depleted premise is contradicted; that mismatch is also a "
                           "numerical pathology (T7 120 ns: >1e7 steps at SOIMOD=0 vs 6.1e3 at "
                           "SOIMOD=2). Unified mode 1 is not a safe substitute: it is stable for "
                           "some parameter sets and stiff for others (7.8e3 vs >1e7 steps), "
                           "whereas mode 2 was stable for every set measured. Mode 2 and mode 1 "
                           "give identical DC here (transfer 0.1931 vs 0.1932 decades). The real "
                           "device's depletion mode remains a TEG question (AGENTS.md 1.2, 6)."),
    ("MOBMOD", 1, "fixed", "Unchanged from the stage-1 reference."),
    ("CAPMOD", 2, "fixed", "Unchanged from the stage-1 reference."),
    ("TNOM", 25, "fixed", "PDK characterisation temperature (p.7, Ta=25 C)."),
    ("TOX", 1.5e-8, "fixed", "PDK p.4/p.7 measured gate oxide 15 nm."),
    ("TOXM", 1.5e-8, "fixed", "Equals TOX; no oxide-thickness scaling is characterised."),
    ("TSI", 9.5e-8, "fixed", "AIST-SOI-CMOS.xs cross-section prior 95 nm (Level 2)."),
    ("TBOX", 4.0e-7, "fixed", "AIST-SOI-CMOS.xs cross-section prior 400 nm (Level 2)."),
    ("XJ", 5.0e-8, "fixed", "Unmeasured placeholder, XJ <= TSI; not copied from the bulk card."),
    ("NGATE", 0.0, "fixed", "TiN metal gate, no polysilicon depletion; midgap work function (AGENTS.md 2.3)."),
    ("NCH", 1.0e15, "fixed", "AGENTS.md 4.1: removes the bulk-BSIM3 NCH=1.7e17 residue; W_d,max >> TSI."),
    ("K2", 0.0, "fixed", "No body-bias sweep constrains the second-order term (AGENTS.md 2.3)."),
]

# Per-device entries. Bounds are (lo, hi) for kind=fitted/prior.
#
# Bound policy: a `prior` bound is a documented window AROUND the
# same-process-line bulk-BSIM3 surrogate value, not the whole physically
# imaginable range.  A wide bound lets the optimiser ride the clamp and turn
# the parameter into a numerical crutch - exactly the failure mode that
# produced the stage-1 PMOS RDSW=46250.  A parameter found sitting on a bound
# is reported as such and is evidence the model form, not the parameter, is
# limiting the fit.
DEVICE_TABLE = {
    "nch": [
        ("VTH0", 0.25, "fitted", "Start = PDK p.7 table value; bounded fit per user decision "
                                  "2026-10-16 (digitized transfer curves vs p.7 row conflict).",
         (0.05, 0.45)),
        ("K1", None, "derived", "K1 = sqrt(2*q*eps_Si*NCH)/Cox from the same NCH=1.0e15 channel doping.", None),
        ("NFACTOR", 1.38, "fitted", "Anchored to the PDK p.7 measured subthreshold slope (71 mV/dec).",
         (0.3, 6.0)),
        ("VOFF", -0.17284, "prior", "Same-line bulk-BSIM3 surrogate subthreshold offset.", (-0.35, 0.10)),
        ("U0", 670.0, "fitted", "Effective low-field mobility; bulk prior 670 cm^2/Vs.", (200.0, 1200.0)),
        ("UA", 2.25e-9, "prior", "Same-line bulk-BSIM3 vertical-field mobility degradation.", (-1.0e-8, 1.0e-8)),
        ("UB", 5.87e-19, "prior", "Same-line bulk-BSIM3 vertical-field mobility degradation.", (0.0, 1.0e-16)),
        ("UC", -4.65e-11, "prior", "Same-line bulk-BSIM3 lateral-field mobility degradation.", (-1.0e-10, 0.0)),
        ("VSAT", 800755.2814, "prior", "Same-line bulk-BSIM3 saturation velocity; +/-30% surrogate window.",
         (5.6e5, 1.04e6)),
        ("RDSW", 0.0, "fitted", "Source/drain access resistance; bounded to the physical contact range.",
         (0.0, 200.0)),
        ("A0", 1.1164, "prior", "Same-line bulk-BSIM3 bulk-charge/DIBL coefficient; surrogate window.",
         (0.0, 1.5)),
        ("AGS", 0.2656, "prior", "Same-line bulk-BSIM3 gate-bias dependence of A0.", (-0.1, 1.0)),
        ("KETA", -0.047, "prior", "Same-line bulk-BSIM3 body-bias DIBL coefficient; small physical term.",
         (-0.06, 0.02)),
        ("PCLM", 2.9694, "prior", "Same-line bulk-BSIM3 channel-length modulation; surrogate window.",
         (0.5, 6.0)),
        ("PDIBLC2", 0.0021218, "prior", "Same-line bulk-BSIM3 DIBL term (AGENTS.md 4.1).", (1.0e-6, 0.05)),
    ],
    "pch": [
        ("VTH0", -0.71, "fitted", "Start = PDK p.7 table value; bounded fit per user decision "
                                  "2026-10-16 (digitized transfer curves vs p.7 row conflict).",
         (-0.75, -0.25)),
        ("K1", None, "derived", "K1 = sqrt(2*q*eps_Si*NCH)/Cox from the same NCH=1.0e15 channel doping.", None),
        ("NFACTOR", 1.67, "fitted", "Anchored to the PDK p.7 measured subthreshold slope (65 mV/dec).",
         (0.3, 6.0)),
        ("VOFF", -0.11293, "prior", "Same-line bulk-BSIM3 surrogate subthreshold offset.", (-0.35, 0.10)),
        ("U0", 80.6055, "prior", "Effective low-field mobility; bulk prior 80.6 cm^2/Vs (bounded refit).",
         (40.0, 200.0)),
        ("UA", -5.7647e-10, "prior", "Same-line bulk-BSIM3 vertical-field mobility degradation.", (-1.0e-8, 1.0e-8)),
        ("UB", 5.0269e-19, "prior", "Same-line bulk-BSIM3 vertical-field mobility degradation.", (0.0, 1.0e-16)),
        ("UC", -4.65e-11, "prior", "Same-line bulk-BSIM3 lateral-field mobility degradation.", (-1.0e-10, 0.0)),
        ("VSAT", 999949.0, "prior", "Same-line bulk-BSIM3 saturation velocity; +/-30% surrogate window.",
         (7.0e5, 1.3e6)),
        ("RDSW", 218.151, "fitted", "Same-line bulk-BSIM3 physical value 218 ohm*um; AGENTS.md 4.1 cap 200.",
         (0.0, 200.0)),
        ("A0", 0.52732, "prior", "Same-line bulk-BSIM3 bulk-charge/DIBL coefficient; surrogate window.",
         (0.0, 1.2)),
        ("AGS", 0.1971, "prior", "Same-line bulk-BSIM3 gate-bias dependence of A0.", (-0.1, 1.0)),
        ("KETA", -0.047, "prior", "Same-line bulk-BSIM3 body-bias DIBL coefficient; small physical term.",
         (-0.06, 0.02)),
        ("PCLM", 8.9189, "prior", "Same-line bulk-BSIM3 channel-length modulation; surrogate window.",
         (3.0, 12.0)),
        ("PDIBLC2", 1.0757e-6, "prior", "Same-line bulk-BSIM3 DIBL term (AGENTS.md 4.1).", (1.0e-6, 0.05)),
    ],
}

# Measured overlap capacitance, PDK p.9 (Level 1). Mandatory on every candidate
# card by AGENTS.md 2.4.
OVERLAP_F_PER_M = {"nch": 5e-9, "pch": 6.8e-9}


def k1_from_nch(nch_cm3: float, tox_m: float) -> float:
    """Body-effect coefficient K1 = gamma = sqrt(2*q*eps_Si*Nch)/Cox."""
    cox = EPS_OX * EPS0_F_PER_M / tox_m
    nch = nch_cm3 * 1e6           # cm^-3 -> m^-3
    return math.sqrt(2.0 * Q_C * EPS_SI * EPS0_F_PER_M * nch) / cox


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def strip_comments(text: str) -> str:
    """Drop `*` comment lines and `$` inline comments from card text."""
    kept = []
    for line in text.splitlines():
        body = re.sub(r"\s*\$.*$", "", line)
        if body.strip().startswith("*"):
            continue
        kept.append(body)
    return "\n".join(kept)


def card_parameters(path: Path, model: str) -> dict:
    """Parse one .model block into uppercase parameter -> float.

    Comments are removed first: the provenance suffixes legitimately contain
    parentheses (e.g. `sqrt(2*q*eps_Si*NCH)/Cox`), which would otherwise
    terminate a naive non-greedy `\\(.*?\\)` model-body match early.
    """
    text = strip_comments(Path(path).read_text(encoding="utf-8"))
    match = re.search(r"(?is)\.model\s+%s\s+\w+\s*\((.*?)\)" % re.escape(model), text)
    if not match:
        raise RuntimeError("model %s not found in %s" % (model, path))
    return {key.upper(): float(value) for key, value in
            re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s*=\s*"
                       r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
                       match.group(1))}


# --------------------------------------------------------------------------
# Card rendering
# --------------------------------------------------------------------------
def defaults_for(device: str) -> dict:
    """Evaluated value for every table entry, with derived quantities resolved."""
    values = {name: value for name, value, _kind, _src in GEOMETRY}
    values["SOIMOD"] = DEFAULT_SOIMOD
    for name, value, kind, _src, _bounds in DEVICE_TABLE[device]:
        if kind == "derived":
            values[name] = k1_from_nch(values["NCH"], values["TOX"])
        else:
            values[name] = float(value)
    values["CGSO"] = OVERLAP_F_PER_M[device]
    values["CGDO"] = OVERLAP_F_PER_M[device]
    return values


def render_card(per_device: dict) -> str:
    """Self-documenting card text with per-parameter provenance comments.

    The card body is deliberately PLAIN (`+ NAME=value` only).  Neither an
    inline `$` comment nor an embedded `*` line inside a `.model (...)` block is
    portable across every consumer: CI runs ngspice-44 while this workstation has
    47, and Xyce rejects `$` comments outright.  Provenance is therefore emitted
    as a `*` comment block placed before each model, where every parser ignores
    it and where it stays adjacent to what it describes.
    """
    lines = [
        "* ICPS2023_5 BSIMSOI v4.4 (ngspice LEVEL=58) bridge candidate.",
        "*",
        "* Generated by tools/fit_b4soi_bridge.py.  ADJUSTED BRIDGE CANDIDATE:",
        "* it changes the stage-1 reference card and must not be selected as the",
        "* nominal model without re-running the IV/AC-charge/dynamic regressions.",
        "*",
        "* Data: PDK_minimalFabSOICMOS.pdf p.7 (Level 1 table) and the digitized",
        "* p.10-11 curves (459 samples) in ../fit/.  Bulk-DIBL/mobility priors come",
        "* from the same-process-line card models/legacy_MF_CMOS20240109_bulk3.inc.",
        "*",
        "* KNOWN CONFLICTS, retained rather than hidden:",
        "*  - the p.7 PMOS threshold row (-0.71 V) disagrees with the p.10-11 PMOS",
        "*    transfer curves (turn-on near |VGS| = 0.45 V), so VTH0 is a BOUNDED FIT;",
        "*  - the p.7 single subthreshold-slope number is not simultaneously attainable",
        "*    for both digitized curves of a device; the fit uses the p.7 value as a",
        "*    deadband target and lands inside the measured spread.",
        "* See audit/b4soi_bridge_fit.md and audit/b4soi_bridge_candidate_verification.md.",
        "*",
        "* SOIMOD=2 (ideal-FD) is deliberate, for self-consistency with this card's own",
        "* NCH=1e15 premise (W_d,max = 863 nm >> TSI = 95 nm, so no neutral body region",
        "* can exist) and NOT as a claim about the measured device. SOIMOD=0 imposes a",
        "* partially-depleted premise the film contradicts, which is also a numerical",
        "* pathology (T7 120 ns: >1e7 steps at SOIMOD=0 vs 6.1e3 at SOIMOD=2). Unified",
        "* mode 1 is stable only for some parameter sets. The real device's depletion",
        "* mode remains a TEG question (AGENTS.md 1.2 and 6).",
        "*",
    ]
    for device in ("nch", "pch"):
        values = per_device[device]
        lines.append("* Parameter provenance for %s (%s):"
                     % (device, "NMOS" if device == "nch" else "PMOS"))
        for name, _value, kind, source in GEOMETRY:
            lines.append("*   %-7s %-10s %s" % (kind, name, source))
        for name, _value, kind, source, _bounds in DEVICE_TABLE[device]:
            lines.append("*   %-7s %-10s %s" % (kind, name, source))
        lines.append("*   %-7s %-10s PDK p.9 measured overlap %s fF/um (AGENTS.md 2.4)"
                     % ("fixed", "CGSO/CGDO",
                        _fmt(OVERLAP_F_PER_M[device] * 1e15 / 1e6)))
        lines.append(".model %s %s (" % (device, "NMOS" if device == "nch" else "PMOS"))
        for name, _value, _kind, _source in GEOMETRY:
            lines.append("+ %s=%s" % (name, _fmt(values["SOIMOD"] if name == "SOIMOD"
                                                  else values[name])))
        for name, _value, _kind, _source, _bounds in DEVICE_TABLE[device]:
            lines.append("+ %s=%s" % (name, _fmt(values[name])))
        lines.append("+ CGSO=%s CGDO=%s" % (_fmt(values["CGSO"]), _fmt(values["CGDO"])))
        lines.append("+ )")
    return "\n".join(lines) + "\n"


def _fmt(value: float) -> str:
    return "%.10g" % value


def write_selector(library: Path, card: Path) -> None:
    library.write_text(
        "* Bridge-candidate library selector.\n"
        "* Mirrors icps2023_5.lib.spice's b4soi section but selects the bridge card.\n"
        ".lib b4soi\n.include \"%s\"\n.endl b4soi\n"
        % card.resolve().as_posix(), encoding="utf-8")


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def evaluate(device: str, card_path: Path, ngspice: str, curves, regress, work: Path,
             step: float = 0.001):
    """Per-point (axis, measured, simulated) for the device's digitized curves."""
    observed = {}
    library = work / "eval.lib.spice"
    write_selector(library, card_path)
    for key, points in sorted(curves.items()):
        if key[0] != ("nmos" if device == "nch" else "pmos"):
            continue
        deck, start = regress.make_sweep_deck(key, points, step, library)
        deck_path = work / ("%s.cir" % key[2])
        data_path = work / "sweep.dat"
        data_path.unlink(missing_ok=True)
        deck_path.write_text(deck, encoding="utf-8")
        run = subprocess.run([ngspice, "-b", str(deck_path)], cwd=work, text=True,
                             capture_output=True, check=False, timeout=180)
        if run.returncode != 0 or not data_path.is_file():
            raise RuntimeError("ngspice failed for %s:\n%s\n%s"
                               % (key[2], run.stdout, run.stderr))
        axis, current = regress.read_sweep(data_path, step, start)
        observed[key[2]] = {
            "family": key[1],
            "axis": np.array([p[0] for p in points]),
            "meas": np.array([p[1] for p in points]),
            "sim": np.array([regress.interpolate(axis, current, p[0]) for p in points]),
        }
    return observed


def relative_rms(sim, meas) -> float:
    """Unweighted pointwise relative RMS in percent (the frozen output metric)."""
    r = sim / meas - 1.0
    return 100.0 * math.sqrt(float(np.mean(r ** 2)))


def log_rms(sim, meas) -> float:
    """Unweighted RMS of log10(sim) - log10(meas) in decades (frozen transfer metric)."""
    r = np.log10(sim) - np.log10(meas)
    return math.sqrt(float(np.mean(r ** 2)))


def simulated_swing(axis, sim):
    """Subthreshold swing in V/decade from log-linear interpolation in SS_WINDOW_A."""
    order = np.argsort(sim)
    x = axis[order]
    y = np.log10(sim[order])
    crossings = []
    for level in SS_WINDOW_A:
        target = math.log10(level)
        if not (y[0] <= target <= y[-1]):
            return None
        crossings.append(float(np.interp(target, y, x)))
    return abs(crossings[1] - crossings[0]) / (
        math.log10(SS_WINDOW_A[1]) - math.log10(SS_WINDOW_A[0]))


def curve_metrics(observed, family=None, axis_mask=None, current_mask=None):
    """Metrics over an optionally masked subset of points, per curve."""
    out = {}
    for name, data in observed.items():
        if family is not None and data["family"] != family:
            continue
        keep = np.ones(data["axis"].shape, dtype=bool)
        if axis_mask is not None:
            keep &= axis_mask(data["axis"])
        if current_mask is not None:
            keep &= current_mask(data["meas"])
        if int(keep.sum()) < 2:
            continue
        if data["family"] == "transfer":
            out[name] = {"samples": int(keep.sum()),
                         "log10_current_rms_decades":
                             log_rms(data["sim"][keep], data["meas"][keep])}
        else:
            out[name] = {"samples": int(keep.sum()),
                         "relative_rms_percent":
                             relative_rms(data["sim"][keep], data["meas"][keep])}
    return out


# --------------------------------------------------------------------------
# Objective
# --------------------------------------------------------------------------
WEIGHT_TRANSFER = 1.0      # per decade of log10 RMS
WEIGHT_OUTPUT = 0.03       # per percent of relative RMS
# AGENTS.md 4.1 states the transfer-residual target explicitly, so it is encoded
# as a one-sided hinge rather than left implicit.  Without it the optimiser
# settles on a point that is better by the aggregate objective yet sits just
# above the stated target (PMOS blue 0.3010 vs a feasible 0.2730).
TRANSFER_TARGET_DECADES = 0.30
WEIGHT_TRANSFER_TARGET = 100.0   # per decade above the target
# The p.7 subthreshold slope is a Level-1 measurement and the user pinned the
# fit to it, so it is enforced as a deadband constraint rather than a soft
# tug-of-war: deviation inside SWING_DEADBAND_V is free, outside it the
# penalty grows quadratically and dominates the I-V terms.
WEIGHT_SWING = 5000.0      # per (V/decade)^2 outside the deadband
SWING_DEADBAND_V = 0.002   # 2 mV/decade
SWING_MISSING_PENALTY = 5.0

STAGES = (
    # name, {family: (|axis| range or None, measured-current range or None)}, swing penalty
    ("subthreshold", {"transfer": (None, (0.0, 1e-6))}, True),
    ("linear", {"transfer": (None, None),
                "output": ((0.0, 0.60), None)}, True),
    ("saturation", {"output": ((0.60, 10.0), None)}, False),
    ("full", {"transfer": (None, None), "output": (None, None)}, True),
)

STAGE_PARAMETERS = {
    "subthreshold": ("VTH0", "NFACTOR", "VOFF"),
    "linear": ("U0", "UA", "UB", "UC", "RDSW"),
    "saturation": ("VSAT", "PCLM", "PDIBLC2", "A0", "KETA"),
    "full": ("VTH0", "NFACTOR", "VOFF", "U0", "UA", "UB", "UC", "RDSW",
             "VSAT", "PCLM", "PDIBLC2", "A0", "KETA"),
}


def stage_spec(stage_name):
    for name, families, swing in STAGES:
        if name == stage_name:
            return families, swing
    raise KeyError(stage_name)


def _axis_filter(axis, spec):
    if spec is None:
        return np.ones(axis.shape, dtype=bool)
    return (np.abs(axis) >= spec[0]) & (np.abs(axis) <= spec[1])


def _current_filter(meas, spec):
    if spec is None:
        return np.ones(meas.shape, dtype=bool)
    return (meas >= spec[0]) & (meas <= spec[1])


def objective(observed, device, stage_name) -> tuple:
    families, swing = stage_spec(stage_name)
    total = 0.0
    breakdown = {}
    for family, (axis_spec, current_spec) in families.items():
        metrics = curve_metrics(
            observed, family=family,
            axis_mask=(lambda axis, s=axis_spec: _axis_filter(axis, s)),
            current_mask=(lambda meas, s=current_spec: _current_filter(meas, s)))
        for name, record in metrics.items():
            if family == "transfer":
                residual = record["log10_current_rms_decades"]
                total += WEIGHT_TRANSFER * residual
                total += WEIGHT_TRANSFER_TARGET * max(
                    0.0, residual - TRANSFER_TARGET_DECADES)
            else:
                total += WEIGHT_OUTPUT * record["relative_rms_percent"]
            breakdown[name] = record
    if swing:
        target = P7_TABLE[device]["ss_mV_per_dec"] / 1000.0
        penalties = []
        for name, data in observed.items():
            if data["family"] != "transfer":
                continue
            value = simulated_swing(data["axis"], data["sim"])
            if value is None:
                # The curve must span the subthreshold window for a swing to exist.
                penalties.append(SWING_MISSING_PENALTY)
                continue
            excess = max(0.0, abs(value - target) - SWING_DEADBAND_V)
            penalties.append(WEIGHT_SWING * excess * excess)
            breakdown[name] = {**breakdown.get(name, {}), "swing_V_per_dec": value}
        if penalties:
            total += sum(penalties) / len(penalties)
    return total, breakdown


# --------------------------------------------------------------------------
# Bounded compass (pattern) search
# --------------------------------------------------------------------------
class Optimiser:
    def __init__(self, ngspice, device, curves, regress, work):
        self.ngspice = ngspice
        self.device = device
        self.curves = curves
        self.regress = regress
        self.work = work
        self.cache = {}
        self.evaluations = 0

    def bounds(self):
        out = {}
        for name, _v, kind, _src, bounds in DEVICE_TABLE[self.device]:
            if kind in ("fitted", "prior") and bounds:
                out[name] = bounds
        return out

    def evaluate(self, values) -> tuple:
        key = tuple(sorted((k, round(v, 12)) for k, v in values.items()))
        if key in self.cache:
            return self.cache[key]
        card = self.work / ("card_%s.inc" % self.device)
        card.write_text(render_card({
            "nch": values if self.device == "nch" else defaults_for("nch"),
            "pch": values if self.device == "pch" else defaults_for("pch"),
        }), encoding="utf-8")
        observed = evaluate(self.device, card, self.ngspice, self.curves,
                            self.regress, self.work)
        self.evaluations += 1
        self.cache[key] = observed
        return observed

    def search(self, values, stage_name, names, max_rounds=36, verbose=True):
        """Compass search over `names` with geometric step halving."""
        bounds = self.bounds()
        step_floor = 1e-4
        axes = {}
        for name in names:
            lo, hi = bounds[name]
            axes[name] = [lo, hi, (hi - lo) / 3.0, values[name]]

        def cost(candidate):
            observed = self.evaluate(candidate)
            return objective(observed, self.device, stage_name)[0]

        best = cost(values)
        for round_index in range(max_rounds):
            improved = False
            for name in names:
                lo, hi, step, _current = axes[name]
                if step <= max(1e-12, step_floor * (hi - lo)):
                    continue
                for direction in (+1.0, -1.0):
                    trial = dict(values)
                    proposal = trial[name] + direction * step
                    if not (lo - 1e-15 <= proposal <= hi + 1e-15):
                        continue
                    trial[name] = min(max(proposal, lo), hi)
                    score = cost(trial)
                    if score < best - 1e-9:
                        best = score
                        values = trial
                        improved = True
            if not improved:
                for name in names:
                    axes[name][2] /= 2.0
                if all(axes[n][2] <= max(1e-12, step_floor * (axes[n][1] - axes[n][0]))
                       for n in names):
                    break
            if verbose:
                print("      %-14s round %2d  J=%.5f  evals=%d"
                      % (stage_name, round_index, best, self.evaluations), flush=True)
        return values, best


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def report_lines(device, observed, baseline_metrics, provenance, table):
    lines = ["## %s" % ("NMOS" if device == "nch" else "PMOS"), ""]
    lines += ["| curve | family | samples | metric | candidate | frozen stage-1 |",
              "|---|---|---:|---|---:|---:|"]
    metrics = curve_metrics(observed)
    for name, data in sorted(observed.items()):
        record = metrics[name]
        key = "log10_current_rms_decades" if data["family"] == "transfer" else "relative_rms_percent"
        unit = "dec" if data["family"] == "transfer" else "%"
        base = baseline_metrics[name].get(key)
        lines.append("| %s | %s | %d | %.4f %s | %.4f | %s |"
                     % (name, data["family"], record["samples"],
                        record[key], unit, record[key],
                        "%.4f" % base if base is not None else "n/a"))
    lines.append("")
    base_ss = P7_TABLE[device]["ss_mV_per_dec"]
    transfer_verdicts = []
    for name, data in sorted(observed.items()):
        if data["family"] != "transfer":
            continue
        transfer_verdicts.append("%s %s (%.4f dec)"
                                % (name, "MEETS" if metrics[name][
                                    "log10_current_rms_decades"]
                                   <= TRANSFER_TARGET_DECADES else "MISSES",
                                   metrics[name]["log10_current_rms_decades"]))
    lines += ["- transfer target %.2f decades: %s"
              % (TRANSFER_TARGET_DECADES, "; ".join(transfer_verdicts)), ""]
    lines += ["| curve | simulated swing (mV/dec) | PDK p.7 measured (mV/dec) |",
              "|---|---:|---:|"]
    for name, data in sorted(observed.items()):
        if data["family"] != "transfer":
            continue
        swing = simulated_swing(data["axis"], data["sim"])
        lines.append("| %s | %s | %.0f |"
                     % (name, "n/a" if swing is None else "%.1f" % (swing * 1000.0), base_ss))
    lines.append("")
    idsat = None
    for name, data in sorted(observed.items()):
        if data["family"] != "output":
            continue
        index = int(np.argmax(np.abs(data["axis"])))
        if abs(data["axis"][index]) < 2.9:
            continue
        idsat = data["sim"][index] * 1e6 / W_UM
    if idsat is not None:
        lines += ["- Idsat at the largest sampled |VDS|: **%.2f uA/um** simulated vs "
                  "**%.2f uA/um** (PDK p.7) -> %+.1f%%"
                  % (idsat, P7_TABLE[device]["idsat_uA_per_um"],
                     100.0 * (idsat / P7_TABLE[device]["idsat_uA_per_um"] - 1.0)), ""]
    lines += ["### Parameter provenance", "",
              "| parameter | value | kind | source |", "|---|---:|---|---|"]
    for name, _value, kind, source in GEOMETRY:
        lines.append("| %s | %s | %s | %s |" % (name, _fmt(provenance[name]), kind, source))
    for name, _v, kind, source, _bounds in DEVICE_TABLE[device]:
        lines.append("| %s | %s | %s | %s |" % (name, _fmt(provenance[name]), kind, source))
    lines.append("")
    at_bound = []
    for name, _v, kind, _source, bounds in DEVICE_TABLE[device]:
        if not bounds:
            continue
        lo, hi = bounds
        value = provenance[name]
        span = hi - lo
        if min(abs(value - lo), abs(value - hi)) <= 1e-6 * span:
            at_bound.append("%s=%s%s" % (name, _fmt(value),
                                         " (lower)" if abs(value - lo) < abs(value - hi)
                                         else " (upper)"))
    if at_bound:
        lines += ["- SEARCH BOUND REACHED: %s. A parameter on a clamp is not a "
                  "physical result; it flags where the model form, not the "
                  "parameter, limits the fit." % ", ".join(at_bound), ""]
    else:
        lines += ["- No fitted or prior parameter sits on a search bound.", ""]
    return lines


STAGE1_REFERENCE_CARD = MODEL_DIR / "stage1_b4soi_reference.inc"
STAGE1_REFERENCE_BASELINE = ROOT / "libs.tech/ngspice/fit/stage1_b4soi_baseline.json"
VERIFICATION_REPORT = ROOT / "libs.tech/ngspice/audit/b4soi_bridge_candidate_verification.md"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"))
    parser.add_argument("--write-card", help="write the un-fitted v1 bridge card here")
    parser.add_argument("--variant", choices=sorted(SOIMOD_VARIANTS), default="fd",
                        help="fd (SOIMOD=2, ideal-FD) or unified (SOIMOD=1); both are "
                             "retained as experimental cards")
    parser.add_argument("--render-from-json",
                        help="re-render the variant's experimental card from a fit json "
                             "without re-optimising (formatting-only changes)")
    parser.add_argument("--evaluate", help="evaluate an existing card and print metrics")
    parser.add_argument("--fit", action="store_true", help="run the bounded refit and emit the variant card")
    parser.add_argument("--out", default=str(ROOT / "build/b4soi-bridge"))
    parser.add_argument("--step-V", type=float, default=0.001)
    args = parser.parse_args()
    if not args.ngspice:
        parser.error("ngspice is required")

    global DEFAULT_SOIMOD
    DEFAULT_SOIMOD = SOIMOD_VARIANTS[args.variant]
    tag = args.variant
    variant_card = card_path(tag)
    variant_lib = lib_path(tag)
    variant_json = fit_json_path(tag)
    variant_md = fit_md_path(tag)

    regress = load_module(TOOLS / "regress_b4soi_stage1_iv.py", "b4soi_iv_regression")
    curves = regress.read_curves(regress.MEASUREMENTS)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    v1 = {device: defaults_for(device) for device in ("nch", "pch")}

    if args.write_card:
        target = Path(args.write_card)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_card(v1), encoding="utf-8")
        print("wrote %s" % target)
        return 0

    if args.render_from_json:
        snapshot = json.loads(Path(args.render_from_json).read_text(encoding="utf-8"))
        per_device = {device: snapshot["devices"][device]["values"]
                      for device in ("nch", "pch")}
        variant_card.write_text(render_card(per_device), encoding="utf-8")
        write_selector(variant_lib, variant_card)
        print("re-rendered %s from %s" % (variant_card, args.render_from_json))
        return 0

    if args.evaluate:
        card = Path(args.evaluate).resolve()
        with tempfile.TemporaryDirectory(prefix="icps-bridge-eval-") as scratch:
            work = Path(scratch)
            for device in ("nch", "pch"):
                observed = evaluate(device, card, args.ngspice, curves, regress, work,
                                    args.step_V)
                metrics = curve_metrics(observed)
                baseline = json.loads(regress.BASELINE.read_text(encoding="utf-8"))["curves"]
                print("\n".join(report_lines(device, observed, baseline, v1[device],
                                             DEVICE_TABLE[device])))
        return 0

    if not args.fit:
        parser.error("choose one of --write-card, --evaluate, --fit, --render-from-json")

    baseline_metrics = json.loads(regress.BASELINE.read_text(encoding="utf-8"))["curves"]
    fitted = {}
    with tempfile.TemporaryDirectory(prefix="icps-bridge-fit-") as scratch:
        work = Path(scratch)
        for device in ("nch", "pch"):
            print("=== fitting %s ===" % device, flush=True)
            optimiser = Optimiser(args.ngspice, device, curves, regress, work)
            values = dict(v1[device])
            for stage_name in ("subthreshold", "linear", "saturation", "full"):
                names = STAGE_PARAMETERS[stage_name]
                print("  stage %s: %s" % (stage_name, ", ".join(names)), flush=True)
                values, cost = optimiser.search(values, stage_name, names)
                print("  -> stage %s J=%.5f" % (stage_name, cost), flush=True)
            observed = optimiser.evaluate(values)
            cost, breakdown = objective(observed, device, "full")
            print("  -> final full-objective J=%.5f" % cost, flush=True)
            for name in sorted(breakdown):
                print("     %-18s %s" % (name, json.dumps(breakdown[name], sort_keys=True)),
                      flush=True)
            fitted[device] = {"values": values, "observed": observed,
                              "evaluations": optimiser.evaluations}

    variant_card.write_text(render_card({d: fitted[d]["values"] for d in fitted}),
                            encoding="utf-8")
    write_selector(variant_lib, variant_card)
    print("wrote %s" % variant_card)

    document = ["# B4SOI %s experimental bridge refit (SOIMOD=%d)"
                % (tag, DEFAULT_SOIMOD), "",
                "Generated by `tools/fit_b4soi_bridge.py --fit --variant %s`." % tag,
                "This is an EXPERIMENTAL card. The nominal model is unchanged:",
                "`libs.tech/ngspice/models/icps2023_5_b4soi.inc` (stage-1) remains",
                "nominal. The %s card's declared scope is the Xyce 7.10 / BSIMSOI"
                % tag,
                "4.5.0 backend at the verified W24/L6 geometry, 3 V, 25 C, four-terminal",
                "D/G/S/E, with the benches documented in the verification report.", "",
                "Objective = sum over curves of (%.1f per transfer decade + %.2f per output "
                "percent)," % (WEIGHT_TRANSFER, WEIGHT_OUTPUT),
                "plus %.0f per transfer decade ABOVE the %.2f-decade AGENTS.md 4.1 target,"
                % (WEIGHT_TRANSFER_TARGET, TRANSFER_TARGET_DECADES),
                "plus a deadband constraint of %.0f per (V/decade)^2 outside %.0f mV/decade"
                % (WEIGHT_SWING, SWING_DEADBAND_V * 1e3),
                "on the subthreshold swing against the PDK p.7 value. NMOS and PMOS are",
                "fitted independently.", "",
                "The swing constraint competes with the transfer residual because the p.7",
                "single slope number and the p.10-11 digitized curves do not agree. The",
                "digitized curves' own swings (same deep-subthreshold window) are",
                "NMOS 69.3 / 75.6 mV/dec and PMOS 65.9 / 75.7 mV/dec, while the table",
                "states 71 / 65 mV/dec. The fitted card lands inside that measured spread;",
                "forcing the single table value drives the transfer residual above the",
                "0.3-decade target. The per-curve swings below are the evidence.", ""]
    snapshot = {"variant": tag, "soimod": DEFAULT_SOIMOD,
                "nominal_model": str(FROZEN_CARD.relative_to(ROOT)),
                "objective_weights": {"transfer_per_decade": WEIGHT_TRANSFER,
                                      "output_per_percent": WEIGHT_OUTPUT,
                                      "swing_per_V_per_decade": WEIGHT_SWING},
                "p7_table": P7_TABLE, "devices": {}}
    for device in ("nch", "pch"):
        observed = fitted[device]["observed"]
        document += report_lines(device, observed, baseline_metrics,
                                 fitted[device]["values"], DEVICE_TABLE[device])
        snapshot["devices"][device] = {
            "values": {k: v for k, v in fitted[device]["values"].items()},
            "evaluations": fitted[device]["evaluations"],
            "metrics": curve_metrics(observed),
        }
    variant_md.write_text("\n".join(document) + "\n", encoding="utf-8")
    variant_json.parent.mkdir(parents=True, exist_ok=True)
    variant_json.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    print("wrote %s" % variant_md)
    print("wrote %s" % variant_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
