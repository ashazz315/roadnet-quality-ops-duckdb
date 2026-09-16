"""One-command offline benchmark, repeated analysis/replay/evaluation and timings.

Each stage uses a fresh process. Failures remain in logs; no silent retries.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.report import canonical, read_report, seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    samples = {}
    runs = []
    environment = dict(
        os.environ,
        PYTHONUTF8="1",
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="safe.directory",
        GIT_CONFIG_VALUE_0=ROOT.as_posix(),
    )

    def stage(script, name, *arguments):
        clock = time.perf_counter()
        log = output / (name + ".log")
        with log.open("wb") as stream:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script), *map(str, arguments)],
                cwd=ROOT,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        duration = time.perf_counter() - clock
        if result.returncode:
            (output / "failure.json").write_bytes(
                canonical(
                    {
                        "stage": name,
                        "exit_code": result.returncode,
                        "log": log.name,
                        "elapsed_seconds": duration,
                    }
                )
            )
            raise RuntimeError(
                f"Stage {name} failed ({result.returncode}); inspect {log}"
            )
        samples.setdefault(script, []).append(round(duration, 6))
        print(f"{name}: {duration:.2f}s", flush=True)

    stage("04_build_golden.py", "golden", "--output", output / "golden")
    for number in (1, 2):
        base = output / f"run{number}"
        benchmark, analysis, replay = [
            base / name for name in ("benchmark", "analysis", "replay")
        ]
        stage(
            "05_build_benchmark.py",
            f"benchmark{number}",
            "--golden",
            output / "golden/manifest.json",
            "--output",
            benchmark,
        )
        stage(
            "06_analyze.py",
            f"analysis{number}",
            "--inputs",
            benchmark / "inputs/manifest.json",
            "--output",
            analysis,
        )
        stage(
            "07_replay.py",
            f"replay{number}",
            "--inputs",
            benchmark / "inputs/manifest.json",
            "--analysis",
            analysis / "manifest.json",
            "--output",
            replay,
        )
        stage(
            "09_evaluate.py",
            f"evaluate{number}",
            "--benchmark",
            benchmark / "manifest.json",
            "--analysis",
            analysis / "manifest.json",
            "--replay",
            replay / "manifest.json",
            "--output",
            base / "evaluation.json",
        )
        runs.append(
            {
                "benchmark": json.loads((benchmark / "manifest.json").read_bytes()),
                "analysis": json.loads((analysis / "manifest.json").read_bytes()),
                "replay": json.loads((replay / "manifest.json").read_bytes()),
                "evaluation": read_report(base / "evaluation.json"),
            }
        )
    first, second = runs
    checks = {
        "faults_and_inputs_byte_identical": first["benchmark"]["artifacts"]
        == second["benchmark"]["artifacts"],
        "analysis_results_identical": first["analysis"][
            "result_content_sha256_excluding_run_fields"
        ]
        == second["analysis"]["result_content_sha256_excluding_run_fields"],
        "replay_results_identical": first["replay"]["result_content_sha256"]
        == second["replay"]["result_content_sha256"],
        "evaluation_assignments_and_metrics_identical": first["evaluation"]["metrics"]
        == second["evaluation"]["metrics"],
        "evaluation_rules_identical": first["evaluation"]["rules_version"]
        == second["evaluation"]["rules_version"],
    }
    report = {
        "schema_version": 1,
        "status": "completed",
        "checks": checks,
        "reproducible": all(checks.values()),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "environment": first["analysis"]["run"]["environment"],
        "machine": {"cpu": platform.processor(), "logical_cpus": os.cpu_count()},
        "input_content_sha256": first["analysis"]["input_content_sha256"],
        "analysis_result_hashes": first["analysis"][
            "result_content_sha256_excluding_run_fields"
        ],
        "replay_result_hashes": first["replay"]["result_content_sha256"],
        "rules_version": first["evaluation"]["rules_version"],
        "evaluation_code_sha256": first["evaluation"]["code_sha256"],
        "metrics": first["evaluation"]["metrics"],
        "run_ids": [
            {
                name: value["run"]["run_id"]
                if name != "evaluation"
                else value["run_id"]
                for name, value in run.items()
            }
            for run in runs
        ],
        "run_manifest_sha256": {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.glob("run*/*/manifest.json")
        },
        "timings": {
            name: {
                "samples_seconds": values,
                "median_seconds": statistics.median(values),
                "min_seconds": min(values),
                "max_seconds": max(values),
            }
            for name, values in samples.items()
        },
        "timing_scope": "local_serial_wall_clock_including_process_start_and_export; two runs; not a throughput/SLA/load test",
        "memory_measurement": "not_measured",
        "source_file": "scripts/09_validate.py",
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "suite.json").write_bytes(canonical(seal(report)))
    if not all(checks.values()):
        raise RuntimeError(f"Reproducibility failure: {checks}")
    print(
        json.dumps(
            {"checks": checks, "metrics": report["metrics"]["overall"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
