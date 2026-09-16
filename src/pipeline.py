"""Offline composition of source verification, normalization, graph checks and output."""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from src.data_sources.base import SourceMetadata
from src.data_sources.osm import canonical_json, load_raw_snapshot
from src.data_sources.snapshot import write_snapshot
from src.domain import InputEntity
from src.network.graph_builder import build_graph, graph_summary
from src.network.normalization import ALGORITHM_VERSION, normalize_osm
from src.network.validation import validate_network

ROOT = Path(__file__).resolve().parents[1]


def build_golden_snapshot(raw_directory: Path, output: Path, *, code_version: str) -> dict:
    """Create a controlled baseline, not a claim of real-world map correctness."""
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing snapshot: {output}")
    started = datetime.now(timezone.utc)
    payload, source = load_raw_snapshot(raw_directory)
    config = source["config"]
    parameters = {key: config[key] for key in ("bbox", "highway_classes", "metric_crs", "profile")}
    code_files = [
        "src/network/normalization.py", "src/network/validation.py", "src/network/graph_builder.py",
        "src/data_sources/frames.py", "src/data_sources/snapshot.py", "src/pipeline.py",
        "sql/v2_network_schema.sql",
    ]
    code_hash = hashlib.sha256(canonical_json({name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest() for name in code_files})).hexdigest()
    identity = {"raw_sha256": source["raw_sha256"], "algorithm_version": ALGORITHM_VERSION, "code_sha256": code_hash, "parameters": parameters}
    network_version = "xuhui-" + hashlib.sha256(canonical_json(identity)).hexdigest()[:20]
    tables = normalize_osm(payload, config, network_version)
    counts = validate_network(tables)
    graph = build_graph(tables)
    summary = {
        **counts, "graph": graph_summary(graph),
        "input_counts": dict(sorted(Counter(item["type"] for item in payload["elements"]).items())),
        "audit_reasons": dict(sorted(Counter(row["reason"] for row in tables.audit).items())),
        "boundary_nodes": sum(row["is_boundary"] for row in tables.road_node),
        "routing_ineligible_segments": sum(not row["routing_eligible"] for row in tables.road_segment),
        "routing_ineligible_nodes": sum(not row["routing_eligible"] for row in tables.road_node),
        "osm_timestamp": source["osm_timestamp"], "retrieved_at": source["retrieved_at"],
        "is_ground_truth": False, "license": source["license"], "attribution": source["attribution"],
    }
    metadata = SourceMetadata(
        source_ref=f"{source['endpoint']}#sha256={source['raw_sha256']}", data_version=network_version,
        network_version=network_version, is_synthetic=source["is_synthetic"],
    )
    environment = {name: version(name) for name in ("pandas", "geopandas", "shapely", "pyproj", "pyarrow", "duckdb", "networkx")}
    environment["python"] = sys.version.split()[0]
    run = {
        "run_id": str(uuid4()), "data_version": network_version, "network_version": network_version,
        "query_version": config["query_version"], "algorithm_version": ALGORITHM_VERSION,
        "code_version": code_version, "code_sha256": code_hash,
        "rules_version": hashlib.sha256(canonical_json(parameters)).hexdigest(),
        "parameter_json": canonical_json(parameters).decode(), "environment_json": canonical_json(environment).decode(),
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed", "seed": None,
    }
    rows = {InputEntity.ROAD_SEGMENT: tables.road_segment, InputEntity.ROAD_NODE: tables.road_node, InputEntity.TURN_RESTRICTION: tables.turn_restriction}
    return write_snapshot(output, rows, tables.audit, metadata, run, summary, ROOT / "sql/v2_network_schema.sql")
