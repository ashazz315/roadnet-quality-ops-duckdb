"""Repeated movement opposite to a current one-way attribute."""

from src.detectors.common import candidate, enough
from src.domain import IssueType


def detect_oneway(network, passages, feedback, rules):
    results = []
    valid = passages.loc[
        passages.direction_consistent.eq(True)
        & passages.point_count.ge(rules["minimum_passage_points"])
        & passages.distance_m.ge(rules["minimum_passage_distance_m"])
    ]
    for segment, group in valid.groupby("segment_id", sort=True):
        road = network.roads[segment]
        if (
            not road["oneway"]
            or road["road_class"] in {"service", "living_street"}
            or not network.interior(segment)
        ):
            continue
        legal = "f" if road["direction"] == "forward" else "r"
        directions = group.groupby("trajectory_id").direction.agg(set)
        supporters = set(
            directions.index[
                directions.map(
                    lambda values, allowed=legal: values == ({"f", "r"} - {allowed})
                )
            ]
        )
        ratio = len(supporters) / len(directions)
        refs = group.loc[group.trajectory_id.isin(supporters)].to_dict("records")
        if ratio < rules["oneway_reverse_ratio"] or not enough(refs, rules):
            continue
        geometry = network.line_by_id[segment]
        reports = network.nearby_feedback(
            feedback, {"direction_wrong", "route_unreasonable"}, geometry
        )
        results.append(
            candidate(
                IssueType.ONEWAY_DIRECTION_CONFLICT,
                "segment",
                segment,
                geometry,
                network,
                refs,
                reports,
                {
                    "reverse_trajectory_count": len(supporters),
                    "eligible_trajectory_count": len(directions),
                    "reverse_ratio": ratio,
                    "current_direction": road["direction"],
                },
                "Repeated motorcar movement conflicts with the current one-way attribute.",
                "Check signed driving direction, road level and vehicle access before changing the attribute.",
                contradiction=1 - ratio,
            )
        )
    return results
