#!/usr/bin/env python3
"""Run every routable entry in `regression.json` and assert its metrics.

The executable flow needs a LibreLane/OpenROAD runtime. Without one this
script reports a skip unless `--require-librelane` is passed, so the JSON
contract test can still run in the minimal repository shell.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIBRELIB = Path(__file__).resolve().parent
ROOT = LIBRELIB.parents[1]
MANIFEST = LIBRELIB / "regression.json"
RUNS = LIBRELIB / "designs" / "runs"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _new_runs(before: set[Path]) -> list[Path]:
    return sorted(
        (path for path in RUNS.glob("RUN_*") if path.is_dir() and path not in before),
        key=lambda path: path.stat().st_mtime,
    )


def _find_metrics(run_dirs: list[Path], design_name: str) -> tuple[Path, dict]:
    for run in reversed(run_dirs):
        metrics = run / "final" / "metrics.json"
        def_path = run / "final" / "def" / f"{design_name}.def"
        if metrics.is_file() and def_path.is_file():
            return run, _load_json(metrics)
    raise RuntimeError(f"no completed run with final metrics for {design_name}")


def _special_net_block(def_path: Path) -> str:
    lines = def_path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("SPECIALNETS 2 ;")
        end = lines.index("END SPECIALNETS")
    except ValueError as error:
        raise AssertionError(f"{def_path}: special-net block is missing") from error
    return "\n".join(lines[start:end + 1])


def _check_special_nets(run: Path, design_name: str) -> None:
    block = _special_net_block(run / "final" / "def" / f"{design_name}.def")
    if "TIN" in block:
        raise AssertionError("1M PDN must not contain TiN power geometry")
    if "M1" not in block or "SHAPE FOLLOWPIN" not in block:
        raise AssertionError("1M PDN must contain M1 followpin rails")


def _find_command(profile: Path, design: Path, pdk_root: Path, shell: Path) -> list[str]:
    base = [
        "--manual-pdk",
        "--pdk-root", str(pdk_root),
        "--pdk", "ICPS2023_5",
        "--scl", "ICPS2023_5_gen_sc",
        str(profile),
        str(design),
    ]
    librelane = shutil.which("librelane")
    if librelane:
        return [librelane, *base]
    nix = shutil.which("nix")
    if nix and shell.is_dir():
        return [nix, "develop", str(shell), "--command", "librelane", *base]
    raise FileNotFoundError(
        "no LibreLane runtime found; install librelane or provide "
        "LIBRELANE_DEV_SHELL (defaults to ../librelane)"
    )


def _prepare_pdk_root(pdk_root: Path) -> None:
    pdk_root.mkdir(parents=True, exist_ok=True)
    target = pdk_root / "ICPS2023_5"
    if target.is_symlink() and target.resolve() != ROOT:
        target.unlink()
    if not target.exists():
        target.symlink_to(ROOT, target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-librelane", action="store_true",
                        help="fail instead of skipping when no runtime is available")
    parser.add_argument("--pdk-root", type=Path,
                        default=Path(os.environ.get("PDK_ROOT", "/tmp/icps2023_5_pdkroot")))
    parser.add_argument("--shell", type=Path,
                        default=Path(os.environ.get("LIBRELANE_DEV_SHELL", ROOT.parent / "librelane")))
    args = parser.parse_args()

    manifest = _load_json(MANIFEST)
    _prepare_pdk_root(args.pdk_root)

    for entry in manifest["routable"]:
        profile = LIBRELIB / entry["profile"]
        design = LIBRELIB / entry["design"]
        design_name = _load_json(design)["DESIGN_NAME"]
        try:
            command = _find_command(profile, design, args.pdk_root, args.shell)
        except FileNotFoundError as error:
            if args.require_librelane:
                print(f"FAIL: {error}", file=sys.stderr)
                return 2
            print(f"SKIP {entry['id']}: {error}")
            continue

        before = set(RUNS.glob("RUN_*"))
        print(f"RUN  {entry['id']}")
        completed = subprocess.run(command, cwd=ROOT)
        runs = _new_runs(before)
        if not runs:
            print(f"FAIL {entry['id']}: LibreLane created no run directory", file=sys.stderr)
            return 1
        try:
            run, metrics = _find_metrics(runs, design_name)
        except RuntimeError as error:
            print(f"FAIL {entry['id']}: {error}", file=sys.stderr)
            return 1
        failed = False
        for key, expected in entry["expected"].items():
            actual = metrics.get(key)
            if actual != expected:
                print(f"FAIL {entry['id']}: {key}={actual!r}, expected {expected!r}",
                      file=sys.stderr)
                failed = True
        try:
            _check_special_nets(run, design_name)
        except AssertionError as error:
            print(f"FAIL {entry['id']}: {error}", file=sys.stderr)
            failed = True
        if completed.returncode != 0:
            print(f"FAIL {entry['id']}: LibreLane exited {completed.returncode}",
                  file=sys.stderr)
            failed = True
        if failed:
            return 1
        print(f"PASS {entry['id']}: {run}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
