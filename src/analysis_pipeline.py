"""STEP 6 composition: observable inputs -> candidates -> evidence -> potential impact."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import time
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path

import pandas as pd
from pyproj import proj_version_str
from shapely import geos_version_string

from src.analysis_inputs import clean_observations, validate_frames, validate_rules
from src.business.impact import assess_impact, validate_impact_rules
from src.data_sources.analysis_results import write_analysis_database
from src.data_sources.frames import content_hash
from src.detectors.common import stable_id
from src.detectors.connectivity import detect_connectivity
from src.detectors.oneway import detect_oneway
from src.detectors.road_change import detect_road_change
from src.detectors.turn_restriction import detect_turns
from src.domain import InputEntity
from src.evidence.fusion import fuse
from src.network.observation_matching import (
    ObservationNetwork,
    build_passages,
    match_observations,
    project_feedback,
    transitions,
)
from src.scoring.confidence import score_confidence

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_VERSION = "observable-road-diagnosis-v1"
ISSUE_COLUMNS = [
    "issue_id",
    "issue_type",
    "object_type",
    "object_id",
    "longitude",
    "latitude",
    "geometry_wkt",
    "severity",
    "confidence",
    "confidence_kind",
    "calibrated",
    "status",
    "root_cause_hypothesis",
    "suggested_action",
    "first_detected_at",
    "last_detected_at",
    "first_observed_at",
    "last_observed_at",
    "evidence_summary_json",
    "network_version",
    "is_synthetic",
    "run_id",
]
EVIDENCE_COLUMNS = [
    "evidence_id",
    "issue_id",
    "evidence_type",
    "metric_name",
    "metric_value",
    "metric_text",
    "source_ref",
    "weight",
    "run_id",
]
IMPACT_COLUMNS = [
    "issue_id",
    "business_scenario",
    "impact_level",
    "impact_reason",
    "assessment_kind",
    "observed_supporting_trajectories",
    "measured_distance_delta_m",
    "measured_eta_delta_s",
    "no_path_verified",
    "run_id",
]


def json_text(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=lambda item: (
            item.isoformat()
            if isinstance(item, (pd.Timestamp, datetime))
            else str(item)
        ),
    )


def audit_content_hash(frame):
    """Retain nonfinite rejected values as tagged numbers in audit fingerprints."""
    normalized = (
        pd.DataFrame(frame)
        .map(
            lambda value: (
                None
                if value is pd.NaT or value is pd.NA
                else {"nonfinite_number": str(value)}
                if isinstance(value, float) and not math.isfinite(value)
                else value
            )
        )
        .astype(object)
    )
    return content_hash(normalized.where(pd.notna(normalized), None))


def implementation_hash():
    paths = [
        Path(__file__),
        ROOT / "src/analysis_inputs.py",
        ROOT / "src/domain.py",
        ROOT / "src/network/observation_matching.py",
        ROOT / "src/network/graph_builder.py",
        ROOT / "src/network/normalization.py",
        ROOT / "src/network/validation.py",
        ROOT / "src/data_sources/analysis_results.py",
        ROOT / "src/data_sources/frames.py",
        ROOT / "sql/v2_analysis_schema.sql",
    ]
    for package in ("detectors", "evidence", "scoring", "business"):
        paths.extend(sorted((ROOT / "src" / package).glob("*.py")))
    return hashlib.sha256(
        json_text(
            {
                path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf8")
                for path in paths
            }
        ).encode()
    ).hexdigest()


def analyze(
    source, rules: dict, business_rules: dict, *, code_version: str
) -> tuple[dict, dict]:
    """Consume only DataSource's five observable entities; no filesystem answer lookup."""
    clock = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    validate_rules(rules)
    validate_impact_rules(business_rules)
    frames = {
        entity.value: source.read(entity).copy(deep=True) for entity in InputEntity
    }
    metadata = {entity.value: source.metadata(entity) for entity in InputEntity}
    tables = validate_frames(frames, metadata)
    network = ObservationNetwork(tables, rules)
    coords = frames["road_node"]
    bounds = (
        coords.longitude.min(),
        coords.latitude.min(),
        coords.longitude.max(),
        coords.latitude.max(),
    )
    points, rejected_points = clean_observations(
        frames["trajectory_point"], "trajectory_point", rules, bounds
    )
    feedback, rejected_feedback = clean_observations(
        frames["user_feedback"], "user_feedback", rules, bounds
    )
    matched = match_observations(points, network)
    feedback = project_feedback(feedback, network)
    passages = build_passages(matched, network)
    movements = transitions(passages, network)
    candidates = (
        detect_connectivity(network, movements, feedback, rules)
        + detect_oneway(network, passages, feedback, rules)
        + detect_turns(network, movements, feedback, rules)
        + detect_road_change(network, matched, feedback, rules)
    )
    input_hashes = {name: audit_content_hash(frame) for name, frame in frames.items()}
    code_hash = implementation_hash()
    analysis_id = stable_id(
        "analysis-",
        {
            "inputs": input_hashes,
            "rules": rules,
            "business_rules": business_rules,
            "code": code_hash,
        },
    )
    run_id = str(uuid.uuid4())
    network_version = metadata["road_segment"].network_version
    synthetic = any(value.is_synthetic for value in metadata.values())
    valid_fraction = (
        int(matched.quality_flag.eq("valid").sum()) / len(frames["trajectory_point"])
        if len(frames["trajectory_point"])
        else 0.0
    )
    issues, evidence_rows, impacts = [], [], []
    for item in sorted(
        candidates,
        key=lambda row: (row["issue_type"], row["object_id"], row["geometry_wkt"]),
    ):
        issue_id = stable_id(
            "issue-",
            [analysis_id, item["issue_type"], item["object_id"], item["geometry_wkt"]],
        )
        evidence = fuse(item, rules, valid_fraction)
        score = score_confidence(evidence, rules)
        severity, business = assess_impact(
            item["issue_type"], evidence["trajectory_count"], business_rules
        )
        summary = {
            "metrics": item["metrics"],
            "trajectory_count": evidence["trajectory_count"],
            "feedback_count": evidence["feedback_count"],
            "time_bin_count": evidence["time_bin_count"],
            "dimensions": evidence["dimensions"],
            "score_calculation": score,
        }
        issues.append(
            {
                **{
                    key: item[key]
                    for key in (
                        "issue_type",
                        "object_type",
                        "object_id",
                        "longitude",
                        "latitude",
                        "geometry_wkt",
                        "root_cause_hypothesis",
                        "suggested_action",
                    )
                },
                "issue_id": issue_id,
                "severity": severity,
                "confidence": score["confidence"],
                "confidence_kind": score["confidence_kind"],
                "calibrated": False,
                "status": "needs_review",
                "first_detected_at": started,
                "last_detected_at": started,
                "first_observed_at": evidence["first_observed_at"],
                "last_observed_at": evidence["last_observed_at"],
                "evidence_summary_json": json_text(summary),
                "network_version": network_version,
                "is_synthetic": synthetic,
                "run_id": run_id,
            }
        )
        sources = {name: asdict(value) for name, value in metadata.items()}
        for name, value in evidence["dimensions"].items():
            is_feedback = name == "feedback_support"
            entity = (
                "user_feedback"
                if is_feedback
                else "road_segment"
                if name == "topology_support"
                else "trajectory_point"
            )
            if name == "topology_support" and item["object_type"] == "node":
                entity = "road_node"
            elif (
                name == "topology_support"
                and item["metrics"].get("variant") == "observed_forbidden_turn"
            ):
                entity = "turn_restriction"
            refs = (
                evidence["feedback_refs"]
                if is_feedback
                else {"object_id": item["object_id"], "metrics": item["metrics"]}
                if name == "topology_support"
                else evidence["trajectory_refs"]
            )
            evidence_rows.append(
                {
                    "evidence_id": stable_id("evidence-", [issue_id, name]),
                    "issue_id": issue_id,
                    "evidence_type": "user_feedback"
                    if is_feedback
                    else "topology"
                    if name == "topology_support"
                    else "trajectory",
                    "metric_name": name,
                    "metric_value": value,
                    "metric_text": json_text(
                        {
                            "references": refs,
                            "metrics": item["metrics"],
                            "contribution_before_penalty": score["contributions"][name],
                        }
                    ),
                    "source_ref": json_text({"entity": entity, **sources[entity]}),
                    "weight": rules["confidence_weights"][name],
                    "run_id": run_id,
                }
            )
        impacts.extend(
            {"issue_id": issue_id, **row, "run_id": run_id} for row in business
        )
    results = {
        "road_issue": pd.DataFrame(issues, columns=ISSUE_COLUMNS),
        "issue_evidence": pd.DataFrame(evidence_rows, columns=EVIDENCE_COLUMNS),
        "business_impact": pd.DataFrame(impacts, columns=IMPACT_COLUMNS),
        "matched_trajectory_point": matched,
        "trajectory_passage": passages,
        "rejected_trajectory_point": rejected_points,
        "accepted_user_feedback": feedback,
        "rejected_user_feedback": rejected_feedback,
    }
    for frame in results.values():
        frame["run_id"] = run_id
    counts = matched.match_status.value_counts().to_dict()
    matched_count = int(counts.get("matched", 0))
    distances = matched.loc[matched.match_status.eq("matched"), "match_distance_m"]
    summary = {
        "input_trajectory_points": len(frames["trajectory_point"]),
        "accepted_trajectory_points": len(points),
        "rejected_trajectory_points": len(rejected_points),
        "quality_excluded_points": int(counts.get("quality_excluded", 0)),
        "input_feedback": len(frames["user_feedback"]),
        "accepted_feedback": len(feedback),
        "rejected_feedback": len(rejected_feedback),
        "trajectory_count": int(points.trajectory_id.nunique()),
        "matching_status_counts": counts,
        "matching_success_rate": matched_count / len(points) if len(points) else None,
        "mean_matched_distance_m": float(distances.mean()) if len(distances) else None,
        "p95_matched_distance_m": float(distances.quantile(0.95))
        if len(distances)
        else None,
        "issue_count": len(issues),
        "issues_per_type": dict(
            sorted(Counter(row["issue_type"] for row in issues).items())
        ),
        "evidence_rows": len(evidence_rows),
        "business_impact_rows": len(impacts),
        "is_synthetic": synthetic,
        "confidence_calibrated": False,
        "business_impact_measured": False,
    }
    result_hashes = {
        name: audit_content_hash(
            frame.drop(
                columns=["run_id", "first_detected_at", "last_detected_at"],
                errors="ignore",
            )
        )
        for name, frame in results.items()
    }
    manifest = {
        "schema_version": 1,
        "run": {
            "run_id": run_id,
            "analysis_id": analysis_id,
            "source_batch_id": stable_id("batch-", input_hashes),
            "network_version": network_version,
            "algorithm_version": ALGORITHM_VERSION,
            "rules_version": hashlib.sha256(
                json_text({"analysis": rules, "business": business_rules}).encode()
            ).hexdigest(),
            "code_version": code_version,
            "code_sha256": code_hash,
            "parameters": {"analysis": rules, "business": business_rules},
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - clock, 6),
            "status": "completed",
            "seed": None,
            "environment": {
                "python": platform.python_version(),
                "system": platform.system(),
                "machine": platform.machine(),
                "proj": proj_version_str,
                "geos": geos_version_string,
                **{
                    name: package_version(name)
                    for name in (
                        "pandas",
                        "numpy",
                        "shapely",
                        "pyproj",
                        "networkx",
                        "pyarrow",
                        "duckdb",
                    )
                },
            },
        },
        "sources": {name: asdict(value) for name, value in metadata.items()},
        "input_content_sha256": input_hashes,
        "result_content_sha256_excluding_run_fields": result_hashes,
        "reproducibility_excluded_columns": [
            "run_id",
            "first_detected_at",
            "last_detected_at",
        ],
        "summary": summary,
        "warnings": (
            ["low_matching_success_requires_review"]
            if summary["matching_success_rate"] is not None
            and summary["matching_success_rate"] < 0.7
            else []
        )
        + (
            ["synthetic_observations_are_not_real_world_validation"]
            if synthetic
            else []
        ),
        "limitations": [
            "Local distance/heading matching is not probabilistic path map matching.",
            "Confidence is an uncalibrated evidence score, not accuracy.",
            "Potential business impact has not been verified by route replay.",
            "No benchmark labels or detector accuracy metrics are consumed or computed.",
        ],
    }
    return results, manifest


def export_analysis(
    source, output: Path, rules: dict, business_rules: dict, *, code_version: str
):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite analysis output: {output}")
    frames, manifest = analyze(source, rules, business_rules, code_version=code_version)
    output.mkdir(parents=True, exist_ok=False)
    for name, frame in frames.items():
        frame.to_parquet(output / f"{name}.parquet", index=False)
    run = manifest["run"]
    run_frame = pd.DataFrame(
        [
            {
                key: json_text(value) if isinstance(value, (dict, list)) else value
                for key, value in {
                    **run,
                    "summary_json": json_text(manifest["summary"]),
                }.items()
            }
        ]
    )
    write_analysis_database(
        output / "analysis.duckdb", {**frames, "analysis_run": run_frame}
    )
    manifest["artifacts"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
        if path.is_file()
    }
    (output / "manifest.json").write_text(json_text(manifest), encoding="utf8")
    return manifest
