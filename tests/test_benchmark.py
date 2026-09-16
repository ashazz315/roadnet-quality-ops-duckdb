"""Offline Benchmark invariants, independent geometry checks, and input isolation."""

import json
import random
from copy import deepcopy
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import transform

from src.benchmark.fault_injection import inject_faults, select_faults
from src.benchmark.isolation import NETWORK_COLUMNS, OBSERVATION_COLUMNS
from src.benchmark.movement import MovementModel
from src.benchmark_pipeline import build_benchmark, canonical, validate_config
from src.data_sources.base import DataSourceError
from src.data_sources.files import FileDataSource
from src.domain import InputEntity
from src.network.normalization import NetworkTables, normalize_osm
from src.network.validation import validate_network
from src.pipeline import build_golden_snapshot

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/benchmark.json").read_bytes())


@pytest.fixture(scope="module")
def benchmark(tmp_path_factory):
    directory = tmp_path_factory.mktemp("benchmark")
    build_golden_snapshot(
        ROOT / "data/reference/xuhui_osm", directory / "golden", code_version="test"
    )
    manifest_path = directory / "golden/manifest.json"
    golden_files = {
        path.name: path.read_bytes() for path in manifest_path.parent.iterdir()
    }
    source = FileDataSource(manifest_path)
    golden = NetworkTables(
        **{
            name: source.read(InputEntity(name))
            .drop(columns="geometry", errors="ignore")
            .to_dict("records")
            for name in NETWORK_COLUMNS
        },
        audit=[],
    )
    model = MovementModel(golden)
    first = build_benchmark(
        manifest_path, directory / "first", CONFIG, code_version="test"
    )
    second = build_benchmark(
        manifest_path, directory / "second", CONFIG, code_version="test"
    )
    assert {
        path.name: path.read_bytes() for path in manifest_path.parent.iterdir()
    } == golden_files
    return directory, golden, model, first, second


def test_default_volume_and_same_seed_complete_artifacts(benchmark):
    _, _, _, first, second = benchmark
    assert first["artifacts"] == second["artifacts"]
    assert first["run"]["network_version"] == second["run"]["network_version"]
    assert first["run"]["run_id"] != second["run"]["run_id"]
    assert first["summary"] == second["summary"]
    summary = first["summary"]
    assert summary["faults"] == 24
    assert set(summary["faults_per_type"].values()) == {6}
    assert 60000 <= summary["input_rows"]["trajectory_point"] <= 150000
    assert summary["input_rows"]["user_feedback"] == 36
    assert summary["targeted_paths"] == 192
    assert summary["supporting_feedback"] == 24
    assert summary["distractor_feedback"] == 12
    assert summary["faults_with_feedback"] == 18
    assert summary["is_synthetic"] and not summary["is_real_world_validation"]


def test_published_summary_matches_current_generator(benchmark):
    _, _, _, first, _ = benchmark
    pinned = json.loads((ROOT / "data/benchmark/default_summary.json").read_bytes())
    assert pinned["network_version"] == first["run"]["network_version"]
    assert pinned["code_sha256"] == first["run"]["code_sha256"]
    assert pinned["summary"] == first["summary"]
    assert {"system", "machine", "proj", "geos", "numpy"} <= first["run"][
        "environment"
    ].keys()
    if pinned["tested_environment"] == first["run"]["environment"]:
        assert pinned["artifacts"] == first["artifacts"]


