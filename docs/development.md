# 春秋乱世庄园主 - 开发指南

> 最近校正：2026-03-26

本文档只记录当前仓库仍然成立的开发流程、命令和环境边界。

## 环境要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 与 `pyproject.toml` / mypy 目标一致 |
| Node.js | 18+ | 用于 Tailwind 构建 |
| MySQL | 8.0+ | 真实服务环境与集成测试使用 |
| Redis | 7.0+ | Celery、Channels、缓存、在线态使用 |

## 本地开发

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
npm install
```

如果只想安装当前仓库已提交的锁定依赖：

```bash
make install-lock
```

仓库已提交 `requirements-dev.lock.txt`，普通的 `make install` 会优先使用它，保证本地与 CI 使用同一套开发依赖。

如果有意升级开发依赖，修改 `requirements-dev.txt` 后重新生成并检查锁文件：

```bash
make lock-dev
make install-dev-lock
python -m pip check
```

不要只修改 `requirements-dev.txt` 而跳过锁文件更新；类型检查和真实服务测试都依赖锁定的开发工具版本。

### 2. 准备环境变量

```bash
cp .env.example .env
```

开发时至少确认：

- `DJANGO_DEBUG=1`
- `DJANGO_SECRET_KEY` 已设置
- 如果要连真实 MySQL / Redis，补齐对应连接参数

生成密钥：

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### 3. 初始化数据

```bash
python manage.py migrate
python manage.py bootstrap_game_data --skip-images
npm run build:css
```

也可以使用 Makefile 包装命令：

```bash
make migrate
make bootstrap-data
```

如果只改了运行期 YAML，可单独刷新：

```bash
python manage.py reload_runtime_configs
```

### 4. 启动服务

只需要 HTTP 页面：

```bash
make dev
```

需要 WebSocket：

```bash
make dev-ws
```

认证页面会分别建立通知、在线统计和世界聊天三条 WebSocket。默认用户容量为 `9`，即支持同一账号同时打开三个标签页。连接槽 TTL 为 `30` 秒；Daphne 进程通过 TTL `8` 秒、每 `2` 秒续期的 Redis Worker 租约声明连接所有权。进程异常退出后，新连接会原子清理租约已失效 Worker 的槽位，不需要手工删除 Redis 键。

需要异步任务：

```bash
make worker
make beat
```

Tailwind 监听：

```bash
npm run watch:css
```

### 5. 常用地址

- 首页：`http://127.0.0.1:8000/`
- 管理后台：`http://127.0.0.1:8000/admin/`
- 健康检查：`http://127.0.0.1:8000/health/live`

## Docker Compose

推荐的本地外部服务启动方式：

```bash
cp .env.docker.example .env.docker
docker compose up --build
```

当前 `docker-compose.yml` 提供：

- `db`：MySQL 8.4
- `redis`：Redis 7
- `web`：`runserver`
- `worker`：Celery worker
- `beat`：Celery beat

生产 compose 在 `docker-compose.prod.yml`，默认形态为：

- `web` 使用 `daphne`
- `worker` / `worker_battle` / `worker_timer` / `worker_timer_scan` / `worker_timer_maintenance` 分队列运行
- `caddy` 负责自动 HTTPS、静态资源与 HTTP/WebSocket 反向代理

## 测试与门禁

### 默认快速门禁

```bash
make test
```

等价于：

```bash
python -m pytest -m "not integration and not evidence"
```

默认使用 hermetic 测试环境：

- SQLite 临时库
- `LocMemCache`
- `InMemoryChannelLayer`
- `memory://` Celery broker / backend

这套门禁不验证真实 MySQL 行锁、Redis 共享语义、真实 Channels fan-out 与真实 Celery broker 行为。

### 真实服务门禁

```bash
make test-real-services-up
make test-real-services
make test-real-services-down
```

`make test-real-services-up` 会启动 compose 中的 `db` / `redis`，并默认发布到宿主机 `127.0.0.1:13306` 与 `127.0.0.1:16379`，避免和常见本机 MySQL/Redis 默认端口冲突。需要自定义端口时可传入 `REAL_SERVICES_MYSQL_PORT` / `REAL_SERVICES_REDIS_PORT`；`test-real-services`、`test-critical` 与 `test-integration` 会显式注入匹配的 `DJANGO_DB_*` 与 Redis URL，避免误连本机默认服务。

该命令会先预检 MySQL 与 Redis；若外部服务不可用，会在进入 pytest 前直接失败，避免把环境缺失伪装成业务回归失败。

固定验收流程：

```bash
DJANGO_TEST_USE_ENV_SERVICES=1 make test-gates
```

只跑 `integration` 标记集：

```bash
DJANGO_TEST_USE_ENV_SERVICES=1 make test-integration
```

集成测试默认启用详细节点名、最慢测试统计和单测试 900 秒超时；如果需要临时调整，可覆盖 `INTEGRATION_PYTEST_ARGS`，但不要在 CI 中移除超时诊断：

```bash
DJANGO_TEST_USE_ENV_SERVICES=1 \
INTEGRATION_PYTEST_ARGS='-vv --durations=30 --timeout=900' \
make test-integration
```

### 静态检查

```bash
make lint
make format
make cov
npm run test:js
```

`make lint` 当前执行：

- `flake8`
- `mypy`

`npm run test:js` 当前用于覆盖聊天挂件脚本的纯逻辑回归，不替代 Python 测试。

## 调试工具

### battle debugger

`battle_debugger` 默认不挂载，只有在开发环境显式打开时才可用：

```bash
export DJANGO_DEBUG=1
export DJANGO_ENABLE_DEBUGGER=1
make dev
```

路由会额外挂到 `/debugger/`。该工具要求登录且必须是 staff 用户。

### OpenAPI / 文档页

项目内置：

- `/api/schema/`
- `/api/docs/`
- `/api/redoc/`

是否可访问受以下设置控制：

- `DJANGO_ENABLE_API_DOCS`
- `DJANGO_API_DOCS_REQUIRE_AUTH`

当前仓库的业务入口主要仍是 Django 页面路由与少量 `JsonResponse` 端点，不要把这组地址理解成“完整 REST API 平台”。

## 数据与配置

### 模板数据导入

以下命令仍是有效的独立导入入口：

```bash
python manage.py load_building_templates
python manage.py load_technology_templates
python manage.py load_item_templates
python manage.py load_troop_templates --skip-images
python manage.py load_guest_templates --skip-images
python manage.py load_mission_templates
python manage.py seed_work_templates
```

通常直接使用：

```bash
python manage.py bootstrap_game_data --skip-images
```

### YAML 校验

```bash
python manage.py validate_yaml_configs
python manage.py validate_yaml_configs --strict-coverage
```

当 `data/` 下新增 YAML 文件时，推荐至少执行一次 `--strict-coverage`，避免新增文件被静默排除在 schema 校验之外。

## 协作约束

- 页面读路径默认走 Django template view，不要擅自把现有页面文档写成 REST 契约。
- 运行期 YAML 改动后，`reload_runtime_configs` 只保证 service loader 更新；已经按 `from X import Y` 缓存下来的模块级常量仍可能需要重启进程。
- 改并发状态机、锁或任务派发前，先看 [`write_model_boundaries.md`](write_model_boundaries.md) 与 [`domain_boundaries.md`](domain_boundaries.md)。
