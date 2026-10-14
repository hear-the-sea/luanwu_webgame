# 2026-10-05 网页无响应诊断

网页服务已恢复。本次证据高度符合“基础设施短暂停顿触发请求取消，asgiref 3.10.0 取消清理死锁阻塞网页事件循环”的故障链。相同版本、相同运行容器中的隔离程序已经复现该机制。旧生产进程没有保存 Python 堆栈，因此不能将隔离复现写成当次堆栈的直接证明。

后续复发已取得生产进程真实堆栈，直接确认主线程在ThreadSensitiveContext退出时等待线程池，工作线程等待同一主事件循环。相关证据位于reports/releases/web_incident_20261005T0440Z。经用户授权，asgiref 3.12.1已发布；当前状态以reports/releases/asgiref_upgrade_plan_20261005.md及asgiref_fix_20261005.json为准。

## 故障时间线（北京时间）

- 03:37:02、04、06：Redis 连续报告 asynchronous AOF fsync taking too long / disk busy。
- 03:37:06：网页最后正常完成请求。
- 03:37:09 产生的一条超时日志到 03:37:51 才进入 Docker 日志。
- 03:37:51：Docker 多个容器健康检查启动超时；containerd 报告 inactive stream。
- 03:37:51：图纸合成和两次银行兑换请求记录 CancelledError，持续约 45–49 秒。
- 03:37:52：多个 Celery 进程同时报告约 45 秒心跳延迟；网页批量报告 Redis/WebSocket 心跳超时，随后不再处理请求。
- 恢复前：网页主线程及其他共 14 个线程都在 futex 等待；FD 为 68，RSS 约 180 MB，容器无 OOM。主机内存充足，数据库、Redis 及代理容器正常运行。
- 11:31：仅重启异常网页容器后，首页、登录页及内部就绪恢复，原枪王修改仍生效。

以上直接证明故障窗口存在存储写入延迟和跨服务停顿。没有历史磁盘延迟监控，也没有块设备错误或明确备份任务记录，无法进一步断言云平台、磁盘硬件或某个进程是物理层起因。

## 已复现的永久卡死机制

生产版本：Django 5.2.8、asgiref 3.10.0、Daphne 4.2.1。

1. 同步中间件在线程中处理慢请求。
2. ASGI 请求被外部取消。Daphne 的断连清理超时可执行 application task 的 cancel。
3. 主线程退出 ThreadSensitiveContext，执行 ThreadPoolExecutor.shutdown(wait=True)，等待工作线程结束。
4. 工作线程继续调用经 Django 适配的 async_to_sync(get_response)，等待主线程执行协程。
5. 两者互相等待，主线程不能接受新请求，容器进程仍存活，代理返回 502。

隔离复现不使用业务代码、数据库、Redis或网络端口。父进程强制限制执行时间；独立周期回调模拟 Daphne 的定时检查。旧版无取消基线能处理独立第二请求，取消后全部线程 futex 且第二请求无响应。远端当前网页容器的独立子进程重复验证了这两个结果。

| 依赖版本 | 取消慢请求 | 独立第二 HTTP 请求 | 子进程 |
| --- | --- | --- | --- |
| asgiref 3.10.0 | 否 | 200 | 正常退出 |
| asgiref 3.10.0 | 是 | 无响应 | 主线程与工作线程锁死 |
| asgiref 3.12.1 | 否 | 200 | 正常退出 |
| asgiref 3.12.1 | 是 | 200 | 正常退出 |

官方在 [asgiref 3.12.0 更新说明](https://github.com/django/asgiref/blob/main/CHANGELOG.txt)中明确记录了退出 ThreadSensitiveContext 时工作线程等待事件循环造成的事件循环死锁修复。3.12.1 的实现把 executor join 移至专用线程，事件循环不再同步等待。

## 修复方向与边界

- 将生产及开发锁文件中的 asgiref 从 3.10.0 更新到 3.12.1，完成项目回归后发布。此轮只在临时目录进行新版本对照，尚未修改项目锁文件或线上依赖。
- 保留并完善网页卡死时的线程堆栈采集，以及持续不健康时的受控恢复，避免进程存活但网页无响应持续数小时。
- 增加磁盘写入延迟监控，必要时让云服务商核对该时间窗口存储侧指标。

诊断证据：reproduce_asgi_cancellation.py、local_version_comparison.txt、remote_3.10_comparison.json。完整生产日志和恢复记录保存在远端 /root/code/releases/web-incident-20261005T0330Z。
