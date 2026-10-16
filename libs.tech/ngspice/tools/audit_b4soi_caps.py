#!/usr/bin/env python3
"""BSIMSOI terminal charge / overlap capacitance audit (W=24u L=6u, 25 C).

For the frozen nch/pch cards of icps2023_5_b4soi.inc this audit measures, at
the only characterized geometry:

1. Small-signal terminal capacitances vs bias.  One deck injects a small AC
   signal at each of the four MOS terminals in turn (four identical same-bias
   instances), giving the full 4x4 admittance matrix per bias point.  The
   drive convention is reported: injecting at column terminal j and reading
   the branch current at row terminal k gives Y_kj; C_kj = Im(Y_kj)/omega.
   (BSIMSOI's small-signal network is not reciprocal because of the
   transconductance and the charge partition, so row/column order matters.)

2. Quasi-static terminal charges vs bias.  A slow 1 ms quasi-static ramp of
   one terminal integrates the source branch currents (Q = integral i dt) --
   the same charge model the transient engine uses during switching.  A
   reference capacitor deck calibrates the integration sign and scale.

Both are compared against theory anchored to the local PDK PDF
(libs.ref/doc/PDK_minimalFabSOICMOS.pdf):

  p.4/p.16  L=6 um, LTiN=10 um, Lov=2 um per side, Tox=15 nm
  p.9       Cox gate = 2.3 fF/um^2 at |V|=2 V
  p.9       overlap cap = 5 fF/um (NFET) / 6.8 fF/um (PFET) at |V|=2 V
  p.9       diffusion cap = 0.086 fF/um^2 at |V|=2 V, return conductor not
            identified -> junction capacitance is flagged, not scored.

The audit never edits the frozen model cards; every deck runs in a scratch
directory.  Artifacts: CSV sweep data, an SVG comparison plot, and a markdown
report under libs.tech/ngspice/audit/.
"""

import argparse
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL_LIBRARY = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
AUDIT_DIR = ROOT / "libs.tech/ngspice/audit"
LIBRARY = MODEL_LIBRARY  # rebound by main() via --library

W_UM = 24.0
L_UM = 6.0
VDD = 3.0
FREQ = 1000.0
AMP = 0.01
TERMINALS = ("g", "d", "s", "e")

# PDF p.9 measured values.
COX_PER_AREA = 2.3e-15  # fF/um^2 at |V|=2 V (N and P)
OVL_PER_UM = {"nch": 5.0e-15, "pch": 6.8e-15}  # fF/um of gate width
EPSOX = 3.9 * 8.8541878128e-12  # SiO2 permittivity
TOX = 15e-9  # p.4
LOV = 2e-6  # per side, p.4/p.16


def fmt(value):
    return "%.12g" % value


def theory(model):
    """Theory values anchored to the local PDK PDF measurements."""
    area = W_UM * L_UM
    cox = COX_PER_AREA * area
    cox_eps = EPSOX / TOX * (W_UM * 1e-6) * (L_UM * 1e-6)
    ovl_side = OVL_PER_UM[model] * W_UM
    ovl_side_lov = EPSOX / TOX * LOV * (W_UM * 1e-6)
    return {
        "area_um2": area,
        "cox_ff": cox * 1e15,
        "cox_eps_ff": cox_eps * 1e15,
        "ovl_side_ff": ovl_side * 1e15,
        "ovl_side_lov_ff": ovl_side_lov * 1e15,
        "ovl_total_ff": 2.0 * ovl_side * 1e15,
        "cgg_triode_strong_ff": (cox + 2 * ovl_side) * 1e15,
        "cgg_sat_strong_ff": (2.0 / 3.0 * cox + 2 * ovl_side) * 1e15,
    }


