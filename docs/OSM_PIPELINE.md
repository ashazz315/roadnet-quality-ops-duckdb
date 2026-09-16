# STEP 4：可追溯的 OSM Golden Network

这是根据公开岗位要求与公开业务场景设计的求职 Demo。Golden 表示故障注入前的固定基准，**不表示已经人工确认道路真实、完整或当前有效**。

## 1. 本次固定数据

| 项目 | 值 |
| --- | --- |
| 区域 | 徐家汇—上海体育场周边约 3×3 km 矩形窗口 |
| WGS84 bbox，西/南/东/北 | 121.4205 / 31.1685 / 121.4520 / 31.1955 |
| 查询缓冲 | 各方向 0.002 度，仅获取边界上下文；输出裁剪回核心 bbox |
| 坐标与长度 | EPSG:4326 存储；EPSG:32651 米制投影计算长度 |
| 下载服务 | `https://overpass.kumi.systems/api/interpreter` |
| 服务报告的 OSM 数据时间 | 2026-05-31 22:37:44 UTC |
| 实际获取时间 | 2026-09-15 09:41:06 UTC |
| 原始对象 | 3,760 个 node、888 个 way、36 个 relation |
| 标准化对象 | 1,077 个路段、836 个拓扑节点、20 条转向关系 |
| 边界节点 | 84 个，不能直接当作异常断头路 |
| 静态车辆方向图 | 1,594 条有向边；38 个弱连通分量，含 31 个孤立节点 |

这个窗口不是徐汇区行政边界裁剪。Overpass bbox 按 OSM 节点筛选道路，并非几何覆盖完整性证明；缓冲可减轻边界遗漏，不能保证覆盖所有跨窗但无窗内节点的长边。

下载时间与数据时间刻意分开。服务返回的是较早的快照，本项目不将它称为“当前路况”或“最新地图”。首次服务超时后使用了备用服务；实际查询和 endpoint 已固定在 `source.json`。

## 2. 仓库文件与来源

```text
config/osm_xuhui.json                  可审阅的区域、道路类型、坐标系和查询参数
data/reference/xuhui_osm/
├── osm.json.gz                        原始响应压缩副本；保留响应内容
├── source.json                        请求、服务时间、下载时间、SHA-256、许可
├── network_summary.json               本次标准化结果、内容哈希、测试依赖版本
└── LICENSE.md                         数据署名与许可
scripts/03_fetch_osm.py                显式联网下载，拒绝覆盖已存在的目录
scripts/04_build_golden.py             离线构建
src/data_sources/                      读取、快照存储和适配器
src/network/                          标准化、对象校验、有向多重图
src/pipeline.py                       组合入口
sql/v2_network_schema.sql              独立 V2 快照数据库 schema
```

原始未压缩响应 SHA-256：

```text
c3cd1b73049629bae645cdc4a5e850534651016de1829e71f981bc8a1848ba7f
```

