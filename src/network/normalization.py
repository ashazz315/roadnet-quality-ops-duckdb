"""Pure OSM-to-road-object normalization; no I/O, scoring, or fault labels."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise

from pyproj import CRS, Transformer
from shapely.geometry import LineString, box
from shapely.ops import transform

ALGORITHM_VERSION = "osm-normalization-v1"
SUPPORTED_RESTRICTIONS = {
    "no_left_turn", "no_right_turn", "no_straight_on", "no_u_turn",
    "only_left_turn", "only_right_turn", "only_straight_on",
}


@dataclass
class NetworkTables:
    road_segment: list[dict]
    road_node: list[dict]
    turn_restriction: list[dict]
    audit: list[dict]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _direction(tags: dict) -> str | None:
    raw = next((tags[key] for key in ("oneway:motorcar", "oneway:motor_vehicle", "oneway:vehicle", "oneway") if key in tags), "").lower().strip()
    if raw in {"yes", "true", "1"}:
        return "forward"
    if raw == "-1":
        return "reverse"
    if raw in {"no", "false", "0"}:
        return "both"
    if raw:
        return None
    return "forward" if tags.get("junction") == "roundabout" or tags.get("highway") == "motorway" else "both"


def _numeric_tag(raw: str | None, *, speed: bool = False) -> float | None:
    if raw is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh)?\s*", str(raw))
    if match is None or (not speed and match[2]):
        return None
    value = float(match[1])
    return round(value * 1.609344, 6) if speed and match[2] == "mph" else value


def _access(tags: dict) -> str:
    return next((tags[key] for key in ("motorcar", "motor_vehicle", "vehicle", "access") if key in tags), "yes")


def _conditional(tags: dict) -> bool:
    return any(key.endswith(":conditional") for key in tags)


def _clip_ordered(line: LineString, bounds) -> list[LineString]:
    """Clip individual legs, retaining traversal order and repeated road geometry."""
    if bounds.covers(line):
        return [line] if line.length else []
    parts, current = [], []
    for a, b in pairwise(line.coords):
        if a == b:
            continue
        clipped = LineString([a, b]).intersection(bounds)
        if clipped.geom_type != "LineString" or clipped.is_empty or not clipped.length:
            if len(current) > 1:
                parts.append(LineString(current))
            current = []
            continue
        coords = list(clipped.coords)
        if sum((coords[0][i]-a[i])**2 for i in (0, 1)) > sum((coords[-1][i]-a[i])**2 for i in (0, 1)):
            coords.reverse()
        if current and current[-1] == coords[0]:
            current.extend(coords[1:])
        else:
            if len(current) > 1:
                parts.append(LineString(current))
            current = coords
        if coords[-1] != tuple(b):
            parts.append(LineString(current))
            current = []
    if len(current) > 1:
        parts.append(LineString(current))
    return parts


def normalize_osm(payload: dict, config: dict, network_version: str) -> NetworkTables:
    """Split at OSM intersections/via nodes, then clip geometry to the core bbox.

    No spatial snapping: distinct OSM IDs remain distinct even at identical XY.
    Synthetic boundary nodes describe clipping only, never an invented road.
    Every input way/restriction receives an explicit retained/excluded audit row.
    """
    if payload.get("remark"):
        raise ValueError("Partial Overpass response cannot become a Golden Network")
    elements = payload["elements"]
    identities = [(item["type"], item["id"]) for item in elements]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate OSM object IDs")
    nodes = {item["id"]: item for item in elements if item["type"] == "node"}
    ways = sorted((item for item in elements if item["type"] == "way"), key=lambda item: item["id"])
    relations = sorted((item for item in elements if item["type"] == "relation"), key=lambda item: item["id"])
    bounds = box(*config["bbox"])
    metric_crs = CRS.from_user_input(config["metric_crs"])
    if not metric_crs.is_projected or any(axis.unit_name != "metre" for axis in metric_crs.axis_info):
        raise ValueError("Road lengths require a projected CRS in metres")
    projector = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
    audit = []

    def record(kind: str, object_id: int | str, status: str, reason: str, count: int = 0) -> None:
        audit.append({"object_type": kind, "object_id": str(object_id), "status": status, "reason": reason, "output_count": count})

    eligible = []
    for way in ways:
        tags = way.get("tags", {})
        reason = None
        if tags.get("highway") not in config["highway_classes"]:
            reason = "outside_highway_profile"
        elif tags.get("area") == "yes":
            reason = "area_highway_not_linear_road"
        elif _access(tags) not in {"yes", "designated", "permissive"}:
            reason = "restricted_or_unknown_motorcar_access"
        elif _direction(tags) is None:
            reason = "unsupported_oneway_value"
        elif len(way.get("nodes", [])) < 2:
            reason = "too_few_nodes"
        else:
            for node_id in way["nodes"]:
                node = nodes.get(node_id, {})
                lon, lat = node.get("lon"), node.get("lat")
                if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)) or not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    reason = "missing_or_invalid_osm_node"
                    break
        if reason:
            record("way", way["id"], "excluded", reason)
        else:
            eligible.append(way)

    occurrences = Counter(node_id for way in eligible for node_id in way["nodes"])
    via_nodes = {member["ref"] for rel in relations for member in rel.get("members", []) if member.get("role") == "via" and member["type"] == "node"}
    constrained_nodes = {node_id for node_id, node in nodes.items() if node.get("tags", {}).get("barrier") or _access(node.get("tags", {})) not in {"yes", "designated", "permissive"}}
    segment_rows = []
    node_rows = {}
    for way in eligible:
        refs, tags = way["nodes"], way.get("tags", {})
        static = not _conditional(tags)
        cuts = [0] + [i for i in range(1, len(refs)-1) if occurrences[refs[i]] > 1 or refs[i] in via_nodes or refs[i] in constrained_nodes] + [len(refs)-1]
        before_count = len(segment_rows)
        for start, end in pairwise(cuts):
            coords = [(nodes[ref]["lon"], nodes[ref]["lat"]) for ref in refs[start:end+1]]
            line = LineString(coords)
            parts = _clip_ordered(line, bounds)
            for part_index, part in enumerate(parts):
                length = round(transform(projector, part).length, 6)
                if length <= 0:
                    continue
                segment_id = f"osm:w{way['id']}:{start}-{end}:c{part_index}"
                endpoints = []
                for side, xy, original_ref, original_xy in (
                    ("from", part.coords[0], refs[start], coords[0]),
                    ("to", part.coords[-1], refs[end], coords[-1]),
                ):
                    is_original = tuple(xy) == tuple(original_xy)
                    node_id = f"osm:n{original_ref}" if is_original else f"boundary:{segment_id}:{side}"
                    endpoints.append(node_id)
                    original_tags = nodes[original_ref].get("tags", {}) if is_original else {}
                    node_rows[node_id] = {
                        "node_id": node_id, "osm_node_id": str(original_ref) if is_original else None,
                        "longitude": float(xy[0]), "latitude": float(xy[1]),
                        "is_boundary": not is_original or bounds.boundary.distance(part.interpolate(0 if side == "from" else part.length)) < 1e-10,
                        "routing_eligible": not original_tags.get("barrier") and _access(original_tags) in {"yes", "designated", "permissive"},
                        "tags_json": _json(original_tags), "network_version": network_version,
                    }
                direction = _direction(tags)
                lanes = _numeric_tag(tags.get("lanes"))
                segment_rows.append({
                    "segment_id": segment_id, "from_node_id": endpoints[0], "to_node_id": endpoints[1],
                    "osm_way_id": str(way["id"]), "name": tags.get("name"), "road_class": tags["highway"],
                    "oneway": direction != "both", "direction": direction,
                    "lanes": int(lanes) if lanes is not None and lanes.is_integer() else None,
                    "maxspeed": _numeric_tag(tags.get("maxspeed"), speed=True),
                    "length_m": length, "geometry_wkt": part.wkt,
                    "routing_eligible": static, "tags_json": _json(tags), "network_version": network_version,
                })
        count = len(segment_rows) - before_count
        reason = "retained" if count else "outside_bbox_or_zero_length"
        if count and not static:
            reason = "retained_with_unmodelled_conditional_tags"
        record("way", way["id"], "retained" if count else "excluded", reason, count)

    degree = Counter(node_id for segment in segment_rows for node_id in (segment["from_node_id"], segment["to_node_id"]))
    for node in node_rows.values():
        node["node_degree"] = degree[node["node_id"]]
    by_way = defaultdict(list)
    for segment in segment_rows:
        by_way[segment["osm_way_id"]].append(segment)

    restriction_rows = []
    for rel in relations:
        tags = rel.get("tags", {})
        members = rel.get("members", [])
        groups = {role: [member for member in members if member.get("role") == role] for role in ("from", "via", "to")}
        restriction = next((tags[key] for key in ("restriction:motorcar", "restriction:motor_vehicle", "restriction:vehicle", "restriction") if key in tags), "")
        reason = None
        if tags.get("type") != "restriction":
            reason = "not_a_restriction"
        elif _conditional(tags):
            reason = "conditional_restriction_not_supported"
        elif set(tags.get("except", "").replace(" ", "").split(";")) & {"motorcar", "motor_vehicle", "vehicle"}:
            reason = "restriction_exempts_motorcar"
        elif any(len(groups[role]) != 1 for role in groups) or len(members) != 3:
            reason = "unsupported_member_cardinality"
        elif groups["via"][0]["type"] != "node" or groups["from"][0]["type"] != "way" or groups["to"][0]["type"] != "way":
            reason = "via_way_or_invalid_member_type"
        elif restriction not in SUPPORTED_RESTRICTIONS:
            reason = "unsupported_restriction_type"
        pairs = []
        if reason is None:
            via = f"osm:n{groups['via'][0]['ref']}"
            incoming, outgoing = [], []
            for role, collection in (("from", incoming), ("to", outgoing)):
                for segment in by_way[str(groups[role][0]["ref"])]:
                    for suffix, u, v in directed_edges(segment):
                        if (role == "from" and v == via) or (role == "to" and u == via):
                            collection.append((segment["segment_id"], f"{segment['segment_id']}:{suffix}"))
            pairs = [(a, b) for a in incoming for b in outgoing]
            if restriction == "no_u_turn" and groups["from"][0]["ref"] == groups["to"][0]["ref"]:
                pairs = [(a, b) for a, b in pairs if a[0] == b[0] and a[1] != b[1]]
            elif len(pairs) > 1:
                reason = "ambiguous_segment_mapping"
            if not pairs:
                reason = "missing_clipped_excluded_or_wrong_direction_member"
        if reason:
            record("relation", rel["id"], "excluded", reason)
            continue
        for index, (from_edge, to_edge) in enumerate(sorted(pairs)):
            restriction_rows.append({
                "restriction_id": f"osm:r{rel['id']}:{index}", "osm_relation_id": str(rel["id"]),
                "from_segment_id": from_edge[0], "via_node_id": via, "to_segment_id": to_edge[0],
                "from_edge_id": from_edge[1], "to_edge_id": to_edge[1],
                "restriction_type": restriction, "tags_json": _json(tags), "network_version": network_version,
            })
        record("relation", rel["id"], "retained", "supported_via_node", len(pairs))

    return NetworkTables(
        sorted(segment_rows, key=lambda row: row["segment_id"]),
        sorted(node_rows.values(), key=lambda row: row["node_id"]),
        sorted(restriction_rows, key=lambda row: row["restriction_id"]),
        sorted(audit, key=lambda row: (row["object_type"], row["object_id"])),
    )


def directed_edges(segment: dict) -> list[tuple[str, str, str]]:
    result = []
    if segment["direction"] in {"forward", "both"}:
        result.append(("f", segment["from_node_id"], segment["to_node_id"]))
    if segment["direction"] in {"reverse", "both"}:
        result.append(("r", segment["to_node_id"], segment["from_node_id"]))
    return result
