# ASGI 数据库连接回收修复（2026-10-26）

## 问题与处理

10 月 25 日远端 Web 日志在北京时间 08:53:07 出现 MySQL 1040 `Too many connections`。远端 `max_connections=96`。现有 HTTP admission 将单 Web 进程的业务并发限制为 24、健康检查并发限制为 2，但这只限制正在执行的请求，不会限制已经完成请求残留的数据库连接。

项目使用 Django ASGI。每个 HTTP 请求进入独立的 `ThreadSensitiveContext`，下一请求不能复用上一请求线程的连接；现有共享配置却设置 `CONN_MAX_AGE=60`。该参数在请求/任务边界检查，不能理解为后台定时器会在 60 秒后主动关闭连接。短请求结束时连接仍未过期，可能在垃圾回收或服务端超时前占用连接名额。Django 官方要求 ASGI 禁用持久连接：<https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections>。

`config/asgi.py` 现在在创建 ASGI application 前，将所有数据库 alias 的 `CONN_MAX_AGE` 设为 0，也覆盖当前已初始化的 wrapper 配置。请求完成、异常和客户端断开后的连接关闭继续交给 Django 自身的生命周期处理；不添加跨线程关闭连接或事务中的强制清理。

这个覆盖只发生在 ASGI 进程。Celery、WSGI 和管理命令仍使用共享环境中的原配置。配置示例新增相应说明，避免误将连接复用时间当成真正的连接池。

## 审查范围

- WebSocket ORM 路径使用 `database_sync_to_async`，未发现自建无界 ORM 线程。ASGI 的新策略也适用于该进程内的 WebSocket 查询。
- Celery 5.5.3 自带 Django fixup 在任务边界执行 `close_if_unusable_or_obsolete()`，会受最大连接年龄影响；默认每个 prefork 子进程通常只持有一个连接，这不能单独解释 96 个连接耗尽。本轮不额外添加重复的 Celery 回收信号。
- 保留原有 HTTP admission，无数据库结构、业务规则或玩家数据修改。遵循最小改动与复用框架生命周期的原则。

## 验证

`tests/test_asgi_database_connections.py` 在独立进程中使用临时文件 SQLite、真实 ASGI handler 和两个数据库 alias。分别从 `CONN_MAX_AGE=60`、`None` 启动，提前初始化 wrapper，再加载实际 `config.asgi.application`。

每个配置执行三轮 12 个并发请求、一次故意异常和一次客户端断开，总计打开并检查 76 个数据库连接，逐轮确认底层连接已经关闭。测试强引用 wrapper，避免垃圾回收掩盖遗漏关闭；不需要 MySQL 或 Redis 服务，也不读取玩家数据。对照实验恢复原 60 秒策略后，第一轮请求即可复现连接残留。

生产锁定的 Django 5.2.18 和 asgiref 3.12.1 安装在 `/tmp` 的独立依赖目录，仅用于验证，没有修改项目依赖。受限沙箱会阻止 asyncio 线程通过 self-pipe 唤醒事件循环，因此这组真实线程测试需要在允许该本地系统调用的环境执行，不能通过修改产品代码或增加超时来规避。

锁定依赖下，连接回收、ASGI、数据库配置、HTTP admission 及 WebSocket 相关回归共 83 项通过；修改文件的 flake8、Black 检查及 `config/asgi.py` 的 mypy 检查通过。

## 发布与边界

修复当前仅在本地，尚未部署。发布需要让 Web 进程加载新的 `config/asgi.py`；既有环境文件中的 `DJANGO_DB_CONN_MAX_AGE=60` 不会绕过 ASGI 覆盖。正常健康服务不应为本地验证而重启。

连接关闭会增加新请求的建连次数，是 ASGI 下及时释放连接名额的取舍；应在发布后观察连接峰值、Sleep 会话数量、1040 错误增量和请求延迟。该修复消除一个可复现的连接滞留风险，但无法证明它是历史 08:53 故障的唯一来源，也不解决已观察到的数据盘持久化延迟。多 Web 副本、多数据库 alias 和额外后台进程仍需要单独核算连接预算。
