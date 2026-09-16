"""Small independent graphs establish routing correctness and repair semantics."""

import copy
import json
from collections import Counter
from pathlib import Path

import pytest
from shapely.geometry import LineString

from src.network.normalization import NetworkTables
from src.network.routing import TurnAwareRouter
from src.replay.patches import apply_plan, propose
from src.replay.route_replay import compare, scenarios

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/replay.json").read_bytes())


def network(edges, restrictions=()):
    """Each edge is (ID, from, to, cost metres, direction[, class, speed])."""
    coordinates = {
        "a": (121.43, 31.18),
        "b": (121.431, 31.18),
        "c": (121.432, 31.18),
        "d": (121.431, 31.181),
        "e": (121.431, 31.18005),
        "z": (121.435, 31.185),
    }
    roads = []
    for key, u, v, length, direction, *extra in edges:
        roads.append(
            {
                "segment_id": key,
                "from_node_id": u,
                "to_node_id": v,
                "length_m": length,
                "direction": direction,
                "oneway": direction != "both",
                "road_class": extra[0] if extra else "residential",
                "maxspeed": extra[1] if len(extra) > 1 else None,
                "lanes": None,
                "name": key,
                "geometry_wkt": LineString([coordinates[u], coordinates[v]]).wkt,
                "routing_eligible": True,
                "network_version": "test-v1",
            }
        )
    degrees = Counter(
        node for row in roads for node in (row["from_node_id"], row["to_node_id"])
    )
    nodes = [
        {
            "node_id": key,
            "longitude": xy[0],
            "latitude": xy[1],
            "node_degree": degrees[key],
            "is_boundary": False,
            "routing_eligible": True,
            "network_version": "test-v1",
        }
        for key, xy in coordinates.items()
    ]
    return NetworkTables(roads, nodes, list(restrictions), [])


def restriction(incoming="ab", outgoing="bc", kind="no_straight_on", key="r1"):
    return {
        "restriction_id": key,
        "from_segment_id": incoming,
        "via_node_id": "b",
        "to_segment_id": outgoing,
        "from_edge_id": incoming + ":f",
        "to_edge_id": outgoing + ":f",
        "restriction_type": kind,
        "network_version": "test-v1",
    }


def issue(kind, target, metrics):
    return {
        "issue_id": "issue-test",
        "issue_type": kind,
        "object_id": target,
        "evidence_summary_json": json.dumps({"metrics": metrics}),
        "network_version": "test-v1",
        "run_id": "analysis-test",
    }


def turn_issue(variant="possible_missing_restriction"):
    metrics = {
        key: value
        for key, value in restriction().items()
        if key not in {"restriction_id", "network_version", "restriction_type"}
    }
    return issue(
        "TURN_RESTRICTION_CONFLICT", "turn-test", {**metrics, "variant": variant}
    )


def test_edge_state_search_revisits_node_via_different_incoming_edge():
    tables = network(
        [
            ("ab", "a", "b", 10, "forward"),
            ("ad", "a", "d", 20, "forward"),
            ("db", "d", "b", 20, "forward"),
            ("bc", "b", "c", 10, "forward"),
        ],
        [restriction()],
    )
    router = TurnAwareRouter(tables, CONFIG)
    result = router.route("a", "c")
    assert result["edge_ids"] == ["ad:f", "db:f", "bc:f"]
    assert result["distance_m"] == 50
    assert not router.validate_path("a", "c", ["ab:f", "bc:f"])
    assert router.validate_path("a", "c", result["edge_ids"])


def test_parallel_edges_keep_distinct_turn_constraints():
    tables = network(
        [
            ("ab", "a", "b", 10, "forward"),
            ("alternate", "a", "b", 11, "forward"),
            ("bc", "b", "c", 10, "forward"),
        ],
        [restriction()],
    )
    result = TurnAwareRouter(tables, CONFIG).route("a", "c")
    assert result["edge_ids"] == ["alternate:f", "bc:f"]
    assert result["distance_m"] == 21


def test_only_turn_and_no_turn_are_both_enforced():
    tables = network(
        [
            ("ab", "a", "b", 10, "forward"),
            ("bd", "b", "d", 11, "forward"),
            ("bc", "b", "c", 10, "forward"),
        ],
        [restriction(outgoing="bd", kind="only_left_turn")],
    )
    router = TurnAwareRouter(tables, CONFIG)
    assert router.route("a", "c")["status"] == "no_path"
    assert router.route("a", "d")["status"] == "found"
    tables.turn_restriction.append(
        restriction(outgoing="bd", kind="no_left_turn", key="r2")
    )
    assert TurnAwareRouter(tables, CONFIG).route("a", "d")["status"] == "no_path"


