# 春秋乱世庄园主

> 最近校正：2026-03-26
>
> 本 README 只保留当前仓库能直接验证的工程事实。补充文档见 [`docs/index.md`](docs/index.md)。

## 项目简介

这是一个以春秋战国题材为背景的 Django 游戏项目。当前仓库已实现账号、庄园、门客、战斗、交易、帮会、地图、消息通知与部分实时功能，玩法模板主要由 `data/*.yaml` 驱动。
<img width="1000" height="228" alt="image" src="https://github.com/user-attachments/assets/b3edb782-02c8-417f-8272-26d6dd8baac9" />
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
DJANGO_TEST_USE_ENV_SERVICES=1 make test-real-services
```

该命令现在会先预检 MySQL 与 Redis 可用性；若外部服务未启动，会在进入 pytest 前直接失败。

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
