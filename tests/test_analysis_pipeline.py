"""End-to-end observable-only analysis with byte-checked input immutability."""

import builtins
import hashlib
import json
from pathlib import Path
from shutil import copytree

import duckdb
import pandas as pd
import pytest

from src.analysis_pipeline import export_analysis
from src.benchmark_pipeline import build_benchmark
from src.data_sources.files import FileDataSource
from src.domain import IssueType
from src.pipeline import build_golden_snapshot

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/analysis.json").read_bytes())
BUSINESS = json.loads((ROOT / "config/business_impact.json").read_bytes())


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("analysis")
    build_golden_snapshot(
        ROOT / "data/reference/xuhui_osm", directory / "golden", code_version="test"
    )
    build_benchmark(
        directory / "golden/manifest.json",
        directory / "benchmark",
        json.loads((ROOT / "config/benchmark.json").read_bytes()),
        code_version="test",
    )
    original = {
        path.relative_to(directory / "benchmark").as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in (directory / "benchmark").rglob("*")
        if path.is_file()
    }
    copytree(directory / "benchmark/inputs", directory / "isolated/inputs")
    # This standalone copy has neither a root benchmark manifest nor any answers.
    first = export_analysis(
        FileDataSource(directory / "isolated/inputs/manifest.json"),
        directory / "first",
        RULES,
        BUSINESS,
        code_version="test",
    )
    with pytest.MonkeyPatch.context() as patch:
        normal_import = builtins.__import__
        normal_open = Path.open

        def guarded_import(name, *args, **kwargs):
            if name.startswith("src.benchmark") or name == "src.pipeline":
                raise AssertionError(
                    "Analysis tried to import Benchmark/Golden generation"
                )
            return normal_import(name, *args, **kwargs)

        def guarded_open(path, *args, **kwargs):
            resolved = path.resolve()
            if (
                resolved.is_relative_to(directory / "golden")
                or resolved.is_relative_to(directory / "benchmark")
                or "truth" in resolved.parts
                or "benchmark" in resolved.parts
                and resolved.suffix == ".py"
            ):
                raise AssertionError("Analysis tried to read answers or Golden")
            return normal_open(path, *args, **kwargs)

        patch.setattr(builtins, "__import__", guarded_import)
        patch.setattr(Path, "open", guarded_open)
        second = export_analysis(
            FileDataSource(directory / "isolated/inputs/manifest.json"),
            directory / "second",
            RULES,
            BUSINESS,
            code_version="test",
        )
    assert original == {
        path.relative_to(directory / "benchmark").as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in (directory / "benchmark").rglob("*")
        if path.is_file()
    }
    return directory, first, second


def test_isolated_inputs_reproduce_results_without_answers(analyzed):
    directory, first, second = analyzed
    assert not (directory / "isolated/truth").exists()
    assert first["run"]["run_id"] != second["run"]["run_id"]
    assert first["run"]["analysis_id"] == second["run"]["analysis_id"]
    assert first["input_content_sha256"] == second["input_content_sha256"]
    assert (
        first["result_content_sha256_excluding_run_fields"]
        == second["result_content_sha256_excluding_run_fields"]
    )
    assert first["summary"] == second["summary"]
    assert set(first["summary"]["issues_per_type"]) == {
        kind.value for kind in IssueType
    }
    assert first["summary"]["input_trajectory_points"] >= 60000
    assert (
        first["summary"]["accepted_trajectory_points"]
        + first["summary"]["rejected_trajectory_points"]
        == first["summary"]["input_trajectory_points"]
    )
    assert (
        sum(first["summary"]["matching_status_counts"].values())
        == first["summary"]["accepted_trajectory_points"]
    )
    assert (
        first["summary"]["accepted_feedback"] + first["summary"]["rejected_feedback"]
        == 36
    )