def test_injection_changes_only_selected_objects_and_preserves_golden(benchmark):
    _, golden, model, _, _ = benchmark
    before = canonical(golden.__dict__)
    selection = select_faults(golden, model, CONFIG, random.Random(42))
    assert selection == select_faults(golden, model, CONFIG, random.Random(42))
    assert selection != select_faults(golden, model, CONFIG, random.Random(43))
    corrupted, faults = inject_faults(golden, selection, CONFIG, "test-corrupted")
    assert canonical(golden.__dict__) == before
    validate_network(corrupted)
    assert (
        len(corrupted.road_segment),
        len(corrupted.road_node),
        len(corrupted.turn_restriction),
    ) == (1071, 842, 14)
    segments = {row["segment_id"]: row for row in corrupted.road_segment}
    targets = {fault["target"] for fault in faults}
    for row in golden.road_segment:
        if row["segment_id"] not in targets:
            assert segments[row["segment_id"]] == {
                **row,
                "network_version": "test-corrupted",
            }
    metric = Transformer.from_crs(4326, 32651, always_xy=True).transform
    for fault in faults:
        prior, after = fault["before"], fault["after"]
        if fault["operation"] == "reverse_oneway":
            assert after["oneway"] and prior["direction"] != after["direction"]
            assert prior["geometry_wkt"] == after["geometry_wkt"]
        elif fault["operation"] == "split_endpoint_with_gap":
            assert after["to_node_id"] != prior["to_node_id"]
            assert after["length_m"] == pytest.approx(prior["length_m"] - 6, abs=1e-4)
            a = transform(metric, wkt.loads(prior["geometry_wkt"]))
            b = transform(metric, wkt.loads(after["geometry_wkt"]))
            assert 5.9 <= Point(a.coords[-1]).distance(Point(b.coords[-1])) <= 6.01
            assert fault["added_node"]["node_degree"] == 1
            assert fault["added_node"]["network_version"] == "test-corrupted"
        elif fault["operation"] == "remove_turn_restriction":
            assert after is None
            incoming = prior["from_edge_id"]
            assert any(
                model.allowed(
                    incoming, edge, ignore_restriction=prior["restriction_id"]
                )
                and not model.allowed(incoming, edge)
                for edge in model.outgoing[prior["via_node_id"]]
            )
        else:
            assert after is None and fault["target"] not in segments


def test_quota_failure_is_explicit(benchmark):
    _, golden, model, _, _ = benchmark
    with pytest.raises(ValueError, match="Not enough"):
        select_faults(
            golden, model, {**CONFIG, "faults_per_type": 99}, random.Random(42)
        )


def test_all_paths_obey_golden_direction_and_turns_and_have_target_exposure(benchmark):
    directory, _, model, _, _ = benchmark
    paths = pd.read_parquet(directory / "first/truth/trajectory_paths.parquet")
    points = pd.read_parquet(directory / "first/truth/point_truth.parquet")
    faults = json.loads((directory / "first/truth/faults.json").read_bytes())
    anchors = {fault["fault_id"]: fault["anchor"] for fault in faults}
    observed_edges = (
        points.groupby("trajectory_id")["golden_edge_id"].agg(set).to_dict()
    )
    assert paths["target_fault_id"].value_counts().to_dict() == dict.fromkeys(
        anchors, 8
    )
    for row in paths.to_dict("records"):
        path = json.loads(row["edge_ids_json"])
        model.validate_path(path)
        if row["target_fault_id"] is not None:
            assert (
                anchors[row["target_fault_id"]] in observed_edges[row["trajectory_id"]]
            )
        for incoming, outgoing in pairwise(path):
            assert model.edges[incoming]["v"] == model.edges[outgoing]["u"]
            for restriction in model.restrictions[incoming]:
                if restriction["restriction_type"].startswith("no_"):
                    assert outgoing != restriction["to_edge_id"]
                else:
                    assert outgoing == restriction["to_edge_id"]
        assert not any(
            model.graph.nodes[model.edges[key][end]]["is_boundary"]
            for key in path
            for end in ("u", "v")
        )


