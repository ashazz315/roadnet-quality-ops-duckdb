# RoadInsight V2 数据模型

## 1. 核心实体

V2 仅维护对面试闭环有直接价值的核心表。

## 2. `road_segment`

```text
segment_id              STRING PK
from_node_id             STRING
to_node_id               STRING
osm_way_id               STRING
name                     STRING
road_class               STRING
oneway                   BOOLEAN
lanes                    INTEGER
maxspeed                 DOUBLE
length_m                 DOUBLE
geometry_wkt             STRING
network_version           STRING
```

## 3. `road_node`

```text
node_id                   STRING PK
longitude                 DOUBLE
latitude                  DOUBLE
node_degree               INTEGER
network_version           STRING
```

## 4. `turn_restriction`

```text
restriction_id            STRING PK
from_segment_id           STRING
via_node_id               STRING
to_segment_id             STRING
restriction_type          STRING
network_version           STRING
```

## 5. `trajectory_point`

```text
trajectory_id             STRING
point_seq                 INTEGER
timestamp                 TIMESTAMP
longitude                 DOUBLE
latitude                  DOUBLE
speed_kmh                 DOUBLE
heading_deg               DOUBLE
source                    STRING
matched_segment_id        STRING
matched_node_id           STRING
match_distance_m          DOUBLE
match_confidence          DOUBLE
quality_flag              STRING
batch_id                  STRING
run_id                    STRING
```

## 6. `user_feedback`

```text
feedback_id               STRING PK
report_time               TIMESTAMP
longitude                 DOUBLE
latitude                  DOUBLE
feedback_type             STRING
description               STRING
matched_segment_id        STRING
matched_node_id           STRING
source                    STRING
batch_id                  STRING
```

示例 `feedback_type`：

- route_unreasonable
- turn_not_allowed
- road_missing
- navigation_interrupted
- direction_wrong

## 7. `road_issue`

```text
issue_id                   STRING PK
object_type                STRING
object_id                  STRING
issue_type                 STRING
severity                   STRING
confidence                 DOUBLE
status                     STRING
root_cause_hypothesis      STRING
first_detected_at          TIMESTAMP
last_detected_at           TIMESTAMP
run_id                     STRING
```

`object_type`：

- segment
- node
- turn

`issue_type`：

- CONNECTIVITY_BREAK
- TURN_RESTRICTION_CONFLICT
- ONEWAY_DIRECTION_CONFLICT
- MISSING_OR_CHANGED_ROAD_CANDIDATE

## 8. `issue_evidence`

```text
evidence_id                STRING PK
issue_id                   STRING
evidence_type              STRING
metric_name                STRING
metric_value               DOUBLE
metric_text                STRING
source_ref                 STRING
weight                     DOUBLE
run_id                     STRING
```

`evidence_type`：

- trajectory
- topology
- user_feedback
- attribute
- route_replay

## 9. `business_impact`

```text
issue_id                   STRING
business_scenario          STRING
impact_level               STRING
impact_reason              STRING
run_id                     STRING
```

`business_scenario`：

- navigation
- routing
- eta
- dispatch
- poi_local_service

`impact_level`：LOW / MEDIUM / HIGH / CRITICAL

## 10. `route_replay`

```text
replay_id                  STRING PK
issue_id                   STRING
origin_node_id             STRING
destination_node_id        STRING
before_distance_m          DOUBLE
after_distance_m           DOUBLE
before_eta_s               DOUBLE
after_eta_s                DOUBLE
before_route_status        STRING
after_route_status         STRING
before_path_json           STRING
after_path_json            STRING
run_id                     STRING
```

## 11. `analysis_run`

```text
run_id                     STRING PK
source_batch_id            STRING
network_version            STRING
algorithm_version          STRING
rules_version              STRING
query_version              STRING
started_at                 TIMESTAMP
finished_at                TIMESTAMP
status                     STRING
record_count               INTEGER
trajectory_count           INTEGER
road_count                 INTEGER
parameter_json             STRING
```

## 12. Benchmark 表

### `benchmark_fault`

```text
fault_id                   STRING PK
fault_type                 STRING
target_object_type         STRING
target_object_id           STRING
expected_issue_type        STRING
injection_payload          STRING
benchmark_version          STRING
```

### `benchmark_result`

```text
run_id                     STRING
fault_id                   STRING
detected_issue_id          STRING
is_true_positive           BOOLEAN
is_false_negative          BOOLEAN
```

## 13. 数据一致性约束

- 不允许静默删除输入记录；
- rejected 数据必须保留原因；
- 每条 issue 必须有至少一条 evidence；
- `confidence` 范围固定 0–1；
- route replay 必须关联具体 issue；
- 所有分析结果必须关联 `run_id`。
