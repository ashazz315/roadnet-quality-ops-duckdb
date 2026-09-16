# RoadInsight V2 页面使用说明（STEP 8–9）

## 启动

在仓库根目录安装 `requirements.txt`，然后运行：

```sh
python -m streamlit run roadinsight_app.py
```

Windows 可使用 `run_roadinsight.bat`。原应用仍用 `python -m streamlit run app.py`，六个旧模块继续保留。新入口不需要 Node 构建或重新运行分析；仓库附带经过内容校验的只读页面快照。

## 五个页面

| 页面 | 已实现内容 |
| --- | --- |
| Dashboard | 城市横幅、五项指标、地图、问题分布、潜在业务影响、最近问题、分析流程 |
| Map Diagnosis | 筛选／图层、地图／问题表、选中问题／证据／建议三栏联动 |
| Issue Detail | 局部地图、真实证据时间线、多源证据、假设回放；右栏问题概览、潜在影响、根因假设、人工核验 |
| Validation | 数据质量、运行来源、真实运行记录；独立合成 Benchmark 指标、逐对象明细及重复性／耗时记录 |
| Data Management | 快照来源与下载、CSV/XLSX 校验、保存上传批次、Excel 日报、人工核验记录 |

页面按用户 PRD 和四张参考图实施，采用固定顶栏、蓝白卡片、左侧导航及上海城市装饰。地图显示当前约 3×3 km 样本，不绘制未经提供的全徐汇行政边界。1680×945 和 1440×900 为桌面验收尺寸；长证据与回放可纵向滚动。小屏会重排，不等同于桌面截图。

## 演示路线

1. 从 Dashboard 进入地图。调整类型、严重度、评分及观测日期；列表、标记、统计和右侧详情使用同一筛选集合。
2. 点击地图标记选择 Issue。同坐标多问题再次点击轮换，也可从问题列表选择。无筛选结果时不展示其他问题的证据。
3. 进入详情查看具体支持轨迹、用户反馈及评分分量；“完整证据与评分计算”提供原始来源和明细。
4. 切换同一问题的回放 OD，对比相同端点与参数下的两条路线。缺路候选没有可执行方案时显示待核验。
5. 填写核验人、结论及依据后保存人工记录。记录与分析结果分开，页面不会把模型结果自动标记为已核验。

示例禁转候选 `issue-05df7e154203f10da880` 的锚点 OD 从 402.2 m 变成 1105.4 m，说明添加约束可能使路线变长。全批次 28 组回放包含 10 组变短、8 组变长、5 组距离不变、4 组恢复可达和 1 组失去可达；不可达距离和 ETA 保持空值。

页面地址支持 `?page=detail&issue=<完整 Issue ID>`。同一浏览器标签页保留筛选和地图选择；不同快照使用独立状态。

## 真实数据口径

- 当前快照包含 1071 条道路、842 个节点、14 条当前转向限制、18 个检测候选、60004 个轨迹点和 36 条反馈。
- 轨迹和反馈为受控合成观测；底层路网源自 OSM，当前网络包含已注入的受控故障。不是企业线上运营数据。
- Confidence 是未校准证据评分。匹配成功率约 87.55% 是规则接受率；平均匹配距离约 1.91 m 不是已知真实定位误差。
- 业务条展示规则映射的潜在影响等级或问题数量，没有实测收益百分比。
- 修复为独立假设副本，未实地核验，不更新原网络。STEP 9 已计算当前合成 Benchmark 的 Precision、Recall、F1；TN 和 FPR 因负例全集未定义而留空，不用候选数量代替正确检测数量。
- OSM 在线栅格底图仅用于位置参照，其更新时间可能晚于分析快照。诊断以本地当前路网和观测为依据；选择“离线路网”可去除底图。底图请求失败时仍能显示本地道路、问题和轨迹。

