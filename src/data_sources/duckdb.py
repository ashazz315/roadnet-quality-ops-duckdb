"""Read-only adapter for a hash-pinned V2 database snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from src.data_sources.base import DataSourceError, SourceMetadata
from src.data_sources.frames import KEYS, content_hash, spatial_frame
from src.domain import InputEntity


class DuckDBDataSource:
    def __init__(self, database_path: Path, *, expected_sha256: str):
        self._frames = {}
        self._metadata = {}
        path = Path(database_path)
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                raise DataSourceError("Database checksum mismatch")
            with duckdb.connect(str(path), read_only=True) as connection:
                entries = connection.execute("SELECT entity, metadata_json, content_sha256, row_count FROM source_metadata ORDER BY entity").fetchall()
                for name, raw_metadata, expected_content, count in entries:
                    entity = InputEntity(name)
                    if entity not in KEYS:
                        raise DataSourceError(f"Database entity is not implemented: {entity}")
                    metadata = SourceMetadata(**json.loads(raw_metadata))
                    # Names come only from the fixed enum and key mapping, never user SQL.
                    frame = connection.execute(f'SELECT * FROM "{entity.value}" WHERE network_version = ? ORDER BY "{KEYS[entity]}"', [metadata.network_version]).df()
                    if len(frame) != count or content_hash(frame) != expected_content:
                        raise DataSourceError(f"Database entity mismatch: {entity}")
                    self._frames[entity] = spatial_frame(frame, entity)
                    self._metadata[entity] = metadata
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                raise DataSourceError("Database changed during loading")
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"Cannot load DuckDB snapshot: {exc}") from exc

    def metadata(self, entity: InputEntity) -> SourceMetadata:
        try:
            return self._metadata[InputEntity(entity)]
        except (KeyError, ValueError) as exc:
            raise DataSourceError(f"Entity is unavailable: {entity}") from exc

    def read(self, entity: InputEntity) -> pd.DataFrame:
        self.metadata(entity)
        return self._frames[InputEntity(entity)].copy(deep=True)
