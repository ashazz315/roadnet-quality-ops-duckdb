"""Explicit user actions; source analysis and network snapshots are never edited."""

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.daily_report import build_daily_report_excel, query_daily_report
from src.ingest import ingest_bytes
from src.road_matching import match_records_to_roads
from src.storage import persist_batch
from src.validation import validate_records

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def review_path(snapshot, directory):
    token = hashlib.sha256(snapshot["analysis_run"]["run_id"].encode()).hexdigest()[:24]
    return Path(directory) / (token + ".jsonl")


def read_reviews(snapshot, directory):
    path = review_path(snapshot, directory)
    return (
        [
            json.loads(line)
            for line in path.read_text(encoding="utf8").splitlines()
            if line.strip()
        ]
        if path.exists()
        else []
    )


def record_review(snapshot, issue_id, conclusion, reviewer, note, directory, event_id):
    if issue_id not in {row["issue_id"] for row in snapshot["issues"]}:
        raise ValueError("找不到当前批次中的问题")
    if (
        conclusion not in {"confirmed", "rejected", "needs_review"}
        or not 2 <= len(reviewer.strip()) <= 80
        or not 5 <= len(note.strip()) <= 2000
    ):
        raise ValueError("请填写核验人、结论和至少 5 字的核验依据")
    path = review_path(snapshot, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "review_id": hashlib.sha256(
            (snapshot["snapshot_id"] + event_id).encode()
        ).hexdigest()[:24],
        "issue_id": issue_id,
        "analysis_run_id": snapshot["analysis_run"]["run_id"],
        "network_version": snapshot["network_version"],
        "conclusion": conclusion,
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "record_kind": "user_asserted_manual_review",
    }
    if any(
        old["review_id"] == row["review_id"]
        for old in read_reviews(snapshot, directory)
    ):
        return row
    with path.open("a", encoding="utf8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def validate_upload(filename, encoded):
    if not isinstance(encoded, str) or len(encoded) > (MAX_UPLOAD_BYTES * 4 // 3 + 8):
        raise ValueError("文件不得超过 5 MB")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("文件不得超过 5 MB")
    frame = ingest_bytes(raw, Path(filename).name)
    result = validate_records(frame)
    return {
        "filename": Path(filename).name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "result": result,
        "preview": {
            "filename": Path(filename).name,
            "summary": result.validation_summary,
            "valid_rows": json.loads(
                result.valid_records.head(12).to_json(
                    orient="records", date_format="iso"
                )
            ),
            "rejected_rows": json.loads(
                result.rejected_records.head(12).to_json(
                    orient="records", date_format="iso"
                )
            ),
        },
    }


def save_upload(upload, database_path):
    result = upload["result"]
    if result.validation_summary["missing_required_columns"]:
        raise ValueError("缺少必填列，请修正文件后再保存")
    matched, matching_note = result.valid_records, None
    if not matched.empty:
        try:
            matched = match_records_to_roads(matched)
        except ValueError as exc:
            matching_note = str(exc)
    batch_id = persist_batch(
        upload["filename"],
        result.valid_records,
        result.rejected_records,
        result.validation_summary,
        records_for_storage=matched,
        database_path=database_path,
    )
    excel = build_daily_report_excel(
        query_daily_report(batch_id, database_path=database_path)
    )
    return {
        "batch_id": batch_id,
        "matching_note": matching_note,
        "report_base64": base64.b64encode(excel).decode(),
        "filename": "roadinsight-upload-report.xlsx",
    }
