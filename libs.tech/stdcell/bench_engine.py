#!/usr/bin/env python3
"""Engine-parameterised SPICE benches for the ICPS2023_5 standard-cell flows.

Two engines are supported and are never mixed:

* ``ngspice`` -- BSIMSOI 4.4, ``LEVEL=58``; executes the nominal stage-1 card.
* ``xyce``    -- Xyce 7.10 / BSIMSOI 4.5.0, ``LEVEL=70450``; executes the
  experimental ideal-FD bridge card, which is version-limited to that engine.

The module owns the per-engine deck dialect (model line, options, analysis and
output form, measure parsing) and the engine-correct model reference.  Bench
code (``characterize``, ``benchmark_spice``, ``sequential_closure``,
``array_acceptance``) builds only device/waveform statements and stays
engine-agnostic.

Deck dialect differences handled here:

======================  ============================  ==========================
                        ngspice                        Xyce 7.10
======================  ============================  ==========================
model line              ``.lib "<selector>" b4soi``   ``.lib "<ported ref>" b4soi``
options                 ``.options temp/tnom/method`` ``.OPTIONS DEVICE TEMP=25``
                                                      ``.OPTIONS TIMEINT METHOD=``
operating point         ``.op`` + ``.print op``       ``.DC VDUMMY 0 0 1``
transient               ``.tran`` + ``.measure``      ``.TRAN`` + ``.MEASURE``
measure results         stdout ``name = value``       ``<deck>.mt0`` ``NAME = value``
series output           ``.control`` + ``wrdata``     ``.PRINT ... FORMAT=CSV``
======================  ============================  ==========================

An Xyce ``method=gear`` request is honoured (``.OPTIONS TIMEINT METHOD=GEAR``);
ngspice uses ``method=gear`` as before.  Nothing is silently substituted.
"""
from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
XYCE_ADAPTER = ROOT / "libs.tech/ngspice/tools/xyce_b4soi.py"
ENGINES = ("ngspice", "xyce")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
SPICE_NUMBER = r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:meg|[tgkmunpfµ])?"
_SPICE_SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
    "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}


def _spice_number(token: str) -> float:
    """Parse a SPICE scalar with an optional engineering suffix."""
    text = token.strip().rstrip(",").casefold()
    for suffix in sorted(_SPICE_SUFFIX, key=len, reverse=True):
        if text.endswith(suffix) and text[:-len(suffix)]:
            return float(text[:-len(suffix)]) * _SPICE_SUFFIX[suffix]
    return float(text)


def _source_node(body: Sequence[str], source: str) -> str:
    """Return the driven node for a voltage source used as a DC sweep axis."""
    wanted = source.casefold()
    for line in body:
        fields = line.split()
        if len(fields) >= 3 and fields[0].casefold() == wanted:
            return fields[1]
    if wanted.startswith("v") and len(source) > 1:
        return source[1:]
    raise BenchError("cannot resolve DC sweep source %s" % source)


class BenchError(RuntimeError):
    """A bench deck, engine invocation, or measurement result is unusable."""


def _load_xyce_adapter():
    loader = importlib.util.spec_from_file_location("icps_xyce_b4soi", XYCE_ADAPTER)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


def available(engine: str) -> bool:
    """Whether the requested engine can run here (never substitutes another)."""
    if engine == "ngspice":
        return shutil.which("ngspice") is not None
    if engine == "xyce":
        return _load_xyce_adapter().available()
    raise BenchError("engine must be ngspice or xyce")


