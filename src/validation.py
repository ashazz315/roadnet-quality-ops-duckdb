"""Lossless row validation for uploaded road-quality reports."""

from __future__ import annotations

from collections import Counter
from typing import Any, NamedTuple

import pandas as pd

STANDARD_FIELDS = (
    "record_id",
    "report_time",
    "longitude",
    "latitude",
    "issue_type",
    "source",
    "description",
    "region",
    "road_name",
    "road_class",
    "direction",
    "speed_limit",
)

REQUIRED_FIELDS = (
    "record_id",
    "report_time",
    "longitude",
    "latitude",
    "issue_type",
    "source",
    "description",
    "region",
)


class ValidationResult(NamedTuple):
    valid_records: pd.DataFrame
    rejected_records: pd.DataFrame
    validation_summary: dict[str, Any]


def _is_blank(value: object) -> bool:
    return pd.isna(value) or not str(value).strip()


def _add_error(codes: list[str], messages: list[str], code: str, message: str) -> None:
    if code not in codes:
        codes.append(code)
        messages.append(message)


def _summary(
    total: int,
    valid: int,
    rejected: int,
    error_counts: Counter[str],
    missing_columns: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "total_records": total,
        "valid_records": valid,
        "rejected_records": rejected,
        "valid_rate": round(valid / total, 4) if total else 0.0,
        "error_counts": dict(sorted(error_counts.items())),
        "missing_required_columns": list(missing_columns),
    }


def _missing_column_result(frame: pd.DataFrame, missing: tuple[str, ...]) -> ValidationResult:
    valid = pd.DataFrame(columns=STANDARD_FIELDS)
    rejected = frame.copy(deep=True)
    message = f"缺少必填字段：{', '.join(missing)}"
    rejected["error_code"] = "missing_required_column"
    rejected["error_message"] = message
    error_counts = Counter({"missing_required_column": len(rejected)}) if len(rejected) else Counter()
    return ValidationResult(
        valid,
        rejected,
        _summary(len(frame), 0, len(rejected), error_counts, missing),
    )


def validate_records(frame: pd.DataFrame) -> ValidationResult:
    """Validate every input row without silently dropping rejected data."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame 必须是 pandas.DataFrame。")

    original = frame.copy(deep=True)
    missing_columns = tuple(field for field in REQUIRED_FIELDS if field not in original.columns)
    if missing_columns:
        return _missing_column_result(original, missing_columns)

    working = original.copy(deep=True)
    for field in STANDARD_FIELDS:
        if field not in working.columns:
            working[field] = pd.NA

    normalized_ids = working["record_id"].map(lambda value: "" if _is_blank(value) else str(value).strip())
    duplicate_ids = normalized_ids.ne("") & normalized_ids.duplicated(keep=False)

    valid_rows: list[dict[str, object]] = []
    rejected_rows: list[dict[str, object]] = []
    error_counts: Counter[str] = Counter()

    for position in range(len(working)):
        row = working.iloc[position]
        original_row = original.iloc[position].to_dict()
        normalized = {field: row[field] for field in STANDARD_FIELDS}
        codes: list[str] = []
        messages: list[str] = []

        for field in REQUIRED_FIELDS:
            if _is_blank(row[field]):
                _add_error(codes, messages, f"missing_{field}", f"{field} 不能为空")

        if not _is_blank(row["record_id"]):
            normalized["record_id"] = str(row["record_id"]).strip()
            if bool(duplicate_ids.iloc[position]):
                _add_error(codes, messages, "duplicate_record_id", "record_id 重复")

        if not _is_blank(row["report_time"]):
            parsed_time = pd.to_datetime(row["report_time"], errors="coerce")
            if pd.isna(parsed_time):
                _add_error(codes, messages, "invalid_report_time", "report_time 不是有效日期时间")
            else:
                normalized["report_time"] = parsed_time

        for field, minimum, maximum in (
            ("longitude", -180.0, 180.0),
            ("latitude", -90.0, 90.0),
        ):
            if _is_blank(row[field]):
                continue
            numeric = pd.to_numeric(pd.Series([row[field]]), errors="coerce").iloc[0]
            if pd.isna(numeric):
                _add_error(codes, messages, f"invalid_{field}", f"{field} 必须是数字")
            elif not minimum <= float(numeric) <= maximum:
                _add_error(codes, messages, f"{field}_out_of_range", f"{field} 超出范围")
            else:
                normalized[field] = float(numeric)

        speed_limit = row["speed_limit"]
        if not _is_blank(speed_limit):
            numeric_speed = pd.to_numeric(pd.Series([speed_limit]), errors="coerce").iloc[0]
            if pd.isna(numeric_speed) or float(numeric_speed) < 0:
                _add_error(codes, messages, "invalid_speed_limit", "speed_limit 必须是非负数字")
            else:
                normalized["speed_limit"] = float(numeric_speed)

        for field in ("issue_type", "source", "description", "region", "road_name", "road_class", "direction"):
            if not _is_blank(row[field]):
                normalized[field] = str(row[field]).strip()

        if codes:
            rejected_row = dict(original_row)
            rejected_row["error_code"] = ";".join(codes)
            rejected_row["error_message"] = "；".join(messages)
            rejected_rows.append(rejected_row)
            error_counts.update(codes)
        else:
            valid_rows.append(normalized)

    valid_records = pd.DataFrame(valid_rows, columns=STANDARD_FIELDS)
    rejected_records = pd.DataFrame(
        rejected_rows,
        columns=[*original.columns, "error_code", "error_message"],
    )
    total = len(original)
    if len(valid_records) + len(rejected_records) != total:
        raise RuntimeError("验证结果数量不守恒。")

    summary = _summary(total, len(valid_records), len(rejected_records), error_counts, ())
    return ValidationResult(valid_records, rejected_records, summary)
