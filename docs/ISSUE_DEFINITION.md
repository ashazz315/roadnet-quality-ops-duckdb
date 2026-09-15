# RoadInsight V2 Issue 定义

## 1. 总原则

Issue Detection 的目标不是自动宣布“地图错了”，而是生成可供核验的**疑似路网数据问题**。

所有 Issue 必须同时给出：

- 触发规则；
- 最小证据；
- 置信度；
- 可能业务影响；
- 建议核验动作。

## 2. CONNECTIVITY_BREAK

### 定义

相邻道路在几何或拓扑上应该连通，但路网图中存在异常断裂，可能导致 No Path、绕路或导航中断。

### 主要证据

- Road Graph 节点异常断开；
- 大量轨迹跨越断裂位置；
- 用户反馈“导航中断/绕路”；
- Golden Network 中原本连通。

### Confidence 示例

```text
0.40 * trajectory_crossing_support
+ 0.30 * topology_conflict
+ 0.20 * feedback_support
+ 0.10 * persistence
```

## 3. TURN_RESTRICTION_CONFLICT

### 定义

当前路网中的转向限制与轨迹行为、用户反馈或 Golden Network 不一致。

可能情况：

- restriction 缺失；
- restriction 多余；
- restriction 类型错误。

### 主要证据

- OSM/当前路网允许某转向；
- 绝大多数轨迹不执行该转向；
- 用户反馈“这里不能转”；
- Route Replay 修复后路线明显合理。

### 注意

“轨迹很少左转”本身不能证明禁止左转，需要多证据融合。

## 4. ONEWAY_DIRECTION_CONFLICT

### 定义

道路 `oneway` 或方向属性与轨迹方向分布显著冲突。

### 主要证据

- 当前属性为单行；
- 大比例高质量轨迹持续反向行驶；
- 用户反馈方向错误；
- 与 Golden Network 不一致。

### 过滤条件

必须过滤低质量 GPS、步行/骑行轨迹、服务道路等不适用场景。

## 5. MISSING_OR_CHANGED_ROAD_CANDIDATE

### 定义

存在稳定轨迹走廊或用户反馈，但当前 reference network 中没有对应道路，或几何形态与现实行为明显不一致。

### 主要证据

- 多条不同 trajectory_id 在相似走廊聚集；
- 匹配距离持续高于阈值；
- 轨迹方向连续；
- 用户反馈“道路缺失/路线绕行”；
- 非单点 GPS 漂移。

## 6. Severity

Severity 不直接来自异常数量，而由业务影响决定。

建议等级：

- LOW：轻微显示或局部属性问题；
- MEDIUM：可能影响 ETA/局部路径；
- HIGH：明显影响路线规划/导航；
- CRITICAL：造成 No Path、导航中断或大范围错误路径。

## 7. Confidence

Confidence 表示“系统有多确信问题真实存在”。

建议维度：

- data_quality_score
- trajectory_support
- topology_support
- feedback_support
- persistence_score
- benchmark/reference_support

统一输出 0–1。

## 8. Business Impact Mapping

### Connectivity Break

- Routing: CRITICAL
- Navigation: CRITICAL
- ETA: HIGH
- Dispatch: HIGH
- POI/Local Service: HIGH

### Turn Restriction Conflict

- Routing: CRITICAL
- Navigation: CRITICAL
- ETA: MEDIUM/HIGH
- Dispatch: MEDIUM
- POI/Local Service: MEDIUM

### Oneway Direction Conflict

- Routing: HIGH
- Navigation: HIGH
- ETA: MEDIUM
- Dispatch: MEDIUM/HIGH
- POI/Local Service: LOW/MEDIUM

### Missing/Changed Road Candidate

- Routing: HIGH
- Navigation: HIGH
- ETA: HIGH
- Dispatch: MEDIUM/HIGH
- POI/Local Service: MEDIUM/HIGH

## 9. Issue 输出示例

```json
{
  "issue_id": "ISSUE-017",
  "issue_type": "TURN_RESTRICTION_CONFLICT",
  "object_type": "turn",
  "object_id": "turn:102-88-211",
  "severity": "HIGH",
  "confidence": 0.92,
  "root_cause_hypothesis": "possible missing no_left_turn restriction",
  "evidence": {
    "trajectory_count": 1284,
    "observed_turn_rate": 0.043,
    "feedback_count": 8
  }
}
```
