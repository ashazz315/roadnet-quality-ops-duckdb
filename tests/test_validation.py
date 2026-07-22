from __future__ import annotations

import pandas as pd
import pytest

from src.validation import REQUIRED_FIELDS, STANDARD_FIELDS, validate_records


def valid_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "record_id": "R001",
        "report_time": "2026-07-22 09:00:00",
        "longitude": "116.481",
        "latitude": "39.991",
        "issue_type": "missing_road",
        "source": "app",
        "description": "疑似缺路",
        "region": "望京",
        "road_name": "测试路",
        "road_class": "secondary",
        "direction": "both",
        "speed_limit": "40",
    }
    row.update(overrides)
    return row


def test_valid_records_use_standard_fields_and_typed_values() -> None:
    result = validate_records(pd.DataFrame([valid_row()]))
    valid_records, rejected_records, summary = result
    assert list(valid_records.columns) == list(STANDARD_FIELDS)
    assert rejected_records.empty
    assert valid_records.loc[0, "longitude"] == pytest.approx(116.481)
    assert valid_records.loc[0, "latitude"] == pytest.approx(39.991)
    assert valid_records.loc[0, "speed_limit"] == pytest.approx(40)
    assert isinstance(valid_records.loc[0, "report_time"], pd.Timestamp)
    assert summary["valid_records"] == 1
    assert summary["valid_rate"] == 1.0


def test_missing_required_columns_reject_every_input_row_without_data_loss() -> None:
    frame = pd.DataFrame([{"record_id": "A1", "extra_field": "keep me"}, {"record_id": "A2", "extra_field": "keep too"}])
    valid_records, rejected_records, summary = validate_records(frame)
    assert valid_records.empty
    assert len(rejected_records) == len(frame)
    assert list(rejected_records["extra_field"]) == ["keep me", "keep too"]
    assert set(rejected_records["error_code"]) == {"missing_required_column"}
    assert summary["missing_required_columns"] == [field for field in REQUIRED_FIELDS if field != "record_id"]
    assert summary["total_records"] == summary["rejected_records"] == 2


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_each_required_field_rejects_blank_value(field: str) -> None:
    valid_records, rejected_records, summary = validate_records(pd.DataFrame([valid_row(**{field: "  "})]))
    assert valid_records.empty
    assert len(rejected_records) == 1
    assert f"missing_{field}" in rejected_records.loc[0, "error_code"]
    assert summary["error_counts"][f"missing_{field}"] == 1


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("report_time", "not-a-date", "invalid_report_time"),
        ("longitude", "east", "invalid_longitude"),
        ("longitude", 181, "longitude_out_of_range"),
        ("latitude", "north", "invalid_latitude"),
        ("latitude", -91, "latitude_out_of_range"),
        ("speed_limit", "fast", "invalid_speed_limit"),
        ("speed_limit", -1, "invalid_speed_limit"),
    ],
)
def test_format_and_range_rules(field: str, value: object, error_code: str) -> None:
    _, rejected_records, summary = validate_records(pd.DataFrame([valid_row(**{field: value})]))
    assert error_code in rejected_records.loc[0, "error_code"]
    assert summary["error_counts"][error_code] == 1


def test_all_duplicate_record_ids_are_rejected_after_trimming() -> None:
    frame = pd.DataFrame([valid_row(record_id="DUP"), valid_row(record_id=" DUP ")])
    valid_records, rejected_records, summary = validate_records(frame)
    assert valid_records.empty
    assert len(rejected_records) == 2
    assert rejected_records["error_code"].str.contains("duplicate_record_id").all()
    assert summary["error_counts"]["duplicate_record_id"] == 2


def test_rejected_record_preserves_original_and_extra_fields_with_all_errors() -> None:
    source = valid_row(longitude="bad", latitude=100)
    source["vendor_payload"] = "raw-value"
    _, rejected_records, summary = validate_records(pd.DataFrame([source]))
    rejected = rejected_records.iloc[0]
    assert rejected["longitude"] == "bad"
    assert rejected["latitude"] == 100
    assert rejected["vendor_payload"] == "raw-value"
    assert rejected["error_code"] == "invalid_longitude;latitude_out_of_range"
    assert "longitude 必须是数字" in rejected["error_message"]
    assert summary["total_records"] == 1
    assert summary["valid_records"] + summary["rejected_records"] == 1


def test_empty_frame_returns_summary_without_dropping_rows() -> None:
    frame = pd.DataFrame(columns=STANDARD_FIELDS)
    valid_records, rejected_records, summary = validate_records(frame)
    assert valid_records.empty
    assert rejected_records.empty
    assert summary["total_records"] == 0
    assert summary["valid_rate"] == 0.0


def test_non_dataframe_is_rejected() -> None:
    with pytest.raises(TypeError, match="DataFrame"):
        validate_records([])  # type: ignore[arg-type]
