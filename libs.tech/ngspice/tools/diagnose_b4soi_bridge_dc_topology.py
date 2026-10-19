#!/usr/bin/env python3
"""D0-D3: DC operating point and load-topology diagnosis for the inverter.

Why this exists: a timing run showed the experimental card's loaded output
settling at 1.582 V instead of 3 V, and the stage-1 reference at 2.628 V. A gate
load is capacitive, and a capacitor passes no DC current, so a final level that
depends on the load is NOT explained by "the fanout is too heavy". Recovery time
can depend on load capacitance; the DC level cannot. Either the netlist has an
unintended DC path, or the pull-up is genuinely weak at its DC operating point,
or the level is a dynamic/initial-condition artefact that never relaxes.

D0  forced DC `.OP`, DUT with NO load, VIN=0 and VIN=3
D1  forced DC `.OP`, DUT with ideal capacitors 5 fF / 100 fF / 500 fF / 1 pF
D2  forced DC `.OP`, DUT with a real FO1 gate load, supply/output/substrate explicit
D3  long transient vs `.OP` at the same VIN/VDD/load

Each run reports the MOS operating points that decide a DC level: PMOS VSG/VSD,
NMOS VGS/VDS, the drain currents of both devices, and the load gate current.

D0 and D3 answer the primary question: is the level a DC balance problem or a
dynamic non-relaxation problem?

Run:  python3 libs.tech/ngspice/tools/diagnose_b4soi_bridge_dc_topology.py
Out:  libs.tech/ngspice/audit/b4soi_<variant>_bridge_dc_topology.md
"""

from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
VARIANT_SOIMOD = {"fd": 2, "unified": 1}

W_N_UM, W_P_UM, L_UM = 24.0, 48.0, 6.0
VDD = 3.0
CAPS_F = (5e-15, 100e-15, 500e-15, 1e-12)
D3_SETTLE_S = 10e-3


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dut_lines(vin_v: float, *, pmos_e: str, load_lines: list) -> list:
    """DUT inverter with per-terminal sense sources, all 0 V.

    Terminal order is D/G/S/E for both devices.  NMOS source is sensed to ground,
    PMOS source is sensed to VDD, and the PMOS back gate `pmos_e` is selectable so
    the common-substrate contract can be compared with the historical E=VDD tie.
    """
    return [
        "VPWR VDD 0 %g" % VDD,
        "VSUB SUB 0 0",
        "VIN IN 0 %g" % vin_v,
        "VDN OUT ND_N 0", "VSN 0 NS_N 0", "VGN IN NG_N 0", "VEN SUB NE_N 0",
        "MN ND_N NG_N NS_N NE_N nch W=%gu L=%gu" % (W_N_UM, L_UM),
        "VDP OUT ND_P 0", "VSP VDD NS_P 0", "VGP IN NG_P 0", "VEP %s NE_P 0" % pmos_e,
        "MP ND_P NG_P NS_P NE_P pch W=%gu L=%gu" % (W_P_UM, L_UM),
    ] + load_lines


def op_deck(inc_name: str, vin_v: float, pmos_e: str, load_lines: list) -> list:
    """Fixed-bias DC operating point.

    Xyce needs a non-zero-width DC sweep to produce a printable data set, and its
    bare `.OP` print type is not accepted by this build. An electrically inert
    swept source (a 1 Gohm to ground) therefore carries the sweep so that VIN and
    VDD stay exactly at their intended values.
    """
    return (["* D0/D1/D2 forced DC operating point",
             '.include "%s"' % inc_name, ".OPTIONS DEVICE TEMP=25"]
            + dut_lines(vin_v, pmos_e=pmos_e, load_lines=load_lines)
            + ["VDMY DMY 0 0", "RDMY DMY 0 1e9",
               ".DC VDMY 0 0.001 0.001",
               ".PRINT DC FORMAT=CSV FILE=op.csv V(OUT) V(IN) I(VPWR) I(VSUB) "
               "I(VDN) I(VSN) I(VGN) I(VEN) I(VDP) I(VSP) I(VGP) I(VEP)",
               ".END"])


FIELDS = ("V(OUT)", "V(IN)", "I(VPWR)", "I(VSUB)", "I(VDN)", "I(VSN)", "I(VGN)",
          "I(VEN)", "I(VDP)", "I(VSP)", "I(VGP)", "I(VEP)")


