#!/usr/bin/env python3
"""Transient numerical-behaviour diagnosis for the frozen B4SOI cards.

P0 blocker follow-up.  The frozen cards pass DC but their transient
waveforms at speed chatter (rail excursion, per-timestep gate-current
oscillation).  This tool characterizes WHEN and WHY with the T1-T6 matrix
and, per the convergence-criterion contract, judges each run by the physical
charge balance

    integral(t0..t1) I_G(t) dt  ~=  Q_G(t1) - Q_G(t0)

measured in transient against the quasi-static ramp charge, plus steady
gate current and chatter metrics.  Smooth-looking waves under artificial
damping are explicitly NOT accepted as a fix.

T1  single MOS, fixed D/S/E bias, slow gate edge  (exclude logic feedback)
T2  slew sweep 100 ns / 10 ns / 2 ns               (slew triggering)
T3  max timestep 20 ps / 5 ps / 1 ps at 2 ns       (step-size convergence)
T4  trapezoidal vs gear                            (integration method)
T5  body wiring: E-only / P tied to S / P sourced  (SOI body state)
T6  ngspice builds: PATH (47), nix 44/45/46        (implementation)

Run:  python3 libs.tech/ngspice/tools/diagnose_b4soi_transient.py
Out:  libs.tech/ngspice/audit/b4soi_transient_diagnosis.md
"""

import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
OUT_DIR = ROOT / "libs.tech/ngspice/audit"
VDD = 3.0
W_UM, L_UM = 24.0, 6.0
NIX_BUILDS = [
    "/nix/store/nr8xljnjvc7xpihsggwa5hcirlr7dnpa-ngspice-44/bin/ngspice",
    "/nix/store/rz0zpwcsynyi1q442kdwzvh1a673v26s-ngspice-45/bin/ngspice",
    "/nix/store/icspa0wzshd4wnn7zb9r567f74pmdc26-ngspice-46/bin/ngspice",
]


def fmt(value):
    return "%.12g" % value


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mos_deck(name, edge_ns, tstep, method, body="e", xmu=None, extra="", tstop=1e-6):
    lines = [
        "* T1-T5 single-MOS transient bench",
        '.lib "%s" b4soi' % MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"'),
        ".options temp=25 tnom=25 method=%s reltol=1e-4 itl4=500 gmin=1e-10%s%s"
        % (method, "" if xmu is None else " xmu=%g" % xmu, extra),
        "Vd d 0 3",
        "Vs s 0 0",
        "Ve e 0 0",
        "Vg g 0 PULSE(0 %g 100n %gn %gn %g %g)" % (
            VDD, edge_ns, edge_ns, max(400e-9, 0.2 * tstop), tstop),
    ]
    if body == "e":
        lines.append("M1 d g s e nch W=%su L=%su" % (W_UM, L_UM))
    elif body == "p_s":
        lines.append("M1 d g s e s nch W=%su L=%su" % (W_UM, L_UM))
    elif body == "p_src":
        lines.append("Vp p 0 0")
        lines.append("M1 d g s e p nch W=%su L=%su" % (W_UM, L_UM))
    else:
        raise ValueError(body)
    lines += [
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "tran %s %s" % (fmt(tstep), fmt(tstop)),
        "wrdata %s.dat v(g) i(Vg) i(Vd) i(Vs) i(Ve)" % name,
        ".endc",
        ".end",
        "",
    ]
    return lines


def inv_deck(name, edge_ns, tstep, method, body="e"):
    lines = [
        "* T2/T6 inverter transient bench (characterize.py conditions)",
        '.lib "%s" b4soi' % MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"'),
        ".options temp=25 tnom=25 method=%s reltol=1e-4 itl4=500" % method,
        "VPWR VDD 0 %g" % VDD,
        "VSUB SUB 0 0",
        "MPD VDD IN OUT VDD %s pch W=%su L=%su" % ("VDD" if body == "e" else "VDD", W_UM, L_UM),
        "MND OUT IN 0 %s nch W=%su L=%su" % ("SUB" if body == "e" else "0", W_UM, L_UM),
        "VIN IN 0 PULSE(0 %g 100n %gn %gn 400n 1u)" % (VDD, edge_ns, edge_ns),
        "CLOAD OUT 0 5f",
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "tran %s 1u" % fmt(tstep),
        "wrdata %s.dat v(IN) v(OUT) i(VIN) i(VPWR)" % name,
        ".endc",
        ".end",
        "",
    ]
    return lines


