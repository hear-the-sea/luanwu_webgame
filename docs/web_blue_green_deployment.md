# 仅 Web 的蓝绿发布

适用：已构建的本地镜像；没有数据库迁移；新 Web 与现有 worker、Redis 键和消息协议兼容。该工具不构建镜像、不迁移、不重启后台服务，也不修改公开 URL。

先在服务器项目目录只读查看计划：

```bash
python scripts/deploy_web_blue_green.py --web-container PROJECT-web-1 --image webgame:RELEASE
```

操作员先完成镜像审查与备份。若改变静态文件，必须事先在当前挂载的 `runtime/staticfiles` 中准备新 manifest、新 hash 文件，并保留旧 hash 文件；不得用清空目录的 collectstatic。仅改 Python 时可以认证资产未变化。

```bash
python scripts/deploy_web_blue_green.py --web-container PROJECT-web-1 --image webgame:RELEASE \
  --apply --no-migrations --protocol-compatible --assets-unchanged \
  --phase-file /tmp/web-blue-green-phase.json
```

资产改变时将 `--assets-unchanged` 替换为 `--static-ready`。这些参数是操作员对已完成审查的明确认证；脚本不会猜测镜像间迁移、协议或资产差异。

流程：从运行中的 web Compose labels 读取全部配置文件与 env-file 路径，准备只覆盖 web 的持久 override；启动无公开端口且不使用 web 网络别名的唯一候选；候选 readiness 成功后切换 Caddy；等待 65 秒排空旧 HTTP；只更新 canonical web；验证 readiness，再切回 web；再等待 65 秒排空候选 HTTP，仅停止删除本操作创建的候选。

Caddyfile 在原 inode 内写入并 reload，保持文件 bind mount 有效。所有状态写入 phase JSON；通过 `cat /tmp/web-blue-green-phase.json` 观察，无需中断发布进程。长连接可能在最终停止旧实例时重连；65 秒针对当前 60 秒 HTTP timeout，不承诺 WebSocket 永久连续。

成功后保留 `.web-bg-OPERATION.json`，它仅固定 web 的新镜像及关闭启动副作用；以后发布必须包含 canonical web labels 中记录的全部 Compose 文件。不要只用基础 compose 文件覆盖已生效的 web 镜像。

失败时：候选未就绪不会替换 canonical。canonical 重建失败则保留已健康的候选承载流量，并记录 `failed_candidate_retained_serving`。此时不要删除候选、override 或更改代理回未就绪的 web。修复 canonical 后再次运行独立发布前，应先人工确认当前 Caddy upstream，恢复为健康 canonical web 并排空候选；默认工具会拒绝非标准初始 upstream。

如果两端均不健康，phase 为 `failed_no_ready_target_manual_recovery`，保留所有容器供排查。容器只按精确候选名称及本次 operation label 清理；不调用 compose down、队列清理或批量 Docker 清理。命令失败仅打印命令名称和退出码，避免泄漏渲染后的环境变量。

适用假设：标准 Compose `web`/`caddy` 服务，Caddyfile 文件 bind mount、唯一 `reverse_proxy web:8000`，容器内有现有 readiness 脚本。部署期间不要并行执行其他 compose 更新。配置 reload、Docker 和机器本身故障仍需要人工恢复，脚本状态文件不是分布式事务日志。
