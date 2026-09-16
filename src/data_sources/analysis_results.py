"""Write an isolated analysis database, never the legacy upload database."""

from pathlib import Path

import duckdb

TABLES = {
    "road_issue",
    "issue_evidence",
    "business_impact",
    "matched_trajectory_point",
    "trajectory_passage",
    "rejected_trajectory_point",
    "accepted_user_feedback",
    "rejected_user_feedback",
    "analysis_run",
}


def write_analysis_database(path: Path, frames: dict) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite analysis database: {path}")
    if set(frames) != TABLES:
        raise ValueError("Unexpected analysis result table set")
    with duckdb.connect(str(path)) as connection:
        schema = Path(__file__).resolve().parents[2] / "sql/v2_analysis_schema.sql"
        connection.execute(schema.read_text(encoding="utf8"))
        core = ["road_issue", "issue_evidence", "business_impact"]
        for name in core + sorted(set(frames) - set(core)):
            frame = frames[name]
            connection.register("result_frame", frame)
            if name in core:
                connection.execute(
                    f'INSERT INTO "{name}" BY NAME SELECT * FROM result_frame'
                )
            else:
                connection.execute(
                    f'CREATE TABLE "{name}" AS SELECT * FROM result_frame'
                )
            connection.unregister("result_frame")
        connection.execute("CREATE UNIQUE INDEX issue_key ON road_issue(issue_id)")
        connection.execute(
            "CREATE UNIQUE INDEX evidence_key ON issue_evidence(evidence_id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX impact_key ON business_impact(issue_id, business_scenario)"
        )
        for table in ("issue_evidence", "business_impact"):
            missing = connection.execute(
                f'SELECT count(*) FROM "{table}" t LEFT JOIN road_issue i USING(issue_id) WHERE i.issue_id IS NULL'
            ).fetchone()[0]
            if missing:
                raise ValueError("Orphan analysis evidence or business impact")
