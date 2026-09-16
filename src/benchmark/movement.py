"""Constraint-aware random walks for simulation, not an OD routing API."""

from __future__ import annotations

import random
from collections import defaultdict
from itertools import pairwise

from src.network.graph_builder import build_graph
from src.network.normalization import NetworkTables


class MovementModel:
    """Keep edge identity through turns; never call unconstrained shortest_path."""

    def __init__(self, tables: NetworkTables):
        self.graph = build_graph(tables)
        self.edges = {}
        self.outgoing = defaultdict(list)
        self.incoming = defaultdict(list)
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            if self.graph.nodes[u]["is_boundary"] or self.graph.nodes[v]["is_boundary"]:
                continue
            self.edges[key] = {**data, "u": u, "v": v}
            self.outgoing[u].append(key)
            self.incoming[v].append(key)
        self.restrictions = defaultdict(list)
        for row in tables.turn_restriction:
            self.restrictions[row["from_edge_id"]].append(row)
        self.next_edges = {
            key: tuple(
                sorted(
                    candidate
                    for candidate in self.outgoing[edge["v"]]
                    if self.allowed(key, candidate)
                )
            )
            for key, edge in sorted(self.edges.items())
        }
        previous = defaultdict(list)
        for key, successors in self.next_edges.items():
            for successor in successors:
                previous[successor].append(key)
        self.previous_edges = {key: tuple(sorted(previous[key])) for key in self.edges}
        self.viable = tuple(
            sorted(
                key
                for key in self.edges
                if self.next_edges[key] and self.previous_edges[key]
            )
        )

    def allowed(
        self, incoming: str, outgoing: str, *, ignore_restriction: str | None = None
    ) -> bool:
        if incoming not in self.edges or outgoing not in self.edges:
            return False
        first, second = self.edges[incoming], self.edges[outgoing]
        if first["v"] != second["u"]:
            return False
        # Conservative simulation policy: no immediate reverse on the same road.
        if first["segment_id"] == second["segment_id"]:
            return False
        for restriction in self.restrictions[incoming]:
            if restriction["restriction_id"] == ignore_restriction:
                continue
            same_target = outgoing == restriction["to_edge_id"]
            if restriction["restriction_type"].startswith("no_") and same_target:
                return False
            if restriction["restriction_type"].startswith("only_") and not same_target:
                return False
        return True

    def walk(
        self, anchor: str, rng: random.Random, edge_range: tuple[int, int]
    ) -> list[str]:
        if anchor not in self.viable:
            raise ValueError(
                "Simulation anchor has no legal incoming/outgoing movement"
            )
        desired = rng.randint(*edge_range)
        path = [anchor]
        for _ in range(rng.randint(2, 4)):
            candidates = self.previous_edges[path[0]]
            if not candidates:
                break
            path.insert(0, rng.choice(candidates))
        # Always include an outgoing transition at the anchor, even for short prefixes.
        while len(path) < desired:
            candidates = self.next_edges[path[-1]]
            if not candidates:
                break
            fresh = [key for key in candidates if key not in path[-5:]]
            path.append(rng.choice(fresh or candidates))
        if path.index(anchor) == len(path) - 1:
            path.append(rng.choice(self.next_edges[anchor]))
        self.validate_path(path)
        return path

    def validate_path(self, path: list[str]) -> None:
        if not path or any(key not in self.edges for key in path):
            raise ValueError("Path contains an unavailable Golden edge")
        for incoming, outgoing in pairwise(path):
            if not self.allowed(incoming, outgoing):
                raise ValueError(
                    f"Path violates direction/turn constraints: {incoming} -> {outgoing}"
                )
