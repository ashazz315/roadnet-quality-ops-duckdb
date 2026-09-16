"""Independent small road fixtures test rule behavior, not injected target labels."""

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString

from src.analysis_inputs import (
    OBSERVATION_FIELDS,
    clean_observations,
    validate_frames,
    validate_rules,
)
from src.analysis_pipeline import analyze, export_analysis
from src.business.impact import assess_impact, validate_impact_rules
from src.data_sources.base import SourceMetadata
from src.detectors.connectivity import detect_connectivity
from src.detectors.oneway import detect_oneway
from src.detectors.road_change import detect_road_change
from src.detectors.turn_restriction import detect_turns
from src.evidence.fusion import fuse
from src.network.normalization import NetworkTables
from src.network.observation_matching import (
    ObservationNetwork,
    build_passages,
    match_observations,
    project_feedback,
)
from src.scoring.confidence import score_confidence

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/analysis.json").read_bytes())
BUSINESS = json.loads((ROOT / "config/business_impact.json").read_bytes())


@pytest.fixture
def network():
    forward = Transformer.from_crs(4326, 32651, always_xy=True).transform
    backward = Transformer.from_crs(32651, 4326, always_xy=True).transform
    x, y = forward(121.43, 31.18)
    points = {
        "0": (0, 0),
        "1": (100, 0),
        "2": (100, 100),
        "3": (200, 0),
        "4": (200, 100),
        "10": (206, 0),
        "11": (306, 0),
    }
    coords = {key: backward(x + a, y + b) for key, (a, b) in points.items()}
    roads = []
    for identifier, a, b in (
        ("A", "0", "1"),
        ("B", "1", "2"),
        ("C", "1", "3"),
        ("D", "2", "4"),
        ("E", "10", "11"),
        ("F", "3", "4"),
    ):
        line = LineString([coords[a], coords[b]])
        roads.append(
            {
                "segment_id": identifier,
                "from_node_id": a,
                "to_node_id": b,
                "name": identifier,
                "road_class": "residential",
                "oneway": identifier == "A",
                "direction": "forward" if identifier == "A" else "both",
                "lanes": None,
                "maxspeed": None,
                "length_m": 100.0,
                "geometry_wkt": line.wkt,
                "routing_eligible": True,
                "network_version": "unit-network",
            }
        )
    degree = Counter(
        row[key] for row in roads for key in ("from_node_id", "to_node_id")
    )
    nodes = [
        {
            "node_id": key,
            "longitude": lon,
            "latitude": lat,
            "node_degree": degree[key],
            "is_boundary": False,
            "routing_eligible": True,
            "network_version": "unit-network",
        }
        for key, (lon, lat) in coords.items()
    ]
    restrictions = [
        {
            "restriction_id": "no-left",
            "from_segment_id": "A",
            "via_node_id": "1",
            "to_segment_id": "B",
            "from_edge_id": "A:f",
            "to_edge_id": "B:f",
            "restriction_type": "no_left_turn",
            "network_version": "unit-network",
        }
    ]
    tables = NetworkTables(roads, nodes, restrictions, [])
    return ObservationNetwork(tables, RULES), tables


def point_rows(network, *, trips=4, reverse=True):
    rows = []
    line = network.line_by_id["A"]
    for trip in range(trips):
        for seq, distance in enumerate(
            range(90, 0, -10) if reverse else range(10, 100, 10)
        ):
            point = line.interpolate(distance)
            lon, lat = network.to_wgs(point.x, point.y)
            rows.append(
                {
                    "trajectory_id": f"T{trip}",
                    "point_seq": seq,
                    "timestamp": (
                        pd.Timestamp("2026-06-01T00:00:00Z")
                        + pd.Timedelta(hours=trip * 6, seconds=seq * 2)
                    ).isoformat(),
                    "longitude": lon,
                    "latitude": lat,
                    "speed_kmh": 18.0,
                    "heading_deg": 270.0 if reverse else 90.0,
                    "vehicle_type": "motorcar",
                    "source": "independent_test",
                    "is_synthetic": True,
                    "network_version": "unit-network",
                }
            )
    return pd.DataFrame(rows, columns=sorted(OBSERVATION_FIELDS["trajectory_point"]))