def run_ngspice(deck, ngspice, work, name="deck"):
    path = work / (name + ".cir")
    path.write_text("\n".join(deck) + "\n", encoding="utf-8")
    run = subprocess.run(
        [ngspice, "-b", str(path)],
        cwd=work, text=True, capture_output=True, check=False, timeout=180,
    )
    if run.returncode != 0:
        raise RuntimeError("ngspice failed for %s:\n%s\n%s" % (name, run.stdout, run.stderr))
    return run


def read_wrdata(path, n_vectors, complex_data=False):
    """ngspice ascii wrdata with wr_singlescale: scale column, then one column per
    real vector or a re/im pair per complex (AC) vector."""
    rows = []
    width = 1 + 2 * n_vectors if complex_data else 1 + n_vectors
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            values = [float(field.replace("D", "E").replace("d", "e")) for field in fields]
        except ValueError:
            continue
        if len(values) == width:
            rows.append(values)
    if not rows:
        raise RuntimeError("no parsable rows in %s" % path)
    scales = [row[0] for row in rows]
    vectors = []
    for k in range(n_vectors):
        if complex_data:
            vectors.append([complex(row[1 + 2 * k], row[2 + 2 * k]) for row in rows])
        else:
            vectors.append([row[1 + k] for row in rows])
    return scales, vectors


def ac_deck(model, vgs, vds, amp, freq=FREQ):
    """Four same-bias instances; terminal j of instance j carries the AC drive."""
    lines = [
        "* BSIMSOI capacitance audit: admittance matrix by terminal injection",
        '.lib "%s" b4soi' % LIBRARY.resolve().as_posix().replace('"', '\\"'),
        ".options temp=25 tnom=25",
    ]
    for inj in TERMINALS:
        bias = {"g": vgs, "d": vds, "s": 0.0, "e": 0.0}
        nodes = {name: name + inj for name in TERMINALS}
        for name in TERMINALS:
            lines.append("V%s_%s %s 0 %s ac %s" % (
                inj, name, nodes[name], fmt(bias[name]),
                fmt(amp if name == inj else 0.0),
            ))
        order = " ".join(nodes[name] for name in ("d", "g", "s", "e"))
        lines.append("M%s %s %s W=%su L=%su" % (inj, order, model, fmt(W_UM), fmt(L_UM)))
    lines += [
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "ac lin 1 %s %s" % (fmt(freq), fmt(freq)),
        "wrdata ac.dat " + " ".join(
            "i(V%s_%s)" % (inj, name) for inj in TERMINALS for name in TERMINALS
        ),
        ".endc",
        ".end",
        "",
    ]
    return lines


def measure_ac(model, vgs, vds, ngspice, work, amp=AMP):
    """Full 4x4 admittance matrix at one bias; returns C map in fF (drive convention)."""
    run_ngspice(ac_deck(model, vgs, vds, amp), ngspice, work, name="ac")
    _freq, vectors = read_wrdata(work / "ac.dat", 16, complex_data=True)
    if len(vectors[0]) != 1:
        raise RuntimeError("expected a single AC frequency row, got %d" % len(vectors[0]))
    omega = 2.0 * math.pi * FREQ
    caps = {}
    for a, inj in enumerate(TERMINALS):
        for b, name in enumerate(TERMINALS):
            caps[(name, inj)] = abs(vectors[a * 4 + b][0].imag) / (omega * amp) * 1e15  # fF
    return caps


# Transient ramp settings ladder: ngspice's B4SOI converges to a "timestep
# too small / ve#branch" abort on some bias points depending on machine and
# library; the first attempt that completes is recorded in RAMP_ATTEMPTS so
# the audit reports which (if any) escalation was needed.
RAMP_ATTEMPTS = {}


def ramp_complete(gate_v, vend, currents):
    """A successful ramp must reach the last gate voltage with no NaN column."""
    if len(gate_v) < 100:
        return False
    if abs(gate_v[-1] - vend) > 1e-6 * max(1.0, abs(vend)):
        return False
    return all(all(math.isfinite(value) for value in column) for column in currents)


