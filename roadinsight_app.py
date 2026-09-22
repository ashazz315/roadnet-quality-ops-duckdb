"""RoadInsight V2 entry point: streamlit run roadinsight_app.py."""

import os
import base64
import hashlib
import time
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
from roadinsight_ui.bundles import decode_bundle, read_bundle
from roadinsight_ui.workspace import Workspace, configuration, unlock, require_editor
from src.evaluation.report import bind_report, read_report

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


@st.cache_resource
def persistent_workspace(directory):
    return Workspace(directory)


try:
    mode, data_directory, editor_token = configuration()
except ValueError as exc:
    st.error(str(exc))
    st.stop()
token_digest = hashlib.sha256(editor_token.encode()).hexdigest()
can_edit = mode == "local" or (bool(editor_token) and st.session_state.get("editor_authorization") == token_digest)
if not can_edit:
    for private_key in ("selected_job", "ui_upload", "ui_saved_upload", "v2_bundle"):
        st.session_state.pop(private_key, None)
    previous_response = st.session_state.get("ui_response")
    if previous_response and previous_response.get("ok") and previous_response.get("action") != "login":
        st.session_state.pop("ui_response", None)
try:
    store = persistent_workspace(str(data_directory)) if can_edit else None
except (OSError, RuntimeError) as exc:
    st.error(str(exc))
    st.stop()

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

selected_job = st.session_state.get("selected_job") if can_edit else None
if selected_job:
    try:
        snapshot = store.snapshot(selected_job)
    except (ValueError, OSError) as exc:
        st.warning(str(exc))
        st.session_state.pop("selected_job", None)
        selected_job = None

review_dir = Path(
    os.environ.get("ROADINSIGHT_REVIEW_DIR", str(ROOT / "data/runtime/ui_reviews"))
)
database_path = Path(
    os.environ.get("ROADINSIGHT_UPLOAD_DB", str(ROOT / "data/road_quality.duckdb"))
)
validation_path = Path(
    os.environ.get(
        "ROADINSIGHT_VALIDATION_REPORT",
        str(ROOT / "data/validation/default_report.json"),
    )
)
validation = None
validation_notice = None
if validation_path.exists() and not selected_job:
    try:
        validation = bind_report(read_report(validation_path), snapshot)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        validation_notice = f"评估报告不可用于当前快照：{exc}"
if selected_job:
    validation_notice = "当前为上传数据的实际分析结果；没有独立标注答案，Precision / Recall 未计算。"

workspace = components.declare_component(
    "workspace", path=str(ROOT / "roadinsight_ui/frontend")
)
value = workspace(
    snapshot=snapshot,
    validation=validation,
    validation_notice=validation_notice,
    reviews=(store.reviews(snapshot) if mode != "local" else read_reviews(snapshot, review_dir)) if can_edit else [],
    access={"mode": "server" if mode == "community" else mode, "can_edit": can_edit,
            "temporary_storage": mode == "community", "login_enabled": bool(editor_token)},
    jobs=store.jobs() if can_edit else [],
    selected_job=selected_job,
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
        if action not in {"navigate", "login", "logout"}:
            require_editor(mode, can_edit)
        if action == "login":
            if time.time() < st.session_state.get("login_retry_after", 0):
                raise ValueError("请稍候再试")
            if not unlock(editor_token, value.get("token")):
                st.session_state.login_retry_after = time.time() + 3
                raise ValueError("编辑访问码不正确")
            st.session_state.editor_authorization = token_digest
            st.session_state.ui_response = {"event_id": value["event_id"], "action": action, "ok": True, "message": "已解锁编辑工作区"}
            st.rerun()
        elif action == "logout":
            for key in ("editor_authorization", "selected_job", "ui_upload", "ui_saved_upload", "v2_bundle", "ui_response"):
                st.session_state.pop(key, None)
            st.rerun()
        elif action == "bundle_upload":
            raw = decode_bundle(value.get("base64"))
            uploaded = read_bundle(raw)
            st.session_state.v2_bundle = raw
            st.session_state.ui_response = {"event_id": value["event_id"], "action": action, "ok": True, "bundle_preview": uploaded["preview"]}
            st.rerun()
        elif action == "run_analysis":
            if "v2_bundle" not in st.session_state:
                raise ValueError("请先上传并校验 V2 数据包")
            with st.spinner("正在运行路网检测和假设回放，请稍候…"):
                job = store.run(st.session_state.v2_bundle, value["event_id"])
            if job["status"] == "completed":
                st.session_state.selected_job = job["id"]
                st.query_params["page"] = "data"
                st.query_params.pop("issue", None)
            st.session_state.ui_response = {"event_id": value["event_id"], "action": action, "ok": job["status"] == "completed", "message": job["message"]}
            st.rerun()
        elif action == "select_job":
            job_id = value.get("job_id")
            if job_id:
                store.snapshot(job_id)
                st.session_state.selected_job = job_id
            else:
                st.session_state.pop("selected_job", None)
            st.query_params["page"] = "data"
            st.query_params.pop("issue", None)
            st.session_state.ui_response = {"event_id": value["event_id"], "action": action, "ok": True}
            st.rerun()
        elif action == "refresh_jobs":
            st.rerun()
        elif action == "download_example":
            st.session_state.ui_response = {"event_id": value["event_id"], "action": action, "ok": True,
                "example_base64": base64.b64encode((ROOT / "data/demo/upload-example.zip").read_bytes()).decode()}
            st.rerun()
        elif action == "navigate":
            page = value.get("page")
            if page in {"dashboard", "map", "detail", "validation", "data"}:
                st.query_params["page"] = page
            issue_id = value.get("issue_id")
            if issue_id in {row["issue_id"] for row in snapshot["issues"]}:
                st.query_params["issue"] = issue_id
        elif action == "review":
            if value.get("snapshot_id") != snapshot["snapshot_id"]:
                raise ValueError("页面数据版本已经变化，请刷新后重新核验")
            if mode != "local":
                store.review(snapshot, value)
            else:
                with store.mutex:
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
            with store.mutex:
                saved = st.session_state.get("ui_saved_upload") or (
                    store.save_legacy(st.session_state.ui_upload) if mode != "local"
                    else save_upload(st.session_state.ui_upload, database_path)
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
