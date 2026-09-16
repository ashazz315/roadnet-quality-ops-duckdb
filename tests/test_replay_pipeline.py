"""Full seeded replay, isolated from answers, with independently checked routes."""

import builtins
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from shutil import copytree

import duckdb
import pandas as pd
import pytest

from src.analysis_pipeline import export_analysis
from src.benchmark_pipeline import build_benchmark
from src.data_sources.files import FileDataSource
from src.domain import InputEntity
from src.network.normalization import NetworkTables
from src.network.routing import TurnAwareRouter
from src.pipeline import build_golden_snapshot
from src.replay_pipeline import export_replay, load_analysis

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/replay.json").read_bytes())


def hashes(directory):
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.fixture(scope="module")
def replayed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("replay")
    build_golden_snapshot(
        ROOT / "data/reference/xuhui_osm", directory / "golden", code_version="test"
    )
    build_benchmark(
        directory / "golden/manifest.json",
        directory / "benchmark",
        json.loads((ROOT / "config/benchmark.json").read_bytes()),
        code_version="test",
    )
    copytree(directory / "benchmark/inputs", directory / "isolated/inputs")
    source = FileDataSource(directory / "isolated/inputs/manifest.json")
    export_analysis(
        source,
        directory / "analysis",
        json.loads((ROOT / "config/analysis.json").read_bytes()),
        json.loads((ROOT / "config/business_impact.json").read_bytes()),
        code_version="test",
    )
    protected = {
        name: hashes(directory / name)
        for name in ("golden", "benchmark", "isolated", "analysis")
    }
    manifests = []
    with pytest.MonkeyPatch.context() as patch:
        normal_import, normal_open = builtins.__import__, Path.open

        def guarded_import(name, *args, **kwargs):
            if name.startswith("src.benchmark") or name == "src.pipeline":
                raise AssertionError("Replay tried to import answer generation")
            return normal_import(name, *args, **kwargs)

        def guarded_open(path, *args, **kwargs):
            resolved = path.resolve()
            if (
                any(
                    resolved.is_relative_to(directory / name)
                    for name in ("golden", "benchmark")
                )
                or "truth" in resolved.parts
            ):
                raise AssertionError("Replay tried to read Golden or fault answers")
            return normal_open(path, *args, **kwargs)

        patch.setattr(builtins, "__import__", guarded_import)
        patch.setattr(Path, "open", guarded_open)
        for name in ("first", "second"):
            manifests.append(
                export_replay(
                    source,
                    directory / "analysis/manifest.json",
                    directory / name,
                    CONFIG,
                    code_version="test",
                )
            )
    assert protected == {name: hashes(directory / name) for name in protected}
    return directory, manifests


def test_replay_reproducible_and_source_issues_unchanged(replayed):
    directory, (first, second) = replayed
    assert first["run"]["run_id"] != second["run"]["run_id"]
    assert first["run"]["replay_batch_id"] == second["run"]["replay_batch_id"]
    assert first["result_content_sha256"] == second["result_content_sha256"]
    assert first["summary"] == second["summary"]
    pd.testing.assert_frame_equal(
        pd.read_parquet(directory / "analysis/road_issue.parquet"),
        pd.read_parquet(directory / "first/source_road_issue.parquet"),
    )
    assert (
        first["summary"]["ready_plans"] >= 1 and first["summary"]["changed_paths"] >= 1
    )
    assert first["summary"]["operations"]["none"] >= 1
    plans = json.loads((directory / "first/repair_plan.json").read_bytes())
    assert len(plans) == first["summary"]["issue_count"]
    assert {plan["issue_type"] for plan in plans} == {
        "CONNECTIVITY_BREAK",
        "ONEWAY_DIRECTION_CONFLICT",
        "TURN_RESTRICTION_CONFLICT",
        "MISSING_OR_CHANGED_ROAD_CANDIDATE",
    }
    for plan in plans:
        if plan["issue_type"] == "MISSING_OR_CHANGED_ROAD_CANDIDATE":
            assert (
                plan["status"] == "needs_manual_review"
                and plan["after_network_version"] is None
            )
    for relative, expected in first["artifacts"].items():
        assert (
            hashlib.sha256((directory / "first" / relative).read_bytes()).hexdigest()
            == expected
        )


def test_every_saved_path_matches_its_snapshot_and_arithmetic(replayed):
    directory, (manifest, _) = replayed
    source = FileDataSource(directory / "isolated/inputs/manifest.json")

    def router(source):
        tables = NetworkTables(
            **{
                entity.value: source.read(entity)
                .drop(columns="geometry", errors="ignore")
                .to_dict("records")
                for entity in (
                    InputEntity.ROAD_SEGMENT,
                    InputEntity.ROAD_NODE,
                    InputEntity.TURN_RESTRICTION,
                )
            },
            audit=[],
        )
        return TurnAwareRouter(tables, CONFIG)

    before = router(source)
    routes = pd.read_parquet(directory / "first/route_replay.parquet")
    assert len(routes) == manifest["summary"]["ready_plans"] * 2
    assert routes.replay_id.is_unique and routes.is_hypothetical.all()
    after_cache = {}
    for row in routes.to_dict("records"):
        if row["patch_id"] not in after_cache:
            after_cache[row["patch_id"]] = router(
                FileDataSource(
                    directory
                    / "first/cases"
                    / row["patch_id"]
                    / "network/manifest.json"
                )
            )
        after = after_cache[row["patch_id"]]
        for phase, engine in (("before", before), ("after", after)):
            route = json.loads(row[f"{phase}_route_json"])
            assert route == engine.route(
                row["origin_node_id"], row["destination_node_id"]
            )
            if route["status"] == "found":
                assert engine.validate_path(
                    row["origin_node_id"], row["destination_node_id"], route["edge_ids"]
                )
                assert route["distance_m"] == pytest.approx(
                    sum(edge["length_m"] for edge in route["edge_details"])
                )
                assert route["eta_s"] == pytest.approx(
                    sum(
                        edge["travel_time_s"] + edge["turn_delay_before_s"]
                        for edge in route["edge_details"]
                    )
                )
            else:
                assert route["distance_m"] is route["eta_s"] is None
        if row["reachability_change"] == "unchanged_available":
            assert row["distance_delta_m"] == pytest.approx(
                row["after_distance_m"] - row["before_distance_m"]
            )
            assert row["eta_delta_s"] == pytest.approx(
                row["after_eta_s"] - row["before_eta_s"]
            )
        else:
            assert pd.isna(row["distance_delta_m"]) and pd.isna(row["eta_delta_s"])
    # A legality correction can require a longer path; never regress to guaranteed gains.
    assert routes.distance_change.eq("longer").any()
    assert routes.reachability_change.eq("restored").any()
    assert routes.reachability_change.eq("lost").any()
    assert routes.before_edge_sequence_legal_after.eq(False).any()


