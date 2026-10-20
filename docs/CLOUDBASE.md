# RoadInsight V2 腾讯云部署

- 公开演示：[RoadInsight V2](https://roadinsight-sy-d2g7eacoh8e6c1fae.webapps.tcloudbase.com/)
- 在线工作台：[Streamlit 后端](https://roadinsight-workbench-331907-5-1511207010.sh.run.tcloudbase.com/)
- 环境：sy（ap-shanghai），CloudBase 体验版；开发发布分支：roadinsight-v2。

## 公开演示

使用 scripts/10_build_public_demo.py 导出经过数据指纹校验的公开资源，部署产物目录 dist。CloudBase 应用 serviceName=roadinsight，appPath=/roadinsight，framework=static，buildPath=dist，installCmd 和 buildCmd 为空。独立子域名的根路径映射至该独立存储目录，不与其他应用共用根目录。

腾讯云测试域名首次打开可能显示平台访问提示；等待倒计时并点击“确定访问”即可进入。公开站点无编辑后端，在线工作台按钮链接至独立 Streamlit 服务。

## 在线工作台

CloudBase 云托管服务 roadinsight-workbench，使用 Dockerfile.cloudbase，Python 3.12，Streamlit 监听 0.0.0.0:8501，健康端点 /_stcore/health。公网 HTTPS 接入；配置 1 核 2GB、最大 1 实例、最小 0 实例，允许空闲缩至 0，首次访问可能冷启动。

容器使用 streamlit_cloud.py 启动，始终启用 community 在线权限检查。保留 CORS 与 XSRF 防护；公开访客仅查看演示，编辑者输入独立访问码后才能上传分析和保存核验。

服务端环境变量：

| 名称 | 设置 |
|---|---|
| PORT | 8501 |
| ROADINSIGHT_MODE | community |
| ROADINSIGHT_DATA_DIR | /tmp/roadinsight-community |
| ROADINSIGHT_EDITOR_TOKEN | 平台私密环境变量中的随机编辑码，至少32字符；不要提交到GitHub |

本次保持原免费在线版的临时存储语义，没有挂载持久卷。容器重启、版本部署或缩容后，上传任务和核验记录可能丢失，请及时导出。不得将本配置描述为已提供长期数据持久化；需要长期保存时按 FULL_SERVICE.md 另行接入持久卷与备份。

## 更新

1. 在 roadinsight-v2 分支完成修改和相关测试，保留默认 main 分支的旧版说明。
2. 构建公开演示，核对 release.json、snapshot.json 和 validation.json；将发布 revision 设置为实际源码提交。
3. 使用相同应用名和 appPath 更新公开站点。后端需要更新时使用相同云托管服务名与 Dockerfile.cloudbase，保留服务端私密变量。
4. 检查构建/运行状态、健康端点、五页浏览、地图筛选、路线回放、访客写入拒绝和编辑解锁。
5. 原 Streamlit Community Cloud 配置见 STREAMLIT_CLOUD.md，可作为备选；其访问码和存储与腾讯云服务相互独立。
