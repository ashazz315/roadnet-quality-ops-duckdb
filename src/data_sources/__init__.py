"""V2 input boundary; import concrete adapters from files or duckdb submodules."""

from src.data_sources.base import DataSource, DataSourceError, SourceMetadata

__all__ = ["DataSource", "DataSourceError", "SourceMetadata"]
