"""Compose observable inputs, version-bound issues, hypothetical patches and replay."""

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
from io import BytesIO
from pathlib import Path

import pandas as pd
from pyproj import proj_version_str
from shapely import geos_version_string, wkt
from shapely.geometry import mapping

from src.analysis_inputs import NETWORK_FIELDS, validate_frames
from src.analysis_pipeline import ISSUE_COLUMNS, audit_content_hash, json_text
from src.data_sources.frames import content_hash, spatial_frame
from src.data_sources.replay_results import REPLAY_COLUMNS, write_replay_database
from src.domain import InputEntity
from src.network.routing import TurnAwareRouter, validate_routing_config
from src.replay.patches import apply_plan, identity, propose
from src.replay.route_replay import compare, scenarios
from src.replay_visualization import comparison_svg

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_VERSION = "hypothetical-edge-state-replay-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_hash():
    paths = [
        Path(__file__),
        ROOT / "src/replay_visualization.py",
        ROOT / "src/analysis_inputs.py",
        ROOT / "src/analysis_pipeline.py",
        ROOT / "src/data_sources/replay_results.py",
        ROOT / "src/data_sources/frames.py",
        ROOT / "sql/v2_replay_schema.sql",
    ]
    paths += [
        ROOT / "src/network" / name
        for name in (
            "routing.py",
            "graph_builder.py",
            "normalization.py",
            "validation.py",
        )
    ]
    paths += sorted((ROOT / "src/replay").glob("*.py"))
    return hashlib.sha256(
        json_text(
            {
                path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf8")
                for path in paths
            }
        ).encode()
    ).hexdigest()


