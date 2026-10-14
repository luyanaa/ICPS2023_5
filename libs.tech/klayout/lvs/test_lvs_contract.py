#!/usr/bin/env python3
"""Integration checks for strict LVS using the experimental contest samples."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
LAYOUT = ROOT / "Samples/Semicon2023/NAND/nand_sample.GDS"
REFERENCE = ROOT / "Samples/Semicon2023/NAND/nand_sample_b4soi_reference.cir"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
EXPERIMENTAL_PROFILE = "icps2023_5_multimetal_experimental"


def run_lvs(tempdir, schematic, filters=None, input_layout=LAYOUT, layer_mode=None,
            process_profile=EXPERIMENTAL_PROFILE, gate_overlap_um=None):
    report = Path(tempdir) / "report.lyrdb"
    output = Path(tempdir) / "extracted.cir"
    command = [
        KLAYOUT,
        "-b",
        "-r",
        str(LVS_DECK),
        "-rd",
        "input=" + str(input_layout),
        "-rd",
        "schematic=" + str(schematic),
        "-rd",
        "report=" + str(report),
        "-rd",
        "output=" + str(output),
    ]
    command.extend(["-rd", "process_profile=" + process_profile])
    if layer_mode:
        command.extend(["-rd", "layer_mode=" + layer_mode])
    if filters:
        command.extend(["-rd", "filters=" + filters])
    if gate_overlap_um is not None:
        command.extend(["-rd", "gate_overlap_um=" + str(gate_overlap_um)])
    return subprocess.run(command, text=True, capture_output=True)


def prepare_strict_device_layout(path):
    import klayout.db as pya

    layout = pya.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("STRICT_DEVICES")

    def add_box(layer, x1, y1, x2, y2):
        box = pya.Box(*(int(round(value / layout.dbu)) for value in (x1, y1, x2, y2)))
        cell.shapes(layout.layer(*layer)).insert(box)

    def add_text(layer, name, x, y):
        point = pya.Point(
            int(round(x / layout.dbu)),
            int(round(y / layout.dbu)),
        )
        cell.shapes(layout.layer(*layer)).insert(pya.Text(name, pya.Trans(point)))

    # N+ MOS: 10 um drawn TiN gate, 10 um width; default 2 um/end gives L=6.
    add_box((3, 0), 0, 0, 30, 10)
    add_box((19, 0), 0, 0, 30, 10)
    add_box((5, 0), 10, -2, 20, 12)
    for x, name in ((5.0, "MOS_SOURCE"), (25.0, "MOS_DRAIN")):
        add_box((7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
        add_box((8, 0), x - 6.5, -1.5, x + 6.5, 11.5)
        add_text((8, 0), name, x, 5)
    add_text((5, 0), "MOS_GATE", 15, 11)

    # 16 um x 10 um N+ resistor at 180 ohm/sq.
    add_box((3, 0), 36, 0, 64, 10)
    add_box((19, 0), 36, 0, 64, 10)
    add_box((15, 0), 42, 0, 58, 10)
    for x, name in ((39.5, "RES_LEFT"), (60.5, "RES_RIGHT")):
        add_box((7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
        add_box((8, 0), x - 6.5, -1.5, x + 6.5, 11.5)
        add_text((8, 0), name, x, 5)

    # 100 um^2 TiN/P+ diffusion capacitor at 2.3 fF/um^2.
    add_box((3, 0), 70, 0, 85, 10)
    add_box((18, 0), 70, 0, 85, 10)
    add_box((16, 0), 70, 0, 80, 10)
    add_box((5, 0), 70, 0, 80, 10)
    add_text((5, 0), "CAP_TOP", 75, 5)
    add_box((7, 0), 80.5, 2.5, 85.5, 7.5)
    add_box((8, 0), 80.5, 2.5, 90.5, 7.5)
    add_text((8, 0), "CAP_BOTTOM", 83, 5)

    layout.write(str(path))
    return path


def write_strict_device_reference(path, gate_net="MOS_GATE", width="10U"):
    reference = """* Explicit strict reference for the hand-built geometry fixture.
