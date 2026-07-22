"""Folium map generation for validated point records."""

from __future__ import annotations

from html import escape

import folium
import pandas as pd

DEFAULT_CENTER = (39.9042, 116.4074)
SEVERITY_COLORS = {
    "low": "green",
    "medium": "blue",
    "high": "orange",
    "critical": "red",
}


def build_record_map(valid_records: pd.DataFrame) -> folium.Map:
    """Build a standalone map from records that already passed validation."""
    if valid_records.empty:
        center = DEFAULT_CENTER
    else:
        center = (
            float(valid_records["latitude"].astype(float).mean()),
            float(valid_records["longitude"].astype(float).mean()),
        )

    record_map = folium.Map(location=center, zoom_start=13, tiles="CartoDB positron")
    for _, row in valid_records.iterrows():
        severity = str(row.get("severity", "")).lower()
        issue_type = escape(str(row.get("issue_type", "未分类")))
        record_id = escape(str(row.get("record_id", "")))
        description = escape(str(row.get("description", "")))
        popup = f"<b>{record_id}</b><br>类型：{issue_type}<br>{description}"
        folium.Marker(
            location=[float(row["latitude"]), float(row["longitude"])],
            popup=folium.Popup(popup, max_width=320),
            tooltip=record_id,
            icon=folium.Icon(color=SEVERITY_COLORS.get(severity, "cadetblue"), icon="info-sign"),
        ).add_to(record_map)
    return record_map


def map_html(valid_records: pd.DataFrame) -> str:
    """Render the record map as embeddable HTML."""
    return build_record_map(valid_records).get_root().render()
