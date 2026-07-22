-- name: data_overview
SELECT
    source_name AS "数据源",
    uploaded_at AS "上传时间",
    total_records AS "总记录数",
    valid_records AS "有效记录数",
    rejected_records AS "错误记录数",
    ROUND(valid_records * 100.0 / NULLIF(total_records, 0), 2) AS "有效率(%)",
    (SELECT COUNT(*) FROM issue_orders i WHERE i.batch_id = b.batch_id) AS "问题工单数",
    (SELECT COUNT(*) FROM issue_orders i WHERE i.batch_id = b.batch_id AND i.priority = 'high') AS "高优先级问题数"
FROM upload_batches b
WHERE batch_id = ?;

-- name: issue_type_statistics
SELECT
    issue_type AS "问题类型",
    COUNT(*) AS "问题数量",
    COUNT(*) FILTER (WHERE priority = 'high') AS "高优先级数量",
    ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 2) AS "占比(%)"
FROM issue_orders
WHERE batch_id = ?
GROUP BY issue_type
ORDER BY "问题数量" DESC, "问题类型";

-- name: region_statistics
WITH record_stats AS (
    SELECT COALESCE(NULLIF(TRIM(region), ''), '未填写') AS region, COUNT(*) AS record_count
    FROM records
    WHERE batch_id = ?
    GROUP BY 1
),
issue_stats AS (
    SELECT
        COALESCE(NULLIF(TRIM(region), ''), '未填写') AS region,
        COUNT(*) AS issue_count,
        COUNT(*) FILTER (WHERE priority = 'high') AS high_priority_count
    FROM issue_orders
    WHERE batch_id = ?
    GROUP BY 1
)
SELECT
    COALESCE(r.region, i.region) AS "区域",
    COALESCE(r.record_count, 0) AS "有效记录数",
    COALESCE(i.issue_count, 0) AS "问题数量",
    COALESCE(i.high_priority_count, 0) AS "高优先级数量"
FROM record_stats r
FULL OUTER JOIN issue_stats i ON r.region = i.region
ORDER BY "问题数量" DESC, "有效记录数" DESC, "区域";

-- name: high_priority_issues
SELECT
    record_id AS "记录编号",
    issue_type AS "问题类型",
    priority AS "优先级",
    region AS "区域",
    matched_road_id AS "匹配道路编号",
    matched_road_name AS "匹配道路名称",
    match_distance_m AS "匹配距离(米)",
    status AS "工单状态"
FROM issue_orders
WHERE batch_id = ? AND priority = 'high'
ORDER BY match_distance_m DESC NULLS LAST, record_id;

-- name: error_details
SELECT
    COALESCE(record_id, json_extract_string(raw_record_json, '$.record_id')) AS "record_id",
    json_extract_string(raw_record_json, '$.report_time') AS "report_time",
    json_extract_string(raw_record_json, '$.longitude') AS "longitude",
    json_extract_string(raw_record_json, '$.latitude') AS "latitude",
    json_extract_string(raw_record_json, '$.issue_type') AS "issue_type",
    json_extract_string(raw_record_json, '$.source') AS "source",
    json_extract_string(raw_record_json, '$.description') AS "description",
    json_extract_string(raw_record_json, '$.region') AS "region",
    json_extract_string(raw_record_json, '$.road_name') AS "road_name",
    json_extract_string(raw_record_json, '$.road_class') AS "road_class",
    json_extract_string(raw_record_json, '$.direction') AS "direction",
    json_extract_string(raw_record_json, '$.speed_limit') AS "speed_limit",
    error_code AS "错误代码",
    error_message AS "错误说明",
    raw_record_json AS "原始记录(JSON)"
FROM validation_results
WHERE batch_id = ? AND NOT is_valid
ORDER BY created_at, validation_id;
