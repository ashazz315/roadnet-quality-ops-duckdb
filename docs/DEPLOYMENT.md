# RoadInsight V2 部署与发布

在线工作台：[RoadInsight Streamlit](https://roadinsight-workbench-331907-5-1511207010.sh.run.tcloudbase.com/)。免费托管上的上传结果仅临时保存，编辑功能需要访问码；长期持久化与备份部署包见 [完整服务说明](FULL_SERVICE.md)。

当前首选公开入口：[腾讯云 RoadInsight V2](https://roadinsight-sy-d2g7eacoh8e6c1fae.webapps.tcloudbase.com/)。容器配置与更新步骤见 [腾讯云部署说明](CLOUDBASE.md)。GitHub Pages 和 Streamlit Community Cloud 保留为备选部署方式。

## 运行方式

| 方式 | 用途 | 可用功能 |
| --- | --- | --- |
| [腾讯云 Streamlit 工作台](https://roadinsight-workbench-331907-5-1511207010.sh.run.tcloudbase.com/) | 在线浏览、受保护的编辑体验 | 五页浏览；配置访问码后可上传分析与核验，结果仅临时保存 |
| [GitHub Pages 公开演示](https://ashazz315.github.io/roadnet-quality-ops-duckdb/) | 面试分享，无需登录 | 五页浏览、地图筛选、证据、假设回放、验证明细、快照下载 |
| 本地 Streamlit 完整版 | 上传处理与人工核验 | 上述全部功能，以及 CSV/XLSX 校验、DuckDB 保存、Excel 日报、人工记录 |

Pages 公开演示复用同一界面与经过校验的数据，不运行 Python 后端；页面提供 Streamlit 工作台入口。桌面验收尺寸为 1680×945、1440×900。

## 构建与预览

安装 Python 依赖后，在仓库根目录运行：

```sh
python scripts/10_build_public_demo.py --output data/runtime/public_demo
python -m http.server 8510 --bind 127.0.0.1 --directory data/runtime/public_demo
```

打开 `http://127.0.0.1:8510/`。输出目录必须是新的或空目录，脚本不会自动清理已有文件。

构建首先核对快照与独立评估报告的内容指纹及运行绑定，然后按白名单导出前端、装饰素材、Leaflet 许可证、页面 JSON 和评估 JSON。不会导出本地数据库、上传文件、人工核验日志、原始私人目录或整个仓库。浏览器再次核对两个 JSON 的 SHA-256；文件缺失、下载异常或校验失败时明确报错。

`release.json` 记录发布代码版本与页面快照版本；分析与评估保留原始生成代码版本，二者含义不同。

## GitHub 自动发布

Pages 使用 GitHub Actions 发布来源。仅 `roadinsight-v2` 的 push 可以触发部署，主分支和 PR 只运行检查。

CI 顺序为：Python 回归 → 前端模型 → 两轮完整流水线复现 → 构建公开站点 → Chromium 浏览器验收 → 上传站点产物 → Pages 部署。任何检查失败都不会执行部署。浏览器验收覆盖五页布局、空筛选、底图不可用、深链接、前进后退、只读入口、报告明细以及损坏数据拒绝加载。

测试任务只有只读仓库权限；部署任务使用 Pages 和短期身份令牌权限，不需要新增仓库密钥。发布目录只指向 `data/runtime/public_demo`。无需修改默认分支、DNS 或付费服务。

首次启用需要仓库管理员设置 Pages 的来源为 GitHub Actions，并允许 V2 分支部署到 `github-pages` 环境。参考 [GitHub 自定义 Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

## 本地完整版配置

```sh
python -m streamlit run roadinsight_app.py --server.address 127.0.0.1
```

| 可选环境变量 | 默认值 / 用途 |
| --- | --- |
| `ROADINSIGHT_UI_SNAPSHOT` | `data/demo/roadinsight-v2.json.gz`，与同目录 manifest 一起使用 |
| `ROADINSIGHT_VALIDATION_REPORT` | `data/validation/default_report.json`，必须与快照绑定 |
| `ROADINSIGHT_REVIEW_DIR` | `data/runtime/ui_reviews`，追加式人工记录 |
| `ROADINSIGHT_UPLOAD_DB` | `data/road_quality.duckdb`，本地上传批次 |

免费 Community Cloud 使用本仓库、`roadinsight-v2` 分支和专用 `streamlit_cloud.py` 入口，配置与验收见 [免费部署说明](STREAMLIT_CLOUD.md)。该入口始终启用在线权限检查；上传与核验使用临时存储，页面明确提示并提供结果导出。

## 发布后检查与回退

1. 确认本次 CI 的测试、部署均成功，站点能打开。
2. 核对线上 `release.json` 的代码版本与发布提交一致。
3. 打开关键案例和验证中心，确认 18 个候选、28 组回放、18 TP / 6 FN。
4. 出现问题时保留失败日志，在 V2 分支撤销有问题的提交，再由同一检查流程发布；不强制改写历史，不修改 `main`。

Pages 和第三方底图依赖网络；底图不可用时本地几何仍显示。面试时可提前启动本地完整版作为备用，不能保证任意网络环境下第三方服务都可达。
