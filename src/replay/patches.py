"""Conservative hypothetical edits derived only from an issue and current network."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter

from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform

from src.network.routing import TurnAwareRouter
from src.network.validation import validate_network


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def identity(prefix, value):
    return prefix + hashlib.sha256(canonical(value).encode()).hexdigest()[:20]


def network_fingerprint(tables):
    normalized = {}
    for name, key in (
        ("road_segment", "segment_id"),
        ("road_node", "node_id"),
        ("turn_restriction", "restriction_id"),
    ):
        normalized[name] = [
            {
                field: None if isinstance(value, float) and math.isnan(value) else value
                for field, value in row.items()
            }
            for row in sorted(getattr(tables, name), key=lambda row: row[key])
        ]
    return hashlib.sha256(canonical(normalized).encode()).hexdigest()


def propose(issue, tables, router):
    """A ready plan means executable in a model, never field-confirmed."""
    if issue["network_version"] != router.version:
        raise ValueError("Stale issue network version")
    metrics = json.loads(issue["evidence_summary_json"])["metrics"]
    plan = {
        "issue_id": issue["issue_id"],
        "issue_type": issue["issue_type"],
        "source_analysis_run_id": issue["run_id"],
        "base_network_version": router.version,
        "base_network_sha256": network_fingerprint(tables),
        "status": "needs_manual_review",
        "operation": "none",
        "payload": {},
        "anchor_od": [],
        "is_hypothetical": True,
        "verification_status": "not_field_verified",
        "reason": "Unsupported issue hypothesis",
    }
    segments = {row["segment_id"]: row for row in tables.road_segment}
    nodes = {row["node_id"]: row for row in tables.road_node}
    kind = issue["issue_type"]
    if kind == "ONEWAY_DIRECTION_CONFLICT":
        row = segments.get(issue["object_id"])
        if (
            row is None
            or not row["oneway"]
            or row["direction"] != metrics.get("current_direction")
        ):
            raise ValueError("Stale or invalid direction hypothesis")
        if any(
            row["segment_id"] in (r["from_segment_id"], r["to_segment_id"])
            for r in tables.turn_restriction
        ):
            plan["reason"] = (
                "Direction edit requires coordinated turn restriction review"
            )
        elif not row["routing_eligible"]:
            plan["reason"] = "Target segment is not eligible for routing"
        else:
            direction = "reverse" if row["direction"] == "forward" else "forward"
            od = (
                [row["to_node_id"], row["from_node_id"]]
                if direction == "reverse"
                else [row["from_node_id"], row["to_node_id"]]
            )
            plan.update(
                status="ready",
                operation="reverse_oneway",
                payload={
                    "segment_id": row["segment_id"],
                    "expected_direction": row["direction"],
                    "direction": direction,
                },
                anchor_od=od,
                reason="Assume reversed one-way direction; evidence does not establish legal road access",
            )
    elif kind == "CONNECTIVITY_BREAK":
        pair = metrics.get("node_pair", [])
        terminal = issue["object_id"]
        if (
            len(pair) != 2
            or len(set(pair)) != 2
            or terminal not in pair
            or any(key not in nodes for key in pair)
        ):
            raise ValueError("Invalid connectivity node pair")
        other = next(key for key in pair if key != terminal)
        incident = [
            row
            for row in tables.road_segment
            if terminal in (row["from_node_id"], row["to_node_id"])
        ]
        metric = Transformer.from_crs(
            4326, router.config["metric_crs"], always_xy=True
        ).transform
        gap = transform(
            metric, Point(nodes[terminal]["longitude"], nodes[terminal]["latitude"])
        ).distance(
            transform(
                metric, Point(nodes[other]["longitude"], nodes[other]["latitude"])
            )
        )
        if len(incident) != 1 or not 0 < gap <= router.config["max_snap_distance_m"]:
            plan["reason"] = (
                "Endpoint extension requires one incident road and a positive gap within the configured limit"
            )
        elif any(r["via_node_id"] == terminal for r in tables.turn_restriction):
            plan["reason"] = (
                "Endpoint has turn restrictions requiring coordinated review"
            )
        else:
            row = incident[0]
            side = "from_node_id" if row["from_node_id"] == terminal else "to_node_id"
            far = row["to_node_id"] if side == "from_node_id" else row["from_node_id"]
            if (
                far == other
                or not row["routing_eligible"]
                or not nodes[other]["routing_eligible"]
            ):
                plan["reason"] = (
                    "Extension would create a self-loop or use an ineligible object"
                )
            else:
                plan.update(
                    status="ready",
                    operation="extend_endpoint",
                    payload={
                        "segment_id": row["segment_id"],
                        "side": side,
                        "expected_node_id": terminal,
                        "new_node_id": other,
                        "gap_m": gap,
                    },
                    anchor_od=[far, other],
                    reason="Assume nearby endpoints connect at grade; crossing/access must be field-reviewed",
                )
    elif kind == "TURN_RESTRICTION_CONFLICT":
        incoming, outgoing = metrics.get("from_edge_id"), metrics.get("to_edge_id")
        if incoming not in router.edges or outgoing not in router.edges:
            raise ValueError("Turn hypothesis references unavailable directed edges")
        first, second = router.edges[incoming], router.edges[outgoing]
        if (
            first["v"] != second["u"]
            or first["v"] != metrics.get("via_node_id")
            or first["segment_id"] != metrics.get("from_segment_id")
            or second["segment_id"] != metrics.get("to_segment_id")
        ):
            raise ValueError("Turn hypothesis has inconsistent directed references")
        payload = {
            key: metrics[key]
            for key in (
                "from_segment_id",
                "via_node_id",
                "to_segment_id",
                "from_edge_id",
                "to_edge_id",
            )
        }
        od = [first["u"], second["v"]]
        if metrics.get("variant") == "possible_missing_restriction":
            if not router.turn_allowed(incoming, outgoing):
                raise ValueError("Missing restriction hypothesis is already forbidden")
            angle = router.turn_angle(incoming, outgoing)
            turn = (
                "u_turn"
                if abs(angle) >= router.config["uturn_angle_threshold_deg"]
                else "straight_on"
                if abs(angle) < router.config["turn_angle_threshold_deg"]
                else "left_turn"
                if angle > 0
                else "right_turn"
            )
            payload["restriction_type"] = "no_" + turn
            plan.update(
                status="ready",
                operation="add_no_turn",
                payload=payload,
                anchor_od=od,
                reason="Assume this specific observed candidate maneuver is prohibited; feedback alone cannot prove it",
            )
        elif metrics.get("variant") == "observed_forbidden_turn":
            blockers = [
                r
                for r in tables.turn_restriction
                if r["from_edge_id"] == incoming
                and (
                    (
                        r["restriction_type"].startswith("no_")
                        and r["to_edge_id"] == outgoing
                    )
                    or (
                        r["restriction_type"].startswith("only_")
                        and r["to_edge_id"] != outgoing
                    )
                )
            ]
            if not blockers or any(
                r["restriction_type"].startswith("only_") for r in blockers
            ):
                plan["reason"] = (
                    "No removable exact no-turn constraint; only-turn policy needs manual review"
                )
            else:
                payload["restriction_ids"] = sorted(
                    r["restriction_id"] for r in blockers
                )
                plan.update(
                    status="ready",
                    operation="remove_no_turn",
                    payload=payload,
                    anchor_od=od,
                    reason="Assume exact no-turn constraints are stale; observed driving is not legal-access proof",
                )
    elif kind == "MISSING_OR_CHANGED_ROAD_CANDIDATE":
        plan["reason"] = (
            "Corridor lacks verified endpoint connections, direction and access; supply a reviewed road proposal before replay"
        )
    if plan["status"] == "ready" and plan["anchor_od"][0] == plan["anchor_od"][1]:
        plan.update(
            status="needs_manual_review",
            reason="Loop maneuver needs a nontrivial manually reviewed OD",
        )
    stable = {
        key: value for key, value in plan.items() if key != "source_analysis_run_id"
    }
    plan["patch_id"] = identity("patch-", stable)
    plan["after_network_version"] = (
        identity("hypothesis-", [stable, router.config])
        if plan["status"] == "ready"
        else None
    )
    return plan


def apply_plan(tables, issue, plan, config):
    """Re-derive preconditions from the current snapshot; apply to a deep copy only."""
    router = TurnAwareRouter(tables, config)
    if plan != propose(issue, tables, router) or plan["status"] != "ready":
        raise ValueError("Plan is stale, modified or not executable")
    fixed = copy.deepcopy(tables)
    payload, operation = plan["payload"], plan["operation"]
    before, after = [], []
    if operation in {"reverse_oneway", "extend_endpoint"}:
        row = next(
            r for r in fixed.road_segment if r["segment_id"] == payload["segment_id"]
        )
        before = [copy.deepcopy(row)]
        if operation == "reverse_oneway":
            row["direction"] = payload["direction"]
        else:
            node = next(
                r for r in fixed.road_node if r["node_id"] == payload["new_node_id"]
            )
            coords = list(wkt.loads(row["geometry_wkt"]).coords)
            point = (node["longitude"], node["latitude"])
            coords = (
                [point] + coords
                if payload["side"] == "from_node_id"
                else coords + [point]
            )
            geometry = LineString(coords)
            row[payload["side"]] = payload["new_node_id"]
            row["geometry_wkt"] = geometry.wkt
            row["length_m"] = transform(
                Transformer.from_crs(
                    4326, config["metric_crs"], always_xy=True
                ).transform,
                geometry,
            ).length
        after = [row]
    elif operation == "add_no_turn":
        row = {
            **payload,
            "restriction_id": identity("restriction-", plan["patch_id"]),
            "network_version": plan["after_network_version"],
        }
        fixed.turn_restriction.append(row)
        after = [row]
    elif operation == "remove_no_turn":
        before = [
            r
            for r in fixed.turn_restriction
            if r["restriction_id"] in payload["restriction_ids"]
        ]
        fixed.turn_restriction = [
            r
            for r in fixed.turn_restriction
            if r["restriction_id"] not in payload["restriction_ids"]
        ]
    else:
        raise ValueError("Unsupported patch operation")
    degrees = Counter(
        node
        for row in fixed.road_segment
        for node in (row["from_node_id"], row["to_node_id"])
    )
    for node in fixed.road_node:
        node["node_degree"] = degrees[node["node_id"]]
    for rows in (fixed.road_segment, fixed.road_node, fixed.turn_restriction):
        for row in rows:
            row["network_version"] = plan["after_network_version"]
    validate_network(fixed)
    return fixed, {
        "patch_id": plan["patch_id"],
        "issue_id": issue["issue_id"],
        "operation": operation,
        "before": before,
        "after": after,
        "derived_changes": "All network versions replaced; node degrees recomputed; isolated source endpoint retained",
    }
