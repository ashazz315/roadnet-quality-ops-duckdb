"""Generate deterministic issue records from real WGS84 road geometries."""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ISSUE_TYPES = (
    "missing_road_name",
    "road_class_anomaly",
    "direction_anomaly",
    "speed_limit_anomaly",
    "suspected_dead_end",
    "point_offset",
)
ATTRIBUTE_ISSUE_TYPES = ISSUE_TYPES[:-1]
VALID_ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential", "service", "unclassified"}
DEFAULT_SPEED_BY_CLASS = {
    "motorway": 100,
    "trunk": 80,
    "primary": 60,
    "secondary": 50,
    "tertiary": 40,
    "residential": 30,
    "service": 20,
    "unclassified": 30,
}


class ReferenceRoadError(ValueError):
    """Raised when the reference road file cannot support safe simulation."""


def load_reference_roads(path: Path) -> list[dict[str, Any]]:
    """Load valid WGS84 LineStrings from a GeoJSON FeatureCollection."""
    reference_path = Path(path)
    if not reference_path.is_file():
        raise ReferenceRoadError(f"参考路网不存在：{reference_path}")
    try:
        payload = json.loads(reference_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceRoadError("参考路网不是有效的 UTF-8 GeoJSON。") from exc
    if payload.get("type") != "FeatureCollection":
        raise ReferenceRoadError("参考路网必须是 GeoJSON FeatureCollection。")

    roads: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "LineString" or len(coordinates) < 2:
            continue
        if not all(
            isinstance(point, list)
            and len(point) >= 2
            and -180 <= float(point[0]) <= 180
            and -90 <= float(point[1]) <= 90
            for point in coordinates
        ):
            raise ReferenceRoadError("参考路网包含非 WGS84 坐标。")
        roads.append(feature)
    if not roads:
        raise ReferenceRoadError("参考路网中没有可用的 LineString。")
    return roads


def _meters_per_degree(latitude: float) -> tuple[float, float]:
    return 111_320.0 * max(math.cos(math.radians(latitude)), 0.01), 111_320.0


def _segment_length_m(start: list[float], end: list[float]) -> float:
    latitude = (float(start[1]) + float(end[1])) / 2
    lon_scale, lat_scale = _meters_per_degree(latitude)
    dx = (float(end[0]) - float(start[0])) * lon_scale
    dy = (float(end[1]) - float(start[1])) * lat_scale
    return math.hypot(dx, dy)


def interpolate_on_line(coordinates: list[list[float]], ratio: float) -> tuple[float, float, int]:
    """Interpolate exactly on a polyline using length-weighted segments."""
    if not 0 <= ratio <= 1:
        raise ValueError("ratio 必须在 0 到 1 之间。")
    lengths = [_segment_length_m(start, end) for start, end in zip(coordinates, coordinates[1:])]
    total = sum(lengths)
    if total <= 0:
        raise ReferenceRoadError("参考道路包含零长度几何。")
    target = ratio * total
    traversed = 0.0
    for index, length in enumerate(lengths):
        if target <= traversed + length or index == len(lengths) - 1:
            local_ratio = 0.0 if length == 0 else (target - traversed) / length
            start, end = coordinates[index], coordinates[index + 1]
            lon = float(start[0]) + (float(end[0]) - float(start[0])) * local_ratio
            lat = float(start[1]) + (float(end[1]) - float(start[1])) * local_ratio
            return lon, lat, index
        traversed += length
    raise RuntimeError("无法在参考道路上插值。")


def offset_perpendicular(
    longitude: float,
    latitude: float,
    segment_start: list[float],
    segment_end: list[float],
    offset_m: float,
) -> tuple[float, float]:
    """Offset a point perpendicular to its source road segment in meters."""
    lon_scale, lat_scale = _meters_per_degree(latitude)
    dx = (float(segment_end[0]) - float(segment_start[0])) * lon_scale
    dy = (float(segment_end[1]) - float(segment_start[1])) * lat_scale
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ReferenceRoadError("无法对零长度道路片段添加偏移。")
    normal_x, normal_y = -dy / length, dx / length
    return longitude + normal_x * offset_m / lon_scale, latitude + normal_y * offset_m / lat_scale


def _road_id(feature: dict[str, Any], fallback_index: int) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("segment_id") or properties.get("osm_id") or feature.get("id") or f"road_{fallback_index:06d}")


def _baseline_attributes(properties: dict[str, Any], road_id: str) -> dict[str, object]:
    road_class = str(properties.get("road_class") or "unclassified").strip().lower()
    if road_class not in VALID_ROAD_CLASSES:
        road_class = "unclassified"
    direction = str(properties.get("direction") or "two_way").strip() or "two_way"
    speed_value = properties.get("speed_limit")
    try:
        speed_limit = float(speed_value)
        if speed_limit <= 0:
            raise ValueError
    except (TypeError, ValueError):
        speed_limit = float(DEFAULT_SPEED_BY_CLASS[road_class])
    return {
        "road_name": str(properties.get("road_name") or f"参考道路_{road_id}").strip(),
        "road_class": road_class,
        "direction": direction,
        "speed_limit": int(speed_limit) if speed_limit.is_integer() else speed_limit,
        "region": str(properties.get("region") or "reference_area").strip(),
    }