def test_points_follow_roads_noise_and_time_contract(benchmark):
    directory, _, model, _, _ = benchmark
    points = pd.read_parquet(directory / "first/inputs/trajectory_point.parquet")
    truth = pd.read_parquet(directory / "first/truth/point_truth.parquet")
    assert not points.duplicated(["trajectory_id", "point_seq"]).any()
    assert points["is_synthetic"].all()
    timestamps = pd.to_datetime(points["timestamp"], utc=True, format="ISO8601")
    assert timestamps.min() >= pd.Timestamp(CONFIG["start_time"])
    assert timestamps.max() <= pd.Timestamp(CONFIG["start_time"]) + timedelta(hours=48)
    assert (
        timestamps.groupby(points["trajectory_id"])
        .diff()
        .dropna()
        .dt.total_seconds()
        .eq(2)
        .all()
    )
    assert points.groupby("trajectory_id")["point_seq"].agg("min").eq(0).all()
    assert points.groupby("trajectory_id")["point_seq"].diff().dropna().eq(1).all()
    assert points["speed_kmh"].between(18, 36).all()
    assert truth.loc[~truth["is_outlier"], "noise_m"].le(8 + 1e-10).all()
    assert truth.loc[truth["is_outlier"], "noise_m"].between(20, 35).all()
    assert 0.002 < truth["is_outlier"].mean() < 0.01
    metric = Transformer.from_crs(4326, 32651, always_xy=True).transform
    # Independently check clean geometry, observed noise, and physical continuity.
    sample = truth.iloc[::53]
    for index, row in sample.iterrows():
        point = Point(metric(row["clean_longitude"], row["clean_latitude"]))
        line = transform(
            metric, wkt.loads(model.edges[row["golden_edge_id"]]["geometry_wkt"])
        )
        assert line.distance(point) < 0.02
        observation = points.iloc[index]
        assert point.distance(
            Point(metric(observation["longitude"], observation["latitude"]))
        ) == pytest.approx(row["noise_m"], abs=1e-5)
    x, y = metric(
        truth["clean_longitude"].to_numpy(), truth["clean_latitude"].to_numpy()
    )
    clean = pd.DataFrame({"x": x, "y": y})
    steps = clean.groupby(points["trajectory_id"]).diff()
    assert ((steps.x**2 + steps.y**2) ** 0.5).dropna().le(20.1).all()


def test_five_entity_input_manifest_contains_no_answer_fields_or_original_ids(
    benchmark,
):
    directory, _, _, first, _ = benchmark
    source = FileDataSource(directory / "first/inputs/manifest.json")
    forbidden = {
        "fault_id",
        "expected_issue_type",
        "golden_edge_id",
        "clean_longitude",
        "clean_latitude",
        "is_outlier",
        "is_supporting",
        "target_fault_id",
        "osm_way_id",
        "osm_node_id",
        "osm_relation_id",
        "tags_json",
        "matched_segment_id",
    }
    for entity in InputEntity:
        frame = source.read(entity)
        assert not forbidden & set(frame)
        assert source.metadata(entity).is_synthetic
        assert (
            source.metadata(entity).network_version == first["run"]["network_version"]
        )
        assert frame["is_synthetic"].all()
        for column in frame.columns:
            if column.endswith("_id"):
                assert (
                    not frame[column]
                    .astype(str)
                    .str.contains("osm:|split:", regex=True)
                    .any()
                )
        if entity.value in NETWORK_COLUMNS:
            assert set(frame) == set(NETWORK_COLUMNS[entity.value]) | {
                "is_synthetic"
            } | ({"geometry"} if entity.value != "turn_restriction" else set())
        else:
            assert set(frame) == set(OBSERVATION_COLUMNS[entity.value]) | {
                "network_version"
            }
    with pytest.raises(DataSourceError):
        source.read("benchmark_fault")
    with pytest.raises(DataSourceError):
        FileDataSource(directory / "first/manifest.json")


def test_feedback_has_partial_coverage_repeats_and_indistinguishable_distractors(
    benchmark,
):
    directory, _, _, _, _ = benchmark
    feedback = pd.read_parquet(directory / "first/inputs/user_feedback.parquet")
    truth = pd.read_parquet(directory / "first/truth/feedback_truth.parquet")
    merged = feedback.merge(truth, on="feedback_id", validate="one_to_one")
    assert merged["is_supporting"].sum() == 24
    assert merged["fault_id"].nunique() == 18
    assert len(set(merged.loc[~merged["is_supporting"], "golden_segment_id"])) == 12
    assert merged["source"].eq("synthetic_benchmark").all()
    assert merged["is_synthetic"].all()
    # Complaint text does not label a report as supportive/distractor.
    assert merged.groupby("feedback_type")["description"].nunique().eq(1).all()
    assert set(merged.loc[~merged["is_supporting"], "feedback_type"]) <= set(
        merged.loc[merged["is_supporting"], "feedback_type"]
    )


