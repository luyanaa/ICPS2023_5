#!/usr/bin/env python3
"""NMOS4 post-layout chain: LVS -> PEX -> assembly, design and fab masks.

The bounded contract: one NMOS4 tie gate, one contacted P+ dot. The assembled
MOS is five-terminal (`D G S E_SUB B nch`); PMOS4 is not part of this path.
Body-access resistance is not characterized; only the terminal node is mapped.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
GENERATOR = ROOT / "libs.tech/klayout/pcells/generate_1m_golden.rb"
PCELL_MACRO = ROOT / "libs.tech/klayout/pcells/pcells_v0.1.lym"
FAB_PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
LVS_DECK = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
PROFILE = "icps2023_5_1m"
REFERENCE = (".GLOBAL SUB\n"
             ".SUBCKT nmos4_min D G S B\n"
             "M1 D G S SUB B NCH L=6U W=20U\n"
             ".ENDS\n"
             "XTOP D G S B nmos4_min\n.END\n")


def environment():
    return dict(os.environ, ICPS2023_5_PROCESS_PROFILE=PROFILE)


def run(command):
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment(), timeout=180)


def records(text, prefix):
    return [json.loads(line[len(prefix):]) for line in text.splitlines()
            if line.startswith(prefix)]


@unittest.skipUnless(KLAYOUT, "KLayout executable required")
class Nmos4PostlayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._temp.name)
        generated = cls.work / "gds"
        generated.mkdir()
        result = run([KLAYOUT, "-b", "-r", str(GENERATOR),
                      "-rd", "output_dir=" + str(generated),
                      "-rd", "pcell_macro=" + str(PCELL_MACRO)])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.design = generated / "nmos4_min.gds"
        cls.fab = cls.work / "nmos4_min.fab.gds"
        result = run([KLAYOUT, "-b", "-r", str(FAB_PREP),
                      "-rd", "input=" + str(cls.design),
                      "-rd", "output=" + str(cls.fab),
                      "-rd", "process_profile=" + PROFILE])
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.reference = cls.work / "nmos4_min.cir"
        cls.reference.write_text(REFERENCE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def chain(self, mode):
        gds = self.design if mode == "design" else self.fab
        lvs = self.work / (mode + ".extracted.cir")
        pex = self.work / (mode + ".pex.spice")
        assembled = self.work / (mode + ".assembled.spice")
        result = run([KLAYOUT, "-b", "-r", str(LVS_DECK),
                      "-rd", "input=" + str(gds), "-rd", "top=nmos4_min",
                      "-rd", "layer_mode=" + mode,
                      "-rd", "schematic=" + str(self.reference),
                      "-rd", "output=" + str(lvs),
                      "-rd", "report=" + str(self.work / (mode + ".lyrdb"))])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ICPS2023_5 LVS MATCH (mismatches=0)",
                      result.stdout + result.stderr)
        result = run([sys.executable, str(PEX), "--input", str(gds),
                      "--top", "nmos4_min", "--layer-mode", mode,
                      "--output", str(pex)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run([sys.executable, str(ASSEMBLER), "--lvs", str(lvs),
                      "--pex", str(pex), "--schematic", str(self.reference),
                      "--top", "nmos4_min", "--output", str(assembled)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return lvs, pex, assembled

    def test_nmos4_body_tie_survives_lvs_pex_and_assembly(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode):
                lvs, pex, assembled = self.chain(mode)
                lvs_text = lvs.read_text(encoding="utf-8")
                self.assertTrue(any(line.startswith("M") and "NMOS4" in line
                                    for line in lvs_text.splitlines()), lvs_text)
                maps = records(pex.read_text(encoding="utf-8"), "* MOS_MAP ")
                self.assertEqual(len(maps), 1, maps)
                mapping = maps[0]
                self.assertEqual(mapping["polarity"], "NMOS4")
                self.assertIn("body", mapping)
                self.assertNotEqual(mapping["body"], mapping["source"])
                self.assertNotEqual(mapping["body"], mapping["drain"])
                text = assembled.read_text(encoding="utf-8")
                mos = [line for line in text.splitlines()
                       if re.match(r"^M\d{4}\s", line)]
                self.assertEqual(len(mos), 1, text)
                fields = mos[0].split()
                self.assertGreaterEqual(len(fields), 8, mos[0])
                self.assertEqual(fields[4], "E_SUB")
                self.assertEqual(fields[6], "nch")
                body_state = records(text, "* MOS_DEVICE_MAP ")
                self.assertEqual(body_state[0]["body_state"], "body_tied")
                self.assertEqual(body_state[0]["p"], fields[5])
                self.assertEqual(body_state[0]["e"], "E_SUB")


    def test_nmos4_body_net_participates_in_open_and_short_checks(self):
        lvs, pex, _assembled = self.chain("design")
        base = pex.read_text(encoding="utf-8")
        mapping = records(base, "* MOS_MAP ")[0]
        body, source, drain = mapping["body"], mapping["source"], mapping["drain"]

        def write_case(name, text):
            path = self.work / (name + ".pex.spice")
            path.write_text(text, encoding="utf-8")
            return path

        def resistor_map(name, a, b, value):
            return "* RESISTOR_MAP " + json.dumps({
                "name": name, "a": a, "b": b, "resistance_ohm": value,
                "material": "ML1", "a_location_um": None, "b_location_um": None,
                "geometry_qualification": "deliberate negative topology fixture",
            }, sort_keys=True, separators=(",", ":"))

        def inject(text, anchor_name, extra):
            out, injected = [], False
            for line in text.splitlines():
                if line.startswith("* MOS_MAP "):
                    record = json.loads(line[len("* MOS_MAP "):])
                    if anchor_name is not None:
                        record["body"] = anchor_name
                    line = "* MOS_MAP " + json.dumps(
                        record, sort_keys=True, separators=(",", ":"))
                if line.upper().startswith(".ENDS") and extra and not injected:
                    out.append(extra)
                    injected = True
                out.append(line)
            return "\n".join(out) + "\n"

        # 1. Body points at an isolated node: the LVS body net now spans two
        # disconnected PEX components and must be rejected as an open.
        isolated = inject(
            base, "FLOAT_BODY_A",
            resistor_map("RBODYISO", "FLOAT_BODY_A", "FLOAT_BODY_B", 100.0)
            + "\nRBODYISO FLOAT_BODY_A FLOAT_BODY_B 100")
        path = write_case("nmos4_body_open", isolated)
        result = run([sys.executable, str(ASSEMBLER), "--lvs", str(lvs),
                      "--pex", str(path), "--schematic", str(self.reference),
                      "--top", "nmos4_min", "--output",
                      str(self.work / "nmos4_body_open.spice")])
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PEX open", result.stdout + result.stderr)

        # 2. A finite resistor joins the body P network to the source N+
        # network: that merges two LVS nets and must be rejected as a short.
        shorted = inject(
            base, None,
            resistor_map("RBODYSHORT", body, source, 100.0)
            + "\nRBODYSHORT %s %s 100" % (body, source))
        path = write_case("nmos4_body_short", shorted)
        result = run([sys.executable, str(ASSEMBLER), "--lvs", str(lvs),
                      "--pex", str(path), "--schematic", str(self.reference),
                      "--top", "nmos4_min", "--output",
                      str(self.work / "nmos4_body_short.spice")])
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PEX short", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
