CREATE TABLE IF NOT EXISTS upload_batches (
    batch_id VARCHAR PRIMARY KEY,
    source_name VARCHAR NOT NULL,
    uploaded_at TIMESTAMP NOT NULL,
    total_records BIGINT NOT NULL,
    valid_records BIGINT NOT NULL,
    rejected_records BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    record_key VARCHAR PRIMARY KEY,
    batch_id VARCHAR NOT NULL,
    record_id VARCHAR,
    report_time TIMESTAMP,
    longitude DOUBLE,
    latitude DOUBLE,
    issue_type VARCHAR,
    source VARCHAR,
    description VARCHAR,
    region VARCHAR,
    road_name VARCHAR,
    road_class VARCHAR,
    direction VARCHAR,
    speed_limit DOUBLE,
    matched_road_id VARCHAR,
    matched_road_name VARCHAR,
    match_distance_m DOUBLE,
    match_status VARCHAR,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id VARCHAR PRIMARY KEY,
    batch_id VARCHAR NOT NULL,
    record_id VARCHAR,
    is_valid BOOLEAN NOT NULL,
    error_code VARCHAR,
    error_message VARCHAR,
    raw_record_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_orders (
    issue_order_id VARCHAR PRIMARY KEY,
    batch_id VARCHAR NOT NULL,
    record_key VARCHAR NOT NULL,
    record_id VARCHAR,
    issue_type VARCHAR NOT NULL,
    priority VARCHAR NOT NULL,
    region VARCHAR,
    status VARCHAR NOT NULL,
    matched_road_id VARCHAR,
    matched_road_name VARCHAR,
    match_distance_m DOUBLE,
    created_at TIMESTAMP NOT NULL
);