.GLOBAL SUB
.SUBCKT NchStrictReference MOS_DRAIN MOS_GATE MOS_SOURCE RES_LEFT RES_RIGHT CAP_TOP CAP_BOTTOM
M1 MOS_DRAIN %(gate_net)s MOS_SOURCE SUB NCH L=6U W=%(width)s
R1 RES_LEFT RES_RIGHT 288
C1 CAP_TOP CAP_BOTTOM 2.3e-13
.ENDS NchStrictReference
XTOP MOS_DRAIN MOS_GATE MOS_SOURCE RES_LEFT RES_RIGHT CAP_TOP CAP_BOTTOM NchStrictReference
.END
"""
    path.write_text(reference % {"gate_net": gate_net, "width": width})


def prepare_fab_layout(tempdir, process_profile=EXPERIMENTAL_PROFILE):
    fab_layout = Path(tempdir) / "layout_fab.gds"
    result = subprocess.run(
        [
            KLAYOUT,
            "-b",
            "-r",
            str(FAB_PREP),
            "-rd",
            "input=" + str(LAYOUT),
            "-rd",
            "output=" + str(fab_layout),
            "-rd",
            "process_profile=" + process_profile,
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return fab_layout


def rewrite_mos_e(source, destination, net, first_only=False, remove_global=False):
    lines = source.read_text().splitlines()
    out = []
    changed = 0
    for line in lines:
        fields = line.split()
        if fields and fields[0][0].upper() == "M" and len(fields) >= 7:
            changed += 1
            fields[4] = "SUB" if first_only and changed != 2 else net
            line = " ".join(fields)
        if remove_global and fields and fields[0].upper() == ".GLOBAL" and any(
            item.upper() == "SUB" for item in fields[1:]
        ):
            continue
        out.append(line)
    if changed == 0:
        raise AssertionError("reference netlist has no four-terminal MOS lines")
    if net.upper() != "SUB" and not remove_global:
        out.insert(1, ".GLOBAL " + net)
    destination.write_text("\n".join(out) + "\n")


@unittest.skipUnless(KLAYOUT, "KLayout executable is required for LVS")
class LvsContractTests(unittest.TestCase):
    def test_legacy_filters_preserve_design_and_fab_reference_matches(self):
        with tempfile.TemporaryDirectory() as tempdir:
            for layer_mode in ("design", "fab"):
                with self.subTest(layer_mode=layer_mode):
                    layout = LAYOUT
                    if layer_mode == "fab":
                        layout = prepare_fab_layout(tempdir)
                    result = run_lvs(
                        tempdir,
                        REFERENCE,
                        filters="legacy",
                        input_layout=layout,
                        layer_mode=layer_mode,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", result.stdout)

    def test_strict_experimental_flow_exposes_unmodelled_pad_parasitics(self):
        with tempfile.TemporaryDirectory() as tempdir:
            result = run_lvs(tempdir, REFERENCE)
            self.assertIn("ICPS2023_5 LVS NO MATCH", result.stdout + result.stderr)

    def test_strict_device_fixture_matches_explicit_mos_resistor_capacitor(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_strict_device_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "strict_devices.cir"
            write_strict_device_reference(schematic)

            result = run_lvs(tempdir, schematic, input_layout=layout)

            extracted = tempdir / "extracted.cir"
            details = result.stdout + result.stderr
            if extracted.exists():
                details += "\n" + extracted.read_text()
            self.assertEqual(result.returncode, 0, details)
            report = tempdir / "report.lyrdb"
            if report.exists():
                details += "\n" + report.read_text()
            self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)
            self.assertIn("gate_overlap_um=2.0 per end", result.stdout)

    def test_strict_device_fixture_rejects_wrong_mos_terminal(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_strict_device_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "wrong_terminal.cir"
            write_strict_device_reference(schematic, gate_net="MOS_DRAIN")

            result = run_lvs(tempdir, schematic, input_layout=layout)

            self.assertIn("ICPS2023_5 LVS NO MATCH", result.stdout + result.stderr)

    def test_strict_device_fixture_rejects_wrong_dimensions_and_overlap(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_strict_device_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "wrong_width.cir"
            write_strict_device_reference(schematic, width="12U")
            wrong_width = run_lvs(tempdir, schematic, input_layout=layout)
            self.assertIn(
                "ICPS2023_5 LVS NO MATCH",
                wrong_width.stdout + wrong_width.stderr,
            )

            write_strict_device_reference(schematic)
            wrong_overlap = run_lvs(
                tempdir,
                schematic,
                input_layout=layout,
                gate_overlap_um=1.5,
            )
            self.assertIn(
                "ICPS2023_5 LVS NO MATCH",
                wrong_overlap.stdout + wrong_overlap.stderr,
            )

    def test_common_but_wrong_e_net_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            schematic = tempdir / "wrong_substrate.cir"
            rewrite_mos_e(REFERENCE, schematic, "VDD")
            result = run_lvs(tempdir, schematic)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MOS E terminal", result.stdout + result.stderr)
            self.assertIn("must connect to .GLOBAL SUB", result.stdout + result.stderr)

    def test_heterogeneous_e_nets_are_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            schematic = tempdir / "mixed_substrate.cir"
            rewrite_mos_e(REFERENCE, schematic, "VDD", first_only=True)
            result = run_lvs(tempdir, schematic)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must use one common substrate net", result.stdout + result.stderr)

    def test_e_net_requires_global_declaration(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            schematic = tempdir / "local_substrate.cir"
            rewrite_mos_e(REFERENCE, schematic, "SUB", remove_global=True)
            result = run_lvs(tempdir, schematic)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declare .GLOBAL SUB", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
