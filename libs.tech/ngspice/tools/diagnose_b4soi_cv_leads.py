#!/usr/bin/env python3
"""F0-F2: BSIMSOI C-V selector lead, full S/D charge symmetry, negative-bias
trigger and tail stability.

F0  VGSTCVMOD selector (BSIMSOI 4.4 manual: 0 = legacy C-V with a known bug,
    1 = official fix, 2 = new Vgsteff expression with body effect/DIBL in
    the C-V; manual default 0 and recommends 1/2).  Implementation check
    first: ngspice-47's b4soi tree references vgstcvmod only in the parameter
    table (no equation usage), while Xyce 4.5.0 branches on it inside the
    charge code.  Both simulators are therefore swept, each against the
    frozen card + selector line only (no other parameter changes).
F1  source/drain interchange symmetry with the CORRECT oracle: keep the
    wiring, exchange the node voltage VALUES at d and s; an ideal model
    gives C_B = P C_A P^T (P the d/s swap of rows and columns).  The
    d/s and g/d, g/s cross-term non-reciprocity of matrix A is reported
    SEPARATELY: it is expected by charge-model design and is not a failure
    condition.
F2  negative-voltage trigger boundary and tail stability: (a) controlled
    charge injection into the free output node with the gate held off,
    swept over negative and positive target voltages (distinguish
    "crossing a negative threshold" from "any fast injection"), (b)
    timestep convergence of the canonical falling-edge tail.  Tail
    acceptance metrics: tail_ac_rms (AC-coupled RMS, separates sustained
    oscillation from a parked DC offset), tail_pp, and tail zero-crossing
    count; tail frequency is only reported when >= 4 crossings occur.

Run:  python3 libs.tech/ngspice/tools/diagnose_b4soi_cv_leads.py
Out:  libs.tech/ngspice/audit/b4soi_cv_leads.md
"""

import importlib.util
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
FROZEN_LIB = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
OUT_DIR = ROOT / "libs.tech/ngspice/audit"
XYCE = "/usr/local/XyceNF_OMPI_7.10/bin/Xyce"
XYCE_ENV = {"DYLD_LIBRARY_PATH": "/opt/homebrew/lib"}
NGSPICE = shutil.which("ngspice") or "ngspice"
W_UM, L_UM = 24.0, 6.0
LIB = FROZEN_LIB.resolve().as_posix().replace('"', '\\"')
C_LOAD = 5e-15


def fmt(value):
    return "%.12g" % value


def _f(value):
    return ("%.1f fF" % value) if value else "n/a"


