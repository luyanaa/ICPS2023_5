#!/usr/bin/env python3
"""Strict LVS contract checks; the hand-drawn fixture is logic-only, not a DRC golden.

The shipped NAND sample is foundry-mask numbered in place, so every run uses
`layer_mode=fab`; the design-numbering path survives only in the fab-prep
conversion deck and its own fixtures.
"""

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
EXPERIMENTAL_PROFILE = "icps2023_5_multimetal_experimental"


def run_lvs(tempdir, schematic, filters=None, input_layout=LAYOUT, layer_mode=None,
            process_profile=EXPERIMENTAL_PROFILE):
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
    return subprocess.run(command, text=True, capture_output=True)


def prepare_logic_only_strict_lvs_layout(path):
    """Build the under-minimum hand-drawn LVS fixture; it is not 1M DRC-clean."""
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

    # Logic-only N+ MOS: physical dopant gap is 6 um; W=10 um is under the
    # native 13 um minimum. This fixture is not a DRC/fab signoff layout.
    add_box((3, 0), 0, 0, 30, 10)
    add_box((19, 0), 0, 0, 30, 10)
    add_box((5, 0), 10, -2, 16, 16)
    for x, name in ((5.0, "MOS_SOURCE"), (25.0, "MOS_DRAIN")):
        add_box((7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
        add_box((8, 0), x - 6.5, -1.5, x + 6.5, 11.5)
        add_text((13, 0), name, x, 5)
    add_box((7, 0), 12, 12, 16, 16)
    add_box((8, 0), 10, 12, 18, 16)
    add_text((13, 0), "MOS_GATE", 14, 14)

    # Logic-only 16 um x 10 um N+ resistor; native PCell length minimum is 25 um.
    add_box((3, 0), 36, 0, 64, 10)
    add_box((19, 0), 36, 0, 64, 10)
    add_box((15, 0), 42, 0, 58, 10)
    for x, name in ((39.5, "RES_LEFT"), (60.5, "RES_RIGHT")):
        add_box((7, 0), x - 2.5, 2.5, x + 2.5, 7.5)
        add_box((8, 0), x - 6.5, -1.5, x + 6.5, 11.5)
        add_text((13, 0), name, x, 5)

    # 100 um^2 TiN/P+ diffusion capacitor at 2.3 fF/um^2.
    add_box((3, 0), 70, 0, 85, 10)
    add_box((18, 0), 70, 0, 85, 10)
    add_box((16, 0), 70, 0, 80, 10)
    add_box((5, 0), 70, 0, 80, 10)
    add_text((13, 0), "CAP_TOP", 75, 5)
    add_box((7, 0), 80.5, 2.5, 85.5, 7.5)
    add_box((8, 0), 80.5, 2.5, 90.5, 7.5)
    add_text((13, 0), "CAP_BOTTOM", 83, 5)

    layout.write(str(path))
    return path


def write_logic_only_lvs_reference(path, gate_net="MOS_GATE", width="10U", length="6U"):
    reference = """* LVS reference for a logic-only, under-minimum hand-built fixture.
.GLOBAL SUB
.SUBCKT NchStrictReference MOS_DRAIN MOS_GATE MOS_SOURCE RES_LEFT RES_RIGHT CAP_TOP CAP_BOTTOM
M1 MOS_DRAIN %(gate_net)s MOS_SOURCE SUB NCH L=%(length)s W=%(width)s
R1 RES_LEFT RES_RIGHT 288
C1 CAP_TOP CAP_BOTTOM 2.3e-13
.ENDS NchStrictReference
XTOP MOS_DRAIN MOS_GATE MOS_SOURCE RES_LEFT RES_RIGHT CAP_TOP CAP_BOTTOM NchStrictReference
.END
"""
    path.write_text(reference % {"gate_net": gate_net, "width": width, "length": length})


def prepare_asymmetric_final_tin_layout(path):
    """Create final masks with a 6 um dopant gap and asymmetric 12 um TiN."""
    import klayout.db as pya

    layout = pya.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell("ASYMMETRIC_FINAL_TIN")

    def add_box(layer, x1, y1, x2, y2):
        cell.shapes(layout.layer(*layer)).insert(
            pya.Box(*(int(round(value / layout.dbu))
                      for value in (x1, y1, x2, y2)))
        )

    def add_text(name, x, y):
        point = pya.Point(int(round(x / layout.dbu)),
                          int(round(y / layout.dbu)))
        cell.shapes(layout.layer(13, 1)).insert(pya.Text(name, pya.Trans(point)))

    # SOI is continuous; the physical N-area masks leave a 6 um channel gap.
    # Final TiN spans x=8..20 (12 um), hence 2 um and 4 um end overlaps.
    add_box((4, 0), 0, 0, 40, 20)
    add_box((3, 0), 0, 0, 10, 20)
    add_box((3, 0), 16, 0, 40, 20)
    add_box((5, 0), 8, 0, 20, 25)
    for x, name in ((5, "MOS_SOURCE"), (25, "MOS_DRAIN")):
        add_box((6, 0), x - 2.5, 7.5, x + 2.5, 12.5)
        add_box((7, 0), x - 5, 5, x + 5, 15)
        add_text(name, x, 10)
    add_box((6, 0), 12, 21, 16, 25)
    add_box((7, 0), 10, 20, 18, 26)
    add_text("MOS_GATE", 14, 23)
    layout.write(str(path))
    return path


def write_asymmetric_fab_reference(path, length="6U"):
    path.write_text(
        """.GLOBAL SUB
.SUBCKT FabChannel MOS_DRAIN MOS_GATE MOS_SOURCE
M1 MOS_DRAIN MOS_GATE MOS_SOURCE SUB NCH L=%s W=20U
.ENDS FabChannel
XTOP MOS_DRAIN MOS_GATE MOS_SOURCE FabChannel
.END
""" % length
    )


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
    def test_legacy_filters_preserve_mask_reference_match(self):
        """The shipped sample is foundry-mask numbered in place: the legacy
        compatibility filter reproduces its reference match directly."""
        with tempfile.TemporaryDirectory() as tempdir:
            result = run_lvs(tempdir, REFERENCE, filters="legacy",
                             input_layout=LAYOUT, layer_mode="fab")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", result.stdout)

    def test_strict_experimental_flow_exposes_unmodelled_pad_parasitics(self):
        with tempfile.TemporaryDirectory() as tempdir:
            result = run_lvs(tempdir, REFERENCE, input_layout=LAYOUT,
                             layer_mode="fab")
            self.assertIn("ICPS2023_5 LVS NO MATCH", result.stdout + result.stderr)

    def test_logic_only_fixture_matches_explicit_mos_resistor_capacitor(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_logic_only_strict_lvs_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "strict_devices.cir"
            write_logic_only_lvs_reference(schematic)

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

    def test_logic_only_fixture_rejects_wrong_mos_terminal(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_logic_only_strict_lvs_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "wrong_terminal.cir"
            write_logic_only_lvs_reference(schematic, gate_net="MOS_DRAIN")

            result = run_lvs(tempdir, schematic, input_layout=layout)

            self.assertIn("ICPS2023_5 LVS NO MATCH", result.stdout + result.stderr)

    def test_logic_only_fixture_rejects_wrong_width_and_channel_length(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_logic_only_strict_lvs_layout(tempdir / "strict_devices.gds")
            schematic = tempdir / "wrong_width.cir"
            write_logic_only_lvs_reference(schematic, width="12U")
            wrong_width = run_lvs(tempdir, schematic, input_layout=layout)
            self.assertIn(
                "ICPS2023_5 LVS NO MATCH",
                wrong_width.stdout + wrong_width.stderr,
            )

            write_logic_only_lvs_reference(schematic, length="10U")
            wrong_length = run_lvs(tempdir, schematic, input_layout=layout)
            self.assertIn(
                "ICPS2023_5 LVS NO MATCH",
                wrong_length.stdout + wrong_length.stderr,
            )

    def test_asymmetric_final_tin_measures_dopant_gap(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            layout = prepare_asymmetric_final_tin_layout(
                tempdir / "asymmetric_final_masks.gds"
            )
            schematic = tempdir / "asymmetric_channel.cir"
            write_asymmetric_fab_reference(schematic)

            result = run_lvs(
                tempdir, schematic, input_layout=layout, layer_mode="fab"
            )
            details = result.stdout + result.stderr
            if result.returncode:
                extracted = tempdir / "extracted.cir"
                if extracted.exists():
                    details += "\n" + extracted.read_text()
            self.assertEqual(result.returncode, 0, details)
            self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)", details)

            write_asymmetric_fab_reference(schematic, length="12U")
            wrong_final_tin_width = run_lvs(
                tempdir, schematic, input_layout=layout, layer_mode="fab"
            )
            self.assertIn(
                "ICPS2023_5 LVS NO MATCH",
                wrong_final_tin_width.stdout + wrong_final_tin_width.stderr,
            )


    def test_pmos_external_body_terminal_requires_a_physical_tie(self):
        """The reader accepts PCH4; a layout without a physical PMOS tie (the
        shipped sample has none) must not match a four-terminal reference."""
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = Path(tempdir)
            schematic = tempdir / "pmos_body.cir"
            schematic.write_text(
                ".GLOBAL SUB\n"
                ".SUBCKT PMOSBody D G S B\n"
                "M1 D G S SUB B PCH L=10U W=40U\n"
                ".ENDS PMOSBody\n"
                "XTOP D G S B PMOSBody\n.END\n",
                encoding="utf-8",
            )
            result = run_lvs(tempdir, schematic, input_layout=LAYOUT,
                             layer_mode="fab")
            self.assertEqual(result.returncode, 0,
                             result.stdout + result.stderr)
            self.assertIn("ICPS2023_5 LVS NO MATCH",
                          result.stdout + result.stderr)
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