发布的数据来自 [OpenStreetMap](https://www.openstreetmap.org/copyright)，署名 **© OpenStreetMap contributors**，许可为 ODbL-1.0。原始数据及其派生路网快照沿用该数据许可；不要把仓库代码许可自动套用于数据。

接口与标签处理参考：[Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL)、[oneway](https://wiki.openstreetmap.org/wiki/Key:oneway)、[restriction relation](https://wiki.openstreetmap.org/wiki/Relation:restriction)。

## 3. 一次离线复现

从仓库根目录运行。依赖安装需要网络；下面的构建与测试使用仓库内已固定的原始数据，不访问 Overpass。

```bash
pip install -r requirements-dev.txt
python scripts/04_build_golden.py
python -m pytest -q
```

输出位于 `data/runtime/xuhui_golden/`：

```text
road_segment.parquet       GeoParquet，含 WGS84 geometry 与 geometry_wkt
road_node.parquet          GeoParquet，含 WGS84 geometry 与经纬度
turn_restriction.parquet   Parquet，含有方向的 from_edge / to_edge
normalization_audit.json   每个原始 way/relation 的处理结果及原因
network.duckdb             三张路网表、来源表、审计表、analysis_run
manifest.json              文件哈希、内容哈希、版本与运行摘要
```

所有运行时输出已忽略，不提交本地 DuckDB。目录不可覆盖；复跑使用新的输出目录：

```bash
python scripts/04_build_golden.py --output data/runtime/xuhui_golden_repeat
```

成功快照以最后写出的 `manifest.json` 为准。失败可能留下未完成目录，不应当作已完成快照读取；检查原因后使用新目录重试。

确实需要更新公开数据时，显式创建另一原始快照：

```bash
python scripts/03_fetch_osm.py --output data/reference/xuhui_osm_new \
  --endpoint https://overpass.private.coffee/api/interpreter
python scripts/04_build_golden.py --raw data/reference/xuhui_osm_new \
  --output data/runtime/xuhui_golden_new
```

更新下载会得到另一数据版本。不要覆盖固定样本，也不要要求在线查询重现历史数据字节。公开服务可能限流、超时或返回较旧数据；错误或含 `remark` 的部分结果不会被接受为完整下载。

## 4. 数据处理口径

### 路段与节点

- 只保留配置中的机动车道路类别；区域型 highway、多余查询成员、受限车辆通行道路、无法处理的方向值和缺失节点等进入审计。
- 按共享 OSM 节点、道路端点、转向 via 节点和障碍节点切分。中间几何点保留在线形中，不一律扩成拓扑节点。
- 不以几何交点自动连接道路；不同 OSM 节点 ID 即使坐标相同也不自动合并，避免错误连接高架与地面道路。
- 按原始行进顺序裁剪，保留平行道路、闭合线形及往返重叠几何。裁剪节点有 `boundary:` 前缀，它们表示裁剪位置，不表示新增真实道路。
- `network_version + object_id` 构成数据库主键范围，避免不同快照的相同 OSM 对象互相覆盖。
- `lanes` 或 `maxspeed` 无法解析时保持空值，原始标签保留在 `tags_json`；支持普通数字速度和 mph 转换，不自动填写默认限速。

### 方向与通行模式

当前模式为静态 motorcar。geometry 始终遵循 OSM way 节点顺序：

| direction | 图上的方向 |
| --- | --- |
| forward | from_node → to_node |
| reverse（包括 oneway=-1） | to_node → from_node |
| both | 两个方向分别保存有向边 |

显式 oneway 值优先于 roundabout/motorway 的隐式方向；机动车专属方向标签优先于一般方向。`oneway:bicycle=no` 不改变机动车单行方向。

访问权限依次检查 motorcar、motor_vehicle、vehicle、access。只纳入 yes/designated/permissive；private、destination、customers 等不作为一般机动车通行网络。含条件标签的路段保留但不自动设为可通行；有障碍或受限访问的节点不自动放行。

当前样本有 26 个节点被保守标为不可自动通行。孤立节点和多个连通分量可能来自真实路网、裁剪、访问权限或这些保守规则，**这里没有运行 Connectivity Break 检测**。

### 转向限制

支持单 from-way、单 via-node、单 to-way 的 `no_left_turn/no_right_turn/no_straight_on/no_u_turn` 以及 `only_left_turn/only_right_turn/only_straight_on`。生成有方向的 edge ID，保留 no 与 only 的区别，不推测歧义关系。

36 条输入关系中：

- 20 条成功关联为可支持的 via-node 约束；
- 8 条为 via-way 或不支持的成员类型；
- 3 条为多成员或其他不支持的成员数量；
- 5 条在裁剪、筛选或方向约束后找不到可关联成员。

没有为缺失限制补造答案。条件限制、模式豁免、歧义映射等均有明确审计原因。原始所有 888 个 way 和 36 个 relation 都有且仅有一条处理审计记录，源对象仍完整保存在原始响应中。

**当前 NetworkX 图不执行转向限制。** 约束作为图元数据保留，`turn_restrictions_enforced=False`；不能直接把 `nx.shortest_path` 结果当作合法导航路线。遵守转向约束的路由与 Replay 留到 STEP 7。

## 5. DataSource 与可复现性

文件适配器支持 CSV/XLSX、Parquet/GeoParquet；CSV/XLSX 复用既有 `ingest_bytes`，保留原始行与未知字段，不在读取时清洗。DuckDB 适配器以只读方式加载固定网络版本，表名和排序键由枚举限定。

两个适配器在构造时验证哈希并缓存快照；调用方得到独立副本。后续修改源文件或返回的数据帧，不会改变已经绑定的快照。不存在的实体、校验不符或读取失败抛出错误，不能冒充空表。轨迹与反馈在本步骤尚不存在，读取它们会报告不可用。

`analysis_run` 保存 run_id、data_version、network_version、query_version、algorithm_version、code_version、实际源码 hash、rules_version、参数 JSON、环境版本和带时区的起止时间。本步骤无随机生成过程，因此 seed 为 null。

从压缩包运行或 Git 无法读取时，code_version 显示 `unavailable-see-code-sha256`，不伪造提交号；实际构建源码摘要仍被记录。

数据版本绑定原始响应哈希、构建代码摘要、算法版本及标准化参数。源码换行规范化后再计算摘要。重跑比较对象内容哈希、图统计与审计，不比较随机 run_id、时间戳或 DuckDB 整文件哈希。

GeoParquet 二进制哈希用于验证某个文件未变化。跨不同依赖版本或平台不能承诺二进制完全相同；测试环境版本已记录在 `network_summary.json` 和运行元数据中。同一环境的两次构建会比较 Parquet 文件与表内容哈希。

## 6. 本步骤验收与后续边界

测试覆盖：真实样本离线重建、两种适配器内容一致、CSV/XLSX 保真、空限制表、哈希破坏、不可覆盖、数据库外键、混合版本、几何端点、反向单行、机动车/自行车区别、平行道路、边界往返、U-turn、via-way/条件/歧义拒绝和模块依赖。

原 `app.py`、六个业务模块、旧数据库 schema 和 STEP 2 六份规格均保留。新增 NetworkX，并明确声明 GeoParquet 所需 PyArrow 依赖。

尚未实现：Fault Injection、模拟轨迹、用户反馈 Benchmark、异常检测、Confidence、业务影响评分、合法路线搜索、Route Replay 和 V2 页面。下一个步骤是 STEP 5，必须单独批准。
