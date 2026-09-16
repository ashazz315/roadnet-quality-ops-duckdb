"""Synthetic observations from legal Golden movements; answers returned separately."""

from __future__ import annotations

import bisect
import json
import math
import random
from datetime import datetime, timedelta
from itertools import pairwise

import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point
from shapely.ops import transform

from src.benchmark.movement import MovementModel
from src.domain import IssueType
from src.network.normalization import NetworkTables
from src.simulation import interpolate_on_line


def trajectories(
    model: MovementModel, faults: list[dict], config: dict
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    planner = random.Random(config["seed"] + 1000)
    noise = random.Random(config["seed"] + 2000)
    to_metric = Transformer.from_crs(
        4326, config["metric_crs"], always_xy=True
    ).transform
    to_wgs = Transformer.from_crs(config["metric_crs"], 4326, always_xy=True).transform
    cache = {}
    for key, edge in model.edges.items():
        coords = list(wkt.loads(edge["geometry_wkt"]).coords)
        if key.endswith(":r"):
            coords.reverse()
        metric_coords = [to_metric(*xy) for xy in coords]
        length = sum(math.dist(a, b) for a, b in pairwise(metric_coords))
        cache[key] = (coords, metric_coords, length)
    plans = []
    sample_count = 0

    def plan(anchor, fault_id):
        nonlocal sample_count
        path = model.walk(anchor, planner, tuple(config["path_edges_range"]))
        speed = planner.uniform(*config["speed_kmh_range"])
        total = sum(cache[key][2] for key in path)
        stride = speed / 3.6 * config["sample_interval_seconds"]
        offset = 0.0
        if fault_id is not None:
            # Keep the regular clock, but align its phase so even a short turn
            # approach has an observed point. This is deliberate benchmark bias.
            anchor_midpoint = (
                sum(cache[key][2] for key in path[: path.index(anchor)])
                + cache[anchor][2] / 2
            )
            offset = anchor_midpoint % stride
        count = int((total - offset) / stride) + 1
        duration = (count - 1) * config["sample_interval_seconds"]
        if duration > config["duration_hours"] * 3600:
            raise ValueError("A trip exceeds the configured observation window")
        plans.append((path, speed, total, count, fault_id, duration, offset))
        sample_count += count

    for fault in faults:
        for _ in range(config["target_paths_per_fault"]):
            plan(fault["anchor"], fault["fault_id"])
    while sample_count < config["target_point_count"]:
        plan(planner.choice(model.viable), None)
    planner.shuffle(plans)
    start = datetime.fromisoformat(config["start_time"])
    observed, truth, paths = [], [], []
    for index, (path, speed, total, count, fault_id, duration, offset) in enumerate(
        plans
    ):
        trip = f"T{index + 1:06}"
        departure = start + timedelta(
            seconds=planner.uniform(0, config["duration_hours"] * 3600 - duration)
        )
        cumulative = [0.0]
        for key in path:
            cumulative.append(cumulative[-1] + cache[key][2])
        paths.append(
            {
                "trajectory_id": trip,
                "edge_ids_json": json.dumps(path),
                "target_fault_id": fault_id,
                "speed_kmh": speed,
                "length_m": total,
                "point_count": count,
                "sample_start_distance_m": offset,
            }
        )
        for seq in range(count):
            distance = offset + seq * speed / 3.6 * config["sample_interval_seconds"]
            edge_index = min(
                bisect.bisect_right(cumulative, distance) - 1, len(path) - 1
            )
            key = path[edge_index]
            coords, metric_coords, length = cache[key]
            ratio = min(1.0, max(0.0, (distance - cumulative[edge_index]) / length))
            lon, lat, leg = interpolate_on_line(coords, ratio)
            x, y = to_metric(lon, lat)
            is_outlier = noise.random() < config["gps_outlier_fraction"]
            if is_outlier:
                radius = noise.uniform(*config["gps_outlier_range_m"])
                angle = noise.uniform(0, 2 * math.pi)
                dx, dy = radius * math.cos(angle), radius * math.sin(angle)
            else:
                dx, dy = (
                    noise.gauss(0, config["gps_sigma_m"]),
                    noise.gauss(0, config["gps_sigma_m"]),
                )
                factor = min(1.0, config["gps_max_m"] / max(math.hypot(dx, dy), 1e-12))
                dx, dy = dx * factor, dy * factor
            noisy_lon, noisy_lat = to_wgs(x + dx, y + dy)
            a, b = metric_coords[leg : leg + 2]
            heading = (
                math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) + noise.gauss(0, 3)
            ) % 360
            observed.append(
                {
                    "trajectory_id": trip,
                    "point_seq": seq,
                    "timestamp": (
                        departure
                        + timedelta(seconds=seq * config["sample_interval_seconds"])
                    ).isoformat(),
                    "longitude": noisy_lon,
                    "latitude": noisy_lat,
                    "speed_kmh": speed,
                    "heading_deg": heading,
                    "vehicle_type": "motorcar",
                    "source": "synthetic_benchmark",
                    "is_synthetic": True,
                }
            )
            truth.append(
                {
                    "trajectory_id": trip,
                    "point_seq": seq,
                    "golden_edge_id": key,
                    "clean_longitude": lon,
                    "clean_latitude": lat,
                    "noise_m": math.hypot(dx, dy),
                    "is_outlier": is_outlier,
                }
            )
    return pd.DataFrame(observed), pd.DataFrame(truth), pd.DataFrame(paths)


