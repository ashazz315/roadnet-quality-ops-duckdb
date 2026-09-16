"""Fault identity, one-to-one accounting, spatial criteria and empty denominators."""

import copy
import json
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform

from src.evaluation.metrics import (
    TYPES,
    counts,
    eligible,
    evaluate,
    maximum_assignment,
    validate_rules,
)
from src.evaluation.report import bind_report, canonical, read_report

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/evaluation.json").read_bytes())
MAPPING = {"s": {"from": "s-a", "to": "s-b"}, "n": {"original": "n-b"}}


def issue(kind=TYPES[2], identifier="I1", **metrics):
    return {
        "issue_id": identifier,
        "issue_type": kind,
        "object_id": "s-a",
        "object_type": "segment",
        "confidence": 0.8,
        "evidence_summary": {"metrics": {"current_direction": "reverse", **metrics}},
    }


def fault(kind=TYPES[2], identifier="F1"):
    return {
        "fault_id": identifier,
        "issue_type": kind,
        "input_target": {"segment_id": "s-a", "node_id": "n-a", "via_node_id": "n-v"},
        "original_node": {"node_id": "original"},
        "after": {"direction": "reverse"},
        "before": {
            "from_edge_id": "from:f",
            "to_edge_id": "to:f",
            "restriction_type": "no_left_turn",
        },
    }


def test_metrics_and_empty_denominators():
    assert counts(2, 1, 3) == {
        "tp": 2,
        "fp": 1,
        "fn": 3,
        "tn": None,
        "fpr": None,
        "precision": 2 / 3,
        "recall": 0.4,
        "f1": 0.5,
    }
    assert counts(0, 0, 3)["precision"] is None
    assert counts(0, 0, 3)["recall"] == 0
    assert counts(0, 0, 0)["f1"] is None
    assert counts(0, 2, 0)["recall"] is None


def test_augmenting_paths_prevent_greedy_under_counting():
    adjacent = {"I1": ["F1", "F2"], "I2": ["F1"]}
    assert maximum_assignment(adjacent) == {"I2": "F1", "I1": "F2"}
    assert maximum_assignment(
        dict(reversed(list(adjacent.items())))
    ) == maximum_assignment(adjacent)


def test_duplicates_are_fp_and_wrong_type_or_object_is_not_a_match():
    issues = [
        issue(identifier="I2"),
        issue(identifier="I1"),
        issue(TYPES[0], "I3", node_pair=["wrong", "pair"]),
    ]
    report = evaluate(issues, [fault()], MAPPING, RULES)
    assert (
        report["overall"]["tp"],
        report["overall"]["fp"],
        report["overall"]["fn"],
    ) == (1, 2, 0)
    assert evaluate(list(reversed(issues)), [fault()], MAPPING, RULES) == report
    wrong = issue()
    wrong["object_id"] = "s-other"
    assert eligible(wrong, fault(), MAPPING, RULES) is None
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate([issue(), issue()], [fault()], MAPPING, RULES)


def test_connectivity_needs_both_mapped_nodes():
    candidate = issue(TYPES[0], node_pair=["n-b", "n-a"])
    candidate["object_type"] = "node"
    assert eligible(candidate, fault(TYPES[0]), MAPPING, RULES)
    candidate["evidence_summary"]["metrics"]["node_pair"] = ["n-a", "unrelated"]
    assert eligible(candidate, fault(TYPES[0]), MAPPING, RULES) is None


def test_directed_turn_and_only_restriction_semantics():
    candidate = issue(
        TYPES[1],
        via_node_id="n-v",
        from_edge_id="s-a:f",
        to_edge_id="s-b:f",
        currently_allowed=True,
    )
    candidate["object_type"] = "turn"
    expected = fault(TYPES[1])
    assert eligible(candidate, expected, MAPPING, RULES)
    metrics = candidate["evidence_summary"]["metrics"]
    metrics["from_edge_id"] = "s-a:r"
    assert eligible(candidate, expected, MAPPING, RULES) is None
    metrics["from_edge_id"] = "s-a:f"
    expected["before"]["restriction_type"] = "only_straight_on"
    assert eligible(candidate, expected, MAPPING, RULES) is None
    metrics["to_edge_id"] = "s-other:f"
    assert eligible(candidate, expected, MAPPING, RULES)
    metrics["currently_allowed"] = False
    assert eligible(candidate, expected, MAPPING, RULES) is None


