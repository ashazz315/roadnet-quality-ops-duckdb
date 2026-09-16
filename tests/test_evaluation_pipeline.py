"""Evaluate newly generated results without mutating inputs or feeding back answers."""

import json

import pytest
from test_replay_pipeline import ROOT, hashes, replayed  # noqa: F401

from src.evaluation_pipeline import evaluate_snapshot


def test_evaluation_is_repeatable_and_all_sources_remain_unchanged(replayed):  # noqa: F811
    directory, _ = replayed
    protected = {
        name: hashes(directory / name)
        for name in ("golden", "benchmark", "analysis", "first")
    }
    reports = [
        evaluate_snapshot(
            directory / "benchmark/manifest.json",
            directory / "analysis/manifest.json",
            directory / "first/manifest.json",
            directory / f"evaluation-{n}.json",
            json.loads((ROOT / "config/evaluation.json").read_bytes()),
            code_version="test",
        )
        for n in (1, 2)
    ]
    assert reports[0]["metrics"] == reports[1]["metrics"]
    assert reports[0]["run_id"] != reports[1]["run_id"]
    assert reports[0]["reproducibility"]["status"] == "not_run"
    assert reports[0]["metrics"]["overall"]["injected"] == 24
    assert all(reports[0]["checks"].values())
    assert protected == {name: hashes(directory / name) for name in protected}
    with pytest.raises(FileExistsError):
        evaluate_snapshot(
            directory / "benchmark/manifest.json",
            directory / "analysis/manifest.json",
            directory / "first/manifest.json",
            directory / "evaluation-1.json",
            json.loads((ROOT / "config/evaluation.json").read_bytes()),
            code_version="test",
        )


def test_wrong_analysis_cannot_be_evaluated_against_unrelated_answers(replayed):  # noqa: F811
    directory, _ = replayed
    original = json.loads((directory / "analysis/manifest.json").read_bytes())
    original["input_content_sha256"]["trajectory_point"] = "another-batch"
    path = directory / "wrong-analysis.json"
    path.write_text(json.dumps(original), encoding="utf8")
    with pytest.raises(ValueError, match="does not match"):
        evaluate_snapshot(
            directory / "benchmark/manifest.json",
            path,
            directory / "first/manifest.json",
            directory / "bad-evaluation.json",
            json.loads((ROOT / "config/evaluation.json").read_bytes()),
            code_version="test",
        )
    assert not (directory / "bad-evaluation.json").exists()
