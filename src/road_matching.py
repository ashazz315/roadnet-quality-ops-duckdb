"""Batch nearest-road matching with all distance work performed in a metric CRS."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import yaml
from pyproj import CRS
from shapely.geometry import Point

WGS84 = CRS.from_epsg(4326)
DEFAULT_ROADS_PATH = Path("data/reference/roads.geojson")
DEFAULT_RULES_PATH = Path("config/rules.yaml")


class RoadMatchingError(ValueError):
    """Raised when matching inputs, rules, or CRS information are unusable."""


@dataclass(frozen=True)
class RoadMatchingRules:
    matched_max_distance_m: float
    review_max_distance_m: float


def load_matching_rules(path: Path = DEFAULT_RULES_PATH) -> RoadMatchingRules:
    rules_path = Path(path)
    if not rules_path.is_file():
        raise RoadMatchingError(f"匹配规则不存在：{rules_path}")
    try:
        payload = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
        config = payload["road_matching"]
        matched = float(config["matched_max_distance_m"])
        review = float(config["review_max_distance_m"])
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RoadMatchingError("config/rules.yaml 中的 road_matching 配置无效。") from exc
    if matched < 0 or review < matched:
        raise RoadMatchingError("匹配阈值必须满足 0 <= matched <= review。")
    return RoadMatchingRules(matched, review)


def load_reference_roads(path: Path = DEFAULT_ROADS_PATH) -> gpd.GeoDataFrame:
    roads_path = Path(path)
    if not roads_path.is_file():
        raise RoadMatchingError(f"参考道路不存在：{roads_path}")
    try:
        roads = gpd.read_file(roads_path)
    except Exception as exc:  # pyogrio/fiona expose different parse exceptions
        raise RoadMatchingError("参考道路无法读取。") from exc
    if roads.crs is None:
        # RFC 7946 GeoJSON coordinates are WGS84 when no legacy crs member exists.
        roads = roads.set_crs(WGS84)
    if roads.empty:
        raise RoadMatchingError("参考道路为空。")
    roads = roads.loc[roads.geometry.notna() & ~roads.geometry.is_empty].copy()
    roads = roads.loc[roads.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
    if roads.empty:
        raise RoadMatchingError("参考道路没有可匹配的线几何。")
    return roads.to_crs(WGS84)


def select_metric_crs(roads_wgs84: gpd.GeoDataFrame) -> CRS:
    if roads_wgs84.crs is None:
        raise RoadMatchingError("参考道路缺少 CRS。")
    roads = roads_wgs84.to_crs(WGS84)
    estimated = roads.estimate_utm_crs()
    if estimated is None:
        raise RoadMatchingError("无法为参考道路选择米制投影。")
    metric_crs = CRS.from_user_input(estimated)
    if not metric_crs.is_projected:
        raise RoadMatchingError("自动选择的 CRS 不是投影坐标系。")
    return metric_crs


def _as_numeric(value: object) -> float | None:
    if pd.isna(value) or isinstance(value, bool) or not str(value).strip():
        return None
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(numeric) else float(numeric)


def _valid_lon_lat(longitude: float, latitude: float) -> bool:
    return -180 <= longitude <= 180 and -90 <= latitude <= 90


def records_to_geodataframe(valid_records: pd.DataFrame) -> gpd.GeoDataFrame:
    """Convert longitude/latitude columns to WGS84 points with explicit error states."""
    if not isinstance(valid_records, pd.DataFrame):
        raise TypeError("valid_records 必须是 pandas.DataFrame。")
    missing = [field for field in ("longitude", "latitude") if field not in valid_records.columns]
    if missing:
        raise RoadMatchingError(f"缺少坐标字段：{', '.join(missing)}")

    source = valid_records.copy(deep=True)
    supplied_geometry: list[Any] | None = None
    if isinstance(valid_records, gpd.GeoDataFrame):
        supplied = valid_records.copy()
        if supplied.crs is None:
            supplied = supplied.set_crs(WGS84)
        supplied = supplied.to_crs(WGS84)
        supplied_geometry = list(supplied.geometry)

    geometries: list[Point | None] = []
    coordinate_states: list[str] = []
    swapped_flags: list[bool] = []
    normalized_lon: list[float | None] = []
    normalized_lat: list[float | None] = []

    for position in range(len(source)):
        longitude = _as_numeric(source.iloc[position]["longitude"])
        latitude = _as_numeric(source.iloc[position]["latitude"])
        if longitude is None or latitude is None:
            geometries.append(None)
            coordinate_states.append("invalid_coordinate")
            swapped_flags.append(False)
            normalized_lon.append(longitude)
            normalized_lat.append(latitude)
            continue

        was_swapped = False
        if not _valid_lon_lat(longitude, latitude) and _valid_lon_lat(latitude, longitude):
            longitude, latitude = latitude, longitude
            was_swapped = True
        if not _valid_lon_lat(longitude, latitude):
            geometries.append(None)
            coordinate_states.append("invalid_coordinate")
            swapped_flags.append(False)
            normalized_lon.append(longitude)
            normalized_lat.append(latitude)
            continue

        if supplied_geometry is not None:
            supplied_point = supplied_geometry[position]
            if supplied_point is None or supplied_point.is_empty:
                geometries.append(None)
                coordinate_states.append("empty_geometry")
                swapped_flags.append(was_swapped)
                normalized_lon.append(longitude)
                normalized_lat.append(latitude)
                continue

        geometries.append(Point(longitude, latitude))
        coordinate_states.append("pending")
        swapped_flags.append(was_swapped)
        normalized_lon.append(longitude)
        normalized_lat.append(latitude)

    source["longitude"] = normalized_lon
    source["latitude"] = normalized_lat
    source["coordinate_was_swapped"] = swapped_flags
    source["_coordinate_state"] = coordinate_states
    return gpd.GeoDataFrame(source, geometry=geometries, crs=WGS84)


def _road_columns(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    id_field = next((field for field in ("segment_id", "road_id", "osm_id") if field in roads.columns), None)
    if id_field is None:
        road_ids = roads.index.map(str)
    else:
        road_ids = roads[id_field].astype("string")
    names = roads["road_name"].astype("string") if "road_name" in roads.columns else pd.Series(pd.NA, index=roads.index, dtype="string")
    return gpd.GeoDataFrame(
        {
            "matched_road_id": road_ids,
            "matched_road_name": names,
        },
        geometry=roads.geometry,
        crs=roads.crs,
    )


def _status_for_distance(distance_m: float, rules: RoadMatchingRules) -> str:
    if distance_m <= rules.matched_max_distance_m:
        return "matched"
    if distance_m <= rules.review_max_distance_m:
        return "needs_review"
    return "unmatched"


def match_records_to_roads(
    valid_records: pd.DataFrame,
    roads_path: Path = DEFAULT_ROADS_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
) -> gpd.GeoDataFrame:
    """Return WGS84 records enriched by batch nearest-road matching results."""
    rules = load_matching_rules(rules_path)
    roads_wgs84 = load_reference_roads(roads_path)
    points_wgs84 = records_to_geodataframe(valid_records)
    metric_crs = select_metric_crs(roads_wgs84)

    result = points_wgs84.reset_index(drop=True).copy()
    result["matched_road_id"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["matched_road_name"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["match_distance_m"] = float("nan")
    result["match_status"] = result["_coordinate_state"]
    result["_match_row_id"] = range(len(result))

    eligible = result["_coordinate_state"].eq("pending") & result.geometry.notna() & ~result.geometry.is_empty
    if eligible.any():
        points_metric = result.loc[eligible, ["_match_row_id", "geometry"]].to_crs(metric_crs)
        roads_metric = _road_columns(roads_wgs84.to_crs(metric_crs))
        joined = gpd.sjoin_nearest(
            points_metric,
            roads_metric,
            how="left",
            distance_col="match_distance_m",
        )
        joined = joined.sort_values(["_match_row_id", "match_distance_m"], na_position="last")
        joined = joined.drop_duplicates("_match_row_id", keep="first")

        for _, matched in joined.iterrows():
            row_id = int(matched["_match_row_id"])
            output_index = row_id
            distance = matched.get("match_distance_m")
            if pd.isna(distance) or pd.isna(matched.get("matched_road_id")):
                result.at[output_index, "match_status"] = "unmatched"
                continue
            distance_m = float(distance)
            result.at[output_index, "matched_road_id"] = str(matched["matched_road_id"])
            road_name = matched.get("matched_road_name")
            if not pd.isna(road_name):
                result.at[output_index, "matched_road_name"] = str(road_name)
            result.at[output_index, "match_distance_m"] = round(distance_m, 3)
            result.at[output_index, "match_status"] = _status_for_distance(distance_m, rules)

    result = result.to_crs(WGS84)
    result["longitude"] = result.geometry.x.where(result.geometry.notna(), result["longitude"])
    result["latitude"] = result.geometry.y.where(result.geometry.notna(), result["latitude"])
    return result.drop(columns=["_coordinate_state", "_match_row_id"])
