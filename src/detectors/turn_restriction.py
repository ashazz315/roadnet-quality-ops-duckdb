"""Conservative maneuver candidates; an unused turn alone never triggers a report."""

from collections import defaultdict

from src.detectors.common import candidate, enough, stable_id
from src.domain import IssueType


def detect_turns(network, transitions, feedback, rules):
    approaches = defaultdict(list)
    for row in transitions:
        if (
            row["exit_node_id"] == row["entry_node_id"]
            and row["from_edge_id"] in network.edges
            and row["to_edge_id"] in network.edges
        ):
            approaches[row["from_edge_id"]].append(row)
    results = []
    for incoming, observations in sorted(approaches.items()):
        via = network.edges[incoming][1]
        geometry = network.node_points[via]
        reports = network.nearby_feedback(feedback, {"turn_not_allowed"}, geometry)
        opportunities = len({row["trajectory_id"] for row in observations})
        if opportunities < rules["turn_minimum_opportunities"]:
            continue
        outgoing = sorted(
            key
            for _, _, key in network.graph.out_edges(via, keys=True)
            if network.edges[key][2] != network.edges[incoming][2]
            and network.interior(network.edges[key][2])
        )
        for target in outgoing:
            taken = [row for row in observations if row["to_edge_id"] == target]
            rate = len({row["trajectory_id"] for row in taken}) / opportunities
            allowed = network.allowed(incoming, target)
            if (
                not allowed
                and rate >= rules["forbidden_turn_min_ratio"]
                and enough(taken, rules)
            ):
                refs, variant, contradiction, cap = (
                    taken,
                    "observed_forbidden_turn",
                    1 - rate,
                    1.0,
                )
                hypothesis = "Repeated observed maneuvers conflict with a current static turn restriction."
            elif (
                allowed
                and reports
                and len(outgoing) > 1
                and rate <= rules["missing_turn_max_ratio"]
                and enough(observations, rules, rules["turn_minimum_opportunities"])
            ):
                refs, variant, contradiction, cap = (
                    observations,
                    "possible_missing_restriction",
                    rate,
                    rules["missing_turn_confidence_cap"],
                )
                hypothesis = "An allowed maneuver is rarely observed and nearby feedback reports a prohibited turn; the complaint does not identify the exact maneuver."
            else:
                continue
            turn = {
                "from_segment_id": network.edges[incoming][2],
                "via_node_id": via,
                "to_segment_id": network.edges[target][2],
                "from_edge_id": incoming,
                "to_edge_id": target,
            }
            results.append(
                candidate(
                    IssueType.TURN_RESTRICTION_CONFLICT,
                    "turn",
                    stable_id("turn-", turn),
                    geometry,
                    network,
                    refs,
                    reports,
                    {
                        **turn,
                        "variant": variant,
                        "opportunity_trajectories": opportunities,
                        "observed_turn_trajectories": len(
                            {row["trajectory_id"] for row in taken}
                        ),
                        "observed_turn_ratio": rate,
                        "currently_allowed": allowed,
                    },
                    hypothesis,
                    "Inspect signs, access exceptions, time conditions and the exact from/via/to maneuver; absence of demand is an alternative explanation.",
                    structural=0.5 if allowed else 1.0,
                    contradiction=contradiction,
                    score_cap=cap,
                )
            )
    return results
