"""Read-only source contract shared by future file and database adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from src.domain import InputEntity

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class SourceMetadata:
    """Caller-supplied provenance for one fixed entity snapshot.

    source_ref identifies a public source, local artifact, or query result.
    Versions identify the actual snapshot; they must not be invented defaults.
    is_synthetic describes this entity, not all entities in a mixed dataset.
    """

    source_ref: str
    data_version: str
    network_version: str
    is_synthetic: bool

    def __post_init__(self) -> None:
        for field in ("source_ref", "data_version", "network_version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a nonempty string")
        if type(self.is_synthetic) is not bool:
            raise ValueError("is_synthetic must be explicitly true or false")


class DataSourceError(ValueError):
    """Requested input is absent, unsupported, or cannot be read."""


class DataSource(Protocol):
    """Contract, not an implemented adapter or a runtime schema validator.

    For each entity, metadata() and read() must refer to the same fixed snapshot.
    read() preserves source rows/fields; validation and cleaning happen downstream.
    A GeoDataFrame may be returned for spatial inputs, retaining its declared CRS.
    No method may write to the source or expose benchmark answer labels.
    """

    def metadata(self, entity: InputEntity) -> SourceMetadata:
        """Return provenance, or raise DataSourceError if unavailable."""
        ...

    def read(self, entity: InputEntity) -> pd.DataFrame:
        """Return an independently mutable frame; raise on unsupported inputs.

        Empty frames represent an existing empty entity, never a hidden read error.
        Repeated reads of a fixed snapshot preserve row content and order.
        """
        ...
