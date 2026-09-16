"""RoadInsight V2 entry point: streamlit run roadinsight_app.py."""

import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from roadinsight_ui.actions import (
    read_reviews,
    record_review,
    save_upload,
    validate_upload,
)
from roadinsight_ui.snapshot import DEFAULT_SNAPSHOT, load_snapshot

ROOT = Path(__file__).resolve().parent
st.set_page_config(
    page_title="RoadInsight · 路网数据质量诊断",
    page_icon="🛣️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """<style>
header[data-testid="stHeader"],footer,#MainMenu,[data-testid="stToolbar"],.stAppDeployButton{display:none!important}
[data-testid="stMainBlockContainer"]{padding:0!important;max-width:100%!important}
[data-testid="stMain"]{overflow:hidden} .stMainBlockContainer>div{gap:0!important}
iframe[title="roadinsight_app.workspace"]{display:block;border:0;width:100%}
</style>""",
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def snapshot_bytes(path, modified):
    return load_snapshot(Path(path))


snapshot_path = Path(os.environ.get("ROADINSIGHT_UI_SNAPSHOT", str(DEFAULT_SNAPSHOT)))
try:
    snapshot = snapshot_bytes(
        str(snapshot_path),
        (
            snapshot_path.stat().st_mtime_ns,
            snapshot_path.with_suffix(".manifest.json").stat().st_mtime_ns,
        ),
    )
except (OSError, ValueError, KeyError) as exc:
    st.error(f"演示数据无法载入：{exc}")
    st.info("请按 docs/UI_GUIDE.md 生成并配置已验证的页面数据快照。")
    st.stop()

review_dir = Path(
    os.environ.get("ROADINSIGHT_REVIEW_DIR", str(ROOT / "data/runtime/ui_reviews"))
)
database_path = Path(
    os.environ.get("ROADINSIGHT_UPLOAD_DB", str(ROOT / "data/road_quality.duckdb"))
)
workspace = components.declare_component(
    "workspace", path=str(ROOT / "roadinsight_ui/frontend")
)
value = workspace(
    snapshot=snapshot,
    reviews=read_reviews(snapshot, review_dir),
    response=st.session_state.get("ui_response"),
    initial_page=st.query_params.get("page", "dashboard"),
    initial_issue=st.query_params.get("issue", ""),
    key="roadinsight-workspace",
    default=None,
)
if (
    isinstance(value, dict)
    and value.get("event_id")
    and value["event_id"] != st.session_state.get("ui_last_event")
):
    st.session_state.ui_last_event = value["event_id"]
    action = value.get("action")
    try:
        if action == "navigate":
            page = value.get("page")
            if page in {"dashboard", "map", "detail", "validation", "data"}:
                st.query_params["page"] = page
            issue_id = value.get("issue_id")
            if issue_id in {row["issue_id"] for row in snapshot["issues"]}:
                st.query_params["issue"] = issue_id
        elif action == "review":
            record_review(
                snapshot,
                value.get("issue_id"),
                value.get("conclusion"),
                value.get("reviewer", ""),
                value.get("note", ""),
                review_dir,
                value["event_id"],
            )
            st.session_state.ui_response = {
                "event_id": value["event_id"],
                "action": action,
                "ok": True,
                "message": "人工核验已单独记录，原始诊断与假设回放保持不变。",
            }
            st.rerun()
        elif action == "upload":
            uploaded = validate_upload(
                value.get("filename", ""), value.get("base64", "")
            )
            st.session_state.ui_upload = uploaded
            st.session_state.ui_saved_upload = None
            st.session_state.ui_response = {
                "event_id": value["event_id"],
                "action": action,
                "ok": True,
                "preview": uploaded["preview"],
            }
            st.rerun()
        elif action == "save_upload":
            if "ui_upload" not in st.session_state:
                raise ValueError("请先上传并校验文件")
            saved = st.session_state.get("ui_saved_upload") or save_upload(
                st.session_state.ui_upload, database_path
            )
            st.session_state.ui_saved_upload = saved
            st.session_state.ui_response = {
                "event_id": value["event_id"],
                "action": action,
                "ok": True,
                **saved,
            }
            st.rerun()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        st.session_state.ui_response = {
            "event_id": value["event_id"],
            "action": action,
            "ok": False,
            "message": str(exc),
        }
        st.rerun()