COMPLAINTS = {
    IssueType.CONNECTIVITY_BREAK.value: (
        "navigation_interrupted",
        "车辆正常通过，但导航在路口附近中断。",
    ),
    IssueType.TURN_RESTRICTION_CONFLICT.value: (
        "turn_not_allowed",
        "导航提示的转向与现场通行限制不一致。",
    ),
    IssueType.ONEWAY_DIRECTION_CONFLICT.value: (
        "direction_wrong",
        "道路通行方向与导航提示不一致。",
    ),
    IssueType.MISSING_OR_CHANGED_ROAD_CANDIDATE.value: (
        "road_missing",
        "实际可通行的道路在地图中未显示。",
    ),
}


def feedback(
    golden: NetworkTables, model: MovementModel, faults: list[dict], config: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = random.Random(config["seed"] + 3000)
    to_metric = Transformer.from_crs(
        4326, config["metric_crs"], always_xy=True
    ).transform
    to_wgs = Transformer.from_crs(config["metric_crs"], 4326, always_xy=True).transform
    segments = {row["segment_id"]: row for row in golden.road_segment}
    nodes = {row["node_id"]: row for row in golden.road_node}
    locations, protected = {}, set()
    for fault in faults:
        before = fault["before"]
        if fault["target_object_type"] == "turn":
            node = nodes[before["via_node_id"]]
            point = Point(to_metric(node["longitude"], node["latitude"]))
            protected.update((before["from_segment_id"], before["to_segment_id"]))
        elif fault["target_object_type"] == "node":
            node = fault["original_node"]
            point = Point(to_metric(node["longitude"], node["latitude"]))
            protected.add(fault["target"])
        else:
            point = transform(to_metric, wkt.loads(before["geometry_wkt"])).interpolate(
                0.5, normalized=True
            )
            protected.add(fault["target"])
        locations[fault["fault_id"]] = point
    covered = max(1, round(len(faults) * config["feedback_fault_coverage"]))
    chosen = rng.sample(faults, covered)
    supported = chosen + rng.choices(
        chosen, k=config["supporting_feedback_count"] - covered
    )
    plans = [
        (
            fault["issue_type"],
            locations[fault["fault_id"]],
            fault["fault_id"],
            model.edges[fault["anchor"]]["segment_id"],
        )
        for fault in supported
    ]
    healthy = []
    for segment in sorted(
        {model.edges[key]["segment_id"] for key in model.viable} - protected
    ):
        point = transform(
            to_metric, wkt.loads(segments[segment]["geometry_wkt"])
        ).interpolate(0.5, normalized=True)
        if all(point.distance(location) > 100 for location in locations.values()):
            healthy.append((segment, point))
    distractors = config["feedback_count"] - len(plans)
    if len(healthy) < distractors:
        raise ValueError("Not enough distinct healthy controls for feedback")
    for segment, point in rng.sample(healthy, distractors):
        plans.append((rng.choice(list(COMPLAINTS)), point, None, segment))
    rng.shuffle(plans)
    start = datetime.fromisoformat(config["start_time"])
    observed, truth = [], []
    for index, (kind, point, fault_id, segment) in enumerate(plans):
        identifier = f"FB{index + 1:04}"
        angle, radius = rng.uniform(0, 2 * math.pi), rng.uniform(0, 10)
        lon, lat = to_wgs(
            point.x + radius * math.cos(angle), point.y + radius * math.sin(angle)
        )
        complaint, description = COMPLAINTS[kind]
        observed.append(
            {
                "feedback_id": identifier,
                "report_time": (
                    start
                    + timedelta(seconds=rng.uniform(0, config["duration_hours"] * 3600))
                ).isoformat(),
                "longitude": lon,
                "latitude": lat,
                "feedback_type": complaint,
                "description": description,
                "source": "synthetic_benchmark",
                "is_synthetic": True,
            }
        )
        truth.append(
            {
                "feedback_id": identifier,
                "fault_id": fault_id,
                "is_supporting": fault_id is not None,
                "golden_segment_id": segment,
            }
        )
    return pd.DataFrame(observed), pd.DataFrame(truth)
