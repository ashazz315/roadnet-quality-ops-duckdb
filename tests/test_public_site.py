"""Release boundary: reject mixed results and prevent private/stale exports."""
import hashlib
import json

import pytest

from roadinsight_ui.public_site import ASSETS, ROOT, build_public_site
from src.evaluation.report import seal


def test_public_export_contains_only_curated_assets_and_verified_results(tmp_path):
    output = tmp_path / "site"
    manifest = build_public_site(output, revision="test-revision")
    expected = set(ASSETS) | {"index.html", ".nojekyll", "release.json", "snapshot.json", "validation.json"}
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == expected
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    snapshot = json.loads((output / "snapshot.json").read_bytes())
    report = json.loads((output / "validation.json").read_bytes())
    assert snapshot["analysis_run"]["run_id"] == report["source"]["analysis_run_id"]
    html = (output / "index.html").read_text(encoding="utf-8")
    assert html.index('src="public.js"') < html.index('src="app.js"')
    assert manifest["revision"] == "test-revision"


def test_public_export_refuses_existing_files_without_deleting_them(tmp_path):
    private = tmp_path / "uploaded.csv"
    private.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="empty directory"):
        build_public_site(tmp_path)
    assert private.read_text() == "private"
    assert list(tmp_path.iterdir()) == [private]


def test_public_export_rejects_mixed_report_before_creating_site(tmp_path):
    report = json.loads((ROOT / "data/validation/default_report.json").read_bytes())
    report.pop("report_sha256")
    report["source"]["analysis_run_id"] = "other-run"
    source = tmp_path / "report.json"
    source.write_text(json.dumps(seal(report)), encoding="utf-8")
    output = tmp_path / "site"
    with pytest.raises(ValueError, match="different analysis"):
        build_public_site(output, report=source)
    assert not output.exists()
