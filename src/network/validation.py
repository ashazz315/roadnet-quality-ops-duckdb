"""Validate normalized road-object identities, geometry, and turn references."""

from __future__ import annotations

import math

from shapely import wkt
from shapely.errors import ShapelyError

from src.network.normalization import NetworkTables, directed_edges


def validate_network(tables: NetworkTables) -> dict:
    errors = []
    for name, rows, key in (
        ("road_node", tables.road_node, "node_id"),
        ("road_segment", tables.road_segment, "segment_id"),
        ("turn_restriction", tables.turn_restriction, "restriction_id"),
    ):
        if len({row[key] for row in rows}) != len(rows):
            errors.append(f"{name}: duplicate object ID")
    if not tables.road_segment or not tables.road_node:
        errors.append("network must have road segments and nodes")
    versions = {row["network_version"] for rows in (tables.road_segment, tables.road_node, tables.turn_restriction) for row in rows}
    if len(versions) != 1 or not next(iter(versions), ""):
        errors.append("network must have one nonempty version")
    nodes = {row["node_id"]: row for row in tables.road_node}
    edges = {}
    for row in tables.road_node:
        lon, lat = row["longitude"], row["latitude"]
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            errors.append(f"{row['node_id']}: invalid WGS84 coordinates")
    for row in tables.road_segment:
        label = row["segment_id"]
        if row["direction"] not in {"forward", "reverse", "both"} or row["oneway"] != (row["direction"] != "both"):
            errors.append(f"{label}: inconsistent direction")
        if not math.isfinite(row["length_m"]) or row["length_m"] <= 0:
            errors.append(f"{label}: invalid length")
        if row["from_node_id"] not in nodes or row["to_node_id"] not in nodes:
            errors.append(f"{label}: dangling node reference")
            continue
        try:
            geometry = wkt.loads(row["geometry_wkt"])
            if geometry.geom_type != "LineString" or geometry.is_empty or not geometry.is_valid:
                raise ValueError("expected a valid nonempty LineString")
            for xy, node_id in ((geometry.coords[0], row["from_node_id"]), (geometry.coords[-1], row["to_node_id"])):
                node = nodes[node_id]
                if abs(xy[0] - node["longitude"]) > 1e-9 or abs(xy[1] - node["latitude"]) > 1e-9:
                    errors.append(f"{label}: geometry endpoint does not match node")
        except (ShapelyError, ValueError, TypeError) as exc:
            errors.append(f"{label}: invalid geometry ({exc})")
        for suffix, u, v in directed_edges(row):
            edges[f"{label}:{suffix}"] = (u, v, label)
    for row in tables.turn_restriction:
        incoming, outgoing = edges.get(row["from_edge_id"]), edges.get(row["to_edge_id"])
        if not incoming or not outgoing or incoming[1] != row["via_node_id"] or outgoing[0] != row["via_node_id"] or incoming[2] != row["from_segment_id"] or outgoing[2] != row["to_segment_id"]:
            errors.append(f"{row['restriction_id']}: invalid directed turn reference")
    if errors:
        raise ValueError("Invalid network: " + "; ".join(errors[:20]))
    return {"road_segment": len(tables.road_segment), "road_node": len(tables.road_node), "turn_restriction": len(tables.turn_restriction), "validation_errors": 0}
