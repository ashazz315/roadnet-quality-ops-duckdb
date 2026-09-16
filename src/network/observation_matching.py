"""Metric, direction-neutral local matching; no Golden or route inference."""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely import STRtree, wkt
from shapely.geometry import Point
from shapely.ops import transform

from src.network.graph_builder import build_graph


def angle_delta(first, second):
    return np.abs((first - second + 180) % 360 - 180)


class ObservationNetwork:
    def __init__(self, tables, rules):
        self.rules = rules
        self.graph = build_graph(tables)
        self.nodes = {row["node_id"]: row for row in tables.road_node}
        self.roads = {row["segment_id"]: row for row in tables.road_segment}
        self.to_metric = Transformer.from_crs(
            4326, rules["metric_crs"], always_xy=True
        ).transform
        self.to_wgs = Transformer.from_crs(
            rules["metric_crs"], 4326, always_xy=True
        ).transform
        self.node_points = {
            key: Point(self.to_metric(row["longitude"], row["latitude"]))
            for key, row in self.nodes.items()
        }
        eligible = {data["segment_id"] for _, _, data in self.graph.edges(data=True)}
        self.ids = sorted(eligible)
        if not self.ids:
            raise ValueError("No routable roads for observation matching")
        self.lines = np.array(
            [
                transform(self.to_metric, wkt.loads(self.roads[key]["geometry_wkt"]))
                for key in self.ids
            ],
            dtype=object,
        )
        self.line_by_id = dict(zip(self.ids, self.lines, strict=True))
        self.tree = STRtree(self.lines)
        self.lengths = shapely.length(self.lines)
        self.edges = {
            key: (u, v, data["segment_id"])
            for u, v, key, data in self.graph.edges(keys=True, data=True)
        }
        self.restrictions = tables.turn_restriction

    def ends(self, segment, direction):
        row = self.roads[segment]
        pair = (row["from_node_id"], row["to_node_id"])
        return pair if direction == "f" else pair[::-1]

    def allowed(self, incoming, outgoing):
        if incoming not in self.edges or outgoing not in self.edges:
            return False
        first, second = self.edges[incoming], self.edges[outgoing]
        if first[1] != second[0] or first[2] == second[2]:
            return False
        for restriction in self.restrictions:
            if restriction["from_edge_id"] == incoming:
                if (
                    restriction["restriction_type"].startswith("no_")
                    and restriction["to_edge_id"] == outgoing
                ):
                    return False
                if (
                    restriction["restriction_type"].startswith("only_")
                    and restriction["to_edge_id"] != outgoing
                ):
                    return False
        return True

    def interior(self, segment):
        row = self.roads[segment]
        return not any(
            self.nodes[row[key]]["is_boundary"]
            for key in ("from_node_id", "to_node_id")
        )

    def nearby_feedback(self, feedback, types, geometry):
        if feedback.empty:
            return []
        selected = feedback.loc[feedback.feedback_type.isin(types)]
        points = shapely.points(selected.x.to_numpy(), selected.y.to_numpy())
        return selected.loc[
            shapely.distance(points, geometry) <= self.rules["feedback_radius_m"]
        ].to_dict("records")

    def wgs_geometry(self, geometry):
        return transform(self.to_wgs, geometry)


def project_feedback(frame, network):
    result = frame.copy()
    result["x"], result["y"] = network.to_metric(
        result.longitude.to_numpy(), result.latitude.to_numpy()
    )
    return result


def match_observations(frame, network):
    """Pick by distance and axial heading, deliberately ignoring legal direction."""
    result = frame.copy().reset_index(drop=True)
    result["x"], result["y"] = network.to_metric(
        result.longitude.to_numpy(), result.latitude.to_numpy()
    )
    for name, default in {
        "matched_segment_id": "",
        "match_distance_m": np.nan,
        "match_score": 0.0,
        "match_status": "unmatched",
        "observed_direction": "",
        "position_m": np.nan,
        "axial_heading_error_deg": np.nan,
    }.items():
        result[name] = default
    if result.empty:
        return result
    points = shapely.points(result.x.to_numpy(), result.y.to_numpy())
    nearest, distances = network.tree.query_nearest(
        points, return_distance=True, all_matches=False
    )
    result.loc[nearest[0], "matched_segment_id"] = np.asarray(network.ids)[nearest[1]]
    result.loc[nearest[0], "match_distance_m"] = distances
    pairs = network.tree.query(
        points, predicate="dwithin", distance=network.rules["review_distance_m"]
    )
    if pairs.shape[1]:
        rows, roads = pairs
        lines = network.lines[roads]
        positions = shapely.line_locate_point(lines, points[rows])
        a = shapely.line_interpolate_point(lines, np.maximum(positions - 3, 0))
        b = shapely.line_interpolate_point(
            lines, np.minimum(positions + 3, network.lengths[roads])
        )
        tangent = (
            np.degrees(
                np.arctan2(
                    shapely.get_x(b) - shapely.get_x(a),
                    shapely.get_y(b) - shapely.get_y(a),
                )
            )
            % 360
        )
        delta = angle_delta(result.heading_deg.to_numpy()[rows], tangent)
        axial = np.minimum(delta, 180 - delta)
        distance = shapely.distance(lines, points[rows])
        candidates = pd.DataFrame(
            {
                "row": rows,
                "road": roads,
                "distance": distance,
                "position": positions,
                "axial": axial,
                "direction": np.where(delta <= 90, "f", "r"),
                "cost": distance + axial / 90 * 8,
            }
        )
        candidates = candidates.sort_values(["row", "cost", "road"])
        rank = candidates.groupby("row").cumcount()
        best = candidates.loc[rank.eq(0)].set_index("row")
        second = candidates.loc[rank.eq(1)].set_index("row").cost.reindex(best.index)
        ambiguous = (second - best.cost).lt(network.rules["ambiguity_margin_m"])
        reliable = best.distance.le(network.rules["match_distance_m"]) & best.axial.le(
            network.rules["heading_tolerance_deg"]
        )
        result.loc[best.index, "matched_segment_id"] = np.asarray(network.ids)[
            best.road
        ]
        result.loc[best.index, "match_distance_m"] = best.distance.to_numpy()
        result.loc[best.index, "position_m"] = best.position.to_numpy()
        result.loc[best.index, "observed_direction"] = best.direction.to_numpy()
        result.loc[best.index, "axial_heading_error_deg"] = best.axial.to_numpy()
        result.loc[best.index, "match_score"] = (
            (1 - best.distance / network.rules["review_distance_m"])
            * (1 - best.axial / 90)
        ).to_numpy()
        result.loc[best.index, "match_status"] = np.where(
            reliable, np.where(ambiguous, "ambiguous", "matched"), "needs_review"
        )
    # Kinematic discontinuities cannot support either matched or missing-road evidence.
    groups = result.groupby("trajectory_id", sort=False)
    dt = groups.timestamp.diff().dt.total_seconds()
    dx, dy = groups.x.diff(), groups.y.diff()
    jump = ((dx**2 + dy**2) ** 0.5 / dt * 3.6).gt(network.rules["max_speed_kmh"])
    next_jump = jump.groupby(result.trajectory_id).shift(-1, fill_value=False)
    result.loc[jump | next_jump, "quality_flag"] = "motion_discontinuity"
    result.loc[jump | next_jump, "match_status"] = "quality_excluded"
    return result


