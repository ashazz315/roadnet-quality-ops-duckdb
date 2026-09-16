"""Offline integration tests using the pinned, attributed public OSM snapshot."""

import hashlib
import json
from pathlib import Path
from shutil import copyfile

import duckdb
import pytest

from src.data_sources.base import DataSourceError
from src.data_sources.duckdb import DuckDBDataSource
from src.data_sources.files import FileDataSource
from src.data_sources.frames import content_hash
from src.data_sources.osm import load_raw_snapshot
from src.domain import InputEntity
from src.pipeline import build_golden_snapshot

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/reference/xuhui_osm"
ENTITIES = (InputEntity.ROAD_SEGMENT, InputEntity.ROAD_NODE, InputEntity.TURN_RESTRICTION)


@pytest.fixture(scope="module")
def snapshots(tmp_path_factory):
    directory = tmp_path_factory.mktemp("golden")
    before = {path.name: path.read_bytes() for path in RAW.iterdir() if path.is_file()}
    first = build_golden_snapshot(RAW, directory / "first", code_version="test-code")
    second = build_golden_snapshot(RAW, directory / "second", code_version="test-code")
    assert {path.name: path.read_bytes() for path in RAW.iterdir() if path.is_file()} == before
    return directory, first, second


def test_same_snapshot_produces_same_tables_graph_and_version(snapshots):
    directory, first, second = snapshots
    assert first["run"]["network_version"] == second["run"]["network_version"]
    assert first["summary"] == second["summary"]
    assert first["run"]["run_id"] != second["run"]["run_id"]
    for entity in ENTITIES:
        assert first["entities"][entity]["content_sha256"] == second["entities"][entity]["content_sha256"]
        assert first["entities"][entity]["sha256"] == second["entities"][entity]["sha256"]
    assert (directory / "first/normalization_audit.json").read_bytes() == (directory / "second/normalization_audit.json").read_bytes()


def test_public_snapshot_counts_and_provenance_are_explicit(snapshots):
    _, manifest, _ = snapshots
    summary = manifest["summary"]
    assert summary["input_counts"] == {"node": 3760, "way": 888, "relation": 36}
    assert 800 <= summary["road_segment"] <= 2000
    assert 500 <= summary["road_node"] <= 1500
    assert summary["turn_restriction"] == 20
    assert summary["validation_errors"] == 0
    assert summary["is_ground_truth"] is False
    assert summary["osm_timestamp"] == "2026-05-31T22:37:44Z"
    assert summary["license"] == "ODbL-1.0"


def test_published_summary_matches_the_current_build(snapshots):
    _, manifest, _ = snapshots
    pinned = json.loads((RAW / "network_summary.json").read_bytes())
    assert manifest["run"]["network_version"] == pinned["network_version"]
    assert manifest["run"]["code_sha256"] == pinned["code_sha256"]
    assert manifest["summary"] == pinned["summary"]
    if json.loads(manifest["run"]["environment_json"]) == pinned["tested_environment"]:
        assert {name: entry["content_sha256"] for name, entry in manifest["entities"].items()} == pinned["entity_content_sha256"]


@pytest.mark.parametrize("entity", ENTITIES)
def test_file_and_duckdb_adapters_return_identical_independent_frames(snapshots, entity):
    directory, manifest, _ = snapshots
    file_source = FileDataSource(directory / "first/manifest.json")
    db_source = DuckDBDataSource(directory / "first/network.duckdb", expected_sha256=manifest["database"]["sha256"])
    expected = manifest["entities"][entity]["content_sha256"]
    assert content_hash(file_source.read(entity)) == content_hash(db_source.read(entity)) == expected
    assert file_source.metadata(entity) == db_source.metadata(entity)
    original = file_source.read(entity)
    original.iloc[0, 0] = "caller-modified"
    assert content_hash(file_source.read(entity)) == expected
    original = db_source.read(entity)
    original.iloc[0, 0] = "caller-modified"
    assert content_hash(db_source.read(entity)) == expected
    if entity in (InputEntity.ROAD_SEGMENT, InputEntity.ROAD_NODE):
        assert file_source.read(entity).crs.to_epsg() == 4326
        assert db_source.read(entity).crs.to_epsg() == 4326