def load_analysis(path, input_hashes, sources):
    """Read only the explicitly named issue artifact; never follow arbitrary paths."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_bytes())
    if (
        manifest["run"]["status"] != "completed"
        or manifest["input_content_sha256"] != input_hashes
        or manifest["sources"] != sources
    ):
        raise ValueError(
            "Analysis snapshot does not match current inputs and provenance"
        )
    issue_path = (path.parent / "road_issue.parquet").resolve()
    if not issue_path.is_relative_to(path.parent):
        raise ValueError("Issue artifact must stay inside the analysis snapshot")
    raw = issue_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["artifacts"]["road_issue.parquet"]:
        raise ValueError("Issue artifact checksum mismatch")
    issues = pd.read_parquet(BytesIO(raw))
    if set(issues) != set(ISSUE_COLUMNS) or not issues.issue_id.is_unique:
        raise ValueError("Invalid issue schema or duplicate issue IDs")
    if (
        not issues.run_id.eq(manifest["run"]["run_id"]).all()
        or not issues.network_version.eq(manifest["run"]["network_version"]).all()
    ):
        raise ValueError("Stale issue run or network version")
    if manifest["run"]["network_version"] != sources["road_segment"]["network_version"]:
        raise ValueError("Analysis and current network versions differ")
    for column in ("issue_id", "issue_type", "object_id", "evidence_summary_json"):
        if (
            not issues[column]
            .map(lambda v: isinstance(v, str) and bool(v.strip()))
            .all()
        ):
            raise ValueError("Missing issue identity or evidence summary")
    expected = manifest["result_content_sha256_excluding_run_fields"]["road_issue"]
    if (
        audit_content_hash(
            issues.drop(columns=["run_id", "first_detected_at", "last_detected_at"])
        )
        != expected
    ):
        raise ValueError("Issue semantic checksum mismatch")
    return issues, manifest


def export_snapshot(tables, directory, patch_id):
    directory.mkdir(parents=True, exist_ok=False)
    entities = {}
    for name in NETWORK_FIELDS:
        frame = pd.DataFrame(
            getattr(tables, name), columns=sorted(NETWORK_FIELDS[name])
        )
        frame = spatial_frame(frame, InputEntity(name))
        path = directory / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        entities[name] = {
            "path": path.name,
            "format": "geoparquet" if name != "turn_restriction" else "parquet",
            "row_count": len(frame),
            "sha256": sha(path),
            "content_sha256": content_hash(frame),
            "metadata": {
                "source_ref": "replay-hypothesis:" + patch_id,
                "data_version": tables.road_segment[0]["network_version"],
                "network_version": tables.road_segment[0]["network_version"],
                "is_synthetic": True,
            },
        }
    (directory / "manifest.json").write_bytes(
        json_text(
            {
                "schema_version": 1,
                "purpose": "route_only_hypothetical_network",
                "entities": entities,
            }
        ).encode()
    )


def export_replay(source, analysis_path, output, config, *, code_version):
    started, clock = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite replay output: {output}")
    validate_routing_config(config)
    frames = {entity.value: source.read(entity) for entity in InputEntity}
    metadata = {entity.value: source.metadata(entity) for entity in InputEntity}
    tables = validate_frames(frames, metadata)
    # Optional numeric road attributes use null, not non-JSON NaN, in audit records.
    for row in tables.road_segment:
        for key in ("lanes", "maxspeed"):
            if isinstance(row[key], float) and math.isnan(row[key]):
                row[key] = None
    inputs = {name: audit_content_hash(frame) for name, frame in frames.items()}
    sources = {name: asdict(value) for name, value in metadata.items()}
    issues, analysis = load_analysis(analysis_path, inputs, sources)
    before = TurnAwareRouter(tables, config)
    run_id = str(uuid.uuid4())
    plans, audits, rows, snapshots = [], [], [], []
    for issue in issues.sort_values("issue_id").to_dict("records"):
        plan = propose(issue, tables, before)
        plans.append(plan)
        if plan["status"] != "ready":
            continue
        fixed, audit = apply_plan(tables, issue, plan, config)
        after = TurnAwareRouter(fixed, config)
        audits.append(audit)
        snapshots.append((plan, fixed))
        for scenario in scenarios(plan, config):
            rows.append({**compare(before, after, plan, scenario), "run_id": run_id})
    results = pd.DataFrame(rows, columns=REPLAY_COLUMNS)
    summary = {
        "issue_count": len(issues),
        "ready_plans": sum(plan["status"] == "ready" for plan in plans),
        "manual_review_plans": sum(plan["status"] != "ready" for plan in plans),
        "replay_count": len(rows),
        "operations": dict(
            sorted(Counter(plan["operation"] for plan in plans).items())
        ),
        "reachability_changes": dict(
            sorted(Counter(row["reachability_change"] for row in rows).items())
        ),
        "distance_changes": dict(
            sorted(Counter(row["distance_change"] for row in rows).items())
        ),
        "changed_paths": sum(row["path_changed"] for row in rows),
        "before_paths_illegal_after": sum(
            row["before_edge_sequence_legal_after"] is False for row in rows
        ),
        "target_not_exercised_in_either_route": sum(
            not row["target_exercised_before"] and not row["target_exercised_after"]
            for row in rows
        ),
        "is_hypothetical": True,
        "verification_status": "not_field_verified",
        "eta_kind": "static_speed_and_turn_delay_model",
        "business_scope": [
            "modeled_navigation_legality",
            "modeled_route_planning_reachability_and_distance",
            "modeled_eta",
        ],
    }
    code_hash = implementation_hash()
    manifest = {
        "schema_version": 1,
        "run": {
            "run_id": run_id,
            "replay_batch_id": identity(
                "replay-batch-",
                [inputs, analysis["run"]["analysis_id"], config, code_hash],
            ),
            "algorithm_version": ALGORITHM_VERSION,
            "code_version": code_version,
            "code_sha256": code_hash,
            "rules_version": hashlib.sha256(json_text(config).encode()).hexdigest(),
            "parameters": config,
            "started_at": started,
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
                        "pyarrow",
                        "duckdb",
                        "shapely",
                        "pyproj",
                        "networkx",
                    )
                },
            },
        },
        "sources": sources,
        "input_content_sha256": inputs,
        "source_analysis": {
            "run_id": analysis["run"]["run_id"],
            "analysis_id": analysis["run"]["analysis_id"],
            "manifest_sha256": sha(Path(analysis_path)),
            "issue_sha256": analysis["artifacts"]["road_issue.parquet"],
        },
        "summary": summary,
        "reproducibility_excluded_columns": ["run_id", "source_analysis_run_id"],
        "result_content_sha256": {
            "route_replay": audit_content_hash(
                results.drop(columns=["run_id", "source_analysis_run_id"])
            ),
            "repair_plan": hashlib.sha256(
                json_text(
                    [
                        {k: v for k, v in plan.items() if k != "source_analysis_run_id"}
                        for plan in plans
                    ]
                ).encode()
            ).hexdigest(),
        },
        "limitations": [
            "Each patch is an independent unverified hypothesis, not a cumulative map update.",
            "No detector confidence or source Issue status is changed.",
            "Distance/ETA may increase, decrease or become unavailable; no-path deltas are null.",
            "Clipped network boundary and model speeds limit business interpretation.",
            "No real dispatch, POI accessibility, traffic ETA, field accuracy or benchmark accuracy is measured.",
            "Missing-road corridors require reviewed endpoint, direction and access information.",
            "Issue-adjacent ODs and their reverse controls are diagnostic examples, not traffic-weighted population estimates.",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    issues.to_parquet(output / "source_road_issue.parquet", index=False)
    results.to_parquet(output / "route_replay.parquet", index=False)
    (output / "repair_plan.json").write_bytes(json_text(plans).encode())
    (output / "repair_audit.json").write_bytes(json_text(audits).encode())
    features = []
    for row in rows:
        for phase in ("before", "after"):
            route = json.loads(row[f"{phase}_route_json"])
            if route["geometry_wkt"]:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(wkt.loads(route["geometry_wkt"])),
                        "properties": {
                            "replay_id": row["replay_id"],
                            "issue_id": row["issue_id"],
                            "phase": phase,
                            "network_version": route["network_version"],
                            "distance_m": route["distance_m"],
                            "eta_s": route["eta_s"],
                            "is_hypothetical": True,
                        },
                    }
                )
    (output / "routes.geojson").write_bytes(
        json_text({"type": "FeatureCollection", "features": features}).encode()
    )
    illustration = comparison_svg(rows, plans, tables, config)
    if illustration is not None:
        (output / "route_example.svg").write_bytes(illustration.encode())
    for plan, fixed in snapshots:
        export_snapshot(
            fixed, output / "cases" / plan["patch_id"] / "network", plan["patch_id"]
        )
    write_replay_database(
        output / "replay.duckdb", issues, plans, results, run_id, json_text(manifest)
    )
    manifest["artifacts"] = {
        path.relative_to(output).as_posix(): sha(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    manifest["run"]["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["run"]["elapsed_seconds"] = round(time.perf_counter() - clock, 6)
    # The manifest is the completion marker; partial exports must not be consumed.
    (output / "manifest.json").write_bytes(json_text(manifest).encode())
    return manifest
