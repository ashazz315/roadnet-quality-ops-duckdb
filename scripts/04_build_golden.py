"""Rebuild the pinned Golden Network offline into a new output directory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import build_golden_snapshot


def code_version() -> str:
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return revision + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unavailable-see-code-sha256"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/reference/xuhui_osm")
    parser.add_argument("--output", type=Path, default=ROOT / "data/runtime/xuhui_golden")
    args = parser.parse_args()
    manifest = build_golden_snapshot(args.raw, args.output, code_version=code_version())
    print(json.dumps({"output": str(args.output), "network_version": manifest["run"]["network_version"], "summary": manifest["summary"]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
