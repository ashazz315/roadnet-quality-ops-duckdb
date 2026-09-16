"""Shared frame encoding for source checksums and optional spatial columns."""

from __future__ import annotations

import hashlib
import json
import math
from numbers import Integral, Real

import geopandas as gpd
import pandas as pd

from src.domain import InputEntity

KEYS = {
    InputEntity.ROAD_SEGMENT: "segment_id", InputEntity.ROAD_NODE: "node_id",
    InputEntity.TURN_RESTRICTION: "restriction_id",
}


def content_hash(frame: pd.DataFrame) -> str:
    """Hash non-geometry fields in row order with normalized numeric/null types."""
    def scalar(value):
        if value is None or value is pd.NA:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, Integral):
            return int(value)
        if isinstance(value, Real):
            if math.isnan(value):
                return None
            if not math.isfinite(value):
                raise ValueError("Non-finite values cannot be hashed")
            return int(value) if float(value).is_integer() else float(value)
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value

    records = [
        {key: scalar(value) for key, value in row.items()}
        for row in frame.drop(columns="geometry", errors="ignore").to_dict("records")
    ]
    encoded = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def spatial_frame(frame: pd.DataFrame, entity: InputEntity) -> pd.DataFrame:
    if isinstance(frame, gpd.GeoDataFrame):
        return frame
    if entity == InputEntity.ROAD_SEGMENT and "geometry_wkt" in frame:
        return gpd.GeoDataFrame(frame, geometry=gpd.GeoSeries.from_wkt(frame["geometry_wkt"], crs="EPSG:4326"))
    if entity == InputEntity.ROAD_NODE and {"longitude", "latitude"} <= set(frame):
        return gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(frame["longitude"], frame["latitude"], crs="EPSG:4326"))
    return frame
