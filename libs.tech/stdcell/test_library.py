#!/usr/bin/env python3
"""Structural tests for stdcell publication provenance and boundary eligibility."""
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
