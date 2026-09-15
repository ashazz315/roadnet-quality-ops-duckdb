"""V2 input boundary. Concrete file and DuckDB adapters are planned for STEP 4."""

from src.data_sources.base import DataSource, DataSourceError, SourceMetadata

__all__ = ["DataSource", "DataSourceError", "SourceMetadata"]