PASSAGE_COLUMNS = [
    "trajectory_id",
    "segment_id",
    "direction",
    "edge_id",
    "start_seq",
    "end_seq",
    "start_time",
    "end_time",
    "start_position_m",
    "end_position_m",
    "point_count",
    "distance_m",
    "quality",
    "direction_consistent",
]


def build_passages(matched, network):
    rows = []
    reliable = matched.loc[matched.match_status.eq("matched")].copy()
    if reliable.empty:
        return pd.DataFrame(columns=PASSAGE_COLUMNS)
    changes = (
        reliable.trajectory_id.ne(reliable.trajectory_id.shift())
        | reliable.matched_segment_id.ne(reliable.matched_segment_id.shift())
        | reliable.observed_direction.ne(reliable.observed_direction.shift())
        | reliable.timestamp.diff()
        .dt.total_seconds()
        .gt(network.rules["max_gap_seconds"])
        | reliable.point_seq.diff().gt(4)
    )
    for _, group in reliable.groupby(changes.cumsum(), sort=False):
        first, last = group.iloc[0], group.iloc[-1]
        movement = float(last.position_m - first.position_m)
        consistent = len(group) == 1 or (
            movement > 0 if first.observed_direction == "f" else movement < 0
        )
        rows.append(
            {
                "trajectory_id": first.trajectory_id,
                "segment_id": first.matched_segment_id,
                "direction": first.observed_direction,
                "edge_id": f"{first.matched_segment_id}:{first.observed_direction}",
                "start_seq": int(first.point_seq),
                "end_seq": int(last.point_seq),
                "start_time": first.timestamp,
                "end_time": last.timestamp,
                "start_position_m": float(first.position_m),
                "end_position_m": float(last.position_m),
                "point_count": len(group),
                "distance_m": abs(movement),
                "quality": float(group.match_score.mean()),
                "direction_consistent": bool(consistent),
            }
        )
    return pd.DataFrame(rows, columns=PASSAGE_COLUMNS)


def transitions(passages, network):
    result = []
    for _, group in passages.groupby("trajectory_id", sort=False):
        records = group.to_dict("records")
        for first, second in zip(records, records[1:], strict=False):
            if (
                first["segment_id"] == second["segment_id"]
                or not first["direction_consistent"]
                or not second["direction_consistent"]
            ):
                continue
            delta = (second["start_time"] - first["end_time"]).total_seconds()
            if (
                not 0 < delta <= network.rules["max_gap_seconds"]
                or not 0 < second["start_seq"] - first["end_seq"] <= 4
            ):
                continue
            before_length = network.line_by_id[first["segment_id"]].length
            after_length = network.line_by_id[second["segment_id"]].length
            exit_gap = (
                before_length - first["end_position_m"]
                if first["direction"] == "f"
                else first["end_position_m"]
            )
            entry_gap = (
                second["start_position_m"]
                if second["direction"] == "f"
                else after_length - second["start_position_m"]
            )
            if max(exit_gap, entry_gap) > network.rules["endpoint_distance_m"]:
                continue
            if not network.interior(first["segment_id"]) or not network.interior(
                second["segment_id"]
            ):
                continue
            result.append(
                {
                    "trajectory_id": first["trajectory_id"],
                    "from_edge_id": first["edge_id"],
                    "to_edge_id": second["edge_id"],
                    "from_segment_id": first["segment_id"],
                    "to_segment_id": second["segment_id"],
                    "exit_node_id": network.ends(
                        first["segment_id"], first["direction"]
                    )[1],
                    "entry_node_id": network.ends(
                        second["segment_id"], second["direction"]
                    )[0],
                    "start_seq": first["end_seq"],
                    "end_seq": second["start_seq"],
                    "start_time": first["end_time"],
                    "end_time": second["start_time"],
                    "quality": min(first["quality"], second["quality"]),
                }
            )
    return result
