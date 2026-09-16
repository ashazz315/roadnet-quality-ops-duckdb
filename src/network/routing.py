"""Deterministic edge-state Dijkstra with static no/only turn restrictions."""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from copy import deepcopy
from itertools import count, pairwise

from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform

from src.network.graph_builder import build_graph
from src.network.normalization import SUPPORTED_RESTRICTIONS


def validate_routing_config(config):
    fields = {
        "objective",
        "allow_immediate_uturn",
        "default_speed_kmh",
        "speed_by_road_class",
        "turn_angle_threshold_deg",
        "uturn_angle_threshold_deg",
        "turn_delay_seconds",
        "uturn_delay_seconds",
        "metric_crs",
        "max_snap_distance_m",
        "max_od_pairs_per_issue",
    }
    if set(config) != fields or config["objective"] not in {"distance", "eta"}:
        raise ValueError("Invalid replay configuration fields or routing objective")
    if type(config["allow_immediate_uturn"]) is not bool:
        raise ValueError("U-turn policy must be an explicit boolean")
    for name in (
        "default_speed_kmh",
        "turn_angle_threshold_deg",
        "uturn_angle_threshold_deg",
        "turn_delay_seconds",
        "uturn_delay_seconds",
        "max_snap_distance_m",
    ):
        value = config[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid finite routing parameter: {name}")
    speeds = config["speed_by_road_class"]
    if (
        not isinstance(speeds, dict)
        or not speeds
        or any(
            not isinstance(key, str)
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 < value <= 130
            for key, value in speeds.items()
        )
        or not 0 < config["default_speed_kmh"] <= 130
    ):
        raise ValueError("Model speeds must be within (0, 130] km/h")
    if (
        not 0
        < config["turn_angle_threshold_deg"]
        < config["uturn_angle_threshold_deg"]
        < 180
        or config["max_snap_distance_m"] <= 0
    ):
        raise ValueError("Invalid angle thresholds or snap distance")
    if (
        type(config["max_od_pairs_per_issue"]) is not int
        or not 1 <= config["max_od_pairs_per_issue"] <= 2
    ):
        raise ValueError("This replay supports one anchor OD plus one reverse control")
    crs = CRS.from_user_input(config["metric_crs"])
    if not crs.is_projected or any(axis.unit_name != "metre" for axis in crs.axis_info):
        raise ValueError("Routing geometry requires a projected metre CRS")


class TurnAwareRouter:
    """Each search state is an incoming directed edge, not merely a visited node."""

    def __init__(self, tables, config):
        validate_routing_config(config)
        self.config = deepcopy(config)
        self.graph = build_graph(tables)
        self.version = self.graph.graph["network_version"]
        self.nodes = {row["node_id"]: row for row in tables.road_node}
        self.edges = {}
        self.outgoing = defaultdict(list)
        self.restrictions = defaultdict(list)
        metric = Transformer.from_crs(
            4326, config["metric_crs"], always_xy=True
        ).transform
        for row in tables.turn_restriction:
            if row["restriction_type"] not in SUPPORTED_RESTRICTIONS:
                raise ValueError("Unsupported routing restriction semantics")
            self.restrictions[row["from_edge_id"]].append(row)
        for u, v, key, row in sorted(
            self.graph.edges(keys=True, data=True), key=lambda item: item[2]
        ):
            geometry = wkt.loads(row["geometry_wkt"])
            coords = list(geometry.coords)
            if key.endswith(":r"):
                coords.reverse()
            line = transform(metric, LineString(coords))
            if not math.isfinite(line.length) or line.length <= 0:
                raise ValueError("Cannot derive routing geometry in the supplied CRS")
            speed = config["speed_by_road_class"].get(
                row["road_class"], config["default_speed_kmh"]
            )
            source = "road_class_model"
            limit = row.get("maxspeed")
            if limit is not None and not (
                isinstance(limit, float) and math.isnan(limit)
            ):
                if (
                    isinstance(limit, bool)
                    or not isinstance(limit, (int, float))
                    or not math.isfinite(limit)
                    or limit <= 0
                ):
                    raise ValueError("Invalid maxspeed; do not silently invent an ETA")
                if limit < speed:
                    speed, source = float(limit), "maxspeed_capped_model"
            start, end = (
                line.interpolate(min(5, line.length)),
                line.interpolate(max(0, line.length - 5)),
            )
            first, last = Point(line.coords[0]), Point(line.coords[-1])
            self.edges[key] = {
                "edge_id": key,
                "segment_id": row["segment_id"],
                "u": u,
                "v": v,
                "length_m": float(row["length_m"]),
                "speed_kmh": float(speed),
                "speed_source": source,
                "travel_time_s": float(row["length_m"]) / speed * 3.6,
                "coords": coords,
                "start_heading": math.degrees(
                    math.atan2(start.y - first.y, start.x - first.x)
                ),
                "end_heading": math.degrees(math.atan2(last.y - end.y, last.x - end.x)),
            }
            self.outgoing[u].append(key)

    def turn_angle(self, incoming, outgoing):
        return (
            self.edges[outgoing]["start_heading"]
            - self.edges[incoming]["end_heading"]
            + 180
        ) % 360 - 180

    def turn_allowed(self, incoming, outgoing):
        if incoming not in self.edges or outgoing not in self.edges:
            return False
        first, second = self.edges[incoming], self.edges[outgoing]
        if first["v"] != second["u"]:
            return False
        if (
            not self.config["allow_immediate_uturn"]
            and first["segment_id"] == second["segment_id"]
            and first["u"] == second["v"]
        ):
            return False
        for row in self.restrictions[incoming]:
            if (
                row["restriction_type"].startswith("no_")
                and outgoing == row["to_edge_id"]
            ):
                return False
            if (
                row["restriction_type"].startswith("only_")
                and outgoing != row["to_edge_id"]
            ):
                return False
        return True

    def turn_delay(self, incoming, outgoing):
        angle = abs(self.turn_angle(incoming, outgoing))
        if angle >= self.config["uturn_angle_threshold_deg"]:
            return self.config["uturn_delay_seconds"]
        return (
            self.config["turn_delay_seconds"]
            if angle >= self.config["turn_angle_threshold_deg"]
            else 0.0
        )

    def validate_path(self, origin, destination, edges):
        if origin not in self.nodes or destination not in self.nodes:
            return False
        if any(
            not self.nodes[key]["routing_eligible"] for key in (origin, destination)
        ):
            return False
        if not edges:
            return origin == destination
        if any(key not in self.edges for key in edges):
            return False
        return (
            self.edges[edges[0]]["u"] == origin
            and self.edges[edges[-1]]["v"] == destination
            and all(self.turn_allowed(a, b) for a, b in pairwise(edges))
        )

    def route(self, origin, destination):
        base = {
            "origin_node_id": origin,
            "destination_node_id": destination,
            "network_version": self.version,
            "objective": self.config["objective"],
            "status": "no_path",
            "distance_m": None,
            "eta_s": None,
            "edge_ids": [],
            "edge_details": [],
            "geometry_wkt": None,
            "legal": None,
        }
        if origin not in self.nodes or destination not in self.nodes:
            return {**base, "status": "invalid_endpoint"}
        if any(
            not self.nodes[key]["routing_eligible"] for key in (origin, destination)
        ):
            return {**base, "status": "ineligible_endpoint"}
        if origin == destination:
            node = self.nodes[origin]
            return {
                **base,
                "status": "same_node",
                "distance_m": 0.0,
                "eta_s": 0.0,
                "legal": True,
                "geometry_wkt": Point(node["longitude"], node["latitude"]).wkt,
            }
        queue, best, previous = [], {}, {}
        sequence = count()
        for edge_id in self.outgoing[origin]:
            edge = self.edges[edge_id]
            cost = (
                edge["length_m"]
                if self.config["objective"] == "distance"
                else edge["travel_time_s"]
            )
            best[edge_id], previous[edge_id] = cost, None
            heapq.heappush(queue, (cost, next(sequence), edge_id))
        final = None
        while queue:
            cost, _, incoming = heapq.heappop(queue)
            if cost != best[incoming]:
                continue
            edge = self.edges[incoming]
            if edge["v"] == destination:
                final = incoming
                break
            for outgoing in self.outgoing[edge["v"]]:
                if not self.turn_allowed(incoming, outgoing):
                    continue
                next_edge = self.edges[outgoing]
                increment = (
                    next_edge["length_m"]
                    if self.config["objective"] == "distance"
                    else next_edge["travel_time_s"]
                    + self.turn_delay(incoming, outgoing)
                )
                proposed = cost + increment
                if proposed < best.get(outgoing, math.inf):
                    best[outgoing], previous[outgoing] = proposed, incoming
                    heapq.heappush(queue, (proposed, next(sequence), outgoing))
        if final is None:
            return base
        path = []
        while final is not None:
            path.append(final)
            final = previous[final]
        path.reverse()
        if not self.validate_path(origin, destination, path):
            raise ValueError(
                "Router produced an invalid directed/turn-constrained path"
            )
        details, coords = [], []
        for index, edge_id in enumerate(path):
            edge = self.edges[edge_id]
            delay = self.turn_delay(path[index - 1], edge_id) if index else 0.0
            details.append(
                {
                    key: edge[key]
                    for key in (
                        "edge_id",
                        "segment_id",
                        "u",
                        "v",
                        "length_m",
                        "speed_kmh",
                        "speed_source",
                        "travel_time_s",
                    )
                }
                | {"turn_delay_before_s": delay}
            )
            coords.extend(edge["coords"] if not coords else edge["coords"][1:])
        return {
            **base,
            "status": "found",
            "distance_m": sum(row["length_m"] for row in details),
            "eta_s": sum(
                row["travel_time_s"] + row["turn_delay_before_s"] for row in details
            ),
            "edge_ids": path,
            "edge_details": details,
            "geometry_wkt": LineString(coords).wkt,
            "legal": True,
        }
