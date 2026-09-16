"""Run hypothetical repairs and turn-constrained before/after route replay."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_sources.files import FileDataSource
from src.replay_pipeline import export_replay


def code_version():
    try:
        head = subprocess.run(
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
        return head + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unavailable-see-code-sha256"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        type=Path,
        default=ROOT / "data/runtime/benchmark_seed42/inputs/manifest.json",
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        default=ROOT / "data/runtime/analysis_seed42/manifest.json",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/runtime/replay_seed42"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config/replay.json")
    args = parser.parse_args()
    manifest = export_replay(
        FileDataSource(args.inputs),
        args.analysis,
        args.output,
        json.loads(args.config.read_bytes()),
        code_version=code_version(),
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "run": manifest["run"],
                "summary": manifest["summary"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