def line(points):
    project = Transformer.from_crs("EPSG:32651", "EPSG:4326", always_xy=True).transform
    return transform(
        project, LineString([(350000 + x, 3450000 + y) for x, y in points])
    ).wkt


def test_missing_road_requires_coverage_in_both_directions():
    candidate, expected = issue(TYPES[3]), fault(TYPES[3])
    expected["before"]["geometry_wkt"] = line([(0, 0), (200, 0)])
    candidate["geometry_wkt"] = line([(20, 5), (180, 5)])
    match = eligible(candidate, expected, MAPPING, RULES)
    assert match and match["candidate_coverage"] > 0.99
    candidate["geometry_wkt"] = line([(20, 5), (40, 5)])
    assert eligible(candidate, expected, MAPPING, RULES) is None
    candidate["geometry_wkt"] = line([(0, 40), (200, 40)])
    assert eligible(candidate, expected, MAPPING, RULES) is None
    candidate["geometry_wkt"] = line([(-400, 0), (400, 0)])
    assert eligible(candidate, expected, MAPPING, RULES) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("missing_buffer_m", -1),
        ("minimum_fault_coverage", 1.1),
        ("minimum_candidate_coverage", float("nan")),
        ("metric_crs", "EPSG:4326"),
        ("matching_warning_threshold", True),
    ],
)
def test_invalid_rules_rejected(field, value):
    rules = {**RULES, field: value}
    with pytest.raises(ValueError):
        validate_rules(rules)


def test_published_report_is_bound_and_not_an_unevaluated_claim(tmp_path):
    from roadinsight_ui.snapshot import load_snapshot

    report = read_report(ROOT / "data/validation/default_report.json")
    snapshot = load_snapshot()
    bind_report(report, snapshot)
    totals = report["metrics"]["overall"]
    assert totals["injected"] == 24
    assert totals["tp"] + totals["fp"] == len(snapshot["issues"])
    assert totals["tp"] + totals["fn"] == 24
    assert totals["tn"] is None and totals["fpr"] is None
    assert (
        len({x["fault_id"] for x in report["metrics"]["detections"] if x["fault_id"]})
        == totals["tp"]
    )
    changed = copy.deepcopy(snapshot)
    changed["analysis_run"]["run_id"] = "stale"
    with pytest.raises(ValueError, match="different"):
        bind_report(report, changed)
    corrupt = tmp_path / "report.json"
    report["metrics"]["overall"]["precision"] = 0.123
    corrupt.write_bytes(canonical(report))
    with pytest.raises(ValueError, match="checksum"):
        read_report(corrupt)


def test_quality_warning_is_based_on_matching_not_precision():
    import pandas as pd

    from src.evaluation_pipeline import quality_report

    points = pd.DataFrame(
        [
            {
                k: 1
                for k in (
                    "trajectory_id",
                    "point_seq",
                    "timestamp",
                    "longitude",
                    "latitude",
                    "speed_kmh",
                    "heading_deg",
                    "vehicle_type",
                )
            }
        ]
    )
    rejected = pd.DataFrame(
        [
            {
                "rejection_reason": "duplicate_point_key;invalid_coordinate;non_increasing_timestamp"
            }
        ]
    )
    summary = {
        "accepted_trajectory_points": 0,
        "matching_success_rate": 0.4,
        "mean_matched_distance_m": None,
        "p95_matched_distance_m": None,
    }
    result = quality_report(points, rejected, [issue()], summary, RULES)
    assert result["matching_status"] == "warning"
    assert result["rejected_points"] == 1
    assert {row["metric"]: row["count"] for row in result["record_rates"]}[
        "duplicate_point_key"
    ] == 1


def test_artifact_path_escape_and_tampering(tmp_path):
    from src.evaluation_pipeline import artifact

    with pytest.raises(ValueError, match="leaves"):
        artifact(tmp_path, {"artifacts": {}}, "../outside")
    (tmp_path / "a.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="checksum"):
        artifact(tmp_path, {"artifacts": {"a.json": "bad"}}, "a.json")
