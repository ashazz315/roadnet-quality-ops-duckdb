"""Write a new V2 snapshot once; never migrate or mutate existing databases."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd

from src.data_sources.base import SourceMetadata
from src.data_sources.frames import content_hash, spatial_frame
from src.data_sources.osm import canonical_json
from src.domain import InputEntity

RESTRICTION_COLUMNS = [
    "restriction_id", "osm_relation_id", "from_segment_id", "via_node_id", "to_segment_id",
    "from_edge_id", "to_edge_id", "restriction_type", "tags_json", "network_version",
]


def write_snapshot(
    output: Path, rows: dict[InputEntity, list[dict]], audit: list[dict],
    metadata: SourceMetadata, run: dict, summary: dict, schema_path: Path,
) -> dict:
    """Only a completed directory with manifest.json is a readable file snapshot."""
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing snapshot: {output}")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "entities": {}, "run": run, "summary": summary}
    database_path = output / "network.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(schema_path.read_text(encoding="utf-8"))
            for entity in (InputEntity.ROAD_NODE, InputEntity.ROAD_SEGMENT, InputEntity.TURN_RESTRICTION):
                frame = pd.DataFrame(rows[entity], columns=RESTRICTION_COLUMNS if entity == InputEntity.TURN_RESTRICTION else None)
                if entity == InputEntity.ROAD_SEGMENT:
                    frame["lanes"] = frame["lanes"].astype("Int64")
                    frame["maxspeed"] = frame["maxspeed"].astype("Float64")
                connection.register("input_frame", frame)
                connection.execute(f'INSERT INTO "{entity.value}" BY NAME SELECT * FROM input_frame')
                connection.unregister("input_frame")
                spatial = spatial_frame(frame, entity)
                path = output / f"{entity.value}.parquet"
                spatial.to_parquet(path, index=False)
                entry = {
                    "path": path.name, "format": "geoparquet" if isinstance(spatial, gpd.GeoDataFrame) else "parquet",
                    "row_count": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "content_sha256": content_hash(frame), "metadata": asdict(metadata),
                }
                manifest["entities"][entity.value] = entry
                connection.execute("INSERT INTO source_metadata VALUES (?, ?, ?, ?)", [entity.value, canonical_json(asdict(metadata)).decode(), entry["content_sha256"], len(frame)])
            audit_frame = pd.DataFrame(audit)
            connection.register("audit_frame", audit_frame)
            connection.execute("INSERT INTO normalization_audit BY NAME SELECT * FROM audit_frame")
            connection.unregister("audit_frame")
            run["finished_at"] = datetime.now(timezone.utc).isoformat()
            run_frame = pd.DataFrame([run])
            connection.register("run_frame", run_frame)
            connection.execute("INSERT INTO analysis_run BY NAME SELECT * FROM run_frame")
            connection.unregister("run_frame")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("CHECKPOINT")
    (output / "normalization_audit.json").write_bytes(canonical_json(audit) + b"\n")
    manifest["database"] = {"path": database_path.name, "sha256": hashlib.sha256(database_path.read_bytes()).hexdigest()}
    (output / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    return manifest
