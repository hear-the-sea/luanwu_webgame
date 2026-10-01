# 性能优化与行为一致性审查（2026-09-30）

本轮针对线上日志已定位的重复数据库查询与 WebSocket 重连放大，采用局部改动。业务规则、数据库结构与运行配置保持原样；代码完成本地验证后供发布。本报告的耗时是本地 MySQL 基准，不是线上整任务或 HTTP 请求的加速承诺。

## 实施与收益

| 路径 | 优化前 → 优化后 | 本地 MySQL 耗时中位数 |
|---|---|---|
| 人口规划，5 地区 × 8 声望段 | 人口统计 120 → 2 条查询；含回填需求读取 121 → 3 条 | 复核修正版六种配置原约 85–93 ms → 8.4–8.8 ms |
| 一键疗伤，1 瓶 | 总 SQL 11 → 9；SELECT 5 → 3 | 6.784 → 5.007 ms |
| 一键疗伤，10 瓶 | 总 SQL 65 → 45；SELECT 23 → 3 | 37.801 → 20.268 ms |
| 一键疗伤，100 瓶 | 总 SQL 605 → 405；SELECT 203 → 3 | 344.836 → 168.058 ms |
| WebSocket 登录失效与短命连接 | 世界聊天、在线统计以 4401/4403 终止失效登录重试；三种客户端在连接稳定 30 秒后才重置退避 | 连续短命连接收到初始消息后，重试仍按 1.5 秒 × 5、2 秒、4 秒递增（固定随机种子的测试值） |

人口规划基准覆盖目标/实际声望归属 × 引擎 None/1/2 六种配置，每种预热后进行 5 次配对测量。疗伤每个药品数量场景同样进行 5 次配对测量，开始状态相同。计时只覆盖被测服务段，不含数据库建库、数据加载和测试清理。SQL 使用 Django 查询捕获；包含保存点操作，因此总 SQL 与 SELECT 分开记录。

## 边界审查

- 人口规划继续使用既有成员筛选条件与独立区间计数。目标声望为空的 fallback、实际声望、重叠区间、无上界、保护期边界、员工排除、引擎/策略版本、竞技场需求与供给均有新旧完整计划对照。容量锁、安全门、创建预算与公平处理流程沿用原实现；每次调用重新查询，没有跨事务缓存。
- 疗伤复用外层事务已经锁定的对象，单次用药公共接口仍按原顺序锁读。每瓶继续执行原治疗、库存扣减、写入与保存点。每次尝试复制实例，成功才接续其全部状态，防止数据库回滚后内存状态污染下一次治疗。测试对照全部数据库字段、疗伤统计与训练恢复派发参数；覆盖药品/门客优先顺序、忠诚衰减、药品不足、错误配置、保存点失败和后续扣药故障导致整体回滚。
- WebSocket 使用通知消费者已有的关闭码与会话守卫。鉴权拒绝时只完成握手以传递终止码，随即关闭，不进入业务连接、不发送业务数据。Origin、IP 与人数限制继续使用原检查。初始消息继续更新界面、通知与聊天确认；消息到达不再耗尽 30 秒稳定性计时器。

这三处分别保留领域规则、事务边界与连接安全边界，只缩短重复读取或修正退避状态更新，符合 KISS/DRY。本轮没有引入新的缓存、调度预算、批量写入机制或依赖。

## 验证与复跑

详细的实库基准、验证结果与本轮文件哈希见同目录 `performance_optimization_20260930.json`。

后续正确性复核补齐了 MySQL 大小写/重音/尾空格地区键兼容边界，人口聚合改为 SQL CASE 规范分组。本表与 JSON 当前人口基准、哈希已更新，初版基准保留在 JSON 的 `initial_population_benchmarks`。复核后的 32 项人口实库检查、6 项独立对照与候选实测、189 项关联回归、17 项 WebSocket 专项均通过，详见 `performance_review_20260930.md`。

- 现有业务回归：419 项通过，4 项需外部环境的检查跳过；新增 WebSocket 实际 ASGI 鉴权终止检查另 9 项通过。
- 本地真实 MySQL：56 项全部通过，无跳过，包含 7 个并发场景（双批量治疗、单药/批量库存争用、故障回滚后的等待调用、既有单药并发与人口物化上限）。
- JavaScript：4 个测试文件直接运行，共 53 项通过，覆盖终止鉴权、短命连接、30 秒稳定恢复、消息渲染与确认。
- Black、isort、flake8、4 个运行时 Python 文件的 mypy、改动 JavaScript 的 ESLint 与 diff 检查全部通过。

隔离快速回归使用项目的默认 SQLite/内存服务测试配置。实库验证显式设置本地 MySQL `127.0.0.1:13306` 和本地 Redis `127.0.0.1:16379`，使用 `test_bank_bulk_release_check` 隔离库，既有数据库与 Redis 服务直接复用。

配置好本地隔离测试环境后，可复跑专项：

```bash
python -m pytest -q -s --reuse-db tests/test_virtual_player_population_aggregation.py
python -m pytest -q -s --reuse-db tests/test_bulk_guest_healing_query_reuse.py
python -m pytest -q --reuse-db tests/test_bulk_guest_healing_concurrency_integration.py
python -m pytest -q --reuse-db tests/test_virtual_player_gate_d1_concurrency_integration.py::test_two_population_workers_materialize_only_one_profile_for_one_deficit
python -m pytest -q tests/test_websocket_terminal_auth.py
node static/js/tests/websocket_reconnect.test.js
node static/js/tests/online_stats.test.js
node static/js/tests/chat_widget_connection.test.js
node static/js/tests/notifications_toast.test.js
```

实库必须设置 `DJANGO_TEST_USE_ENV_SERVICES=1` 并显式覆盖数据库和 Redis 环境变量；不要直接沿用项目 `.env` 中的开发数据库地址。pytest 自动给数据库名前加 `test_`。第一轮实库测试发现测试模板标识超过 SlugField 的 50 字符限制，已缩短测试随机后缀；业务模型与字段长度未改。

## 观测口径与剩余验证

- 人口规划只测量统计与规划路径。原 266 秒人口滚动任务包含物化、安全检查等其他阶段，完整收益要发布后观察同负载任务耗时与队列等待。
- 疗伤仍逐瓶写入与保存点；本轮消除了重复锁读，对真实请求尾延迟的收益取决于门客/药品数量和数据库往返。
- 世界聊天和在线统计的鉴权拒绝会由 HTTP 403 改为握手 101 后以 4401/4403 关闭，因此不能只看 403 数量评价改善。应同时观察连接尝试总量、终止码、稳定连接与重试间隔。持续不稳定期间重连等待可增长到原策略的 15 秒上限。
- 本轮未写入生产环境。上线后应比较人口任务耗时、疗伤 P95、WebSocket 尝试总量，并核对库存与人数上限相关错误。
