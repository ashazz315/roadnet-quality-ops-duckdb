"""Strict observable-only schemas and auditable V2 observation cleaning."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd
from pyproj import CRS

from src.network.normalization import NetworkTables
from src.network.validation import validate_network

NETWORK_FIELDS = {
    "road_segment": {
        "segment_id",
        "from_node_id",
        "to_node_id",
        "name",
        "road_class",
        "oneway",
        "direction",
        "lanes",
        "maxspeed",
        "length_m",
        "geometry_wkt",
        "routing_eligible",
        "network_version",
    },
    "road_node": {
        "node_id",
        "longitude",
        "latitude",
        "node_degree",
        "is_boundary",
        "routing_eligible",
        "network_version",
    },
    "turn_restriction": {
        "restriction_id",
        "from_segment_id",
        "via_node_id",
        "to_segment_id",
        "from_edge_id",
        "to_edge_id",
        "restriction_type",
        "network_version",
    },
}
OBSERVATION_FIELDS = {
    "trajectory_point": {
        "trajectory_id",
        "point_seq",
        "timestamp",
        "longitude",
        "latitude",
        "speed_kmh",
        "heading_deg",
        "vehicle_type",
        "source",
        "is_synthetic",
        "network_version",
    },
    "user_feedback": {
        "feedback_id",
        "report_time",
        "longitude",
        "latitude",
        "feedback_type",
        "description",
        "source",
        "is_synthetic",
        "network_version",
    },
}


def validate_rules(rules: dict) -> None:
    numeric = {
        "match_distance_m",
        "review_distance_m",
        "ambiguity_margin_m",
        "heading_tolerance_deg",
        "max_speed_kmh",
        "max_gap_seconds",
        "endpoint_distance_m",
        "connectivity_gap_m",
        "feedback_radius_m",
        "minimum_trajectories",
        "minimum_passage_points",
        "minimum_passage_distance_m",
        "minimum_time_bins",
        "time_bin_hours",
        "oneway_reverse_ratio",
        "turn_minimum_opportunities",
        "missing_turn_max_ratio",
        "forbidden_turn_min_ratio",
        "missing_distance_m",
        "corridor_minimum_points",
        "corridor_minimum_length_m",
        "corridor_cluster_distance_m",
        "corridor_heading_tolerance_deg",
        "support_saturation",
        "feedback_saturation",
        "persistence_saturation",
        "missing_turn_confidence_cap",
    }
    if set(rules) != numeric | {"metric_crs", "confidence_weights"}:
        raise ValueError("Analysis rules must have exactly the documented fields")
    for key in numeric:
        value = rules[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid rule: {key}")
    for key in (
        "minimum_trajectories",
        "minimum_passage_points",
        "minimum_time_bins",
        "turn_minimum_opportunities",
        "corridor_minimum_points",
    ):
        if type(rules[key]) is not int or rules[key] < 2:
            raise ValueError(f"Expected integer >= 2: {key}")
    if (
        rules["corridor_minimum_points"] < 3
        or not rules["match_distance_m"]
        < rules["missing_distance_m"]
        < rules["review_distance_m"]
    ):
        raise ValueError("Invalid matching/corridor thresholds")
    for key in (
        "oneway_reverse_ratio",
        "missing_turn_max_ratio",
        "forbidden_turn_min_ratio",
        "missing_turn_confidence_cap",
    ):
        if not 0 < rules[key] <= 1:
            raise ValueError(f"Expected fraction: {key}")
    for key in ("heading_tolerance_deg", "corridor_heading_tolerance_deg"):
        if rules[key] >= 90:
            raise ValueError("Axial heading tolerance must be below 90 degrees")
    weights = rules["confidence_weights"]
    if (
        set(weights)
        != {
            "data_quality",
            "trajectory_support",
            "topology_support",
            "feedback_support",
            "persistence",
        }
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in weights.values()
        )
        or not math.isclose(sum(weights.values()), 1)
    ):
        raise ValueError(
            "Confidence weights must be finite, nonnegative and sum to one"
        )
    crs = CRS.from_user_input(rules["metric_crs"])
    if not crs.is_projected or any(axis.unit_name != "metre" for axis in crs.axis_info):
        raise ValueError("Analysis requires a projected metre CRS")


def validate_frames(frames: dict[str, pd.DataFrame], metadata: dict) -> NetworkTables:
    expected = set(NETWORK_FIELDS) | set(OBSERVATION_FIELDS)
    if set(frames) != expected or set(metadata) != expected:
        raise ValueError("Analysis requires exactly five observable input entities")
    versions = {value.network_version for value in metadata.values()}
    if len(versions) != 1:
        raise ValueError("All inputs must reference the same diagnosis network")
    version = next(iter(versions))
    for name, frame in frames.items():
        columns = set(frame) - {"geometry"}
        required = NETWORK_FIELDS.get(name, OBSERVATION_FIELDS.get(name))
        allowed = required | {"is_synthetic"}
        if columns - allowed or required - columns:
            raise ValueError(
                f"Unexpected/missing observable fields for {name}: {sorted(columns ^ required)}"
            )
        if not frame["network_version"].eq(version).all():
            raise ValueError(f"Table version conflicts with metadata: {name}")
        if (
            "is_synthetic" in frame
            and not frame["is_synthetic"]
            .map(
                lambda v, expected=metadata[name].is_synthetic: (
                    type(v) is bool and v == expected
                )
            )
            .all()
        ):
            raise ValueError(f"Synthetic flag conflicts with metadata: {name}")
        if name in NETWORK_FIELDS:
            for column in ("routing_eligible", "is_boundary", "oneway"):
                if (
                    column in frame
                    and not frame[column].map(lambda v: type(v) is bool).all()
                ):
                    raise ValueError(
                        f"Network boolean must be explicit: {name}.{column}"
                    )
    tables = NetworkTables(
        **{
            name: frame.drop(columns="geometry", errors="ignore").to_dict("records")
            for name, frame in frames.items()
            if name in NETWORK_FIELDS
        },
        audit=[],
    )
    validate_network(tables)
    degree = Counter(
        node
        for row in tables.road_segment
        for node in (row["from_node_id"], row["to_node_id"])
    )
    for row in tables.road_node:
        if row["node_degree"] != degree[row["node_id"]]:
            raise ValueError("Node degree does not match the supplied road topology")
    for rows, field in (
        (tables.road_node, "node_id"),
        (tables.road_segment, "segment_id"),
        (tables.turn_restriction, "restriction_id"),
    ):
        if any(
            not isinstance(row[field], str) or not row[field].strip() for row in rows
        ):
            raise ValueError("Network object IDs must be nonempty strings")
    for row in tables.turn_restriction:
        if not row["restriction_type"].startswith(("no_", "only_")):
            raise ValueError("Unsupported turn restriction semantics")
    return tables


def clean_observations(
    frame: pd.DataFrame, entity: str, rules: dict, bounds: tuple
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep all rejected rows and reasons; never silently repair coordinates/times."""
    result = frame.reset_index(drop=True).copy()
    result["input_row_id"] = np.arange(len(result))
    reasons = [[] for _ in range(len(result))]

    def reject(mask, reason):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            reasons[index].append(reason)

    identifier = "trajectory_id" if entity == "trajectory_point" else "feedback_id"
    time_field = "timestamp" if entity == "trajectory_point" else "report_time"
    reject(
        ~result[identifier].map(lambda v: isinstance(v, str) and bool(v.strip())),
        "invalid_identifier",
    )
    for field in ("longitude", "latitude") + (
        ("speed_kmh", "heading_deg", "point_seq")
        if entity == "trajectory_point"
        else ()
    ):
        is_bool = result[field].map(lambda v: isinstance(v, (bool, np.bool_)))
        result[field] = pd.to_numeric(result[field], errors="coerce")
        reject(is_bool | ~np.isfinite(result[field]), f"invalid_{field}")
    reject(
        ~result.longitude.between(-180, 180) | ~result.latitude.between(-90, 90),
        "invalid_coordinate",
    )
    west, south, east, north = bounds
    reject(
        ~result.longitude.between(west - 0.01, east + 0.01)
        | ~result.latitude.between(south - 0.01, north + 0.01),
        "outside_analysis_area",
    )

    def explicit_timezone(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.utcoffset() is not None
        except (ValueError, TypeError):
            return False

    zoned = result[time_field].map(explicit_timezone)
    parsed = pd.to_datetime(
        result[time_field], utc=True, errors="coerce", format="mixed"
    )
    reject(~zoned | parsed.isna(), "invalid_or_naive_timestamp")
    result[time_field] = parsed
    if entity == "trajectory_point":
        reject(
            ~result.speed_kmh.between(1, rules["max_speed_kmh"]), "unsupported_speed"
        )
        reject(~result.heading_deg.between(0, 360, inclusive="left"), "invalid_heading")
        reject((result.point_seq < 0) | (result.point_seq % 1 != 0), "invalid_sequence")
        reject(~result.vehicle_type.eq("motorcar"), "unsupported_vehicle")
        reject(
            result.duplicated([identifier, "point_seq"], keep=False),
            "duplicate_point_key",
        )
        ordered = result.sort_values([identifier, "point_seq", "input_row_id"])
        delta = (
            ordered.groupby(identifier, dropna=False)[time_field]
            .diff()
            .dt.total_seconds()
        )
        bad = pd.Series(False, index=result.index)
        bad.loc[ordered.index] = delta.le(0).to_numpy()
        reject(bad, "non_increasing_timestamp")
    else:
        reject(result.duplicated(identifier, keep=False), "duplicate_feedback_key")
        reject(
            ~result.feedback_type.isin(
                {
                    "navigation_interrupted",
                    "route_unreasonable",
                    "turn_not_allowed",
                    "direction_wrong",
                    "road_missing",
                }
            ),
            "unsupported_feedback_type",
        )
    result["rejection_reason"] = [";".join(value) for value in reasons]
    result["quality_flag"] = np.where(
        result.rejection_reason.eq(""), "valid", "rejected"
    )
    accepted = result.loc[result.quality_flag.eq("valid")].copy()
    rejected = result.loc[result.quality_flag.eq("rejected")].copy()
    rejected["raw_record_json"] = [
        json.dumps(
            {
                key: (
                    {"nonfinite_number": str(value)}
                    if isinstance(value, (float, np.floating))
                    and not math.isfinite(value)
                    else value
                )
                for key, value in frame.iloc[int(index)]
                .drop(labels="geometry", errors="ignore")
                .to_dict()
                .items()
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            allow_nan=False,
        )
        for index in rejected.input_row_id
    ]
    if entity == "trajectory_point":
        accepted["point_seq"] = accepted.point_seq.astype("int64")
        accepted = accepted.sort_values([identifier, "point_seq"]).reset_index(
            drop=True
        )
    else:
        accepted = accepted.sort_values(identifier).reset_index(drop=True)
    return accepted, rejected