def test_adapters_reject_missing_entities_and_arbitrary_table_names(snapshots):
    directory, manifest, _ = snapshots
    sources = [FileDataSource(directory / "first/manifest.json"), DuckDBDataSource(directory / "first/network.duckdb", expected_sha256=manifest["database"]["sha256"])]
    for source in sources:
        for entity in (InputEntity.TRAJECTORY_POINT, "benchmark_fault", "road_node; DROP TABLE road_node"):
            with pytest.raises(DataSourceError):
                source.read(entity)


def test_snapshot_overwrite_is_refused(snapshots):
    directory, _, _ = snapshots
    with pytest.raises(FileExistsError):
        build_golden_snapshot(RAW, directory / "first", code_version="test-code")


def test_database_fk_and_run_metadata(snapshots, tmp_path):
    directory, manifest, _ = snapshots
    copyfile(directory / "first/network.duckdb", tmp_path / "network.duckdb")
    with duckdb.connect(str(tmp_path / "network.duckdb")) as connection:
        row = connection.execute("SELECT data_version, code_version, query_version, parameter_json, started_at, finished_at, seed FROM analysis_run").fetchone()
        assert row[0] == manifest["run"]["data_version"]
        assert row[1] == "test-code" and row[2] == "osm-roads-restrictions-v1"
        assert json.loads(row[3])["metric_crs"] == "EPSG:32651"
        assert row[4] <= row[5] and row[6] is None
        with pytest.raises(duckdb.ConstraintException):
            connection.execute("UPDATE road_segment SET from_node_id = 'missing'")


def test_checksums_detect_modified_source_database_and_parquet(snapshots, tmp_path):
    directory, _, _ = snapshots
    with pytest.raises(DataSourceError, match="checksum"):
        DuckDBDataSource(directory / "first/network.duckdb", expected_sha256="0" * 64)
    payload = json.loads((directory / "first/manifest.json").read_bytes())
    entity = InputEntity.ROAD_SEGMENT
    file = tmp_path / payload["entities"][entity]["path"]
    file.write_bytes(b"changed content")
    payload["entities"] = {entity: payload["entities"][entity]}
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataSourceError, match="checksum"):
        FileDataSource(tmp_path / "manifest.json")
    copyfile(RAW / "osm.json.gz", tmp_path / "osm.json.gz")
    metadata = json.loads((RAW / "source.json").read_bytes())
    metadata["raw_sha256"] = "0" * 64
    (tmp_path / "source.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_raw_snapshot(tmp_path)


def test_every_raw_way_and_relation_has_an_audit_outcome(snapshots):
    directory, _, _ = snapshots
    audit = json.loads((directory / "first/normalization_audit.json").read_bytes())
    raw, _ = load_raw_snapshot(RAW)
    expected = {(item["type"], str(item["id"])) for item in raw["elements"] if item["type"] in {"way", "relation"}}
    assert {(row["object_type"], row["object_id"]) for row in audit} == expected
    assert len(audit) == len(expected)


def test_cached_adapters_remain_bound_to_original_snapshot(snapshots, tmp_path):
    directory, manifest, _ = snapshots
    copyfile(directory / "first/network.duckdb", tmp_path / "copy.duckdb")
    source = DuckDBDataSource(tmp_path / "copy.duckdb", expected_sha256=manifest["database"]["sha256"])
    before = content_hash(source.read(InputEntity.ROAD_NODE))
    with duckdb.connect(str(tmp_path / "copy.duckdb")) as connection:
        connection.execute("UPDATE road_node SET node_degree = 999")
    assert content_hash(source.read(InputEntity.ROAD_NODE)) == before
    assert hashlib.sha256((tmp_path / "copy.duckdb").read_bytes()).hexdigest() != manifest["database"]["sha256"]
