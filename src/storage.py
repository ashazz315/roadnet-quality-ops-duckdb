"""DuckDB persistence for upload batches, records, validation, and issue orders."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "road_quality.duckdb"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "sql" / "duckdb_schema.sql"

HIGH_PRIORITY_TYPES = frozenset({"point_offset", "suspected_dead_end"})
MEDIUM_PRIORITY_TYPES = frozenset(
    {"speed_limit_anomaly", "direction_anomaly", "road_class_anomaly", "road_name_missing"}
)
NON_ISSUE_TYPES = frozenset({"", "normal", "normal_observation", "none"})


class StorageError(RuntimeError):
    """Raised when a batch cannot be stored atomically."""


def initialize_database(
    database_path: Path = DEFAULT_DB_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB database and create the required tables."""
    db_path = Path(database_path)
    sql_path = Path(schema_path)
    if not sql_path.is_file():
        raise StorageError(f"数据库表结构文件不存在：{sql_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(sql_path.read_text(encoding="utf-8"))
    except Exception:
        connection.close()
        raise
    return connection


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _row_json(row: pd.Series, excluded: frozenset[str] = frozenset()) -> str:
    payload = {str(key): _json_value(value) for key, value in row.items() if key not in excluded}
    return json.dumps(payload, ensure_ascii=False, default=str)


def _text(value: Any) -> str | None:
    normalized = _json_value(value)
    return None if normalized is None else str(normalized)


def _number(value: Any) -> float | None:
    normalized = _json_value(value)
    if normalized is None:
        return None
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _priority(issue_type: str) -> str:
    if issue_type in HIGH_PRIORITY_TYPES:
        return "high"
    if issue_type in MEDIUM_PRIORITY_TYPES:
        return "medium"
    return "low"


def persist_batch(
    source_name: str,
    valid_records: pd.DataFrame,
    rejected_records: pd.DataFrame,
    validation_summary: dict[str, Any],
    *,
    records_for_storage: pd.DataFrame | None = None,
    database_path: Path = DEFAULT_DB_PATH,
    uploaded_at: datetime | None = None,
) -> str:
    """Persist one validated upload in a transaction and return its batch ID."""
    if not isinstance(valid_records, pd.DataFrame) or not isinstance(rejected_records, pd.DataFrame):
        raise TypeError("valid_records 和 rejected_records 必须是 pandas.DataFrame。")
    stored_records = valid_records if records_for_storage is None else records_for_storage
    if len(stored_records) != len(valid_records):
        raise StorageError("匹配后的记录数必须与有效记录数一致。")

    total = len(valid_records) + len(rejected_records)
    expected_total = validation_summary.get("total_records", total)
    if int(expected_total) != total:
        raise StorageError("validation_summary 与记录数量不一致。")

    batch_id = str(uuid4())
    created_at = (uploaded_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    connection = initialize_database(database_path)
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            "INSERT INTO upload_batches VALUES (?, ?, ?, ?, ?, ?)",
            [batch_id, Path(source_name).name, created_at, total, len(valid_records), len(rejected_records)],
        )

        record_keys: list[str] = []
        for _, row in stored_records.reset_index(drop=True).iterrows():
            record_key = str(uuid4())
            record_keys.append(record_key)
            connection.execute(
                """INSERT INTO records VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                [
                    record_key,
                    batch_id,
                    _text(row.get("record_id")),
                    _timestamp(row.get("report_time")),
                    _number(row.get("longitude")),
                    _number(row.get("latitude")),
                    _text(row.get("issue_type")),
                    _text(row.get("source")),
                    _text(row.get("description")),
                    _text(row.get("region")),
                    _text(row.get("road_name")),
                    _text(row.get("road_class")),
                    _text(row.get("direction")),
                    _number(row.get("speed_limit")),
                    _text(row.get("matched_road_id")),
                    _text(row.get("matched_road_name")),
                    _number(row.get("match_distance_m")),
                    _text(row.get("match_status")),
                    created_at,
                ],
            )

        for _, row in valid_records.reset_index(drop=True).iterrows():
            connection.execute(
                "INSERT INTO validation_results VALUES (?, ?, ?, TRUE, NULL, NULL, ?, ?)",
                [str(uuid4()), batch_id, _text(row.get("record_id")), _row_json(row), created_at],
            )
        for _, row in rejected_records.reset_index(drop=True).iterrows():
            connection.execute(
                "INSERT INTO validation_results VALUES (?, ?, ?, FALSE, ?, ?, ?, ?)",
                [
                    str(uuid4()),
                    batch_id,
                    _text(row.get("record_id")),
                    _text(row.get("error_code")),
                    _text(row.get("error_message")),
                    _row_json(row, frozenset({"error_code", "error_message"})),
                    created_at,
                ],
            )

        for position, (_, row) in enumerate(stored_records.reset_index(drop=True).iterrows()):
            issue_type = (_text(row.get("issue_type")) or "").strip()
            if issue_type.lower() in NON_ISSUE_TYPES:
                continue
            connection.execute(
                "INSERT INTO issue_orders VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
                [
                    str(uuid4()),
                    batch_id,
                    record_keys[position],
                    _text(row.get("record_id")),
                    issue_type,
                    _priority(issue_type),
                    _text(row.get("region")),
                    _text(row.get("matched_road_id")),
                    _text(row.get("matched_road_name")),
                    _number(row.get("match_distance_m")),
                    created_at,
                ],
            )
        connection.execute("COMMIT")
    except Exception as exc:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise StorageError(f"上传批次写入 DuckDB 失败：{exc}") from exc
    finally:
        connection.close()
    return batch_id
