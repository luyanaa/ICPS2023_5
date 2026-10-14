#!/usr/bin/env python3
"""Integration checks for the LVS substrate contract and strict defaults."""

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


def run_lvs(tempdir, schematic, filters=None, input_layout=LAYOUT, layer_mode=None):
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
    if layer_mode:
        command.extend(["-rd", "layer_mode=" + layer_mode])
    if filters:
        command.extend(["-rd", "filters=" + filters])
    return subprocess.run(command, text=True, capture_output=True)


def prepare_fab_layout(tempdir):
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

    def test_strict_default_exposes_unmodelled_pad_parasitics(self):
        with tempfile.TemporaryDirectory() as tempdir:
            result = run_lvs(tempdir, REFERENCE)
            self.assertIn("ICPS2023_5 LVS NO MATCH", result.stdout + result.stderr)

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
