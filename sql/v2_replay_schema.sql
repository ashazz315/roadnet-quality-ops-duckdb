-- A distinct database: hypothetical results never update source issues or roads.
CREATE TABLE replay_run (run_id VARCHAR PRIMARY KEY, provenance_json VARCHAR NOT NULL);
CREATE TABLE source_issue (issue_id VARCHAR PRIMARY KEY, issue_json VARCHAR NOT NULL);
CREATE TABLE repair_plan (
    patch_id VARCHAR PRIMARY KEY, issue_id VARCHAR NOT NULL REFERENCES source_issue(issue_id),
    status VARCHAR NOT NULL, plan_json VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL REFERENCES replay_run(run_id)
);
CREATE TABLE route_replay (
    replay_id VARCHAR PRIMARY KEY, issue_id VARCHAR NOT NULL REFERENCES source_issue(issue_id),
    patch_id VARCHAR NOT NULL REFERENCES repair_plan(patch_id),
    origin_node_id VARCHAR NOT NULL, destination_node_id VARCHAR NOT NULL,
    scenario_role VARCHAR NOT NULL, selection_reason VARCHAR NOT NULL,
    source_analysis_run_id VARCHAR NOT NULL,
    before_network_version VARCHAR NOT NULL, after_network_version VARCHAR NOT NULL,
    before_status VARCHAR NOT NULL, after_status VARCHAR NOT NULL,
    before_distance_m DOUBLE, after_distance_m DOUBLE, before_eta_s DOUBLE, after_eta_s DOUBLE,
    distance_delta_m DOUBLE, eta_delta_s DOUBLE,
    before_legal BOOLEAN, after_legal BOOLEAN, before_edge_sequence_legal_after BOOLEAN,
    reachability_change VARCHAR NOT NULL, path_changed BOOLEAN NOT NULL, distance_change VARCHAR NOT NULL,
    target_exercised_before BOOLEAN NOT NULL, target_exercised_after BOOLEAN NOT NULL,
    before_route_json VARCHAR NOT NULL, after_route_json VARCHAR NOT NULL,
    is_hypothetical BOOLEAN NOT NULL CHECK(is_hypothetical),
    verification_status VARCHAR NOT NULL CHECK(verification_status = 'not_field_verified'),
    run_id VARCHAR NOT NULL REFERENCES replay_run(run_id),
    CHECK (origin_node_id <> destination_node_id),
    CHECK ((before_distance_m IS NULL AND before_eta_s IS NULL) OR (before_distance_m >= 0 AND before_eta_s >= 0)),
    CHECK ((after_distance_m IS NULL AND after_eta_s IS NULL) OR (after_distance_m >= 0 AND after_eta_s >= 0)),
    CHECK ((before_distance_m IS NOT NULL AND after_distance_m IS NOT NULL) OR (distance_delta_m IS NULL AND eta_delta_s IS NULL))
);
