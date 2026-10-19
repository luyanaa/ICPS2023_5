#!/usr/bin/env python3
"""Shared Xyce execution path for the ICPS2023_5 BSIMSOI verification tools.

Why Xyce: the local ngspice-47 build is empirically unstable on BSIMSOI
*dynamic* benches (the depletion audit retained 21/216 transient aborts with
`timestep too small`, plus mA-scale supply-current chatter behind an otherwise
quiet output).  Xyce is used as an independent numerical path so that a solver
pathology cannot be mistaken for a model result.  It is NOT proposed as a
replacement PDK timing simulator: Xyce's BSIMSOI is a different version.

Model versions are not interchangeable, so porting is explicit:

    ngspice LEVEL=58   = BSIMSOI 4.4
    Xyce    LEVEL=70450 = BSIMSOI 4.5.0   (primary port target)
    Xyce    LEVEL=70    = BSIMSOI 4.6.1   (diagnostic control)

Only the LEVEL token is rewritten.  Two Xyce netlist constraints matter and are
handled here rather than in each caller:

* the first netlist line is the title and is swallowed by the parser, so a
  title line is always emitted;
* Xyce has no `$` inline comment, while the generated cards carry their
  parameter provenance in `$ ...` suffixes.  Those suffixes are lifted onto
  their own `*` lines so the provenance survives the port instead of being
  silently dropped.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

XYCE = "/usr/local/XyceNF_OMPI_7.10/bin/Xyce"
NGSPICE_LEVEL = 58
XYCE_LEVEL = 70450


def xyce_env() -> dict:
    """Xyce links against Homebrew OpenMPI; DYLD_LIBRARY_PATH must find libmpi."""
    env = dict(os.environ)
    prefix = "/opt/homebrew/opt/open-mpi/lib"
    try:
        prefix = subprocess.run(["brew", "--prefix", "open-mpi"], text=True,
                                capture_output=True, check=False).stdout.strip() + "/lib"
    except OSError:
        pass
    existing = env.get("DYLD_LIBRARY_PATH", "")
    env["DYLD_LIBRARY_PATH"] = prefix + ((":" + existing) if existing else "")
    return env


def available() -> bool:
    return Path(XYCE).is_file()


def port_card(text: str, level: int = XYCE_LEVEL) -> str:
    """Rewrite an ngspice BSIMSOI card for Xyce: LEVEL token plus comment form."""
    out = []
    for line in text.splitlines():
        match = re.match(r"^(?P<body>.*?)\s*\$(?P<comment>.*)$", line)
        if match and match.group("body").strip():
            out.append("* %s" % match.group("comment").strip())
            line = match.group("body")
        out.append(re.sub(r"\bLEVEL\s*=\s*%d\b" % NGSPICE_LEVEL,
                          "LEVEL=%d" % level, line))
    return "\n".join(out) + "\n"


def run(deck_lines, name: str, work: Path, *, timeout: float = 900.0,
        budget_bytes: int | None = None, require_csv: bool = True) -> dict:
    """Write and run one Xyce netlist.  Never rewrites a failure into a pass.

    The printed CSV is located through the deck's own `FILE=` argument
    rather than assumed to equal `name`, because Xyce writes exactly the requested
    filename.  Measure-only decks may set ``require_csv=False``; their ``.mt0``
    file is consumed by the caller instead.
    `budget_bytes` kills a run whose output grows past that size.  A stiff model
    can otherwise emit hundreds of megabytes: a transient that silently takes
    ~1e7 internal steps is a numerical result, and it must be reported as an
    over-budget failure rather than allowed to exhaust the disk.
    """
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    path = work / (name + ".cir")
    path.write_text("\n".join(deck_lines) + "\n", encoding="utf-8")
    match = re.search(r"FILE=(\S+)", "\n".join(deck_lines))
    csv_path = work / (match.group(1) if match else name + ".csv")
    csv_path.unlink(missing_ok=True)
    if not available():
        return {"status": "unavailable", "reason": "Xyce not found at %s" % XYCE}
    started = time.monotonic()
    process = subprocess.Popen([XYCE, path.name], cwd=work, env=xyce_env(),
                               text=True, errors="replace", stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    over_budget = None
    while True:
        try:
            stdout, _ = process.communicate(timeout=2.0)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            size = csv_path.stat().st_size if csv_path.is_file() else 0
            if budget_bytes is not None and size > budget_bytes:
                over_budget = ("output exceeded %d bytes (%.1f MB) after %.0f s"
                               % (budget_bytes, size / 1e6, elapsed))
            elif elapsed > timeout:
                over_budget = "exceeded the %.0f s wall budget" % timeout
            if over_budget:
                process.kill()
                stdout, _ = process.communicate()
                break
    log = stdout or ""
    (work / (name + ".log")).write_text(log, encoding="utf-8")
    if over_budget:
        csv_path.unlink(missing_ok=True)
        return {"status": "over_budget", "reason": over_budget, "csv": None, "log": log}
    fatal = [line.strip() for line in log.splitlines()
             if re.search(r"(?i)(netlist error|msg_fatal|simulation aborted|"
                          r"no valid model|unrecognized|can't open|cannot open)", line)]
    warnings = [line.strip() for line in log.splitlines()
                if "warning" in line.lower()]
    if fatal or (require_csv and not csv_path.is_file()):
        return {"status": "failed", "reason": "; ".join(fatal) or "no CSV produced",
                "warnings": warnings, "log": log, "csv": None}
    return {"status": "observed", "warnings": warnings, "log": log,
            "csv": csv_path if csv_path.is_file() else None}


def read_csv(csv_path: Path):
    """Return (axis, [columns...]) from a Xyce FORMAT=CSV print file."""
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("empty Xyce CSV")
    header = lines[0].split(",")
    rows = []
    for line in lines[1:]:
        fields = line.split(",")
        try:
            values = [float(field) for field in fields]
        except ValueError:
            continue
        rows.append(values)
    if not rows:
        raise ValueError("no numeric rows in Xyce CSV")
    width = len(header)
    rows = [row for row in rows if len(row) == width]
    return [row[0] for row in rows], [[row[k] for row in rows] for k in range(1, width)]


def dc_sweep(card_path: Path, model: str, sweep: str, fixed: float, start: float,
             stop: float, step: float, work: Path, name: str,
             level: int = XYCE_LEVEL, timeout: float = 900.0) -> dict:
    """DC sweep of VDS or VGS with the ngspice benches' 4-terminal topology.

    Source and E/substrate are both node 0, matching `MTEST drain gate 0 0 model`.
    `sweep` is "VDS" (fixed = VGS) or "VGS" (fixed = VDS).  The returned
    `current` is |I(sweep source)|, the same convention as the ngspice
    regression's `wrdata sweep.dat i(VDS)`.
    """
    if sweep not in ("VDS", "VGS"):
        raise ValueError("sweep must be VDS or VGS")
    work = Path(work).resolve()
    inc = work / ("%s_xyce.inc" % name)
    inc.write_text(port_card(Path(card_path).read_text(encoding="utf-8"), level),
                   encoding="utf-8")
    if sweep == "VDS":
        sources = ["VGS gate 0 %.10g" % fixed, "VDS drain 0 0"]
        axis_node = "drain"
    else:
        sources = ["VGS gate 0 0", "VDS drain 0 %.10g" % fixed]
        axis_node = "gate"
    lines = [
        "* Xyce DC %s sweep for %s (%s)" % (sweep, model, name),
        '.include "%s"' % inc.name,
        ".OPTIONS DEVICE TEMP=25",
        *sources,
        "MTEST drain gate 0 0 %s W=24u L=6u" % model,
        ".DC %s %.10g %.10g %.10g" % (sweep, start, stop, step),
        # Xyce writes exactly the requested variables, so the swept node is
        # printed explicitly as the axis and I(VDS) is always the drain current.
        ".PRINT DC FORMAT=CSV FILE=%s.csv V(%s) I(VDS)" % (name, axis_node),
        ".END",
    ]
    result = run(lines, name, work, timeout=timeout)
    if result["status"] != "observed":
        return result
    axis, columns = read_csv(result["csv"])
    if len(columns) != 1:
        return {"status": "failed",
                "reason": "expected one printed column, got %d" % len(columns)}
    if len(axis) < 2 or any(b <= a for a, b in zip(axis, axis[1:])):
        return {"status": "failed", "reason": "non-monotone Xyce DC axis"}
    result["axis"] = axis
    result["current"] = [abs(value) for value in columns[0]]
    return result


if __name__ == "__main__":
    import sys
    print("Xyce path: %s" % XYCE)
    print("available: %s" % available())
    print("DYLD_LIBRARY_PATH -> %s" % xyce_env()["DYLD_LIBRARY_PATH"].split(":")[0])
    sys.exit(0 if available() else 1)