def bounds(network):
    return (121.42, 31.17, 121.44, 31.19)


def reports(network, kind="turn_not_allowed"):
    lon, lat = network.nodes["1"]["longitude"], network.nodes["1"]["latitude"]
    frame = pd.DataFrame(
        [
            {
                "feedback_id": "fb1",
                "report_time": "2026-06-01T00:00:00Z",
                "longitude": lon,
                "latitude": lat,
                "feedback_type": kind,
                "description": "Please verify this maneuver",
                "source": "independent_test",
                "is_synthetic": True,
                "network_version": "unit-network",
                "input_row_id": 0,
            }
        ]
    )
    return project_feedback(frame, network)


def reference(index, **fields):
    timestamp = pd.Timestamp("2026-06-01T00:00:00Z") + pd.Timedelta(hours=index * 6)
    return {
        "trajectory_id": f"T{index}",
        "start_seq": 0,
        "end_seq": 2,
        "start_time": timestamp,
        "end_time": timestamp + pd.Timedelta(seconds=4),
        "quality": 0.95,
        **fields,
    }


class MemorySource:
    def __init__(self, tables, points, feedback=None):
        self.frames = {
            name: pd.DataFrame(getattr(tables, name))
            for name in ("road_segment", "road_node", "turn_restriction")
        }
        self.frames["trajectory_point"] = points
        self.frames["user_feedback"] = (
            feedback
            if feedback is not None
            else pd.DataFrame(columns=sorted(OBSERVATION_FIELDS["user_feedback"]))
        )

    def read(self, entity):
        return self.frames[entity.value].copy(deep=True)

    def metadata(self, entity):
        return SourceMetadata(
            "independent-unit-input:" + entity.value, "unit-data", "unit-network", True
        )


def test_matcher_preserves_reverse_evidence_and_oneway_counts_unique_trips(network):
    net, _ = network
    clean, rejected = clean_observations(
        point_rows(net), "trajectory_point", RULES, bounds(net)
    )
    assert rejected.empty
    matched = match_observations(clean, net)
    assert matched.match_status.eq("matched").all()
    assert matched.observed_direction.eq("r").all()
    passages = build_passages(matched, net)
    found = detect_oneway(net, passages, reports(net).iloc[:0], RULES)
    assert len(found) == 1 and found[0]["object_id"] == "A"
    assert found[0]["metrics"]["reverse_trajectory_count"] == 4
    assert found[0]["metrics"]["reverse_ratio"] == 1
    repeated = pd.concat([passages.iloc[:1]] * 100, ignore_index=True)
    assert detect_oneway(net, repeated, reports(net), RULES) == []
    net.roads["A"]["road_class"] = "service"
    assert detect_oneway(net, passages, reports(net), RULES) == []


def test_legal_direction_and_ambiguous_parallel_roads_do_not_claim_reverse(network):
    net, tables = network
    clean, _ = clean_observations(
        point_rows(net, reverse=False), "trajectory_point", RULES, bounds(net)
    )
    assert (
        detect_oneway(
            net,
            build_passages(match_observations(clean, net), net),
            reports(net),
            RULES,
        )
        == []
    )
    duplicate = deepcopy(tables.road_segment[0])
    duplicate["segment_id"] = "parallel-A"
    tables.road_segment.append(duplicate)
    parallel = ObservationNetwork(tables, RULES)
    assert match_observations(clean, parallel).match_status.eq("ambiguous").all()


def test_connectivity_requires_topology_and_distinct_crossing_evidence(network):
    net, _ = network
    refs = [
        reference(index, exit_node_id="3", entry_node_id="10") for index in range(4)
    ]
    found = detect_connectivity(net, refs, reports(net), RULES)
    assert len(found) == 1 and found[0]["object_id"] == "10"
    assert found[0]["metrics"]["gap_m"] == pytest.approx(6, abs=0.001)
    assert detect_connectivity(net, refs[:1] * 10, reports(net), RULES) == []
    assert detect_connectivity(net, [], reports(net), RULES) == []
    net.nodes["10"]["is_boundary"] = True
    assert detect_connectivity(net, refs, reports(net), RULES) == []


