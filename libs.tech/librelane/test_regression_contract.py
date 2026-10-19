#!/usr/bin/env python3
"""Contract checks for the checked-in LibreLane routable regression.

This does not run LibreLane. It keeps the regression manifest, the profile
row contract, and the rails-only 1M PDN configuration from drifting apart.
`run_smoke_regression.py` is the executable regression that consumes the same
manifest when a LibreLane runtime is available.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

LIBRELIB = Path(__file__).resolve().parent
ROOT = LIBRELIB.parents[1]
MANIFEST = LIBRELIB / "regression.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RegressionManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _load_json(MANIFEST)

    def test_manifest_has_one_explicit_routable_entry(self) -> None:
        routable = self.manifest["routable"]
        self.assertTrue(routable, "at least one routable regression is required")
        ids = [entry["id"] for entry in routable]
        self.assertEqual(len(ids), len(set(ids)), "routable ids must be unique")
        for entry in routable:
            self.assertEqual(entry["status"], "routable")
            self.assertEqual(set(entry["expected"]), {
                "route__drc_errors",
                "klayout__drc_error__count",
                "design__disconnected_pin__count",
                "design__critical_disconnected_pin__count",
                "design__power_grid_violation__count",
                "design__instance__count",
            })
            for value in entry["expected"].values():
                self.assertIsInstance(value, int)

    def test_routable_pair_uses_the_row700_and_rails_only_contract(self) -> None:
        for entry in self.manifest["routable"]:
            profile_path = LIBRELIB / entry["profile"]
            design_path = LIBRELIB / entry["design"]
            self.assertTrue(profile_path.is_file(), profile_path)
            self.assertTrue(design_path.is_file(), design_path)

            profile = _load_json(profile_path)
            design = _load_json(design_path)
            self.assertEqual(profile["PLACE_SITE"], "ICPS2023_5_ROW700")
            self.assertNotEqual(profile.get("PDN_VERTICAL_LAYER"), "TIN")

            die = design["DIE_AREA"]
            core = design["CORE_AREA"]
            self.assertEqual(core[1] % 700, 0, "core y origin must be a row boundary")
            self.assertEqual((core[3] - core[1]) % 700, 0,
                             "core height must be a whole number of rows")
            self.assertLessEqual(core[0], core[2])
            self.assertLessEqual(core[1], core[3])
            self.assertGreaterEqual(core[0], die[0])
            self.assertGreaterEqual(core[1], die[1])
            self.assertLessEqual(core[2], die[2])
            self.assertLessEqual(core[3], die[3])

            pdn_cfg = profile.get("PDN_CFG")
            self.assertIsInstance(pdn_cfg, str, "1M regression must pin PDN_CFG")
            tcl_path = ROOT / pdn_cfg.split("::", 1)[-1]
            self.assertTrue(tcl_path.is_file(), tcl_path)
            tcl = tcl_path.read_text(encoding="utf-8")
            code = "\n".join(line for line in tcl.splitlines()
                             if not line.lstrip().startswith("#"))
            self.assertIn("-followpins", code)
            self.assertNotIn("PDN_VERTICAL_LAYER", code)
            self.assertNotIn("TIN", code.upper())

    def test_excluded_runs_have_reasons_and_are_not_routable(self) -> None:
        routable_ids = {entry["id"] for entry in self.manifest["routable"]}
        for entry in self.manifest["excluded"]:
            self.assertEqual(entry["status"], "excluded")
            self.assertNotIn(entry["id"], routable_ids)
            self.assertTrue(entry["reason"].strip())
            self.assertTrue((LIBRELIB / entry["profile"]).is_file())
            self.assertTrue((LIBRELIB / entry["design"]).is_file())


if __name__ == "__main__":
    unittest.main()
