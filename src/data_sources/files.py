"""Eager immutable-snapshot adapter for CSV/XLSX/Parquet/GeoParquet inputs."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.data_sources.base import DataSourceError, SourceMetadata
from src.data_sources.frames import content_hash, spatial_frame
from src.domain import InputEntity
from src.ingest import ingest_bytes


class FileDataSource:
    """Bind a manifest and cache verified frames; callers receive defensive copies."""

    def __init__(self, manifest_path: Path):
        self._frames = {}
        self._metadata = {}
        manifest_path = Path(manifest_path).resolve()
        try:
            manifest = json.loads(manifest_path.read_bytes())
            for name, entry in manifest["entities"].items():
                entity = InputEntity(name)
                path = (manifest_path.parent / entry["path"]).resolve()
                if not path.is_relative_to(manifest_path.parent):
                    raise DataSourceError("Manifest paths must remain inside the snapshot")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise DataSourceError(f"File checksum mismatch: {name}")
                format_name = entry["format"]
                if format_name in {"csv", "xlsx"}:
                    frame = ingest_bytes(raw, f"input.{format_name}")
                elif format_name == "geoparquet":
                    frame = gpd.read_parquet(BytesIO(raw))
                elif format_name == "parquet":
                    frame = pd.read_parquet(BytesIO(raw))
                else:
                    raise DataSourceError(f"Unsupported file format: {format_name}")
                if len(frame) != entry["row_count"] or content_hash(frame) != entry["content_sha256"]:
                    raise DataSourceError(f"Entity content mismatch: {name}")
                self._frames[entity] = spatial_frame(frame, entity)
                self._metadata[entity] = SourceMetadata(**entry["metadata"])
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"Cannot load file snapshot: {exc}") from exc

    def metadata(self, entity: InputEntity) -> SourceMetadata:
        try:
            return self._metadata[InputEntity(entity)]
        except (KeyError, ValueError) as exc:
            raise DataSourceError(f"Entity is unavailable: {entity}") from exc

    def read(self, entity: InputEntity) -> pd.DataFrame:
        self.metadata(entity)
        return self._frames[InputEntity(entity)].copy(deep=True)
