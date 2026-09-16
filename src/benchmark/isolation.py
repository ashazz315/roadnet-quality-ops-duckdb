"""Uniform input projections and opaque IDs; original identities stay in truth."""

from __future__ import annotations

import hashlib

from src.network.normalization import NetworkTables
from src.network.validation import validate_network

NETWORK_COLUMNS = {
    "road_segment": (
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
    ),
    "road_node": (
        "node_id",
        "longitude",
        "latitude",
        "node_degree",
        "is_boundary",
        "routing_eligible",
        "network_version",
    ),
    "turn_restriction": (
        "restriction_id",
        "from_segment_id",
        "via_node_id",
        "to_segment_id",
        "from_edge_id",
        "to_edge_id",
        "restriction_type",
        "network_version",
    ),
}
ID_FIELDS = {
    "segment_id": "s",
    "from_segment_id": "s",
    "to_segment_id": "s",
    "node_id": "n",
    "from_node_id": "n",
    "to_node_id": "n",
    "via_node_id": "n",
    "restriction_id": "t",
}
OBSERVATION_COLUMNS = {
    "trajectory_point": (
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
    ),
    "user_feedback": (
        "feedback_id",
        "report_time",
        "longitude",
        "latitude",
        "feedback_type",
        "description",
        "source",
        "is_synthetic",
    ),
}


def isolate_inputs(
    golden: NetworkTables, corrupted: NetworkTables, faults: list[dict], version: str
) -> tuple[NetworkTables, dict]:
    mapping = {"s": {}, "n": {}, "t": {}}
    for tables in (golden, corrupted):
        for entity, key, kind in (
            ("road_segment", "segment_id", "s"),
            ("road_node", "node_id", "n"),
            ("turn_restriction", "restriction_id", "t"),
        ):
            for row in getattr(tables, entity):
                original = row[key]
                mapping[kind][original] = (
                    kind
                    + "-"
                    + hashlib.sha256(
                        f"{version}:{kind}:{original}".encode()
                    ).hexdigest()[:24]
                )
    frames = {}
    for entity, columns in NETWORK_COLUMNS.items():
        projected = []
        for row in getattr(corrupted, entity):
            result = {key: row[key] for key in columns}
            for key, kind in ID_FIELDS.items():
                if key in result:
                    result[key] = mapping[kind][result[key]]
            for key in ("from_edge_id", "to_edge_id"):
                if key in result:
                    original, suffix = result[key].rsplit(":", 1)
                    result[key] = mapping["s"][original] + ":" + suffix
            projected.append(result)
        frames[entity] = sorted(projected, key=lambda row: row[columns[0]])
    for fault in faults:
        before = fault["before"]
        if fault["target_object_type"] == "turn":
            fault["input_target"] = {
                key: mapping[ID_FIELDS[key]][before[key]]
                for key in ("from_segment_id", "via_node_id", "to_segment_id")
            }
        else:
            fault["input_target"] = {"segment_id": mapping["s"][fault["target"]]}
            if fault["target_object_type"] == "node":
                fault["input_target"]["node_id"] = mapping["n"][
                    fault["added_node"]["node_id"]
                ]
    result = NetworkTables(**frames, audit=[])
    validate_network(result)
    return result, mapping
