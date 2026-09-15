# RoadInsight V2 技术架构

## 1. 架构目标

V2 采用轻量、模块化、可复现架构。重点不是吞吐极限，而是让每个数据结论可解释、每个算法模块可单测、每次运行可追踪。

## 2. 技术栈建议

- Python 3.11+
- Streamlit：面试 Demo 前端
- DuckDB：本地分析数据库
- Pandas / GeoPandas：数据处理
- Shapely / PyProj：空间计算
- NetworkX：路网图与最短路径
- Folium 或 PyDeck：地图可视化
- Pytest：测试
- GeoJSON / GeoParquet：空间中间结果

V2 暂不引入 Spark/Flink/Kafka。

## 3. 分层架构

```text
Data Source
  ├─ OSM Reference Network
  ├─ Trajectory
  ├─ User Feedback
  └─ Fault Injection Config
        ↓
Ingestion Layer
  ├─ file adapter
  ├─ DuckDB adapter
  └─ future database adapter
        ↓
Validation & Cleaning
        ↓
Road Graph / Map Matching
        ↓
Issue Detection
        ↓
Evidence Fusion
        ↓
Severity / Confidence
        ↓
Business Impact
        ↓
Route Replay
        ↓
Validation & UI
```

## 4. 推荐代码结构

```text
roadinsight/
├── app.py
├── config/
│   ├── app.yaml
│   ├── rules.yaml
│   └── business_impact.yaml
├── data/
│   ├── reference/
│   ├── generated/
│   ├── benchmark/
│   └── runtime/
├── src/
│   ├── ingest.py
│   ├── validation.py
│   ├── storage.py
│   ├── road_matching.py
│   ├── simulation.py
│   ├── network/
│   │   ├── graph_builder.py
│   │   └── routing.py
│   ├── detectors/
│   │   ├── connectivity.py
│   │   ├── turn_restriction.py
│   │   ├── oneway.py
│   │   └── road_change.py
│   ├── evidence/
│   │   └── fusion.py
│   ├── scoring/
│   │   ├── severity.py
│   │   └── confidence.py
│   ├── business/
│   │   └── impact.py
│   ├── replay/
│   │   └── route_replay.py
│   └── benchmark/
│       ├── fault_injection.py
│       └── metrics.py
├── sql/
├── tests/
└── docs/
```

## 5. 对现有仓库的保留策略

优先复用：

- `src/ingest.py`
- `src/validation.py`
- `src/storage.py`
- `src/road_matching.py`
- `src/simulation.py`
- DuckDB 与 pytest 体系

后续逐步替换当前 `app.py`，但不在一个提交里重写所有模块。

## 6. 每次分析运行

每次运行创建一个 `analysis_run`：

```text
run_id
source_batch_id
network_version
algorithm_version
rules_version
started_at
finished_at
status
record_count
road_count
trajectory_count
parameter_json
```

同一份输入 + 同一版本代码 + 同一参数，应生成可重复结果。

## 7. 性能策略

因为是面试 Demo，优先使用以下手段：

1. 先裁剪研究区，再做分析；
2. 轨迹预清洗后再 Map Matching；
3. 使用空间索引，避免逐点遍历全路网；
4. 将 road graph 缓存；
5. 将静态 reference network 与动态 analysis result 分离；
6. UI 只加载当前视窗或当前 Issue 的详细轨迹。

目标不是“支持亿级数据”，而是在 50k–150k 轨迹点规模下稳定、可解释、可重复。

## 8. 数据版本策略

所有数据处理遵循：

```text
raw/reference → clean → feature/result
```

禁止覆盖原始输入。任何派生表必须包含 `run_id` 或 `data_version`。
