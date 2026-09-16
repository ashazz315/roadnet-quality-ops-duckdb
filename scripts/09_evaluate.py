"""Evaluate existing, immutable benchmark/analysis/replay snapshots."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation_pipeline import evaluate_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("benchmark", "analysis", "replay", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/evaluation.json")
    parser.add_argument(
        "--suite", type=Path, help="Optional verified repeat-suite report"
    )
    args = parser.parse_args()
    report = evaluate_snapshot(
        args.benchmark,
        args.analysis,
        args.replay,
        args.output,
        json.loads(args.config.read_bytes()),
        code_version="see-evaluator-code-sha256",
        suite_path=args.suite,
    )
    print(
        json.dumps(
            {"metrics": report["metrics"]["overall"], "run_id": report["run_id"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
