# STEP 6：可解释的问题检测、证据评分与潜在业务影响

本步骤只根据**当前待诊断路网、轨迹观测和反馈**生成疑似问题，不读取 Golden、注入位置或故障答案。所有结果均为 `needs_review`，不能直接宣布现实地图错误。原始 STEP 2 规格中的 Golden/reference support 不进入检测或评分。

## 1. 运行与交付

在仓库根目录使用现有 Python 依赖：

```bash
# 已有 STEP 4/5 输出时可跳过前两条；每次使用新的输出目录。
python scripts/04_build_golden.py --output data/runtime/xuhui_golden
python scripts/05_build_benchmark.py --output data/runtime/benchmark_seed42
python scripts/06_analyze.py --inputs data/runtime/benchmark_seed42/inputs/manifest.json --output data/runtime/analysis_seed42
python -m pytest -q
```

`--rules` 可指定分析规则，`--business-rules` 指定潜在影响映射。运行目录不可覆盖；输入文件不修改。当前文件入口要求 V2 五实体的规范化可观测字段，不能直接把原始 OSM JSON 或带答案的 Benchmark 根清单作为输入。空轨迹/空反馈要提供有完整列的空实体，缺失实体不是空数据。

| 文件或模块 | 职责 |
| --- | --- |
| `config/analysis.json` | 所有检测阈值、证据权重、支持样本数 |
| `config/business_impact.json` | 四类问题 × 五个业务场景的潜在影响级别 |
| `src/analysis_inputs.py` | 可观测列白名单、版本/拓扑检查、拒绝记录审计 |
| `src/network/observation_matching.py` | 米制空间索引匹配、片段行程、观测转移 |
| `src/detectors/` | 连接、转向、单行、缺路四类规则 |
| `src/evidence/fusion.py` | 去重、样本分母、时间持续性、来源引用 |
| `src/scoring/confidence.py` | 可展开的证据评分 |
| `src/business/impact.py` | 潜在影响及由影响级别得出的 Severity |
| `src/analysis_pipeline.py` | 组合入口与可复现运行记录 |
| `src/data_sources/analysis_results.py`、`sql/v2_analysis_schema.sql` | 独立结果数据库和类型/关联约束 |

输出为独立 `analysis.duckdb`，以及以下 Parquet 表：

- `road_issue`：对象、位置/几何、触发指标、根因假设、建议动作、Severity、Confidence、来源是否含 synthetic。
- `issue_evidence`：每个 Issue 的五个证据维度、指标、权重、贡献、实际轨迹序号范围/反馈 ID 和实体来源。
- `business_impact`：每个 Issue 五个场景的潜在影响、解释及明确为空的实测收益字段。
- `matched_trajectory_point`：全部通过基础校验的点，包括 matched、ambiguous、needs_review、unmatched、quality_excluded。
- `trajectory_passage`：由匹配点得到的方向片段、点数、位移、时间和方向一致性。
- `rejected_trajectory_point`、`rejected_user_feedback`：原输入行号、拒绝原因，以及保留原值的 `raw_record_json`。
- `accepted_user_feedback`：通过基础校验的反馈及投影坐标。

数据库还包含 `analysis_run`；根 `manifest.json` 最后写入，记录输入和结果 hash、Git/算法/参数版本、操作系统与计算库、来源、质量统计及运行时间。运行耗时统计覆盖读取到分析与内容校验，不包含最终 Parquet/数据库写盘。

## 2. 数据质量与匹配口径

首先检查五实体字段、版本、明确的 synthetic/布尔标记、路网对象 ID、几何端点、转向引用和节点度数。带 `fault_id`、`golden_edge_id` 等额外字段的输入直接拒绝，不静默丢掉答案列再继续。

轨迹/反馈逐行校验经纬度、研究区范围、带时区时间、ID 与重复键；机动车点还检查非负整数序号、1–130 km/h 速度、0–360° 航向、motorcar 类型和时间单调性。重复键的所有副本都拒绝，防止任意保留一条形成证据。数值/时间转换前的原值保留在拒绝审计中；研究区采用路网经纬度包围盒向外扩 0.01° 的粗筛选，不是精确裁剪边界。

