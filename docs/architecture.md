# 春秋乱世庄园主 - 系统架构

> 最近校正：2026-03-26

本文档只保留当前仓库可以直接从代码、配置和运行入口验证的架构事实；历史方案、已完成治理记录与阶段性审计请看 [`technical_audit_2026-03.md`](technical_audit_2026-03.md) 和 [`optimization_plan.md`](optimization_plan.md)。

## 总览

当前项目是一个以 Django Template 为主交互层的多人在线游戏服务，核心事实如下：

- 页面渲染以 Django Template 为主，不是 SPA。
- 前端样式由 Tailwind CSS 构建到 `static/css/tailwind.css`，页面脚本仍是手写 `static/js/*.js`，没有 JS bundler。
- HTTP 与 WebSocket 共享同一套 Django 业务代码；WebSocket 入口由 Channels + ASGI 暴露。
- 异步与定时任务通过 Celery 承担，Redis 同时提供 broker / result backend / channel layer / cache。
- 默认本地开发和 hermetic 测试可以回退到 SQLite + LocMem + InMemory channel layer + memory Celery；真实并发语义仍需要外部 MySQL / Redis 验证。

## 运行拓扑

### 开发环境

常见启动形态：

- `make dev`：Django `runserver`，只覆盖 HTTP 页面。
- `make dev-ws`：`daphne config.asgi:application`，覆盖 HTTP + WebSocket。
- `make worker`：Celery worker。
- `make beat`：Celery beat。

### 生产 / 近生产 Compose 形态

`docker-compose.prod.yml` 当前默认分为：

- `web`：`daphne`，负责 HTTP + WebSocket。
- `worker`：默认队列。
- `worker_battle`：`battle` 队列。
- `worker_timer`：`timer` 队列。
- `beat`：定时扫描与心跳。
- `caddy`：自动 HTTPS、静态/媒体资源、HTTP 与 WebSocket 反向代理。
- `db`：MySQL 8.4。
- `redis`：Redis 7。

## 主要应用边界

| 模块 | 当前职责 | 说明 |
|------|----------|------|
| `accounts` | 用户认证、资料、单会话控制 | 自定义 `AUTH_USER_MODEL` 与会话治理 |
| `gameplay` | 庄园、建筑、地图、任务、资源、背包、竞技场、打工、监狱/结义 | 主玩法聚合层 |
| `guests` | 门客模板、招募、培养、装备、工资、叛逃 | 既有模板数据也有运行期实例 |
| `battle` | 战斗引擎、战报、战斗相关任务 | 既有同步执行入口，也有异步报告后处理 |
| `trade` | 商铺、银庄、交易行、拍卖 | 包含运行期 YAML 配置与结算任务 |
| `guilds` | 帮会、成员、科技、仓库、英雄池 | 部分规则由 YAML 驱动 |
| `websocket` | consumers、routing、在线态 / 世界聊天后端 | 通过 Channels 暴露实时入口 |
| `battle_debugger` | 开发态战斗调试工具 | 仅在 `DEBUG=1` 且显式打开时注册 |
| `core` | 健康检查、中间件、异常、基础设施工具 | 为其他 app 提供共享能力 |

## 分层约束

当前仓库的主流协作方式仍然是三层分离：

1. View / Consumer 层
   负责请求提取、权限检查、表单处理、响应格式化。
2. Service 层
   负责业务逻辑、事务边界、跨模块协调、异步任务调度。
3. Model / Query 层
   负责 ORM 数据结构、约束、查询入口与兼容导出。

实践约束：

- 页面动作默认优先调用 service，而不是把业务逻辑堆进 view。
- 并发状态机逻辑优先收敛在 service / task 中，并通过测试固定行为。
- `gameplay.models`、`guilds.models` 已按子模块拆包，但保留 `__init__.py` 兼容导出。
- `gameplay.services` 本身只保留包级入口说明，仓内新代码应优先从具体子模块导入。

## URL 与入口形态

主路由定义于 [`config/urls.py`](/home/daniel/code/web_game_v5/config/urls.py)。

稳定入口前缀：

- `/accounts/`
- `/manor/`
- `/guests/`
- `/battle/`
- `/trade/`
- `/guilds/`
- `/health/`
- `/api/schema/`、`/api/docs/`、`/api/redoc/`
- `/debugger/` 仅在开发态可选启用

WebSocket 路由定义于 [`websocket/routing.py`](/home/daniel/code/web_game_v5/websocket/routing.py)，当前注册：

- `ws/notifications/`
- `ws/online-stats/`
- `ws/chat/world/`

### WebSocket 连接容量与恢复

每个认证页面固定使用通知、在线统计和世界聊天三条连接。默认每用户允许 `9` 条并发连接，对应三个标签页；IP 级容量和握手令牌桶在 ASGI 认证前独立生效。

每个 Daphne 进程维护一份 Redis Worker 租约，连接槽以 `v2|worker_id|connection_id` 记录所有权。用户级和 IP 级 Lua 脚本在申请新槽时使用 Redis 时间，并原子清理过期槽及租约已失效 Worker 的槽。该机制支持多个 Daphne 实例和滚动发布，不在启动时扫描或全量删除 Redis 数据。

浏览器端三个客户端共享关闭码策略：`4401/4403` 停止重连，`4429` 和浏览器可能映射出的预握手 `1006` 在 1～2 秒内重试，`1013` 使用带抖动的指数退避。连接建立本身不清零退避；收到有效消息或稳定连接 30 秒后才重置。

## 异步任务与定时扫描

