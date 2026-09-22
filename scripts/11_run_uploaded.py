"""Run the existing diagnosis/replay pipeline on one verified observable upload."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.analysis_pipeline import export_analysis
from src.data_sources.files import FileDataSource
from src.replay_pipeline import export_replay
from roadinsight_ui.snapshot import build_snapshot


def run(job):
    job = Path(job).resolve()
    inputs = job / "inputs/manifest.json"
    description = json.loads((job / "dataset.json").read_bytes())
    source = FileDataSource(inputs)
    version = "uploaded-workspace-see-implementation-sha256"
    export_analysis(source, job / "analysis", json.loads((ROOT / "config/analysis.json").read_bytes()),
                    json.loads((ROOT / "config/business_impact.json").read_bytes()), code_version=version)
    export_replay(source, job / "analysis/manifest.json", job / "replay",
                  json.loads((ROOT / "config/replay.json").read_bytes()), code_version=version)
    build_snapshot(inputs, job / "analysis/manifest.json", job / "replay/manifest.json",
                   job / "snapshot.json.gz", dataset_label=description["label"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    run(parser.parse_args().job)
