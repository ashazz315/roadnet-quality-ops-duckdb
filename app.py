"""The only supported Streamlit entry point for the minimal runnable version."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from quality_mvp.map_builder import map_html
from quality_mvp.report_builder import build_error_csv
from src.daily_report import build_daily_report_excel, daily_report_name, query_daily_report
from src.ingest import IngestError, ingest_bytes
from src.road_matching import RoadMatchingError, match_records_to_roads
from src.storage import StorageError, persist_batch
from src.validation import REQUIRED_FIELDS, STANDARD_FIELDS, validate_records

st.set_page_config(page_title="路网数据质量最小工作台", page_icon="🛣️", layout="wide")
st.title("路网数据质量最小工作台")
st.caption("上传 UTF-8 CSV 或 XLSX，完成校验、入库、道路匹配、地图核验和日报导出。")

with st.expander("字段要求", expanded=False):
    st.markdown(f"必填字段：`{'`、`'.join(REQUIRED_FIELDS)}`")
    st.markdown(f"标准字段：`{'`、`'.join(STANDARD_FIELDS)}`")

uploaded = st.file_uploader(
    "上传 CSV 或 XLSX 文件",
    type=["csv", "xlsx"],
    accept_multiple_files=False,
)

if uploaded is None:
    st.info("请上传一个 UTF-8 CSV 或 XLSX 文件开始检查。")
    st.stop()

source_name = Path(uploaded.name).name
upload_content = uploaded.getvalue()
try:
    source_frame = ingest_bytes(upload_content, source_name)
    result = validate_records(source_frame)
except (IngestError, TypeError, ValueError) as exc:
    st.error(str(exc))
    st.stop()

valid_records = result.valid_records
rejected_records = result.rejected_records
validation_summary = result.validation_summary
records_for_storage = valid_records
if not valid_records.empty:
    try:
        records_for_storage = match_records_to_roads(valid_records)
    except RoadMatchingError as exc:
        st.warning(f"道路匹配未完成，本批次仍会保存并生成日报：{exc}")

upload_key = sha256(source_name.encode("utf-8") + b"\0" + upload_content).hexdigest()
session_key = f"duckdb_batch_{upload_key}"
try:
    if session_key not in st.session_state:
        st.session_state[session_key] = persist_batch(
            source_name,
            valid_records,
            rejected_records,
            validation_summary,
            records_for_storage=records_for_storage,
        )
    batch_id = st.session_state[session_key]
    report_tables = query_daily_report(batch_id)
except (StorageError, OSError, ValueError, RuntimeError) as exc:
    st.error(str(exc))
    st.stop()

overview = report_tables["data_overview"]
if overview.empty:
    st.error("DuckDB 中未找到当前上传批次。")
    st.stop()
overview_row = overview.iloc[0]

st.subheader("字段与格式检查")
st.write("识别到的字段：", list(source_frame.columns))
if validation_summary["missing_required_columns"]:
    st.error(f"缺少必填字段：{', '.join(validation_summary['missing_required_columns'])}")

metric_columns = st.columns(4)
metric_columns[0].metric("总记录", int(overview_row["总记录数"]))
metric_columns[1].metric("有效记录", int(overview_row["有效记录数"]))
metric_columns[2].metric("错误记录", int(overview_row["错误记录数"]))
metric_columns[3].metric("问题工单", int(overview_row["问题工单数"]))

valid_tab, rejected_tab, map_tab, summary_tab = st.tabs(
    ["有效记录", "错误记录", "地图", "日报指标"]
)
with valid_tab:
    st.dataframe(records_for_storage.drop(columns=["geometry"], errors="ignore"), use_container_width=True, hide_index=True)

with rejected_tab:
    if rejected_records.empty:
        st.success("没有错误记录。")
    else:
        st.dataframe(rejected_records, use_container_width=True, hide_index=True)
    st.download_button(
        "下载错误明细 CSV",
        data=build_error_csv(rejected_records),
        file_name="rejected_records.csv",
        mime="text/csv",
        disabled=rejected_records.empty,
    )

with map_tab:
    if valid_records.empty:
        st.warning("没有可展示的有效记录。")
    else:
        components.html(map_html(records_for_storage), height=620, scrolling=False)

with summary_tab:
    st.markdown("#### 数据概览")
    st.dataframe(overview, use_container_width=True, hide_index=True)
    st.markdown("#### 问题类型统计")
    st.dataframe(report_tables["issue_type_statistics"], use_container_width=True, hide_index=True)
    st.markdown("#### 区域统计")
    st.dataframe(report_tables["region_statistics"], use_container_width=True, hide_index=True)
    st.markdown("#### 高优先级问题")
    st.dataframe(report_tables["high_priority_issues"], use_container_width=True, hide_index=True)

generated_at = datetime.now().astimezone()
report_bytes = build_daily_report_excel(report_tables)
st.download_button(
    "下载日报 Excel",
    data=report_bytes,
    file_name=str(daily_report_name(generated_at)),
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