def test_replay_database_and_geojson_preserve_links_and_nulls(replayed):
    directory, (manifest, _) = replayed
    with duckdb.connect(str(directory / "first/replay.duckdb"), read_only=True) as db:
        assert (
            db.execute("SELECT count(*) FROM route_replay").fetchone()[0]
            == manifest["summary"]["replay_count"]
        )
        assert (
            db.execute("SELECT count(*) FROM repair_plan").fetchone()[0]
            == manifest["summary"]["issue_count"]
        )
        assert (
            db.execute(
                "SELECT count(*) FROM route_replay r JOIN repair_plan p USING(patch_id) WHERE r.issue_id <> p.issue_id"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM route_replay WHERE reachability_change <> 'unchanged_available' AND (distance_delta_m IS NOT NULL OR eta_delta_s IS NOT NULL)"
            ).fetchone()[0]
            == 0
        )
    features = json.loads((directory / "first/routes.geojson").read_bytes())["features"]
    assert len(features) > 0
    svg = ET.fromstring((directory / "first/route_example.svg").read_bytes())
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert len(svg.findall(".//{http://www.w3.org/2000/svg}polyline")) > 2
    assert "not field verified" in " ".join(svg.itertext())
    assert all(feature["properties"]["is_hypothetical"] for feature in features)
    assert {feature["properties"]["phase"] for feature in features} == {
        "before",
        "after",
    }


def test_stale_inputs_tampered_issues_and_overwrite_are_rejected(replayed, tmp_path):
    directory, (manifest, _) = replayed
    with pytest.raises(FileExistsError):
        export_replay(None, None, directory / "first", CONFIG, code_version="test")
    with pytest.raises(ValueError, match="snapshot"):
        load_analysis(
            directory / "analysis/manifest.json",
            {**manifest["input_content_sha256"], "trajectory_point": "stale"},
            manifest["sources"],
        )
    copytree(directory / "analysis", tmp_path / "analysis")
    issue_path = tmp_path / "analysis/road_issue.parquet"
    issue_path.write_bytes(issue_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_analysis(
            tmp_path / "analysis/manifest.json",
            manifest["input_content_sha256"],
            manifest["sources"],
        )


def test_zero_issues_produce_valid_empty_replay_artifacts(replayed, tmp_path):
    from src.analysis_pipeline import audit_content_hash

    directory, _ = replayed
    copytree(directory / "analysis", tmp_path / "analysis")
    issue_path = tmp_path / "analysis/road_issue.parquet"
    empty = pd.read_parquet(issue_path).iloc[:0]
    empty.to_parquet(issue_path, index=False)
    manifest_path = tmp_path / "analysis/manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifacts"]["road_issue.parquet"] = hashlib.sha256(
        issue_path.read_bytes()
    ).hexdigest()
    manifest["result_content_sha256_excluding_run_fields"]["road_issue"] = (
        audit_content_hash(
            empty.drop(columns=["run_id", "first_detected_at", "last_detected_at"])
        )
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf8")
    result = export_replay(
        FileDataSource(directory / "isolated/inputs/manifest.json"),
        manifest_path,
        tmp_path / "empty",
        CONFIG,
        code_version="test",
    )
    assert result["summary"]["replay_count"] == result["summary"]["issue_count"] == 0
    assert pd.read_parquet(tmp_path / "empty/route_replay.parquet").empty


def test_replay_composition_does_not_import_answer_generators():
    from test_v2_boundaries import import_targets

    for relative in (
        "src/replay_pipeline.py",
        "src/replay_visualization.py",
        "scripts/07_replay.py",
    ):
        imports = import_targets((ROOT / relative).read_text(encoding="utf8"), "src")
        assert not any(
            target.startswith(("src.benchmark", "src.simulation", "src.pipeline"))
            for target in imports
        )


def test_published_replay_summary_matches_code_and_same_environment(replayed):
    _, (manifest, _) = replayed
    pinned = json.loads((ROOT / "data/replay/default_summary.json").read_bytes())
    assert pinned["code_sha256"] == manifest["run"]["code_sha256"]
    assert pinned["rules_version"] == manifest["run"]["rules_version"]
    if (
        pinned["tested_environment"] == manifest["run"]["environment"]
        and pinned["input_content_sha256"] == manifest["input_content_sha256"]
    ):
        assert pinned["summary"] == manifest["summary"]
        assert pinned["result_content_sha256"] == manifest["result_content_sha256"]