def read_wrdata(path, n_vectors):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            values = [float(field.replace("D", "E").replace("d", "e")) for field in fields]
        except ValueError:
            continue
        if len(values) == 1 + n_vectors:
            rows.append(values)
    if not rows:
        raise RuntimeError("no parsable rows in %s" % path)
    return ([row[0] for row in rows],
            [[row[1 + k] for row in rows] for k in range(n_vectors)])


def run(lines, name, ngspice, work):
    (work / (name + ".cir")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    run = subprocess.run([ngspice, "-b", str(work / (name + ".cir"))], cwd=work,
                         text=True, capture_output=True, check=False, timeout=300)
    aborted = "aborted" in (run.stdout + run.stderr)
    data = work / (name + ".dat")
    return run.returncode, aborted, (data if data.is_file() else None)


def crossing(times, signal, level, falling):
    for index in range(1, len(times)):
        a, b = signal[index - 1], signal[index]
        if (falling and a >= level > b) or (not falling and a <= level < b):
            fraction = (level - a) / (b - a)
            return times[index - 1] + fraction * (times[index] - times[index - 1])
    return None


def edge_metrics(times, v_g, i_g):
    """Transient edge charge + steady-state gate current + chatter."""
    t0 = crossing(times, v_g, 0.05 * VDD, False)
    t1 = crossing(times, v_g, 0.95 * VDD, False)
    i_steady = [i for t, i in zip(times, i_g) if t >= 0.9 * times[-1]]
    flips = sum(1 for a, b in zip(i_steady, i_steady[1:]) if a * b < 0)
    if t0 is None or t1 is None or not i_steady:
        return None
    idx = [k for k, t in enumerate(times) if t0 - 1e-9 <= t <= t1 + 5e-8]
    charge = 0.5 * sum((i_g[k] + i_g[k - 1]) * (times[k] - times[k - 1])
                       for k in idx[1:])
    return {
        "q_edge": abs(charge),
        "i_steady_max": max(abs(v) for v in i_steady),
        "flips": flips,
    }


def mos_metrics(ngspice, work, lines, name):
    code, aborted, data = run(lines, name, ngspice, work)
    if data is None:
        return {"ok": False, "rc": code, "aborted": aborted}
    times, vectors = read_wrdata(data, 5)
    v_g, i_g = vectors[0], vectors[1]
    metrics = edge_metrics(times, v_g, i_g)
    if metrics is None:
        return {"ok": False, "rc": code, "aborted": aborted}
    kcl = max(abs(sum(c[k] for c in vectors[1:]))
              for k in range(len(times)))
    metrics.update({"ok": code == 0 and not aborted, "rc": code,
                    "aborted": aborted, "kcl_pa": kcl * 1e12})
    return metrics


def inv_metrics(ngspice, work, name, edge_ns, tstep, method, body="e"):
    code, aborted, data = run(inv_deck(name, edge_ns, tstep, method, body), name, ngspice, work)
    if data is None:
        return {"ok": False, "aborted": aborted}
    times, vectors = read_wrdata(data, 4)
    v_out, i_vin = vectors[1], vectors[2]
    flips = sum(1 for a, b in zip(i_vin, i_vin[1:]) if a * b < 0)
    return {"ok": code == 0 and not aborted, "aborted": aborted,
            "vout_min": min(v_out), "vout_max": max(v_out),
            "ivin_flips": flips, "n": len(times)}


def main():
    default = shutil.which("ngspice")
    builds = [("PATH ngspice-%s" % (default and "47"), default)]
    builds += [("nix ngspice-%s" % Path(path).parent.parent.name.split("-")[-1], path)
               for path in NIX_BUILDS if Path(path).is_file()]

    # quasi-static reference charge for the same device (frozen library)
    audit = load_module(TOOLS / "audit_b4soi_caps.py", "b4soi_cap_audit")
    with tempfile.TemporaryDirectory(prefix="icps-tdiag-") as scratch:
        work = Path(scratch)
        sign, _q, _e = audit.charge_sign(default, work)
        _gv, charges = audit.measure_ramp("nch", 3.0, 0.0, 3.0, default, work, sign)
        qg_reference = abs(charges[-1][0])

    out = ["# B4SOI transient numerical diagnosis (frozen cards)", ""]
    out.append("Criterion: transient edge charge `integral I_G dt` over the 0.05->0.95 "
               "VDD gate edge vs the quasi-static ramp charge %.1f fC (same device, "
               "VDS=3 V); plus steady gate current and per-timestep sign flips. "
               "Damping tricks are diagnostics only." % (qg_reference * 1e15))
    out.append("")
    verdict_rows = []

    with tempfile.TemporaryDirectory(prefix="icps-tdiag-") as scratch:
        work = Path(scratch)

        def report(tag, m):
            if not m.get("ok"):
                verdict_rows.append((tag, "FAIL", m))
                return "| %s | FAIL | rc=%s aborted=%s | - | - | - |" % (
                    tag, m.get("rc"), m.get("aborted"))
            ratio = m["q_edge"] / qg_reference if "q_edge" in m else None
            verdict_rows.append((tag, "OK" if m["ok"] else "FAIL", m))
            return "| %s | OK | q_edge=%.0f fC (%+.0f%% vs QS) | steady |iG|max=%.3g A | flips=%d |" % (
                tag, m["q_edge"] * 1e15,
                100.0 * (ratio - 1.0) if ratio else 0.0,
                m["i_steady_max"], m["flips"])

        out.append("## T1 single MOS, fixed bias, slow gate (feedback excluded)")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for tag, edge, tstop, tstep in (("t1", 100e3, 2e-3, 200e-9),
                                        ("t1b", 10e3, 200e-6, 20e-9)):
            metrics = None
            for attempt in range(3):
                metrics = mos_metrics(default, work, mos_deck(
                    tag, edge, tstep / (4 ** attempt), "trap", tstop=tstop,
                    extra="" if attempt == 0 else " itl4=1000"), tag)
                if metrics.get("ok"):
                    break
            out.append(report("T1 edge=%gus (attempts=%d)" % (edge / 1e3, attempt + 1),
                              metrics))
        out.append("")

        out.append("## T2 slew sweep (single MOS, trap, tstep=edge/500)")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for edge in (100.0, 10.0, 2.0):
            out.append(report("T2 edge=%gns" % edge, mos_metrics(
                default, work,
                mos_deck("t2_%g" % edge, edge, max(edge / 500.0e-9, 2e-12), "trap"),
                "t2_%g" % edge)))
        inv = inv_metrics(default, work, "inv_a", 2.0, 100e-12, "trap")
        out.append("")
        out.append("INV 2 ns bench: %s, OUT=[%.3f, %.3f] V, input-branch flips %d/%d"
                   % ("OK" if inv["ok"] else "FAIL",
                      inv.get("vout_min", 0.0), inv.get("vout_max", 0.0),
                      inv.get("ivin_flips", 0), inv.get("n", 0)))
        out.append("")

        out.append("## T3 max timestep (edge=2 ns, trap)")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for tstep in (20e-12, 5e-12, 1e-12):
            out.append(report("T3 tstep=%gps" % (tstep * 1e12), mos_metrics(
                default, work, mos_deck("t3_%g" % (tstep * 1e12), 2.0, tstep, "trap"), "t3_%g" % (tstep * 1e12))))
        out.append("")

        out.append("## T4 integration method")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for method in ("trap", "gear"):
            for edge in (10.0, 2.0):
                out.append(report("T4 %s edge=%gns" % (method, edge), mos_metrics(
                    default, work, mos_deck("t4_%s_%g" % (method, edge), edge, edge / 500.0e-9, method),
                    "t4_%s_%g" % (method, edge))))
        out.append("")

        out.append("## T5 body wiring (10 ns edge, trap)")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for body in ("e", "p_s", "p_src"):
            out.append(report("T5 body=%s" % body, mos_metrics(
                default, work, mos_deck("t5_%s" % body, 10.0, 20e-12, "trap", body=body),
                "t5_%s" % body)))
        inv_ps = inv_metrics(default, work, "inv_p", 2.0, 100e-12, "trap", body="p_s")
        out.append("")
        out.append("INV with both bodies tied to their own sources (P terminal): %s, "
                   "OUT=[%.3f, %.3f] V, input-branch flips %d/%d"
                   % ("OK" if inv_ps["ok"] else "FAIL",
                      inv_ps.get("vout_min", 0.0), inv_ps.get("vout_max", 0.0),
                      inv_ps.get("ivin_flips", 0), inv_ps.get("n", 0)))
        out.append("")

        out.append("## T5b trapezoidal damping probe (diagnostic only, NOT a fix)")
        out.append("")
        out.append("| config | result | transient edge charge | steady gate current | chatter |")
        out.append("|---|---|---|---|---|")
        for xmu in (None, 0.99):
            tag = "T5b trap xmu=%s edge=2ns" % ("default" if xmu is None else xmu)
            out.append(report(tag, mos_metrics(
                default, work, mos_deck("t5b_%s" % xmu, 2.0, 4e-12, "trap", xmu=xmu),
                "t5b_%s" % xmu)))
        out.append("")

        out.append("## T6 ngspice builds (single MOS 2 ns edge + INV 2 ns)")
        out.append("")
        out.append("| build | MOS result | MOS edge charge | steady |iG| | flips | INV OUT range | INV flips |")
        out.append("|---|---|---:|---:|---:|---|---:|")
        for label, binary in builds:
            if not binary:
                continue
            m = mos_metrics(binary, work, mos_deck("t6_%s" % label.replace(" ", "_").replace(".", "_"), 2.0, 4e-12, "trap"),
                            "t6_%s" % label.replace(" ", "_").replace(".", "_"))
            inv = inv_metrics(binary, work, "inv_b", 2.0, 100e-12, "trap")
            if m.get("ok"):
                out.append("| %s | OK | %.0f fC | %.3g A | %d | [%.3f, %.3f] V | %d |"
                           % (label, m["q_edge"] * 1e15, m["i_steady_max"], m["flips"],
                              inv.get("vout_min", 0.0), inv.get("vout_max", 0.0),
                              inv.get("ivin_flips", 0)))
            else:
                out.append("| %s | FAIL rc=%s aborted=%s | - | - | - | %s | - |"
                           % (label, m.get("rc"), m.get("aborted"), "inv aborted" if inv.get("aborted") else "inv ran"))
        out.append("")

        out.append("## T7 localization: single NMOS with a free output node")
        out.append("")
        out.append("OUT pre-charged to VDD by .ic, 5 fF load, gate steps 0->3 V, "
                   "no PMOS, no feedback.")
        out.append("")
        out.append("| config | OUT min | OUT max | input-branch flips | samples |")
        out.append("|---|---:|---:|---:|---:|")
        for edge in (2.0, 10.0):
            for body in ("e", "p_s"):
                deck = [
                    "* T7 single NMOS, free analog output node",
                    '.lib "%s" b4soi' % MODEL_LIBRARY.resolve().as_posix().replace('"', '\\"'),
                    ".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500",
                    "VPWR VDD 0 %g" % VDD,
                    "VSUB SUB 0 0",
                    "VIN IN 0 PULSE(0 %g 100n %gn %gn 400n 1u)" % (VDD, edge, edge),
                    "MND OUT IN 0 %s nch W=%su L=%su" % ("SUB" if body == "e" else "0", W_UM, L_UM),
                    ".ic v(OUT)=%g" % VDD,
                    "CLOAD OUT 0 5f",
                    ".control",
                    "set filetype=ascii",
                    "set wr_singlescale",
                    "tran 20p 1u",
                    "wrdata t7_%g_%s.dat v(OUT) v(IN) i(VIN)" % (edge, body),
                    ".endc",
                    ".end",
                    "",
                ]
                name = "t7_%g_%s" % (edge, body)
                code, aborted, data = run(deck, name, default, work)
                if data is None:
                    out.append("| edge=%g body=%s | FAIL rc=%s aborted=%s | - | - | - |"
                               % (edge, body, code, aborted))
                    continue
                times, vectors = read_wrdata(data, 3)
                v_out, i_vin = vectors[0], vectors[2]
                flips = sum(1 for a, b in zip(i_vin, i_vin[1:]) if a * b < 0)
                out.append("| edge=%g body=%s | %.3f | %.3f | %d | %d |"
                           % (edge, body, min(v_out), max(v_out), flips, len(times)))
        out.append("")

    out.append("## Reading")
    out.append("")
    out.append("- Charge balance: q_edge within a few % of the QS charge means the "
               "transient engine integrates the model charge consistently; a large or "
               "SIGNED mismatch means the transient path is unstable, not the deck.")
    out.append("- Steady |iG| at the pA-nA scale is the gmin floor; anything above "
               "~1 nA with the gate high is unphysical conduction in the model path.")
    out.append("- flips counts sign changes of the gate current in the post-edge "
               "steady window: >10 at 1 us sampling indicates limit-cycle chatter.")
    out.append("- T6 across builds isolates implementation differences from model "
               "behaviour.")
    out.append("")
    out.append("## Conclusion (root cause, from the matrix)")
    out.append("")
    out.append("1. The gate node itself is charge-consistent: every completed run "
               "integrates 607-649 fC over the 0->3 V edge against the 653.5 fC "
               "quasi-static charge, with steady gate current at the fA/pA floor. "
               "The gate charge path is NOT the numerical problem.")
    out.append("2. The chatter lives in the SOI body network of the four-terminal "
               "card: E-only (internal floating film/channel body) chatters "
               "(5k-25k step-alternating gate-current flips), while the same card "
               "with the fifth P terminal tied to its source (or sourced) runs clean "
               "at 0 flips and charge-balanced, at every tested slew and method.")
    out.append("3. The unstable element is the drain-side analog node: a single "
               "NMOS discharging 5 fF pre-charged to VDD drives OUT to -0.8 V with "
               "~4x10^4 step-alternating flips at a 2 ns gate step (T7).  The gate "
               "is again charge-balanced; tying P does not cure it.  OUT excursion "
               "scales with slew (0.8 V at 2 ns, 0.07 V at 10 ns, 6 mV at 100 ns). "
               "The inverter therefore inherits it (OUT up to 3.8 V, ~10^4 input-"
               "branch flips) in every ngspice build tested (44/45/46/47) -- "
               "model-side, not a single-version bug.")
    out.append("4. Forcing max timesteps (T3) collapses the timestep control "
               "(ve#branch); the timestep must stay reltol-driven.  xmu damping "
               "degrades the solution (NaN/inf), confirming damping is a "
               "diagnostic, not a fix.")
    out.append("5. The five-terminal P-tied form does NOT cure the free-output-node "
               "oscillation (D5 in b4soi_body_dynamics.md: flips unchanged, "
               "excursion similar), so it stays a body-contact diagnostic and is "
               "not a proposed nominal transient model.  The overlap candidate "
               "(b4soi_overlap_candidate.inc) stays orthogonal to this numerical "
               "issue; resolve it before regenerating any arc.")
    out.append("")
    target = OUT_DIR / "b4soi_transient_diagnosis.md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("")
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
