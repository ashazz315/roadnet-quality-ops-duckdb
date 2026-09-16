"""Configured potential consequences; no measured routes or invented ETA savings."""

from src.domain import IssueType, Severity

SCENARIOS = {"navigation", "routing", "eta", "dispatch", "poi_local_service"}
REASONS = {
    "navigation": "May produce guidance inconsistent with actual motorcar passage or access.",
    "routing": "May change route legality, connectivity or candidate route choice.",
    "eta": "May change distance and therefore modelled travel time; no ETA delta is measured.",
    "dispatch": "May affect assignment feasibility or modelled arrival time; no dispatch simulation is run.",
    "poi_local_service": "May affect road access near destinations; no POI inventory or accessibility change is measured.",
}


def validate_impact_rules(mapping):
    if set(mapping) != {kind.value for kind in IssueType}:
        raise ValueError("Business mapping must cover the four issue types")
    for scenarios in mapping.values():
        if set(scenarios) != SCENARIOS or any(
            level
            not in {Severity.LOW.value, Severity.MEDIUM.value, Severity.HIGH.value}
            for level in scenarios.values()
        ):
            raise ValueError(
                "STEP 6 potential impacts must cover five scenarios without unverified CRITICAL claims"
            )


def assess_impact(issue_type, support_count, mapping):
    rows = []
    for scenario, level in sorted(mapping[issue_type].items()):
        rows.append(
            {
                "business_scenario": scenario,
                "impact_level": level,
                "impact_reason": REASONS[scenario],
                "assessment_kind": "potential_not_replayed",
                "observed_supporting_trajectories": support_count,
                "measured_distance_delta_m": None,
                "measured_eta_delta_s": None,
                "no_path_verified": False,
            }
        )
    rank = {Severity.LOW.value: 0, Severity.MEDIUM.value: 1, Severity.HIGH.value: 2}
    return max((row["impact_level"] for row in rows), key=rank.get), rows
