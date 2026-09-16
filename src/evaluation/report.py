"""Checksummed result envelopes; safe for a presentation layer to read."""

import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def seal(value):
    return {**value, "report_sha256": hashlib.sha256(canonical(value)).hexdigest()}


def read_report(path):
    report = json.loads(Path(path).read_bytes())
    digest = report.pop("report_sha256")
    if hashlib.sha256(canonical(report)).hexdigest() != digest:
        raise ValueError("Validation report checksum mismatch")
    if report.get("schema_version") != 1 or report.get("status") != "completed":
        raise ValueError("Incomplete validation report")
    return {**report, "report_sha256": digest}


def bind_report(report, snapshot):
    source = report["source"]
    if (
        source["analysis_run_id"] != snapshot["analysis_run"]["run_id"]
        or source["network_version"] != snapshot["network_version"]
        or source["input_content_sha256"] != snapshot["input_content_sha256"]
        or source["analysis_manifest_sha256"]
        != snapshot["provenance"]["analysis_manifest_sha256"]
        or source["replay_manifest_sha256"]
        != snapshot["provenance"]["replay_manifest_sha256"]
    ):
        raise ValueError(
            "Validation report belongs to a different analysis/replay snapshot"
        )
    return report
