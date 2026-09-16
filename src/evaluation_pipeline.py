"""Read frozen inputs/results and separately verified fault answers after detection."""

import hashlib
import json
import platform
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd

from src.analysis_pipeline import audit_content_hash
from src.data_sources.files import FileDataSource
from src.domain import InputEntity
from src.evaluation.metrics import ALGORITHM_VERSION, TYPES, evaluate, validate_rules
from src.evaluation.report import canonical, read_report, seal
from src.replay_pipeline import load_analysis

ROOT = Path(__file__).resolve().parents[1]


def artifact(directory, manifest, name):
    directory = Path(directory).resolve()
    path = (directory / name).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Evaluation artifact leaves its snapshot directory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["artifacts"][name]:
        raise ValueError(f"Evaluation artifact checksum mismatch: {name}")
    return raw


def quality_report(points, rejected, issues, summary, rules):
    total = len(points)
    reasons = rejected.rejection_reason.fillna("")
    rows = []
    required = [
        "trajectory_id",
        "point_seq",
        "timestamp",
        "longitude",
        "latitude",
        "speed_kmh",
        "heading_deg",
        "vehicle_type",
    ]
    missing = int(points[required].isna().any(axis=1).sum())
    counts = {
        "missing_required_value": missing,
        "duplicate_point_key": int(
            reasons.str.contains("duplicate_point_key", regex=False).sum()
        ),
        "invalid_coordinate": int(
            reasons.str.contains(
                "invalid_coordinate|invalid_longitude|invalid_latitude"
            ).sum()
        ),
        "time_anomaly": int(
            reasons.str.contains(
                "invalid_or_naive_timestamp|non_increasing_timestamp"
            ).sum()
        ),
        "unsupported_speed": int(
            reasons.str.contains("unsupported_speed|invalid_speed_kmh").sum()
        ),
    }
    for name, count in counts.items():
        rows.append(
            {
                "metric": name,
                "count": count,
                "denominator": total,
                "rate": count / total if total else None,
            }
        )
    rate = summary["matching_success_rate"]
    low = sum(i["confidence"] < rules["low_confidence_threshold"] for i in issues)
    return {
        "input_points": total,
        "accepted_points": summary["accepted_trajectory_points"],
        "rejected_points": len(rejected),
        "record_rates": rows,
        "matching_success_rate": rate,
        "matching_warning_threshold": rules["matching_warning_threshold"],
        "matching_status": "not_computed"
        if rate is None
        else "warning"
        if rate < rules["matching_warning_threshold"]
        else "within_threshold",
        "mean_matched_distance_m": summary["mean_matched_distance_m"],
        "p95_matched_distance_m": summary["p95_matched_distance_m"],
        "low_confidence_count": low,
        "low_confidence_threshold": rules["low_confidence_threshold"],
        "low_confidence_rate": low / len(issues) if issues else None,
        "note": "Record reasons may overlap; matched distance is not true location error; confidence is uncalibrated.",
    }


