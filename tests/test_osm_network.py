"""Hand-built synthetic OSM fixtures test topology, never serve as demo data."""

from copy import deepcopy

import pytest
from shapely import wkt

from src.network.graph_builder import build_graph
from src.network.normalization import normalize_osm
from src.network.validation import validate_network


def node(identifier, x, y, **tags):
    return {"type": "node", "id": identifier, "lon": 121.43 + x * .001, "lat": 31.18 + y * .001, "tags": tags}


def way(identifier, refs, **tags):
    return {"type": "way", "id": identifier, "nodes": refs, "tags": {"highway": "residential", **tags}}


def relation(identifier=30, restriction="no_left_turn", **tags):
    return {"type": "relation", "id": identifier, "members": [
        {"type": "way", "ref": 10, "role": "from"},
        {"type": "node", "ref": 2, "role": "via"},
        {"type": "way", "ref": 20, "role": "to"},
    ], "tags": {"type": "restriction", "restriction": restriction, **tags}}


@pytest.fixture
def config():
    return {"bbox": [121.4295, 31.1795, 121.4325, 31.1825], "metric_crs": "EPSG:32651", "highway_classes": ["residential", "motorway"]}


def normalize(elements, config):
    tables = normalize_osm({"elements": elements}, config, "test-network")
    validate_network(tables)
    return tables


def test_shared_osm_node_splits_way_without_losing_shape(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 2, 0), node(4, 1, 1), way(10, [1, 2, 3]), way(20, [2, 4])], config)
    assert len(tables.road_segment) == 3
    assert next(row for row in tables.road_node if row["node_id"] == "osm:n2")["node_degree"] == 3
    assert all(50 < row["length_m"] < 150 for row in tables.road_segment)


def test_geometric_crossing_with_distinct_ids_does_not_connect(config):
    tables = normalize([node(1, 0, 1), node(2, 2, 1), node(3, 1, 0), node(4, 1, 2), way(10, [1, 2], bridge="yes"), way(20, [3, 4])], config)
    assert len(tables.road_node) == 4
    graph = build_graph(tables)
    assert not graph.has_edge("osm:n1", "osm:n3")
    assert len(tables.road_segment) == 2


@pytest.mark.parametrize(("tags", "direction", "edges"), [
    ({"oneway": "yes"}, "forward", {(1, 2)}),
    ({"oneway": "yes", "oneway:bicycle": "no"}, "forward", {(1, 2)}),
    ({"oneway": "yes", "oneway:motorcar": "no"}, "both", {(1, 2), (2, 1)}),
    ({"oneway": "-1"}, "reverse", {(2, 1)}),
    ({"oneway": "no", "junction": "roundabout"}, "both", {(1, 2), (2, 1)}),
    ({"junction": "roundabout"}, "forward", {(1, 2)}),
    ({"highway": "motorway"}, "forward", {(1, 2)}),
    ({}, "both", {(1, 2), (2, 1)}),
])
def test_direction_and_roundabout_defaults(config, tags, direction, edges):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), way(10, [1, 2], **tags)], config)
    assert tables.road_segment[0]["direction"] == direction
    graph = build_graph(tables)
    assert set(graph.edges()) == {(f"osm:n{u}", f"osm:n{v}") for u, v in edges}


def test_clipping_marks_boundary_nodes_and_never_fills_missing_geometry(config):
    tables = normalize([node(1, -2, 0), node(2, 0, 0), node(3, 4, 0), way(10, [1, 2, 3])], config)
    assert len(tables.road_segment) == 1
    assert all(row["is_boundary"] for row in tables.road_node)
    geometry = wkt.loads(tables.road_segment[0]["geometry_wkt"])
    assert geometry.bounds[0] == pytest.approx(config["bbox"][0])
    assert geometry.bounds[2] == pytest.approx(config["bbox"][2])


def test_parallel_roads_and_closed_ring_are_preserved(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 1, 1), way(10, [1, 2]), way(20, [1, 2]), way(40, [2, 3, 2], oneway="yes")], config)
    graph = build_graph(tables)
    assert graph.number_of_edges("osm:n1", "osm:n2") == 2
    assert any(row["from_node_id"] == row["to_node_id"] for row in tables.road_segment)


def test_speed_units_unknown_lanes_and_restricted_access_are_audited(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), way(10, [1, 2], maxspeed="30 mph", lanes="2;3"), way(20, [1, 2], access="private"), way(30, [1, 999])], config)
    assert tables.road_segment[0]["maxspeed"] == pytest.approx(48.28032)
    assert tables.road_segment[0]["lanes"] is None
    assert {row["reason"] for row in tables.audit} >= {"restricted_or_unknown_motorcar_access", "missing_or_invalid_osm_node"}