使用 EPSG:32651 的 STRtree 空间索引。候选道路按 `距离 + 8 × 轴向航向差 / 90` 排序，**不使用当前单行合法方向过滤候选**，以免消除反向冲突证据。

- 距离 ≤12 米、轴向航向差 ≤35°，且次优候选成本至少差 2 米，记为 matched。
- 条件满足但多个道路太接近，记为 ambiguous；35 米内不满足可靠条件的记为 needs_review；范围外记为 unmatched。
- 相邻观测推导速度超过 130 km/h，相关端点标为 quality_excluded；记录仍保留，但不提供规则支持。
- `match_score` 由距离与轴向航向差归一化而来，是局部匹配质量提示，不是校准概率。
- 匹配成功率 = matched 点数 / 通过基础校验点数；匹配距离均值/P95 也只针对 matched 点。**这不是地图匹配正确率**，没有人工真值标签校准。分母为 0 时输出 null。

行程片段可以跨过最多 4 个序号差、8 秒内的非可靠匹配点；片段保留首末观测和方向一致性。转移要求前段靠近出口、后段靠近入口（各 30 米以内），排除边界道路。引用范围的端点必须是实际输入点，范围内部不保证每个点都被匹配采用。此方法是局部方向/距离匹配，不是 HMM 或完整路径匹配，也不保证路口每个短连接段都能识别。

## 3. 四类检测规则

公共门槛默认至少 4 个不同 `trajectory_id`、至少 2 个不同的 6 小时时间桶。同一行程重复点/片段不能充当多辆车或多条独立行程；时间桶表示样本分布，不表示连续几天持续发生。

| 类型 | 触发条件 | 主要保守限制 |
| --- | --- | --- |
| CONNECTIVITY_BREAK | 观测从一个节点附近跨到另一个，距离 0.5–12 米；一端度数 1、另一端至少 2，当前没有直接边；达到公共样本门槛 | 边界不报；几何靠近或反馈单独出现不报；仍需核验立交、门禁、真实通行 |
| ONEWAY_DIRECTION_CONFLICT | 当前为单行；至少两点、投影位移 ≥15 米且方向一致的片段；反向行程占全部有效行程 ≥75% | 排除 service/living_street、非 motorcar、边界和歧义匹配；同一行程双向出现计入分母但不作纯反向支持 |
| TURN_RESTRICTION_CONFLICT | 已有限制：至少 8 个可观测入口行程，受禁止转移的行程比例 ≥50% 且达到公共支持门槛 | 尊重 no/only 的具体有向边身份；当前方向不合法的边不用于转向判断，避免单行错误串成转向错误 |
| TURN_RESTRICTION_CONFLICT | 疑似缺限制：当前允许、有多个出口，至少 8 个入口行程，某出口转移占比 ≤5%，同时附近存在 turn_not_allowed 反馈 | 单纯低转向率不报；反馈不识别精确转向，可能关联多个候选；评分上限 0.69，需求偏差是替代解释 |
| MISSING_OR_CHANGED_ROAD_CANDIDATE | 连续至少 3 点离当前道路 ≥18 米；首尾位移 ≥30 米，首尾距离/折线长度 ≥0.7，航向连续；20 米内、轴向角度接近的走廊聚类达到公共门槛 | 单点漂移不报；排除边界；对象是新 corridor ID，不伪装成已知被删除 segment；系统性定位偏差仍可能形成假候选 |

走廊聚类使用连通分组，可能合并邻接的相似方向片段；弯曲缺路、密集平行道路和很短的道路容易漏掉。转向候选不承诺“一条投诉唯一定位一个转向”；同一路段掉头、via-way/conditional 限制和跨多个短连接段的转向不在当前识别范围。反馈按类型与到候选几何的 35 米距离关联，同一投诉可以支持多个附近候选；它们不是独立验证样本。

**问题数量不是 TP 数量。** 本步骤不匹配注入答案，不计算 Precision/Recall/FPR；这些指标需要 STEP 9 的对象级匹配与评估分母。默认运行摘要仅报告真实计算的候选数、匹配状态与质量统计。

## 4. Confidence：证据评分

`confidence_kind="uncalibrated_evidence_score"`，`calibrated=false`。

| 维度 | 默认权重 | 计算 |
| --- | --- | --- |
| data_quality | 0.15 | 行程内质量均值，再按行程平均，乘全批有效点比例（含排除运动跳变） |
| trajectory_support | 0.35 | 不同行程数 / 10，最高 1 |
| topology_support | 0.25 | 规则强度：明确当前属性/断点/限制为 1，离路走廊为 0.8，疑似缺限制为 0.5；这是设计权重，不是实测概率 |
| feedback_support | 0.15 | 不同反馈 ID 数 / 3，最高 1；缺反馈为 0，不重新分配权重 |
| persistence | 0.10 | 不同 6 小时时间桶数 / 4，最高 1 |

先求加权和，再乘 `1 - 0.5 × contradiction_fraction`，最后应用规则上限。单行反向候选的矛盾比例为非纯反向行程占比；已有限制冲突为未观测到该禁止转向的占比；疑似缺限制为实际观测到该允许转向的占比。这些只是透明的启发式折减。每个结果保存各维度、贡献、折减、上限和原始分母。

GPS 匹配、拓扑、反馈可能相关；合成轨迹与合成反馈来自同一实验设计。没有独立标签校准前，不能把 0.85 写成“85% 准确”或“85% 地图一定有错”。

## 5. Severity 与业务影响

Severity 取该问题五个场景的最高**潜在影响级别**，不由异常点数或 Confidence 决定。默认所有类型的导航/路线规划潜在影响为 HIGH，ETA/调度为 MEDIUM；POI 场景按类型为 LOW 或 MEDIUM，全部规则在配置中。默认 Severity 因此均为 HIGH，是人工映射的潜在等级，不能用来假装实测影响排序。

输出覆盖 navigation、routing、eta、dispatch、poi_local_service，附场景原因。`observed_supporting_trajectories` 只表示样本支持数量，不代表业务受影响用户数。`assessment_kind="potential_not_replayed"`；距离变化、ETA 变化为 null，`no_path_verified=false`。本步禁止配置未经验证的 CRITICAL 影响。后续页面切换场景应仅选择对应影响行，不重新计算事实或 Confidence。

## 6. 可复现、隔离与测试

分析无需随机数。`analysis_id` 由输入内容、规则与实现指纹确定；每次执行有不同 `run_id` 和检测时间。`first_observed_at/last_observed_at` 是证据观测范围，`first_detected_at/last_detected_at` 是本次分析时间，当前不维护跨批次工单历史。

比较重跑结果时使用清单的 `result_content_sha256_excluding_run_fields`，明确排除 `run_id`、`first_detected_at`、`last_detected_at`。产物字节 SHA-256 用来检查文件完整性；因为运行字段与数据库文件布局不同，不要求两个数据库二进制相同。平台、底层计算库或输入浮点值变化可能改变内容指纹，需按完整环境复核。

测试使用独立小路网验证正反例；完整测试从公开 OSM 生成 STEP 5 数据后只复制 `inputs/` 到独立目录，第二次执行阻止导入生成器和读取原 Golden/答案目录，比较全部结果内容，并检查输入文件未改写。另覆盖错误数据审计、重复样本、空输入、歧义匹配、已有 no/only 限制、缺反馈不报、反向保留、缺路连续性、评分可重算和数据库关联。这是程序依赖与已知路径的防泄漏检查，不是操作系统安全隔离。

默认运行的计算摘要与内容指纹保存于 `data/analysis/default_summary.json`；它记录疑似问题数量，不记录 TP/FN。完整输出保留在本地忽略的运行目录，可通过上述命令重建。

原有六个模块及应用保留；STEP 4/5 的规范化与生成实现未修改。Route Replay、自动修复、真实业务收益、准确率评估和新页面不在本步范围。下一步 STEP 7 需要单独批准。
