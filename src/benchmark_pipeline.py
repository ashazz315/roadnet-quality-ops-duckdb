"""STEP 5 composition: validated source -> pure simulation -> isolated snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import random
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path

import pandas as pd
from pyproj import CRS, proj_version_str
from shapely import geos_version_string

from src.benchmark.fault_injection import inject_faults, select_faults
from src.benchmark.isolation import NETWORK_COLUMNS, OBSERVATION_COLUMNS, isolate_inputs
from src.benchmark.movement import MovementModel
from src.benchmark.observations import feedback, trajectories
from src.data_sources.files import FileDataSource
from src.data_sources.frames import content_hash, spatial_frame
from src.domain import InputEntity
from src.network.normalization import NetworkTables
from src.network.validation import validate_network

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_VERSION = "roadinsight-synthetic-benchmark-v1"
NETWORK_ENTITIES = (
    InputEntity.ROAD_SEGMENT,
    InputEntity.ROAD_NODE,
    InputEntity.TURN_RESTRICTION,
)


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def validate_config(config: dict) -> None:
    defaults = json.loads((ROOT / "config/benchmark.json").read_bytes())
    if set(config) != set(defaults):
        raise ValueError("Benchmark config must have exactly the documented fields")
    for key in (
        "seed",
        "faults_per_type",
        "target_point_count",
        "target_paths_per_fault",
        "feedback_count",
        "supporting_feedback_count",
    ):
        if type(config[key]) is not int or config[key] < (0 if key == "seed" else 1):
            raise ValueError(f"Invalid integer: {key}")
    for key in (
        "feedback_fault_coverage",
        "sample_interval_seconds",
        "gps_sigma_m",
        "gps_max_m",
        "gps_outlier_fraction",
        "connectivity_gap_m",
        "minimum_target_length_m",
        "duration_hours",
    ):
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            if (
                key != "gps_outlier_fraction"
                or type(value) not in (int, float)
                or value != 0
            ):
                raise ValueError(f"Invalid positive finite number: {key}")
    for key in ("speed_kmh_range", "gps_outlier_range_m", "path_edges_range"):
        values = config[key]
        if (
            not isinstance(values, list)
            or len(values) != 2
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or v <= 0
                for v in values
            )
            or values[0] > values[1]
        ):
            raise ValueError(f"Invalid range: {key}")
    if (
        any(type(v) is not int for v in config["path_edges_range"])
        or config["path_edges_range"][0] < 8
    ):
        raise ValueError("Path edge counts must be integers >= 8")
    if (
        not 0 < config["feedback_fault_coverage"] <= 1
        or not 0 <= config["gps_outlier_fraction"] <= 1
    ):
        raise ValueError("Coverage and outlier fractions must be within [0, 1]")
    covered = max(
        1, round(4 * config["faults_per_type"] * config["feedback_fault_coverage"])
    )
    if not covered <= config["supporting_feedback_count"] < config["feedback_count"]:
        raise ValueError(
            "Feedback counts must cover selected faults and leave distractors"
        )
    if config["minimum_target_length_m"] <= 2 * config["connectivity_gap_m"]:
        raise ValueError("Target length must exceed twice the connectivity gap")
    if config["gps_outlier_range_m"][0] <= config["gps_max_m"]:
        raise ValueError("Outlier noise must exceed normal noise")
    start = datetime.fromisoformat(config["start_time"])
    if start.utcoffset() is None or start.utcoffset().total_seconds() != 0:
        raise ValueError("start_time must be UTC with an explicit timezone")
    crs = CRS.from_user_input(config["metric_crs"])
    if not crs.is_projected or any(axis.unit_name != "metre" for axis in crs.axis_info):
        raise ValueError("metric_crs must be projected with metre units")


def generator_hash() -> str:
    paths = sorted((ROOT / "src/benchmark").glob("*.py")) + [
        Path(__file__),
        ROOT / "src/simulation.py",
        ROOT / "src/network/graph_builder.py",
        ROOT / "src/network/normalization.py",
        ROOT / "src/network/validation.py",
        ROOT / "src/data_sources/frames.py",
    ]
    return hashlib.sha256(
        canonical(
            {
                path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
                for path in paths
            }
        )
    ).hexdigest()


def build_benchmark(
    golden_manifest: Path, output: Path, config: dict, *, code_version: str
) -> dict:
    validate_config(config)
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Benchmark: {output}")
    started = datetime.now(timezone.utc).isoformat()
    source = FileDataSource(golden_manifest)
    frames = {
        entity.value: source.read(entity).drop(columns="geometry", errors="ignore")
        for entity in NETWORK_ENTITIES
    }
    metadata = {entity.value: source.metadata(entity) for entity in NETWORK_ENTITIES}
    if (
        any(item.is_synthetic for item in metadata.values())
        or len({item.network_version for item in metadata.values()}) != 1
    ):
        raise ValueError("Expected one non-synthetic Golden network version")
    golden = NetworkTables(
        **{key: frame.to_dict("records") for key, frame in frames.items()}, audit=[]
    )
    validate_network(golden)
    golden_version = metadata["road_segment"].network_version
    if any(
        not frame["network_version"].eq(golden_version).all()
        for frame in frames.values()
    ):
        raise ValueError("Golden table versions do not match source metadata")
    hashes = {key: content_hash(frame) for key, frame in frames.items()}
    code_hash = generator_hash()
    version = (
        "benchmark-"
        + hashlib.sha256(
            canonical(
                {
                    "golden": golden_version,
                    "tables": hashes,
                    "config": config,
                    "code": code_hash,
                    "algorithm": ALGORITHM_VERSION,
                }
            )
        ).hexdigest()[:20]
    )
    model = MovementModel(golden)
    selections = select_faults(golden, model, config, random.Random(config["seed"]))
    corrupted, faults = inject_faults(golden, selections, config, version)
    points, point_truth, paths = trajectories(model, faults, config)
    reports, report_truth = feedback(golden, model, faults, config)
    isolated, mapping = isolate_inputs(golden, corrupted, faults, version)
    inputs = {
        entity: pd.DataFrame(getattr(isolated, entity), columns=columns)
        for entity, columns in NETWORK_COLUMNS.items()
    }
    for name, frame in {"trajectory_point": points, "user_feedback": reports}.items():
        if set(frame) != set(OBSERVATION_COLUMNS[name]):
            raise ValueError(f"Unexpected observation fields: {name}")
        inputs[name] = frame.loc[:, list(OBSERVATION_COLUMNS[name])].copy()
    for frame in inputs.values():
        frame["network_version"] = version
        frame["is_synthetic"] = True
    output.mkdir(parents=True, exist_ok=False)
    (output / "inputs").mkdir()
    (output / "truth").mkdir()
    entries = {}
    for name, frame in inputs.items():
        frame = spatial_frame(frame, InputEntity(name))
        path = output / "inputs" / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        entries[name] = {
            "path": path.name,
            "format": "geoparquet"
            if name in {"road_segment", "road_node"}
            else "parquet",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_sha256": content_hash(frame),
            "row_count": len(frame),
            "metadata": {
                "source_ref": f"synthetic-benchmark:{version}",
                "data_version": version,
                "network_version": version,
                "is_synthetic": True,
            },
        }
    (output / "inputs/manifest.json").write_bytes(
        canonical({"schema_version": 1, "entities": entries})
    )
    for name, value in {"faults": faults, "id_mapping": mapping}.items():
        (output / "truth" / f"{name}.json").write_bytes(canonical(value))
    for name, frame in {
        "point_truth": point_truth,
        "trajectory_paths": paths,
        "feedback_truth": report_truth,
    }.items():
        frame.to_parquet(output / "truth" / f"{name}.parquet", index=False)
    # The regular adapter must accept all five tables without any answer access.
    verified = FileDataSource(output / "inputs/manifest.json")
    for entity in InputEntity:
        if (
            content_hash(verified.read(entity))
            != entries[entity.value]["content_sha256"]
        ):
            raise ValueError(f"Exported input differs from generated frame: {entity}")
    summary = {
        "faults": len(faults),
        "faults_per_type": dict(
            sorted(Counter(fault["issue_type"] for fault in faults).items())
        ),
        "input_rows": {name: len(frame) for name, frame in inputs.items()},
        "trajectories": len(paths),
        "gps_outliers": int(point_truth["is_outlier"].sum()),
        "targeted_paths": int(paths["target_fault_id"].notna().sum()),
        "supporting_feedback": int(report_truth["is_supporting"].sum()),
        "distractor_feedback": int((~report_truth["is_supporting"]).sum()),
        "faults_with_feedback": int(report_truth["fault_id"].nunique()),
        "is_synthetic": True,
        "is_real_world_validation": False,
    }
    artifacts = {
        path.relative_to(output).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "run": {
            "run_id": str(uuid.uuid4()),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "code_version": code_version,
            "code_sha256": code_hash,
            "algorithm_version": ALGORITHM_VERSION,
            "seed": config["seed"],
            "parameters": config,
            "network_version": version,
            "golden_network_version": golden_version,
            "golden_content_sha256": hashes,
            "golden_sources": {name: asdict(value) for name, value in metadata.items()},
            "golden_manifest_sha256": hashlib.sha256(
                Path(golden_manifest).read_bytes()
            ).hexdigest(),
            "environment": {
                "python": platform.python_version(),
                "system": platform.system(),
                "machine": platform.machine(),
                "proj": proj_version_str,
                "geos": geos_version_string,
                **{
                    name: package_version(name)
                    for name in (
                        "pandas",
                        "pyarrow",
                        "geopandas",
                        "networkx",
                        "pyproj",
                        "shapely",
                        "numpy",
                    )
                },
            },
        },
        "summary": summary,
        "artifacts": artifacts,
        "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors; RoadInsight synthetic modifications and observations",
    }
    # Final manifest is the completion marker; a failed partial directory is not reusable.
    (output / "manifest.json").write_bytes(canonical(manifest))
    return manifest
