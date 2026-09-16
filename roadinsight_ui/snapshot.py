"""Build and verify an offline presentation snapshot from completed STEP 6/7 runs."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import pandas as pd
from shapely import wkt
from shapely.geometry import mapping

from src.analysis_pipeline import audit_content_hash, json_text
from src.data_sources.files import FileDataSource
from src.domain import InputEntity
from src.replay_pipeline import load_analysis

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data/demo/roadinsight-v2.json.gz"


def checked_artifact(directory, manifest, name):
    path = (Path(directory) / name).resolve()
    if not path.is_relative_to(Path(directory).resolve()):
        raise ValueError("Artifact path leaves snapshot directory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["artifacts"][name]:
        raise ValueError(f"Artifact checksum mismatch: {name}")
    return raw


def records(frame):
    return json.loads(
        pd.DataFrame(frame)
        .drop(columns="geometry", errors="ignore")
        .to_json(orient="records", date_format="iso", double_precision=12)
    )


def build_snapshot(inputs_path, analysis_path, replay_path, output):
    """Read only observable inputs and completed analysis/replay outputs, not truth."""
    output = Path(output)
    if output.exists() or output.with_suffix(".manifest.json").exists():
        raise FileExistsError("Refusing to overwrite UI snapshot")
    source = FileDataSource(inputs_path)
    frames = {entity.value: source.read(entity) for entity in InputEntity}
    hashes = {name: audit_content_hash(frame) for name, frame in frames.items()}
    sources = {entity.value: asdict(source.metadata(entity)) for entity in InputEntity}
    issues, analysis = load_analysis(analysis_path, hashes, sources)
    replay_path = Path(replay_path)
    replay = json.loads(replay_path.read_bytes())
    if (
        replay["run"]["status"] != "completed"
        or replay["input_content_sha256"] != hashes
        or replay["source_analysis"]["run_id"] != analysis["run"]["run_id"]
        or replay["source_analysis"]["manifest_sha256"]
        != hashlib.sha256(Path(analysis_path).read_bytes()).hexdigest()
    ):
        raise ValueError(
            "Replay is not from this completed analysis and input snapshot"
        )
    result = {}
    for name in ("issue_evidence", "business_impact", "matched_trajectory_point"):
        result[name] = pd.read_parquet(
            BytesIO(
                checked_artifact(
                    Path(analysis_path).parent, analysis, name + ".parquet"
                )
            )
        )
    route_frame = pd.read_parquet(
        BytesIO(checked_artifact(replay_path.parent, replay, "route_replay.parquet"))
    )
    plans = json.loads(checked_artifact(replay_path.parent, replay, "repair_plan.json"))
    issue_ids = set(issues.issue_id)
    if (
        set(route_frame.issue_id) - issue_ids
        or {row["issue_id"] for row in plans} != issue_ids
    ):
        raise ValueError("Replay contains mismatched issue references")
    payload = {
        "schema_version": 1,
        "dataset_label": "Shanghai-Xuhui-v1",
        "network_version": sources["road_segment"]["network_version"],
        "sources": sources,
        "analysis_run": analysis["run"],
        "replay_run": replay["run"],
        "summary": analysis["summary"],
        "replay_summary": replay["summary"],
        "input_content_sha256": hashes,
        "is_synthetic": analysis["summary"]["is_synthetic"],
        "benchmark": {
            "status": "not_computed",
            "reason": "STEP 9 evaluates faults separately; no accuracy values are available.",
        },
        "roads": [],
        "issues": [],
        "routes": [],
        "plans": plans,
        "network_counts": {
            name: len(frames[name])
            for name in ("road_segment", "road_node", "turn_restriction")
        },
        "provenance": {
            "analysis_manifest_sha256": hashlib.sha256(
                Path(analysis_path).read_bytes()
            ).hexdigest(),
            "replay_manifest_sha256": hashlib.sha256(
                replay_path.read_bytes()
            ).hexdigest(),
            "analysis_artifacts_verified": ["road_issue", *result],
            "replay_artifacts_verified": ["route_replay", "repair_plan"],
        },
    }
    road_names = {}
    for row in records(frames["road_segment"]):
        road_names[row["segment_id"]] = row["name"] or row["segment_id"]
        payload["roads"].append(
            {
                "id": row["segment_id"],
                "name": row["name"],
                "road_class": row["road_class"],
                "direction": row["direction"],
                "coordinates": mapping(wkt.loads(row["geometry_wkt"]))["coordinates"],
            }
        )
    points = result["matched_trajectory_point"]
    point_groups = {
        key: group.sort_values("point_seq")
        for key, group in points.groupby("trajectory_id")
    }
    feedback = {row["feedback_id"]: row for row in records(frames["user_feedback"])}
    for row in records(issues.sort_values("issue_id")):
        summary = json.loads(row.pop("evidence_summary_json"))
        row["evidence_summary"] = summary
        row["geometry"] = mapping(wkt.loads(row["geometry_wkt"]))
        metrics = summary["metrics"]
        linked_segments = [
            row["object_id"],
            metrics.get("from_segment_id"),
            metrics.get("to_segment_id"),
        ]
        names = list(
            dict.fromkeys(
                road_names[key] for key in linked_segments if key in road_names
            )
        )
        row["location_label"] = (
            " / ".join(names)
            if names
            else f"徐汇样本区域 · {row['longitude']:.5f}, {row['latitude']:.5f}"
        )
        row["evidence"] = records(
            result["issue_evidence"].loc[
                result["issue_evidence"].issue_id.eq(row["issue_id"])
            ]
        )
        refs, reports = {}, set()
        for item in row["evidence"]:
            item["detail"] = json.loads(item.pop("metric_text"))
            item["source"] = json.loads(item.pop("source_ref"))
            for ref in (
                item["detail"]["references"]
                if isinstance(item["detail"]["references"], list)
                else []
            ):
                if "trajectory_id" in ref:
                    refs[(ref["trajectory_id"], ref["start_seq"], ref["end_seq"])] = ref
                if "feedback_id" in ref:
                    reports.add(ref["feedback_id"])
        row["trajectories"] = []
        for (trajectory, start, end), ref in sorted(refs.items()):
            selected = point_groups[trajectory]
            context = selected.loc[
                selected.point_seq.between(max(0, start - 5), end + 5)
            ]
            support = selected.loc[selected.point_seq.between(start, end)]
            row["trajectories"].append(
                {
                    "trajectory_id": trajectory,
                    "reference": ref,
                    "context": context[["longitude", "latitude"]].values.tolist(),
                    "support": support[["longitude", "latitude"]].values.tolist(),
                }
            )
        row["feedback"] = [feedback[key] for key in sorted(reports)]
        row["business_impact"] = records(
            result["business_impact"].loc[
                result["business_impact"].issue_id.eq(row["issue_id"])
            ]
        )
        payload["issues"].append(row)
    for row in records(route_frame):
        for phase in ("before", "after"):
            route = json.loads(row.pop(phase + "_route_json"))
            route["geometry"] = (
                mapping(wkt.loads(route["geometry_wkt"]))
                if route["geometry_wkt"]
                else None
            )
            row[phase] = route
        payload["routes"].append(row)
    payload["snapshot_id"] = (
        "ui-" + hashlib.sha256(json_text(payload).encode()).hexdigest()[:20]
    )
    validate_snapshot(payload)
    raw = gzip.compress(json_text(payload).encode(), mtime=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "snapshot_id": payload["snapshot_id"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "analysis_run_id": analysis["run"]["run_id"],
        "replay_run_id": replay["run"]["run_id"],
        "input_content_sha256": hashes,
        "description": "Read-only presentation snapshot, generated from verified observable inputs and completed STEP 6/7 results.",
    }
    output.with_suffix(".manifest.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    )
    return manifest


def validate_snapshot(payload):
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported presentation snapshot")
    ids = [row["issue_id"] for row in payload["issues"]]
    if len(set(ids)) != len(ids) or len(ids) != payload["summary"]["issue_count"]:
        raise ValueError("Invalid issue identity/count in presentation snapshot")
    if any(
        row["network_version"] != payload["network_version"]
        for row in payload["issues"]
    ):
        raise ValueError("Mixed network versions in presentation snapshot")
    if any(
        row["issue_id"] not in ids
        or not row["is_hypothetical"]
        or row["verification_status"] != "not_field_verified"
        for row in payload["routes"]
    ):
        raise ValueError("Invalid replay provenance in presentation snapshot")
    if payload["benchmark"]["status"] != "not_computed":
        raise ValueError("STEP 8 cannot assert an unevaluated benchmark")


def load_snapshot(path=DEFAULT_SNAPSHOT):
    path = Path(path)
    manifest = json.loads(path.with_suffix(".manifest.json").read_bytes())
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("Presentation snapshot checksum mismatch")
    payload = json.loads(gzip.decompress(raw))
    validate_snapshot(payload)
    expected = (
        "ui-"
        + hashlib.sha256(
            json_text(
                {key: value for key, value in payload.items() if key != "snapshot_id"}
            ).encode()
        ).hexdigest()[:20]
    )
    if payload["snapshot_id"] != expected or manifest["snapshot_id"] != expected:
        raise ValueError("Presentation snapshot semantic checksum mismatch")
    return payload
