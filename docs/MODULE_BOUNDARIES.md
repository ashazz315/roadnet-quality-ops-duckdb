# RoadInsight V2 — STEP 3 模块边界

本项目是**根据公开岗位要求与公开业务场景设计的求职 Demo**，不代表字节跳动内部架构，不使用内部数据。

本文件补充 STEP 2 六份规格，不替换原文。STEP 3 只建立类型、数据源接口、包目录和依赖检查；包目录存在不表示算法已经完成。

**STEP 4 更新**：数据源适配器、OSM 标准化、网络校验和图构建现已实现，详见 [OSM 数据管线](OSM_PIPELINE.md)。本文件下文保留 STEP 3 的阶段记录；路由、检测与评估仍按各自后续步骤实施。

## 1. 当前可运行能力与复用方式

| 已有模块 | 已实现能力 | V2 使用边界 |
| --- | --- | --- |
| `src/ingest.py` | UTF-8 CSV / XLSX 读取，保留原始字符串与空值 | 文件适配器复用读取函数；OSM、Parquet 接入待 STEP 4 |
| `src/validation.py` | 上传点记录校验，valid/rejected/summary | 保留旧字段规则；路段、节点、转向、轨迹专用校验待 STEP 4 |
| `src/storage.py` | 旧四表初始化、批次事务写入 | 保留旧持久化；V2 schema 和运行记录另行设计，不迁移现有表 |
| `src/road_matching.py` | 米制投影下最近道路匹配 | 可复用空间处理；尚非利用轨迹顺序、方向和拓扑的 Map Matching |
| `src/simulation.py` | 道路插值、点偏移与属性故障标签 | 可复用几何与随机种子处理；尚非 Golden Network 路径轨迹生成器 |
| `src/daily_report.py` | 日报 SQL 与 Excel 导出 | 保留旧工作流；不充当 V2 Benchmark 指标 |
| `app.py` / `quality_mvp/` | 上传、校验、入库、地图、日报 | 当前入口保持可用，三个 V2 页面到 STEP 8 再实现 |

现有六类模拟异常仍属于旧演示，不能映射为已完成的 V2 四类检测能力。现有 `data/reference/roads.geojson` 也未被认证为徐汇 Golden Network。

## 2. STEP 3 新增结构

```text
src/
├── domain.py                 四类 Issue、Severity、检测输入实体名称
├── data_sources/
│   ├── __init__.py
│   └── base.py               DataSource Protocol、SourceMetadata、读取错误
├── network/__init__.py       图构建与路由职责
├── detectors/__init__.py     四类候选问题检测职责
├── evidence/__init__.py      证据关联与聚合职责
├── scoring/__init__.py       Severity / Confidence 职责
├── business/__init__.py      业务影响说明职责
├── replay/__init__.py        修复前后路线对比职责
└── benchmark/__init__.py     故障注入与评估职责
tests/test_v2_boundaries.py   静态依赖约束、无第三方依赖的导入检查
```

STEP 2 架构中列出的 `graph_builder.py`、四个 detector 文件、`fusion.py`、`route_replay.py`、`metrics.py` 等具体实现文件，在对应步骤获批后创建。当前不放置返回空结果、假分数或假成功的函数。

## 3. DataSource 接口

`DataSource` 是类型协议，尚无具体适配器或运行时 schema 校验。未来 CSV/XLSX、Parquet/GeoParquet、DuckDB 或数据仓库查询结果适配器提供同一接口：

- `metadata(entity) -> SourceMetadata`：该实体的来源引用、数据版本、路网版本、是否合成；
- `read(entity) -> DataFrame`：独立可修改的数据帧；空间输入可返回 GeoDataFrame 并保留 CRS。

允许读取：`road_segment`、`road_node`、`turn_restriction`、`trajectory_point`、`user_feedback`。不存在通用 SQL 执行或任意表名读取入口。`benchmark_fault`、Golden 答案和 `expected_*` 字段不属于检测输入。

适配器必须保持：

1. 同一实例绑定固定快照，metadata 与 read 的版本一致；
2. 保留原始行、字段和顺序，清洗由下一层负责；
3. 不写源文件或源数据库，不悄悄丢弃非法记录；
4. 空表代表实体存在且为空；缺失、损坏或不支持时抛出 `DataSourceError`；
5. 混合数据中分别标识真实路网、模拟轨迹、模拟反馈的来源，不能用一个全局“真实数据”标签覆盖所有实体。

