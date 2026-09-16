"""Repeated continuous off-network corridors, without consulting missing-road labels."""

import math

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString

from src.detectors.common import candidate, enough, stable_id
from src.domain import IssueType
from src.network.observation_matching import angle_delta


def detect_road_change(network, matched, feedback, rules):
    if matched.empty:
        return []
    usable = matched.quality_flag.eq("valid") & matched.match_distance_m.ge(
        rules["missing_distance_m"]
    )
    off = matched.loc[usable].copy()
    if off.empty:
        return []
    breaks = (
        off.trajectory_id.ne(off.trajectory_id.shift())
        | off.point_seq.diff().ne(1)
        | off.timestamp.diff().dt.total_seconds().gt(rules["max_gap_seconds"])
    )
    runs = []
    for _, group in off.groupby(breaks.cumsum(), sort=False):
        if len(group) < rules["corridor_minimum_points"]:
            continue
        first, last = group.iloc[0], group.iloc[-1]
        line = LineString(zip(group.x, group.y, strict=True))
        chord = math.hypot(last.x - first.x, last.y - first.y)
        if chord < rules["corridor_minimum_length_m"] or chord / line.length < 0.7:
            continue
        heading = math.degrees(math.atan2(last.x - first.x, last.y - first.y)) % 360
        if (
            np.max(angle_delta(group.heading_deg.to_numpy(), heading))
            > rules["corridor_heading_tolerance_deg"]
        ):
            continue
        # Avoid drawing candidates at the clipped network perimeter.
        if any(
            point.distance(line) < rules["endpoint_distance_m"]
            for key, point in network.node_points.items()
            if network.nodes[key]["is_boundary"]
        ):
            continue
        runs.append(
            {
                "line": line,
                "heading": heading,
                "trajectory_id": first.trajectory_id,
                "start_seq": int(first.point_seq),
                "end_seq": int(last.point_seq),
                "start_time": first.timestamp,
                "end_time": last.timestamp,
                "quality": min(1.0, chord / line.length),
                "minimum_match_distance_m": float(group.match_distance_m.min()),
            }
        )
    if not runs:
        return []
    lines = np.array([row["line"] for row in runs], dtype=object)
    tree = STRtree(lines)
    parent = list(range(len(runs)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for a, b in tree.query(
        lines, predicate="dwithin", distance=rules["corridor_cluster_distance_m"]
    ).T:
        delta = angle_delta(runs[a]["heading"], runs[b]["heading"])
        if min(delta, 180 - delta) <= rules["corridor_heading_tolerance_deg"]:
            left, right = root(int(a)), root(int(b))
            parent[max(left, right)] = min(left, right)
    grouped = {}
    for index, row in enumerate(runs):
        grouped.setdefault(root(index), []).append(row)
    results = []
    for refs in grouped.values():
        if not enough(refs, rules):
            continue
        representative = max(
            refs,
            key=lambda row: (
                row["line"].length,
                row["trajectory_id"],
                row["start_seq"],
            ),
        )["line"]
        identity = sorted(
            (row["trajectory_id"], row["start_seq"], row["end_seq"]) for row in refs
        )
        reports = network.nearby_feedback(
            feedback, {"road_missing", "route_unreasonable"}, representative
        )
        results.append(
            candidate(
                IssueType.MISSING_OR_CHANGED_ROAD_CANDIDATE,
                "segment",
                stable_id("corridor-", identity),
                representative,
                network,
                refs,
                reports,
                {
                    "corridor_run_count": len(refs),
                    "representative_length_m": representative.length,
                    "minimum_match_distance_m": min(
                        row["minimum_match_distance_m"] for row in refs
                    ),
                    "existing_segment_id": None,
                },
                "Multiple motorcar trajectories form a continuous corridor separated from the current road geometry.",
                "Inspect imagery and access rules; verify a missing road, changed geometry, parallel road or systematic positioning bias.",
                structural=0.8,
            )
        )
    return results
