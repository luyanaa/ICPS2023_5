#!/usr/bin/env python3
"""Joint SOI contact contract: no single tool may certify a contact alone.

The gate binds actual DRC/LVS/PEX/ERC artifacts.  The fixtures here use real
tool runs from the partition regressions; synthetic artifacts appear only where
the point is the gate's own decision table, never as process evidence.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "libs.tech/ngspice/tools"))
import test_soi_partition as partition
import soi_contact_contract as contract

KLAYOUT = shutil.which("klayout")


def write_lyrdb(path: Path, categories: list[str]) -> None:
    root = ElementTree.Element("report-database")
    items = ElementTree.SubElement(root, "items")
    for category in categories:
        item = ElementTree.SubElement(items, "item")
        ElementTree.SubElement(item, "category").text = category
    ElementTree.ElementTree(root).write(path, encoding="utf-8",
                                        xml_declaration=True)


def erc_report(*, geometry: str, contacts: str, overall: str | None = None) -> dict:
    return {
        "status": overall,
        "contact_policy": "strict",
        "checks": {
            "gds_geometry": {"status": geometry},
            "illegal_contacts": {"status": contacts},
        },
    }


class ContractDecisionTableTest(unittest.TestCase):
    """The gate's own truth table, with synthetic artifacts as fixtures."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_all_legs_pass_only_when_every_tool_passes(self):
        drc = self.root / "drc.lyrdb"
        write_lyrdb(drc, [])
        pex = self.root / "pex.spice"
        pex.write_text(".SUBCKT TOP A B\n.ENDS TOP\n", encoding="utf-8")
        report = contract.evaluate_contract(
            drc_report=drc,
            lvs_log_text="ICPS2023_5 LVS MATCH (mismatches=0)\n",
            pex_output=pex,
            erc_report=erc_report(geometry="PASS", contacts="PASS", overall="PASS"),
        )
        self.assertEqual(report["result"], "PASS_SOI", report)
        self.assertEqual(report["reasons"], [])

    def test_lvs_match_alone_cannot_pass_the_contract(self):
        report = contract.evaluate_contract(
            lvs_log_text="ICPS2023_5 LVS MATCH (mismatches=0)\n",
        )
        self.assertEqual(report["legs"]["lvs"]["status"], "MATCH")
        self.assertEqual(report["result"], "FAIL_SOI")
        self.assertEqual(
            {reason.split(":")[0] for reason in report["reasons"]},
            {"drc", "pex", "erc"})

    def test_illegal_contact_fails_the_contract_even_with_lvs_match(self):
        pex = self.root / "pex.spice"
        report = contract.evaluate_contract(
            lvs_log_text="ICPS2023_5 LVS MATCH (mismatches=0)\n",
            pex_output=pex,
            erc_report=erc_report(geometry="PASS", contacts="FAIL", overall="FAIL"),
        )
        self.assertEqual(report["result"], "FAIL_SOI")
        self.assertEqual(report["legs"]["erc"]["status"], "FAIL")
        self.assertEqual(report["legs"]["pex"]["status"], "FAIL")

    def test_drc_markers_fail_the_contract(self):
        drc = self.root / "drc.lyrdb"
        write_lyrdb(drc, ["CNT.J TiN-ML1 Cnt not 5x5 um (golden CNT-J)"])
        report = contract.evaluate_contract(
            drc_report=drc,
            lvs_log_text="ICPS2023_5 LVS MATCH (mismatches=0)\n",
            pex_output=None,
            erc_report=erc_report(geometry="PASS", contacts="PASS"),
        )
        self.assertEqual(report["legs"]["drc"]["status"], "FAIL")
        self.assertEqual(report["result"], "FAIL_SOI")


@unittest.skipUnless(KLAYOUT, "KLayout executable required")
class RealArtifactContractTest(unittest.TestCase):
    """The same gate over actual tool runs, including the LVS/ERC asymmetry."""

    def test_illegal_landing_keeps_lvs_running_but_fails_the_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            design = work / "drawing.gds"
            top = partition.build_fixture(design, "unknown_neck", invalid_contact=True)
            for mode in ("design", "fab"):
                with self.subTest(mode=mode):
                    gds = partition.prepare_mode(design, mode, work)
                    lrun, lvs, prun, pex, _reference = partition.extract(
                        gds, top, mode, work, "unknown_neck")
                    self.assertEqual(lrun.returncode, 0, lrun.stdout + lrun.stderr)
                    self.assertNotEqual(prun.returncode, 0, prun.stdout + prun.stderr)
                    self.assertFalse(pex.exists())
                    report = contract.evaluate_contract(
                        lvs_log_text=lrun.stdout,
                        pex_output=pex,
                        erc_report=None,
                    )
                    # LVS still produced a topology verdict; the contract must
                    # refuse to turn that into a contact verdict.
                    self.assertIn(report["legs"]["lvs"]["status"],
                                  {"MATCH", "NO_MATCH"})
                    self.assertEqual(report["result"], "FAIL_SOI")
                    self.assertEqual(report["legs"]["pex"]["status"], "FAIL")
                    self.assertEqual(report["legs"]["erc"]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
