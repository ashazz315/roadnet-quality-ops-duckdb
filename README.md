# 路网数据质量最小工作台

根目录 `app.py` 是唯一 Streamlit 入口。当前闭环支持：

1. 上传 UTF-8 CSV 或 XLSX；
2. 检查字段、日期、坐标和数据格式；
3. 输出 `valid_records`、`rejected_records`、`validation_summary`；
4. 将上传批次、有效记录、校验结果和问题工单写入本地 DuckDB；
5. 在 Folium 地图展示有效记录；
6. 下载错误明细 CSV；
7. 通过 SQL 查询并下载五表日报 Excel。

## 运行

```bash
pip install -r requirements.txt -r requirements-dev.txt
streamlit run app.py
```

Windows 可运行 `run_streamlit.bat`。默认地址为 `http://127.0.0.1:8502/`。

## 标准字段

```text
record_id, report_time, longitude, latitude, issue_type,
source, description, region, road_name, road_class,
direction, speed_limit
```

必填字段：

```text
record_id, report_time, longitude, latitude,
issue_type, source, description, region
```

核心规则：

- `record_id` 非空且不能重复；
- `report_time` 必须是有效日期时间；
- `longitude` 必须为 -180～180 的数字；
- `latitude` 必须为 -90～90 的数字；
- `speed_limit` 非空时必须是非负数字；
- 缺少整列时，所有输入记录进入 rejected，不会被静默删除；
- rejected 保留全部原始字段，并增加 `error_code`、`error_message`。

XLSX 默认读取第一个工作表。

## 模块与测试

```text
src/ingest.py       UTF-8 CSV、XLSX 读取
src/validation.py   无损数据校验和三类输出
src/storage.py      DuckDB 表初始化与批次事务写入
src/daily_report.py 日报 SQL 执行与 Excel 生成
tests/test_ingest.py
tests/test_validation.py
tests/test_storage.py
tests/test_daily_report.py
```

运行 pytest：

```bash
python -m pytest -q
```

本版本不包含在线 OSM 下载、真实道路自动修改、复杂拓扑、登录、多人协作、外部数据库或大模型接口。

## 基于真实道路生成模拟数据

参考路网固定为：

```text
data/reference/roads.geojson
```

生成器只从参考 LineString 抽取道路并在道路上插值生成 Point，不会在经纬度范围内随机创建道路：

```bash
python scripts/02_generate_mock_data.py
```

输出：

```text
data/generated/simulated_records.geojson
data/generated/simulated_records.csv
```

可控制问题比例和偏移：

```bash
python scripts/02_generate_mock_data.py \
  --count 200 \
  --seed 42 \
  --issue-rate 0.7 \
  --offset-share 0.2 \
  --offset-min-m 15 \
  --offset-max-m 40
```

每条记录保存 `expected_is_issue`、`expected_issue_type`、`original_road_id`、`injected_error`，并额外保存偏移前坐标和实际偏移米数，便于自动验收。

## 最近道路匹配

`src/road_matching.py` 将有效记录转换为 EPSG:4326 GeoDataFrame，再根据参考道路自动选择本地 UTM 米制投影。最近邻搜索和距离阈值判断全部在米制 CRS 中完成，最后转换回 EPSG:4326。

默认阈值位于 `config/rules.yaml`：

```yaml
road_matching:
  matched_max_distance_m: 30.0
  review_max_distance_m: 100.0
```

匹配输出字段：

```text
matched_road_id, matched_road_name, match_distance_m, match_status
```

状态包括 `matched`、`needs_review`、`unmatched`、`invalid_coordinate`、`empty_geometry`。明显颠倒的经纬度会被纠正，并通过 `coordinate_was_swapped` 标记。

## DuckDB 与日报

运行时数据库为 `data/road_quality.duckdb`，由 `sql/duckdb_schema.sql` 创建以下四张表：

```text
upload_batches, records, validation_results, issue_orders
```

核心日报指标全部由 `sql/daily_report.sql` 查询。Excel 包含“数据概览”“问题类型统计”“区域统计”“高优先级问题”“错误数据明细”五个工作表。数据库和运行时生成的 XLSX 已加入 `.gitignore`，不会作为源代码提交。
