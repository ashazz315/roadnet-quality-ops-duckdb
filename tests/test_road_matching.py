from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from src.road_matching import (
    RoadMatchingError,
    load_matching_rules,
    load_reference_roads,
    match_records_to_roads,
    records_to_geodataframe,
    select_metric_crs,
)


def write_roads(path: Path, *, include_empty: bool = False, only_empty: bool = False) -> None:
    features: list[dict[str, object]] = []
    if not only_empty:
        features.append(
            {
                "type": "Feature",
                "properties": {"segment_id": "road_1", "road_name": "测试道路"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.40, 39.90], [116.41, 39.90]],
                },
            }
        )
    if include_empty or only_empty:
        features.append(
            {
                "type": "Feature",
                "properties": {"segment_id": "empty", "road_name": "空道路"},
                "geometry": None,
            }
        )
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_rules(path: Path, matched: float = 30, review: float = 100) -> None:
    path.write_text(
        f"road_matching:\n  matched_max_distance_m: {matched}\n  review_max_distance_m: {review}\n",
        encoding="utf-8",
    )


@pytest.fixture
def matching_files(tmp_path: Path) -> tuple[Path, Path]:
    roads_path = tmp_path / "roads.geojson"
    rules_path = tmp_path / "rules.yaml"
    write_roads(roads_path)
    write_rules(rules_path)
    return roads_path, rules_path


def test_exact_point_matches_and_returns_wgs84(matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    records = pd.DataFrame([{"record_id": "A1", "longitude": 116.405, "latitude": 39.90}])
    result = match_records_to_roads(records, roads_path, rules_path)
    assert result.crs.to_epsg() == 4326
    assert result.loc[0, "matched_road_id"] == "road_1"
    assert result.loc[0, "matched_road_name"] == "测试道路"
    assert result.loc[0, "match_distance_m"] == pytest.approx(0, abs=0.02)
    assert result.loc[0, "match_status"] == "matched"


def test_distance_is_calculated_in_meters_not_degrees(matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    records = pd.DataFrame([{"longitude": 116.405, "latitude": 39.9005}])
    result = match_records_to_roads(records, roads_path, rules_path)
    assert 54 <= result.loc[0, "match_distance_m"] <= 57
    assert result.loc[0, "match_status"] == "needs_review"


def test_unmatched_point_keeps_nearest_road_and_distance(matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    records = pd.DataFrame([{"longitude": 116.405, "latitude": 39.902}])
    result = match_records_to_roads(records, roads_path, rules_path)
    assert result.loc[0, "match_status"] == "unmatched"
    assert result.loc[0, "matched_road_id"] == "road_1"
    assert result.loc[0, "match_distance_m"] > 100


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    [(None, 39.9), (116.4, None), ("", 39.9), (116.4, "north"), (500, 200)],
)
def test_missing_or_invalid_coordinates_are_not_matched(longitude: object, latitude: object, matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    result = match_records_to_roads(pd.DataFrame([{"longitude": longitude, "latitude": latitude}]), roads_path, rules_path)
    assert result.loc[0, "match_status"] == "invalid_coordinate"
    assert pd.isna(result.loc[0, "matched_road_id"])
    assert pd.isna(result.loc[0, "match_distance_m"])
    assert result.geometry.iloc[0] is None


def test_obvious_coordinate_swap_is_corrected_and_flagged(matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    records = pd.DataFrame([{"longitude": 39.90, "latitude": 116.405}])
    result = match_records_to_roads(records, roads_path, rules_path)
    assert bool(result.loc[0, "coordinate_was_swapped"])
    assert result.loc[0, "longitude"] == pytest.approx(116.405)
    assert result.loc[0, "latitude"] == pytest.approx(39.90)
    assert result.loc[0, "match_status"] == "matched"


def test_explicit_empty_point_geometry_has_own_status(matching_files: tuple[Path, Path]) -> None:
    roads_path, rules_path = matching_files
    records = gpd.GeoDataFrame(
        {"longitude": [116.405], "latitude": [39.90]},
        geometry=[None],
        crs="EPSG:4326",
    )
    result = match_records_to_roads(records, roads_path, rules_path)
    assert result.loc[0, "match_status"] == "empty_geometry"
    assert pd.isna(result.loc[0, "matched_road_id"])


def test_empty_road_geometry_is_ignored_when_valid_roads_exist(tmp_path: Path) -> None:
    roads_path = tmp_path / "roads.geojson"
    write_roads(roads_path, include_empty=True)
    roads = load_reference_roads(roads_path)
    assert list(roads["segment_id"]) == ["road_1"]


def test_all_empty_road_geometries_are_rejected(tmp_path: Path) -> None:
    roads_path = tmp_path / "roads.geojson"
    write_roads(roads_path, only_empty=True)
    with pytest.raises(RoadMatchingError, match="没有可匹配"):
        load_reference_roads(roads_path)


def test_metric_crs_is_projected_for_reference_area(matching_files: tuple[Path, Path]) -> None:
    roads_path, _ = matching_files
    roads = load_reference_roads(roads_path)
    assert roads.crs.to_epsg() == 4326
    metric_crs = select_metric_crs(roads)
    assert metric_crs.is_projected
    assert metric_crs.to_epsg() == 32650


def test_rules_validate_threshold_order(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    write_rules(rules_path, matched=100, review=30)
    with pytest.raises(RoadMatchingError, match="matched <= review"):
        load_matching_rules(rules_path)


def test_records_to_geodataframe_requires_coordinate_columns() -> None:
    with pytest.raises(RoadMatchingError, match="缺少坐标字段"):
        records_to_geodataframe(pd.DataFrame([{"record_id": "A1"}]))