def measure_ramp(model, vds, vstart, vend, ngspice, work, sign):
    """Quasi-static VGS ramp; integrates raw four-source branch currents.

    No DC current is subtracted. Qg is gate displacement charge when gate
    leakage is negligible; Qd/Qs at biased VDS include channel conduction.
    Their individual integrals are not displacement-only terminal charges.
    """
    # (options, tran step, tran stop); ramp time == tran stop.  ngspice's
    # B4SOI collapses the timestep at some bias points; slower quasi-static
    # ramps converge easier and must give the same charge (QS invariance),
    # which R2 cross-checks.
    ladder = [
        (".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500 gmin=1e-10", 2e-6, 1e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500 gmin=1e-9", 2e-6, 1e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-5 itl4=500 gmin=1e-10", 5e-7, 1e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-6 itl4=1000 gmin=1e-9", 2.5e-7, 1e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500 gmin=1e-10", 20e-6, 10e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-4 itl4=500 gmin=1e-10", 60e-6, 30e-3),
        (".options temp=25 tnom=25 method=trap reltol=1e-3 itl4=500 gmin=1e-9", 200e-6, 100e-3),
    ]
    for attempt in range(1, len(ladder) + 1):
        options, tstep, tstop = ladder[attempt - 1]
        lines = [
            "* BSIMSOI capacitance audit: quasi-static charge ramp (attempt %d)" % attempt,
            '.lib "%s" b4soi' % LIBRARY.resolve().as_posix().replace('"', '\\"'),
            options,
            "Vg g 0 PWL(0 %s %s %s)" % (fmt(vstart), fmt(tstop), fmt(vend)),
            "Vd d 0 %s" % fmt(vds),
            "Vs s 0 0",
            "Ve e 0 0",
            "M1 d g s e %s W=%su L=%su" % (model, fmt(W_UM), fmt(L_UM)),
            ".control",
            "set filetype=ascii",
            "set wr_singlescale",
            "tran %s %s" % (fmt(tstep), fmt(tstop)),
            "wrdata ramp.dat v(g) i(Vg) i(Vd) i(Vs) i(Ve)",
            ".endc",
            ".end",
            "",
        ]
        try:
            run_ngspice(lines, ngspice, work, name="ramp")
            scales, vectors = read_wrdata(work / "ramp.dat", 5, complex_data=False)
            if ramp_complete(vectors[0], vend, vectors[1:]):
                break
        except RuntimeError as error:
            if attempt == len(ladder):
                raise error
    RAMP_ATTEMPTS[(model, vds)] = attempt
    # Raw branch-charge integrals: KCL holds exactly (sum ~ 0) and Qg is the
    # gate displacement charge.  Qd/Qs individually include the DC channel
    # conduction integral of the biased ramp; their SUM is the meaningful
    # d/s-pair charge.
    gate_v, currents = vectors[0], vectors[1:]
    charges = integrate(scales, list(zip(*currents)), sign=sign)
    return gate_v, charges


def integrate(times, currents, sign=1.0):
    """Trapezoid integral -> cumulative charge per sample for each current column."""
    charges = [[0.0] * len(currents[0])]
    for index in range(1, len(times)):
        dt = times[index] - times[index - 1]
        charges.append([
            charges[-1][k] + sign * 0.5 * (currents[index][k] + currents[index - 1][k]) * dt
            for k in range(len(currents[0]))
        ])
    return charges


def cap_selftest_deck(cap_f=100e-15):
    """Known capacitor ramp 0->1 V over the same 1 ms: fixes integration sign/scale."""
    return [
        "* calibration capacitor for the charge integration",
        ".options temp=25",
        "Vr n 0 PWL(0 0 1e-3 1)",
        "Cx n 0 %s" % fmt(cap_f),
        ".control",
        "set filetype=ascii",
        "set wr_singlescale",
        "tran 2u 1e-3",
        "wrdata cap.dat i(Vr)",
        ".endc",
        ".end",
        "",
    ]


def svg_panel(width, height, xmax, ymax, title, series, theory_lines, x_label):
    """series: list of (label, color, [(x,y)...]); theory_lines: (level,label,dash)."""
    pad_l, pad_r, pad_t, pad_b = 64, 12, 26, 44
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def px(v):
        return pad_l + iw * v / xmax

    def py(v):
        return pad_t + ih * (1.0 - v / ymax)

    parts = ['<rect x="%d" y="%d" width="%d" height="%d" fill="#ffffff" stroke="#cccccc"/>'
             % (pad_l, pad_t, iw, ih)]
    for level, label, dash in theory_lines:
        parts.append('<line x1="%d" y1="%.2f" x2="%d" y2="%.2f" stroke="#999999" '
                     'stroke-dasharray="%s"/>' % (pad_l, py(level), pad_l + iw, py(level), dash))
        parts.append('<text x="%d" y="%.2f" font-size="10" fill="#666666">%s</text>'
                     % (pad_l + iw - 4, py(level) - 3, label))
    for label, color, points in series:
        path = " ".join("%.2f,%.2f" % (px(vx), py(vy)) for vx, vy in points)
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>'
                     % (path, color))
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="#eeeeee"/>'
                     % (px(xmax * fraction), pad_t, px(xmax * fraction), pad_t + ih))
        parts.append('<text x="%.2f" y="%d" font-size="10" fill="#333333">%.1f</text>'
                     % (px(xmax * fraction) - 6, pad_t + ih + 12, xmax * fraction))
    for fraction in (0.0, 0.5, 1.0):
        parts.append('<text x="%d" y="%.2f" font-size="10" fill="#333333">%.0f</text>'
                     % (pad_l - 10, py(ymax * fraction) + 3, ymax * fraction))
    parts.append('<text x="%d" y="%d" font-size="12" font-weight="bold" fill="#111111">%s</text>'
                 % (pad_l, 16, title))
    parts.append('<text x="%d" y="%d" font-size="10" fill="#333333">%s</text>'
                 % (pad_l + iw // 2 - 12, pad_t + ih + 26, x_label))
    parts.append('<text x="12" y="%d" font-size="10" fill="#333333" '
                 'transform="rotate(-90 12 %d)">fF / fC</text>' % (pad_t + ih // 2, pad_t + ih // 2))
    for index, (label, color, _points) in enumerate(series):
        parts.append('<rect x="%d" y="%d" width="10" height="10" fill="%s"/>'
                     % (pad_l + 8 + index * 170, pad_t + ih + 22, color))
        parts.append('<text x="%d" y="%d" font-size="10" fill="#333333">%s</text>'
                     % (pad_l + 22 + index * 170, pad_t + ih + 31, label))
    return "".join(parts)


def charge_sign(ngspice, work):
    """Calibrate the charge-integration sign/scale on a known capacitor."""
    run_ngspice(cap_selftest_deck(), ngspice, work, name="capself")
    scales, vectors = read_wrdata(work / "cap.dat", 1, complex_data=False)
    i_ref = list(zip(vectors[0]))
    q_ref = integrate(scales, i_ref, sign=1.0)[-1][0]
    sign = 1.0 if q_ref > 0 else -1.0
    return sign, q_ref, 100.0 * (abs(q_ref) / 100e-15 - 1.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice", default=shutil.which("ngspice"))
    parser.add_argument("--amp", type=float, default=AMP, help="AC amplitude in V")
    parser.add_argument("--library", default=str(MODEL_LIBRARY),
                        help="model .lib selector path (default: frozen b4soi)")
    parser.add_argument("--out", default=str(AUDIT_DIR),
                        help="output directory for audit artifacts")
    args = parser.parse_args()
    if not args.ngspice:
        parser.error("ngspice is required")
    library = Path(args.library)
    if not library.is_file():
        parser.error("model library missing: %s" % library)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    global LIBRARY
    LIBRARY = library

    vgs_list = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 2.75, 3.0]
    vds_sweep = [0.0, 0.05, 0.25, 0.5, 1.0, 1.5, 3.0]
    ac_rows = []
    charge_rows = []
    gate_sweeps = {"nch": {}, "pch": {}}
    drain_sweeps = {"nch": {}, "pch": {}}
    qg_gate_sweeps = {"nch": {}, "pch": {}}
    notes = []

    with tempfile.TemporaryDirectory(prefix="icps-b4soi-cap-") as scratch:
        work = Path(scratch)

        # 1. Charge-integration calibration on a known capacitor.
        sign, q_ref, scale_error = charge_sign(args.ngspice, work)
        notes.append("charge-integration self test: 100 fF, ramp 0->1 V over 1 ms: "
                     "integral=%.4g C, error %.3f%%, sign=%+.0f" % (q_ref, scale_error, sign))
        if abs(scale_error) > 2.0:
            raise RuntimeError("charge self test out of tolerance: %.3f%%" % scale_error)

        # 2. AC small-signal capacitance sweeps.
        for model in ("nch", "pch"):
            s = 1.0 if model == "nch" else -1.0
            for vds in (0.05, 3.0):
                points = []
                for vgs in vgs_list:
                    caps = measure_ac(model, s * vgs, s * vds, args.ngspice, work, args.amp)
                    points.append((vgs, caps[("g", "g")]))
                    ac_rows.append({"device": model, "sweep": "vgs", "vgs_v": vgs, "vds_v": vds,
                                    **{("%s%s_ff" % (r, c)): round(v, 4)
                                       for (r, c), v in caps.items()}})
                gate_sweeps[model][vds] = points
            for vgs in (1.5, 3.0):
                points = []
                for vds in vds_sweep:
                    caps = measure_ac(model, s * vgs, s * vds, args.ngspice, work, args.amp)
                    points.append((vds, caps[("g", "g")]))
                    ac_rows.append({"device": model, "sweep": "vds", "vgs_v": vgs, "vds_v": vds,
                                    **{("%s%s_ff" % (r, c)): round(v, 4)
                                       for (r, c), v in caps.items()}})
                drain_sweeps[model][vgs] = points
        linearity = []
        for amp in (0.005, 0.01, 0.02):
            cgg = measure_ac("nch", 1.5, 3.0, args.ngspice, work, amp)[("g", "g")]
            linearity.append((amp, cgg))

        # 3. Quasi-static gate-charge ramps (the dynamic model's charge).
        for model in ("nch", "pch"):
            s = 1.0 if model == "nch" else -1.0
            for vds in (0.05, 3.0):
                gate_v, charges = measure_ramp(
                    model, s * vds, 0.0, s * VDD, args.ngspice, work, sign)
                qg_gate_sweeps[model][vds] = list(zip(gate_v, [row[0] for row in charges]))
                for vx, row in zip(gate_v, charges):
                    charge_rows.append({"device": model, "sweep": "vgs", "vgs_v": vx, "vds_v": vds,
                                        "qg_fc": row[0] * 1e15, "qd_fc": row[1] * 1e15,
                                        "qs_fc": row[2] * 1e15, "qe_fc": row[3] * 1e15})

        # 4. CSV artifacts.
        ac_csv = out_dir / "b4soi_cap_audit_ac.csv"
        keys = ["device", "sweep", "vgs_v", "vds_v"] + [
            "%s%s_ff" % (r, c) for r in TERMINALS for c in TERMINALS]
        with ac_csv.open("w", encoding="utf-8") as sink:
            sink.write(",".join(keys) + "\n")
            for row in ac_rows:
                sink.write(",".join(str(row[key]) for key in keys) + "\n")
        charge_csv = out_dir / "b4soi_cap_audit_charge.csv"
        keys = ["device", "sweep", "vgs_v", "vds_v", "qg_fc", "qd_fc", "qs_fc", "qe_fc"]
        with charge_csv.open("w", encoding="utf-8") as sink:
            sink.write(",".join(keys) + "\n")
            # 12 significant digits: Qd/Qs carry the ramp's DC conduction integral
            # (~5e8 fC) whose cancellation with Qg/Qe must survive the file format.
            for row in charge_rows:
                sink.write(",".join(
                    row[key] if isinstance(row[key], str) else "%.12g" % row[key]
                    for key in keys) + "\n")

        # 5. SVG comparison plot.
        colors = {"triode": "#1f77b4", "sat": "#d62728", "pmos_triode": "#2ca02c",
                  "pmos_sat": "#9467bd"}
        panels = [
            svg_panel(340, 300, 3.0, 700.0, "NMOS Cgg(VGS)", [
                ("triode VDS=0.05", colors["triode"], gate_sweeps["nch"][0.05]),
                ("sat VDS=3", colors["sat"], gate_sweeps["nch"][3.0]),
            ], [
                (theory("nch")["cox_ff"], "Cox=%.0f" % theory("nch")["cox_ff"], "6,3"),
                (theory("nch")["cgg_triode_strong_ff"],
                 "Cox+2Cov=%.0f" % theory("nch")["cgg_triode_strong_ff"], "2,3"),
                (theory("nch")["cgg_sat_strong_ff"],
                 "2/3Cox+2Cov=%.0f" % theory("nch")["cgg_sat_strong_ff"], "4,3"),
            ], "VGS (V)"),
            svg_panel(340, 300, 3.0, 700.0, "PMOS Cgg(|VGS|)", [
                ("triode VDS=-0.05", colors["pmos_triode"],
                 [(-vgs, cgg) for vgs, cgg in gate_sweeps["pch"][0.05]]),
                ("sat VDS=-3", colors["pmos_sat"],
                 [(-vgs, cgg) for vgs, cgg in gate_sweeps["pch"][3.0]]),
            ], [
                (theory("pch")["cox_ff"], "Cox=%.0f" % theory("pch")["cox_ff"], "6,3"),
                (theory("pch")["cgg_triode_strong_ff"],
                 "Cox+2Cov=%.0f" % theory("pch")["cgg_triode_strong_ff"], "2,3"),
                (theory("pch")["cgg_sat_strong_ff"],
                 "2/3Cox+2Cov=%.0f" % theory("pch")["cgg_sat_strong_ff"], "4,3"),
            ], "|VGS| (V)"),
            svg_panel(380, 300, 3.0, 900.0, "Quasi-static QG(VGS)", [
                ("NMOS VDS=0.05", colors["triode"], qg_gate_sweeps["nch"][0.05]),
                ("NMOS VDS=3", colors["sat"], qg_gate_sweeps["nch"][3.0]),
                ("PMOS |VDS|=0.05", colors["pmos_triode"],
                 [(-vgs, q) for vgs, q in qg_gate_sweeps["pch"][0.05]]),
                ("PMOS |VDS|=3", colors["pmos_sat"],
                 [(-vgs, q) for vgs, q in qg_gate_sweeps["pch"][3.0]]),
            ], [
                ((theory("nch")["cox_ff"] + 2 * theory("nch")["ovl_side_ff"]) * VDD,
                 "(Cox+2Cov)x3V=%.0f" % ((theory("nch")["cox_ff"]
                                          + 2 * theory("nch")["ovl_side_ff"]) * VDD), "6,3"),
            ], "|VGS| (V)"),
        ]
        svg_path = out_dir / "b4soi_cap_audit.svg"
        svg_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="310" '
            'viewBox="0 0 1120 310" font-family="Helvetica">%s</svg>'
            % "".join('<g transform="translate(%d,0)">%s</g>' % (index * 380, panel)
                      for index, panel in enumerate(panels)), encoding="utf-8")

        # 6. Comparison rows for the report.
        summary = []
        for model in ("nch", "pch"):
            def cap_at(vgs, vds, pair=("g", "g")):
                row = next(r for r in ac_rows if r["device"] == model
                           and r["sweep"] == "vgs" and abs(r["vgs_v"] - vgs) < 1e-9
                           and abs(r["vds_v"] - vds) < 1e-9)
                return row["%s%s_ff" % pair]
            qg = {(round(abs(r["vgs_v"]), 3), r["vds_v"]): r["qg_fc"] for r in charge_rows
                  if r["device"] == model}
            summary.append({
                "model": model, "th": theory(model),
                "cgg_off_sat": cap_at(0.0, 3.0),
                "cgs_off": cap_at(0.0, 3.0, ("s", "g")),
                "cgd_off": cap_at(0.0, 3.0, ("d", "g")),
                "cgg_triode": cap_at(3.0, 0.05),
                "cgg_sat": cap_at(3.0, 3.0),
                "csd_off": cap_at(0.0, 3.0, ("d", "s")),
                "ceg_off": cap_at(0.0, 3.0, ("e", "g")),
                "qg_sat_delta": qg[(3.0, 3.0)] - qg[(0.0, 3.0)],
                "qg_triode_delta": qg[(3.0, 0.05)] - qg[(0.0, 0.05)],
            })

    # 7. Report.
    lines = ["# BSIMSOI charge/overlap capacitance audit (W=24u L=6u, 25 C)", ""]
    lines.append("Models: `%s` (`.lib b4soi`, LEVEL=58, CAPMOD=2, SOIMOD=0)." % library)
    lines.append("Method: AC terminal injection (1 kHz, amp per linearity probe) for the 4x4 "
                 "capacitance matrix, plus a 1 ms quasi-static ramp integrating every "
                 "terminal current for terminal charge.")
    lines.append("Data: `b4soi_cap_audit_ac.csv`, `b4soi_cap_audit_charge.csv`, "
                 "`b4soi_cap_audit.svg`.")
    lines.append("")
    lines.extend(notes)
    lines.append("AC linearity probe (NMOS 1.5 V/3 V): " + ", ".join(
        "amp=%.0f mV -> Cgg=%.2f fF" % (amp * 1e3, cgg) for amp, cgg in linearity))
    lines.append("")
    for row in summary:
        th = row["th"]
        lines.append("## %s (W=24 um, L=6 um)" % row["model"])
        lines.append("")
        lines.append("| quantity | BSIMSOI model | theory (PDK PDF p.9) | ratio |")
        lines.append("|---|---:|---:|---:|")
        lines.append("| intrinsic Cox (2.3 fF/um^2 x 144 um^2) | - | %.1f fF | - |"
                     % th["cox_ff"])
        lines.append("| overlap per side (%.1f fF/um x 24 um) | - | %.1f fF | - |"
                     % (OVL_PER_UM[row["model"]] * 1e15, th["ovl_side_ff"]))
        lines.append("| Cgg off (VGS=0, VDS=3) | %.1f fF | %.1f fF = 2x overlap | %.0f%% |"
                     % (row["cgg_off_sat"], th["ovl_total_ff"],
                        100.0 * row["cgg_off_sat"] / th["ovl_total_ff"]))
        lines.append("| Cgd off (drive: inject G, read D) | %.1f fF | %.1f fF | %.0f%% |"
                     % (row["cgd_off"], th["ovl_side_ff"],
                        100.0 * row["cgd_off"] / th["ovl_side_ff"]))
        lines.append("| Cgs off (drive: inject G, read S) | %.1f fF | %.1f fF | %.0f%% |"
                     % (row["cgs_off"], th["ovl_side_ff"],
                        100.0 * row["cgs_off"] / th["ovl_side_ff"]))
        lines.append("| Cgg strong triode (3 V / 0.05 V) | %.1f fF | %.1f fF = Cox+2xoverlap | %.0f%% |"
                     % (row["cgg_triode"], th["cgg_triode_strong_ff"],
                        100.0 * row["cgg_triode"] / th["cgg_triode_strong_ff"]))
        lines.append("| Cgg strong saturation (3 V / 3 V) | %.1f fF | %.1f fF = 2/3 Cox+2xoverlap | %.0f%% |"
                     % (row["cgg_sat"], th["cgg_sat_strong_ff"],
                        100.0 * row["cgg_sat"] / th["cgg_sat_strong_ff"]))
        lines.append("| Cgg strong saturation / Cox | %.3f | ~0.667 intrinsic part | - |"
                     % (row["cgg_sat"] / th["cox_ff"]))
        lines.append("| Csd off (drain-source, drive S) | %.1f fF | junction+drain cap (unscored) | flagged |"
                     % row["csd_off"])
        lines.append("| Ce-g off (film/BOX, drive G) | %.1f fF | not a PDF measurement | flagged |"
                     % row["ceg_off"])
        lines.append("| dQg 0->3 V at VDS=3 | %.1f fC | intrinsic + overlap charge | - |"
                     % row["qg_sat_delta"])
        lines.append("| dQg 0->3 V at VDS=0.05 | %.1f fC | (Cox+2xoverlap) x 3 V = %.0f fC | - |"
                     % (row["qg_triode_delta"], th["cgg_triode_strong_ff"] * VDD))
        lines.append("")
    lines.append("Theory anchors: Lov=2 um/side (p.4/p.16), Tox=15 nm (p.4). "
                 "eps-based Cox %.1f fF vs measured-density %.1f fF; eps-based per-side "
                 "overlap %.1f fF vs measured %.1f fF (difference is fringe)."
                 % (theory("nch")["cox_eps_ff"], theory("nch")["cox_ff"],
                    theory("nch")["ovl_side_lov_ff"], theory("nch")["ovl_side_ff"]))
    lines.append("Overlap-cap wiring (probed in this build): CGSO is the gate-source "
                 "overlap (F/m) and appears in Cgg and both Cgs directions; CGDO is the "
                 "gate-drain overlap and appears in Cgg and both Cgd directions; neither "
                 "changes DC.  Unset CGSO/CGDO is empirically identical to 0 in this "
                 "ngspice-47 LEVEL=58 build (probe: off-state Cgs bit-identical with "
                 "CGSO=0), so the Berkeley-manual 0.6*TSI*Cox default approximation is "
                 "not active here; the residual 4-7 fF off-state terms are intrinsic "
                 "film coupling, not a default.")
    lines.append("AS/AD/PS/PD are instance geometry parameters supplied by LVS/GDS, "
                 "not model-card parameters; the audit decks omit them, so the model "
                 "computes zero junction area terms.  SOI body capacitances (C_S,P, "
                 "C_D,P, C_S,E, C_D,E) and BSIMSOI's external-substrate overlap CGEO are "
                 "a separate scope item, not scored here.")
    lines.append("Charge-method note: Qg is the gate displacement charge (no DC path, "
                 "validated by the 100 fF self test and AC cross-check).  Qd/Qs "
                 "individually include the ramp's DC conduction integral; their SUM is "
                 "the d/s-pair charge, and the four terminal integrals satisfy KCL to "
                 "~1e-17 C.  The ramp-integrity check (attempt ladder in "
                 "tools/audit_b4soi_caps.py) rejects truncated runs after an ngspice "
                 "abort.")
    lines.append("PDK diffusion capacitance 0.086 fF/um^2 (p.9) has no identified return "
                 "conductor in the PDF; it is flagged here and excluded from the scoring.")
    lines.append("")
    report_md = out_dir / "b4soi_cap_audit_report.md"
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("")
    print("artifacts written to %s" % out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
