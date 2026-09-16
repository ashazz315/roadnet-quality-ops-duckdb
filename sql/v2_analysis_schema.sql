-- Derived STEP 6 results in a new database; no changes to source/legacy tables.
CREATE TABLE road_issue (
    issue_id VARCHAR PRIMARY KEY, issue_type VARCHAR NOT NULL,
    object_type VARCHAR NOT NULL, object_id VARCHAR NOT NULL,
    longitude DOUBLE, latitude DOUBLE, geometry_wkt VARCHAR,
    severity VARCHAR NOT NULL, confidence DOUBLE CHECK(confidence BETWEEN 0 AND 1),
    confidence_kind VARCHAR, calibrated BOOLEAN, status VARCHAR,
    root_cause_hypothesis VARCHAR, suggested_action VARCHAR,
    first_detected_at VARCHAR, last_detected_at VARCHAR,
    first_observed_at VARCHAR, last_observed_at VARCHAR,
    evidence_summary_json VARCHAR, network_version VARCHAR, is_synthetic BOOLEAN,
    run_id VARCHAR NOT NULL
);
CREATE TABLE issue_evidence (
    evidence_id VARCHAR PRIMARY KEY, issue_id VARCHAR NOT NULL REFERENCES road_issue(issue_id),
    evidence_type VARCHAR, metric_name VARCHAR, metric_value DOUBLE,
    metric_text VARCHAR, source_ref VARCHAR, weight DOUBLE,
    run_id VARCHAR NOT NULL
);
CREATE TABLE business_impact (
    issue_id VARCHAR NOT NULL REFERENCES road_issue(issue_id), business_scenario VARCHAR NOT NULL,
    impact_level VARCHAR, impact_reason VARCHAR, assessment_kind VARCHAR,
    observed_supporting_trajectories BIGINT,
    measured_distance_delta_m DOUBLE, measured_eta_delta_s DOUBLE,
    no_path_verified BOOLEAN, run_id VARCHAR NOT NULL,
    PRIMARY KEY(issue_id, business_scenario)
);