@dataclass
class Bench:
    """One engine-bound bench context: decks, runs, and measure parsing."""

    binding: dict[str, Any]
    engine: str
    work: Path
    method: str = "trap"
    timeout: float = 900.0
    options_extra: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise BenchError("engine must be ngspice or xyce")
        if self.engine != self.binding.get("engine"):
            raise BenchError(
                "engine %r does not match the bound model card (%r); never mix engines"
                % (self.engine, self.binding.get("engine")))
        if self.method not in {"trap", "gear"}:
            raise BenchError("method must be trap or gear")
        if not available(self.engine):
            raise BenchError("%s is not available in this environment" % self.engine)
        self.work = Path(self.work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._xyce = _load_xyce_adapter() if self.engine == "xyce" else None
        self._reference = self._prepare_reference()
        # Xyce rejects duplicate model definitions, so a deck whose included
        # references already carry a card must not add this bench's line.
        self._emit_model = True

    # ---------------------------------------------------------------- setup --
    def _prepare_reference(self) -> Path:
        if self.engine == "ngspice":
            return Path(self.binding["library_path"])
        card = self.work / "model_card_xyce.inc"
        card.write_text(self._xyce.port_card(
            Path(self.binding["include_path"]).read_text(encoding="utf-8"),
            self.binding["engine_level"]), encoding="utf-8")
        reference = self.work / "model_reference.lib.spice"
        reference.write_text(
            "* Bench model reference for the %s binding (Xyce only).\n"
            ".lib %s\n.include \"%s\"\n.endl %s\n"
            % (self.binding["engine_version"], self.binding["section"],
               card, self.binding["section"]), encoding="utf-8")
        return reference

    def header(self, title: str, *, body: Sequence[str] = ()) -> list[str]:
        """Deck header; the model line is omitted when ``body`` already includes one."""
        lines = ["* %s" % title]
        if self._emit_model and not _includes_define_models(self.work, body):
            lines.append(self.model_line())
        if self.engine == "ngspice":
            lines.append(".options temp=25 tnom=25 method=%s reltol=1e-4 itl4=500" % self.method)
        else:
            lines.append(".OPTIONS DEVICE TEMP=25")
            lines.append(".OPTIONS TIMEINT METHOD=%s RELTOL=1e-4" % self.method.upper())
        lines.extend(self.options_extra)
        return lines

    def _name(self, name: str) -> str:
        self._counter += 1
        return "%s_%02d" % (name, self._counter)

    def model_line(self) -> str:
        """The `.lib` line that binds this bench's engine-correct card."""
        return '.lib "%s" %s' % (self._reference, self.binding["section"])

    # ---------------------------------------------------------------- runner --
    def _run(self, deck: list[str], name: str, *, series: bool = False,
             require_csv: bool = True) -> dict[str, Any]:
        path = self.work / (name + ".cir")
        path.write_text("\n".join(deck) + "\n", encoding="utf-8")
        if self.engine == "ngspice":
            result = subprocess.run([shutil.which("ngspice"), "-b", path.name],
                                    cwd=self.work, capture_output=True, text=True,
                                    errors="replace", timeout=self.timeout)
            log = (result.stdout or "") + (result.stderr or "")
            (self.work / (name + ".log")).write_text(log, encoding="utf-8")
            if result.returncode or re.search(r"(?im)^\s*(error|fatal)\b", log):
                raise BenchError("ngspice failed for %s: %s" % (name, log[-400:]))
            return {"log": log}
        outcome = self._xyce.run(deck, name, self.work, timeout=self.timeout,
                                 require_csv=require_csv)
        if outcome["status"] != "observed":
            raise BenchError("Xyce failed for %s: %s" % (name, outcome.get("reason", "")))
        return {"log": outcome["log"], "csv": outcome["csv"]}

    def _measures(self, log: str, name: str) -> dict[str, float]:
        if self.engine == "xyce":
            mt0 = self.work / (name + ".cir.mt0")
            if not mt0.is_file():
                raise BenchError("missing measure file %s" % mt0.name)
            text = mt0.read_text(encoding="utf-8")
        else:
            text = log
        values = {key.casefold(): float(value) for key, value in
                  re.findall(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(%s)\s*(?:$|\s)" % NUMBER, text)}
        if not values:
            raise BenchError("no measurements parsed for %s" % name)
        if not all(math.isfinite(value) for value in values.values()):
            raise BenchError("nonfinite SPICE measurement in %s" % name)
        return values

    # ------------------------------------------------------------- analyses --
    def operating_point(self, body: Sequence[str], probes: Sequence[str],
                        *, name: str) -> dict[str, float]:
        probes = list(probes)
        base = self._name(name)
        if self.engine == "ngspice":
            deck = [*self.header("operating point", body=body), *body, ".op",
                    ".print op %s" % " ".join(probes), ".end"]
            outcome = self._run(deck, base)
            values = {key.casefold(): float(value) for key, value in
                      re.findall(r"(?m)^\s*([vi]\([^)]+\))\s*=\s*(%s)" % NUMBER,
                                 outcome["log"])}
        else:
            deck = [*self.header("operating point", body=body), *body, "VDUMMY dummy 0 0",
                    ".DC VDUMMY 0 0 1",
                    ".PRINT DC FORMAT=CSV FILE=%s.csv %s" % (base, " ".join(probes)),
                    ".end"]
            outcome = self._run(deck, base)
            table = _read_csv_table(Path(outcome["csv"]))
            values = {name: rows[0] for name, rows in table.items()}
        required = {probe.casefold() for probe in probes}
        missing = sorted(required - set(values))
        if missing:
            raise BenchError("missing operating-point probes: %s" % ", ".join(missing))
        return values

    def dc_sweep(self, body: Sequence[str], source: str, start: float, stop: float,
                 step: float, probe: str, *, name: str) -> tuple[list[float], list[float]]:
        if self.engine == "ngspice":
            deck = [*self.header("dc sweep", body=body), *body, ".control", "set noaskquit",
                    "dc %s %.12g %.12g %.12g" % (source, start, stop, step),
                    "wrdata %s.dat %s" % (self._name(name), probe), "quit", ".endc", ".end"]
            base = deck[-4].split()[1].removesuffix(".dat")
            self._run(deck, base)
            pairs = []
            for line in (self.work / (base + ".dat")).read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) != 2:
                    raise BenchError("unexpected sweep column count: %r" % line)
                pairs.append((float(fields[0]), float(fields[1])))
        else:
            base = self._name(name)
            axis_node = _source_node(body, source)
            axis_probe = "V(%s)" % axis_node
            deck = [*self.header("dc sweep", body=body), *body,
                    ".DC %s %.12g %.12g %.12g" % (source, start, stop, step),
                    ".PRINT DC FORMAT=CSV FILE=%s.csv %s %s" % (base, axis_probe, probe),
                    ".end"]
            outcome = self._run(deck, base)
            axis, columns = _read_csv_rows(Path(outcome["csv"]))
            try:
                values = columns[probe.casefold()]
            except KeyError as error:
                raise BenchError("Xyce sweep CSV lacks probe %s" % probe) from error
            pairs = list(zip(axis, values))
        if len(pairs) < 2:
            raise BenchError("sweep produced %d rows" % len(pairs))
        return [point[0] for point in pairs], [point[1] for point in pairs]

    def transient(self, body: Sequence[str], *, tstep: float, tstop: float,
                  tmax: float | None = None, measures: Sequence[str],
                  probes: Sequence[str] = (), tstart: float = 0.0,
                  name: str) -> dict[str, Any]:
        base = self._name(name)
        measures = list(measures)
        analysis = ".tran %.12g %.12g %.12g" % (tstep, tstop, tstart)
        if tmax is not None:
            analysis += " %.12g" % tmax
        if self.engine == "ngspice":
            if probes:
                # Series output needs the interpreter loop; keep the analysis
                # inside it so the deck is not run twice.
                control = "tran %.12g %.12g %.12g" % (tstep, tstop, tstart)
                if tmax is not None:
                    control += " %.12g" % tmax
                deck = [*self.header("transient", body=body), *body, ".control", "set noaskquit",
                        control, "wrdata %s.dat %s" % (base, " ".join(probes)),
                        "quit", ".endc", ".end"]
                outcome = self._run(deck, base)
                series = _read_wrdata(self.work / (base + ".dat"), probes)
            else:
                deck = [*self.header("transient", body=body), *body, analysis, *measures, ".end"]
                outcome = self._run(deck, base)
                series = None
        else:
            deck = [*self.header("transient", body=body), *body, analysis, *measures]
            if probes:
                deck.append(".PRINT TRAN FORMAT=CSV FILE=%s.csv %s" % (base, " ".join(probes)))
            deck.append(".end")
            outcome = self._run(deck, base, require_csv=bool(probes))
            series = None
            if probes:
                axis, columns = _read_csv_rows(Path(outcome["csv"]))
                series = (axis, {probe: columns[probe.casefold()] for probe in probes})
        return {"measures": self._measures(outcome["log"], base), "series": series}



def _read_csv_table(path: Path) -> dict[str, list[float]]:
    """Return every CSV column (including the first) keyed by lowercased header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise BenchError("empty CSV %s" % path.name)
    header = [field.strip().casefold() for field in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        fields = line.split(",")
        try:
            rows.append([float(field) for field in fields])
        except ValueError:
            continue
    rows = [row for row in rows if len(row) == len(header)]
    if not rows:
        raise BenchError("no numeric rows in %s" % path.name)
    return {name: [row[index] for row in rows] for index, name in enumerate(header)}


def _read_csv_rows(path: Path) -> tuple[list[float], dict[str, list[float]]]:
    """Return (axis, columns) from an engine CSV file (header names lowercased)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise BenchError("empty CSV %s" % path.name)
    header = [field.strip().casefold() for field in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        fields = line.split(",")
        try:
            rows.append([float(field) for field in fields])
        except ValueError:
            continue
    rows = [row for row in rows if len(row) == len(header)]
    if not rows:
        raise BenchError("no numeric rows in %s" % path.name)
    axis = [row[0] for row in rows]
    columns = {name: [row[index] for row in rows] for index, name in enumerate(header[1:], start=1)}
    return axis, columns


def _read_wrdata(path: Path, probes: Sequence[str]) -> tuple[list[float], dict[str, list[float]]]:
    """Parse ngspice `wrdata` output: each vector is written as scale/value pairs."""
    if not path.is_file():
        raise BenchError("missing ngspice series file %s" % path.name)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 2 * len(probes):
            raise BenchError("unexpected wrdata column count: %r" % line)
        rows.append([float(field) for field in fields])
    axis = [row[0] for row in rows]
    columns = {}
    for index, probe in enumerate(probes):
        values = [row[2 * index + 1] for row in rows]
        if any(abs(row[2 * index] - time) > 1e-12 for row, time in zip(rows, axis)):
            raise BenchError("wrdata scales disagree for %s" % probe)
        columns[probe] = values
    return axis, columns


def bench_for(*, binding: dict[str, Any] | None = None, engine: str | None = None,
              work: str | Path, method: str = "trap", timeout: float = 900.0) -> Bench:
    """Build a bench; the default follows the *configured* model binding.

    The configured binding is the experimental ideal-FD bridge card, so the
    default bench runs Xyce.  Pass ``binding=model_binding.nominal()``
    explicitly to run the stage-1 card on ngspice.
    """
    import model_binding

    selected_binding = binding if binding is not None else model_binding.load()
    return Bench(binding=selected_binding, engine=engine or selected_binding["engine"],
                 work=Path(work), method=method, timeout=timeout)


_NETLIST_MODEL_MARKERS = (
    ("fd", ("b4soi_fd_bridge_experimental", "model_card_xyce", "model_reference.lib")),
    ("nominal", ("icps2023_5.lib.spice", "icps2023_5_b4soi.inc")),
)


def bench_for_netlist(netlist: str | Path, *, work: str | Path,
                      method: str = "trap", timeout: float = 900.0) -> Bench:
    """Bind the engine from the model line a netlist (or its includes) carries.

    This is how the flows avoid mixing engines: an assembled postlayout netlist
    bound to the FD card runs on Xyce; a netlist carrying the stage-1 selector
    runs on ngspice; anything unrecognised is refused rather than guessed.
    """
    import model_binding

    path = Path(netlist)
    if not path.is_file():
        raise BenchError("netlist does not exist: %s" % path)
    seen: set[Path] = set()
    queue = [path]
    targets: list[str] = []
    while queue and len(seen) < 32:
        current = queue.pop(0).resolve()
        if current in seen:
            continue
        seen.add(current)
        try:
            text = current.read_text(encoding="utf-8")
        except OSError as error:
            raise BenchError("cannot read %s: %s" % (current, error)) from error
        for match in re.finditer(r'(?im)^\s*\.(?:lib|include)\s+"?([^"\s]+)"?', text):
            target = match.group(1)
            targets.append(target)
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = current.parent / candidate
            if candidate.is_file() and len(seen) < 32:
                queue.append(candidate)
    for name, markers in _NETLIST_MODEL_MARKERS:
        if any(marker in target for target in targets for marker in markers):
            binding = (model_binding.load() if name == "fd" else model_binding.nominal())
            return Bench(binding=binding, engine=binding["engine"], work=Path(work),
                         method=method, timeout=timeout)
    raise BenchError("no recognised model line in %s; refusing to guess an engine" % path)


# ---------------------------------------------------------------------------
# Legacy-deck translation
#
# The repository's benches were written against ngspice idioms (`.control` with
# `alter`/`op`/`print`, `wrdata`, `.measure`).  Rewriting every bench twice
# would duplicate every metric, so the ngspice decks are *translated* here:
# the same deck text is executed through the native Bench primitives and the
# translator synthesises the log/dataset artefacts the existing parsers read.
# The ngspice path is unchanged and never translated.
# ---------------------------------------------------------------------------
_MEASURE_RE = re.compile(r"^\s*\.measure\s+tran\s+(\S+)\s+(.*)$", re.IGNORECASE)
_ALTER_RE = re.compile(r"^\s*alter\s+(\S+)\s+(%s)\s*$" % SPICE_NUMBER, re.IGNORECASE)
_ECHO_RE = re.compile(r"^\s*echo\s+(\S+)\s*$", re.IGNORECASE)
_PRINT_RE = re.compile(r"^\s*print\s+(.+?)\s*$", re.IGNORECASE)
_PROBE_RE = re.compile(r"^\s*([vi]\([^()]+\))\s*$", re.IGNORECASE)


def run_legacy_deck(bench: "Bench", deck: Sequence[str], *, name: str,
                    output_dir: str | Path | None = None) -> str:
    """Execute a legacy ngspice-style bench deck on the bound engine.

    Only the execution path differs; the deck's analyses, measures and
    thresholds stay exactly as the bench wrote them.  Returns the log text the
    existing parsers already understand.
    """
    if bench.engine != "xyce":
        raise BenchError("run_legacy_deck is the Xyce translation path; "
                         "run ngspice decks directly")
    body: list[str] = []
    control: list[str] = []
    in_control = False
    for raw in deck:
        stripped = raw.strip()
        if not stripped:
            continue
        low = stripped.casefold()
        if low.startswith(".control"):
            in_control = True
            continue
        if low.startswith(".endc"):
            in_control = False
            continue
        if in_control:
            if low == "quit" or low.startswith("set "):
                continue
            control.append(stripped)
            continue
        if low.startswith((".tran", ".measure", ".op", ".print", ".end")):
            if not low.startswith(".end"):
                control.append(stripped)   # analysis/measure lines drive the translation
            continue
        if low.startswith(".lib ") or low.startswith(".options"):
            continue
        body.append(raw.rstrip())

    if control:
        return "\n".join(_translate_control(bench, body, control, name,
                                              output_dir=output_dir)) + "\n"
    raise BenchError("legacy deck has no translatable analysis")



def _includes_define_models(work: Path, body: Sequence[str]) -> bool:
    """Whether an included reference already carries a model card or selector."""
    for line in body:
        match = re.match(r'^\s*\.include\s+"?([^"\s]+)"?', line, re.IGNORECASE)
        if not match:
            continue
        path = Path(match.group(1))
        if not path.is_absolute():
            path = work / path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"(?im)^\s*\.(lib|model)\b", text):
            return True
    return False


def _alter_source(body: Sequence[str], source: str, value: str) -> str:
    """Return the declared voltage source with its DC value overridden."""
    wanted = source.casefold()
    for line in body:
        fields = line.split()
        if len(fields) >= 4 and fields[0].casefold() == wanted:
            return " ".join([*fields[:3], "%.12g" % _spice_number(value), *fields[4:]])
    raise BenchError("alter references undeclared source %s" % source)


def _translate_control(bench: "Bench", body: Sequence[str], control: Sequence[str],
                       name: str, *, output_dir: str | Path | None = None) -> list[str]:
    """Translate a `.control` block: per-row operating points, sweeps, or series."""
    if not control:
        raise BenchError("empty .control block")
    folded = [(line, line.casefold().lstrip(".")) for line in control]
    dc = next((line for line, low in folded if low.startswith("dc ")), None)
    tran = next((line for line, low in folded if low.startswith("tran ")), None)
    wrdata = next((line for line, low in folded if low.startswith("wrdata ")), None)
    measures = [line for line in control if _MEASURE_RE.match(line)]
    if dc is not None:
        fields = dc.split()
        if len(fields) != 5 or wrdata is None:
            raise BenchError("unsupported .control sweep: %r / %r" % (dc, wrdata))
        source, start, stop, step = fields[1], *(_spice_number(value) for value in fields[2:5])
        probe = wrdata.split()[2]
        axis, values = bench.dc_sweep(body, source, start, stop, step, probe,
                                      name=name + "_sweep")
        target = (Path(output_dir) if output_dir is not None else bench.work) / wrdata.split()[1]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join("%.10g %.10g\n" % (x, y) for x, y in zip(axis, values)),
                          encoding="utf-8")
        return []
    if tran is not None:
        log = _translate_tran(bench, body, [tran, *measures], name)
        if wrdata is not None:
            raise BenchError("series output inside a transient .control block is not "
                             "translated; request it through Bench.transient(probes=...)")
        return log
    # Row-wise operating points: the deck sweeps rows with `alter Vx v`, labels
    # each row with `echo NAME` and names its probes with `print`.  Source values
    # may appear before or after the echo that opens their row.
    rows: list[list] = []
    pending_sources: list[str] = []
    current: list | None = None
    for line in control:
        alter = _ALTER_RE.match(line)
        echo = _ECHO_RE.match(line)
        print_match = _PRINT_RE.match(line)
        if alter:
            pending_sources.append(_alter_source(body, alter.group(1), alter.group(2)))
        elif echo:
            if current is not None:
                rows.append(current)
            current = [echo.group(1), list(pending_sources), []]
            pending_sources = []
        elif print_match:
            probes = [probe.group(1) for probe in
                      (_PROBE_RE.match(item) for item in print_match.group(1).split())
                      if probe]
            if current is None:
                current = ["ROW", list(pending_sources), []]
                pending_sources = []
            current[2].extend(probes)
    if current is not None:
        rows.append(current)
    if not rows:
        raise BenchError("unsupported .control block: %r" % list(control))
    log: list[str] = []
    for label, sources, probes in rows:
        if not probes:
            raise BenchError("control row %s has no printed probe" % label)
        # `alter Vx v` overrides the body's declaration of Vx: keep exactly one
        # source per altered pin, otherwise a duplicate device leaves the row's
        # value unapplied (Xyce errors; ngspice keeps the first definition).
        altered = {line.split()[0].casefold() for line in sources}
        row_body = [line for line in body
                    if not (line.split() and line.split()[0].casefold() in altered)]
        values = bench.operating_point([*row_body, *sources], probes,
                                       name=name + "_" + label)
        log.append(label)
        log.extend("%s = %.10g" % (probe, values[probe.casefold()]) for probe in probes)
    return log


def _translate_tran(bench: "Bench", body: Sequence[str], analyses: Sequence[str],
                    name: str) -> list[str]:
    """Translate `.tran` + `.measure tran` lines (ngspice measure semantics)."""
    tran = next(line for line in analyses if line.strip().casefold().startswith(".tran"))
    fields = tran.split()[1:]
    if len(fields) < 2:
        raise BenchError("unsupported .tran line: %r" % tran)
    tstep, tstop = _spice_number(fields[0]), _spice_number(fields[1])
    tstart = _spice_number(fields[2]) if len(fields) > 2 else 0.0
    tmax = _spice_number(fields[3]) if len(fields) > 3 else None
    measures = [line.strip() for line in analyses if _MEASURE_RE.match(line)]
    result = bench.transient(body, tstep=tstep, tstop=tstop, tmax=tmax,
                             tstart=tstart, measures=measures, name=name)
    return ["%s = %.10g" % (key, value) for key, value in sorted(result["measures"].items())]
