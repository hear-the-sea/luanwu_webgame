# Docker 部署运行手册

> 最近校正：2026-09-17

本文档沉淀当前仓库在 WSL2 本地构建、导出镜像、传输到服务器、更新运行中容器与排查常见 Docker 发布问题的实操经验。

相关文件：

- [`Dockerfile`](/home/daniel/code/web_game_v5/Dockerfile)
- [`docker-compose.prod.yml`](/home/daniel/code/web_game_v5/docker-compose.prod.yml)
- [`docker/caddy/Caddyfile`](/home/daniel/code/web_game_v5/docker/caddy/Caddyfile)
- [`docker/entrypoint.sh`](/home/daniel/code/web_game_v5/docker/entrypoint.sh)
- [`.env.docker.prod.example`](/home/daniel/code/web_game_v5/.env.docker.prod.example)
- [`config/settings/database.py`](/home/daniel/code/web_game_v5/config/settings/database.py)

## 部署形态

当前生产 Docker 方案不是“单容器全包”，而是：

- 应用业务镜像一张，同时供 `web`、`worker`、`worker_battle`、`worker_timer`、`worker_timer_scan`、`worker_timer_safety`、`worker_timer_maintenance`、`beat` 复用
- `db` 使用 MySQL 容器
- `redis` 使用 Redis 容器
- `caddy` 直接监听公网 `80/443`，自动管理 HTTPS、静态资源和反向代理

生产 Compose 明确拆分了 Celery 队列：

- `worker` 只消费 `default` 队列
- `worker_battle` 只消费 `battle` 队列
- `worker_timer` 只消费 `timer` 队列
- `worker_timer_scan` 只消费 `timer_scan` 队列
- `worker_timer_safety` 只消费 `timer_safety` 队列，专门运行安全心跳、聚合和监控
- `worker_timer_maintenance` 只消费 `timer_maintenance` 队列
- `beat` 只负责发布定时任务，不消费业务任务

因此，生产部署时必须把 `worker_timer_scan`、`worker_timer_safety` 和 `worker_timer_maintenance` 一并启动。只启动
`worker` 不会自动创建或启动这些独立的定时 worker 容器。

