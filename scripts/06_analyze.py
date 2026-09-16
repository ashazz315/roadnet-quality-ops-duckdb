"""Diagnose the current road network using observable inputs only."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis_pipeline import export_analysis
from src.data_sources.files import FileDataSource


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
        "--output", type=Path, default=ROOT / "data/runtime/analysis_seed42"
    )
    parser.add_argument("--rules", type=Path, default=ROOT / "config/analysis.json")
    parser.add_argument(
        "--business-rules", type=Path, default=ROOT / "config/business_impact.json"
    )
    args = parser.parse_args()
    result = export_analysis(
        FileDataSource(args.inputs),
        args.output,
        json.loads(args.rules.read_bytes()),
        json.loads(args.business_rules.read_bytes()),
        code_version=code_version(),
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "analysis_id": result["run"]["analysis_id"],
                "elapsed_seconds": result["run"]["elapsed_seconds"],
                "summary": result["summary"],
                "warnings": result["warnings"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
