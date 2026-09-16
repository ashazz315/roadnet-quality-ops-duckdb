"""Nearby disconnected endpoints supported by observed crossing transitions."""

from collections import defaultdict

from shapely.geometry import LineString

from src.detectors.common import candidate, enough
from src.domain import IssueType


def detect_connectivity(network, transitions, feedback, rules):
    grouped = defaultdict(list)
    for row in transitions:
        a, b = row["exit_node_id"], row["entry_node_id"]
        if a == b or any(network.nodes[key]["is_boundary"] for key in (a, b)):
            continue
        degree = [network.nodes[key]["node_degree"] for key in (a, b)]
        if min(degree) != 1 or max(degree) < 2:
            continue
        distance = network.node_points[a].distance(network.node_points[b])
        if (
            not 0.5 < distance <= rules["connectivity_gap_m"]
            or network.graph.has_edge(a, b)
            or network.graph.has_edge(b, a)
        ):
            continue
        grouped[tuple(sorted((a, b)))].append(row)
    results = []
    for pair, refs in sorted(grouped.items()):
        if not enough(refs, rules):
            continue
        target = next(key for key in pair if network.nodes[key]["node_degree"] == 1)
        geometry = LineString([network.node_points[key] for key in pair])
        reports = network.nearby_feedback(
            feedback, {"navigation_interrupted", "route_unreasonable"}, geometry
        )
        results.append(
            candidate(
                IssueType.CONNECTIVITY_BREAK,
                "node",
                target,
                geometry,
                network,
                refs,
                reports,
                {
                    "node_pair": list(pair),
                    "gap_m": geometry.length,
                    "direct_edge_present": False,
                },
                "Repeated trajectories cross between distinct nearby nodes with an unexplained terminal endpoint.",
                "Verify grade separation, barriers and actual access before connecting the node pair.",
            )
        )
    return results