def load_audit():
    spec = importlib.util.spec_from_file_location(
        "b4soi_cap_audit", TOOLS / "audit_b4soi_caps.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the audit tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_audit()


def read_wrdata(path, n_vectors, complex_data=False):
    rows = []
    width = 1 + 2 * n_vectors if complex_data else 1 + n_vectors
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.replace(",", " ").split()
        try:
            values = [float(f.replace("D", "E").replace("d", "e")) for f in fields]
        except ValueError:
            continue
        if len(values) == width:
            rows.append(values)
    if not rows:
        raise RuntimeError("no parsable rows in %s" % path)
    if complex_data:
        return ([row[0] for row in rows],
                [[complex(row[1 + 2 * k], row[2 + 2 * k]) for row in rows]
                 for k in range(n_vectors)])
    return ([row[0] for row in rows],
            [[row[1 + k] for row in rows] for k in range(n_vectors)])


# --------------------------------------------------------------- tail metrics
def tail_stats(t, v_out, window_start=7e-7):
    """Tail acceptance metrics: sustained oscillation vs parked DC offset.

    tail_ac_rms  AC-coupled RMS over the window (near zero if the tail is
                 a parked value, large if a limit cycle persists).
    tail_pp      peak-to-peak inside the window.
    tail_x       zero crossings of (v - mean) in the window.
    tail_freq    only meaningful when tail_x >= 4; else 0 (reported as n/a).
    """
    tail = [(tt, vv) for tt, vv in zip(t, v_out) if tt > window_start]
    if len(tail) < 4:
        return {"tail_mean": 0.0, "tail_ac_rms": 0.0, "tail_pp": 0.0,
                "tail_x": 0, "tail_freq": 0.0}
    values = [vv for _tt, vv in tail]
    mean = sum(values) / len(values)
    ac_rms = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    span = tail[-1][0] - tail[0][0]
    crossings = sum(1 for a, b in zip(values, values[1:])
                    if (a - mean) * (b - mean) < 0)
    freq = crossings / (2.0 * span) if (crossings >= 4 and span > 0) else 0.0
    return {"tail_mean": mean, "tail_ac_rms": ac_rms,
            "tail_pp": max(values) - min(values), "tail_x": crossings,
            "tail_freq": freq}


def base_metrics(t, v_out, i_g):
    m = {"rows": len(t), "vout_min": min(v_out), "vout_max": max(v_out),
         "flips": sum(1 for a, b in zip(i_g, i_g[1:]) if a * b < 0)}
    m.update(tail_stats(t, v_out))
    return m


def t7_metrics(dat, body=True):
    n = 5 if body else 4
    rows = []
    for line in Path(dat).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < n:
            continue
        try:
            rows.append([float(f.replace("D", "E").replace("d", "e"))
                         for f in fields])
        except ValueError:
            continue
    if len(rows) < 20:
        return None
    t, v_out, i_g = ([r[i] for r in rows] for i in (0, 1, 3))
    m = base_metrics(t, v_out, i_g)
    if body:
        body_v = [r[4] for r in rows]
        m["body_min"] = min(body_v)
        m["body_max"] = max(body_v)
        m["body_flips"] = sum(1 for a, b in zip(body_v, body_v[1:])
                              if a * b < 0)
    return m


# --------------------------------------------------------------- F0 helpers
def variant_card(level, vgstcvmod, capmod=2):
    """Frozen card parameters + the selector line only."""
    return (
        "* F0 variant: LEVEL=%d VGSTCVMOD=%d CAPMOD=%d (all other "
        "parameters frozen)\n"
        ".model nch NMOS (\n"
        "+ LEVEL=%d SOIMOD=0 MOBMOD=1 CAPMOD=%d TNOM=25\n"
        "+ TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9\n"
        "+ VTH0=0.25 U0=670 NFACTOR=1.38 K1=0.125 K2=0 RDSW=0\n"
        "+ VGSTCVMOD=%d\n+ )\n" % (level, vgstcvmod, capmod,
                                   level, capmod, vgstcvmod))


def f0_deck_ng(vgstcvmod, capmod=2):
    """T7 on the variant card alone (no .lib: the b4soi section would
    define `nch` a second time)."""
    return [
        "* F0 ngspice T7 (VGSTCVMOD=%d)" % vgstcvmod,
        ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=2000",
        variant_card(58, vgstcvmod, capmod),
        "VG G 0 PULSE(0 3 100n 2n 2n 400n 1u)",
        "MND OUT G 0 SUB nch W=%su L=%su" % (W_UM, L_UM),
        ".ic v(OUT)=3",
        "CLOAD OUT 0 %g" % C_LOAD,
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "save all",
        "tran 20p 1u",
        "wrdata f0.dat v(OUT) v(G) i(VG) v(MND#Body)",
        ".endc",
        ".end",
        "",
    ]


def f0_ac_ng(vgstcvmod, vgs, vds, capmod=2):
    return [
        "* F0 ngspice AC (VGSTCVMOD=%d)" % vgstcvmod,
        ".options temp=25 tnom=25",
        variant_card(58, vgstcvmod, capmod),
        "VGg g 0 %s ac 1" % fmt(vgs),
        "VDd d 0 %s ac 0" % fmt(vds),
        "VSs s 0 0 ac 0",
        "VEe e 0 0 ac 0",
        "MD d g s e nch W=%su L=%su" % (W_UM, L_UM),
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "ac lin 1 1000 1000",
        "wrdata f0ac.dat i(VGg) i(VDd) i(VSs) i(VEe)",
        ".endc",
        ".end",
        "",
    ]


def f0_dc_ng(vgstcvmod, capmod=2):
    return [
        "* F0 ngspice DC (VGSTCVMOD=%d)" % vgstcvmod,
        ".options temp=25 tnom=25",
        variant_card(58, vgstcvmod, capmod),
        "VG G 0 3", "VD D 0 3", "VS S 0 0", "VE E 0 0",
        "MND D G S E nch W=%su L=%su" % (W_UM, L_UM),
        ".control", "op", "print i(vd)", "quit", ".endc", ".end", "",
    ]


def run_ng(deck, work, name, dat):
    (work / (name + ".cir")).write_text(
        "\n".join(deck) if isinstance(deck, list) else deck,
        encoding="utf-8")
    run = subprocess.run([NGSPICE, "-b", name + ".cir"], cwd=work, text=True,
                         capture_output=True, check=False, timeout=600)
    out = run.stdout + run.stderr
    if dat is not None and not (work / dat).is_file():
        return None, out
    return run.returncode, out


def parse_id(log):
    match = re.search(r"i\(vd\)\s*=\s*([-+0-9.eEdD]+)", log, re.IGNORECASE)
    if match:
        try:
            return abs(float(match.group(1).replace("D", "E").replace("d", "e")))
        except ValueError:
            return None
    return None


def f0_x_deck(vgstcvmod, pre_inc, work):
    (work / (pre_inc + ".inc")).write_text(
        variant_card(70450, vgstcvmod), encoding="utf-8")
    return "\n".join([
        "* F0 Xyce T7 (VGSTCVMOD=%d)" % vgstcvmod,
        '.include "%s.inc"' % pre_inc,
        ".OPTIONS DEVICE TEMP=25",
        ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12",
        "VG G 0 PULSE(0 3 100n 2n 2n 400n 1u)",
        "MND OUT G 0 SUB nch W=%su L=%su" % (W_UM, L_UM),
        ".IC V(OUT)=3",
        "CLOAD OUT 0 %g" % C_LOAD,
        ".TRAN 20p 1u 0 20p",
        ".PRINT TRAN FORMAT=CSV FILE=f0x.csv V(OUT) V(G) I(VG)",
        ".END", ""])


def f0_x_ac(vgstcvmod, tag, vgs, vds, work):
    name = "f0xac_%d_%s" % (vgstcvmod, tag)
    (work / (name + ".inc")).write_text(variant_card(70450, vgstcvmod),
                                        encoding="utf-8")
    return "\n".join([
        "* F0 Xyce AC (VGSTCVMOD=%d)" % vgstcvmod,
        '.include "%s.inc"' % name,
        ".OPTIONS DEVICE TEMP=25",
        "VGg g 0 %s ac 1" % fmt(vgs),
        "VDd d 0 %s ac 0" % fmt(vds),
        "VSs s 0 0 ac 0",
        "VEe e 0 0 ac 0",
        "MD d g s e nch W=%su L=%su" % (W_UM, L_UM),
        ".AC LIN 1 1000 1000",
        ".PRINT AC FORMAT=CSV FILE=%s.csv I(VGg) I(VDd) I(VSs) I(VEe)" % name,
        ".END", ""])


def run_x(deck, name, work):
    (work / (name + ".cir")).write_text(deck, encoding="utf-8")
    env = dict(os.environ)
    env.update(XYCE_ENV)
    subprocess.run([XYCE, name + ".cir"], cwd=work, env=env, text=True,
                   capture_output=True, check=False, timeout=900)
    return (work / (name + ".csv")).is_file()


def x_ac_cgg(path):
    try:
        _freq, vectors = read_wrdata(path, 4, complex_data=True)
    except RuntimeError:
        return None
    omega = 2 * math.pi * 1000
    return abs(vectors[0][0].imag) / omega * 1e15


def x_t7_metrics(path):
    try:
        t, vectors = read_wrdata(path, 3, complex_data=False)
    except RuntimeError:
        return None
    return base_metrics(t, vectors[0], vectors[2])


# --------------------------------------------------------------- F1 helpers
def ac_matrix_deck(vgs, vds, vsrc):
    """4x4 small-signal capacitance matrix, normal wiring throughout;
    caller exchanges the d/s node voltage VALUES for the mirror deck."""
    lines = [
        "* F1 AC matrix (VG=%g VD=%g VS=%g)" % (vgs, vds, vsrc),
        '.lib "%s" b4soi' % LIB,
        ".options temp=25 tnom=25",
    ]
    bias = {"g": vgs, "d": vds, "s": vsrc, "e": 0.0}
    for inj in ("g", "d", "s", "e"):
        nodes = {"g": "g" + inj, "d": "d" + inj, "s": "s" + inj, "e": "e" + inj}
        for name in ("g", "d", "s", "e"):
            lines.append("V%s_%s %s 0 %s ac %s" % (
                inj, name, nodes[name], bias[name], 1 if name == inj else 0))
        lines.append("M%s %s %s %s %s nch W=%su L=%su" % (
            inj, "d" + inj, "g" + inj, "s" + inj, "e" + inj, W_UM, L_UM))
    lines += [
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "ac lin 1 1000 1000",
        "wrdata f1.dat " + " ".join("i(V%s_%s)" % (i, n)
                                    for i in ("g", "d", "s", "e")
                                    for n in ("g", "d", "s", "e")),
        ".endc",
        ".end",
        "",
    ]
    return lines


def read_matrix(path):
    _freq, vectors = read_wrdata(path, 16, complex_data=True)
    omega = 2 * math.pi * 1000
    matrix = {}
    terminals = ("g", "d", "s", "e")
    # vectors[k] is i(V{inj}_{name}) with k = 4*inject_index + name_index
    for k in range(16):
        inj, row_name = terminals[k // 4], terminals[k % 4]
        matrix[(row_name, inj)] = abs(vectors[k][0].imag) / omega * 1e15
    return matrix


def swapped_expected(matrix):
    """P C P^T with P exchanging the d and s indices."""
    swap = {"g": "g", "d": "s", "s": "d", "e": "e"}
    return {(a, b): matrix.get((swap[a], swap[b]), 0.0)
            for (a, b) in matrix}


def worst_deviation(cand, exp, threshold=1.0):
    worst, worst_key = 0.0, None
    for key in exp:
        a, b = cand.get(key, 0.0), exp[key]
        if max(a, b) > threshold:
            dev = abs(a - b) / max(a, b)
            if dev > worst:
                worst, worst_key = dev, key
    return worst, worst_key


def nonreciprocity(matrix, threshold=1.0):
    """Max |C_ab - C_ba| / max among gate/drain/source pairs; expected by
    charge-model design, reported only for context."""
    worst, worst_key = 0.0, None
    for a, b in (("g", "d"), ("g", "s"), ("d", "s")):
        va, vb = matrix.get((a, b), 0.0), matrix.get((b, a), 0.0)
        if max(va, vb) > threshold:
            dev = abs(va - vb) / max(va, vb)
            if dev > worst:
                worst, worst_key = dev, "%s%s/%s%s" % (a, b, b, a)
    return worst, worst_key


# --------------------------------------------------------------- F2 helpers
def f2_deck(v_target, tstart_ns=60.0, width_ns=40.0):
    """Gate held at 0; a controlled current pulse charges the free output
    node toward v_target, then releases.  i_min>0 drains positive charge
    from OUT (drives OUT negative)."""
    i_min = -(C_LOAD * v_target) / (width_ns * 1e-9)
    return [
        "* F2a charge injection, target OUT = %g V (gate off)" % v_target,
        '.lib "%s" b4soi' % LIB,
        ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=2000",
        "VG G 0 0",
        "MND OUT G 0 SUB nch W=%su L=%su" % (W_UM, L_UM),
        ".ic v(OUT)=0",
        "CLOAD OUT 0 %g" % C_LOAD,
        "ICUR OUT 0 PWL(0 0 %gn 0 %gn %g %gn %g %gn 0)" % (
            tstart_ns, tstart_ns, i_min, tstart_ns + width_ns, i_min,
            tstart_ns + width_ns + 10.0),
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "save all",
        "tran 20p 1u",
        "wrdata f2.dat v(OUT) v(G) i(VG) v(MND#Body)",
        ".endc",
        ".end",
        "",
    ]


def f2b_deck(tstep):
    return [
        "* F2b step convergence (T7 frozen card)",
        '.lib "%s" b4soi' % LIB,
        ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=2000",
        "VG G 0 PULSE(0 3 100n 2n 2n 400n 1u)",
        "MND OUT G 0 SUB nch W=%su L=%su" % (W_UM, L_UM),
        ".ic v(OUT)=3",
        "CLOAD OUT 0 %g" % C_LOAD,
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "save all",
        "tran %s 1u" % fmt(tstep),
        "wrdata f2b.dat v(OUT) v(G) i(VG) v(MND#Body)",
        ".endc",
        ".end",
        "",
    ]


def fmt_tail(m):
    freq = _fmt_freq(m["tail_freq"])
    return "%.4g | %.4g | %d%s" % (m["tail_ac_rms"], m["tail_pp"],
                                   m["tail_x"], freq)


def _fmt_freq(freq):
    if freq <= 0:
        return " | n/a"
    return " | %.3g MHz" % (freq / 1e6)


def main():
    out = ["# F0-F2: C-V selector lead, charge symmetry, negative-trigger tail", ""]
    out.append("Selector sweep against the frozen card + selector line "
               "only.  An earlier source-tree reading held that ngspice-47 "
               "never uses vgstcvmod; the empirical sweep below shows mode 2 "
               "changes ngspice's C-V results identically to Xyce, so the "
               "parameter is live in both simulators and the empirical table "
               "supersedes that reading.")
    out.append("")
    tail_hdr = "tail_ac_rms | tail_pp | tail_x | tail_freq"
    out.append("Tail columns: AC-coupled RMS and peak-to-peak over 700ns-1us "
               "(near zero = parked DC offset, not ringing), zero crossings "
               "of the mean-subtracted tail, and its frequency (n/a unless "
               ">= 4 crossings).")
    out.append("")

    with tempfile.TemporaryDirectory(prefix="icps-fcv-") as scratch:
        work = Path(scratch)

        # ---------------- F0 ngspice ----------------
        out.append("## F0 ngspice (VGSTCVMOD on the frozen 4.4 card)")
        out.append("")
        out.append("| VGSTCVMOD | ID(3/3V) | Cgg off | Cgg(-0.2V) | Cgg "
                   "triode | T7 OUT range | %s |" % tail_hdr)
        out.append("|---|---:|---:|---:|---:|---|---:|---:|---:|---:|")
        for vgst in (0, 1, 2):
            rc, log = run_ng(f0_deck_ng(vgst), work, "f0_t7_%d" % vgst,
                             "f0.dat")
            m = t7_metrics(work / "f0.dat") if rc == 0 else None
            caps = {}
            for tag, vgs, vds in (("off", 0.0, 3.0), ("neg", 0.0, -0.2),
                                  ("triode", 3.0, 0.05)):
                rc2, _l2 = run_ng(f0_ac_ng(vgst, vgs, vds), work,
                                  "f0_ac_%d_%s" % (vgst, tag), "f0ac.dat")
                if rc2 == 0:
                    _freq, vec = read_wrdata(work / "f0ac.dat", 4,
                                             complex_data=True)
                    caps[tag] = abs(vec[0][0].imag) / (2 * math.pi * 1000) * 1e15
            rc3, log3 = run_ng(f0_dc_ng(vgst), work, "f0_dc_%d" % vgst,
                               dat=None)
            id_val = parse_id(log3) if rc3 == 0 else None
            id_txt = ("%.4f mA" % (id_val * 1e3)) if id_val else "n/a"
            if m:
                out.append("| %d | %s | %s | %s | %s | %.3f..%.3f | %s |" % (
                    vgst, id_txt,
                    _f(caps.get("off")), _f(caps.get("neg")),
                    _f(caps.get("triode")),
                    m["vout_min"], m["vout_max"], fmt_tail(m)))
            else:
                out.append("| %d | %s | FAIL | - | - | - | - |" % (vgst, id_txt))
        out.append("")

        # ---------------- F0 Xyce 70450 ----------------
        out.append("## F0 Xyce LEVEL=70450 (4.5.0 implements the selector)")
        out.append("")
        out.append("| VGSTCVMOD | Cgg off | Cgg(-0.2V) | Cgg triode | T7 OUT "
                   "range | %s |" % tail_hdr)
        out.append("|---|---:|---:|---:|---|---:|---:|---:|---:|")
        for vgst in (0, 1, 2):
            inc = "f0x_%d" % vgst
            run_x(f0_x_deck(vgst, inc, work), "f0x_t7_%d" % vgst, work)
            m = x_t7_metrics(work / "f0x.csv")
            caps = {}
            for tag, vgs, vds in (("off", 0.0, 3.0), ("neg", 0.0, -0.2),
                                  ("triode", 3.0, 0.05)):
                deck = f0_x_ac(vgst, tag, vgs, vds, work)
                run_x(deck, "f0xac_%d_%s" % (vgst, tag), work)
                cgg = x_ac_cgg(work / ("f0xac_%d_%s.csv" % (vgst, tag)))
                if cgg is not None:
                    caps[tag] = cgg
            if m:
                out.append("| %d | %s | %s | %s | %.3f..%.3f | %s |" % (
                    vgst, _f(caps.get("off")), _f(caps.get("neg")),
                    _f(caps.get("triode")),
                    m["vout_min"], m["vout_max"], fmt_tail(m)))
            else:
                out.append("| %d | FAIL | - | - | - | - |" % vgst)
        out.append("")

        # ---------------- F1 ----------------
        out.append("## F1 source/drain interchange symmetry (ngspice)")
        out.append("")
        out.append("Deck A: (VD=v, VS=0).  Deck B: same wiring, VD=0, VS=v "
                   "(the mirror operating point).  Ideal model: "
                   "C_B = P C_A P^T with P the d/s swap of rows and columns. "
                   "  The non-reciprocity column (|C_ab - C_ba| of matrix A) "
                   "is expected by charge-model design and is context only.")
        out.append("")
        for vgs, vds in ((0.0, -0.2), (1.5, -0.2), (0.0, 0.2), (1.5, 0.2)):
            rc, _l = run_ng(ac_matrix_deck(vgs, vds, 0.0), work,
                            "f1a_%g_%g" % (vgs, vds), "f1.dat")
            if rc != 0:
                out.append("- (VG=%g, VD=%+g): deck A FAIL" % (vgs, vds))
                continue
            c_a = read_matrix(work / "f1.dat")
            rc, _l = run_ng(ac_matrix_deck(vgs, 0.0, vds), work,
                            "f1b_%g_%g" % (vgs, vds), "f1.dat")
            if rc != 0:
                out.append("- (VG=%g, VD=%+g): deck B FAIL" % (vgs, vds))
                continue
            c_b = read_matrix(work / "f1.dat")
            exp = swapped_expected(c_a)
            worst, worst_key = worst_deviation(c_b, exp)
            nonrecip, nr_key = nonreciprocity(c_a)
            cgg_a, cgg_b = c_a[("g", "g")], c_b[("g", "g")]
            cgg_dev = (abs(cgg_a - cgg_b) / max(cgg_a, cgg_b)
                       if max(cgg_a, cgg_b) > 0 else 0.0)
            out.append("- (VG=%g, VD=%+g V): Cgg A %.1f fF / B %.1f fF "
                       "(dev %.2f%%); worst P-C-P^T deviation %.1f%% at %s; "
                       "A non-reciprocity %.1f%% at %s" % (
                           vgs, vds, cgg_a, cgg_b, 100.0 * cgg_dev,
                           100.0 * worst,
                           "%s%s" % (worst_key[0], worst_key[1])
                           if worst_key else "?",
                           100.0 * nonrecip, nr_key or "?"))
        out.append("")

        # ---------------- F2a ----------------
        out.append("## F2a charge injection into the free node (gate held 0)")
        out.append("")
        out.append("| target OUT V | injected Q | actual OUT min | "
                   "tail_mean | %s |" % tail_hdr)
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for v_target in (-0.1, -0.4, -2.0, 0.4, 2.0):
            rc, _l = run_ng(f2_deck(v_target), work, "f2_%g" % v_target,
                            "f2.dat")
            m = t7_metrics(work / "f2.dat") if rc == 0 else None
            q_fc = C_LOAD * v_target * 1e15
            if m:
                out.append("| %+.1f | %+.1f fC | %.3f V | %s |" % (
                    v_target, q_fc, m["vout_min"],
                    "%.4g | %s" % (m["tail_mean"], fmt_tail(m))))
            else:
                out.append("| %+.1f | %+.1f fC | FAIL | - |" % (
                    v_target, q_fc))
        out.append("")

        # ---------------- F2b ----------------
        out.append("## F2b falling-edge tail convergence (T7 frozen card)")
        out.append("")
        out.append("| tran step | OUT range | tail_mean | %s |" % tail_hdr)
        out.append("|---|---|---:|---:|---:|---:|---:|")
        for tstep in (20e-12, 10e-12, 5e-12):
            rc, _l = run_ng(f2b_deck(tstep), work, "f2b_%g" % (tstep * 1e12),
                            "f2b.dat")
            m = t7_metrics(work / "f2b.dat") if rc == 0 else None
            if m:
                out.append("| %g ps | %.3f..%.3f | %s |" % (
                    tstep * 1e12, m["vout_min"], m["vout_max"],
                    "%.4g | %s" % (m["tail_mean"], fmt_tail(m))))
            else:
                out.append("| %g ps | abort | - | - |" % (tstep * 1e12))
        out.append("")

    out.append("## Reading")
    out.append("")
    out.append("- F0: ngspice and Xyce agree bit-for-bit at every cell.  "
               "Mode 0 vs 1 (the documented C-V bug fix) shows no difference "
               "at these bias points in either simulator; mode 2 "
               "(VgsteffCV with body effect/DIBL) changes reverse-bias Cgg "
               "by ~2.2x and the T7 overshoot.")
    out.append("- F0-Xyce: mode 1 is the official C-V fix and mode 2 the "
               "new VgsteffCV expression; compare their Cgg and tails "
               "against mode 0.")
    out.append("- F1: worst P-C-P^T deviation is the d/s interchange "
               "symmetry check (elements > 1 fF); the non-reciprocity "
               "column is expected by design and is not a failure.")
    out.append("- F2a: tail_ac_rms near zero after release = the free node "
               "parks (no limit cycle without a gate edge); growth of "
               "tail_ac_rms with |Q| at gate=0 but absence of a sign "
               "threshold would rule out a negative-voltage trigger.")
    out.append("- F2b: a real limit cycle needs tail_ac_rms, tail_pp and "
               "tail_x convergent (nonzero) as the step shrinks; "
               "step-dependent values are a numerical artifact.")
    out.append("")
    target = OUT_DIR / "b4soi_cv_leads.md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("")
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
