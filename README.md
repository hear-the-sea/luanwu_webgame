# 春秋乱世庄园主

> 最近校正：2026-07-29
>
> 本 README 只保留当前仓库能直接验证的工程事实。补充文档见 [`docs/index.md`](docs/index.md)。

## 项目简介

这是一个以春秋战国题材为背景的 Django 游戏项目。当前仓库已实现账号、庄园、门客、战斗、交易、帮会、地图、消息通知与部分实时功能，玩法模板主要由 `data/*.yaml` 驱动。
<img width="1000" height="228" alt="image" src="https://github.com/user-attachments/assets/b3edb782-02c8-417f-8272-26d6dd8baac9" />

## 虚拟玩家 V2 部署快照

> 以下内容是 2026-07-29 17:09（Asia/Shanghai）的环境快照，不代表读取文档时的实时运行态。新环境仍需单独执行 migration、策略发布、档案迁移和 Gate 切换，不会仅因拉取代码而自动继承。

| Gate | 能力 | 快照状态 |
|------|------|----------|
| Gate D1 | 新建虚拟玩家使用 V2 Bootstrap | 已开启 |
| Gate D2 | 使用获批匿名真人快照进行参考分布校准 | 未开启，保持 `INTENTIONALLY_OFF` |
| Gate E | 已有 V2 虚拟玩家执行自动 Maintenance | 已开启 |

该快照的持久路由为 `bootstrap_mode=v2_active`、`maintenance_mode=v2_active`、`revision=10`；revision 会在安全监控消费新窗口时自动递增。快照内 44 个 `BotProfile` 均已保留式迁移为 `engine_version=2`，其中 32 个 active、12 个 retired；运行时可用的 V1 档案为 0，V2 必填字段、策略校验和及声望段复检均无异常。Policy v1 已发布，独立 policy rollout 仍关闭。`hourly:20260729T080000Z` 安全窗口已完整冻结并消费，五条心跳各 60 个采样点、最大间隔 60 秒，且没有维护失败、硬约束、经济上限、重复提交或性能违规。恢复后已处理 30 个到期档案，其中 2 个执行实际成长动作，28 个按强度上限或领域约束正常推进计划；快照采集时到期数为 0。人口状态为 32 maintained、32 planned、0 unplanned、32 attackable，目标总数为 32。

Gate D2 在该快照中没有激活任何声望段：`calibration_routes=[]`，且 `reference_snapshot_catalog` 为空。V2 在没有获批代表性真人 artifact/report 时继续使用实时合格真人样本与版本化保守起点，不影响虚拟玩家创建和维护，也不得把 synthetic fixture 表述为可信真人校准。

自动生成与培养由 Celery Worker 和 Beat 消费虚拟玩家人口及 Maintenance 任务；快照采集时两者均已启动且 Worker ping 正常。路由、档案和进程状态都会变化，排障或发布前必须以数据库和进程现场检查为准。路由处于 `v2_active` 只表示执行链已获准；未运行 Worker/Beat 时不会产生周期性自动执行，启动服务仍按下文 `make worker`、`make beat` 操作。

## 致我的童年回忆----乱舞春秋
当前前端形态为：

- Django Templates
- Tailwind CSS 构建产物
- 手写 CSS / JavaScript

仓库当前不是 SPA，也不依赖 Bootstrap。

## 技术栈

- Python 3.12
- Django 5
- Django REST framework + drf-spectacular
- Channels + Daphne
- Celery
- Redis
- MySQL（真实服务环境） / SQLite（本地默认与 hermetic 测试）
- Tailwind CSS

默认 `.env.example` 使用 SQLite，hermetic 测试会退回 SQLite / LocMem / InMemory channel layer / memory Celery；普通开发运行若启用 WebSocket、缓存或 Celery，仍需要本地 Redis。真实并发与 Redis 语义需外部服务验证。

## 快速开始

### 1. 安装依赖

```bash
make install
npm install
```

### 2. 准备环境变量

```bash
cp .env.example .env
```

按需补充数据库、Redis 与密钥配置。仓库还提供：

- `.env.docker.example`
- `.env.docker.prod.example`

### 3. 初始化数据

```bash
python manage.py migrate
python manage.py bootstrap_game_data --skip-images
# 或 make bootstrap-data
npm run build:css
```

### 4. 启动服务

只看页面：

```bash
make dev
```

需要 WebSocket / 异步任务：

```bash
make dev-ws
make worker
make beat
```

## 测试与质量门禁

默认快速门禁：

```bash
make test
```

真实服务门禁：

```bash
make test-real-services-up
make test-real-services
make test-real-services-down
```

`make test-real-services-up` 会在宿主机发布 MySQL `127.0.0.1:13306` 与 Redis `127.0.0.1:16379`，避开常见本机 3306/6379 服务。需要自定义端口时可传入 `REAL_SERVICES_MYSQL_PORT` / `REAL_SERVICES_REDIS_PORT`；测试目标会显式注入匹配的 Django DB 与 Redis 连接参数，避免误连本机默认服务。该命令现在会先预检 MySQL 与 Redis 可用性；若外部服务未启动，会在进入 pytest 前直接失败。

固定验收流程：

```bash
DJANGO_TEST_USE_ENV_SERVICES=1 make test-gates
```

静态检查：

```bash
make lint
```

前端脚本回归：

```bash
npm run test:js
```

## 前端资源边界

- 样式源码：`src/input.css`
- 样式产物：`static/css/tailwind.css`
- 手写样式：`static/css/*.css`
- 手写脚本：`static/js/*.js`

仓库当前没有 JS bundler 聚合业务脚本；Tailwind 只负责样式构建。

## 目录概览

```text
accounts/    账号与登录态
battle/      战斗推演与战报
config/      Django / Celery / settings
core/        健康检查、中间件、基础设施工具
data/        YAML 配置与静态资源
docs/        维护中的技术文档
gameplay/    庄园、任务、地图、仓库、生产、竞技场
guests/      门客招募、培养、装备、技能
guilds/      帮会与英雄池
trade/       商铺、银庄、交易行、拍卖
websocket/   Channels consumers 与后端适配
tests/       pytest 测试
```

## 文档入口

- [文档索引](docs/index.md)
- [架构设计](docs/architecture.md)
- [开发指南](docs/development.md)
- [接口与实时入口](docs/api.md)
- [数据库边界](docs/database.md)
- [配置数据说明](docs/config_data.md)
- [健康检查运行手册](docs/runbook_health_checks.md)

## 相关文件

- `Makefile`
- `pyproject.toml`
- `requirements.txt`
- `requirements.lock.txt`
- `requirements-dev.txt`
- `package.json`
- `docker-compose.yml`
- `docker-compose.prod.yml`

联系方式：qq593128360
