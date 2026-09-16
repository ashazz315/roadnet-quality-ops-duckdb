"""Source adapters preserve raw records and distinguish missing from empty inputs."""

import gzip
import hashlib
import json
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from src.data_sources.base import DataSourceError, SourceMetadata
from src.data_sources.files import FileDataSource
from src.data_sources.frames import content_hash
from src.data_sources.osm import (
    build_query,
    canonical_json,
    fetch_osm,
    load_raw_snapshot,
)
from src.domain import InputEntity
from src.ingest import ingest_bytes
from src.pipeline import build_golden_snapshot

ROOT = Path(__file__).resolve().parents[1]


def fixture_config():
    return json.loads((ROOT / "config/osm_xuhui.json").read_text(encoding="utf-8"))


def file_manifest(tmp_path, raw, format_name, expected):
    filename = "source." + format_name
    (tmp_path / filename).write_bytes(raw)
    metadata = SourceMetadata("synthetic-test-fixture", "test-data", "test-network", True)
    payload = {"entities": {"user_feedback": {
        "path": filename, "format": format_name, "sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": content_hash(expected), "row_count": len(expected), "metadata": asdict(metadata),
    }}}
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical_json(payload))
    return path


@pytest.mark.parametrize("format_name", ["csv", "xlsx"])
def test_csv_xlsx_reuse_legacy_ingest_and_preserve_invalid_rows(tmp_path, format_name):
    original = pd.DataFrame({"feedback_id": ["001", "001", ""], "longitude": ["bad", "121.43", ""], "extra": ["keep", "NA", ""]})
    if format_name == "csv":
        raw = original.to_csv(index=False).encode()
    else:
        buffer = BytesIO()
        original.to_excel(buffer, index=False)
        raw = buffer.getvalue()
    expected = ingest_bytes(raw, "input." + format_name)
    source = FileDataSource(file_manifest(tmp_path, raw, format_name, expected))
    pd.testing.assert_frame_equal(source.read(InputEntity.USER_FEEDBACK), expected)
    assert source.read(InputEntity.USER_FEEDBACK).iloc[0]["longitude"] == "bad"
    assert source.metadata(InputEntity.USER_FEEDBACK).is_synthetic is True


def test_empty_entity_is_not_a_missing_entity(tmp_path):
    raw = b"feedback_id,description\n"
    expected = ingest_bytes(raw, "input.csv")
    source = FileDataSource(file_manifest(tmp_path, raw, "csv", expected))
    assert source.read(InputEntity.USER_FEEDBACK).empty
    with pytest.raises(DataSourceError):
        source.read(InputEntity.TRAJECTORY_POINT)


def test_file_adapter_caches_its_verified_snapshot(tmp_path):
    raw = b"feedback_id\n001\n"
    expected = ingest_bytes(raw, "input.csv")
    source = FileDataSource(file_manifest(tmp_path, raw, "csv", expected))
    (tmp_path / "source.csv").write_bytes(b"feedback_id\nCHANGED\n")
    assert source.read(InputEntity.USER_FEEDBACK).iloc[0, 0] == "001"
    with pytest.raises(DataSourceError, match="checksum"):
        FileDataSource(tmp_path / "manifest.json")


def test_manifest_path_escape_is_rejected(tmp_path):
    raw = b"feedback_id\n001\n"
    path = file_manifest(tmp_path, raw, "csv", ingest_bytes(raw, "input.csv"))
    payload = json.loads(path.read_bytes())
    payload["entities"]["user_feedback"]["path"] = "../outside.csv"
    path.write_bytes(canonical_json(payload))
    with pytest.raises(DataSourceError, match="inside"):
        FileDataSource(path)


@pytest.mark.parametrize("patch", [{"bbox": [0, 0, 2, 2]}, {"bbox": [121, 32, 120, 31]}, {"highway_classes": ['residential"];out;']}, {"timeout_seconds": 0}])
def test_downloader_rejects_unbounded_or_invalid_queries(patch):
    config = fixture_config()
    config.update(patch)
    with pytest.raises(ValueError):
        build_query(config)


def test_downloader_does_not_accept_partial_overpass_success(monkeypatch, tmp_path):
    payload = {"elements": [{"type": "node", "id": 1}], "remark": "runtime error: timed out", "osm3s": {"timestamp_osm_base": "2020-01-01T00:00:00Z"}}
    monkeypatch.setattr("src.data_sources.osm.urlopen", lambda *args, **kwargs: BytesIO(canonical_json(payload)))
    with pytest.raises(ValueError, match="incomplete"):
        fetch_osm(fixture_config(), tmp_path / "download")
    assert not (tmp_path / "download").exists()


def test_offline_pipeline_supports_zero_restrictions_without_inventing_rows(tmp_path, monkeypatch):
    config = fixture_config()
    payload = {"osm3s": {"timestamp_osm_base": "2020-01-01T00:00:00Z"}, "elements": [
        {"type": "node", "id": 1, "lon": 121.43, "lat": 31.18},
        {"type": "node", "id": 2, "lon": 121.431, "lat": 31.18},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "residential"}},
    ]}
    raw = canonical_json(payload)
    query = build_query(config)
    metadata = {
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "config": config, "query": query, "osm_timestamp": "2020-01-01T00:00:00Z",
        "retrieved_at": "2020-01-01T00:00:00Z", "license": "synthetic-test-fixture",
        "attribution": "synthetic-test-fixture", "endpoint": "fixture://local", "is_synthetic": True,
    }
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "source.json").write_bytes(canonical_json(metadata))
    (directory / "osm.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    monkeypatch.setattr("src.data_sources.osm.urlopen", lambda *args, **kwargs: pytest.fail("Offline build must not download"))
    manifest = build_golden_snapshot(directory, tmp_path / "output", code_version="synthetic-test")
    assert manifest["summary"]["turn_restriction"] == 0
    source = FileDataSource(tmp_path / "output/manifest.json")
    assert source.read(InputEntity.TURN_RESTRICTION).empty
    assert source.metadata(InputEntity.ROAD_SEGMENT).is_synthetic is True
    changed = json.loads((directory / "source.json").read_bytes())
    changed["query"] += "changed"
    (directory / "source.json").write_bytes(canonical_json(changed))
    with pytest.raises(ValueError, match="Query"):
        load_raw_snapshot(directory)


@pytest.mark.parametrize("changes", [{"data_version": ""}, {"network_version": " "}, {"is_synthetic": "false"}])
def test_metadata_never_silently_defaults_missing_provenance(changes):
    fields = {"source_ref": "test", "data_version": "d1", "network_version": "n1", "is_synthetic": True}
    fields.update(changes)
    with pytest.raises(ValueError):
        SourceMetadata(**fields)
