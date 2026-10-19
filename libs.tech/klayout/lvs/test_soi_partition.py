#!/usr/bin/env python3
"""Actual-GDS LVS/PEX/ERC agreement on ohmic and nonohmic SOI boundaries.

These are topology fixtures, not manufacturing-rule goldens or calibrated
semiconductor transport models. Device bodies are deliberately kept out of the
DC conductor graph; only explicit contacts join semiconductor domains to ML1.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import klayout.db as db

ROOT = Path(__file__).resolve().parents[3]
KLAYOUT = shutil.which("klayout")
sys.path.insert(0, str(ROOT / "libs.tech/ngspice/tools"))
sys.path.insert(0, str(ROOT / "libs.tech/klayout/pex"))
import check_postlayout_erc as erc
from test_pex import equivalent_resistance, json_records

LVS = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PREP = ROOT / "libs.tech/klayout/drc/icps2023_5_fab_prep.drc"
PEX = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLE = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
PROFILE = "icps2023_5_1m"


def build_fixture(path: Path, kind: str, invalid_contact: bool = False) -> str:
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("SOI_" + kind.upper())

    def box(layer, coordinates):
        top.shapes(layout.layer(layer, 0)).insert(
            db.Box(*(round(value / layout.dbu) for value in coordinates)))

    def contact(name, x, y=10.0):
        box(7, (x - 2.5, y - 2.5, x + 2.5, y + 2.5))
        box(8, (x - 6.5, y - 6.5, x + 6.5, y + 6.5))
        top.shapes(layout.layer(13, 0)).insert(
            db.Text(name, db.Trans(db.Point(round(x / layout.dbu),
                                           round(y / layout.dbu)))))

    box(3, (0, 0, 60, 20))
    if kind in ("nmos", "pmos"):
        box(19 if kind == "nmos" else 18, (-2, -2, 62, 22))
        box(5, (27, -18.5, 33, 26))
        box(5, (23.5, -18.5, 36.5, -5.5))
        if kind == "nmos":
            box(21, (27, 0, 33, 20))
        contact("S", 6.5)
        contact("D", 53.5)
        contact("G", 30, -12)
    elif kind in ("unknown_neck", "shared_diffusion"):
        # Capacitor devices keep the real LVS nets observable without adding
        # a DC device path between the two contact electrodes.
        if kind == "shared_diffusion":
            box(19, (-2, -2, 62, 22))
        else:
            box(19, (-2, -2, 18, 22))
            box(19, (42, -2, 62, 22))
        for x in (12, 44):
            box(16, (x, 0, x + 4, 20))
            box(5, (x, 0, x + 4, 20))
        contact("A", 6.5)
        contact("B", 53.5)
        if invalid_contact:
            contact("UNKNOWN", 30)
    elif kind == "pn":
        # DIO explicitly owns the junction; the electrodes are not one wire.
        box(17, (0, 0, 60, 20))
        box(18, (-2, -2, 18, 22))
        box(19, (22, -2, 62, 22))
        contact("A", 6.5)
        contact("B", 53.5)
        if invalid_contact:
            contact("JUNCTION", 20)
    else:
        raise ValueError(kind)
    layout.write(str(path))
    return top.name


def reference_text(top: str, kind: str) -> str:
    if kind in ("nmos", "pmos"):
        ports = "D G S"
        body = "M1 D G S SUB %s L=6U W=20U" % (
            "NCH" if kind == "nmos" else "PCH")
    elif kind in ("unknown_neck", "shared_diffusion"):
        ports = "A B"
        body = "C1 A FLOAT_A 184f\nC2 B FLOAT_B 184f"
    elif kind == "pn":
        ports = "A B"
        body = "D1 A B D"
    else:
        raise ValueError(kind)
    return (".GLOBAL SUB\n.SUBCKT %s %s\n%s\n.ENDS %s\n"
            "XTOP %s %s\n.END\n" % (top, ports, body, top, ports, top))


def run(command):
    env = dict(os.environ, ICPS2023_5_PROCESS_PROFILE=PROFILE)
    return subprocess.run(command, capture_output=True, text=True, env=env,
                          timeout=120)


def prepare_mode(design: Path, mode: str, work: Path) -> Path:
    if mode == "design":
        return design
    fab = work / "fab.gds"
    result = run([KLAYOUT, "-b", "-r", str(PREP), "-rd", "input=" + str(design),
                  "-rd", "output=" + str(fab), "-rd", "process_profile=" + PROFILE])
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return fab


def extract(gds: Path, top: str, mode: str, work: Path, kind: str):
    reference = work / "reference.cir"
    reference.write_text(reference_text(top, kind), encoding="utf-8")
    lvs = work / "lvs.cir"
    lvs_result = run([
        KLAYOUT, "-b", "-r", str(LVS), "-rd", "input=" + str(gds),
        "-rd", "top=" + top, "-rd", "layer_mode=" + mode,
        "-rd", "process_profile=" + PROFILE,
        "-rd", "schematic=" + str(reference), "-rd", "output=" + str(lvs),
        "-rd", "report=" + str(work / "lvs.lyrdb")])
    pex = work / "pex.spice"
    pex_result = run([
        sys.executable, str(PEX), "--input", str(gds), "--top", top,
        "--layer-mode", mode, "--process-profile", PROFILE,
        "--output", str(pex)])
    return lvs_result, lvs, pex_result, pex, reference


def net_ids(text: str):
    return {fields[3]: fields[2] for line in text.splitlines()
            if len(fields := line.split()) == 4 and fields[:2] == ["*", "net"]}


def ml1_node(text: str, logical: str) -> str:
    nodes = {row["physical"] for row in json_records(text, "* PORT_MAP ")
             if row["logical"] == logical and row["layer"] == "ML1"}
    if len(nodes) != 1:
        raise AssertionError("expected one ML1 anchor for %s, got %s" % (logical, nodes))
    return nodes.pop()


@unittest.skipUnless(KLAYOUT, "KLayout executable required")
class SoiPartitionTests(unittest.TestCase):
    def test_mos_terminals_agree_across_lvs_pex_erc_and_assembly(self):
        for kind in ("nmos", "pmos"):
            for mode in ("design", "fab"):
                with self.subTest(kind=kind, mode=mode), tempfile.TemporaryDirectory() as temp:
                    work = Path(temp)
                    design = work / "drawing.gds"
                    top = build_fixture(design, kind)
                    gds = prepare_mode(design, mode, work)
                    lrun, lvs, prun, pex, reference = extract(gds, top, mode, work, kind)
                    self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                    self.assertIn("LVS MATCH", lrun.stdout)
                    self.assertEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                    ids = net_ids(lvs.read_text())
                    self.assertNotEqual(ids["D"], ids["S"])
                    pex_text = pex.read_text()
                    self.assertIsNone(equivalent_resistance(
                        pex_text, ml1_node(pex_text, "D"), ml1_node(pex_text, "S")))
                    geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                    self.assertTrue(all(row["status"] == "legal"
                                        for row in geometry["contacts"]), geometry["contacts"])
                    roots = [erc._physical_anchor({"kind": "pin", "layer": "ML1",
                             "point_um": [x, 10]}, geometry, None)["component_id"]
                             for x in (6.5, 53.5)]
                    self.assertNotEqual(*roots)
                    assembled = work / "assembled.spice"
                    assembly = run([sys.executable, str(ASSEMBLE), "--lvs", str(lvs),
                                    "--pex", str(pex), "--schematic", str(reference),
                                    "--top", top, "--output", str(assembled)])
                    self.assertEqual(assembly.returncode, 0,
                                     assembly.stdout + assembly.stderr)
                    self.assertTrue(assembled.is_file())

    def test_unknown_film_cannot_bypass_distinct_implanted_contacts(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                design = work / "drawing.gds"
                top = build_fixture(design, "unknown_neck")
                gds = prepare_mode(design, mode, work)
                lrun, lvs, prun, pex, _ = extract(gds, top, mode, work, "unknown_neck")
                self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                ids = net_ids(lvs.read_text())
                self.assertNotEqual(ids["A"], ids["B"])
                self.assertEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                text = pex.read_text()
                self.assertIsNone(equivalent_resistance(
                    text, ml1_node(text, "A"), ml1_node(text, "B")))
                geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                roots = [erc._physical_anchor({"kind": "pin", "layer": "ML1",
                         "point_um": [x, 10]}, geometry, None)["component_id"]
                         for x in (6.5, 53.5)]
                self.assertNotEqual(*roots)

    def test_declared_pn_junction_is_not_an_ohmic_short(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                design = work / "drawing.gds"
                top = build_fixture(design, "pn")
                gds = prepare_mode(design, mode, work)
                lrun, lvs, prun, pex, _ = extract(gds, top, mode, work, "pn")
                self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                ids = net_ids(lvs.read_text())
                self.assertNotEqual(ids["A"], ids["B"])
                diodes = [line.split() for line in lvs.read_text().splitlines()
                          if line.startswith("D")]
                self.assertEqual(len(diodes), 1)
                self.assertEqual(set(diodes[0][1:3]), {ids["A"], ids["B"]})
                self.assertEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                text = pex.read_text()
                self.assertIsNone(equivalent_resistance(
                    text, ml1_node(text, "A"), ml1_node(text, "B")))
                geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                roots = [erc._physical_anchor({"kind": "pin", "layer": "ML1",
                         "point_um": [x, 10]}, geometry, None)["component_id"]
                         for x in (6.5, 53.5)]
                self.assertNotEqual(*roots)

    def test_contact_crossing_a_pn_boundary_fails_before_output(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                design = work / "drawing.gds"
                top = build_fixture(design, "pn", invalid_contact=True)
                gds = prepare_mode(design, mode, work)
                lrun, lvs, prun, pex, _ = extract(gds, top, mode, work, "pn")
                self.assertNotEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                self.assertFalse(lvs.exists())
                self.assertNotEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                self.assertFalse(pex.exists())
                geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                self.assertTrue(any(row["status"] == "illegal" for row in geometry["contacts"]))

    def test_unknown_film_contact_never_creates_a_bypass(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                design = work / "drawing.gds"
                top = build_fixture(design, "unknown_neck", invalid_contact=True)
                gds = prepare_mode(design, mode, work)
                lrun, lvs, prun, pex, _ = extract(gds, top, mode, work, "unknown_neck")
                self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                ids = net_ids(lvs.read_text())
                self.assertNotEqual(ids["A"], ids["B"])
                self.assertNotEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                self.assertFalse(pex.exists())
                geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                self.assertTrue(any(row["status"] == "illegal" for row in geometry["contacts"]))
                roots = [erc._physical_anchor({"kind": "pin", "layer": "ML1",
                         "point_um": [x, 10]}, geometry, None)["component_id"]
                         for x in (6.5, 30, 53.5)]
                self.assertEqual(len(set(roots)), 3)

    def test_legal_same_polarity_diffusion_remains_connected(self):
        for mode in ("design", "fab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                design = work / "drawing.gds"
                top = build_fixture(design, "shared_diffusion")
                gds = prepare_mode(design, mode, work)
                lrun, lvs, prun, pex, _ = extract(gds, top, mode, work, "shared_diffusion")
                self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                names = [line.split()[2] for line in lvs.read_text().splitlines()
                         if line.startswith("C")]
                self.assertEqual(len(names), 2)
                self.assertEqual(names[0], names[1])
                self.assertEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                text = pex.read_text()
                resistance = equivalent_resistance(
                    text, ml1_node(text, "A"), ml1_node(text, "B"))
                self.assertIsNotNone(resistance)
                self.assertGreater(resistance, 0)
                geometry = erc._layout_inputs(gds, top, None, layer_mode=mode)
                roots = [erc._physical_anchor({"kind": "pin", "layer": "ML1",
                         "point_um": [x, 10]}, geometry, None)["component_id"]
                         for x in (6.5, 53.5)]
                self.assertEqual(*roots)


if __name__ == "__main__":
    unittest.main()
