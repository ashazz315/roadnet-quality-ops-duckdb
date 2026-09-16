"""Explicit OSM download; immutable compressed raw bytes plus retrieval metadata."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_query(config: dict) -> str:
    west, south, east, north = config["bbox"]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (west, south, east, north)):
        raise ValueError("bbox must contain four finite WGS84 numbers")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid WGS84 bbox")
    if east - west > 0.06 or north - south > 0.06:
        raise ValueError("This downloader is limited to a small interview demo area")
    buffer = config.get("query_buffer_degrees", 0)
    if not isinstance(buffer, (int, float)) or not 0 <= buffer <= 0.005:
        raise ValueError("Invalid query buffer")
    classes = config["highway_classes"]
    if not classes or any(not re.fullmatch(r"[a-z_]+", item) for item in classes):
        raise ValueError("Invalid highway classes")
    timeout = config["timeout_seconds"]
    if not isinstance(timeout, int) or not 1 <= timeout <= 180:
        raise ValueError("Invalid Overpass timeout")
    bounds = ",".join(f"{value:.7f}" for value in (south-buffer, west-buffer, north+buffer, east+buffer))
    return (
        f'[out:json][timeout:{timeout}];\n'
        f'way["highway"~"^({"|".join(sorted(classes))})$"]({bounds})->.roads;\n'
        'rel(bw.roads)["type"="restriction"]->.restrictions;\n'
        '(.roads;.restrictions;);\n(._;>>;);\nout body;\n'
    )


def fetch_osm(config: dict, output: Path) -> dict:
    """Download once, fail on partial Overpass results, never replace a snapshot."""
    query = build_query(config)
    if output.exists():
        raise FileExistsError(f"Snapshot directory already exists: {output}")
    request = Request(
        config["overpass_endpoint"], data=urlencode({"data": query}).encode(),
        headers={"User-Agent": "RoadInsight/2 public interview demo", "Accept": "application/json"},
    )
    with urlopen(request, timeout=config["timeout_seconds"] + 30) as response:
        raw = response.read(30_000_001)
    if len(raw) > 30_000_000:
        raise ValueError("Response exceeds demo size limit")
    payload = json.loads(raw)
    if payload.get("remark") or not payload.get("elements") or not payload.get("osm3s", {}).get("timestamp_osm_base"):
        raise ValueError("OSM response is empty, incomplete, or missing snapshot timestamp")
    metadata = {
        "source": "OpenStreetMap / Overpass API", "endpoint": config["overpass_endpoint"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "osm_timestamp": payload["osm3s"]["timestamp_osm_base"],
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "query": query, "config": config, "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors", "license_url": "https://www.openstreetmap.org/copyright",
        "is_synthetic": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "osm.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    (output / "source.json").write_bytes(canonical_json(metadata) + b"\n")
    return metadata


def load_raw_snapshot(directory: Path) -> tuple[dict, dict]:
    metadata = json.loads((directory / "source.json").read_bytes())
    raw = gzip.decompress((directory / "osm.json.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != metadata["raw_sha256"]:
        raise ValueError("Raw OSM hash does not match source manifest")
    if build_query(metadata["config"]) != metadata["query"]:
        raise ValueError("Query and source configuration do not match")
    if hashlib.sha256(metadata["query"].encode()).hexdigest() != metadata["query_sha256"]:
        raise ValueError("Query hash does not match")
    payload = json.loads(raw)
    if payload.get("remark") or not payload.get("elements"):
        raise ValueError("Incomplete OSM snapshot")
    if payload.get("osm3s", {}).get("timestamp_osm_base") != metadata["osm_timestamp"]:
        raise ValueError("OSM timestamp does not match source manifest")
    return payload, metadata