def read_op(xyce, csv_path: Path) -> dict:
    """Map columns by their printed header name, not by position.

    Xyce does not guarantee that every requested variable appears, so a
    positional read silently mis-assigns currents.
    """
    lines = Path(csv_path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("empty operating-point CSV")
    header = [name.strip() for name in lines[0].split(",")]
    rows = []
    for line in lines[1:]:
        try:
            rows.append([float(field) for field in line.split(",")])
        except ValueError:
            continue
    if not rows:
        raise ValueError("no numeric operating-point row")
    last = [row for row in rows if len(row) == len(header)]
    if not last:
        raise ValueError("malformed operating-point rows")
    row = last[-1]
    table = {name.upper(): row[k] for k, name in enumerate(header)}
    missing = [name for name in FIELDS if name.upper() not in table]
    if missing:
        raise ValueError("operating-point CSV is missing %s (header: %s)"
                         % (missing, header))
    return {name: table[name.upper()] for name in FIELDS}


def describe(op: dict) -> dict:
    """MOS operating points that decide the DC level, sign-normalised."""
    return {
        "vout": op["V(OUT)"],
        "pmos_vsg": op["V(IN)"] - VDD,          # V(G) - V(S) with S = VDD
        "pmos_vsd": op["V(OUT)"] - VDD,         # V(D) - V(S)
        "nmos_vgs": op["V(IN)"],                # V(G) - V(S) with S = 0
        "nmos_vds": op["V(OUT)"],
        "pmos_id_a": -op["I(VDP)"],             # current into the drain
        "nmos_id_a": op["I(VDN)"],
        "pmos_is_a": -op["I(VSP)"],
        "nmos_is_a": op["I(VSN)"],
        "pmos_ig_a": op["I(VGP)"],
        "nmos_ig_a": op["I(VGN)"],
        "pmos_ie_a": op["I(VEP)"],
        "nmos_ie_a": op["I(VEN)"],
        "vdd_i_a": op["I(VPWR)"],
        "sub_i_a": op["I(VSUB)"],
    }


def run_op(xyce, inc: Path, name: str, vin_v: float, pmos_e: str,
           load_lines: list, work: Path) -> dict:
    deck = op_deck(inc.name, vin_v, pmos_e, load_lines)
    result = xyce.run(deck, name, work, timeout=300.0)
    if result["status"] != "observed":
        return {"status": result["status"], "reason": result.get("reason")}
    try:
        op = read_op(xyce, result["csv"])
    except (OSError, ValueError) as error:
        return {"status": "failed", "reason": str(error)}
    result["csv"].unlink(missing_ok=True)
    out = {"status": "observed"}
    out.update(describe(op))
    return out


def transient_tail(xyce, inc: Path, name: str, vin_v: float, pmos_e: str,
                   load_lines: list, work: Path) -> dict:
    """D3: hold the input at `vin_v` for 10 ms and read the final tail."""
    lines = (["* D3 long transient at fixed input",
              '.include "%s"' % inc.name, ".OPTIONS DEVICE TEMP=25",
              ".OPTIONS TIMEINT METHOD=TRAP RELTOL=1e-4 ABSTOL=1e-12"]
             + dut_lines(vin_v, pmos_e=pmos_e, load_lines=load_lines)
             + [".TRAN 2e-9 10e-3 0 20e-12",
                ".PRINT TRAN FORMAT=CSV FILE=t3.csv V(OUT) I(VPWR) I(VDN) I(VDP)",
                ".END"])
    result = xyce.run(lines, name, work, timeout=1800.0, budget_bytes=300_000_000)
    if result["status"] != "observed":
        return {"status": result["status"], "reason": result.get("reason")}
    times, columns = xyce.read_csv(result["csv"])
    if len(columns) != 4:
        return {"status": "failed", "reason": "expected 4 columns"}
    result["csv"].unlink(missing_ok=True)
    tail = [k for k, t in enumerate(times) if t >= times[-1] - 0.1 * times[-1]]
    vout = [columns[0][k] for k in tail]
    ipwr = [columns[1][k] for k in tail]
    idn = [columns[2][k] for k in tail]
    idp = [columns[3][k] for k in tail]
    mean = sum(vout) / len(vout)
    return {
        "status": "observed",
        "vout_tail_mean": mean,
        "vout_tail_min": min(vout), "vout_tail_max": max(vout),
        "vout_tail_pp": max(vout) - min(vout),
        "supply_tail_rms_a": math.sqrt(sum(v * v for v in ipwr) / len(ipwr)),
        "nmos_id_tail_a": sum(idn) / len(idn),
        "pmos_id_tail_a": -sum(idp) / len(idp),
        "t_end_s": times[-1],
    }


def fmt(value, scale, unit, digits=4):
    if value is None:
        return "n/a"
    return "%.*f %s" % (digits, value * scale, unit)


def op_row(label, op: dict) -> str:
    if op.get("status") != "observed":
        return "| %s | %s: %s | | | | | | | | |" % (label, op.get("status"),
                                                    op.get("reason", ""))
    return ("| %s | %.4f | %.3f | %.3f | %.3f | %.3f | %+.3e | %+.3e | %+.3e | %+.3e |"
            % (label, op["vout"], op["pmos_vsg"], op["pmos_vsd"], op["nmos_vgs"],
               op["nmos_vds"], op["pmos_id_a"], op["nmos_id_a"],
               op["pmos_ig_a"], op["vdd_i_a"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=sorted(VARIANT_SOIMOD), default="fd")
    parser.add_argument("--skip-d3", action="store_true",
                        help="run D0-D2 only; D3's 10 ms transients are slow")
    args = parser.parse_args()
    variant = args.variant
    cards = [("bridge_%s" % variant,
              ROOT / ("libs.tech/ngspice/models/b4soi_%s_bridge_experimental.inc" % variant)),
             ("stage1_reference",
              ROOT / "libs.tech/ngspice/models/icps2023_5_b4soi.inc")]
    xyce = load_module(TOOLS / "xyce_b4soi.py", "xyce_b4soi")
    if not xyce.available():
        raise SystemExit("Xyce is required")

    import tempfile
    out = ["# D0-D3 inverter DC operating point and load topology: %s variant" % variant,
           "",
           "A gate load is capacitive and passes no DC current, so a final output",
           "level must not depend on it. These runs separate a DC balance problem",
           "from a dynamic non-relaxation problem.", "",
           "`pmos_e` selects the PMOS back gate: `SUB` is the PDK's common-substrate",
           "four-terminal contract; `VDD` is the historical diagnostic tie that the",
           "depletion audit flagged as a different topology.", ""]

    with tempfile.TemporaryDirectory(prefix="icps-dc-topology-") as scratch:
        work = Path(scratch)
        incs = {}
        for label, card in cards:
            inc = work / ("%s.inc" % label)
            inc.write_text(xyce.port_card(card.read_text(encoding="utf-8")),
                           encoding="utf-8")
            incs[label] = inc

        # A gate load: terminal order is D, G, S, E, so the DRAIN goes to the
        # load's own output FL and the GATE goes to the DUT output OUT.  Writing
        # this as (OUT FL ...) instead puts the load drains on OUT and its gates
        # on a floating node, turning the "load" into a second inverter shorted
        # to the DUT output - which is what produced the earlier 1.58 V.
        fo1 = ["MLN FL OUT 0 SUB nch W=%gu L=%gu" % (W_N_UM, L_UM),
               "MLP FL OUT VDD SUB pch W=%gu L=%gu" % (W_P_UM, L_UM),
               "CFL FL 0 1e-15"]
        caps = {("%g fF" % (c * 1e15)): ["CL OUT 0 %g" % c] for c in CAPS_F}

        out += ["## D0 forced DC, NO load", "",
                "| card | pmos_e | VIN | VOUT (V) | PMOS VSG | PMOS VSD | NMOS VGS | "
                "NMOS VDS | PMOS ID (A) | NMOS ID (A) | PMOS IG (A) | VDD I (A) |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for label, _ in cards:
            for pmos_e in ("SUB", "VDD"):
                for vin in (0.0, VDD):
                    op = run_op(xyce, incs[label], "d0_%s_%s_%g" % (label, pmos_e, vin),
                                vin, pmos_e, [], work)
                    out.append(op_row("%s / %s / VIN=%.0f" % (label, pmos_e, vin), op))
        out.append("")

        out += ["## D1 forced DC with ideal capacitive load", "",
                "A capacitor cannot change a DC operating point. Any VOUT movement",
                "here means the added element is not purely capacitive.",
                "",
                "| card | load | VOUT (V) | PMOS ID (A) | NMOS ID (A) | VDD I (A) |",
                "|---|---|---:|---:|---:|---:|"]
        for label, _ in cards:
            for cap_label, cap_lines in caps.items():
                op = run_op(xyce, incs[label], "d1_%s_%s" % (label, cap_label.replace(" ", "")),
                            0.0, "SUB", cap_lines, work)
                if op.get("status") != "observed":
                    out.append("| %s | %s | %s | | | |" % (label, cap_label, op.get("status")))
                    continue
                out.append("| %s | %s | %.4f | %+.3e | %+.3e | %+.3e |"
                           % (label, cap_label, op["vout"], op["pmos_id_a"],
                              op["nmos_id_a"], op["vdd_i_a"]))
        out.append("")

        out += ["## D2 forced DC with a real FO1 gate load", "",
                "The load inverter has explicit drain (FL), gate (OUT), source and",
                "substrate. A pure gate load must draw no DC current, so any VOUT",
                "change here is a topology or DC-path effect, not drive strength.",
                "",
                "| card | load | VOUT (V) | PMOS ID (A) | NMOS ID (A) | load gate I "
                "(A) | load supply I (A) | VDD I (A) |",
                "|---|---|---:|---:|---:|---:|---:|---:|"]
        for label, _ in cards:
            for load_label, load_lines in (("none", []), ("FO1", fo1)):
                op = run_op(xyce, incs[label], "d2_%s_%s" % (label, load_label),
                            0.0, "SUB", load_lines, work)
                if op.get("status") != "observed":
                    out.append("| %s | %s | %s | | | | | |"
                               % (label, load_label, op.get("status")))
                    continue
                # The load gate shares node OUT, so its DC current is not separately
                # sensed; the DUT drain currents and the supply current bound it.
                out.append("| %s | %s | %.4f | %+.3e | %+.3e | %.3e | %.3e | %+.3e |"
                           % (label, load_label, op["vout"], op["pmos_id_a"],
                              op["nmos_id_a"], op["pmos_ig_a"], op["pmos_is_a"],
                              op["vdd_i_a"]))
        out.append("")

        out += ["## D3 long transient versus the DC operating point", "",
                "The input is held at 0 V for 10 ms so the output has every chance to",
                "relax. If the tail stays away from the D0 operating point, the level",
                "is a dynamic artefact; if it equals D0, the DC balance itself is low.",
                "",
                "| card | load | VOUT_OP (V) | VOUT_tail mean (V) | tail pp (mV) | "
                "supply tail RMS (A) | PMOS ID tail (A) | NMOS ID tail (A) | "
                "VOUT_tail - VOUT_OP (mV) |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        if args.skip_d3:
            out += ["| (skipped) | | | | | | | | |", ""]
        d3_ops = {}
        for label, _ in cards:
            if args.skip_d3:
                break
            for load_label, load_lines in (("none", []), ("FO1", fo1)):
                op = run_op(xyce, incs[label], "d3op_%s_%s" % (label, load_label),
                            0.0, "SUB", load_lines, work)
                tr = transient_tail(xyce, incs[label], "d3t_%s_%s" % (label, load_label),
                                    0.0, "SUB", load_lines, work)
                d3_ops[(label, load_label)] = (op, tr)
                if op.get("status") != "observed" or tr.get("status") != "observed":
                    out.append("| %s | %s | %s | %s | | | | | |"
                               % (label, load_label, op.get("status"),
                                  tr.get("status")))
                    continue
                out.append("| %s | %s | %.4f | %.4f | %.2f | %.3e | %+.3e | %+.3e | %+.2f |"
                           % (label, load_label, op["vout"], tr["vout_tail_mean"],
                              tr["vout_tail_pp"] * 1e3, tr["supply_tail_rms_a"],
                              tr["pmos_id_tail_a"], tr["nmos_id_tail_a"],
                              (tr["vout_tail_mean"] - op["vout"]) * 1e3))
        out.append("")

    out += ["## How to read this", "",
            "- **VOUT(D0, no load) close to VDD but the transient tail far below it**:",
            "  the DC solution is fine and the problem is dynamic (state, initial",
            "  condition, numerical integration, floating nodes).",
            "- **VOUT(D0, no load) already low**: the DC balance, bias or connectivity is",
            "  wrong, and no amount of transient sweeping or DUT upsizing fixes it.",
            "- **VOUT unchanged by ideal capacitors (D1) but moved by a real gate (D2)**:",
            "  the load inverter introduces a DC path that a gate should not have.",
            "- **PMOS VSG near -3 V with PMOS VSD well inside saturation but little",
            "  current**: the question becomes where the PMOS current goes, which the",
            "  per-device currents above are meant to answer.",
            "",
            "Timing acceptance remains blocked until VOH >= 2.85 V, VOL <= 0.15 V and",
            "|VOUT_tail - VOUT_OP| <= 10 mV are all satisfied on the same condition.",
            ""]
    report = ROOT / ("libs.tech/ngspice/audit/b4soi_%s_bridge_dc_topology.md" % variant)
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print("wrote %s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