def test_existing_no_and_only_restrictions_detect_repeated_forbidden_turns(network):
    net, _ = network
    refs = [
        reference(
            index,
            exit_node_id="1",
            entry_node_id="1",
            from_edge_id="A:f",
            to_edge_id="B:f",
        )
        for index in range(8)
    ]
    found = detect_turns(net, refs, reports(net).iloc[:0], RULES)
    assert len(found) == 1
    assert found[0]["metrics"]["variant"] == "observed_forbidden_turn"
    assert found[0]["metrics"]["observed_turn_ratio"] == 1
    net.restrictions[0]["restriction_type"] = "only_left_turn"
    for row in refs:
        row["to_edge_id"] = "C:f"
    found = detect_turns(net, refs, reports(net).iloc[:0], RULES)
    assert len(found) == 1 and found[0]["metrics"]["to_segment_id"] == "C"


def test_missing_turn_requires_feedback_and_opportunities_not_just_absence(network):
    net, _ = network
    net.restrictions = []
    refs = [
        reference(
            index,
            exit_node_id="1",
            entry_node_id="1",
            from_edge_id="A:f",
            to_edge_id="C:f",
        )
        for index in range(8)
    ]
    assert detect_turns(net, refs, reports(net).iloc[:0], RULES) == []
    assert detect_turns(net, refs[:1] * 20, reports(net), RULES) == []
    found = detect_turns(net, refs, reports(net), RULES)
    assert len(found) == 1 and found[0]["metrics"]["to_segment_id"] == "B"
    assert found[0]["confidence_cap"] == 0.69


def test_missing_road_needs_continuous_repeated_corridor_not_single_gps_jumps(network):
    net, _ = network
    x, y = net.node_points["0"].coords[0]
    rows = []
    for trip in range(4):
        for seq in range(8):
            rows.append(
                {
                    "trajectory_id": f"T{trip}",
                    "point_seq": seq,
                    "timestamp": pd.Timestamp("2026-06-01T00:00:00Z")
                    + pd.Timedelta(hours=trip * 6, seconds=seq * 2),
                    "x": x + seq * 10,
                    "y": y + 200 + trip,
                    "heading_deg": 90.0,
                    "quality_flag": "valid",
                    "match_distance_m": 100.0,
                }
            )
    points = pd.DataFrame(rows)
    found = detect_road_change(net, points, reports(net), RULES)
    assert len(found) == 1 and found[0]["object_id"].startswith("corridor-")
    assert (
        detect_road_change(
            net, points.groupby("trajectory_id").head(1), reports(net), RULES
        )
        == []
    )
    points["trajectory_id"] = "same-vehicle"
    assert detect_road_change(net, points, reports(net), RULES) == []


def test_cleaning_retains_rejection_reasons_and_original_values(network):
    net, _ = network
    frame = point_rows(net, trips=1)
    frame.loc[0, "longitude"] = 999
    frame.loc[1, "vehicle_type"] = "bicycle"
    frame.loc[2, "timestamp"] = "2026-06-01 00:00:04"
    frame.loc[3, "speed_kmh"] = float("inf")
    frame.loc[4, "point_seq"] = frame.loc[5, "point_seq"]
    clean, rejected = clean_observations(frame, "trajectory_point", RULES, bounds(net))
    assert len(clean) + len(rejected) == len(frame)
    assert set(rejected.input_row_id) >= {0, 1, 2, 3, 4, 5}
    assert rejected.rejection_reason.str.len().gt(0).all()
    assert (
        json.loads(
            rejected.loc[rejected.input_row_id.eq(0), "raw_record_json"].iloc[0]
        )["longitude"]
        == 999
    )
    assert (
        "nonfinite_number"
        in rejected.loc[rejected.input_row_id.eq(3), "raw_record_json"].iloc[0]
    )


