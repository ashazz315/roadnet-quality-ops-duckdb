"""Deterministic, type-aware one-to-one fault matching; no accuracy targets."""

import math

from pyproj import CRS, Transformer
from shapely import wkt
from shapely.ops import transform

TYPES = (
    "CONNECTIVITY_BREAK",
    "TURN_RESTRICTION_CONFLICT",
    "ONEWAY_DIRECTION_CONFLICT",
    "MISSING_OR_CHANGED_ROAD_CANDIDATE",
)
ALGORITHM_VERSION = "fault-object-matching-v1"


def validate_rules(rules):
    expected = {
        "metric_crs",
        "missing_buffer_m",
        "minimum_fault_coverage",
        "minimum_candidate_coverage",
        "matching_warning_threshold",
        "low_confidence_threshold",
    }
    if set(rules) != expected:
        raise ValueError("Unexpected evaluation rule fields")
    crs = CRS(rules["metric_crs"])
    if not crs.is_projected or any(a.unit_name != "metre" for a in crs.axis_info):
        raise ValueError("Evaluation requires a projected metre CRS")
    for key in expected - {"metric_crs"}:
        value = rules[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
        ):
            raise ValueError(f"Invalid evaluation threshold: {key}")
        if not 0 < value <= (100 if key == "missing_buffer_m" else 1):
            raise ValueError(f"Invalid evaluation threshold: {key}")


def counts(tp, fp, fn):
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": None,
        "fpr": None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def maximum_assignment(adjacency):
    """Sorted augmenting paths maximize match count, independent of input order.

    No confidence threshold or score affects TP credit; duplicate alerts remain FP.
    """
    owners = {}

    def visit(issue, seen):
        for fault in sorted(adjacency[issue]):
            if fault in seen:
                continue
            seen.add(fault)
            if fault not in owners or visit(owners[fault], seen):
                owners[fault] = issue
                return True
        return False

    for issue in sorted(adjacency):
        visit(issue, set())
    return {issue: fault for fault, issue in sorted(owners.items())}


def mapped_edge(edge, mapping):
    segment, direction = edge.rsplit(":", 1)
    return mapping["s"][segment] + ":" + direction


def eligible(issue, fault, mapping, rules):
    """Return an auditable identity/geometry explanation, or no eligible match."""
    if issue["issue_type"] != fault["issue_type"]:
        return None
    kind, target = fault["issue_type"], fault["input_target"]
    expected_object = {
        TYPES[0]: "node",
        TYPES[1]: "turn",
        TYPES[2]: "segment",
        TYPES[3]: "segment",
    }[kind]
    if issue.get("object_type") != expected_object:
        return None
    metrics = issue["evidence_summary"]["metrics"]
    if kind == TYPES[0]:
        pair = {target["node_id"], mapping["n"][fault["original_node"]["node_id"]]}
        if set(metrics.get("node_pair", [])) == pair:
            return {"rule": "exact_node_pair"}
    elif kind == TYPES[2]:
        if (
            issue["object_id"] == target["segment_id"]
            and metrics.get("current_direction") == fault["after"]["direction"]
        ):
            return {"rule": "exact_segment_and_corrupted_direction"}
    elif kind == TYPES[1]:
        before = fault["before"]
        same_approach = metrics.get("via_node_id") == target[
            "via_node_id"
        ] and metrics.get("from_edge_id") == mapped_edge(
            before["from_edge_id"], mapping
        )
        expected_to = mapped_edge(before["to_edge_id"], mapping)
        if not same_approach or not metrics.get("currently_allowed"):
            return None
        if (
            before["restriction_type"].startswith("no_")
            and metrics.get("to_edge_id") == expected_to
        ):
            return {"rule": "exact_directed_prohibited_turn"}
        if (
            before["restriction_type"].startswith("only_")
            and metrics.get("to_edge_id")
            and metrics["to_edge_id"] != expected_to
        ):
            return {"rule": "movement_prohibited_by_removed_only_restriction"}
    elif kind == TYPES[3]:
        project = Transformer.from_crs(
            "EPSG:4326", rules["metric_crs"], always_xy=True
        ).transform
        candidate = transform(project, wkt.loads(issue["geometry_wkt"]))
        expected = transform(project, wkt.loads(fault["before"]["geometry_wkt"]))
        if any(
            g.geom_type != "LineString" or not g.is_valid or g.length <= 0
            for g in (candidate, expected)
        ):
            raise ValueError("Missing-road evaluation requires valid nonempty lines")
        radius = rules["missing_buffer_m"]
        candidate_coverage = (
            candidate.intersection(expected.buffer(radius)).length / candidate.length
        )
        fault_coverage = (
            expected.intersection(candidate.buffer(radius)).length / expected.length
        )
        if (
            candidate_coverage >= rules["minimum_candidate_coverage"]
            and fault_coverage >= rules["minimum_fault_coverage"]
        ):
            return {
                "rule": "bidirectional_buffer_coverage",
                "candidate_coverage": candidate_coverage,
                "fault_coverage": fault_coverage,
                "buffer_m": radius,
            }
    return None