def test_snapshot_refuses_overwrite_and_changed_files(benchmark):
    directory, _, _, _, _ = benchmark
    with pytest.raises(FileExistsError):
        build_benchmark(
            directory / "golden/manifest.json",
            directory / "first",
            CONFIG,
            code_version="test",
        )
    path = directory / "second/inputs/user_feedback.parquet"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"tamper")
        with pytest.raises(DataSourceError, match="checksum"):
            FileDataSource(directory / "second/inputs/manifest.json")
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("seed", True),
        ("faults_per_type", 0),
        ("target_point_count", -1),
        ("gps_sigma_m", float("nan")),
        ("duration_hours", float("inf")),
        ("feedback_fault_coverage", 2),
        ("gps_outlier_fraction", -0.1),
        ("supporting_feedback_count", 10),
        ("feedback_count", 24),
        ("speed_kmh_range", [36, 18]),
        ("path_edges_range", [2, 4]),
        ("gps_outlier_range_m", [4, 8]),
        ("minimum_target_length_m", 8),
        ("start_time", "2026-06-01"),
        ("metric_crs", "EPSG:4326"),
    ],
)
def test_invalid_config_is_rejected(key, value):
    with pytest.raises(ValueError):
        validate_config({**CONFIG, key: value})


@pytest.mark.parametrize("restriction", ["no_left_turn", "only_left_turn"])
def test_movement_enforces_turn_identity_and_direction(restriction):
    coordinates = {
        1: (121.43, 31.18),
        2: (121.431, 31.18),
        3: (121.431, 31.181),
        4: (121.432, 31.18),
    }
    elements = [
        {"type": "node", "id": key, "lon": xy[0], "lat": xy[1]}
        for key, xy in coordinates.items()
    ]
    for identifier, refs in ((10, [1, 2]), (20, [2, 3]), (30, [2, 4]), (40, [2, 3])):
        elements.append(
            {
                "type": "way",
                "id": identifier,
                "nodes": refs,
                "tags": {
                    "highway": "residential",
                    "oneway": "yes" if identifier == 30 else "no",
                },
            }
        )
    elements.append(
        {
            "type": "relation",
            "id": 100,
            "members": [
                {"type": "way", "ref": 10, "role": "from"},
                {"type": "node", "ref": 2, "role": "via"},
                {"type": "way", "ref": 20, "role": "to"},
            ],
            "tags": {"type": "restriction", "restriction": restriction},
        }
    )
    tables = normalize_osm(
        {"elements": elements},
        {
            "bbox": [121.42, 31.17, 121.44, 31.19],
            "metric_crs": "EPSG:32651",
            "highway_classes": ["residential"],
        },
        "test",
    )
    model = MovementModel(tables)
    edges = {(row["osm_way_id"], key[-1]): key for key, row in model.edges.items()}
    incoming = edges["10", "f"]
    assert model.allowed(incoming, edges["20", "f"]) == (
        restriction == "only_left_turn"
    )
    assert model.allowed(incoming, edges["30", "f"]) == (
        restriction != "only_left_turn"
    )
    assert model.allowed(incoming, edges["40", "f"]) == (
        restriction != "only_left_turn"
    )
    assert not model.allowed(incoming, edges["10", "r"])
    assert ("30", "r") not in edges
    assert model.allowed(
        incoming,
        edges["20", "f"],
        ignore_restriction=tables.turn_restriction[0]["restriction_id"],
    )
    with pytest.raises(ValueError, match="anchor"):
        model.walk(edges["30", "f"], random.Random(42), (8, 18))
    with pytest.raises(ValueError, match="unavailable"):
        model.validate_path(["nonexistent"])
    restricted = deepcopy(tables)
    restricted.road_node[0]["is_boundary"] = True
    boundary = restricted.road_node[0]["node_id"]
    assert not any(
        boundary in (edge["u"], edge["v"])
        for edge in MovementModel(restricted).edges.values()
    )
