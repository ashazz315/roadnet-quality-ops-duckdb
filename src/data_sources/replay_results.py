"""Persist a separate, typed replay database with issue/patch foreign keys."""

import json
from pathlib import Path

import duckdb
import pandas as pd

REPLAY_COLUMNS = [
    "replay_id",
    "issue_id",
    "patch_id",
    "origin_node_id",
    "destination_node_id",
    "scenario_role",
    "selection_reason",
    "source_analysis_run_id",
    "before_network_version",
    "after_network_version",
    "before_status",
    "after_status",
    "before_distance_m",
    "after_distance_m",
    "before_eta_s",
    "after_eta_s",
    "distance_delta_m",
    "eta_delta_s",
    "before_legal",
    "after_legal",
    "before_edge_sequence_legal_after",
    "reachability_change",
    "path_changed",
    "distance_change",
    "target_exercised_before",
    "target_exercised_after",
    "before_route_json",
    "after_route_json",
    "is_hypothetical",
    "verification_status",
    "run_id",
]


def write_replay_database(path, issues, plans, routes, run_id, manifest_json):
    if Path(path).exists():
        raise FileExistsError("Refusing to overwrite replay database")
    with duckdb.connect(str(path)) as db:
        db.execute(
            (
                Path(__file__).resolve().parents[2] / "sql/v2_replay_schema.sql"
            ).read_text(encoding="utf8")
        )
        db.execute("INSERT INTO replay_run VALUES (?, ?)", [run_id, manifest_json])
        for row in issues.to_dict("records"):
            db.execute(
                "INSERT INTO source_issue VALUES (?, ?)",
                [
                    row["issue_id"],
                    json.dumps(row, ensure_ascii=False, allow_nan=False, default=str),
                ],
            )
        for plan in plans:
            db.execute(
                "INSERT INTO repair_plan VALUES (?, ?, ?, ?, ?)",
                [
                    plan["patch_id"],
                    plan["issue_id"],
                    plan["status"],
                    json.dumps(plan, ensure_ascii=False, allow_nan=False),
                    run_id,
                ],
            )
        db.register("routes", pd.DataFrame(routes))
        db.execute("INSERT INTO route_replay BY NAME SELECT * FROM routes")