## 数据与操作存放位置

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `ROADINSIGHT_UI_SNAPSHOT` | `data/demo/roadinsight-v2.json.gz` | 已生成的页面数据；旁边必须有 `.json.manifest.json` |
| `ROADINSIGHT_VALIDATION_REPORT` | `data/validation/default_report.json` | 与页面快照严格绑定的独立评估报告 |
| `ROADINSIGHT_REVIEW_DIR` | `data/runtime/ui_reviews` | 按分析 run ID 隔离的追加式人工记录 |
| `ROADINSIGHT_UPLOAD_DB` | `data/road_quality.duckdb` | 旧版上传批次数据库 |

上传最大 5 MB；必填字段为 `record_id,report_time,longitude,latitude,issue_type,source,description,region`。先预览校验结果，再明确保存；同一会话同一次上传不会重复入库。上传点文件不自动刷新 V2 四类路网诊断。人工核验是本地单用户演示记录，不包含登录、权限、电子签名或多人审核系统。

## 重建页面快照

使用同一组可观测输入、已完成分析和已完成回放：

```sh
python scripts/08_build_ui_snapshot.py --inputs data/runtime/benchmark_seed42/inputs/manifest.json --analysis data/runtime/analysis_seed42/manifest.json --replay data/runtime/replay_seed42/manifest.json --output data/runtime/ui_new.json.gz
```

参数的具体默认值可用 `--help` 查看。构建器检查输入内容指纹、分析／回放来源、导出文件校验和及路径范围，拒绝覆盖已有输出；不读取 Golden 或故障答案。组件启动时验证压缩文件及语义内容指纹；刷新页面不会重新检测或生成 Benchmark。预置快照来源和完整参数可在界面“查看完整来源”中查看。

## 验证

```sh
python -m pytest -q
node --test tests/ui_model.test.cjs
```

浏览器验收脚本需要单独安装 Playwright 及 Chromium，或通过 `ROADINSIGHT_BROWSER` 指定 Edge/Chrome。应用运行本身无需这些测试依赖。先启动独立 QA 服务并设置 `ROADINSIGHT_REVIEW_DIR` 与 `ROADINSIGHT_UPLOAD_DB` 为测试路径，再执行：

```sh
node scripts/08_verify_ui.cjs
```

默认 QA 地址为 `http://127.0.0.1:8502`，可用 `ROADINSIGHT_QA_URL` 修改；输出默认在忽略目录 `data/runtime/ui_qa`，可用 `ROADINSIGHT_QA_OUTPUT` 修改。脚本会保存测试核验与上传记录，检查筛选、地图点击、图层、详情身份、回放、不可达、深链接、刷新、核验、上传／保存／导出、断网底图降级，并输出两种尺寸截图与报告。CI 运行 Python 回归和 Node 纯展示计算测试；浏览器验收需本地单独运行。

## 实现与资产

Streamlit 提供会话与 Python 操作，`roadinsight_ui/frontend` 为本地 V1 自定义组件；展示计算与源分析模块分离。地图使用本地 Leaflet 1.9.4，许可证与 SHA-256 在 `frontend/vendor`；OSM 署名始终保留，在线底图遵守 [OSM 标准瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)，不预取或批量下载。

`assets/shanghai-skyline.png` 是本任务用 OpenAI 图像生成工具于 2026-09-16 生成的装饰画，不是地理数据或上海现状照片；标志与线性图标为项目内 SVG。用户 PRD 和参考图仅作为本地设计依据，未随代码发布。

相关接口依据：[Streamlit V1 自定义组件](https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro)、[Leaflet 发行与许可](https://leafletjs.com/download.html)。


## STEP 9 评估结果

当前 Precision 1.000、Recall 0.750、F1 0.857，24 个故障命中 18 个、漏检 6 个。完整规则、漏检对象、一键重复运行和性能口径见 [Benchmark Evaluation](BENCHMARK_EVALUATION.md)。页面数据快照保留 STEP 8 原始内容；独立评估报告通过校验后再显示。新快照若没有对应报告，准确率仍显示未计算。
