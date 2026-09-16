-- V2 snapshot database only. Never executed against the legacy upload database.
CREATE TABLE road_node (
    node_id VARCHAR NOT NULL, osm_node_id VARCHAR,
    longitude DOUBLE NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude DOUBLE NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    is_boundary BOOLEAN NOT NULL, routing_eligible BOOLEAN NOT NULL,
    tags_json VARCHAR NOT NULL, network_version VARCHAR NOT NULL,
    node_degree INTEGER NOT NULL CHECK (node_degree >= 0),
    PRIMARY KEY (network_version, node_id)
);
CREATE TABLE road_segment (
    segment_id VARCHAR NOT NULL, from_node_id VARCHAR NOT NULL, to_node_id VARCHAR NOT NULL,
    osm_way_id VARCHAR NOT NULL, name VARCHAR, road_class VARCHAR NOT NULL,
    oneway BOOLEAN NOT NULL, direction VARCHAR NOT NULL CHECK (direction IN ('forward', 'reverse', 'both')),
    lanes INTEGER, maxspeed DOUBLE, length_m DOUBLE NOT NULL CHECK (length_m > 0),
    geometry_wkt VARCHAR NOT NULL, routing_eligible BOOLEAN NOT NULL,
    tags_json VARCHAR NOT NULL, network_version VARCHAR NOT NULL,
    CHECK (oneway = (direction <> 'both')),
    PRIMARY KEY (network_version, segment_id),
    FOREIGN KEY (network_version, from_node_id) REFERENCES road_node (network_version, node_id),
    FOREIGN KEY (network_version, to_node_id) REFERENCES road_node (network_version, node_id)
);
CREATE TABLE turn_restriction (
    restriction_id VARCHAR NOT NULL, osm_relation_id VARCHAR NOT NULL,
    from_segment_id VARCHAR NOT NULL, via_node_id VARCHAR NOT NULL, to_segment_id VARCHAR NOT NULL,
    from_edge_id VARCHAR NOT NULL, to_edge_id VARCHAR NOT NULL,
    restriction_type VARCHAR NOT NULL, tags_json VARCHAR NOT NULL, network_version VARCHAR NOT NULL,
    PRIMARY KEY (network_version, restriction_id),
    FOREIGN KEY (network_version, from_segment_id) REFERENCES road_segment (network_version, segment_id),
    FOREIGN KEY (network_version, to_segment_id) REFERENCES road_segment (network_version, segment_id),
    FOREIGN KEY (network_version, via_node_id) REFERENCES road_node (network_version, node_id)
);
CREATE TABLE source_metadata (
    entity VARCHAR PRIMARY KEY, metadata_json VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL, row_count BIGINT NOT NULL
);
CREATE TABLE normalization_audit (
    object_type VARCHAR, object_id VARCHAR, status VARCHAR, reason VARCHAR, output_count INTEGER
);
CREATE TABLE analysis_run (
    run_id VARCHAR PRIMARY KEY, data_version VARCHAR NOT NULL, network_version VARCHAR NOT NULL,
    query_version VARCHAR NOT NULL, algorithm_version VARCHAR NOT NULL, code_version VARCHAR NOT NULL,
    code_sha256 VARCHAR NOT NULL, rules_version VARCHAR NOT NULL,
    parameter_json VARCHAR NOT NULL, environment_json VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL, seed BIGINT
);
