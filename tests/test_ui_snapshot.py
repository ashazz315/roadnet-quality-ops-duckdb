"""Presentation values remain bound to real, read-only STEP 6/7 results."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from roadinsight_ui.snapshot import (
    DEFAULT_SNAPSHOT,
    checked_artifact,
    load_snapshot,
    validate_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_snapshot_matches_published_analysis_and_replay():
    data = load_snapshot()
    analysis = json.loads((ROOT / "data/analysis/default_summary.json").read_bytes())
    replay = json.loads((ROOT / "data/replay/default_summary.json").read_bytes())
    assert data["summary"] == analysis["summary"]
    assert data["replay_summary"] == replay["summary"]
    assert (
        data["input_content_sha256"]
        == analysis["input_content_sha256"]
        == replay["input_content_sha256"]
    )
    assert len(data["issues"]) == 18 and len(data["routes"]) == 28
    assert data["benchmark"]["status"] == "not_computed"
    for issue in data["issues"]:
        assert issue["status"] == "needs_review" and not issue["calibrated"]
        refs = {
            (
                row["trajectory_id"],
                row["reference"]["start_seq"],
                row["reference"]["end_seq"],
            )
            for row in issue["trajectories"]
        }
        expected = set()
        for evidence in issue["evidence"]:
            assert evidence["source"]["network_version"] == data["network_version"]
            references = evidence["detail"]["references"]
            if isinstance(references, list):
                expected |= {
                    (ref["trajectory_id"], ref["start_seq"], ref["end_seq"])
                    for ref in references
                    if "trajectory_id" in ref
                }
        assert refs == expected
        assert (
            len({row["feedback_id"] for row in issue["feedback"]})
            == issue["evidence_summary"]["feedback_count"]
        )
    assert any(
        row["distance_delta_m"] and row["distance_delta_m"] > 0
        for row in data["routes"]
    )
    assert any(row["after_distance_m"] is None for row in data["routes"])


def test_bundle_bytes_are_verified_before_loading(tmp_path):
    path = tmp_path / DEFAULT_SNAPSHOT.name
    path.write_bytes(DEFAULT_SNAPSHOT.read_bytes() + b"tampered")
    path.with_suffix(".manifest.json").write_bytes(
        DEFAULT_SNAPSHOT.with_suffix(".manifest.json").read_bytes()
    )
    with pytest.raises(ValueError, match="checksum"):
        load_snapshot(path)


def test_artifacts_cannot_escape_or_bypass_checksums(tmp_path):
    directory = tmp_path / "inside"
    directory.mkdir()
    path = directory / "safe.json"
    path.write_bytes(b"{}")
    manifest = {"artifacts": {"safe.json": hashlib.sha256(b"{}").hexdigest()}}
    assert checked_artifact(directory, manifest, "safe.json") == b"{}"
    with pytest.raises(ValueError, match="leaves"):
        checked_artifact(directory, manifest, "../outside.json")
    path.write_bytes(b"[]")
    with pytest.raises(ValueError, match="checksum"):
        checked_artifact(directory, manifest, "safe.json")


@pytest.mark.parametrize("kind", ["duplicate", "stale", "real_fix", "accuracy"])
def test_inconsistent_or_unearned_claims_are_rejected(kind):
    data = copy.deepcopy(load_snapshot())
    if kind == "duplicate":
        data["issues"][1]["issue_id"] = data["issues"][0]["issue_id"]
    elif kind == "stale":
        data["issues"][0]["network_version"] = "stale"
    elif kind == "real_fix":
        data["routes"][0]["is_hypothetical"] = False
    else:
        data["benchmark"] = {"status": "passed", "precision": 0.99}
    with pytest.raises(ValueError):
        validate_snapshot(data)


def test_streamlit_entry_loads_without_errors():
    app = AppTest.from_file(str(ROOT / "roadinsight_app.py")).run(timeout=30)
    assert not app.exception
    assert not app.error


def test_streamlit_reports_invalid_snapshot_without_fake_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("ROADINSIGHT_UI_SNAPSHOT", str(tmp_path / "missing.json.gz"))
    app = AppTest.from_file(str(ROOT / "roadinsight_app.py")).run(timeout=30)
    assert not app.exception
    assert app.error and "演示数据无法载入" in app.error[0].value


def test_ui_does_not_import_fault_answers_or_modify_algorithms():
    from test_v2_boundaries import import_targets

    for path in [ROOT / "roadinsight_app.py", *(ROOT / "roadinsight_ui").glob("*.py")]:
        imports = import_targets(path.read_text(encoding="utf8"), "roadinsight_ui")
        assert not any(
            target.startswith(("src.benchmark", "src.simulation", "src.pipeline"))
            for target in imports
        )