def test_reverse_oneway_geometry_endpoints_and_unavailable_values():
    tables = network([("ab", "a", "b", 10, "reverse")])
    router = TurnAwareRouter(tables, CONFIG)
    result = router.route("b", "a")
    assert result["edge_ids"] == ["ab:r"]
    assert result["geometry_wkt"] == "LINESTRING (121.431 31.18, 121.43 31.18)"
    missing = router.route("a", "b")
    assert (
        missing["status"] == "no_path"
        and missing["distance_m"] is missing["eta_s"] is None
    )
    assert router.route("a", "a")["distance_m"] == 0
    assert router.route("unknown", "a")["status"] == "invalid_endpoint"
    assert router.route("a", "z")["status"] == "no_path"
    tables.road_node[0]["routing_eligible"] = False
    assert (
        TurnAwareRouter(tables, CONFIG).route("a", "a")["status"]
        == "ineligible_endpoint"
    )


def test_uturn_policy_is_explicit_and_cannot_override_a_restriction():
    tables = network([("ab", "a", "b", 10, "both")])
    assert not TurnAwareRouter(tables, CONFIG).turn_allowed("ab:f", "ab:r")
    allowed = {**CONFIG, "allow_immediate_uturn": True}
    assert TurnAwareRouter(tables, allowed).turn_allowed("ab:f", "ab:r")
    row = restriction(outgoing="ab", kind="no_u_turn")
    row["to_edge_id"] = "ab:r"
    tables.turn_restriction.append(row)
    assert not TurnAwareRouter(tables, allowed).turn_allowed("ab:f", "ab:r")


def test_eta_objective_and_speed_cap_and_turn_delay():
    tables = network(
        [
            ("ac", "a", "c", 100, "forward", "service", 5),
            ("ad", "a", "d", 80, "forward", "primary"),
            ("dc", "d", "c", 80, "forward", "primary"),
        ]
    )
    distance = TurnAwareRouter(tables, CONFIG).route("a", "c")
    assert distance["distance_m"] == 100 and distance["eta_s"] == 72
    assert distance["edge_details"][0]["speed_source"] == "maxspeed_capped_model"
    config = {**CONFIG, "objective": "eta"}
    router = TurnAwareRouter(tables, config)
    result = router.route("a", "c")
    assert result["edge_ids"] == ["ad:f", "dc:f"]
    assert result["distance_m"] == 160 and result["eta_s"] == pytest.approx(
        160 / 40 * 3.6 + 5
    )
    config["turn_delay_seconds"] = 1000
    assert router.route("a", "c") == result


@pytest.mark.parametrize(
    "change",
    [
        {"objective": "fastest"},
        {"allow_immediate_uturn": 1},
        {"default_speed_kmh": float("nan")},
        {"metric_crs": "EPSG:4326"},
        {"max_od_pairs_per_issue": 3},
    ],
)
def test_invalid_routing_configuration_fails(change):
    with pytest.raises(ValueError):
        TurnAwareRouter(network([("ab", "a", "b", 10, "both")]), {**CONFIG, **change})


def test_unknown_restriction_semantics_rejected():
    tables = network(
        [("ab", "a", "b", 10, "forward"), ("bc", "b", "c", 10, "forward")],
        [restriction(kind="no_entry")],
    )
    with pytest.raises(ValueError, match="Unsupported"):
        TurnAwareRouter(tables, CONFIG)


def test_direction_repair_can_restore_one_od_and_lose_reverse_control():
    tables = network([("ab", "a", "b", 10, "forward")])
    original = copy.deepcopy(tables)
    source_issue = issue(
        "ONEWAY_DIRECTION_CONFLICT", "ab", {"current_direction": "forward"}
    )
    before = TurnAwareRouter(tables, CONFIG)
    plan = propose(source_issue, tables, before)
    fixed, audit = apply_plan(tables, source_issue, plan, CONFIG)
    after = TurnAwareRouter(fixed, CONFIG)
    results = [
        compare(before, after, plan, scenario) for scenario in scenarios(plan, CONFIG)
    ]
    assert [row["reachability_change"] for row in results] == ["restored", "lost"]
    assert all(row["distance_delta_m"] is row["eta_delta_s"] is None for row in results)
    assert results[1]["before_edge_sequence_legal_after"] is False
    assert (
        audit["before"][0]["direction"] == "forward"
        and audit["after"][0]["direction"] == "reverse"
    )
    assert tables == original and source_issue["network_version"] == "test-v1"
    with pytest.raises(ValueError, match="stale"):
        apply_plan(tables, source_issue, {**plan, "operation": "none"}, CONFIG)
    tables.road_segment[0]["length_m"] += 1
    with pytest.raises(ValueError, match="stale"):
        apply_plan(tables, source_issue, plan, CONFIG)


