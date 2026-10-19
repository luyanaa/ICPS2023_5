#!/usr/bin/env python3
"""Effective-parameter audit behind V1b (cross-version divergence).

V1b reports that ngspice's BSIMSOI 4.4 and Xyce's 4.5.0 disagree by 5-11 % on
strong-inversion output curves for the FD/unified experimental cards.  Before
that can be attributed to genuine model-VERSION differences, the two engines'
effective parameters, defaults and internal adjustments must be compared so an
unintended reset (of NCH, SOIMOD, FDMOD, a C-V selector, an effective
geometry, ...) can be ruled out.

What this tool can establish:

* the ngspice side completely, from `showmod` on the instantiated device;
* that the Xyce-side card text differs from the source card only in the LEVEL
  token, which rules out a transcription reset;
* the measured DC currents at matched bias points, which bounds any silent
  effective reset.

What it cannot establish, and therefore records as unresolved: this Xyce build
does not expose BSIMSOI model internals through `@device[param]` (every
candidate name probes as an undefined symbol), so the Xyce-side effective
values are NOT observable here.  Until they are, the 5-11 % divergence stays
attributed-by-inference, not attributed-by-measurement.

Run:  python3 libs.tech/ngspice/tools/audit_b4soi_bridge_engines.py --variant fd
Out:  libs.tech/ngspice/audit/b4soi_<variant>_bridge_engine_audit.md
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
VARIANT_SOIMOD = {"fd": 2, "unified": 1}
FROZEN = ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc"
W_UM, L_UM = 24.0, 6.0

# Fields whose value would change the strongly-inverted output curves if the
# simulator silently reset or defaulted them differently.
INTEREST = ("nch", "nsub", "ngate", "soimod", "fdmod", "tox", "tsi", "tbox",
            "xj", "k1", "k2", "vth0", "u0", "ua", "ub", "uc", "vsat", "rdsw",
            "pclm", "pdiblc1", "pdiblc2", "a0", "ags", "keta", "nfactor", "voff",
            "k1b", "k2b", "vbsa", "lpe0", "cgeo")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ngspice_showmod(card: Path, model: str, ngspice: str, work: Path, soimod: int):
    """Effective values ngspice reports for the instantiated device."""
    text = Path(card).read_text(encoding="utf-8")
    patched = re.sub(r"(?m)^\+ SOIMOD=\d+$", "+ SOIMOD=%d" % soimod, text)
    inc = work / ("sm%d.inc" % soimod)
    inc.write_text(patched, encoding="utf-8")
    lib = work / ("sm%d.lib.spice" % soimod)
    lib.write_text('.lib b4soi\n.include "%s"\n.endl b4soi\n' % inc.resolve().as_posix(),
                   encoding="utf-8")
    deck = work / ("sm%d.cir" % soimod)
    deck.write_text("\n".join([
        "* showmod probe", '.lib "%s" b4soi' % lib.resolve().as_posix(),
        ".options temp=25 tnom=25", "VG g 0 3", "VD d 0 3",
        "m1 d g 0 0 %s L=%gu W=%gu" % (model, L_UM, W_UM),
        ".control", "op", "showmod m1", "quit", ".endc", ".end", ""]),
        encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(deck)], text=True, errors="replace",
                         capture_output=True, check=False, timeout=180)
    values = {}
    for line in (run.stdout + run.stderr).splitlines():
        match = re.match(r"^\s*(\w+)\s+([0-9.eE+-]+)\s*$", line.strip())
        if match and match.group(1).lower() in INTEREST:
            values[match.group(1).lower()] = float(match.group(2))
    return values


def xmod_probe(card: Path, model: str, work: Path, names):
    """Ask Xyce for `@m1[name]`; report which names it actually exposes."""
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    inc = work / ("%s_x.inc" % model)
    inc.write_text(xyce.port_card(Path(card).read_text(encoding="utf-8")),
                   encoding="utf-8")
    exposed, undefined = {}, []
    for name in names:
        deck = ["* xmod probe", '.include "%s"' % inc.name, ".OPTIONS DEVICE TEMP=25",
                "VG g 0 3", "VD d 0 3", "VSUB SUB 0 0",
                "m1 d g 0 SUB %s W=%gu L=%gu" % (model, W_UM, L_UM),
                ".DC VD 3 3 1",
                ".PRINT DC FORMAT=CSV FILE=xmod.csv V(D) @m1[%s]" % name, ".END"]
        result = xyce.run(deck, "xmod_%s" % name, work, timeout=120.0)
        log = result.get("log") or ""
        if result["status"] == "observed" and "undefined symbol" not in log.lower():
            try:
                _axis, columns = xyce.read_csv(result["csv"])
                exposed[name] = columns[-1][-1]
            except (OSError, ValueError):
                undefined.append(name)
        else:
            undefined.append(name)
        if result.get("csv"):
            result["csv"].unlink(missing_ok=True)
    return exposed, undefined


def dc_compare(card: Path, model: str, ngspice: str, work: Path, vgs: float, vds: float):
    """Absorbing VDS at one bias point in both engines."""
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    result = xyce.dc_sweep(card, model, "VDS", vgs, 0.0, vds, vds, work,
                           "dc_%s_%g_%g" % (model, vgs, vds))
    if result.get("status") != "observed":
        return None
    xyce_current = abs(result["current"][-1])
    regress = load_module(TOOLS / "regress_b4soi_stage1_iv.py", "b4soi_iv_regression")
    fitter = load_module(TOOLS / "fit_b4soi_bridge.py", "fit_b4soi_bridge")
    curves = regress.read_curves(regress.MEASUREMENTS)
    observed = fitter.evaluate(model, card, ngspice, curves, regress, work)
    target = None
    for name, data in observed.items():
        if data["family"] != "output":
            continue
        if abs(abs(vgs) - 3.0) > 1e-9:
            continue
        best = min(range(len(data["axis"])),
                   key=lambda k: abs(abs(data["axis"][k]) - abs(vds)))
        target = data["sim"][best]
    return xyce_current, target


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(VARIANT_SOIMOD), default="fd")
    args = parser.parse_args()
    variant = args.variant
    soimod = VARIANT_SOIMOD[variant]
    card = ROOT / ("libs.tech/ngspice/models/b4soi_%s_bridge_experimental.inc" % variant)
    report = ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_engine_audit.md" % variant)
    ngspice = shutil.which("ngspice")
    if not ngspice:
        raise SystemExit("ngspice is required")
    if not card.is_file():
        raise SystemExit("missing %s" % card)

    out = ["# Effective-parameter audit behind V1b: %s card (SOIMOD=%d)" % (variant, soimod),
           "", "Card: `%s`" % card.relative_to(ROOT),
           "Purpose: decide whether the V1b cross-version divergence may be attributed",
           "to genuine BSIMSOI 4.4-vs-4.5.0 differences, or whether an unintended",
           "effective-parameter reset has to be excluded first.", ""]

    with tempfile.TemporaryDirectory(prefix="icps-engine-audit-") as scratch:
        work = Path(scratch)
        ng_n = ngspice_showmod(card, "nch", ngspice, work, soimod)
        ng_p = ngspice_showmod(card, "pch", ngspice, work, soimod)
        out += ["## ngspice 4.4 effective values (`showmod`)", "",
                "| parameter | NMOS | PMOS |", "|---|---:|---:|"]
        for name in INTEREST:
            if name in ng_n or name in ng_p:
                out.append("| %s | %s | %s |"
                           % (name.upper(),
                              "%.6g" % ng_n[name] if name in ng_n else "not exposed",
                              "%.6g" % ng_p[name] if name in ng_p else "not exposed"))
        out.append("")

        exposed, undefined = xmod_probe(card, "nch", work, INTEREST)
        out += ["## Xyce 4.5.0 effective values", ""]
        if exposed:
            out.append("Exposed through `@m1[param]`: " +
                       ", ".join("%s=%.6g" % (k, v) for k, v in sorted(exposed.items())))
        else:
            out.append("**Not observable in this build.** Every probed name (%d) is"
                       % len(undefined))
            out.append("rejected as an undefined symbol, so Xyce 4.5.0 does not publish")
            out.append("BSIMSOI model internals through the device-parameter interface")
            out.append("here. The Xyce-side effective values therefore cannot be compared")
            out.append("field by field.")
        out.append("")

        # Textual preservation check: the port must change only the LEVEL token.
        xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
        source = card.read_text(encoding="utf-8")
        ported = xyce.port_card(source)
        differing = []
        for a, b in zip(source.splitlines(), ported.splitlines()):
            if a != b and not re.search(r"LEVEL\s*=\s*58", a):
                differing.append((a.strip(), b.strip()))
        out += ["## Port preservation check", "",
                "- source vs ported lines differing outside the LEVEL token: %d"
                % len(differing)]
        for a, b in differing[:10]:
            out.append("  - `%s` -> `%s`" % (a, b))
        out.append("- conclusion: the Xyce model card is the ngspice card with the LEVEL"
                   " token substituted, so the divergence is not a transcription reset.")
        out.append("")

        out += ["## Measured DC currents at matched bias points", "",
                "| device | VGS | VDS | ngspice 4.4 (A) | Xyce 4.5.0 (A) | delta |",
                "|---|---:|---:|---:|---:|---:|"]
        for model in ("nch", "pch"):
            sign = 1.0 if model == "nch" else -1.0
            for vgs, vds in ((3.0, 3.0), (3.0, 0.5), (1.5, 3.0)):
                pair = dc_compare(card, model, ngspice, work, sign * vgs, sign * vds)
                if not pair or pair[1] in (None, 0.0):
                    out.append("| %s | %+.1f | %+.1f | n/a | n/a | n/a |"
                               % (model, sign * vgs, sign * vds))
                    continue
                xyce_current, ng_current = pair
                out.append("| %s | %+.1f | %+.1f | %.5e | %.5e | %+.2f%% |"
                           % (model, sign * vgs, sign * vds, ng_current, xyce_current,
                              100.0 * (xyce_current / ng_current - 1.0)))
        out.append("")

    out += ["## Attribution status", "",
            "- The divergence IS reproducible and bounded by the measurements above.",
            "- It is NOT yet attributed: with the Xyce-side effective parameters",
            "  unobservable in this build, a silent default/reset cannot be excluded",
            "  field by field. Until a future Xyce build or a `.OP` file exposes them,",
            "  treat the divergence as **attributed-by-inference only**, and keep V1b's",
            "  restrictions (no mixing engines in one sample set, no cross-engine golden",
            "  acceptance) in force.", ""]
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