生产镜像内应用进程使用 UID/GID `10001:10001` 运行；`web` 容器在
[`docker-compose.prod.yml`](/home/daniel/code/web_game_v5/docker-compose.prod.yml#L42)
中启用了 `read_only: true`。因此 `DJANGO_COLLECTSTATIC=1` 依赖
`./runtime/staticfiles:/app/staticfiles` 这个可写 volume，首次部署和由 root 创建
runtime 目录后，都必须把 runtime 目录归属修正给 `10001:10001`。
`worker`、`worker_battle`、`worker_timer`、`worker_timer_scan`、`worker_timer_safety`、`worker_timer_maintenance`、`beat` 没有挂载 `/app/staticfiles`，
生产 Compose 已对这些服务设置 `DJANGO_COLLECTSTATIC=0`，避免只读容器在启动时写静态目录。

这意味着发布时通常只需要传输业务镜像；数据库和 Redis 由服务器上的 Compose 编排直接启动。

Caddy 是唯一公网入口，直接监听 TCP `80/443`，并额外暴露 UDP `443` 供 HTTP/3 使用。
它会自动申请和续期公开证书，并把 HTTPS 状态通过 `X-Forwarded-Proto` 传给 Django。
生产保持 `DJANGO_SECURE_SSL_REDIRECT=1`；不再需要外层 TLS 代理或手动运行 Certbot。

首次签发证书前必须满足：

- `CADDY_SITE_ADDRESS` 是真实域名，且域名 A/AAAA 记录已经指向服务器；没有可达 IPv6 时不要保留错误的 AAAA 记录。
- 公网入站 TCP `80/443` 已放行并转发到该服务器；UDP `443` 可选放行以启用 HTTP/3。
- 服务器上没有其他进程占用 `80/443`。
- `caddy_data` 和 `caddy_config` 命名卷必须保留；执行 `docker compose down -v` 会删除证书和 ACME 账户状态。

## 服务器前置检查

以下命令在生产服务器上执行。部署目录和镜像名可以按实际环境调整；本文后续命令默认使用
`/opt/web_game_v5` 和 `webgame:v1`。

```bash
docker version
docker compose version
docker info >/dev/null
```

还需要确认：

1. 服务器已经安装 Docker Engine 和 Docker Compose V2，当前用户有权访问 Docker daemon。
2. 生产域名的 A/AAAA 记录已经指向服务器；没有可达 IPv6 时不要配置错误的 AAAA 记录。
3. 防火墙或云安全组已放行 TCP `80`、`443`；UDP `443` 用于可选的 HTTP/3。
4. 服务器上的 `80/443` 没有被其他 Web 服务占用。
5. 应用镜像已经传到服务器，或者准备在服务器上完成镜像加载。
6. `.env.docker` 中的 `CADDY_SITE_ADDRESS`、`DJANGO_ALLOWED_HOSTS` 和
   `DJANGO_CSRF_TRUSTED_ORIGINS` 指向同一个生产域名。

## 本地构建镜像

仓库默认镜像名应与生产环境变量保持一致，推荐统一使用：

```bash
webgame:v1
```

对应关系：

- [`.env.docker.prod.example`](/home/daniel/code/web_game_v5/.env.docker.prod.example#L76) 默认 `WEBGAME_IMAGE=webgame:v1`
- [`docker-compose.prod.yml`](/home/daniel/code/web_game_v5/docker-compose.prod.yml#L38) 默认 `image: ${WEBGAME_IMAGE:-webgame:v1}`

如果本地和服务器都是 Linux x86_64，可直接构建：

```bash
docker build -t "webgame:v1" "."
```

如果本地是 WSL2 + Docker Desktop，且需要明确产出服务器常用的 `linux/amd64` 镜像，使用：

```bash
docker buildx build --platform "linux/amd64" -t "webgame:v1" --load "."
```

如果最近改过 Tailwind 或静态资源，先执行：

```bash
npm run build:css
```

原因是当前 [Dockerfile](/home/daniel/code/web_game_v5/Dockerfile#L24) 只按 `requirements.lock.txt` 安装 Python 依赖并复制仓库内容，不会在构建阶段自动执行前端构建。

## 导出并传输镜像

导出镜像：

```bash
docker save "webgame:v1" | gzip > "webgame_v1.tar.gz"
```

传到服务器：

```bash
scp "webgame_v1.tar.gz" "user@your-server:/opt/web_game_v5/"
```

服务器上加载：

```bash
cd "/opt/web_game_v5"
docker load -i "webgame_v1.tar.gz"
```

`docker load` 完成后要留意输出，例如：

```bash
Loaded image: webgame:v1
```

后续 `WEBGAME_IMAGE`、`docker rmi`、`docker compose` 中引用的镜像名，都应以这里的实际输出为准。

## 首次部署（完整流程）

以下流程适用于服务器第一次部署，或服务器上还没有这套生产 Compose 项目的情况。命令按顺序
执行，不建议把迁移和业务容器启动合并成一个不可检查的长命令。

### 1. 准备部署目录、镜像和运行目录

先把仓库中的 `docker-compose.prod.yml`、`docker/`、`runtime/` 相关目录和镜像包传到服务器，
然后执行：

```bash
cd "/opt/web_game_v5"
docker load -i "webgame_v1.tar.gz"
```

确认 `docker load` 输出的镜像名与 `.env.docker` 中的 `WEBGAME_IMAGE` 一致，例如：

```text
Loaded image: webgame:v1
```

准备可写运行目录。生产 `web` 容器启用了只读根文件系统，静态文件、媒体文件和 Celery Beat
状态文件必须使用这些目录或卷：

```bash
mkdir -p "runtime/media" "runtime/staticfiles" "runtime/celerybeat"
chown -R "10001:10001" "runtime/staticfiles" "runtime/media" "runtime/celerybeat"
```

### 2. 创建并填写生产环境变量

只在第一次部署且 `.env.docker` 不存在时执行复制；已有生产环境变量文件不要重复覆盖：

```bash
cp ".env.docker.prod.example" ".env.docker"
chmod 600 ".env.docker"
```

编辑 `.env.docker`，至少检查并修改以下内容：

- `WEBGAME_IMAGE`：必须与 `docker load` 实际加载的镜像名一致，默认是 `webgame:v1`。
- `DJANGO_SECRET_KEY`：使用新的随机生产密钥，不要沿用示例值。
- `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`：使用强密码。
- `DJANGO_DB_PASSWORD`：必须与 `MYSQL_PASSWORD` 一致。
- `DJANGO_DB_CONNECT_TIMEOUT`、`DJANGO_DB_READ_TIMEOUT` 和 `DJANGO_DB_WRITE_TIMEOUT`：限制 MySQL 建连、读、写等待；初始值分别为 5、60、60 秒，长查询应拆成可观测的批次。
- `REDIS_PASSWORD`：生产必填，且不能留空。
- `CADDY_SITE_ADDRESS`、`DJANGO_ALLOWED_HOSTS`、`DJANGO_CSRF_TRUSTED_ORIGINS`：使用同一个生产域名。
- `DJANGO_RUN_MIGRATIONS=0`：保持关闭，由发布步骤手动执行迁移。
- `CELERY_TIMER_SCAN_QUEUE=timer_scan` 和 `CELERY_TIMER_SCAN_CONCURRENCY`：确认核心扫描队列配置符合容量规划。
- `CELERY_TIMER_SAFETY_QUEUE=timer_safety` 和 `CELERY_TIMER_SAFETY_CONCURRENCY`：确认安全任务使用独立 worker，默认并发为 1，避免队列排队制造心跳缺口。
- `CELERY_TIMER_MAINTENANCE_QUEUE=timer_maintenance` 和 `CELERY_TIMER_MAINTENANCE_CONCURRENCY`：确认维护队列有独立 worker；默认并发为 1，避免低优先级任务争用核心扫描资源。
- `REDIS_SOCKET_CONNECT_TIMEOUT` 和 `REDIS_SOCKET_TIMEOUT`：限制 Django cache 与 Celery 的 Redis 连接和读写等待，初始值为 2 秒；`REDIS_CHANNEL_SOCKET_TIMEOUT` 单独用于 Channels Redis，必须高于其默认 5 秒阻塞读取，初始值为 10 秒。
- `DJANGO_DB_CONN_MAX_AGE=60` 和 `DJANGO_DB_CONN_HEALTH_CHECKS=1`：让 Django 短时间复用且复用前检查 MySQL 连接，避免少量僵尸连接长期占满连接池。
- `MYSQL_WAIT_TIMEOUT=600`：限制 MySQL 空闲连接的服务端保留时间。
- `MYSQL_BINLOG_EXPIRE_LOGS_SECONDS`：必须与备份/复制策略一致。8 GiB 单机示例使用 `86400`（24 小时），没有完成可恢复备份前不要盲目缩短该值。
- `DJANGO_ONLINE_PRESENCE_FAILURE_COOLDOWN_SECONDS=30`：Redis 发生基础设施故障时，在线人数是可丢失的 best-effort 数据；进程会短暂熔断该写入，避免每个 HTTP 请求重复等待 Redis。页面公共统计也会在进程内短暂熔断缓存读取，并使用本地快照，避免故障扩散。

#### 8 GiB 单机初始资源基线

2026-09-14 的生产排查确认维护 worker（256 MiB）和 Caddy（64 MiB）发生容器限额 OOM，
并发现 MySQL 被 OOM 杀死的记录；2026-09-16 的内核日志进一步确认 `worker_timer` 的 Celery
进程达到约 580 MiB 后触发容器限额 OOM。即使临时提高到 768 MiB，23:37 仍再次触发同一
worker 的 cgroup OOM，因此当前初始预算提高到 1 GiB。旧的 4 GiB 低配估算不再作为当前生产默认配置。
新的 Compose 默认值和 `.env.docker.prod.example` 按约 8 GiB 单机设置，适用于每种服务一个实例：

| 服务 | 内存硬上限 | 软保留值 |
| --- | ---: | ---: |
| MySQL | 2048 MiB | 1024 MiB |
| 维护 worker | 768 MiB | 384 MiB |
| 安全 worker | 512 MiB | 256 MiB |
| Caddy | 256 MiB | 128 MiB |
| Timer worker | 1024 MiB | 512 MiB |
| 其他三个普通 worker，每个 | 256 MiB | 160 MiB |
| Web | 384 MiB | 256 MiB |
| Beat | 192 MiB | 128 MiB |
| Redis | 512 MiB | 128 MiB |
| 合计 | **6464 MiB（约 6.31 GiB）** | **3296 MiB（约 3.22 GiB）** |

这是一组针对已观察到的 OOM 调整的初始预算，不是压测得出的峰值，也不能保证不再 OOM。
上限不是预分配内存，软保留值也不是额外叠加的硬上限；宿主机、Docker、其他项目和发布时的临时
容器都需要额外空间。7.8 GiB 主机与上述硬上限总和的差额约 1.5 GiB，但不等于实时可用内存。
4 GiB 主机不能直接套用这组预算，应扩容、拆分服务或重新进行容量评估；增加服务副本或并发也要重算。

Timer、维护和安全 worker 分别使用独立的内存变量；原 `CELERY_WORKER_MEMORY_*` 继续控制
`worker`、`worker_battle` 和 `worker_timer_scan` 三个普通 worker。需要定制维护预算的旧部署必须迁移到新变量。
MySQL 使用 768 MiB buffer pool 和原临时表参数，最大连接数基线调整为 96，并将线程缓存设为 32，减少
高峰连接创建开销；`DJANGO_DB_CONN_MAX_AGE=60` 与 `MYSQL_WAIT_TIMEOUT=600` 共同回收空闲连接，把连接池留给实时请求。Redis 的容器上限提高到 512 MiB，
`REDIS_MAXMEMORY=256mb` 保留 Redis 分配器、AOF 和连接缓冲区的余量；保持 `noeviction`，避免 broker
消息被静默淘汰。Redis 上限与 Compose 环境文件必须同步更新，否则已有 `.env.docker` 中的 128 MiB 值
仍会覆盖模板默认值。
Caddy 的 `validate` 健康检查会在同一容器中启动另一个进程，预算必须覆盖主进程和检查进程。

六个 worker 仍各用 `concurrency=1`、`prefetch=1`，保持 `max-tasks-per-child=200`。普通 worker
和维护 worker 使用 `max-memory-per-child=180000`（KiB），Timer worker 使用独立的
`CELERY_TIMER_MAX_MEMORY_PER_CHILD=500000`（KiB），安全 worker 使用独立的
`CELERY_TIMER_SAFETY_MAX_MEMORY_PER_CHILD=400000`（KiB），以覆盖安全快照聚合的工作内存。
该阈值是单个子进程的回收阈值，超过后在任务结束时回收，不是运行中任务或整个容器的实时内存上限；
容器还要容纳父进程和任务峰值。
参见 [Celery 子进程内存阈值](https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-max-memory-per-child)
和 [Docker 内存限制](https://docs.docker.com/engine/containers/resource_constraints/#memory)。

#### 磁盘、MySQL binlog 与 Redis AOF

生产宿主机的根盘只有约 29 GiB 时，镜像层、MySQL 数据和 binlog 必须一起纳入容量预算。生产 Compose
当前会把 MySQL 的 `binlog_expire_logs_seconds` 默认设为 86400 秒，并把空闲连接 `wait_timeout` 设为
600 秒；实际 `.env.docker` 应按备份和复制要求显式复核这两个值。binlog 不是普通缓存，不能用 `rm` 直接删除；
需要缩短历史时，先确认备份/PITR 与复制状态，再通过 MySQL 的 `PURGE BINARY LOGS` 或保留策略完成清理。

Redis 生产实例保留 AOF、使用 `appendfsync everysec`，并关闭默认 RDB 定时快照（`--save ""`）。AOF 已是
恢复来源时，这可以避免 BGSAVE 与 AOF fsync 同时争用满盘；如果部署需要 RDB 作为额外恢复介质，应在容量预算
和备份流程中重新启用，而不是只删除 Redis 数据卷。

镜像构建上下文会排除 `.worktrees/`、`.agents/`、`.codex/`、`.claude/`、`.omc/` 和 `.superpowers/`。
这些目录不参与运行时，若再次进入镜像，会把本地 worktree 和工具缓存直接复制到每个发布层，迅速消耗根盘。

#### Timer OOM 与 ETA 队列取证

内核日志中的 `task=celery` 只说明被杀的是 Celery 进程，不能确定具体业务任务。下次重建容器前先保留
现场，尤其要查看 `worker_timer` 的计划、保留和正在执行任务：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "worker_timer" \
  celery -A config inspect scheduled --timeout=2 --json
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "worker_timer" \
  celery -A config inspect reserved --timeout=2 --json
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "worker_timer" \
  celery -A config inspect active --timeout=2 --json
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --since "30m" "worker_timer"
timer_cid="$(docker inspect "code-worker_timer-1" --format '{{.Id}}')"
timer_cgroup="/sys/fs/cgroup/system.slice/docker-${timer_cid}.scope"
for metric in memory.current memory.peak memory.events; do
  printf '%s: ' "$metric"
  sudo cat "${timer_cgroup}/${metric}" 2>/dev/null || true
done
```

`scheduled` 中若有大量远期任务，或数量随时间持续增长，应优先怀疑 ETA/countdown 堆积；如果数量不高但
单个任务执行时 RSS 突然上升，则继续按任务名检查扫描批量、`prefetch_related` 和快照聚合。当前锁定的
Celery 5.5.3 没有 `worker_eta_task_limit`，在该版本的环境文件中添加同名变量不会产生限制效果；该选项是
Celery 5.6 才提供的能力，升级依赖前必须单独做兼容性验证。

持久状态完成任务的 ETA 上限为 `CELERY_PERSISTENT_ETA_MAX_SECONDS=300`（旧环境中的
`CELERY_GUEST_TRAINING_ETA_MAX_SECONDS` 仍可作为兼容回退）。超过 5 分钟的建筑、科技、任务、募兵、生产、
门客训练、侦察、掠夺和帮会战斗只保留数据库截止时间，由各自的周期扫描器按索引批量结算；旧版遗留的长 ETA
任务执行时也会直接交给扫描兜底。这样完成可能比理论时间最多晚一个扫描周期，但不会把数小时或数天的状态
长期留在 `worker_timer` 的 ETA 保留列表中。调整该阈值前先观察各扫描队列 oldest-due、批量耗时和完成延迟，
不要只提高 Redis 或 worker 的硬上限。

升级后 Redis 的 `unacked` 仍可能暂时包含旧镜像已经预取的长 ETA 消息。不要直接删除 `unacked`、
`unacked_index` 或 Redis 数据卷；先按任务名和 ETA 做只读统计，观察 worker 的内存、重启计数和数量是否持续下降。
这些任务对应的业务截止时间仍以数据库为准，周期扫描器会补偿丢失、延迟或重复投递。只有在确认某类任务有完整的
数据库扫描兜底、备份和审计记录后，才可以制定单独的消息回收方案；新代码发布后不应再产生超过 5 分钟的持久 ETA。

本项目存在多种带 `countdown` 的持久业务状态，并且 Redis transport 默认可见性超时是 1 小时。保留超过
1 小时的 countdown 时，任务可能在执行前被 Redis 重新投递，造成重复任务和更多内存；不能只把
`CELERY_TIMER_SAFETY_MEMORY_LIMIT` 或 `CELERY_MAX_MEMORY_PER_CHILD` 调大来解决。若暂时保留 ETA，须先
统计生产环境的最大倒计时和任务最长运行时间，再将 broker、result backend 和 Celery 对应的 visibility
timeout 一致设置为更大的值；不要凭估计写死。根治方案是把 `complete_at`、`return_at` 等截止时间作为数据库
事实源，由 `timer_scan` 按小批量轮询到期记录，Celery 只承载短延迟任务，避免把数小时任务长期留在 worker 内存。

当前数据模板在 `GAME_TIME_MULTIPLIER=1` 时已经包含很长的单步时长：`youxibaota` 和 `juxianzhuang`
的最高级建筑升级约 1,916,950 秒（22.2 天），`march_art` 的科技升级约 238,430 秒（66.2 小时）。
这不是异常任务才会出现的远期 ETA；只要玩家数量增长，`worker_timer` 的计划列表就可能持续积累。

#### Web 卡住时的现场取证与恢复

`web` 显示 `unhealthy` 不会触发 Compose 自动重建；`restart: unless-stopped` 只在容器进程退出时生效。
在线人数 HTTP touch 是 best-effort 写入，Redis 基础设施异常后会由进程内单飞锁和 30 秒熔断窗口快速跳过，
不会让每个 Daphne 请求都建立新的 Redis 阻塞调用。该保护只减少故障扩散，不替代磁盘、Redis 和 MySQL 的根因修复。
若 Daphne 仍为 `running` 但本机 `/health/live` 也无法建立 TCP 连接，记录监听队列和进程状态后再恢复：

```bash
docker inspect "code-web-1" --format 'status={{.State.Status}} health={{.State.Health.Status}} oom={{.State.OOMKilled}} pid={{.State.Pid}} restarts={{.RestartCount}} memory={{.HostConfig.Memory}}'
docker inspect "code-web-1" --format '{{range .State.Health.Log}}{{println .Start .ExitCode .Output}}{{end}}'
docker stats --no-stream "code-web-1" "code-worker_timer-1"
docker top "code-web-1"
web_pid="$(docker inspect "code-web-1" --format '{{.State.Pid}}')"
ps -o pid,ppid,stat,wchan:32,etime,pcpu,pmem,cmd -p "$web_pid"
sudo cat "/proc/$web_pid/stack" 2>/dev/null || true
sudo nsenter -t "$web_pid" -n ss -lntp
sudo nsenter -t "$web_pid" -n curl -sv --max-time 5 -H "Host: luanwu.top" "http://127.0.0.1:8000/health/live"
docker logs --since "30m" "code-web-1"
sudo journalctl -k --since "30m" --no-pager | rg -i 'oom|out of memory|killed process|segfault|i/o error'
sudo journalctl -u docker --since "30m" --no-pager
docker events --since "30m" --until "0s" --filter type=container
```

监听 socket 的 `Recv-Q` 接近 `Send-Q` 且本机 curl 超时，表示 Daphne 没有及时 accept 新连接，网站的
502/504 是反向代理看到的后果。先保存上述输出，再只重建卡住的服务：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --no-deps --force-recreate "web"
```

恢复后要同时验证 `health/live`、`health/ready`、Caddy 日志和 WebSocket；不要用 `docker compose restart` 代替
配置或镜像变更，也不要执行 `docker compose down -v`。如果线上有外层 Caddy，必须沿用双层覆盖文件，并同时
查看 `gongsi-deploy-caddy-1` 和 `code-caddy-1` 两层日志；内层当前只有一个 `web:8000` 上游，Caddy 本身不会
把 Docker 的 `unhealthy` 状态变成自动重启。需要自动恢复时，应在宿主机用 systemd/编排器根据健康状态执行
受控重建，并保留事件日志。

初始配置下不要为了追赶虚拟玩家 backlog 直接增加 worker 并发。先在目标主机记录至少以下信息，
再决定是否逐个提高 battle 或扫描 worker 的并发：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" stats --no-stream
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps
docker stats --no-stream
free -h
vmstat 1 5
swapon --show
df -h
df -i
```

连续观测必须同时记录容器/进程 RSS 峰值、swap、OOM/restart、MySQL 连接数、Redis `used_memory`、
队列 oldest-due、queue wait 和 maintenance owner duration。只有在没有 OOM、没有持续 swap、
队列 oldest-due 不持续上升且保留明确 `MemAvailable` 余量时，才允许一次只调高一个队列并重新测量；
当前静态基线不能替代 `1h/6h/24h` 长时矩阵。

#### 已部署环境升级内存预算

只替换镜像不会修改运行中容器的 cgroup 限额；只更新模板也不会覆盖服务器 `.env.docker` 的旧值。
本轮包含应用代码和 settings 变更，必须构建并发布新应用镜像；不需要执行数据库迁移，但需要同步新版
`docker-compose.prod.yml`，并在已有 `.env.docker` 中逐项更新下列值（不存在则补充，每个键只保留一次）：

```dotenv
MYSQL_MEMORY_LIMIT=2048m
MYSQL_MEMORY_RESERVATION=1024m
DJANGO_DB_CONNECT_TIMEOUT=5
DJANGO_DB_READ_TIMEOUT=60
DJANGO_DB_WRITE_TIMEOUT=60
CELERY_WORKER_MEMORY_LIMIT=256m
CELERY_WORKER_MEMORY_RESERVATION=160m
CELERY_TIMER_WORKER_MEMORY_LIMIT=1024m
CELERY_TIMER_WORKER_MEMORY_RESERVATION=512m
CELERY_TIMER_MAINTENANCE_MEMORY_LIMIT=768m
CELERY_TIMER_MAINTENANCE_MEMORY_RESERVATION=384m
CELERY_TIMER_SAFETY_MEMORY_LIMIT=512m
CELERY_TIMER_SAFETY_MEMORY_RESERVATION=256m
CELERY_BEAT_MEMORY_LIMIT=192m
CELERY_BEAT_MEMORY_RESERVATION=128m
CELERY_TIMER_MAX_MEMORY_PER_CHILD=500000
CELERY_TIMER_SAFETY_MAX_MEMORY_PER_CHILD=400000
CELERY_PERSISTENT_ETA_MAX_SECONDS=300
CELERY_GUEST_TRAINING_ETA_MAX_SECONDS=300
MYSQL_MAX_CONNECTIONS=96
MYSQL_THREAD_CACHE_SIZE=32
MYSQL_WAIT_TIMEOUT=600
MYSQL_BINLOG_EXPIRE_LOGS_SECONDS=86400
DJANGO_DB_CONN_MAX_AGE=60
DJANGO_DB_CONN_HEALTH_CHECKS=1
DJANGO_ONLINE_PRESENCE_FAILURE_COOLDOWN_SECONDS=30
REDIS_SOCKET_CONNECT_TIMEOUT=2
REDIS_SOCKET_TIMEOUT=2
REDIS_CHANNEL_SOCKET_TIMEOUT=10
DAPHNE_HTTP_TIMEOUT=60
CADDY_MEMORY_LIMIT=256m
CADDY_MEMORY_RESERVATION=128m
REDIS_MEMORY_LIMIT=512m
REDIS_MEMORY_RESERVATION=128m
REDIS_MAXMEMORY=256mb
```

不要用示例文件覆盖整个生产 `.env.docker`，不要修改其中的密码、域名、镜像标签或其他业务参数。
检查当前 shell 中是否有同名导出变量；它们也可能覆盖环境文件。若之前为 Timer 临时修改过
`CELERY_WORKER_MEMORY_LIMIT`，要把它恢复为普通 worker 的 256 MiB，并新增
`CELERY_TIMER_WORKER_MEMORY_LIMIT=1024m`；否则三个普通 worker 也会同时获得过大的上限。先检查重复键并
仅校验展开后的配置：

```bash
awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{count[$1]++} END{for (key in count) if (count[key] > 1) print key, count[key]}' ".env.docker"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config --quiet
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config --format json |
  python3 -c 'import json, sys; services = json.load(sys.stdin)["services"]; print(json.dumps({name: {key: service.get(key) for key in ("mem_limit", "mem_reservation")} for name, service in services.items()}, indent=2))'
```

这里只输出资源字段，避免把完整 Compose 展开结果中的生产密码贴到工单。
应用配置必须安排并确认维护窗口：MySQL 和 Caddy 重建会分别中断数据库连接和公网入口；应用镜像更新需要
重建 `web` 及全部 Celery 业务 worker，安全 worker 增加或重建时也要短暂确认安全心跳连续性。
先确认数据库备份可恢复，再停止接收新业务、排空在途任务，按下文“计划重启与虚拟玩家安全窗口”
处理运行围栏，然后暂停相关业务进程和调度器。只对需要更新限额的 `db`、
`worker_timer`、`worker_timer_safety`、`worker_timer_maintenance`、`caddy` 重建容器，保留全部命名卷；本轮应用代码变更还要按完整发布流程重建 `web` 和其余业务 worker；数据库恢复健康后再恢复业务服务。
`docker compose restart` 不会应用新的 Compose 配置，不能代替重建。若同时部署新代码，应合并到
下文完整发布流程，不要重复重建。参见 [Compose restart 的限制](https://docs.docker.com/reference/cli/docker/compose/restart/)。

如果虚拟玩家当前已因心跳异常暂停，不要覆盖原因、伪造计划重启标记或直接清空暂停状态。
内存调整不修改安全阈值、历史快照或恢复计数；本次 03:00–04:00 的心跳缺口尚未与更早的 OOM
建立直接因果关系。安全任务现由专用队列承载，但安全 worker 自身 OOM、Beat 停止、Redis/数据库故障
或发布期间任务未启动仍可能造成心跳缺口。
已有缺口也可能在日窗口定稿时再次触发暂停，不能把“容器已恢复运行”当成安全窗口已恢复的证明。

更新后以 `docker inspect` 的 `HostConfig.Memory` / `HostConfig.MemoryReservation` 验证实际限额，
并观察新 OOM 事件、重启次数、队列延迟、MySQL 健康、Web readiness 和安全窗口推进情况。
若需要回滚，恢复备份中的对应 Compose/资源配置后在维护窗口重建受影响容器，不删除数据卷；
回到已发生 OOM 的低限额不是稳定性修复。

### 3. 校验 Compose 和 Caddy 配置

配置校验通过后再启动容器，可以提前发现环境变量缺失、YAML 错误和 Caddyfile 错误：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config >/dev/null

docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm --no-deps "caddy" \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
```

确认 Compose 展开的服务名包含独立的扫描 worker：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config --services
```

输出中应至少包含：`db`、`redis`、`web`、`worker`、`worker_battle`、`worker_timer`、
`worker_timer_scan`、`worker_timer_safety`、`worker_timer_maintenance`、`beat`、`caddy`。

### 4. 启动基础设施并执行数据库迁移

先启动 MySQL 和 Redis：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d "db" "redis"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps "db" "redis"
```

然后使用同一份业务镜像手动执行迁移。不要把 `DJANGO_RUN_MIGRATIONS=1` 长期写进统一环境文件，
否则多个服务可能在启动时并发执行迁移：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py migrate --noinput
```

如果是新服并且需要导入模板数据，在迁移完成后执行一次：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py bootstrap_game_data --skip-images
```

### 5. 启动全部业务服务

必须显式包含 `worker_timer_scan`、`worker_timer_safety` 和 `worker_timer_maintenance`。前者以 `timer_scan` 队列和独立并发度运行
用户相关的批量扫描，安全 worker 只处理安全心跳/窗口监控，维护 worker 以 `timer_maintenance` 队列运行虚拟玩家、竞技场储备、市场和清理等维护任务：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d \
  "web" "worker" "worker_battle" "worker_timer" "worker_timer_scan" "worker_timer_safety" "worker_timer_maintenance" "beat" "caddy"
```

如果直接执行不带服务名的 `docker compose up -d`，Compose 也会启动文件中声明的全部服务；生产发布
仍建议使用上面的显式服务列表，便于确认本次发布确实包含三个独立的定时 worker。

### 6. 验证部署结果

先查看所有服务状态：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps
```

重点确认以下服务处于 `running` 或健康状态：`web`、`worker`、`worker_battle`、`worker_timer`、
`worker_timer_scan`、`worker_timer_safety`、`worker_timer_maintenance`、`beat`、`caddy`、`db`、`redis`。

分别查看应用和扫描 worker 的最近日志：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "web"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_scan"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_safety"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_maintenance"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "beat"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "caddy"
```

最后使用实际生产域名检查健康接口；不要直接使用未导出的 `${CADDY_SITE_ADDRESS}` shell 变量：

```bash
curl --fail --show-error --silent "https://your-production-domain.example/health/live"
```

将 `your-production-domain.example` 替换为 `.env.docker` 中的 `CADDY_SITE_ADDRESS`。

## 更新已有旧版本容器

如果服务器上已经在跑旧版本，不要直接删除数据卷，也不要使用：

```bash
docker compose down -v
```

推荐更新顺序：

1. 加载新镜像
2. 停掉 worker 和 beat，避免旧代码继续消费任务
3. 用新镜像手动执行迁移
4. 如果有模板数据变更，再执行导入
5. 强制重建业务容器

更新登录限流和邮件重发配置时，已有 `.env.docker` 中的显式值会覆盖代码默认值。
将 `EMAIL_VERIFICATION_RESEND_IP_LIMIT` 调整为 `20`（每 IP 每小时请求数），
`EMAIL_VERIFICATION_RECOVERY_EMAIL_LIMIT` 设为 `10`（找回入口每邮箱每小时请求数）；
账号发送间隔仍为 60 秒，邮件供应商的日/月额度独立生效。
同时核对 `DJANGO_TRUSTED_PROXY_IPS` 是否包含实际入口代理，且代理正确传递 `X-Forwarded-For`。
如果不同玩家的请求被识别为同一代理 IP，仍会共用 IP 防刷额度；应修正代理配置，不能信任任意来源的转发头。
本次门客头像字段使用 `guests/0075_guest_avatar_asset_key.py` 增量迁移，保留旧迁移历史和现有玩家数据。

常用命令：

```bash
cd "/opt/web_game_v5"
docker load -i "webgame_v1.tar.gz"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config >/dev/null
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" stop "beat" "worker" "worker_battle" "worker_timer" "worker_timer_scan" "worker_timer_safety" "worker_timer_maintenance"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py migrate --noinput
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate --no-deps \
  "web" "worker" "worker_battle" "worker_timer" "worker_timer_scan" "worker_timer_safety" "worker_timer_maintenance" "beat"
```

如果本次发布包含模板数据变更，迁移完成后、重启业务服务前执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py bootstrap_game_data --skip-images
```

如果只更新应用代码，通常不需要重建 `db`、`redis` 或 `caddy`。如果修改了
`docker-compose.prod.yml`、队列名、并发度或 Caddy 配置，则应使用 `--force-recreate`，确保容器
实际使用新配置。

如果这次发布修改了 Caddy 配置，先校验再平滑加载：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "caddy" \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "caddy" \
  caddy reload --config "/etc/caddy/Caddyfile" --adapter caddyfile
```

### 计划重启与虚拟玩家安全窗口

如果本次操作是计划内的应用重启，先用当前 routing revision 设置计划重启围栏。该围栏会继续禁止
V2 新增和培养写入，但允许安全监控把重启造成的心跳/窗口缺口作为计划维护恢复；未声明的异常中断仍会
按 fail-closed 规则暂停。

先只读查看 revision：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py shell -c \
  'from gameplay.models import BotRuntimeRoutingState; print(BotRuntimeRoutingState.objects.get(pk="virtual_players").revision)'
```

先执行 dry-run，确认当前状态确实是 `v2_active`，再加 `--apply`：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py prepare_virtual_player_planned_restart \
  --expected-revision "<REVISION>"

docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py prepare_virtual_player_planned_restart \
  --expected-revision "<REVISION>" --apply
```

如果是未计划的异常重启，不要使用该命令；恢复后应保留安全监控的暂停结果，等待连续完整安全窗口自动恢复。

验证状态：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "web"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_scan"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_safety"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker_timer_maintenance"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "caddy"
curl --fail --show-error --silent "https://your-production-domain.example/health/live"
```

将健康检查命令中的 `your-production-domain.example` 替换为实际生产域名。

### 发布失败时的处理顺序

1. 先查看 `docker compose ps`，确认是容器未启动、健康检查失败，还是应用进程启动后退出。
2. 查看对应服务日志，优先检查 `web`、`worker_timer_scan`、`worker_timer_safety`、`worker_timer_maintenance`、`beat`、`db` 和 `redis`。
3. 如果是镜像名错误，执行 `docker images`，确认 `WEBGAME_IMAGE` 与 `docker load` 输出完全一致。
4. 如果是迁移失败，不要继续重启全部 worker；先保留错误日志，修复迁移或镜像后重新执行迁移。
5. 如果只是业务容器异常，可重新执行对应服务的 `up -d --force-recreate`，不要删除数据卷。

回滚应用镜像时，只回滚 `WEBGAME_IMAGE` 和业务容器，并重新执行与该镜像兼容的发布流程。数据库
迁移通常不可自动回滚；如果新版本已经执行了不可逆迁移，不能仅靠切回旧镜像完成安全回滚，必须先
根据项目的数据库备份和迁移策略评估。

Caddy 日志首次出现证书签发成功后，后续续期由 Caddy 自动完成。排查证书问题时重点检查域名解析、端口占用、防火墙以及 `caddy_data` 卷是否被误删。

## Gate D1 证据自动化

每次推送到仓库的提交，CI 会在真实 MySQL/Redis 服务上依次重跑 Gate A 前置和 Gate D1 三段测试，生成绑定当前提交的 YAML，并上传为
`gate-d1-evidence-<commit>` 构建产物。证据生成器会在写入前校验源码摘要、测试集合、执行结果、性能基准和只读测试数据库状态；生成失败不会产出半成品。

因此，常规部署不需要手动改写 `docs/virtual_player_gate_d1_evidence_*.yaml`。部署前应使用与镜像相同提交的 CI 构建产物进行审阅；该产物只证明测试环境的 Gate D1 readiness，不会自动执行 Gate exit、修改 Bootstrap routing 或授权生产发布。

本地需要复现时，可以执行：

```bash
make gate-d1-evidence \
  GATE_D1_EVIDENCE_OUTPUT="test-results/gate-d1/local.yaml"
make verify-gate-d1-evidence \
  GATE_D1_EVIDENCE_OUTPUT="test-results/gate-d1/local.yaml"
```

## Gate E 就绪证据自动化

完整 Gate E 不是每次普通 push 都执行的快速检查，而是发布前的 MySQL/Redis 真实服务门禁。`.github/workflows/virtual_player_readiness.yml` 会在以下时机自动执行：

- 推送 `v*` 发布标签时
- 每天 UTC `02:17` 定时检查默认分支
- 通过 GitHub Actions `workflow_dispatch` 手动重跑

该流程使用隔离的 MySQL/Redis 服务，先执行静态门禁，再依次执行 Gate A、Gate D1 和 Gate E；只有全部通过才会上传绑定当前提交的 manifest、D1 和 E 三份 YAML 证据，失败时不会上传半成品。任务使用并发锁避免同一 ref 上的两次昂贵验证重叠运行。

Gate E 证据只证明测试环境的 readiness，不会执行 Gate exit、切换 Bootstrap routing、启用 Maintenance 或连接生产业务库。生产发布仓库设置应将 `gate-e-readiness` 配置为发布所需检查；工作流文件本身不能替代 GitHub 仓库的保护规则配置。

本地需要完整复现时，先确保隔离服务已启动，再执行：

```bash
DJANGO_TEST_USE_ENV_SERVICES=1 make gate-e-readiness-evidence \
  GATE_E_EXPECTED_COMMIT="$(git rev-parse HEAD)"
make verify-gate-e-readiness-evidence
```

证据生成器会校验提交号、源码逐文件 SHA-256、测试集合、静态检查、真实服务结果和六格性能矩阵；代码或质量门禁配置变化后，旧证据会 fail-closed，必须由该流程重新生成。

虚拟玩家竞技场自愈使用配置默认值即可运行，不需要手动写入 `tournament:newbie` 基线。新服在真人数量较少时允许稳定的高短缺比例经过连续成熟窗口自动建立临时基线；只有检测到 reserve 供给和其他安全指标正常时才会接受该基线。需要调节时，优先调整 `DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_REAL_ENTRIES` 和 `DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_RATIO`，并保留 `DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MIN_MATURE_WINDOWS` 的连续窗口约束。

相关健康熔断参数是 `DJANGO_VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD`、`DJANGO_VIRTUAL_PLAYER_HEALTH_COOLDOWN_SECONDS`、`DJANGO_VIRTUAL_PLAYER_HEALTH_RECOVERY_PROBE_SECONDS` 和 `DJANGO_VIRTUAL_PLAYER_HEALTH_RECOVERY_SUCCESS_THRESHOLD`。这些参数只影响运行期自愈节奏，不需要额外迁移或人工恢复操作。

只有在明确进行 Gate D1 退出评审并取得相应授权时，才继续执行现有的显式状态转换流程。

## WebSocket 重启恢复

认证页面每页会建立通知、在线统计和世界聊天三条 WebSocket。生产默认每用户上限为 `9`，支持同一账号三个标签页。Daphne Worker 租约 TTL 为 `8` 秒、每 `2` 秒续期；实例异常退出或滚动替换后，新连接会清理死亡 Worker 的用户级和 IP 级槽位，目标恢复时间不超过 `10` 秒。

首次从旧连接成员格式发布到 Worker 所有权格式时，旧成员没有 Worker ID，只能按原分数过期，因此可能出现一次最长约 `120` 秒的兼容窗口。所有新连接均使用版本化成员，后续发布不再受该旧窗口影响。

容量拒绝日志包含 `active_slots`、`expired_pruned`、`dead_worker_pruned`、`malformed_members` 和 `worker_id`。`4429` 表示容量暂满；预握手拒绝在浏览器中也可能表现为 `1006`，两者都会在 1～2 秒内重试，以覆盖 8 秒 Worker 租约失效窗口。`1013` 表示 Redis 或租约基础设施暂不可用，使用指数退避。正常恢复流程不应删除 `websocket:*` Redis 键，也不应在 Daphne 启动时做全量清理，因为这会误删其他存活实例的连接所有权。

## 迁移和导库的经验约束

不要长期在统一 `.env.docker` 里把：

```bash
DJANGO_RUN_MIGRATIONS=1
```

设为常开。

原因是 [`docker/entrypoint.sh`](/home/daniel/code/web_game_v5/docker/entrypoint.sh#L61) 会在每个经过该入口脚本的服务中检查这个变量。`web`、`worker`、`beat` 都复用同一个入口脚本，常开会带来并发执行迁移的风险。

更稳的方式是：

- 平时保持 `DJANGO_RUN_MIGRATIONS=0`
- 发布时手动运行一次 `python manage.py migrate --noinput`

如果只是运行期 YAML 规则调整，可以只执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py reload_runtime_configs
```

如果需要给缺失形象的门客模板补图，执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py load_guest_templates --assign-missing-avatars
```

不要带 `--skip-images`，否则图片处理会被跳过。

灰色基础门客（`base_gray_civil`、`base_gray_military`）在单个招募、批量招募和门客卡召唤时，
按实例的文武、性别选择头像，不依赖统一模板头像。因此上面的模板导入命令不会补齐这些门客的实例头像。
先按发布流程加载包含修复的新镜像、完成迁移并更新业务容器，再检查历史门客：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "web" \
  python manage.py backfill_guest_avatars
```

默认 `mode=dry-run` 只查询，不写数据库或媒体文件。`ready` 是找到匹配源图的门客数；确认范围后执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "web" \
  python manage.py backfill_guest_avatars --apply
```

可加 `--manor-id 123` 限定庄园，或 `--limit 100` 限定本次检查的门客数。命令只处理上述灰色基础模板，
保留已有实例头像、特殊形象覆盖和仍可读取的模板头像；重复执行不会重分配已补齐的头像。
`updated` 表示成功补齐数，`unavailable` 表示源图匹配或资源写入失败数。出现失败时命令返回非零退出码，
已成功补齐的记录会保留，修复资源问题后可以重跑。

镜像必须包含 `/app/data/images/guests` 下匹配文武、性别标签的源图；`/app/media` 必须可由应用用户
UID/GID `10001:10001` 写入，并与 Caddy 的 `/srv/media` 指向同一份 `runtime/media` 目录。
数据库中的非空实例头像路径如果对应文件已丢失，应恢复媒体文件；此命令不会覆盖这类已有选择。

## 双层 Caddy 的真实 IP

默认部署让游戏 Caddy 直接接收公网请求。如果外层 Caddy 将请求转发给游戏 Caddy，需要在内层配置
`CADDY_TRUSTED_PROXIES`，否则内层会忽略外层提供的 `X-Forwarded-For`，所有玩家共用外层容器的限流额度。
容器 IP 不是稳定标识，不能把旧机器或旧重建实例的地址写进模板。先在目标服务器上确认两个容器
在外部入口网络上的实际地址：

```bash
docker inspect "gongsi-deploy-caddy-1" \
  --format '{{range $name, $network := .NetworkSettings.Networks}}{{$name}} {{$network.IPAddress}}{{println}}{{end}}'
docker inspect "code-caddy-1" \
  --format '{{range $name, $network := .NetworkSettings.Networks}}{{$name}} {{$network.IPAddress}}{{println}}{{end}}'
```

把外层 Caddy 在 `CADDY_INGRESS_NETWORK` 上的地址填入 `CADDY_TRUSTED_PROXIES`。例如当前这台服务器实际
检查到的外层地址是 `172.18.0.4`，对应配置为：

```dotenv
CADDY_TRUSTED_PROXIES=172.18.0.4/32
```

这里必须填写经 `docker inspect` 确认、由自己控制且正确维护转发头的上游代理地址；不要照抄历史地址。
多个 IP/CIDR 用空格分隔，
默认留空表示不信任上游。Caddy 使用 `trusted_proxies_strict` 从代理链右端解析，防止伪造左端地址。
这项配置与 Django 的 `DJANGO_TRUSTED_PROXY_IPS` 分属两跳，不能互相替代。

内层 Caddy 转发给 Django 的 `X-Forwarded-For` 保留完整代理链，例如
`203.0.113.8, 172.18.0.4`。Django 从右向左剥离可信代理，因此必须同时信任内层和外层的受控地址。
按刚才的检查结果填写：

```dotenv
DJANGO_TRUSTED_PROXY_IPS=<INNER_CADDY_IP>/32,<OUTER_CADDY_IP>/32
```

只信任内层地址时，Django 仍会把外层代理当作玩家 IP；地址应以当前容器检查结果为准。
更新 Django 环境变量后还需重建 `web` 容器，更新 `CADDY_TRUSTED_PROXIES` 后需重建或 reload 内层 Caddy。

同步新的 `docker-compose.prod.yml`、可选的
`docker-compose.prod.double-caddy.yml` 和 `docker/caddy/Caddyfile` 并填写环境变量后，在变更前检查现有
Caddy 的网络和端口绑定。双层部署必须使用覆盖文件持久化外层网络和端口配置；不能只依赖手工
`docker network connect`，也不能用仓库默认文件覆盖服务器已有双层配置，否则重建会丢失连接。

先确认宿主机 `80/443` 的归属：如果已有外层 Caddy 占用端口，目标就是双层部署，直接使用下面的“双层部署”命令，
不能执行单层命令。只有确认端口空闲、游戏 Caddy 直接接收公网请求时，才使用单层命令。

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | rg '(:80->|:443->|0.0.0.0:80|0.0.0.0:443|:::80|:::443)' || true
sudo ss -lntup | rg ':(80|443)\b' || true
```

单层部署（确认宿主机 `80/443` 空闲）:
```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config --quiet
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm --no-deps caddy \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --no-deps --force-recreate caddy
```

若外层 Caddy 已经占用宿主机 `80/443`，必须改用双层覆盖文件；外层 Caddy 与内层 Caddy
必须加入同一个外部 Docker 网络。覆盖文件会移除内层 Caddy 的宿主机端口发布，但保留它与
`web` 所在默认网络的连接，并注册稳定别名 `webgame-caddy`。外层 Caddy 的目标应写成
`webgame-caddy:80`（不要写成 `webgame_caddy`）：

```bash
# Replace this with the external network name stored in .env.docker.
export CADDY_INGRESS_NETWORK="gongsi-deploy_default"
docker network inspect "${CADDY_INGRESS_NETWORK}" >/dev/null
docker compose --env-file ".env.docker" \
  -f "docker-compose.prod.yml" \
  -f "docker-compose.prod.double-caddy.yml" config --quiet
docker compose --env-file ".env.docker" \
  -f "docker-compose.prod.yml" \
  -f "docker-compose.prod.double-caddy.yml" run --rm --no-deps caddy \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
docker compose --env-file ".env.docker" \
  -f "docker-compose.prod.yml" \
  -f "docker-compose.prod.double-caddy.yml" up -d --no-deps --force-recreate caddy
```

确认外层容器也连接了 `CADDY_INGRESS_NETWORK`，并且 `CADDY_INGRESS_ALIAS` 与外层 Caddyfile
中的目标主机完全一致。随后用 `docker inspect` 取得外层 Caddy 在该网络上的实际 IP，填入
`CADDY_TRUSTED_PROXIES`；不要复用它在另一网络上的地址。

不需要重建业务镜像。外层代理应使用稳定的容器地址，或在地址变化后同步精确的信任配置。
现有容器需要快速热更新时，也可在服务器 Caddyfile 顶部的全局 `servers` 块中写入当前外层代理地址、
`trusted_proxies_strict`、`client_ip_headers X-Forwarded-For`，先执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T caddy \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T caddy \
  caddy reload --config "/etc/caddy/Caddyfile" --adapter caddyfile
```

这种方式保留现有容器网络。

验证时使用两个不同公网出口访问注册页面，无需提交注册。内层 Caddy 日志中 `remote_ip` 仍应为外层代理，
但 `client_ip` 应分别为玩家公网 IP，Django 访问日志的 `ip` 也应随之区分。
如果关闭过注册限流，验证后将 `DJANGO_REGISTRATION_RATE_LIMIT_ENABLED` 恢复为 `1` 并重建 `web`。
只清除旧的 `rl:registration_ip:ip:172.18.0.2` 计数不能代替代理修复。

本地可用 Caddy 2.10 程序运行配置解析及隔离 HTTP 转发测试：

```bash
CADDY_TEST_BINARY="/path/to/caddy" CADDY_TEST_HTTP=1 python -m pytest "tests/test_caddy_proxy_forwarding.py" -q
```

## 多个玩家首次注册被限流

需要临时开放注册时，先部署包含开关的新镜像，再在 `.env.docker` 设置
`DJANGO_REGISTRATION_RATE_LIMIT_ENABLED=0`，然后重建 `web` 容器读取新环境：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --no-deps --force-recreate web
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T web \
  python manage.py shell -c 'from django.conf import settings; print(settings.REGISTRATION_RATE_LIMIT_ENABLED)'
```

应输出 `False`。此开关只停用注册 IP/邮箱限流，表单校验、邮件验证、邮件配额、登录和邮件重发限流仍生效。
代理问题修正后将开关恢复为 `1` 并重建 `web`。关闭期间不更新注册计数，旧计数仍按原 TTL 自然过期。
未包含此开关的旧镜像不会因添加环境变量而改变行为。

注册当前按 IP 每小时 5 次、邮箱每小时 3 次限制，表单校验失败的 POST 也会计数。
多个玩家首次提交就被拦截时，先确认实际运行容器的可信代理配置，而不是增加限额：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec -T "web" \
  python manage.py shell -c 'from django.conf import settings; print("trusted_proxies=", settings.TRUSTED_PROXY_IPS); print("access_log_trust_proxy=", settings.ACCESS_LOG_TRUST_PROXY)'
docker inspect "$(docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps -q caddy)" \
  --format '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{$net.IPAddress}} {{$net.GlobalIPv6Address}}{{println}}{{end}}'
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --since=30m "web" \
  | rg 'path=/accounts/register/|Rate limit redirect cache backend unavailable'
```

`DJANGO_USE_PROXY=1` 只启用 HTTPS 转发识别，不会自动信任客户端 IP 转发头。若 Caddy 的实际容器地址
未包含在 `DJANGO_TRUSTED_PROXY_IPS` 中，不同玩家会共用代理地址的注册额度。
将 `.env.docker` 中的可信代理配置修正为实际代理地址或受控的代理网络，并设置
`DJANGO_ACCESS_LOG_TRUST_PROXY=1`。单个容器 IP 可能在重建时变化，必须重新核对；不要信任任意来源
（例如 `0.0.0.0/0`），也不要仅因地址是私网地址就扩大信任范围。
环境文件修改后执行 `docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --no-deps --force-recreate web`
使配置生效，单纯 `restart` 不会重新读取环境文件。

访问日志只有在 `ACCESS_LOG_TRUST_PROXY=True` 时才按同一代理策略记录客户端地址。若玩家真实 IP 已被正确识别，
进一步检查是否共用公网出口（公司、校园或运营商 NAT）、邮箱是否重复，以及 Redis 是否报错。
旧版本在 Redis 故障时也会将普通页面请求提示为“注册请求过于频繁”；修复后该场景提示“系统繁忙”，
实际超限则分别提示当前网络或该邮箱请求频繁。不要清空 Redis 来排查注册限流，那里还包含业务缓存和任务状态。

## Redis 认证

当前生产配置最容易踩的坑是 Redis 密码没有真正传进 Redis 容器。

应用在生产模式下会强制检查 Redis 认证，相关逻辑见 [database.py](/home/daniel/code/web_game_v5/config/settings/database.py#L40)。`REDIS_PASSWORD` 是生产部署必填项；Compose 会在变量缺失或为空时直接拒绝解析，避免应用与 Redis 的认证状态不一致。

```text
AUTH <password> called without any password configured for the default user.
```

要让 Redis 正确启用密码，`docker-compose.prod.yml` 中的 `redis` 服务至少应包含：

```yaml
redis:
  image: redis:7-alpine
  environment:
    REDIS_PASSWORD: ${REDIS_PASSWORD:?set REDIS_PASSWORD in .env.docker}
  command:
    - sh
    - -c
    - |
      exec redis-server \
        --appendonly yes \
        --appendfsync everysec \
        --save "" \
        --requirepass "$$REDIS_PASSWORD";
```

同时 `.env.docker` 里要设置：

```dotenv
REDIS_PASSWORD=your-strong-password
```

改完后不能只重启应用，必须重建 Redis 容器：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" stop "web" "worker" "worker_battle" "worker_timer" "worker_timer_scan" "worker_timer_safety" "worker_timer_maintenance" "beat"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate "redis"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate "web" "worker" "worker_battle" "worker_timer" "worker_timer_scan" "worker_timer_safety" "worker_timer_maintenance" "beat"
```

自检命令：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "redis" sh -c 'echo "$REDIS_PASSWORD"'
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "web" sh -c 'echo "$REDIS_PASSWORD"'
```

如果 `redis` 容器内输出为空，说明 Compose 配置仍未把密码注入进去。

## 镜像命名与清理经验

“No such image” 往往不是镜像没导入，而是镜像名写错了。例如：

```text
Error response from daemon: No such image: web_game_v5:v1
```

此时应先查看实际镜像：

```bash
docker images
docker images | rg "webgame|web_game_v5"
```

如果实际镜像名是 `webgame:v1`，就不要在命令里写成 `web_game_v5:v1`。

如果需要统一标签，可重新打 tag：

```bash
docker tag "旧镜像名:旧tag" "webgame:v1"
```

删除旧镜像前先确认没有旧容器占用：

```bash
docker ps -a --filter "ancestor=webgame:v1"
docker rm -f "<container_id>"
docker rmi "webgame:v1"
```

只想清理所有未使用镜像时使用：

```bash
docker image prune -a -f
```

如果要看 Docker 当前整体占用：

```bash
docker system df
```

## WSL2 使用经验

如果本地是 WSL2，Docker 一般有两种来源：

- Docker Desktop for Windows 提供 daemon
- WSL 发行版内自行安装 Docker Engine

最常见且稳定的是 Docker Desktop + WSL Integration。排查顺序：

1. 在 Windows 启动 Docker Desktop
2. 确认 Docker Desktop 已给当前 WSL 发行版开启 WSL Integration
3. 在 WSL 内执行：

```bash
docker version
docker ps
```

如果清理镜像后 Windows 磁盘空间没有立刻下降，可在 Windows 侧执行：

```powershell
wsl --shutdown
```

这通常能让 WSL2 / Docker Desktop 的磁盘占用统计及时回收。
