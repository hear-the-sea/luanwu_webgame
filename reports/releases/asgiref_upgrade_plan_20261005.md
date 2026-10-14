# 请求取消死锁修复（已发布）

本次复发已取得生产堆栈，直接证明网页主线程在 asgiref 3.10.0 的 ThreadSensitiveContext.__aexit__ → executor.shutdown → threading.join 等待；请求线程在 AsyncToSync → CurrentThreadExecutor 等待主循环，涉及 OnlinePresenceMiddleware 调用下游处理器。与先前隔离复现一致。

已将 requirements.lock.txt 和 requirements-dev.lock.txt 的 asgiref==3.10.0 更新为 asgiref==3.12.1，分别在各应用服务现有镜像上构建仅更新该依赖的增量镜像，保留旧镜像和回滚配置，并已发布到8个应用服务。

已验证：同版本真实 Django ASGI 取消请求复现中，3.10.0 会阻塞整个网页事件循环，3.12.1 可继续处理独立请求并返回200。在临时目录安装3.12.1运行项目ASGI、单会话、WebSocket会话/容量、通知和伤害状态测试：68通过、1跳过。

风险：更新基础依赖并替换应用容器，发布过程中有短暂重连。保留现有服务各自基线镜像，不引入其他未发布代码；失败则回滚镜像。数据库、玩家属性及枪王伤害规则不涉及此次变更。

上线验证：网页与战斗任务容器实际版本均为3.12.1；生产容器内独立子进程的取消回归均正常完成，第二请求返回200。首页、登录、存活接口均200；内部就绪通过，网页健康状态healthy，所有应用容器运行且没有重启。枪王反击1000人数上限复核通过。

真实堆栈位于远端 /root/code/releases/web-incident-20261005T0440Z/code-web-1-stack.txt。发布记录、离线安装包、旧锁文件、部署日志、验证结果和回滚镜像覆盖配置位于 /root/code/releases/asgiref-fix-20261005。后续Compose操作应沿用运行容器记录的配置文件列表，包含该目录的compose.new.yml；回滚覆盖文件为compose.rollback.yml。

磁盘写入延迟是另一个基础设施问题，本次依赖升级修复了请求取消后整个网页事件循环永久锁死的机制，没有修改Redis持久化策略。
