# 远端卡顿检查（2026-10-25）

本轮仅通过 SSH 只读检查，未重启、发布、修改生产配置或玩家数据。采样结束：北京时间 2026-10-25 23:59:59。未保存登录密码、用户 IP、Cookie 或玩家内容。

## 结论

确认存在服务器端间歇性长时间阻塞。重点怀疑共用数据盘的持久化链路：任务 Redis 多次 AOF fsync 延迟，后台多队列同时读取超时，部分时段 MySQL 调用也明显阻塞。尚缺故障发生时的块设备延迟与进程 I/O 时间序列，不能确定是云盘/宿主机抖动、数据库写入竞争或其他存储因素，也不能把每次卡顿都直接归因于 Redis。

另有一项独立问题：今日 08:53:07 MySQL 达到连接上限，产生 Too many connections。当前 CPU、Web 内存和任务队列没有持续饱和。

## 已确认的卡顿证据

下表时间均为北京时间。

| 时间 | 证据 |
| --- | --- |
| 08:53:07 | Web traceback 含 MySQL 1040 Too many connections；4 条异常行来自底层与 Django 包装，不能算作 4 次独立故障。 |
| 20:31:56–20:32:11 | 任务 Redis 报 AOF fsync 延迟；scan、maintenance worker 随后记录 Redis 读取超时；任务完成耗时约 16.4 秒。 |
| 22:12–22:13 | 多队列 broker 监督检查超时。Web 金条兑换耗时 52.380 秒，其中数据库调用累计 52.117 秒；批量疗伤耗时 49.552 秒，其中数据库调用 49.474 秒；仓库耗时 49.340 秒，其中数据库调用 48.430 秒。 |
| 22:13 附近 | Caddy 独立记录装备操作 41.675 秒、招募用卡 35.038 秒且最终 HTTP 200，证明不仅是取消请求的应用日志现象；WebSocket 101 长连接时长已排除。 |
| 23:32:18、23:32:25 | Redis AOF fsync 延迟，23:32:40–23:32:46 多个后台 worker 随后读取超时。 |
| 23:40:05、23:40:08 | Redis AOF fsync 延迟，23:40:12–23:40:18 多个 worker 报 broker 超时；派工请求分别耗时 15.559 秒、10.548 秒，其中后一请求数据库调用累计 10.498 秒；资源同步后台任务耗时 18.868 秒。 |

Web 保留日志起点为 UTC 2026-10-24 22:15:15，即北京时间 10 月 25 日 06:15:15，统计终点约北京时间 23:58:08，不覆盖全天。该窗口解析出 130628 条非 health 请求，118 条超过 1 秒，应用记录 89 条 5xx。89 条均标记 CancelledError，因此不能称为用户实际收到 89 次 HTTP 500，也不能全部归为业务异常；其中有客户端断开/取消与服务器阻塞混合情况。Caddy 的 status=0 也不能视为标准 HTTP 状态码。

## 持久化与数据库

- MySQL 数据位于 `/data/docker/volumes/code_db_data/_data`，任务 Redis 位于 `/data/docker/volumes/code_redis_data/_data`，共用 `/dev/vdb1`。
- 运行时 Redis 已独立运行约 12 天，AOF 关闭；任务 Redis AOF 开启，当前写入/重写状态正常，`aof_delayed_fsync=454` 为启动以来累计值，不能称为今日次数。
- Redis 最近 10 条 slowlog 的命令耗时约 10–17ms，没有秒级命令证据；slowlog 不能排除持久化等待或网络阻塞。
- MySQL 当前 `max_connections=96`、`wait_timeout=600`；`Max_used_connections=97`、`Connection_errors_max_connections=369` 为约 35 天运行期累计值，不能称为今日 369 次。
- 采样时 MySQL 连接 9、运行线程 2、当前行锁等待 0。该快照不能排除故障时锁等待。
- MySQL buffer pool 为 768MiB，容器内存限额为 3GiB。`Innodb_buffer_pool_wait_free=74837` 为累计值；提示需要观察 buffer pool/刷脏压力，不能直接证明本次故障原因或据此直接扩容。
- MySQL 数据、日志、doublewrite 文件等待历史最大值分别约 189、183、189 秒，均为启动以来累计监控，不能作为今晚故障的时间定位证据。
- MySQL cgroup `memory.events max=440391`、`oom=0`、`oom_kill=0`；累计发生过内存边界压力，当前事件是否仍增长未做持续采样。
- 未发现 iostat/sar 工具或 sysstat 历史文件。3 秒磁盘快照显示当前无长延迟，不能否定历史抖动。

## 当前健康状态与其他观察

- 服务器 8GiB 内存，available 约 3.3GiB；短采样 CPU idle 约 61%–85%，iowait 0%–4%，无持续整机 CPU 饱和证据。
- Web 约 353MiB / 768MiB，未重启，cgroup 内存 max/oom/oom_kill 均为 0，CPU throttling 为 0。
- MySQL 约 1.99GiB / 3GiB。其他主要容器均运行，Web、数据库、两个 Redis 及 Caddy 健康检查正常。
- 六条主队列 default、battle、timer、timer_scan、timer_safety、timer_maintenance 的 LLEN 采样均为 0；不能推断全天无瞬时积压或无正在执行任务。
- 服务器本机通过域名、TLS 和 Caddy 请求 `/health/live` 返回 200，耗时约 62.7ms。本地工作环境直连公网 HTTPS 超时，未把它当作远端服务故障证据。
- 根分区使用率 87%，约余 3.9GiB；数据分区使用率 25%，约余 85GiB。根分区余量需处理，但没有磁盘写满证据。
- timer worker 有 2 次重启，日志显示北京时间 02:52、09:43 为 Warm shutdown；当前状态和内核日志无 OOM 证据，不能认定是内存杀进程。

## 后续处理顺序

1. 先补充低开销短周期观测：数据盘读写/flush 延迟、IOPS/吞吐、云盘额度与突发限制、Redis 延迟及 delayed_fsync 增量、MySQL 活跃连接/锁等待/文件等待增量。故障窗口应同时保留进程 I/O 与主机 CPU steal、I/O pressure，区分存储抖动与资源竞争。
2. 单独核查 MySQL 连接峰值来源、连接回收和 Web/worker 并发预算。不要仅凭累计 369 次拒绝就直接提高连接上限。
3. 根据上述数据评估 MySQL buffer pool、容器内存余量及写入负载；如确认为云盘延迟/额度瓶颈，再评估更稳定的磁盘性能或将持久化负载分盘。
4. 审查根分区可清理内容并制定明确清单；本轮没有删除文件。

运行时 Redis 拆分和 Web 内存提高已经生效，不应重复将它们当作尚未实施的修复。不要为缓解卡顿关闭任务 Redis AOF、降低数据库持久化保证，或反复重启正常服务。