def test_conditional_roads_and_barrier_nodes_not_automatically_routable(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0, barrier="gate"), node(3, 2, 0), way(10, [1, 2]), way(20, [2, 3], **{"oneway:conditional": "yes @ (08:00-10:00)"})], config)
    assert build_graph(tables).number_of_edges() == 0
    assert len(tables.road_segment) == 2


def test_reversed_way_turn_maps_to_incoming_directed_edge(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 1, 1), way(10, [2, 1], oneway="-1"), way(20, [2, 3], oneway="yes"), relation()], config)
    turn = tables.turn_restriction[0]
    assert turn["from_edge_id"].endswith(":r")
    assert turn["to_edge_id"].endswith(":f")
    graph = build_graph(tables)
    assert graph.graph["turn_restrictions"] == tables.turn_restriction
    assert graph.graph["turn_restrictions_enforced"] is False


@pytest.mark.parametrize("restriction", ["no_left_turn", "only_right_turn"])
def test_supported_turn_types_remain_distinct(config, restriction):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 1, 1), way(10, [1, 2]), way(20, [2, 3]), relation(restriction=restriction)], config)
    assert tables.turn_restriction[0]["restriction_type"] == restriction


@pytest.mark.parametrize(("alteration", "reason"), [
    ("via_way", "via_way_or_invalid_member_type"),
    ("conditional", "conditional_restriction_not_supported"),
    ("except", "restriction_exempts_motorcar"),
    ("wrong_direction", "missing_clipped_excluded_or_wrong_direction_member"),
])
def test_unsupported_turns_are_audited_not_silently_accepted(config, alteration, reason):
    rel = relation()
    incoming = way(10, [1, 2])
    if alteration == "via_way":
        rel["members"][1]["type"] = "way"
    elif alteration == "conditional":
        rel["tags"]["restriction:conditional"] = "no_left_turn @ (Mo-Fr)"
    elif alteration == "except":
        rel["tags"]["except"] = "bus;motorcar"
    else:
        incoming["tags"]["oneway"] = "-1"
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 1, 1), incoming, way(20, [2, 3]), rel], config)
    assert tables.turn_restriction == []
    assert any(row["reason"] == reason for row in tables.audit)


def test_ambiguous_turn_is_not_guessed(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), node(3, 2, 0), node(4, 1, 1), way(10, [1, 2, 3]), way(20, [2, 4]), relation()], config)
    assert tables.turn_restriction == []
    assert any(row["reason"] == "ambiguous_segment_mapping" for row in tables.audit)


def test_normalization_is_independent_of_input_element_order(config):
    elements = [node(1, 0, 0), node(2, 1, 0), way(10, [1, 2])]
    assert normalize(elements, config) == normalize(list(reversed(elements)), config)


def test_u_turn_restriction_keeps_the_reverse_edge_on_the_same_segment(config):
    rel = relation(restriction="no_u_turn")
    rel["members"][2]["ref"] = 10
    tables = normalize([node(1, 0, 0), node(2, 1, 0), way(10, [1, 2]), rel], config)
    turn = tables.turn_restriction[0]
    assert turn["from_segment_id"] == turn["to_segment_id"]
    assert turn["from_edge_id"] != turn["to_edge_id"]


def test_boundary_out_and_back_keeps_both_traversals(config):
    tables = normalize([node(1, 0, 0), node(2, 4, 0), way(10, [1, 2, 1])], config)
    assert len(tables.road_segment) == 2
    assert tables.road_segment[0]["from_node_id"] == "osm:n1"
    assert tables.road_segment[1]["to_node_id"] == "osm:n1"
    assert tables.road_segment[0]["length_m"] == pytest.approx(tables.road_segment[1]["length_m"])


def test_duplicate_osm_ids_and_partial_responses_fail(config):
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_osm({"elements": [node(1, 0, 0), node(1, 1, 0)]}, config, "v1")
    with pytest.raises(ValueError, match="Partial"):
        normalize_osm({"remark": "timeout", "elements": []}, config, "v1")


def test_validation_rejects_dangling_nodes_and_mixed_versions(config):
    tables = normalize([node(1, 0, 0), node(2, 1, 0), way(10, [1, 2])], config)
    broken = deepcopy(tables)
    broken.road_segment[0]["from_node_id"] = "missing"
    with pytest.raises(ValueError, match="dangling"):
        validate_network(broken)
    tables.road_node[0]["network_version"] = "different"
    with pytest.raises(ValueError, match="one nonempty version"):
        validate_network(tables)