Celery 配置位于 [`config/settings/celery_conf.py`](/home/daniel/code/web_game_v5/config/settings/celery_conf.py)。

当前固定三类队列：

| 队列 | 用途 |
|------|------|
| `default` | 通用任务、站内维护、健康探针往返 |
| `battle` | 战斗相关任务 |
| `timer` | 定时完成类任务与扫描兜底 |

当前 beat 中的重要周期任务包括：

- 任务、建筑、科技、兵种、生产、招募等完成态扫描
- 竞技场轮次扫描
- 踢馆 / 侦查相关扫描
- 交易行过期挂单处理
- 拍卖轮次结算与新轮次创建
- `core.record_celery_beat_heartbeat`

关键事实：

- 仓库没有依赖“单次精确延迟任务一定成功”的乐观假设，多个玩法都保留扫描兜底。
- 拍卖轮次不是结束瞬间同步结算，而是由 `trade.settle_auction_round` 轮询到期轮次处理。
- 真实多进程互斥、行锁与 Redis 共享语义不能靠 hermetic 测试替代。

## 战斗与玩法写路径

### 战斗

`battle/` 当前不是单一“引擎文件”，而是由以下层次组成：

- `battle/services.py`：对外服务入口
- `battle/execution.py`、`battle/locking.py`、`battle/rewards.py`：执行、锁与奖励逻辑
- `battle/simulation/`：回合、伤害、选敌、顺序等模拟细节
- `battle/combatants_pkg/`：战斗参与者装配
- `battle/tasks.py`：异步任务

### 任务 / 踢馆 / 招募等状态机

当前仓库对并发敏感玩法已经明显拆分：

- `gameplay/services/missions_impl/`
- `gameplay/services/raid/`
- `gameplay/services/recruitment/`

这类写路径的共同约束：

- 状态推进优先通过显式 service / command 实现。
- 高风险路径需要真实服务门禁覆盖。
- 读路径与写路径逐步解耦，详见 [`domain_boundaries.md`](domain_boundaries.md) 与 [`write_model_boundaries.md`](write_model_boundaries.md)。

## 配置数据策略

`data/` 下的 YAML 目前分成两类：

1. 运行期读取并带缓存的规则文件
   例如商铺、拍卖、竞技场、生产、帮会规则等。
2. 需要导入数据库的模板文件
   例如建筑、科技、物品、兵种、门客、技能、任务模板。

当前统一入口：

- 刷新运行期缓存：`python manage.py reload_runtime_configs`
- 导入数据库模板：`python manage.py bootstrap_game_data --skip-images`
- 校验当前 YAML 契约：`python manage.py validate_yaml_configs --strict-coverage`

注意：

- 并不是所有运行期 YAML 都已经纳入 `reload_runtime_configs()`；例如 `recruitment_rarity_weights.yaml` 仍主要依赖模块缓存与进程重启刷新。
- 文档里不再维护“手工抄录的 YAML 字段说明”；字段约束以 schema 校验与 loader 实现为准。

## 测试门禁

当前仓库的测试口径分成两层：

| 门禁 | 命令 | 覆盖重点 |
|------|------|----------|
| Hermetic rapid gate | `make test` / `make test-unit` | 快速反馈；SQLite、LocMem、InMemory channel layer、memory Celery |
| Real external-service gate | `make test-real-services-up && make test-real-services` | MySQL 行锁、Redis 语义、真实 Channels / Celery；默认宿主机端口为 MySQL `13306`、Redis `16379`，可用 `REAL_SERVICES_MYSQL_PORT` / `REAL_SERVICES_REDIS_PORT` 覆盖 |

补充入口：

- `DJANGO_TEST_USE_ENV_SERVICES=1 make test-critical`
- `DJANGO_TEST_USE_ENV_SERVICES=1 make test-integration`
- `DJANGO_TEST_USE_ENV_SERVICES=1 make test-gates`
- `npm run test:js`：当前覆盖聊天挂件的纯逻辑脚本测试

## 健康检查与可观测性

健康检查入口：

- `/health/live`
- `/health/ready`

`/health/ready` 当前可以按配置检查：

- `db`
- `cache`
- `channel_layer`
- `celery_broker`
- `celery_workers`
- `celery_beat`
- `celery_roundtrip`
- `websocket_routing`（仅在路由导入失败时出现为失败项）

实现位于 [`core/views/health.py`](/home/daniel/code/web_game_v5/core/views/health.py)；详细运维口径见 [`runbook_health_checks.md`](runbook_health_checks.md)。

## 部署与资源边界

前端资源边界：

- 样式源码：`src/input.css`
- 样式产物：`static/css/tailwind.css`
- 手写样式：`static/css/*.css`
- 页面脚本：`static/js/*.js`

部署事实：

- 生产 compose 默认让 `web` 容器只读挂载应用目录，运行时写入主要落在 `/tmp` 与 `runtime/*`。
- 静态资源通过 `caddy` 暴露，媒体文件与 `staticfiles/` 走独立只读挂载目录；证书状态由 Caddy 命名卷持久化。
- battle debugger 不是生产常驻能力，不应依赖它作为正式调试入口。

## 变更协作建议

以下情况需要同步更新文档：

- 新增或删除稳定 URL / WebSocket 前缀
- 新增 Celery 队列、beat 任务或健康检查项
- `reload_runtime_configs()` 的覆盖面变化
- 主测试门禁命令变化
- 生产 compose 进程拓扑变化

如果只是阶段性治理记录或技术债进展，不要把它们塞回本文件，更新对应的审计 / 计划文档即可。
