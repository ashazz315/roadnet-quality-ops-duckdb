"""Generate the STEP 5 synthetic Benchmark offline in a new directory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.benchmark_pipeline import build_benchmark


def code_version() -> str:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return revision + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unavailable-see-code-sha256"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--golden", type=Path, default=ROOT / "data/runtime/xuhui_golden/manifest.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/runtime/benchmark_seed42"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config/benchmark.json")
    args = parser.parse_args()
    manifest = build_benchmark(
        args.golden,
        args.output,
        json.loads(args.config.read_bytes()),
        code_version=code_version(),
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "network_version": manifest["run"]["network_version"],
                "summary": manifest["summary"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
