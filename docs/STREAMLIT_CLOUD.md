# Streamlit Community Cloud 免费部署

状态：部署入口已准备；尚未获得实际 Streamlit 应用网址，不将预期域名标记为已上线。

2026-09-22 本地验收：273 项 Python 测试、6 项前端模型测试通过；专用入口的真实浏览器验收通过，覆盖上传实际分析、访问码登录、核验保存、退出隔离及两种桌面宽度。以上是本地结果，尚未完成 Community Cloud 上的运行验收。

## 发布配置

先确保这些文件已推送到 GitHub 的 `roadinsight-v2` 分支，再在 [Community Cloud](https://share.streamlit.io/) 登录并创建应用。填写：

| 字段 | 值 |
| --- | --- |
| Repository | `ashazz315/roadnet-quality-ops-duckdb` |
| Branch | `roadinsight-v2` |
| Main file path | `streamlit_cloud.py` |
| Python version | `3.12` |
| App URL | 可尝试 `roadinsight-v2-ashazz315`，是否可用以平台为准 |
| Sharing | Public，允许访客直接打开 |

选择免费 Community Cloud，无需购买域名。依赖使用仓库根目录的 `requirements.txt`，演示数据已包含在仓库中。不要选择旧版 `app.py` 或用于 Docker 的 `scripts/13_serve.py`。

## 编辑功能

不填 Secrets 即以访客模式启动，五页演示可浏览。需要上传分析和核验时，在应用的 Advanced settings / Secrets 配置顶层值：

```toml
ROADINSIGHT_EDITOR_TOKEN = "替换为你自己生成的至少32字符随机访问码"
```

不要直接使用以上占位文字，不要把真实访问码写入 GitHub 或聊天。入口强制使用受保护的在线模式；未配置访问码时不允许登录编辑，短访问码会明确报错。保留共享编辑工作区模型，不包含注册账号或不同编辑者之间的隔离。

## 免费存储边界

上传文件、分析任务、核验和旧版批次保存在临时目录。它们可在同一实例存活期间使用，但休眠、重启、重新部署或平台清理可能使其丢失。页面在所有标签页显示提示；编辑者可下载当前结果、核验记录和已有 Excel 日报，并应保留原始输入。

这不是长期存储服务，尚未接入 Supabase。当前最多 50 个任务，并串行运行分析；免费计算资源能否承载实际样本须以部署后的验收为准。

## 发布后再更新 GitHub 链接

1. 等待部署完成，使用未登录窗口打开实际 `*.streamlit.app` 网址。
2. 核对五页、18 个诊断候选、28 组假设回放及临时存储提示。
3. 配置访问码后验证编辑登录、示例包分析、核验、导出和退出隔离。真实私人数据不用于公网验收。
4. 将实际网址加入 `roadinsight-v2` 的 README 顶部及完整服务说明，并更新仓库 About 的 Website。Pages 地址保留为只读备用入口；不修改 `main`。
5. 只有确认可访问后，才将文档状态改为“已上线”。

参考：[官方部署步骤](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)、[本地文件不保证持久保存](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)。