## 4. 数据与模块依赖方向

```text
组合入口（未来的离线分析命令 / UI）
  → DataSource → 专用校验 → 标准化输入 / Road Graph
  → Detectors → Evidence → Confidence、Severity、Business Impact
  → Replay → 结果持久化 / 展示

Benchmark（独立评估入口）
  Golden → 注入故障 → Corrupted + 可观察证据 → 上述检测流程
  Expected labels ─────────────────────────→ 结果评估
```

依赖约束：

| 层 | 可以依赖的其他 src 层 |
| --- | --- |
| domain | 无 |
| data_sources | domain、现有 ingest |
| network | domain |
| detectors | domain、network |
| evidence | domain |
| scoring | domain、evidence |
| business | domain |
| replay | domain、network |
| benchmark | domain、network、detectors、evidence、scoring、business、replay、现有 simulation |

各层可以依赖自身包。分析层通过参数接收数据，不直接导入 Streamlit/Folium/PyDeck、DuckDB 或旧 storage；DuckDB 读取归适配器负责。组合入口负责串联，模块不能隐式读取 UI session state。

`test_v2_boundaries.py` 检查普通与相对 Python import，并阻止检测代码直接依赖 Benchmark、旧带标签模拟器、存储和 UI。**这是静态依赖检查，不是数据泄漏隔离系统**；不能阻止动态 import、直接文件读取或调用方把答案塞进 DataFrame。STEP 5/6 仍须增加输入字段隔离和答案不进入检测流程的端到端测试。

## 5. 实体与可复现运行边界

逻辑实体沿用 `DATA_MODEL.md`：road_segment、road_node、turn_restriction、trajectory_point、user_feedback、road_issue、issue_evidence、business_impact、analysis_run、route_replay、benchmark_fault、benchmark_result。STEP 3 不创建这些表。

后续 `analysis_run` 至少统一记录：

| 信息 | 对应字段 / 约束 |
| --- | --- |
| 输入快照 | `data_version`、`network_version`、`source_batch_id`，指向来源与内容 hash |
| 实现版本 | `code_version`（Git commit）、`algorithm_version`、`query_version`、`rules_version` |
| 随机性与参数 | `seed`、`parameter_json`，序列化顺序固定，不允许 NaN/Infinity |
| 运行身份与时间 | `run_id`、`started_at`、`finished_at`、`status`，时间带时区 |
| 环境 | Python 与依赖版本，必要时记录 CRS 与单位 |

`run_time` 对应开始时间与耗时；重跑时运行 ID 和实际时间可以不同，比较的是排序稳定的故障、证据、Issue 与指标。完整运行记录和可复现性实现留到后续数据管线。

## 6. 进入后续步骤前需要明确的口径

- **Golden 隔离**：原规格列出的 Golden/reference support 只适用于评估。检测、证据聚合和 Confidence 不得读取注入答案，否则 Precision/Recall 会泄漏真值。
- **Confidence**：原文中的权重和数字是设计示例。未经校准只能称为证据评分，不能当作真实准确率或已校准概率。
- **评估分母**：Precision/Recall 来自对象级一对一匹配；FPR 还需要定义负例全集与 TN，Detection Rate 需说明是否等同 Recall。分母为零的指标应标记为不可计算，不伪造 0% 或 100%。具体实现留到 STEP 5/9。
- **方向与版本**：STEP 4 需明确 OSM 反向单行与 segment 方向的表示，并解决同一对象跨版本的主键范围。
- **Replay**：修复禁止转向可能增加路线距离/时间。展示可达性、合法性及真实计算的变化；不可预设“修复一定更短”。ETA 为所用模型估计，不能称为真实出行时间。

## 7. STEP 3 验证与范围

```bash
python -m pytest -q
python -m compileall app.py src quality_mvp tests
```

CI 保留原有检查，只增加 `roadinsight-v2` 的 push 触发。没有新增运行依赖、修改旧上传逻辑、下载 OSM、建立 V2 表、生成 Benchmark 或开发新 UI。

下一步为 STEP 4：OSM Golden Network 与数据管线；须先确认数据区域、文件计划、验收标准并取得明确批准。