def build_issue_schedule(count: int, issue_rate: float, offset_share: float, rng: random.Random) -> list[str | None]:
    if count < 1:
        raise ValueError("count 必须大于 0。")
    if not 0 <= issue_rate <= 1 or not 0 <= offset_share <= 1:
        raise ValueError("issue_rate 和 offset_share 必须在 0 到 1 之间。")
    issue_count = round(count * issue_rate)
    offset_count = round(issue_count * offset_share)
    attribute_count = issue_count - offset_count
    schedule: list[str | None] = ["point_offset"] * offset_count
    schedule.extend(ATTRIBUTE_ISSUE_TYPES[index % len(ATTRIBUTE_ISSUE_TYPES)] for index in range(attribute_count))
    schedule.extend([None] * (count - issue_count))
    rng.shuffle(schedule)
    return schedule


def generate_simulated_records(
    roads: list[dict[str, Any]],
    count: int = 200,
    seed: int = 42,
    issue_rate: float = 0.7,
    offset_share: float = 0.2,
    offset_min_m: float = 15.0,
    offset_max_m: float = 40.0,
) -> list[dict[str, Any]]:
    """Generate Point features tied to real reference roads with auditable labels."""
    if not roads:
        raise ReferenceRoadError("没有可用于模拟的参考道路。")
    if offset_min_m < 0 or offset_max_m < offset_min_m:
        raise ValueError("偏移距离范围无效。")
    rng = random.Random(seed)
    schedule = build_issue_schedule(count, issue_rate, offset_share, rng)
    base_time = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, Any]] = []

    for index, issue_type in enumerate(schedule, start=1):
        road_index = rng.randrange(len(roads))
        road = roads[road_index]
        properties = road.get("properties") or {}
        coordinates = road["geometry"]["coordinates"]
        road_id = _road_id(road, road_index)
        attributes = _baseline_attributes(properties, road_id)

        if issue_type == "suspected_dead_end":
            endpoint_index = 0 if rng.random() < 0.5 else -1
            longitude, latitude = map(float, coordinates[endpoint_index][:2])
            segment_index = 0 if endpoint_index == 0 else len(coordinates) - 2
        else:
            longitude, latitude, segment_index = interpolate_on_line(coordinates, rng.random())

        base_longitude, base_latitude = longitude, latitude
        injected_error = "none"
        offset_m = 0.0
        if issue_type == "point_offset":
            offset_m = rng.uniform(offset_min_m, offset_max_m)
            if rng.random() < 0.5:
                offset_m *= -1
            longitude, latitude = offset_perpendicular(
                longitude,
                latitude,
                coordinates[segment_index],
                coordinates[segment_index + 1],
                offset_m,
            )
            injected_error = f"point_offset_m={offset_m:.2f}"
        elif issue_type == "missing_road_name":
            attributes["road_name"] = ""
            injected_error = "road_name_set_empty"
        elif issue_type == "road_class_anomaly":
            attributes["road_class"] = "invalid_class"
            injected_error = "road_class_set_invalid"
        elif issue_type == "direction_anomaly":
            attributes["direction"] = "invalid_direction"
            injected_error = "direction_set_invalid"
        elif issue_type == "speed_limit_anomaly":
            attributes["speed_limit"] = 999
            injected_error = "speed_limit_set_999"
        elif issue_type == "suspected_dead_end":
            injected_error = "point_moved_to_road_endpoint"

        expected_is_issue = issue_type is not None
        record_properties = {
            "record_id": f"sim_{index:05d}",
            "report_time": (base_time + timedelta(minutes=index - 1)).isoformat(),
            "longitude": round(longitude, 7),
            "latitude": round(latitude, 7),
            "issue_type": issue_type or "normal_observation",
            "source": "simulation",
            "description": f"基于参考道路 {road_id} 生成的可验证模拟记录",
            **attributes,
            "expected_is_issue": expected_is_issue,
            "expected_issue_type": issue_type,
            "original_road_id": road_id,
            "injected_error": injected_error,
            "base_longitude": round(base_longitude, 7),
            "base_latitude": round(base_latitude, 7),
            "offset_m": round(offset_m, 2),
        }
        records.append(
            {
                "type": "Feature",
                "properties": record_properties,
                "geometry": {
                    "type": "Point",
                    "coordinates": [record_properties["longitude"], record_properties["latitude"]],
                },
            }
        )
    return records


def write_geojson(path: Path, records: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": records}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [record["properties"] for record in records]
    fieldnames = list(rows[0]) if rows else []
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
