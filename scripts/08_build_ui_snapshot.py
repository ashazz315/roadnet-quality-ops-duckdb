"""Package verified observable analysis and replay outputs for the offline V2 UI."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roadinsight_ui.snapshot import build_snapshot


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
        "--replay", type=Path, default=ROOT / "data/runtime/replay_seed42/manifest.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/demo/roadinsight-v2.json.gz"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build_snapshot(args.inputs, args.analysis, args.replay, args.output),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
