"""Column normalization and row-level validation for uploaded point records."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS = ("record_id", "longitude", "latitude")
OPTIONAL_COLUMNS = ("issue_type", "description", "severity", "reported_at")
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}
COLUMN_ALIASES = {
    "id": "record_id",
    "recordid": "record_id",
    "lon": "longitude",
    "lng": "longitude",
    "x": "longitude",
    "lat": "latitude",
    "y": "latitude",
}


@dataclass(frozen=True)
class ValidationResult:
    valid: pd.DataFrame
    errors: pd.DataFrame
    missing_columns: tuple[str, ...]

    @property
    def total_rows(self) -> int:
        return len(self.valid) + len(self.errors)


def normalize_column_name(value: object) -> str:
    normalized = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return COLUMN_ALIASES.get(normalized, normalized)


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    names = [normalize_column_name(column) for column in normalized.columns]
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ValueError(f"字段别名归一化后重复：{', '.join(duplicates)}")
    normalized.columns = names
    return normalized


def _is_blank(value: object) -> bool:
    return pd.isna(value) or not str(value).strip()


def _append_error(codes: list[str], messages: list[str], code: str, message: str) -> None:
    codes.append(code)
    messages.append(message)


def validate_records(frame: pd.DataFrame) -> ValidationResult:
    """Split records into valid and error frames while preserving source rows."""
    normalized = normalize_columns(frame)
    missing = tuple(column for column in REQUIRED_COLUMNS if column not in normalized.columns)
    if missing:
        empty = normalized.iloc[0:0].copy()
        return ValidationResult(valid=empty, errors=empty, missing_columns=missing)

    working = normalized.copy()
    working.insert(0, "source_row", range(2, len(working) + 2))
    duplicate_ids = (
        working["record_id"].astype("string").str.strip().duplicated(keep=False)
        & working["record_id"].notna()
    )

    valid_rows: list[dict[str, object]] = []
    error_rows: list[dict[str, object]] = []

    for position, (_, row) in enumerate(working.iterrows()):
        record = row.to_dict()
        codes: list[str] = []
        messages: list[str] = []

        if _is_blank(record.get("record_id")):
            _append_error(codes, messages, "missing_record_id", "record_id 不能为空")
        elif bool(duplicate_ids.iloc[position]):
            _append_error(codes, messages, "duplicate_record_id", "record_id 重复")
        else:
            record["record_id"] = str(record["record_id"]).strip()

        for column, minimum, maximum, label in (
            ("longitude", -180.0, 180.0, "经度"),
            ("latitude", -90.0, 90.0, "纬度"),
        ):
            value = pd.to_numeric(pd.Series([record.get(column)]), errors="coerce").iloc[0]
            if pd.isna(value):
                _append_error(codes, messages, f"invalid_{column}", f"{label}必须是数字")
            elif not minimum <= float(value) <= maximum:
                _append_error(codes, messages, f"out_of_range_{column}", f"{label}超出范围")
            else:
                record[column] = float(value)

        severity = record.get("severity")
        if not _is_blank(severity):
            severity_value = str(severity).strip().lower()
            if severity_value not in ALLOWED_SEVERITIES:
                _append_error(
                    codes,
                    messages,
                    "invalid_severity",
                    "severity 仅支持 low/medium/high/critical",
                )
            else:
                record["severity"] = severity_value

        reported_at = record.get("reported_at")
        if not _is_blank(reported_at):
            parsed = pd.to_datetime(reported_at, errors="coerce")
            if pd.isna(parsed):
                _append_error(codes, messages, "invalid_reported_at", "reported_at 不是有效日期")
            else:
                record["reported_at"] = parsed.isoformat()

        if codes:
            record["error_codes"] = ";".join(codes)
            record["error_messages"] = "；".join(messages)
            error_rows.append(record)
        else:
            valid_rows.append(record)

    base_columns = list(working.columns)
    valid = pd.DataFrame(valid_rows, columns=base_columns)
    errors = pd.DataFrame(error_rows, columns=[*base_columns, "error_codes", "error_messages"])
    return ValidationResult(valid=valid, errors=errors, missing_columns=())
