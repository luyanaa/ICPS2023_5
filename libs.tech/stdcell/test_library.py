#!/usr/bin/env python3
"""Structural tests for stdcell publication provenance and boundary eligibility."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "libs.ref/ICPS2023_5_gen_sc/tools"))

import library
import logic
import make_gen_sc
import model_binding


class LibraryValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The source is the actual strict-verified publication; each test mutates
        # only a private copy of its GDS/LEF/SPICE and manifest.
        cls.source_manifest = library.load_library()
        cls.source_root = library.LIBRARY

    def setUp(self) -> None:
        self._copy_publication()

    def _copy_publication(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="stdcell_library_test_")
        self.addCleanup(temporary.cleanup)
        self.temporary = temporary
        self.root = Path(temporary.name)
        target_doc = self.root / "doc"
        target_doc.mkdir(parents=True)
        self.manifest_path = target_doc / "cell_manifest.json"
        shutil.copyfile(self.source_root / "doc/cell_manifest.json", self.manifest_path)
        logical_spice = self.source_manifest["spice_file"]
        target_logical_spice = self.root / logical_spice
        target_logical_spice.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source_root / logical_spice, target_logical_spice)
        for record in self.source_manifest["cells"]:
            if record["status"] != "strict_physical_pass":
                continue
            for relative in record["views"].values():
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.source_root / relative, target)
        self.cell = next(record["cell"] for record in self.source_manifest["cells"]
                         if record["status"] == "strict_physical_pass")

    def _manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _record(self, manifest: dict) -> dict:
        return next(record for record in manifest["cells"] if record["cell"] == self.cell)

    def _write_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n",
                                      encoding="utf-8")

    def _refresh_view_hashes(self, manifest: dict, record: dict) -> None:
        record["fingerprints"] = make_gen_sc._fingerprints(
            record, self.root, manifest["provenance"], manifest["spice_file_sha256"])

    def test_loads_only_published_cells_with_bounded_generator_geometry(self):
        manifest = library.load_library(self.manifest_path)
        physical = library.physical_manifests(self.manifest_path)
        record = physical[self.cell]
        self.assertEqual(record["boundary_um"], [0.0, 0.0,
                         record["cell_width_um"], record["cell_height_um"]])
        self.assertEqual(record["generator_manifest"]["boundary_layer"], [100, 0])
        self.assertIsNone(record["site_width_um"])
        self.assertEqual(record["source_spec"], logic.cell_specs()[self.cell])

    def test_changed_parameters_and_source_spec_are_ineligible(self):
        manifest = self._manifest()
        record = self._record(manifest)
        record["parameters"]["height_um"] += 10.0
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

        self._copy_publication()
        manifest = self._manifest()
        record = self._record(manifest)
        current_width = record["parameters"]["width_um"]
        if current_width is None:
            current_width = record["source_spec"]["mos"][0]["w_um"]
        record["parameters"]["width_um"] = current_width + 1.0
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

        self._copy_publication()
        manifest = self._manifest()
        record = self._record(manifest)
        record["source_spec"]["mos"][0]["w_um"] += 1.0
        record["fingerprints"]["source_spec_sha256"] = make_gen_sc._canonical_digest(
            record["source_spec"])
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

    def test_changed_source_fingerprint_is_ineligible(self):
        manifest = self._manifest()
        manifest["provenance"]["logic_sha256"] = "0" * 64
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

    def test_stale_gds_lef_and_spice_fingerprints_are_ineligible(self):
        for view, directory, suffix in (
                ("gds", "gds", ".gds"),
                ("lef", "lef", ".lef"),
                ("spice", "spice/physical", ".spice")):
            with self.subTest(view=view):
                self._copy_publication()
                path = self.root / directory / f"{self.cell}{suffix}"
                path.write_bytes(path.read_bytes() + b"\n* changed view\n")
                with self.assertRaises(library.LibraryValidationError):
                    library.load_library(self.manifest_path)

    def test_lef_and_spice_content_cannot_be_reblessed_by_updating_hashes(self):
        manifest = self._manifest()
        record = self._record(manifest)
        lef_path = self.root / record["views"]["lef"]
        lef_path.write_text(lef_path.read_text(encoding="utf-8") + "# stale\n",
                            encoding="utf-8")
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

        self._copy_publication()
        manifest = self._manifest()
        record = self._record(manifest)
        spice_path = self.root / record["views"]["spice"]
        spice_path.write_text(spice_path.read_text(encoding="utf-8") + "* stale\n",
                              encoding="utf-8")
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaises(library.LibraryValidationError):
            library.load_library(self.manifest_path)

    def test_gds_without_reserved_boundary_is_not_physically_loadable(self):
        manifest = self._manifest()
        record = self._record(manifest)
        gds = self.root / record["views"]["gds"]
        layout = library.db.Layout()
        layout.read(str(gds))
        cell = layout.cell(self.cell)
        boundary_layer = layout.find_layer(library.db.LayerInfo(100, 0))
        self.assertIsNotNone(boundary_layer)
        cell.shapes(boundary_layer).clear()
        layout.write(str(gds))
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaisesRegex(library.LibraryValidationError, "boundary"):
            library.load_library(self.manifest_path)

    def test_removing_edge_protection_is_rejected_even_with_refreshed_view_hashes(self):
        manifest = self._manifest()
        record = self._record(manifest)
        gds = self.root / record["views"]["gds"]
        layout = library.db.Layout()
        layout.read(str(gds))
        cell = layout.cell(self.cell)
        diff = library.db.Region(cell.begin_shapes_rec(layout.find_layer(3, 0)))
        island = next(diff.each_merged()).bbox()
        gap = round(1.0 / layout.dbu)
        low, high = island.top - gap, island.top + gap
        ml1 = layout.find_layer(8, 0)
        boxes = [shape.box for shape in cell.shapes(ml1).each()]
        cell.shapes(ml1).clear()
        for box in boxes:
            if box.top <= low or box.bottom >= high:
                cell.shapes(ml1).insert(box)
                continue
            if box.bottom < low:
                cell.shapes(ml1).insert(
                    library.db.Box(box.left, box.bottom, box.right, low))
            if box.top > high:
                cell.shapes(ml1).insert(
                    library.db.Box(box.left, high, box.right, box.top))
        layout.write(str(gds))
        self._refresh_view_hashes(manifest, record)
        self._write_manifest(manifest)
        with self.assertRaisesRegex(library.LibraryValidationError, "SOI-edge protection"):
            library.load_library(self.manifest_path)


class ModelBindingTests(unittest.TestCase):
    """The library evidence path and the benches must not silently mix engines."""

    def test_library_provenance_matches_the_bound_model_files(self):
        binding = model_binding.load()
        provenance = library._current_provenance()
        self.assertEqual(provenance["model_sha256"],
                         hashlib.sha256(binding["library_path"].read_bytes()).hexdigest())
        self.assertEqual(provenance["model_include_sha256"],
                         hashlib.sha256(binding["include_path"].read_bytes()).hexdigest())
        self.assertEqual(provenance["model_binding"]["engine"], binding["engine"])
        self.assertEqual(provenance["model_binding"]["engine_level"], binding["engine_level"])
        self.assertEqual(provenance["model_binding"]["library_sha256"],
                         provenance["model_sha256"])
        manifest = json.loads(library.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["provenance"], provenance)

    def test_config_binds_the_fd_bridge_card_to_xyce(self):
        binding = model_binding.load()
        self.assertEqual(binding["engine"], "xyce")
        self.assertTrue(binding["library"].endswith("b4soi_fd_bridge_experimental.lib.spice"))
        self.assertTrue(binding["include"].endswith("b4soi_fd_bridge_experimental.inc"))
        self.assertEqual(binding["engine_level"], 70450)
        self.assertEqual(binding["section"], "b4soi")
        self.assertIn("Xyce", binding["engine_version"])
        self.assertIn("ngspice", binding["provenance"])
        second = json.loads(
            (HERE / "config_2x2.json").read_text(encoding="utf-8"))
        self.assertEqual(second["model"], json.loads(
            (HERE / "config.json").read_text(encoding="utf-8"))["model"])

    def test_characterization_benches_keep_the_nominal_card(self):
        import characterize
        nominal = model_binding.nominal()
        self.assertEqual(characterize.MODELS, nominal["library_path"])
        self.assertEqual(characterize.MODEL_CARD, nominal["include_path"])
        self.assertNotEqual(characterize.MODELS, model_binding.load()["library_path"])
        self.assertEqual(nominal["engine"], "ngspice")


class RowSiteContractTests(unittest.TestCase):
    """Published cells must be row-placeable and share one power-rail geometry.

    The LibreLane profiles select `PLACE_SITE ICPS2023_5_ROW500`, so every
    published cell has to declare that site at exactly its height, and a
    row-rail PDN only powers every cell if the VDD and VSS pin bands are the
    same for all of them.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = library.load_library(check=False)
        cls.published = [record for record in cls.manifest["cells"]
                         if record["status"] == "strict_physical_pass"]

    def _pins(self, text: str, name: str) -> list[tuple[float, float, float, float]]:
        match = re.search(r"^  PIN %s$.*?^  END %s$" % (name, name), text,
                          re.S | re.M)
        self.assertIsNotNone(match, f"{name} pin is missing")
        return [tuple(float(value) for value in rect)
                for rect in re.findall(r"^        RECT (\S+) (\S+) (\S+) (\S+) ;$",
                                       match.group(0), re.M)]

    def test_published_cells_declare_the_row_site_and_share_power_bands(self):
        self.assertTrue(self.published, "no published cell")
        bands: dict[str, tuple[float, float]] = {}
        for record in self.published:
            cell = record["cell"]
            with self.subTest(cell=cell):
                lef = (library.LIBRARY / record["views"]["lef"]).read_text(encoding="utf-8")
                self.assertIn(f"  SITE {library.ROW_SITE_NAME} ;", lef)
                self.assertIn(f"BY {library.ROW_SITE_HEIGHT_UM:g} ;", lef)
                width = float(record["cell_width_um"])
                for pin in ("VDD", "VSS"):
                    rects = self._pins(lef, pin)
                    self.assertTrue(rects, f"{pin} has no port geometry")
                    low = min(rect[1] for rect in rects)
                    high = max(rect[3] for rect in rects)
                    self.assertGreaterEqual(high - low, 6.0, f"{pin} is narrower than ML1 minimum")
                    self.assertGreaterEqual(low, 0.0)
                    self.assertLessEqual(high, library.ROW_SITE_HEIGHT_UM)
                    bands.setdefault(pin, (low, high))
                    self.assertEqual((low, high), bands[pin],
                                     f"{pin} band differs from the first published cell")
                    left = min(rect[0] for rect in rects)
                    right = max(rect[2] for rect in rects)
                    self.assertLessEqual(left, 0.05, f"{pin} does not reach the left edge")
                    self.assertGreaterEqual(right, width - 0.05,
                                            f"{pin} does not reach the right edge")
        self.assertLess(bands["VSS"][1], bands["VDD"][0],
                        "the VSS and VDD rail bands overlap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
