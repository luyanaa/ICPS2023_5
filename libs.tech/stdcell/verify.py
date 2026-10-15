#!/usr/bin/env python3
"""Canonical, strict physical checks for generated ICPS2023_5 1M layouts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from xml.etree import ElementTree

import klayout.db as pya

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "icps2023_5_1m"
DRC = ROOT / "libs.tech/klayout/drc/icps2023_5.drc"
LVS = ROOT / "libs.tech/klayout/lvs/icps2023_5.lvs"
PEX = ROOT / "libs.tech/klayout/pex/icps2023_5_pex.py"
ASSEMBLER = ROOT / "libs.tech/ngspice/tools/assemble_postlayout.py"
MODELS = ROOT / "libs.tech/ngspice/icps2023_5.lib.spice"
ALLOWED_LAYERS = {(1, 0), (2, 0), (3, 0), (5, 0), (7, 0), (8, 0),
                  (13, 0), (14, 0), (18, 0), (19, 0), (21, 0)}


class VerificationError(RuntimeError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def environment():
    result = os.environ.copy()
    result["ICPS2023_5_PROCESS_PROFILE"] = PROFILE
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result


def run_command(command, log_path, *, cwd=None, timeout=300):
    result = subprocess.run([str(part) for part in command], capture_output=True,
                            text=True, cwd=cwd, env=environment(), timeout=timeout)
    text = result.stdout + result.stderr
    Path(log_path).write_text(text, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("command failed (%d): %s; log %s" %
                           (result.returncode, command[0], log_path))
    return text


def _preflight(gds_path, top_name):
    layout = pya.Layout()
    layout.read(str(gds_path))
    top = layout.cell(top_name)
    if top is None:
        raise ValueError("GDS has no top cell %s" % top_name)
    for index in layout.layer_indices():
        info = layout.get_info(index)
        if not top.begin_shapes_rec(index).at_end() and (info.layer, info.datatype) not in ALLOWED_LAYERS:
            raise ValueError("generated 1M layout uses unsupported layer %d/%d" %
                             (info.layer, info.datatype))
    def region(layer):
        index = layout.find_layer(pya.LayerInfo(layer, 0))
        return pya.Region() if index is None else pya.Region(top.begin_shapes_rec(index))
    forbidden = region(7) & region(5) & region(3)
    if not forbidden.is_empty():
        raise ValueError("CNT overlaps TiN and SOI: direct TiN/diffusion contact is unqualified")
    return {"dbu_um": layout.dbu, "bbox_um": [value * layout.dbu for value in
            (top.bbox().left, top.bbox().bottom, top.bbox().right, top.bbox().top)]}


def verify_physical(gds_path, reference_path, top_name, output_dir, *, extract=True):
    """Return evidence only after physical checks; failures retain their stage result."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    gds_path, reference_path = Path(gds_path).resolve(), Path(reference_path).resolve()
    klayout = shutil.which("klayout")
    evidence = {"top": top_name, "process_profile": PROFILE, "strict_drc": False,
                "strict_lvs": False, "pex": False, "assembly": False,
                "gds_sha256": sha256(gds_path), "reference_sha256": sha256(reference_path),
                "deck_sha256": {"drc": sha256(DRC), "lvs": sha256(LVS), "pex": sha256(PEX)}}
    try:
        if klayout is None:
            raise RuntimeError("KLayout executable required; use the root Nix devShell")
        evidence["geometry"] = _preflight(gds_path, top_name)
        drc_report = output_dir / "strict.drc.lyrdb"
        run_command([klayout, "-b", "-r", DRC, "-rd", "input=" + str(gds_path),
                     "-rd", "top=" + top_name, "-rd", "report=" + str(drc_report),
                     "-rd", "process_profile=" + PROFILE, "-rd", "strict=true"],
                    output_dir / "drc.log", cwd=output_dir)
        report_root = ElementTree.parse(drc_report).getroot()
        items = report_root.find("items")
        if items is None:
            raise RuntimeError("DRC report has no items collection")
        markers = [item.findtext("category", "") for item in items.findall("item")]
        evidence["drc_markers"] = markers
        if markers:
            raise RuntimeError("strict DRC failed: %d markers" % len(markers))
        evidence["strict_drc"] = True
        extracted = output_dir / "extracted.cir"
        lvs_log = run_command([klayout, "-b", "-r", LVS, "-rd", "input=" + str(gds_path),
                               "-rd", "top=" + top_name, "-rd", "schematic=" + str(reference_path),
                               "-rd", "report=" + str(output_dir / "strict.lvs.lyrdb"),
                               "-rd", "output=" + str(extracted), "-rd", "filters=off",
                               "-rd", "process_profile=" + PROFILE], output_dir / "lvs.log", cwd=output_dir)
        if "ICPS2023_5 LVS MATCH (mismatches=0)" not in lvs_log:
            raise RuntimeError("strict LVS mismatch; see %s" % (output_dir / "lvs.log"))
        evidence["strict_lvs"] = True
        evidence["extracted_spice"] = str(extracted)
        if extract:
            pex_path = output_dir / "parasitics.spice"
            run_command([sys.executable, PEX, "--input", gds_path, "--top", top_name,
                         "--output", pex_path, "--process-profile", PROFILE],
                        output_dir / "pex.log", cwd=output_dir)
            evidence["pex"] = True
            assembled = output_dir / "postlayout.spice"
            loader = importlib.util.spec_from_file_location("icps_stdcell_assembly", ASSEMBLER)
            module = importlib.util.module_from_spec(loader)
            loader.loader.exec_module(module)
            evidence["assembly_stats"] = module.assemble(extracted, pex_path, reference_path,
                                                          top_name, assembled, model_library=MODELS)
            evidence["assembly"] = True
            evidence["postlayout_spice"] = str(assembled)
            evidence["pex_sha256"] = sha256(pex_path)
        evidence["passed"] = True
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, ElementTree.ParseError) as error:
        evidence["passed"] = False
        evidence["error"] = str(error)
        (output_dir / "physical.json").write_text(json.dumps(evidence, indent=2) + "\n")
        raise VerificationError(str(error), evidence) from error
    (output_dir / "physical.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence
