# RoadInsight V2 产品规格

## 1. 项目定位

RoadInsight V2 是一个面向路网数据运营岗位的轻量级面试 Demo。它不试图复刻完整地图平台，而是展示一条可解释、可验证的数据运营闭环：

**Detect → Diagnose → Validate → Fix → Replay**

系统使用公开 OSM 路网作为基础数据，结合小规模轨迹、用户反馈与人工故障注入，识别底层路网问题，并评估其对路线规划、导航导引、ETA、运力调度与 POI/本地生活可达性的潜在影响。

## 2. 核心目标

1. 证明可以从数据库/文件稳定提取并清洗路网相关数据。
2. 证明可以把轨迹、路网拓扑、用户反馈关联到同一个 Road Object。
3. 证明可以从异常现象反推可能的路网数据问题。
4. 证明结论不是“红点可视化”，而是有证据、有置信度、有业务影响说明。
5. 通过 Golden Network + Fault Injection + Route Replay 形成可量化验证。

## 3. 非目标

本项目不做：

- 上海全量路网实时处理；
- 实时街景；
- 完整车道级几何网络；
- 大规模分布式计算；
- 真实字节内部数据或内部接口；
- 自动写回真实地图数据库；
- 复杂多人协作与工单权限系统。

## 4. Demo 数据范围

建议固定为上海徐汇区内约 3 km × 3 km 的演示区域，控制规模：

- 路段：约 800–2,000；
- 节点：约 500–1,500；
- 轨迹点：约 50,000–150,000；
- 用户反馈：20–50 条；
- 注入 Benchmark 问题：20–30 个。

目标是在普通笔记本上完成秒级到十几秒级分析，同时保留足够空间复杂度。

## 5. 三个核心页面

### 5.1 Map Diagnosis

主工作台。核心任务是“发现问题 + 查看证据”。

页面元素：

- 顶部：区域、时间、问题类型、业务场景、数据版本；
- 左侧：图层控制和筛选；
- 中间：路网地图、异常路段、异常节点、轨迹证据、反馈点；
- 右侧：选中 Issue 的快速诊断卡；
- 底部：Top Issues / Data Quality / Run Metadata。

业务场景切换：

- Road Quality
- Navigation
- Route Planning
- ETA
- Dispatch
- POI / Local Service

切换业务场景只改变优先级和影响展示，不改变底层数据事实。

### 5.2 Issue Detail

完成“证据 → 根因 → 业务影响 → 修复验证”。

必须展示：

- Issue ID；
- Road Object（segment/node/turn）；
- Issue Type；
- Severity；
- Confidence；
- Evidence Summary；
- 用户反馈；
- 路网属性；
- 轨迹行为；
- Root Cause Hypothesis；
- Business Impact；
- Suggested Action；
- Before / After Route Replay。

### 5.3 Validation

回答“为什么相信结果”。

展示：

- 数据量；
- 有效率；
- Map Matching 成功率；
- 平均匹配距离；
- Benchmark 注入问题数；
- TP / FP / FN；
- Precision；
- Recall；
- 运行版本与参数。

## 6. 四类核心 Issue

仅保留四类核心问题：

1. `CONNECTIVITY_BREAK`
2. `TURN_RESTRICTION_CONFLICT`
3. `ONEWAY_DIRECTION_CONFLICT`
4. `MISSING_OR_CHANGED_ROAD_CANDIDATE`

任何新增 Issue 必须证明其能在面试中提供新的能力证据，否则不进入 V2。

## 7. 核心产品原则

### 7.1 Risk 不等于 Confidence

不使用模糊的“总风险分”作为主要结论。改为：

- `severity`: 问题造成业务影响的严重程度；
- `confidence`: 系统认为该问题真实存在的可信程度。

### 7.2 热力图是证据，不是结果

轨迹图层提供三种模式：

- All Trajectories
- Abnormal Trajectories
- Issue Evidence

当用户选中某个 Issue 时，优先只显示与该 Issue 相关的轨迹证据。

### 7.3 每个结论必须可追溯

任何 Issue 必须能追溯到：

- 数据批次；
- 路网版本；
- 算法版本；
- 参数版本；
- 轨迹证据；
- 用户反馈；
- 检测规则。

## 8. 面试演示主 Case

### Case A：Turn Restriction 缺失

现象：路线规划要求左转，但大量真实轨迹不左转，用户反馈“这里禁止左转”。

系统输出：

- 疑似 Turn Restriction 缺失；
- Confidence 高；
- Navigation / Routing 影响高；
- 修复 restriction 后 Route Replay 路线改变。

### Case B：Connectivity Break

现象：轨迹持续穿过某节点，但当前路网图断开，规划 No Path 或绕路。

系统输出：

- Topology Break；
- Route Found 前后对比。

### Case C：Oneway Direction Conflict

现象：路网标注单行方向与高比例轨迹方向相反。

系统输出：

- Direction Conflict；
- 影响导航、ETA、调度。

## 9. Definition of Done

V2 达成以下条件即可停止扩展：

- 三个页面可在线访问；
- 四类 Issue 至少三类有可演示案例；
- 能运行一套可复现的小样本数据；
- 能生成 Issue + Evidence + Confidence；
- 至少一个 Case 能完成 Before / After Route Replay；
- Validation 页面有真实计算得到的 Precision / Recall；
- README 能在 3 分钟内让面试官理解项目价值；
- 项目可在公开 GitHub 运行或在线打开。
