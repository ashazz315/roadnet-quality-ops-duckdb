# STEP 5：可复现的合成 Benchmark

这是根据公开岗位要求与公开业务场景设计的求职 Demo。基于 STEP 4 固定 OSM 快照构造故障、轨迹与反馈，**全部标注 synthetic，不代表字节内部数据或现实道路真值**。本步骤提供生成器和答案，不计算检测准确率，也不实现导航、修复或业务收益。

## 1. 离线复现

在 `roadinsight-v2` 分支仓库根目录，安装现有依赖后运行：

```bash
python scripts/04_build_golden.py --output data/runtime/xuhui_golden
python scripts/05_build_benchmark.py --golden data/runtime/xuhui_golden/manifest.json --output data/runtime/benchmark_seed42
python scripts/05_build_benchmark.py --golden data/runtime/xuhui_golden/manifest.json --output data/runtime/benchmark_seed42_repeat
python -m pytest -q
```

两个生成器都拒绝覆盖现有目录；已有 Golden 可直接使用。第二个 Benchmark 根清单中的 `artifacts` 应与第一个完全相同；根清单自身的运行 ID、开始/结束时间可以不同。测试会使用提交的 OSM 原始快照重建 Golden，并实际执行两次完整默认生成、比较全部产物 hash。

默认配置在 `config/benchmark.json`，可用 `--config <path>` 替换。输出写入已忽略的 `data/runtime/`；仓库保留生成器、原始公开 OSM、参数及 `data/benchmark/default_summary.json`，不提交多份运行数据。根 `manifest.json` 最后写入，作为完成标记；失败的半成品目录应使用新输出路径重跑。

## 2. 数据目录与使用边界

```text
benchmark_seed42/
  manifest.json                 # 完整运行、Golden 来源、参数、环境、产物 hash
  inputs/
    manifest.json               # 检测程序唯一输入入口，无 truth 路径
    road_segment.parquet        # 修改后的路段，GeoParquet / EPSG:4326
    road_node.parquet           # 修改后的节点，GeoParquet / EPSG:4326
    turn_restriction.parquet
    trajectory_point.parquet   # 含噪观测，无匹配道路 ID
    user_feedback.parquet      # 合成投诉，无 fault_id
  truth/
    faults.json                 # 操作前后对象、seed、四类标签、对应输入对象
    id_mapping.json             # 原始 ID ↔ 输入对象 ID（值为输入 ID）
    trajectory_paths.parquet    # Golden 边序列、目标 fault、采样相位
    point_truth.parquet         # Golden 边、干净坐标、真实噪声与离群标签
    feedback_truth.parquet      # 反馈关联 fault、支持/干扰标签
```

调用 `FileDataSource(Path(".../inputs/manifest.json"))` 读取五类 `InputEntity`。该清单只引用本目录文件；所有实体的 `is_synthetic=True`，版本关联本次 Corrupted Network。轨迹实际产生于 Golden，生成来源仅在评估侧根清单与 truth 中披露；输入 `network_version` 表示要诊断的地图版本。

输入路网只允许显式列出的可观测字段。所有节点、路段、限制统一换成不带语义的稳定编号，包括未修改对象；不输出原始 OSM ID、原始 tags 或 `split:` 等注入标记。保留道路名称、等级、车道/限速、几何、方向、边界与通行资格。删除道路的目标 ID 保留在答案映射中，但不出现在当前路段表；后续检测需按位置/几何匹配缺路候选。

**这不是安全沙箱，也不是盲测。** 源码和 OSM 公开，几何仍可关联；调用方仍可能错误地读取 truth。静态依赖检查阻止 detectors/evidence/scoring 直接依赖 Benchmark，测试检查输入字段和适配器边界。真正检测流程的端到端防泄漏验证在 STEP 6 接入时完成。

## 3. 故障选择和注入

默认 seed=42，四类各 6 个，共 24 个；候选不足直接报错，不静默降低配额。

| 类型 | 本次操作 | 选择与定位规则 |
| --- | --- | --- |
| CONNECTIVITY_BREAK | 几何终点缩短 6 米，接入独立节点 | 长度至少 50 米，原终点度数至少 3；答案定位新节点及关联路段 |
| TURN_RESTRICTION_CONFLICT | 删除已有静态转向限制 | 删除后确实新增一个原本不合法的可用转移；答案用 from/via/to 定位 |
| ONEWAY_DIRECTION_CONFLICT | 反转单行方向 | 保持几何顺序，修改方向及对应 tag；不改变道路几何 |
| MISSING_OR_CHANGED_ROAD_CANDIDATE | 删除整段道路 | 两端度数至少 2，删除后仍存在无向替代连通；答案保存原始几何 |

