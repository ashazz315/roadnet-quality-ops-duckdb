"""Same-OD comparisons with explicit reachability and legal-route semantics."""

from itertools import pairwise

from src.replay.patches import canonical, identity


def scenarios(plan, config):
    if plan["status"] != "ready":
        return []
    origin, destination = plan["anchor_od"]
    if origin == destination:
        raise ValueError("Replay anchor must have distinct endpoints")
    return [
        {
            "origin_node_id": a,
            "destination_node_id": b,
            "scenario_role": role,
            "selection_reason": "Issue-adjacent endpoints selected before routing; no outcome filtering",
        }
        for a, b, role in [
            (origin, destination, "anchor"),
            (destination, origin, "reverse_control"),
        ]
    ][: config["max_od_pairs_per_issue"]]


def exercised(route, plan):
    payload = plan["payload"]
    if plan["operation"] in {"add_no_turn", "remove_no_turn"}:
        return (payload["from_edge_id"], payload["to_edge_id"]) in set(
            pairwise(route["edge_ids"])
        )
    return any(
        row["segment_id"] == payload["segment_id"] for row in route["edge_details"]
    )


def compare(before_router, after_router, plan, scenario):
    if before_router.config != after_router.config:
        raise ValueError("Before and after routing parameters must match")
    if (
        before_router.version != plan["base_network_version"]
        or after_router.version != plan["after_network_version"]
    ):
        raise ValueError("Replay network versions do not match the plan")
    a, b = scenario["origin_node_id"], scenario["destination_node_id"]
    before, after = before_router.route(a, b), after_router.route(a, b)
    available_before, available_after = before["legal"] is True, after["legal"] is True
    both = available_before and available_after
    distance_delta = after["distance_m"] - before["distance_m"] if both else None
    eta_delta = after["eta_s"] - before["eta_s"] if both else None
    row = {
        "replay_id": identity(
            "replay-", [plan["patch_id"], scenario, before_router.config]
        ),
        "issue_id": plan["issue_id"],
        "patch_id": plan["patch_id"],
        **scenario,
        "source_analysis_run_id": plan["source_analysis_run_id"],
        "before_network_version": before_router.version,
        "after_network_version": after_router.version,
        "before_status": before["status"],
        "after_status": after["status"],
        "before_distance_m": before["distance_m"],
        "after_distance_m": after["distance_m"],
        "before_eta_s": before["eta_s"],
        "after_eta_s": after["eta_s"],
        "distance_delta_m": distance_delta,
        "eta_delta_s": eta_delta,
        "before_legal": before["legal"],
        "after_legal": after["legal"],
        "before_edge_sequence_legal_after": after_router.validate_path(
            a, b, before["edge_ids"]
        )
        if available_before
        else None,
        "reachability_change": "unchanged_available"
        if both
        else "restored"
        if available_after
        else "lost"
        if available_before
        else "unchanged_unavailable",
        "path_changed": before["edge_ids"] != after["edge_ids"],
        "distance_change": "unavailable"
        if not both
        else "longer"
        if distance_delta > 1e-6
        else "shorter"
        if distance_delta < -1e-6
        else "equal",
        "target_exercised_before": exercised(before, plan),
        "target_exercised_after": exercised(after, plan),
        "before_route_json": canonical(before),
        "after_route_json": canonical(after),
        "is_hypothetical": True,
        "verification_status": "not_field_verified",
    }
    return row
