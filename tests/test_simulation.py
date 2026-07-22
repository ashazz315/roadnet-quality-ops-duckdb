from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.simulation import (
    ISSUE_TYPES,
    ReferenceRoadError,
    generate_simulated_records,
    interpolate_on_line,
    load_reference_roads,
    write_csv,
    write_geojson,
)


def reference_features() -> list[dict[str, object]]:
    return [
        {
            "type": "Feature",
            "properties": {
                "segment_id": "road_a",
                "road_name": "道路 A",
                "road_class": "secondary",
                "direction": "two_way",
                "speed_limit": 50,
                "region": "test_region",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.40, 39.90], [116.41, 39.90], [116.42, 39.91]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "segment_id": "road_b",
                "road_name": "道路 B",
                "road_class": "residential",
                "direction": "forward",
                "speed_limit": 30,
                "region": "test_region",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.43, 39.91], [116.44, 39.92]],
            },
        },
    ]


def write_reference(path: Path, features: list[dict[str, object]] | None = None) -> None:
    payload = {"type": "FeatureCollection", "features": features or reference_features()}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lat = (lat1 + lat2) / 2
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat))
    dy = (lat2 - lat1) * 111_320
    return math.hypot(dx, dy)


def test_load_reference_keeps_only_linestrings(tmp_path: Path) -> None:
    features = reference_features()
    features.append(
        {"type": "Feature", "properties": {"segment_id": "point"}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}
    )
    path = tmp_path / "roads.geojson"
    write_reference(path, features)
    roads = load_reference_roads(path)
    assert [road["properties"]["segment_id"] for road in roads] == ["road_a", "road_b"]


def test_reference_must_exist_and_use_wgs84_coordinates(tmp_path: Path) -> None:
    with pytest.raises(ReferenceRoadError, match="不存在"):
        load_reference_roads(tmp_path / "missing.geojson")
    bad = reference_features()
    bad[0]["geometry"]["coordinates"][0] = [500000, 4400000]
    path = tmp_path / "bad.geojson"
    write_reference(path, bad)
    with pytest.raises(ReferenceRoadError, match="WGS84"):
        load_reference_roads(path)


def test_interpolation_follows_all_polyline_segments() -> None:
    coordinates = [[116.4, 39.9], [116.41, 39.9], [116.41, 39.92]]
    lon, lat, segment = interpolate_on_line(coordinates, 0.9)
    assert segment == 1
    assert lon == pytest.approx(116.41)
    assert 39.9 < lat < 39.92


def test_generated_geometry_is_point_and_tied_to_reference_roads() -> None:
    records = generate_simulated_records(reference_features(), count=60, seed=7, issue_rate=0.8, offset_share=0.25)
    road_ids = {"road_a", "road_b"}
    assert len(records) == 60
    assert all(record["geometry"]["type"] == "Point" for record in records)
    assert {record["properties"]["original_road_id"] for record in records} <= road_ids
    assert {record["properties"]["expected_issue_type"] for record in records if record["properties"]["expected_is_issue"]} == set(ISSUE_TYPES)
    for record in records:
        properties = record["properties"]
        assert {"expected_is_issue", "expected_issue_type", "original_road_id", "injected_error"} <= properties.keys()
        assert record["geometry"]["coordinates"] == [properties["longitude"], properties["latitude"]]


def test_clean_points_stay_exactly_on_reference_geometry() -> None:
    records = generate_simulated_records(reference_features(), count=40, seed=11, issue_rate=0.5, offset_share=0.2)
    clean = [record for record in records if not record["properties"]["expected_is_issue"]]
    assert clean
    for record in clean:
        properties = record["properties"]
        assert properties["longitude"] == properties["base_longitude"]
        assert properties["latitude"] == properties["base_latitude"]
        assert properties["injected_error"] == "none"


def test_offsets_have_controlled_metric_distance() -> None:
    records = generate_simulated_records(
        reference_features(),
        count=50,
        seed=13,
        issue_rate=1,
        offset_share=0.4,
        offset_min_m=20,
        offset_max_m=25,
    )
    offsets = [record for record in records if record["properties"]["expected_issue_type"] == "point_offset"]
    assert len(offsets) == 20
    for record in offsets:
        properties = record["properties"]
        measured = distance_m(
            properties["base_longitude"],
            properties["base_latitude"],
            properties["longitude"],
            properties["latitude"],
        )
        assert 19.9 <= measured <= 25.1
        assert measured == pytest.approx(abs(properties["offset_m"]), abs=0.1)


def test_each_injected_issue_changes_expected_field() -> None:
    records = generate_simulated_records(reference_features(), count=30, seed=17, issue_rate=1, offset_share=1 / 6)
    by_type = {record["properties"]["expected_issue_type"]: record["properties"] for record in records}
    assert by_type["missing_road_name"]["road_name"] == ""
    assert by_type["road_class_anomaly"]["road_class"] == "invalid_class"
    assert by_type["direction_anomaly"]["direction"] == "invalid_direction"
    assert by_type["speed_limit_anomaly"]["speed_limit"] == 999
    assert by_type["suspected_dead_end"]["injected_error"] == "point_moved_to_road_endpoint"
    assert by_type["point_offset"]["offset_m"] != 0


def test_generation_is_reproducible() -> None:
    first = generate_simulated_records(reference_features(), count=20, seed=99)
    second = generate_simulated_records(reference_features(), count=20, seed=99)
    assert first == second


def test_outputs_preserve_labels_in_geojson_and_csv(tmp_path: Path) -> None:
    records = generate_simulated_records(reference_features(), count=10, seed=21)
    geojson_path = tmp_path / "records.geojson"
    csv_path = tmp_path / "records.csv"
    write_geojson(geojson_path, records)
    write_csv(csv_path, records)
    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(csv_path, encoding="utf-8-sig")
    assert payload["features"][0]["geometry"]["type"] == "Point"
    assert {"expected_is_issue", "expected_issue_type", "original_road_id", "injected_error"} <= set(frame.columns)
    assert len(payload["features"]) == len(frame) == 10


def test_cli_uses_reference_file_and_writes_both_formats(tmp_path: Path) -> None:
    reference_path = tmp_path / "roads.geojson"
    output_dir = tmp_path / "generated"
    write_reference(reference_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/02_generate_mock_data.py",
            "--roads",
            str(reference_path),
            "--output-dir",
            str(output_dir),
            "--count",
            "12",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "Generated records: 12" in completed.stdout
    assert (output_dir / "simulated_records.geojson").is_file()
    assert (output_dir / "simulated_records.csv").is_file()
