"""Actual uploaded analysis, durable state, archive boundaries and editor access."""
import base64
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from roadinsight_ui.bundles import MAX_BYTES, decode_bundle, read_bundle
from roadinsight_ui.workspace import Workspace, configuration, require_editor, unlock

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data/demo/upload-example.zip"


def altered(changes):
    stream = BytesIO()
    with ZipFile(EXAMPLE) as original, ZipFile(stream, "w", ZIP_DEFLATED) as output:
        for name in original.namelist():
            if name not in changes:
                output.writestr(name, original.read(name))
        for name, content in changes.items():
            if content is not None:
                output.writestr(name, content)
    return stream.getvalue()


def test_server_requires_explicit_storage_and_strong_editor_token(tmp_path):
    with pytest.raises(ValueError, match="持久化"):
        configuration({"ROADINSIGHT_MODE": "server"})
    settings = {"ROADINSIGHT_MODE": "server", "ROADINSIGHT_DATA_DIR": str(tmp_path), "ROADINSIGHT_EDITOR_TOKEN": "x"*48}
    assert configuration(settings) == ("server", tmp_path, "x"*48)
    assert unlock("x"*48, "x"*48)
    assert not unlock("x"*48, "y"*48)
    assert not unlock("", "") and not unlock("x"*48, None)
    with pytest.raises(PermissionError):
        require_editor("server", False)
    require_editor("server", True)
    require_editor("local", False)


@pytest.mark.parametrize("changes", [
    {"../outside.csv": b"bad"}, {"fault_answers.csv": b"hidden truth"},
    {"user_feedback.csv": None}, {"dataset.json": b"{}"},
    {"road_node.csv": b"node_id,node_id\na,a\n"},
])
def test_malformed_or_answer_containing_archives_are_rejected(changes):
    with pytest.raises(ValueError):
        read_bundle(altered(changes))


def test_bundle_limits_and_boolean_provenance():
    with pytest.raises(ValueError):
        decode_bundle("!")
    with pytest.raises(ValueError, match="15 MB"):
        decode_bundle("a"*(MAX_BYTES*2))
    with ZipFile(EXAMPLE) as archive:
        description = json.loads(archive.read("dataset.json"))
    description["synthetic"]["trajectory_point"] = False
    with pytest.raises(ValueError, match="Synthetic flag"):
        read_bundle(altered({"dataset.json": json.dumps(description)}))


def test_observable_bundle_runs_and_survives_restart_without_fake_accuracy(tmp_path):
    workspace = Workspace(tmp_path)
    with pytest.raises(RuntimeError, match="实例"):
        Workspace(tmp_path)
    try:
        raw = EXAMPLE.read_bytes()
        job = workspace.run(raw, "integration-test")
        assert job["status"] == "completed", (workspace.job_path(job["id"])/"worker.log").read_text(encoding="utf8")
        assert workspace.run(raw, "integration-test")["id"] == job["id"]
        snapshot = workspace.snapshot(job["id"])
        assert snapshot["dataset_label"] == "Shanghai-Xuhui-upload-example"
        assert snapshot["summary"]["input_trajectory_points"] == 60004
        assert snapshot["summary"]["issue_count"] == 18
        assert len(snapshot["routes"]) == 28
        assert snapshot["benchmark"]["status"] == "not_computed"
        workspace.review(snapshot, {"issue_id": snapshot["issues"][0]["issue_id"], "conclusion": "needs_review",
                                   "reviewer": "test-editor", "note": "Needs field verification", "event_id": "review-once"})
        backup = workspace.backup_to(tmp_path.parent / "complete-backup.zip")
        with ZipFile(backup) as archive:
            assert "workspace.sqlite" in archive.namelist()
            assert ".instance.lock" not in archive.namelist()
            assert any(name.endswith("snapshot.json.gz") for name in archive.namelist())
            restored_path = tmp_path.parent / "restored-workspace"
            archive.extractall(restored_path)  # Self-generated archive, not user input.
        restored_workspace = Workspace(restored_path)
        try:
            assert restored_workspace.snapshot(job["id"])["snapshot_id"] == snapshot["snapshot_id"]
            assert len(restored_workspace.reviews(snapshot)) == 1
        finally:
            restored_workspace.close()
    finally:
        workspace.close()
    restarted = Workspace(tmp_path)
    try:
        restored = restarted.snapshot(job["id"])
        assert restored["snapshot_id"] == snapshot["snapshot_id"]
        assert restarted.reviews(restored)[0]["note"] == "Needs field verification"
        assert len(restarted.jobs()) == 1
        with pytest.raises(ValueError):
            restarted.snapshot("../outside")
    finally:
        restarted.close()


def test_timeout_preserves_failed_job_and_input(tmp_path, monkeypatch):
    import subprocess
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1)
    monkeypatch.setattr("roadinsight_ui.workspace.subprocess.run", timed_out)
    workspace = Workspace(tmp_path)
    try:
        job = workspace.run(EXAMPLE.read_bytes(), "failure-test")
        assert job["status"] == "failed"
        assert (workspace.job_path(job["id"])/"upload.zip").exists()
        with pytest.raises(ValueError, match="尚未成功"):
            workspace.snapshot(job["id"])
    finally:
        workspace.close()


def test_restart_marks_unfinished_job_interrupted(tmp_path):
    import sqlite3
    workspace = Workspace(tmp_path)
    with sqlite3.connect(workspace.database) as db:
        db.execute("INSERT INTO jobs (id,request_id,label,status,created_at,input_sha256) VALUES (?,?,?,'running',?,?)",
                   ("a"*32, "unfinished", "interrupted test", "2026-09-22", "b"*64))
    workspace.close()
    restarted = Workspace(tmp_path)
    try:
        assert restarted.jobs()[0]["status"] == "interrupted"
        with pytest.raises(ValueError):
            restarted.snapshot("a"*32)
    finally:
        restarted.close()