def evaluate(issues, faults, mapping, rules):
    validate_rules(rules)
    for rows, key in ((issues, "issue_id"), (faults, "fault_id")):
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError("Duplicate evaluation identity")
        if any(row["issue_type"] not in TYPES for row in rows):
            raise ValueError("Unsupported evaluation type")
    issues = sorted(issues, key=lambda x: x["issue_id"])
    faults = sorted(faults, key=lambda x: x["fault_id"])
    reasons = {
        (i["issue_id"], f["fault_id"]): result
        for i in issues
        for f in faults
        if (result := eligible(i, f, mapping, rules)) is not None
    }
    adjacent = {
        i["issue_id"]: [f for issue, f in reasons if issue == i["issue_id"]]
        for i in issues
    }
    assignments = maximum_assignment(adjacent)
    detections = [
        {
            "issue_id": i["issue_id"],
            "issue_type": i["issue_type"],
            "fault_id": assignments.get(i["issue_id"]),
            "outcome": "tp" if i["issue_id"] in assignments else "fp",
            "reason": reasons[(i["issue_id"], assignments[i["issue_id"]])]
            if i["issue_id"] in assignments
            else {
                "rule": "duplicate_or_competing_alert"
                if adjacent[i["issue_id"]]
                else "no_eligible_fault"
            },
            "eligible_fault_ids": sorted(adjacent[i["issue_id"]]),
        }
        for i in issues
    ]
    outcomes = [
        {
            "fault_id": f["fault_id"],
            "issue_type": f["issue_type"],
            "issue_id": next(
                (i for i, fault in assignments.items() if fault == f["fault_id"]), None
            ),
            "outcome": "tp" if f["fault_id"] in assignments.values() else "fn",
        }
        for f in faults
    ]
    by_type = {}
    for kind in TYPES:
        tp = sum(
            row["issue_type"] == kind and row["outcome"] == "tp" for row in detections
        )
        fp = sum(
            row["issue_type"] == kind and row["outcome"] == "fp" for row in detections
        )
        fn = sum(
            row["issue_type"] == kind and row["outcome"] == "fn" for row in outcomes
        )
        by_type[kind] = {**counts(tp, fp, fn), "injected": tp + fn, "detected": tp + fp}
    tp = len(assignments)
    return {
        "overall": {
            **counts(tp, len(issues) - tp, len(faults) - tp),
            "injected": len(faults),
            "detected": len(issues),
        },
        "by_type": by_type,
        "detections": detections,
        "faults": outcomes,
        "scope": "closed_world_synthetic_injected_faults",
        "negative_universe": "undefined",
        "assignment": "maximum_cardinality_sorted_augmenting_paths",
    }
