# Docker 部署运行手册

> 最近校正：2026-07-13

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

- 应用业务镜像一张，同时供 `web`、`worker`、`worker_battle`、`worker_timer`、`beat` 复用
- `db` 使用 MySQL 容器
- `redis` 使用 Redis 容器
- `caddy` 直接监听公网 `80/443`，自动管理 HTTPS、静态资源和反向代理

生产镜像内应用进程使用 UID/GID `10001:10001` 运行；`web` 容器在
[`docker-compose.prod.yml`](/home/daniel/code/web_game_v5/docker-compose.prod.yml#L42)
中启用了 `read_only: true`。因此 `DJANGO_COLLECTSTATIC=1` 依赖
`./runtime/staticfiles:/app/staticfiles` 这个可写 volume，首次部署和由 root 创建
runtime 目录后，都必须把 runtime 目录归属修正给 `10001:10001`。
`worker`、`worker_battle`、`worker_timer`、`beat` 没有挂载 `/app/staticfiles`，
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

## 首次部署

首次部署前准备目录和环境变量：

```bash
cd "/opt/web_game_v5"
mkdir -p "runtime/media" "runtime/staticfiles" "runtime/celerybeat"
chown -R "10001:10001" "runtime/staticfiles" "runtime/media" "runtime/celerybeat"
cp ".env.docker.prod.example" ".env.docker"
```

编辑 `.env.docker`，至少替换数据库、Redis、Django 密钥，并确认
`CADDY_SITE_ADDRESS`、`DJANGO_ALLOWED_HOSTS` 和 `DJANGO_CSRF_TRUSTED_ORIGINS`
使用同一个生产域名。启动前验证 Compose 和 Caddy 配置：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" config >/dev/null
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm --no-deps "caddy" \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
```

先启动基础设施：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d "db" "redis"
```

再手动执行迁移：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" python manage.py migrate --noinput
```

如果需要导入模板数据：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" python manage.py bootstrap_game_data --skip-images
```

最后启动业务服务：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d "web" "worker" "worker_battle" "worker_timer" "beat" "caddy"
```

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

常用命令：

```bash
cd "/opt/web_game_v5"
docker load -i "webgame_v1.tar.gz"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" stop "beat" "worker" "worker_battle" "worker_timer"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" python manage.py migrate --noinput
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate --no-deps \
  "web" "worker" "worker_battle" "worker_timer" "beat"
```

如果这次发布修改了 Caddy 配置，先校验再平滑加载：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "caddy" \
  caddy validate --config "/etc/caddy/Caddyfile" --adapter caddyfile
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" exec "caddy" \
  caddy reload --config "/etc/caddy/Caddyfile" --adapter caddyfile
```

验证状态：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" ps
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "web"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "worker"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" logs --tail=100 "caddy"
curl --fail --show-error --silent "https://${CADDY_SITE_ADDRESS}/health/live"
```

Caddy 日志首次出现证书签发成功后，后续续期由 Caddy 自动完成。排查证书问题时重点检查域名解析、端口占用、防火墙以及 `caddy_data` 卷是否被误删。

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

如果需要给缺失形象的门客补图，执行：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" run --rm "web" \
  python manage.py load_guest_templates --assign-missing-avatars
```

不要带 `--skip-images`，否则图片处理会被跳过。

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
      exec redis-server --appendonly yes --requirepass "$$REDIS_PASSWORD";
```

同时 `.env.docker` 里要设置：

```dotenv
REDIS_PASSWORD=your-strong-password
```

改完后不能只重启应用，必须重建 Redis 容器：

```bash
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" stop "web" "worker" "worker_battle" "worker_timer" "beat"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate "redis"
docker compose --env-file ".env.docker" -f "docker-compose.prod.yml" up -d --force-recreate "web" "worker" "worker_battle" "worker_timer" "beat"
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
