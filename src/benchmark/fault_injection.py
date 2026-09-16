"""Known controlled faults on a copy of Golden; never used by detectors."""

from __future__ import annotations

import json
import random
from collections import Counter
from copy import deepcopy

import networkx as nx
from pyproj import Transformer
from shapely import wkt
from shapely.ops import substring, transform

from src.benchmark.movement import MovementModel
from src.domain import IssueType
from src.network.normalization import NetworkTables
from src.network.validation import validate_network


def select_faults(
    golden: NetworkTables, model: MovementModel, config: dict, rng: random.Random
) -> list[dict]:
    """Choose disjoint road targets and observable, nonredundant turn removals."""
    count = config["faults_per_type"]
    selected = []
    restriction_candidates = []
    for row in golden.turn_restriction:
        incoming = row["from_edge_id"]
        if incoming not in model.viable:
            continue
        possible = model.outgoing[model.edges[incoming]["v"]]
        if any(
            model.allowed(incoming, edge, ignore_restriction=row["restriction_id"])
            and not model.allowed(incoming, edge)
            for edge in possible
        ):
            restriction_candidates.append(row)
    restriction_candidates.sort(key=lambda row: row["restriction_id"])
    rng.shuffle(restriction_candidates)
    used_incoming = set()
    for row in restriction_candidates:
        if row["from_edge_id"] in used_incoming:
            continue
        used_incoming.add(row["from_edge_id"])
        selected.append(
            {
                "issue_type": IssueType.TURN_RESTRICTION_CONFLICT.value,
                "target": row["restriction_id"],
                "anchor": row["from_edge_id"],
            }
        )
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError(
            f"Not enough nonredundant, supported turn faults: need {count}, found {len(selected)}"
        )

    protected_segments = {
        row[key]
        for row in golden.turn_restriction
        for key in ("from_segment_id", "to_segment_id")
    }
    protected_nodes = {row["via_node_id"] for row in golden.turn_restriction}
    nodes = {row["node_id"]: row for row in golden.road_node}
    segments = {row["segment_id"]: row for row in golden.road_segment}
    candidates = [
        key
        for key in sorted(segments)
        if key not in protected_segments
        and segments[key]["length_m"] >= config["minimum_target_length_m"]
        and not {segments[key]["from_node_id"], segments[key]["to_node_id"]}
        & protected_nodes
        and any(model.edges[edge]["segment_id"] == key for edge in model.viable)
    ]
    used_nodes = set()
    for issue_type in (
        IssueType.ONEWAY_DIRECTION_CONFLICT,
        IssueType.CONNECTIVITY_BREAK,
        IssueType.MISSING_OR_CHANGED_ROAD_CANDIDATE,
    ):
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        chosen = 0
        for key in shuffled:
            row = segments[key]
            ends = {row["from_node_id"], row["to_node_id"]}
            if ends & used_nodes or row["from_node_id"] == row["to_node_id"]:
                continue
            if issue_type == IssueType.ONEWAY_DIRECTION_CONFLICT and not row["oneway"]:
                continue
            if (
                issue_type == IssueType.CONNECTIVITY_BREAK
                and nodes[row["to_node_id"]]["node_degree"] < 3
            ):
                continue
            if issue_type == IssueType.MISSING_OR_CHANGED_ROAD_CANDIDATE:
                if any(nodes[node]["node_degree"] < 2 for node in ends):
                    continue
                alternative = model.graph.to_undirected()
                for u, v, edge_key, data in list(
                    alternative.edges(keys=True, data=True)
                ):
                    if data["segment_id"] == key:
                        alternative.remove_edge(u, v, edge_key)
                if not nx.has_path(alternative, row["from_node_id"], row["to_node_id"]):
                    continue
            anchor = next(
                edge for edge in model.viable if model.edges[edge]["segment_id"] == key
            )
            selected.append(
                {"issue_type": issue_type.value, "target": key, "anchor": anchor}
            )
            used_nodes.update(ends)
            chosen += 1
            if chosen == count:
                break
        if chosen != count:
            raise ValueError(
                f"Not enough disjoint candidates for {issue_type}: {chosen}/{count}"
            )
    return selected


