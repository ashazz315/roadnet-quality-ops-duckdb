from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from src.storage import persist_batch


def sample_frames() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    valid = pd.DataFrame(
        [
            {
                "record_id": "A1",
                "report_time": "2026-07-22T08:00:00+08:00",
                "longitude": 116.4,
                "latitude": 39.9,
                "issue_type": "point_offset",
                "source": "巡检",
                "description": "点位偏移",
                "region": "朝阳区",
            },
            {
                "record_id": "A2",
                "report_time": "2026-07-22T08:05:00+08:00",
                "longitude": 116.41,
                "latitude": 39.91,
                "issue_type": "normal_observation",
                "source": "巡检",
                "description": "正常",
                "region": "海淀区",
            },
        ]
    )
    rejected = pd.DataFrame(
        [
            {
                "record_id": "A3",
                "longitude": "bad",
                "extra_field": "必须保留",
                "error_code": "invalid_longitude",
                "error_message": "longitude 必须是数字",
            }
        ]
    )
    return valid, rejected, {"total_records": 3, "valid_records": 2, "rejected_records": 1}


def test_persist_batch_creates_four_tables_and_keeps_raw_errors(tmp_path: Path) -> None:
    database = tmp_path / "quality.duckdb"
    valid, rejected, summary = sample_frames()
    matched = valid.assign(
        matched_road_id=["road-1", "road-2"],
        matched_road_name=["测试路", "示例路"],
        match_distance_m=[42.5, 0.5],
        match_status=["needs_review", "matched"],
    )
    batch_id = persist_batch(
        "upload.csv",
        valid,
        rejected,
        summary,
        records_for_storage=matched,
        database_path=database,
        uploaded_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    connection = duckdb.connect(str(database), read_only=True)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {"upload_batches", "records", "validation_results", "issue_orders"} <= tables
    assert connection.execute("SELECT COUNT(*) FROM records WHERE batch_id = ?", [batch_id]).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM validation_results WHERE batch_id = ?", [batch_id]
    ).fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM issue_orders WHERE batch_id = ?", [batch_id]).fetchone()[0] == 1
    raw_json = connection.execute(
        "SELECT raw_record_json FROM validation_results WHERE batch_id = ? AND NOT is_valid", [batch_id]
    ).fetchone()[0]
    assert "必须保留" in raw_json
    assert connection.execute(
        "SELECT priority FROM issue_orders WHERE batch_id = ?", [batch_id]
    ).fetchone()[0] == "high"
    connection.close()
