"""CSV decoding and parsing without Streamlit dependencies."""

from __future__ import annotations

from io import BytesIO

import pandas as pd


class CsvReadError(ValueError):
    """Raised when uploaded bytes cannot be parsed as a usable CSV file."""


SUPPORTED_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    """Parse CSV bytes, trying common Chinese and UTF-8 encodings."""
    if not content:
        raise CsvReadError("CSV 文件为空。")

    last_error: Exception | None = None
    for encoding in SUPPORTED_ENCODINGS:
        try:
            frame = pd.read_csv(BytesIO(content), encoding=encoding, dtype=object)
            if frame.columns.empty:
                raise CsvReadError("CSV 文件缺少表头。")
            return frame
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.EmptyDataError as exc:
            raise CsvReadError("CSV 文件为空或缺少表头。") from exc
        except pd.errors.ParserError as exc:
            raise CsvReadError(f"CSV 格式错误：{exc}") from exc

    raise CsvReadError("无法识别 CSV 编码，请保存为 UTF-8 或 GB18030。") from last_error