def inject_faults(
    golden: NetworkTables, selections: list[dict], config: dict, network_version: str
) -> tuple[NetworkTables, list[dict]]:
    corrupted = deepcopy(golden)
    segments = {row["segment_id"]: row for row in corrupted.road_segment}
    nodes = {row["node_id"]: row for row in corrupted.road_node}
    restrictions = {row["restriction_id"]: row for row in corrupted.turn_restriction}
    to_metric = Transformer.from_crs(
        4326, config["metric_crs"], always_xy=True
    ).transform
    to_wgs = Transformer.from_crs(config["metric_crs"], 4326, always_xy=True).transform
    faults = []
    for index, selection in enumerate(selections):
        fault = {
            **selection,
            "fault_id": f"F{index + 1:03}",
            "seed": config["seed"],
            "expected_issue_type": selection["issue_type"],
        }
        kind = IssueType(selection["issue_type"])
        target = selection["target"]
        if kind == IssueType.TURN_RESTRICTION_CONFLICT:
            before = deepcopy(restrictions.pop(target))
            fault.update(
                before=before,
                after=None,
                operation="remove_turn_restriction",
                target_object_type="turn",
            )
        else:
            row = segments[target]
            before = deepcopy(row)
            if kind == IssueType.ONEWAY_DIRECTION_CONFLICT:
                row["direction"] = (
                    "reverse" if row["direction"] == "forward" else "forward"
                )
                row["oneway"] = True
                tags = json.loads(row["tags_json"])
                for key in list(tags):
                    if key.startswith("oneway:"):
                        del tags[key]
                tags["oneway"] = "yes" if row["direction"] == "forward" else "-1"
                row["tags_json"] = json.dumps(
                    tags, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                )
                operation = "reverse_oneway"
            elif kind == IssueType.CONNECTIVITY_BREAK:
                original_node = nodes[row["to_node_id"]]
                metric = transform(to_metric, wkt.loads(row["geometry_wkt"]))
                if metric.length <= config["connectivity_gap_m"] * 2:
                    raise ValueError(
                        "Road too short for the requested connectivity gap"
                    )
                trimmed = substring(
                    metric, 0, metric.length - config["connectivity_gap_m"]
                )
                geometry = transform(to_wgs, trimmed)
                clone = deepcopy(original_node)
                clone.update(
                    node_id=f"split:{index}",
                    osm_node_id=None,
                    longitude=geometry.coords[-1][0],
                    latitude=geometry.coords[-1][1],
                    is_boundary=False,
                )
                nodes[clone["node_id"]] = clone
                row.update(
                    to_node_id=clone["node_id"],
                    geometry_wkt=geometry.wkt,
                    length_m=round(trimmed.length, 6),
                )
                fault["added_node"] = deepcopy(clone)
                fault["original_node"] = deepcopy(original_node)
                operation = "split_endpoint_with_gap"
            else:
                del segments[target]
                operation = "remove_road_segment"
            fault.update(
                before=before,
                after=deepcopy(segments.get(target)),
                operation=operation,
                target_object_type="node"
                if kind == IssueType.CONNECTIVITY_BREAK
                else "segment",
            )
        faults.append(fault)
    degree = Counter(
        node_id
        for row in segments.values()
        for node_id in (row["from_node_id"], row["to_node_id"])
    )
    for node in nodes.values():
        node["node_degree"] = degree[node["node_id"]]
    for rows in (segments.values(), nodes.values(), restrictions.values()):
        for row in rows:
            row["network_version"] = network_version
    for fault in faults:
        if fault["target_object_type"] != "turn":
            fault["after"] = deepcopy(segments.get(fault["target"]))
        if "added_node" in fault:
            fault["added_node"] = deepcopy(nodes[fault["added_node"]["node_id"]])
    corrupted = NetworkTables(
        sorted(segments.values(), key=lambda row: row["segment_id"]),
        sorted(nodes.values(), key=lambda row: row["node_id"]),
        sorted(restrictions.values(), key=lambda row: row["restriction_id"]),
        [],
    )
    validate_network(corrupted)
    return corrupted, faults
