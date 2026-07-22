"""Downloadable CSV and daily Excel report generation."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def build_error_csv(errors: pd.DataFrame) -> bytes:
    """Return an Excel-friendly UTF-8 CSV containing error details."""
    return errors.to_csv(index=False).encode("utf-8-sig")


def daily_report_name(generated_at: datetime) -> Path:
    return Path(f"road_quality_daily_{generated_at:%Y%m%d}.xlsx")


def _autosize_worksheet(worksheet) -> None:
    for column_cells in worksheet.columns:
        width = min(max(len(str(cell.value or "")) for cell in column_cells) + 2, 45)
        worksheet.column_dimensions[get_column_letter(column_cells[0].column)].width = width
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for cell in worksheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="1F4E79")


def build_daily_report(
    valid: pd.DataFrame,
    errors: pd.DataFrame,
    source_name: str,
    generated_at: datetime,
) -> bytes:
    """Build a verified, reusable workbook with summary and record details."""
    total = len(valid) + len(errors)
    summary = pd.DataFrame(
        [
            {"指标": "数据源", "值": source_name},
            {"指标": "生成时间", "值": generated_at.isoformat(timespec="seconds")},
            {"指标": "总记录数", "值": total},
            {"指标": "有效记录数", "值": len(valid)},
            {"指标": "错误记录数", "值": len(errors)},
            {"指标": "有效率", "值": round(len(valid) / total, 4) if total else 0},
        ]
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="日报汇总", index=False)
        valid.to_excel(writer, sheet_name="有效记录", index=False)
        errors.to_excel(writer, sheet_name="错误记录", index=False)
        for worksheet in writer.book.worksheets:
            _autosize_worksheet(worksheet)
    return output.getvalue()