def evaluate_snapshot(
    benchmark_path,
    analysis_path,
    replay_path,
    output,
    rules,
    *,
    code_version,
    suite_path=None,
):
    validate_rules(rules)
    output = Path(output)
    if output.exists():
        raise FileExistsError("Refusing to overwrite evaluation")
    clock = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    benchmark_path, analysis_path, replay_path = map(
        Path, (benchmark_path, analysis_path, replay_path)
    )
    benchmark = json.loads(benchmark_path.read_bytes())
    if (
        benchmark["run"]["status"] != "completed"
        or not benchmark["summary"]["is_synthetic"]
    ):
        raise ValueError("Expected completed synthetic benchmark")
    for name in benchmark["artifacts"]:
        artifact(benchmark_path.parent, benchmark, name)
    source = FileDataSource(benchmark_path.parent / "inputs/manifest.json")
    frames = {e.value: source.read(e) for e in InputEntity}
    hashes = {name: audit_content_hash(frame) for name, frame in frames.items()}
    sources = {e.value: asdict(source.metadata(e)) for e in InputEntity}
    issue_frame, analysis = load_analysis(analysis_path, hashes, sources)
    if analysis["run"]["network_version"] != benchmark["run"]["network_version"]:
        raise ValueError("Benchmark and analysis network mismatch")
    faults = json.loads(artifact(benchmark_path.parent, benchmark, "truth/faults.json"))
    mapping = json.loads(
        artifact(benchmark_path.parent, benchmark, "truth/id_mapping.json")
    )
    if len(faults) != benchmark["summary"]["faults"] or any(
        f["seed"] != benchmark["run"]["seed"] for f in faults
    ):
        raise ValueError("Fault answers do not match benchmark metadata")
    issues = json.loads(issue_frame.to_json(orient="records", double_precision=15))
    for issue in issues:
        issue["evidence_summary"] = json.loads(issue.pop("evidence_summary_json"))
    metrics = evaluate(issues, faults, mapping, rules)
    rejected = pd.read_parquet(
        BytesIO(
            artifact(
                analysis_path.parent, analysis, "rejected_trajectory_point.parquet"
            )
        )
    )
    replay = json.loads(replay_path.read_bytes())
    analysis_sha = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
    if (
        replay["run"]["status"] != "completed"
        or replay["input_content_sha256"] != hashes
        or replay["source_analysis"]["manifest_sha256"] != analysis_sha
        or replay["source_analysis"]["run_id"] != analysis["run"]["run_id"]
    ):
        raise ValueError("Replay does not belong to evaluated analysis")
    for name in replay["artifacts"]:
        artifact(replay_path.parent, replay, name)
    routes = pd.read_parquet(
        BytesIO(artifact(replay_path.parent, replay, "route_replay.parquet"))
    )
    if set(routes.issue_id) - {i["issue_id"] for i in issues}:
        raise ValueError("Replay issue references mismatch")
    no_path = routes.before_distance_m.isna() | routes.after_distance_m.isna()
    checks = {
        "hypothetical_only": bool(routes.is_hypothetical.all()),
        "unverified_only": bool(
            routes.verification_status.eq("not_field_verified").all()
        ),
        "no_path_deltas_null": bool(
            routes.loc[no_path, ["distance_delta_m", "eta_delta_s"]].isna().all().all()
        ),
        "original_issues_unverified": bool(issue_frame.status.eq("needs_review").all()),
        "minimum_three_faults_each_type": all(
            metrics["by_type"][t]["injected"] >= 3 for t in TYPES
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Validation invariant failed: {checks}")
    code_paths = sorted((ROOT / "src/evaluation").glob("*.py")) + [Path(__file__)]
    report = {
        "schema_version": 1,
        "status": "completed",
        "run_id": str(uuid.uuid4()),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - clock, 6),
        "algorithm_version": ALGORITHM_VERSION,
        "code_version": code_version,
        "code_sha256": hashlib.sha256(
            canonical(
                {
                    p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf8")
                    for p in code_paths
                }
            )
        ).hexdigest(),
        "rules": rules,
        "rules_version": hashlib.sha256(canonical(rules)).hexdigest(),
        "environment": analysis["run"]["environment"],
        "evaluator_environment": {
            "python": platform.python_version(),
            "system": platform.system(),
        },
        "source": {
            "network_version": analysis["run"]["network_version"],
            "seed": benchmark["run"]["seed"],
            "analysis_run_id": analysis["run"]["run_id"],
            "analysis_id": analysis["run"]["analysis_id"],
            "analysis_manifest_sha256": analysis_sha,
            "input_content_sha256": hashes,
            "benchmark_manifest_sha256": hashlib.sha256(
                benchmark_path.read_bytes()
            ).hexdigest(),
            "truth_sha256": {
                k: v
                for k, v in benchmark["artifacts"].items()
                if k.startswith("truth/")
            },
            "replay_manifest_sha256": hashlib.sha256(
                replay_path.read_bytes()
            ).hexdigest(),
            "analysis_result_hashes": analysis[
                "result_content_sha256_excluding_run_fields"
            ],
            "replay_result_hashes": replay["result_content_sha256"],
        },
        "metrics": metrics,
        "data_quality": quality_report(
            frames["trajectory_point"], rejected, issues, analysis["summary"], rules
        ),
        "checks": checks,
        "replay_summary": replay["summary"],
        "reproducibility": {"status": "not_run"},
        "limitations": [
            "Synthetic seed benchmark, not held-out or real-world accuracy.",
            "Unmatched alerts are benchmark FP; this does not prove real roads are correct.",
            "TN and FPR are undefined without a finite negative universe.",
            "Geometry thresholds affect missing-road matching; configuration is versioned.",
            "No detector thresholds were tuned by the evaluator; confidence remains uncalibrated.",
            "Replay checks verify artifacts and invariants, not field repair correctness.",
        ],
    }
    if suite_path:
        suite = read_report(suite_path)
        if (
            not suite["reproducible"]
            or not all(suite["checks"].values())
            or suite["input_content_sha256"] != hashes
            or suite["analysis_result_hashes"]
            != report["source"]["analysis_result_hashes"]
            or suite["replay_result_hashes"] != report["source"]["replay_result_hashes"]
            or suite["rules_version"] != report["rules_version"]
            or suite["evaluation_code_sha256"] != report["code_sha256"]
            or suite["metrics"] != metrics
        ):
            raise ValueError(
                "Repeat suite does not verify these exact results and evaluation rules"
            )
        report["reproducibility"] = {
            "status": "passed",
            "suite_sha256": suite["report_sha256"],
            "checks": suite["checks"],
            "run_ids": suite["run_ids"],
            "environment": suite["environment"],
        }
        report["performance"] = {
            "timings": suite["timings"],
            "scope": suite["timing_scope"],
            "machine": suite["machine"],
            "memory_measurement": suite["memory_measurement"],
        }
    report = seal(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(report))
    return report