先选择限制，其他三类不修改任何 Golden 限制引用的道路/节点；这三类的目标端点彼此不重叠。候选必须可生成合法经过样本，排除边界附近的路径边。缺路筛选仅验证无向替代连通，**不承诺存在合法有向替代路线**。节点度数统一重算，保留可能孤立的节点；每次修改都有 before/after，Golden 不被改写。

这里只覆盖“删除限制、反转单行、终点断开、整段缺失”四种具体操作，不声称覆盖每类问题所有现实变体，也不保证所有注入都能被后续检测发现。

## 4. 合成轨迹

- Golden 上按边 ID 做受约束随机游走；尊重单行、静态 `no_*` / `only_*` 转向限制。平行道路保留独立身份；禁止同一路段立即掉头，排除边界、禁止通行的边与节点。它是生成用的游走器，不是 STEP 7 的 OD 路由器。
- 每个故障至少安排 8 条经过锚点边的行程；再增加背景行程，直到达到 60,000 点。完整保留末条行程，所以实际点数可略超目标；路径通常请求 8–18 条边，遇到死路可以提前终止。
- 每条行程速度固定，在 18–36 km/h 内抽样；每 2 秒采样。目标行程调整采样起点相位，使短转向进口道也能有观测点，时间间隔保持不变。相位只记录在 truth；这是主动增加目标覆盖的模拟偏差。
- 复用现有 `interpolate_on_line` 沿 WGS84 折线按长度加权插值；行程进度、噪声和方向使用 EPSG:32651 米制坐标。旧插值采用局部米/度近似，不是高精度动力学模型。
- 常规 XY 高斯噪声 sigma=2.5 米，向量长度截断为 8 米；每点以 0.5% 概率加入 20–35 米离群噪声。航向另加 sigma=3° 噪声。实际离群数由抽样计算，不强制凑比例。
- 时间固定在 2026-06-01 起的 48 小时内，UTC、带时区；行程随机打乱后编号，避免目标类型通过编号排序暴露。轨迹保留 ID、从 0 开始的连续序号、时间、经纬度、速度、航向及 synthetic 来源。

没有真实交通流量、拥堵、停车、驾驶员选择或设备模型；速度不代表实测通行速度，也未按每段限速建模。不存在 Map Matching 结果列，不能把生成时已知道路关联当成检测证据。

## 5. 合成反馈

默认 36 条：18 个不同故障各有至少 1 条反馈，额外 6 条重复支持，共 24 条；另 12 条为不关联注入故障的干扰投诉，放在距离所有故障代表位置超过 100 米的不同未选道路上，再添加至多 10 米位置扰动。6 个故障没有反馈。

支持和干扰反馈共享投诉类型与文本模板，混合打乱后编号。输入提供报告 ID、时间、位置、投诉类型、描述及 synthetic 来源；是否支持、关联哪个 fault 只在 truth 中。这里的“未选道路”仅指没有注入，不能证明现实中没有问题。干扰位置不是完整负例全集，不能据此计算 FPR/TN。

反馈和轨迹由同一 Benchmark 设计产生，并非独立现实证据；高覆盖和重复反馈不得解读为真实用户投诉率或检测置信度。

## 6. 版本、检查与后续

Benchmark 版本由 Golden 版本及三张表内容 hash、全部参数、生成相关源码指纹和算法版本共同确定。故障、路径、GPS 噪声、反馈使用确定性的独立随机流。根清单另记 Git 版本（开发树带 `+dirty`）、源码 hash、依赖版本、运行 ID、时区时间与每个产物 SHA-256。固定 Python/依赖环境重跑比较字节；跨依赖版本需先比较内容，不能承诺 Parquet 二进制相同。

自动测试检查：Golden 未修改、四类配额、目标修改与几何一致性、转向身份/方向、目标实际采样、轨迹连续性和噪声、反馈覆盖、输入字段隔离、所有产物重跑一致、配置拒绝、文件防篡改及旧模块回归。

OSM 来源遵循 ODbL-1.0，保留 © OpenStreetMap contributors，详见 `data/reference/xuhui_osm/LICENSE.md`。本数据是受控实验基线，不是现实正确率证明。Precision/Recall、Confidence、Business Impact、Replay 和 UI 尚未在本步骤实现。

下一步 STEP 6：Issue Detection + Confidence + Business Impact，需要单独批准。
