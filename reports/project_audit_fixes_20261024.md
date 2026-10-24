# 审查问题修复交付记录（2026-10-24）

## 范围

针对 `project_audit_20261024.md` 的 A01–A12 实施修复，另完成后台登录共享限流、类型检查错误、四处测试格式问题及擂台文档纠正。保留任务开始前的业务、数值、图片和前端改动。未提交代码或部署生产服务。

## 修复对应关系

| 编号 | 修复与关键不变量 | 主要位置 |
| --- | --- | --- |
| A01 | 注册/重发页面只返回独立用途的重发凭据；真正激活凭据采用 v2 签名域，仅经邮件交付。旧激活凭据全部失去激活能力，但可请求受限重发。锁内再次核对邮箱，阻止验证过程中邮箱变化后错误激活。 | `accounts/email_verification.py`、`accounts/views.py` |
| A02 | 每个既有校验周期重新加载有效 session，并经认证后端取得当前用户，核验 active、当前/回退 auth hash、有效期和单会话记录；异常保持既有关闭策略。 | `websocket/consumers/session_guard.py` |
| A03 | Django 安全下限提高至 `>=5.2.18,<5.3`，生产和开发锁均更新至 5.2.18。临时环境按已有其他精确版本安装，两个锁生成结果与仓库逐字一致。 | `requirements.txt`、两份 `requirements*.lock.txt` |
| A04 | 兑换前定价继续严格拒绝不可用数据。已提交交易的下一根汇率为可降级展示项，已知基础设施错误返回 `None` 并显示成功及刷新提示；数据库保存点保护调用方外层事务，编程错误不吞掉。 | `trade/services/bank_flows.py`、`trade/views.py` |
| A05 | 发放任何资源前校验普通附件和绑定附件模板；缺失时整封保留，恢复模板后可完整领取。 | `gameplay/services/utils/messages.py` |
| A06 | 成员变动与产出科技变化先按主键锁庄园，再锁帮会及成员。批量变更在帮会锁内复核成员集合，发现未预锁新成员即回滚，不倒序补锁。创建、审批、兑换、职务变更同步收紧锁序，联表锁仅作用实际写入对象。解散后的旧申请不能自动批准。 | `guilds/services/resource_locks.py`、`guild.py`、`member.py`、`technology.py`、`utils.py`、`warehouse.py`、`guild_dispatch.py`、`hero_pool.py`、`guild_troops.py` |
| A07 | 首个短事务准备双方 Combatant、技能、兵种、科技、规则和随机上下文；事务外纯内存模拟；第二短事务复核 token、版本、任期、期限、冷却及次数后一次性保存战报、比赛和结果。模拟阶段禁止奖励、伤害、消息和真实门客状态变更，不产生提前提交的孤儿战报。普通战斗默认行为保持原接口。 | `gameplay/services/arena/ring.py`、`battle/execution.py`、`battle/services.py` |
| A08 | 普通历史流水按小批次独立删除，默认每表最多240批、5秒预算，并记录预算耗尽时最老过期时间；擂台历史也在预算内逐批追赶。新增全局时间清理索引。 | `gameplay/tasks/maintenance.py`、`config/settings/base.py`、相关模型与迁移 |
| A09 | 到期打工以 `(complete_at,id)` 游标有界扫描，默认每批100、每调用最多检查1000个候选，跳过锁竞争项也计入总量。扫描、刷新、召回、领奖统一庄园→门客→任务锁序，支持时对庄园使用 SKIP LOCKED；锁内复核任务，保护召回重派。新增到期扫描索引。 | `gameplay/services/work.py`、`gameplay/models/progression.py` |
| A10 | 消息列表、消息跳转及擂台比赛列表去掉完整战报 JOIN，继续使用 `battle_report_id`，保留所需其他预加载。 | `gameplay/services/utils/messages.py`、`gameplay/views/messages.py`、`gameplay/selectors/arena/rings.py` |
| A11 | 同 buyer/tenure/version 复用最早有效侦察快照；旧快照不改写。终止任期保留24小时，按表先清侦察、凭据、比赛，再删无引用任期；保留当前任期、有效凭据，Daily保留15天。旧请求在凭据清理后仍因单调版本/任期检查被拒绝。 | `gameplay/services/arena/ring.py`、`ring_retention.py`、维护任务 |
| A12 | 所有报价在分支处理前统一校验起拍价，包含多名额未满和已有报价者；无效报价不会创建竞拍或冻结记录。 | `trade/services/auction/bidding.py` |

后台登录新增 `ThrottledAdminAuthenticationForm`，复用现有账号/IP失败计数和锁，保留 Django 的 staff/active、CSRF、never_cache、匿名登录标记、安全 next 与可配置表单；成功只清账号桶，保留IP攻击信号。

## 验证

验证使用 `/tmp/web_game_v5_fix_env/bin/python`，Python 3.12.7、Django 5.2.18、redis 5.2.1、asgiref 3.12.1。运行时和开发依赖锁均通过现有锁生成器逐字复核，`pip check` 通过。临时环境未覆盖全局 Python。

已完成真实服务验证：

