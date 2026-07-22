from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from src.ingest import IngestError, ingest_bytes, ingest_path


def test_ingest_utf8_csv_preserves_text_and_blank_line() -> None:
    content = (
        "record_id,report_time,longitude,latitude,issue_type,source,description,region\n"
        "001,2026-07-22 09:00,116.4,39.9,missing_road,app,疑似缺路,望京\n"
        "\n"
    ).encode("utf-8")
    frame = ingest_bytes(content, "reports.csv")
    assert len(frame) == 2
    assert frame.loc[0, "record_id"] == "001"
    assert frame.loc[0, "description"] == "疑似缺路"


def test_ingest_utf8_bom_csv() -> None:
    content = "record_id,report_time\nA1,2026-07-22\n".encode("utf-8-sig")
    frame = ingest_bytes(content, Path("reports.CSV"))
    assert list(frame.columns) == ["record_id", "report_time"]


def test_reject_non_utf8_csv() -> None:
    content = "record_id,description\nA1,道路缺失\n".encode("gb18030")
    with pytest.raises(IngestError, match="UTF-8"):
        ingest_bytes(content, "reports.csv")


def test_ingest_first_xlsx_sheet() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(["record_id", "report_time", "longitude", "latitude"])
    sheet.append(["A1", "2026-07-22", 116.4, 39.9])
    workbook.create_sheet("ignored").append(["not_used"])
    buffer = BytesIO()
    workbook.save(buffer)

    frame = ingest_bytes(buffer.getvalue(), "reports.xlsx")
    assert frame.loc[0, "record_id"] == "A1"
    assert frame.loc[0, "longitude"] == 116.4


def test_ingest_path_uses_pathlib(tmp_path: Path) -> None:
    source = tmp_path / "records.csv"
    source.write_text("record_id,report_time\nA1,2026-07-22\n", encoding="utf-8")
    assert ingest_path(source).loc[0, "record_id"] == "A1"


@pytest.mark.parametrize("filename", ["records.xls", "records.json", "records"])
def test_reject_unsupported_extension(filename: str) -> None:
    with pytest.raises(IngestError, match="仅支持"):
        ingest_bytes(b"content", filename)


def test_reject_empty_or_corrupt_file() -> None:
    with pytest.raises(IngestError, match="为空"):
        ingest_bytes(b"", "records.csv")
    with pytest.raises(IngestError, match="损坏"):
        ingest_bytes(b"not an xlsx", "records.xlsx")