def test_analysis_rejects_answer_columns_mixed_versions_and_stale_degree(network):
    net, tables = network
    source = MemorySource(tables, point_rows(net))
    frames = deepcopy(source.frames)
    metadata = {
        name: SourceMetadata("unit", "unit", "unit-network", True) for name in frames
    }
    frames["trajectory_point"]["golden_edge_id"] = "leak"
    with pytest.raises(ValueError, match="observable fields"):
        validate_frames(frames, metadata)
    del frames["trajectory_point"]["golden_edge_id"]
    frames["trajectory_point"]["network_version"] = "other"
    with pytest.raises(ValueError, match="version"):
        validate_frames(frames, metadata)
    frames["trajectory_point"]["network_version"] = "unit-network"
    frames["road_node"].loc[0, "node_degree"] = 99
    with pytest.raises(ValueError, match="degree"):
        validate_frames(frames, metadata)


def test_zero_observations_export_typed_empty_results_and_no_fake_rates(
    network, tmp_path
):
    import duckdb

    net, tables = network
    source = MemorySource(tables, point_rows(net).iloc[:0])
    manifest = export_analysis(
        source, tmp_path / "empty", RULES, BUSINESS, code_version="test"
    )
    assert manifest["summary"]["issue_count"] == 0
    assert manifest["summary"]["matching_success_rate"] is None
    with duckdb.connect(
        str(tmp_path / "empty/analysis.duckdb"), read_only=True
    ) as connection:
        assert connection.execute("SELECT count(*) FROM road_issue").fetchone()[0] == 0
        assert (
            dict(
                connection.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='road_issue'"
                ).fetchall()
            )["confidence"]
            == "DOUBLE"
        )
    with pytest.raises(FileExistsError):
        export_analysis(
            source, tmp_path / "empty", RULES, BUSINESS, code_version="test"
        )


def test_invalid_records_are_audited_through_entire_pipeline(network):
    net, tables = network
    points = point_rows(net)
    points.loc[0, "speed_kmh"] = float("inf")
    points.loc[1, "timestamp"] = "bad-time"
    frames, manifest = analyze(
        MemorySource(tables, points), RULES, BUSINESS, code_version="test"
    )
    assert manifest["summary"]["rejected_trajectory_points"] == 2
    assert len(frames["rejected_trajectory_point"]) + len(
        frames["matched_trajectory_point"]
    ) == len(points)


def test_evidence_deduplicates_refs_and_score_explains_penalty_and_cap():
    refs = [reference(index) for index in range(10)]
    item = {
        "trajectory_refs": refs,
        "feedback_refs": [{"feedback_id": "fb", "input_row_id": 0}],
        "topology_support": 1,
        "contradiction_fraction": 0.25,
        "confidence_cap": 0.69,
    }
    first = fuse(item, RULES, 1.0)
    item["trajectory_refs"] = refs * 20
    assert fuse(item, RULES, 1.0) == first
    score = score_confidence(first, RULES)
    assert score["confidence"] == round(
        min(0.69, sum(score["contributions"].values()) * 0.875), 6
    )
    assert (
        score["confidence_kind"] == "uncalibrated_evidence_score"
        and not score["calibrated"]
    )
    reduced = fuse(item, RULES, 0.1)
    assert reduced["dimensions"]["data_quality"] < first["dimensions"]["data_quality"]


def test_business_impact_is_potential_not_fake_replay_or_confidence():
    validate_impact_rules(BUSINESS)
    for issue_type in BUSINESS:
        severity, rows = assess_impact(issue_type, 4, BUSINESS)
        assert severity == "HIGH" and len(rows) == 5
        assert all(
            row["measured_eta_delta_s"] is None
            and row["measured_distance_delta_m"] is None
            and not row["no_path_verified"]
            for row in rows
        )
        assert all(row["assessment_kind"] == "potential_not_replayed" for row in rows)
    invalid = deepcopy(BUSINESS)
    invalid[next(iter(invalid))]["routing"] = "CRITICAL"
    with pytest.raises(ValueError):
        validate_impact_rules(invalid)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("minimum_trajectories", True),
        ("minimum_time_bins", 1),
        ("match_distance_m", 100),
        ("oneway_reverse_ratio", 1.1),
        ("max_speed_kmh", float("nan")),
        ("heading_tolerance_deg", 90),
        ("metric_crs", "EPSG:4326"),
        ("confidence_weights", {"reference_support": 1}),
    ],
)
def test_invalid_analysis_rules_fail(key, value):
    with pytest.raises(ValueError):
        validate_rules({**RULES, key: value})
