# 定时任务消费恢复与防复发

2026-10-08 的现场问题：timer 主进程持续收到同一 Redis 连接的可读事件但没有读取回复，CPU 空转、任务子进程空闲，队列积压超过一万条。单独恢复失效 worker 后，积压通过正常业务结算消退至零。

## 依赖兼容

保留 Celery 5.5.3 / Kombu 5.5.4，将 redis-py 固定为 5.2.1，并声明 celery[redis] extras。Kombu 5.5.4 声明 redis-py 上限为 5.2.1，旧锁的 6.4.0 超出了支持范围。锁生成脚本现在保留传递 extras 并验证已安装版本，拒绝再次产生越界组合。

版本支持范围是已确认事实；消费者停顿由哪条内部重连路径触发，仍需故障复现进一步细化。不能把 epoll 的 EEXIST/ENOENT 单独当作根因。

## 健康检查

- 每个已配置队列每分钟执行一次轻量探针，120 秒后未执行的探针过期，不堆积旧健康消息。
- worker 检查要求所有队列的实际执行心跳在 180 秒内，而不是只要求任意 worker 回复控制 ping。
- Beat 的发布心跳与队列执行心跳分开记录。原 Beat 心跳任务移到 safety 队列；timer 的执行健康仍由独立探针检查。
- `DJANGO_HEALTH_CHECK_WORKER_QUEUE_MAX_AGE_SECONDS` 可调整执行心跳最大年龄。

## 主进程监督

RedisConsumerSupervisor 使用独立线程和独立、有限超时的 Redis 连接。默认只监控 timer、timer_scan、timer_maintenance、timer_safety 的专用 worker。

存在真实运行任务或等待子进程接收的工作时，监督器不会重启。无积压、启动宽限内及 Redis 不可访问时，也不会重启。可确认 prefork 池空闲时，幽灵 reserved 状态不会永久掩盖停顿。

默认参数：

| 环境变量 | 默认值 |
|---|---:|
| CELERY_WORKER_STALL_WATCHDOG_ENABLED | 1 |
| CELERY_WORKER_STALL_WATCHDOG_STARTUP_GRACE | 120 秒 |
| CELERY_WORKER_STALL_WATCHDOG_TIMEOUT | 600 秒 |
| CELERY_WORKER_STALL_WATCHDOG_FAILURES | 3 次 |
| CELERY_WORKER_STALL_WATCHDOG_INTERVAL | 30 秒 |
| CELERY_WORKER_STALL_WATCHDOG_SHUTDOWN_GRACE | 60 秒 |

监督器先请求主进程温退出；宽限后仍未退出且重新确认空闲、Redis 可访问并有积压，才退出主进程。Docker `restart: unless-stopped` 随后恢复容器。该机制不 purge 队列，不修改业务数据。长任务受到保护，所以无法替代独立的任务超时或业务进度监测。

## 验证

在安装兼容依赖的环境运行：

```bash
python scripts/check_redis_worker_recovery.py
python scripts/check_worker_supervision.py
```

两个脚本使用独立临时 Redis 和随机 loopback 端口，不访问生产 Redis。覆盖连续 Redis 停机恢复、Channels/cache 兼容、消费停止自动退出且消息保留、长任务不误退出。单元和业务回归测试还覆盖逐队列心跳、Beat 发布/执行分离与训练结算幂等。

发布应保留 Web 与 worker 各自原镜像，在现有镜像上只替换修复文件和 Redis wheel；先更新 worker，再更新 Beat 和 Web。通过实际队列探针、健康端点与队列长度确认恢复，不能通过手工写心跳缓存来伪造健康。
