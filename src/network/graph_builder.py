"""Build a directed multigraph while preserving parallel roads and OSM direction.

Turn constraints are attached as data, NOT enforced by NetworkX shortest paths.
Restriction-aware routing is deliberately reserved for STEP 7.
"""

from __future__ import annotations

import networkx as nx

from src.network.normalization import NetworkTables, directed_edges
from src.network.validation import validate_network


def build_graph(tables: NetworkTables) -> nx.MultiDiGraph:
    validate_network(tables)
    graph = nx.MultiDiGraph()
    for node in tables.road_node:
        graph.add_node(node["node_id"], **node)
    for segment in tables.road_segment:
        if not segment["routing_eligible"]:
            continue
        if any(not graph.nodes[node_id]["routing_eligible"] for node_id in (segment["from_node_id"], segment["to_node_id"])):
            continue
        for suffix, u, v in directed_edges(segment):
            edge_id = f"{segment['segment_id']}:{suffix}"
            graph.add_edge(u, v, key=edge_id, edge_id=edge_id, **segment)
    graph.graph.update(
        network_version=tables.road_segment[0]["network_version"],
        turn_restrictions=[dict(row) for row in tables.turn_restriction],
        turn_restrictions_enforced=False,
        routing_profile="motorcar_static_conservative",
    )
    return graph


def graph_summary(graph: nx.MultiDiGraph) -> dict:
    return {
        "nodes": graph.number_of_nodes(), "directed_edges": graph.number_of_edges(),
        "weak_components": nx.number_weakly_connected_components(graph),
        "isolated_nodes": len(list(nx.isolates(graph))),
        "turn_restrictions_enforced": False,
    }