- 新增及相关 MySQL 并发回归15项通过：帮会捐献与退帮/踢人/解散/产出科技升级/重置的竞态，出征不隐式锁庄园/用户/模板，擂台同凭据/换阵/侦察竞态，以及打工扫描/忙庄园/召回重派。
- MySQL 保留清理、吞吐预算和维护任务20项通过，验证 JSON 条件、OuterRef、删除及相关索引迁移。测试夹具已兼容 MySQL 不回填 bulk_create 主键的行为。
- 最终战斗冻结实现落地后，擂台与既有帮会、拍卖、邮件领取并发回归12项通过；其中3项擂台与前述15项重叠，合计44项不同真实服务测试通过。

真实服务只复用本机既有健康 MySQL 8.4.10 / Redis 容器，使用独立 `test_webgame_audit_fixes_20261024` 测试库和 Redis DB12–15，不访问实际游戏数据库，不重启服务。MySQL 测试不是线上压测，不能据此承诺特定吞吐和延迟。

隔离回归与源码门禁：

| 检查 | 最终结果 |
| --- | --- |
| 业务、锁、维护、擂台冻结、普通战斗及工程配置隔离回归 | 636 passed，12个真实服务用例被标记排除并在MySQL组验证；130.57秒 |
| 账号、邮箱、WebSocket与后台限流 | 153 passed；45.78秒，包含旧凭据恢复、锁内邮箱复核和匿名后台登录中间件契约 |
| JavaScript单元测试 | 40 passed |
| JavaScript语法、ESLint | 通过 |
| 玩家前端模板契约 | 122个模板通过 |
| mypy | 838个生产源文件全部通过 |
| flake8 | 应用、公共库、任务、测试、脚本及battle_debugger通过 |
| Black / isort | 上述源码目录检查通过，1844个Python文件无需格式调整；生成的reports发布产物不纳入源码格式范围 |
| Django system check | 通过 |
| makemigrations --check --dry-run | No changes detected |
| git diff --check | 按仓库CRLF规则通过 |

核心复现与回归文件：`tests/test_email_verification.py`、`test_websocket_session_guard.py`、`test_admin_login_throttle.py`、`test_guild_resource_locking.py`、`test_guild_dispatch_locking.py`、`test_work_completion_batches.py`、`test_audit_retention_capacity.py`、`test_arena_ring_commit.py`、`test_arena_ring_retention.py`、`test_arena_ring_concurrency_integration.py`，以及现有交易、邮件、帮会、打工、战斗和配置回归。

最终两组隔离Python回归合计789项通过；真实服务44项按不同测试去重统计，不把擂台重复执行的3项再次累加。表格仅列收尾稳定版本的结果。

真实服务复现命令（须先配置独立测试数据库和Redis，禁止指向业务库）：

```bash
python -m pytest -m integration \
  tests/test_guild_resource_locking.py \
  tests/test_guild_dispatch_locking.py \
  tests/test_arena_ring_concurrency_integration.py \
  tests/test_work_completion_batches.py \
  tests/test_work_service_concurrency.py --reuse-db -q --timeout=900

python -m pytest tests/test_arena_ring_retention.py \
  tests/test_audit_retention_capacity.py tests/test_maintenance_tasks.py \
  --reuse-db -q --timeout=900
```

最终测试日志：`/tmp/web_game_v5_fixes_regression_final.log`、`/tmp/web_game_v5_fixes_mysql.log`、`/tmp/web_game_v5_fixes_mysql_retention.log`、`/tmp/web_game_v5_fixes_mysql_final.log`、`/tmp/web_game_v5_fixes_security_final.log`；临时文件用于本次验证，不作为长期证据存储承诺。

## 迁移与上线影响

新增两份索引迁移：

- `battle/migrations/0015_audit_capacity_indexes.py`：`BattleReport(created_at,id)`。
- `gameplay/migrations/0243_audit_capacity_indexes.py`：`ResourceEvent(created_at,id)`、`WorkAssignment(status,complete_at,id)`。

迁移已在独立 SQLite/MySQL 测试数据库执行。生产尚未应用；部署应按现有迁移流程执行，索引构建时间和资源占用取决于实际表规模，不能把本地测试时长当作生产估算。

旧邮件激活链接需要点击重发以获得新的 v2 链接；该兼容变化用于关闭此前已泄露凭据的利用窗口，不影响已经激活的账号。旧 outbox 已发送或尚待发送的链接都可走受限重发恢复。

WebSocket 撤销在既有5秒缓存后的下一次消息校验生效。客户端闲置期间不主动触发额外轮询；通知发送和客户端请求都经过守卫。

成员集合在批量资源变更准备期间发生新增时，请求会安全回滚并提示刷新重试。日志与打工有明确预算，极端积压可能需要多个调度周期追平，应结合积压告警调节配置。清理索引和有界批次改善的是数据库工作量边界，尚未完成生产数据压测。

## 设计边界

维持现有 service 写入口与 selector 只读边界，库存与资源继续走集中服务。修复以局部行为纠正为主；锁序、擂台快照计算属于必要的局部结构调整，未进行整体架构迁移。

新增测试针对失效凭据、提交后故障、附件丢失、真实并发、快照冻结和预算行为，不以单纯复制实现断言替代行为验证。没有运行全仓所有测试，也没有执行线上渗透、生产 Celery 故障演练或生产容量测试。