def test_adding_turn_restriction_can_require_a_longer_legal_route():
    tables = network(
        [
            ("ab", "a", "b", 10, "forward"),
            ("bc", "b", "c", 10, "forward"),
            ("ad", "a", "d", 30, "forward"),
            ("dc", "d", "c", 30, "forward"),
        ]
    )
    source_issue = turn_issue()
    before = TurnAwareRouter(tables, CONFIG)
    plan = propose(source_issue, tables, before)
    fixed, _ = apply_plan(tables, source_issue, plan, CONFIG)
    result = compare(
        before, TurnAwareRouter(fixed, CONFIG), plan, scenarios(plan, CONFIG)[0]
    )
    assert result["distance_delta_m"] == 40 and result["distance_change"] == "longer"
    assert result["before_legal"] and result["after_legal"]
    assert result["before_edge_sequence_legal_after"] is False
    assert result["target_exercised_before"] and not result["target_exercised_after"]
    assert result["verification_status"] == "not_field_verified"
    assert len(tables.turn_restriction) == 0 and len(fixed.turn_restriction) == 1


def test_remove_no_turn_and_defer_only_turn_policy():
    tables = network(
        [
            ("ab", "a", "b", 10, "forward"),
            ("bc", "b", "c", 10, "forward"),
            ("bd", "b", "d", 10, "forward"),
        ],
        [restriction()],
    )
    source_issue = turn_issue("observed_forbidden_turn")
    plan = propose(source_issue, tables, TurnAwareRouter(tables, CONFIG))
    fixed, _ = apply_plan(tables, source_issue, plan, CONFIG)
    assert plan["operation"] == "remove_no_turn" and fixed.turn_restriction == []
    assert TurnAwareRouter(fixed, CONFIG).route("a", "c")["status"] == "found"
    tables.turn_restriction = [restriction(outgoing="bd", kind="only_left_turn")]
    assert (
        propose(source_issue, tables, TurnAwareRouter(tables, CONFIG))["status"]
        == "needs_manual_review"
    )


def test_connectivity_extension_preserves_original_and_recomputes_degrees():
    tables = network([("ae", "a", "e", 100, "both"), ("bc", "b", "c", 100, "both")])
    original = copy.deepcopy(tables)
    source_issue = issue("CONNECTIVITY_BREAK", "e", {"node_pair": ["e", "b"]})
    before = TurnAwareRouter(tables, CONFIG)
    plan = propose(source_issue, tables, before)
    fixed, _ = apply_plan(tables, source_issue, plan, CONFIG)
    assert plan["status"] == "ready" and 0 < plan["payload"]["gap_m"] < 15
    assert fixed.road_segment[0]["to_node_id"] == "b"
    assert (
        next(node for node in fixed.road_node if node["node_id"] == "e")["node_degree"]
        == 0
    )
    assert (
        next(node for node in fixed.road_node if node["node_id"] == "b")["node_degree"]
        == 2
    )
    assert before.route("a", "c")["status"] == "no_path"
    assert TurnAwareRouter(fixed, CONFIG).route("a", "c")["status"] == "found"
    assert tables == original
    source_issue["evidence_summary_json"] = json.dumps(
        {"metrics": {"node_pair": ["e", "z"]}}
    )
    assert propose(source_issue, tables, before)["status"] == "needs_manual_review"


def test_missing_corridor_and_referenced_oneway_are_explicitly_deferred():
    tables = network(
        [("ab", "a", "b", 10, "forward"), ("bc", "b", "c", 10, "forward")],
        [restriction()],
    )
    router = TurnAwareRouter(tables, CONFIG)
    for source_issue in [
        issue("MISSING_OR_CHANGED_ROAD_CANDIDATE", "corridor", {}),
        issue("ONEWAY_DIRECTION_CONFLICT", "ab", {"current_direction": "forward"}),
    ]:
        plan = propose(source_issue, tables, router)
        assert plan["status"] == "needs_manual_review" and scenarios(plan, CONFIG) == []
        with pytest.raises(ValueError):
            apply_plan(tables, source_issue, plan, CONFIG)


def test_before_and_after_parameters_and_versions_must_match():
    tables = network([("ab", "a", "b", 10, "forward")])
    source_issue = issue(
        "ONEWAY_DIRECTION_CONFLICT", "ab", {"current_direction": "forward"}
    )
    before = TurnAwareRouter(tables, CONFIG)
    plan = propose(source_issue, tables, before)
    fixed, _ = apply_plan(tables, source_issue, plan, CONFIG)
    with pytest.raises(ValueError, match="parameters"):
        compare(
            before,
            TurnAwareRouter(fixed, {**CONFIG, "objective": "eta"}),
            plan,
            scenarios(plan, CONFIG)[0],
        )
    with pytest.raises(ValueError, match="versions"):
        compare(before, before, plan, scenarios(plan, CONFIG)[0])
