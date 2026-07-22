"""Read uploaded UTF-8 CSV and XLSX files into pandas DataFrames."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


class IngestError(ValueError):
    """Raised when an uploaded file cannot be safely parsed."""


def _read_csv(content: bytes) -> pd.DataFrame:
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError("CSV 必须使用 UTF-8 编码。") from exc

    try:
        return pd.read_csv(
            BytesIO(content),
            encoding="utf-8-sig",
            dtype=object,
            keep_default_na=False,
            skip_blank_lines=False,
        )
    except pd.errors.EmptyDataError as exc:
        raise IngestError("CSV 文件为空或缺少表头。") from exc
    except pd.errors.ParserError as exc:
        raise IngestError(f"CSV 格式错误：{exc}") from exc


def _read_xlsx(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(
            BytesIO(content),
            sheet_name=0,
            engine="openpyxl",
            dtype=object,
            keep_default_na=False,
        )
    except (BadZipFile, ValueError, OSError) as exc:
        raise IngestError("XLSX 文件损坏或格式不正确。") from exc


def ingest_bytes(content: bytes, filename: str | Path) -> pd.DataFrame:
    """Read uploaded bytes based on the suffix of ``filename``."""
    source_path = Path(filename)
    extension = source_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise IngestError("仅支持 .csv 和 .xlsx 文件。")
    if not content:
        raise IngestError("上传文件为空。")
    if extension == ".csv":
        return _read_csv(content)
    return _read_xlsx(content)


def ingest_path(path: Path) -> pd.DataFrame:
    """Read a local file using pathlib.Path only."""
    source_path = Path(path)
    if not source_path.is_file():
        raise IngestError(f"文件不存在：{source_path}")
    return ingest_bytes(source_path.read_bytes(), source_path)
