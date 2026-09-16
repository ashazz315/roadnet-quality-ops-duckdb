# RoadInsight V2 验证计划

## 1. 验证目标

Validation 页面必须回答三个问题：

1. 输入数据可靠吗？
2. 检测结果可靠吗？
3. 修复后业务结果真的变化了吗？

## 2. 数据质量验证

每个分析批次记录：

- 总记录数；
- 有效记录数；
- rejected 数；
- 坐标异常率；
- 重复率；
- 时间异常率；
- Map Matching 成功率；
- 平均 / P95 匹配距离；
- Low Confidence 比例。

如果 Map Matching 成功率低于阈值，Validation 页面应显示警告，不允许把结果包装成高可信分析。

## 3. Golden Network

选择一个小范围真实 OSM 数据作为基准网络快照。

保存：

- network_version；
- 获取日期；
- bbox；
- segment_count；
- node_count；
- restriction_count；
- hash。

Golden Network 只用于 Demo Benchmark，不宣称等同现实绝对真值。

## 4. Fault Injection

对 Golden Network 自动注入已知问题。

### 类型 A：Connectivity Break

随机选择适合的连接边，删除或断开拓扑关系。

### 类型 B：Turn Restriction

删除、增加或修改 restriction。

### 类型 C：Oneway

反转或错误设置 oneway 属性。

### 类型 D：Missing Road

从当前检测网络删除一段道路，但保留模拟轨迹证据。

每个 fault 保存可复现 seed 和目标对象。

## 5. 轨迹与反馈生成

轨迹生成必须基于 Golden Network 路径，而不是在 bbox 内随机撒点。

原则：

- 保留 trajectory_id；
- 保留 sequence；
- 加入有限 GPS noise；
- 对故障位置生成足够穿越样本；
- 用户反馈与对应 fault 绑定但不全部命中，避免过于“完美”。

## 6. 检测指标

定义：

```text
TP = 注入问题且系统正确发现
FP = 未注入问题但系统错误报警
FN = 注入问题但系统漏检
```

输出：

```text
Precision = TP / (TP + FP)
Recall = TP / (TP + FN)
F1 = 2PR / (P + R)
```

V2 不预设夸张目标值。先跑真实结果，再把结果如实展示。

## 7. Route Replay 验证

至少选择一个 Issue 做路线重放：

```text
Corrupted Network
→ route_before

Fixed Network
→ route_after
```

对比：

- route status；
- distance；
- ETA；
- path geometry；
- 是否产生绕行。

## 8. 可重复性验证

同一 benchmark seed 连续运行两次，必须满足：

- fault 列表一致；
- 数据 hash 一致；
- issue 检测结果一致；
- Precision/Recall 一致。

## 9. 运行元数据

Validation 页面显示：

```text
Run ID
Network Version
Data Batch
Algorithm Version
Rules Version
Seed
Started At
Duration
```

## 10. 验收条件

- 每类核心 Issue 至少有 3 个 benchmark fault；
- benchmark 可一键运行；
- Precision / Recall 来自程序计算；
- 至少一个 Route Replay Case 可视化；
- 失败数据不会被静默删除；
- 数据质量过低时页面给出明确告警。


## 11. STEP 9 实施结果

已实现独立的一对一类型／对象匹配、分类型和总体 Precision / Recall / F1、逐对象 TP / FP / FN、数据质量告警、两次完整重复运行、阶段耗时与 UI 展示。FP 的准确含义是相对注入故障的未匹配候选；无完整负例全集，因此 TN / FPR 未定义。完整口径、结果、测试和一键入口见 [BENCHMARK_EVALUATION.md](BENCHMARK_EVALUATION.md)。当前样本每类 6 个故障，总体 18 TP / 0 FP / 6 FN，不声称为现实正确率。