def test_every_issue_has_explained_score_sources_and_input_evidence(analyzed):
    directory, manifest, _ = analyzed
    issues = pd.read_parquet(directory / "first/road_issue.parquet")
    evidence = pd.read_parquet(directory / "first/issue_evidence.parquet")
    points = pd.read_parquet(directory / "isolated/inputs/trajectory_point.parquet")
    feedback = pd.read_parquet(directory / "isolated/inputs/user_feedback.parquet")
    keys = set(zip(points.trajectory_id, points.point_seq, strict=True))
    assert issues.issue_id.is_unique
    assert issues.status.eq("needs_review").all()
    assert issues.confidence.between(0, 1).all() and not issues.calibrated.any()
    assert issues.root_cause_hypothesis.str.len().gt(0).all()
    assert issues.suggested_action.str.len().gt(0).all()
    assert set(evidence.issue_id) == set(issues.issue_id)
    assert evidence.groupby("issue_id").size().eq(5).all()
    for row in issues.to_dict("records"):
        summary = json.loads(row["evidence_summary_json"])
        calculation = summary["score_calculation"]
        computed = min(
            calculation["cap"],
            sum(calculation["contributions"].values())
            * calculation["contradiction_multiplier"],
        )
        assert row["confidence"] == pytest.approx(computed, abs=1e-6)
        assert summary["trajectory_count"] >= RULES["minimum_trajectories"]
        assert summary["time_bin_count"] >= RULES["minimum_time_bins"]
    for row in evidence.to_dict("records"):
        source = json.loads(row["source_ref"])
        assert source["network_version"] == manifest["run"]["network_version"]
        assert "truth" not in source["source_ref"]
        refs = json.loads(row["metric_text"])["references"]
        if row["evidence_type"] == "trajectory":
            for ref in refs:
                assert (ref["trajectory_id"], ref["start_seq"]) in keys
                assert (ref["trajectory_id"], ref["end_seq"]) in keys
        elif row["evidence_type"] == "user_feedback":
            assert {ref["feedback_id"] for ref in refs} <= set(feedback.feedback_id)


def test_result_database_has_only_real_computed_records_and_potential_impacts(analyzed):
    directory, manifest, _ = analyzed
    with duckdb.connect(
        str(directory / "first/analysis.duckdb"), read_only=True
    ) as connection:
        assert (
            connection.execute("SELECT count(*) FROM road_issue").fetchone()[0]
            == manifest["summary"]["issue_count"]
        )
        assert (
            connection.execute("SELECT count(*) FROM issue_evidence").fetchone()[0]
            == manifest["summary"]["evidence_rows"]
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM business_impact WHERE measured_eta_delta_s IS NOT NULL OR measured_distance_delta_m IS NOT NULL OR no_path_verified"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM business_impact").fetchone()[0]
            == 5 * manifest["summary"]["issue_count"]
        )
        for table in (
            "road_issue",
            "issue_evidence",
            "business_impact",
            "matched_trajectory_point",
            "trajectory_passage",
            "accepted_user_feedback",
        ):
            assert (
                connection.execute(
                    f'SELECT count(*) FROM "{table}" WHERE run_id != ?',
                    [manifest["run"]["run_id"]],
                ).fetchone()[0]
                == 0
            )
        assert not {"benchmark_fault", "benchmark_result", "route_replay"} & {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }
    for filename, digest in manifest["artifacts"].items():
        assert (
            hashlib.sha256((directory / "first" / filename).read_bytes()).hexdigest()
            == digest
        )


def test_composition_imports_do_not_reach_benchmark_or_old_label_simulation():
    from test_v2_boundaries import import_targets

    for path in (
        ROOT / "src/analysis_pipeline.py",
        ROOT / "src/analysis_inputs.py",
        ROOT / "scripts/06_analyze.py",
    ):
        imports = import_targets(path.read_text(encoding="utf8"), "src")
        assert not any(
            target.startswith(("src.benchmark", "src.simulation", "src.pipeline"))
            for target in imports
        )


def test_published_run_summary_matches_code_and_same_environment(analyzed):
    _, manifest, _ = analyzed
    pinned = json.loads((ROOT / "data/analysis/default_summary.json").read_bytes())
    assert pinned["code_sha256"] == manifest["run"]["code_sha256"]
    assert pinned["rules_version"] == manifest["run"]["rules_version"]
    if (
        pinned["tested_environment"] == manifest["run"]["environment"]
        and pinned["input_content_sha256"] == manifest["input_content_sha256"]
    ):
        assert pinned["analysis_id"] == manifest["run"]["analysis_id"]
        assert pinned["summary"] == manifest["summary"]
        assert (
            pinned["result_content_sha256_excluding_run_fields"]
            == manifest["result_content_sha256_excluding_run_fields"]
        )
