# 项目重构优化规则与阶段目标（2026-03）

最近更新：2026-05-06

本文档不记录详细审计过程、历史数据或阶段性结果，只保留后续重构必须遵守的规则，以及各阶段的优化目标。

相关文档：

- [架构设计](architecture.md)
- [开发指南](development.md)
- [优化计划](optimization_plan.md)
- [数据流边界](domain_boundaries.md)
- [第二阶段统一写模型基线](write_model_boundaries.md)

## 0. 当前基线（2026-05-06）

本文档只保留当前仍生效的治理结论、门禁基线与未收口项摘要，不再记录已封板阶段的实施流水。

- 默认门禁基线：
  - 2026-03-23 `make lint` 通过。
  - 2026-03-23 默认 `make test` 通过，结果为 `2350 passed, 38 deselected`。
  - 2026-03-23 关键 real-services 并发回归通过：`tests/test_mission_concurrency_integration.py` 与 `tests/test_guest_recruitment_concurrency_integration.py` 合计 `6 passed, 1 skipped`。
  - 2026-03-26 `python -m flake8 --jobs=1 accounts battle gameplay guests guilds trade core websocket config tests` 通过。
  - 2026-03-26 `python -m pytest -q -m "not integration"` 通过，结果为 `2454 passed, 38 deselected`。
  - 2026-04-04 `npm run test:js`、`python -m flake8 --jobs=1 accounts battle gameplay guests guilds trade core websocket config tests`、`python -m mypy accounts battle common config core gameplay guests guilds tasks trade websocket` 与 `python -m pytest -x -q -m "not integration"` 通过；结果分别为 `10 passed`、`flake8 通过`、`533 source files mypy 通过` 与 `2669 passed, 40 deselected`。
  - 2026-04-06 `npm run test:js`、`make lint` 与 `python -m pytest -q -m "not integration"` 通过；结果分别为 `12 passed`、`flake8 + mypy（551 source files）通过` 与 `2796 passed, 41 deselected`。
  - 2026-04-06 `python -m pytest -q` 通过，结果为 `2782 passed, 41 skipped`。
  - 2026-04-07 `npm run test:js`、`make lint` 与 `python -m pytest -q` 通过；结果分别为 `12 passed`、`flake8 + mypy（553 source files）通过` 与 `2853 passed, 41 skipped`。
  - 2026-04-12 `python -m pytest -q tests/test_type_gate_configuration.py` 与 `make lint` 通过；结果分别为 `1 passed` 与 `flake8 + mypy（562 source files）通过`。
  - 2026-05-02 `make lint` 通过；`make test` 通过；`python -m pytest tests/test_deployment_configuration.py tests/test_technical_audit_baseline.py tests/test_gameplay_services_lazy_exports.py tests/test_mission_sync_report.py -q` 通过；结果分别为 `flake8 + mypy（563 source files）通过`、`2969 passed, 44 deselected` 与 `35 passed`。
  - 2026-05-06 `make lint` 通过；`python -m pytest -m "not integration" -q` 通过；`python -m pytest -q tests/test_real_service_preflight.py tests/test_type_gate_configuration.py tests/test_guild_mission_views.py tests/test_technical_audit_baseline.py tests/test_guild_pvp_views.py tests/test_runtime_refresh_views.py tests/test_guild_hero_pool.py tests/test_guild_hero_pool_views.py tests/test_deployment_configuration.py tests/test_pytest_configuration.py tests/test_reload_runtime_configs_command.py` 通过；结果分别为 `JS gate + flake8 + mypy（563 source files）通过`、`2992 passed, 44 deselected` 与 `97 passed`。同日 `python scripts/check_env_services_ready.py` 在当前环境仍报告 MySQL socket 不可用、Redis socket 受限；预检提示已明确指向 `make test-real-services-up`、`DJANGO_TEST_USE_ENV_SERVICES=1 make test-real-services` 与 `make test-real-services-down`。
- 当前已封板阶段：
  - 阶段 1 已完成：热点页面入口、读写边界与包级聚合导入治理已收口。
  - 阶段 2 已完成：`mission / raid / guest recruitment` 的统一写模型、显式 refresh 边界与关键 real-services gate 已建立。
  - 阶段 3 已完成：高频主链路异常语义、`broad except Exception` 与宽泛 `ignore_errors = true` 的治理目标已封板，后续仅按新增触点常规补强。
  - 阶段 4 已完成：审计范围内页面脚本已迁出模板，模板内联脚本/事件扫描结果已清零。
- 最近一次边界复核结论：
  - `config/urls.py`、`gameplay/context_processors.py`、`gameplay/views/arena.py`、`guests/urls.py`、`guilds/urls.py` 已改为显式子模块导入，不再依赖热点包根聚合入口。
  - `gameplay/views/__init__.py`、`gameplay/selectors/__init__.py`、`guests/views/__init__.py`、`guilds/views/__init__.py` 已收口为无副作用最小包标记文件。
  - `2026-04-06` 已继续收口页面倒计时的显式 refresh 边界：建筑、科技、生产、打工、护院募兵、招募大厅与竞技场页面的倒计时组件都已改为命中显式 `POST` refresh API，不再依赖整页 `GET` reload 暗中完成状态收口；对应默认门禁 `python -m pytest -q` 已通过，结果为 `2782 passed, 41 skipped`。
  - `2026-04-06` 已继续补第二阶段写路径 hardening：`guilds/services/guild_raids.py`、`guilds/services/guild_missions.py` 与 `gameplay/services/missions_impl/finalize_command.py` 已把帮会 PVP、帮会任务和个人任务完成后的站内信/战报消息统一改为 `transaction.on_commit(...)` after-commit 发送，不再在事务内混入 DB-backed 消息副作用。后续复核已重新收口帮会 PVP 页首屏 `GET`：`guilds/views/pvp.py` 不再显式命中 `prepare_guild_pvp_read_state()`，到点运行态推进保留在显式 `POST` refresh API 中，避免读请求隐藏写副作用。对应回归覆盖 `tests/test_guild_pvp_views.py::test_guild_pvp_page_does_not_process_due_runs_on_get`、`tests/guild_pvp_service/start_flow.py::test_start_guild_raid_defers_warning_messages_until_after_commit`、`tests/guild_pvp_service/battle_flow.py::test_process_guild_raid_battle_defers_report_messages_until_after_commit`、`tests/guild_mission_service/finalize_flow.py::test_finalize_guild_mission_defers_report_messages_until_after_commit` 与 `tests/mission_refresh_async/report_notifications.py::test_finalize_mission_run_defers_report_message_until_after_commit`。
  - `2026-04-02` 已继续推进 arena 边界收口：`gameplay/services/arena/__init__.py` 与 `gameplay/selectors/arena/__init__.py` 已都收口为无副作用最小包标记；`gameplay/views/arena.py` 已改为显式依赖 `gameplay.selectors.arena.registration / events / details` 与 `gameplay.services.arena.core / coop_core`，不再通过 arena 包根聚合入口取页面查询或 service 常量；原 `gameplay/selectors/arena.py` 485 行热点已拆为 `gameplay/selectors/arena/common.py`（154 行）、`gameplay/selectors/arena/registration.py`（99 行）、`gameplay/selectors/arena/events.py`（57 行）与 `gameplay/selectors/arena/details.py`（151 行），且 `python -m pytest tests/test_arena_audit_boundaries.py tests/arena_services/coop_registration.py tests/arena_services/coop_resolution.py tests/test_arena_views.py tests/test_arena_tasks.py tests/test_battle_report_view.py tests/test_arena_coop_battle_mechanics.py tests/test_load_guest_templates_command.py tests/test_battle_attack_metadata.py tests/test_battle_guest_display_names.py -q` 通过，结果为 `69 passed`；`node --test static/js/tests/nav_partial.test.js` 通过，结果为 `1 passed`。
  - `2026-04-03` 已补 arena coop / passive 战斗链路回归与模板复杂度收口：`battle/simulation/battle_flow.py` 中 `battle_start / round_start / action_before / action_end` 的被动事件已改为统一经过事件追加入口补 `order`，不再把裸 `passive` 事件直接写进回合日志；`battle/status_manager.py` 已在通用 `prepare_combatants_for_round()` 中清空回合级 `battle_modifiers`，避免被动倍率跨回合残留；`tests/test_battle_passives.py` 已收口为兼容入口，并拆到 `tests/battle_passives/core_cases.py`（356 行）与 `tests/battle_passives/attack_flow_cases.py`（356 行）；`tests/test_arena_coop_battle_mechanics.py` 也已收口为兼容入口，并拆到 `tests/arena_coop_battle_mechanics/state_passives.py`（425 行）与 `tests/arena_coop_battle_mechanics/simulation_cases.py`（407 行）；`battle_debugger/templates/battle_debugger/result_detail.html` 已将页面样式与事件列表拆到 `battle_debugger/templates/battle_debugger/partials/result_detail_styles.html`（498 行）与 `battle_debugger/templates/battle_debugger/partials/event_list.html`（73 行），主模板降到 159 行；`battle/templates/battle/report_detail.html` 也已将页面样式拆到 `battle/templates/battle/partials/report_detail_styles.html`（173 行），主模板降到 337 行。验证结果：`python -m pytest tests/test_battle_passives.py tests/test_battle_report_view.py tests/test_battle_debugger_contracts.py tests/test_arena_coop_battle_mechanics.py -q` 通过，结果为 `41 passed`；`python -m pytest tests/test_battle_passives.py tests/test_battle_report_view.py tests/test_battle_debugger_contracts.py tests/test_arena_views.py tests/arena_services/coop_registration.py tests/arena_services/coop_resolution.py tests/test_arena_coop_battle_mechanics.py tests/test_load_guest_templates_command.py -q` 通过，结果为 `91 passed`。
  - `2026-03-25` 已启动复杂度热点首刀整改：`gameplay/views/jail.py` 中的“锁包装与异常/响应映射”及“监牢/结义林状态载荷拼装”已分别下沉到 `gameplay/views/jail_action_support.py` 与 `gameplay/views/jail_payloads.py`；主文件体量已由 `514` 行降到 `368` 行，且 `python -m flake8 gameplay/views/jail.py gameplay/views/jail_action_support.py gameplay/views/jail_payloads.py`、`python -m mypy gameplay/views/jail.py gameplay/views/jail_action_support.py gameplay/views/jail_payloads.py` 与 `python -m pytest tests/test_jail_views.py tests/test_jail_service.py -q` 均通过。
  - `2026-03-25` 已推进复杂度热点第二刀：`trade/selector_builders.py` 中的钱庄/兵库上下文构建已按业务域拆到 `trade/bank_context_builder.py`，`trade/selectors.py` 也已改为显式依赖该子模块；`trade/selector_builders.py` 主文件已由 `437` 行降到 `331` 行，且 `python -m flake8 trade/selector_builders.py trade/bank_context_builder.py trade/selectors.py`、`python -m mypy trade/selector_builders.py trade/bank_context_builder.py trade/selectors.py` 与 `python -m pytest tests/test_trade_selectors.py tests/trade/test_trade_page_view.py -q` 均通过。
  - `2026-03-25` 已推进复杂度热点第三刀：`gameplay/services/manor/core.py` 中的“庄园初始化/补建/坐标分配”与“庄园命名规则/改名事务”已分别按稳定职责拆到 `gameplay/services/manor/bootstrap.py` 与 `gameplay/services/manor/naming.py`，同时保留 `core.py` 作为兼容公开入口；`gameplay/services/manor/core.py` 主文件已由 `622` 行降到 `362` 行，且 `python -m flake8 gameplay/services/manor/core.py gameplay/services/manor/bootstrap.py gameplay/services/manor/naming.py`、`python -m mypy gameplay/services/manor/core.py gameplay/services/manor/bootstrap.py gameplay/services/manor/naming.py` 与 `python -m pytest tests/gameplay_services/manor_bootstrap.py tests/test_manor_naming.py tests/gameplay/manor_refresh.py tests/test_upgrade_concurrency_limits.py -q` 均通过。
  - `2026-03-25` 已启动新一轮模板复杂度治理：`guests/templates/guests/detail.html` 中的页面级样式已迁移到 `static/css/guest-detail.css`，装备区与详情弹窗已拆到 `guests/templates/guests/partials/detail_*.html`；详情页主模板已由 `952` 行降到 `110` 行，且 `python -m pytest tests/test_guest_runtime_refresh_views.py tests/test_guest_allocate_points_view.py tests/test_guest_item_view_validation.py tests/test_guest_view_error_boundaries.py -q` 通过，结果为 `93 passed`。
  - `2026-03-25` 已继续推进模板复杂度治理第二刀：`guests/templates/guests/roster.html` 中的页面级样式已迁移到 `static/css/guest-roster.css`，名册表格主体与经验/药品/工资弹窗已拆到 `guests/templates/guests/partials/roster_*.html`；名册页主模板已由 `520` 行降到 `43` 行，且 `python -m pytest tests/test_guest_runtime_refresh_views.py tests/test_salary_views.py tests/test_guest_view_error_boundaries.py tests/test_inventory_views.py -q` 通过，结果为 `97 passed`。
  - `2026-04-04` 已继续收口帮会写路径并发边界：`guilds/services/contribution.py` 已把金条日限切回 `GuildMember.daily_donation_gold_bar` 的成员锁内权威状态，`guilds/views/contribution.py` 也已同步退出页面侧按 `GuildDonationLog` 聚合推断今日金条捐赠量的旧口径；`guilds/services/warehouse.py` 中普通仓库兑换路径的共享锁顺序也已收口为 `Manor -> GuildMember -> GuildWarehouse`，与帮会捐赠不再形成 `Manor/GuildMember` 交叉等待窗口。对应聚焦回归 `pytest tests/guilds/test_contribution_upgrade.py tests/test_guild_warehouse_service.py tests/test_guild_resource_views.py -q` 通过，结果为 `29 passed`；默认门禁复跑后 `python -m pytest -x -q -m "not integration"` 结果更新为 `2668 passed, 38 deselected`。
  - `2026-04-04` 已继续收口运行期招募稀有度刷新：`gameplay/services/runtime_configs.py` 已接入 `guests.utils.recruitment_utils.refresh_recruitment_rarity_constants()`，不再只刷新成长规则而遗漏招募稀有度权重的 `lru_cache` 与模块级导出常量；`reload_runtime_configs()` 现在会同步刷新 `TOTAL_WEIGHT / RARITY_WEIGHTS / BLACK_WEIGHT / RARITY_DISTRIBUTION`，避免运行期切换 `recruitment_rarity_weights.yaml` 后招募概率仍停留在旧值。对应回归 `pytest tests/test_reload_runtime_configs_command.py -q` 与 `pytest tests/guest_recruitment_service/template_selection.py tests/guests/recruitment_flow.py -q` 通过，结果分别为 `10 passed` 与 `27 passed`；默认门禁复跑后结果更新为 `2669 passed, 38 deselected`。
  - `2026-04-04` 已继续补帮会写路径的 real-services 并发覆盖：新增 `tests/test_guild_concurrency_integration.py`，在真 MySQL `select_for_update` 语义下约束“金条日限并发请求只能成功一次”与“普通仓库道具并发兑换不能超卖”；对应验证 `DJANGO_TEST_USE_ENV_SERVICES=1 pytest tests/test_guild_concurrency_integration.py -q` 通过，结果为 `2 passed in 51.15s`。由于新增了两条 integration 用例，默认 `python -m pytest -x -q -m "not integration"` 的 deselected 计数也同步更新为 `40`。这轮没有继续扩大生产改动范围，而是把前面已收口的 `guild` 并发边界锁进真实环境回归。
  - `2026-04-12` 已继续补 arena coop 的 real-services 并发门禁接线：新增 `tests/test_arena_coop_concurrency_integration.py`，在真 `select_for_update` 语义下约束“同一庄园对 `register_arena_coop_entry()` 的并发报名只能成功一次，失败方必须命中已有报名保护”；同时 `Makefile` 的 `CRITICAL_INTEGRATION_TESTS` 已把该文件纳入固定 critical gate，后续 `make test-critical` / `make test-real-services` 不再遗漏 arena coop 的核心并发写路径。当前工作区已完成 `tests/test_pytest_configuration.py` 的 gate 配置回归与新测试文件语法编译验证，`make lint` 也已通过；受当前环境未启用 `DJANGO_TEST_USE_ENV_SERVICES=1` 限制，这条新 integration 用例尚未在本地真服务环境执行，后续需在真实 gate 上补跑。
  - `2026-04-12` 已继续补 trade auction 的 real-services 并发门禁接线：新增 `tests/test_trade_auction_concurrency_integration.py`，在真 cache lock + 数据库锁语义下约束“同一拍卖轮次的并发 `settle_auction_round()` 只能有一个线程真正完成售卖、扣金条与发货，另一线程必须成为无害 no-op”；同时 `Makefile` 的 `CRITICAL_INTEGRATION_TESTS` 已把该文件纳入固定 critical gate，后续 `make test-critical` / `make test-real-services` 也会覆盖 trade auction 的核心并发结算路径。当前工作区已完成 `tests/test_pytest_configuration.py` 的 gate 配置回归、新测试文件语法编译与 `make lint` 验证；受当前环境未启用 `DJANGO_TEST_USE_ENV_SERVICES=1` 限制，这条新 integration 用例尚未在本地真服务环境执行，后续需在真实 gate 上补跑。
  - `2026-04-12` 已继续收紧类型门禁：`pyproject.toml` 的 `disallow_untyped_defs = true` 严格名单已把 `gameplay.services.arena.coop_battle`、`gameplay.services.arena.coop_core`、`gameplay.services.arena.coop_lifecycle`、`gameplay.services.arena.coop_settlement`、`trade.services.auction.rounds`、`trade.services.auction.rounds_delivery_support`、`trade.services.auction.rounds_lifecycle_support` 与 `trade.services.auction.rounds_settlement_support` 纳入固定约束，并新增 `tests/test_type_gate_configuration.py` 防止这些模块后续悄悄退出严格名单。为满足新门禁，`trade/services/auction/rounds.py`、`trade/services/auction/rounds_settlement_support.py` 与 `gameplay/services/arena/coop_settlement.py` 已补齐缺失的参数/闭包注解，未改变业务语义。对应验证：`python -m pytest -q tests/test_type_gate_configuration.py` 通过，结果为 `1 passed`；`python -m mypy gameplay/services/arena/coop_core.py gameplay/services/arena/coop_battle.py gameplay/services/arena/coop_lifecycle.py gameplay/services/arena/coop_settlement.py trade/services/auction/rounds.py trade/services/auction/rounds_lifecycle_support.py trade/services/auction/rounds_settlement_support.py trade/services/auction/rounds_delivery_support.py` 通过，结果为 `Success: no issues found in 8 source files`；`make lint` 通过，结果为 `flake8 + mypy（562 source files）通过`。
  - `2026-04-12` 已继续收口 real-services gate 的环境可执行性风险：新增 `scripts/check_env_services_ready.py`，并把 `Makefile` 的 `test-critical`、`test-integration` 与 `test-real-services` 接入统一预检；在进入 `pytest` 之前会先探测 MySQL 与 Redis 是否可达，避免当前环境缺少外部服务时直接掉进大段 Django/MySQL 栈追踪。对应验证：`python -m pytest -q tests/test_real_service_preflight.py tests/test_pytest_configuration.py tests/test_type_gate_configuration.py` 通过，结果为 `7 passed`；`make lint` 通过，结果为 `flake8 + mypy（562 source files）通过`。在当前工作区内，`python scripts/check_env_services_ready.py` 会明确报告 “MySQL local socket 不可用、Redis 127.0.0.1:6379 不可达”，因此剩余风险已从“门禁失败原因不透明”收口为“本机外部服务未启动”这一单一前置条件。
  - `2026-04-04` 已继续推进阶段 5 的超大测试文件收口：`tests/test_guilds_tasks.py` 已收口为兼容入口，并按“mission 调度 / 生产补偿与 failed-id cache / 周维护与清理任务”拆到 `tests/guilds_tasks/` 子模块；兼容入口 `python -m pytest tests/test_guilds_tasks.py -q` 通过，结果为 `24 passed`，且新子模块 `mission_tasks.py`、`production_tasks.py`、`maintenance_tasks.py` 与 `support.py` 体量分别为 `150 / 302 / 170 / 82` 行，原 `690` 行热点已退出默认测试复杂度预算。
  - `2026-04-04` 已继续推进帮会 PVP 边界与复杂度治理：`guilds/services/guild_dispatch.py` 当前统一承接出征门客解析、成员锁与阵容行加载，`guilds/services/guild_pvp_queries.py` 则把帮会 PVP 页面的上下文收口为纯读查询；`guilds/services/guild_raids.py` 已退出页面查询拼装和 `guild_missions` 私有实现依赖，改为显式复用共享 dispatch contract、`guild_troops.build_guild_defender_setup()` 与 `guild_raid_loot.grant_guild_raid_battle_rewards()`，同时把读侧补偿入口显式收口到 `prepare_guild_pvp_read_state()`，并纳入“本帮会作为防守方的已到点来袭”补偿处理；`guilds/views/pvp.py` 也已改为在单次请求内复用同一个 `now` 快照，避免补偿与 page context 查询各自取时导致的跨日显示漂移。`guilds/services/guild_raid_rules.py` 也已把跨天攻防次数的归一化收口到读侧规则函数，页面 GET 不再顺手把目标帮会日计数写回数据库；对应回归 `tests/test_guild_pvp_views.py` 会同时约束“页面显示恢复到 `0/2`、`0/3`”与“数据库旧计数保持不变直到真实写路径重置”，`tests/test_guild_pvp_service.py` 也已新增约束“`now` 参数必须贯穿计数投影”和“防守方读侧补偿必须处理已到点来袭”。这轮不是靠空转包装层转移复杂度，`guilds/services/guild_raids.py` 主文件已回落到 `397` 行。对应验证：`pytest -q tests/test_guild_pvp_service.py tests/test_guild_pvp_views.py tests/test_guild_pvp_tasks.py tests/test_guild_mission_service.py` 通过，结果为 `30 passed in 18.82s`；`python -m flake8 --jobs=1 guilds/services/guild_pvp_queries.py guilds/services/guild_raids.py guilds/views/pvp.py tests/test_guild_pvp_service.py` 通过；`DJANGO_DEBUG=1 DJANGO_SECRET_KEY=test-secret-for-local-mypy python -m mypy --cache-dir=/tmp/mypy-guild-pvp-audit guilds/services/guild_pvp_queries.py guilds/services/guild_raids.py guilds/views/pvp.py tests/test_guild_pvp_service.py` 通过，结果为 `Success: no issues found in 4 source files`。同日默认门禁再次复跑时，被既有失败 `tests/test_guild_resource_views.py::test_guild_warehouse_page_projects_guild_resources_without_writing_guild_warehouse` 阻塞，现象为 `projected_entries["grain"]` 触发 `KeyError`；该失败与本轮帮会 PVP 边界治理无关，因此只作为当前未收口风险记录，不据此宣称默认门禁已恢复稳定。
  - `2026-04-05` 已继续收口帮会任务/门客模板/被动联动基线：`guilds/models/missions.py` 与 `guilds/migrations/0013_align_guild_mission_task_types.py` 已把帮会任务类型统一到 `guest / troop / defense` 口径，`guilds/services/guild_missions.py` 与详情弹窗也已改为统一读取 `actual_duration_seconds`，发起任务与页面展示都会遵守 `GAME_TIME_MULTIPLIER`；`data/guild_mission_templates.yaml`、`data/guests/special.yaml` 与 `data/guest_skills.yaml` 已补齐帮会任务专属敌方门客与被动技能，`battle/passive_effects.py` / `battle/arena_coop.py` 也已把伤害倍率与 softcap 收口为按 `skill_key` 追踪的来源模型，支持同名 aura 去重和不同来源叠乘；`guests/management/commands/load_guest_templates.py` 现在会在模板 `base_hp` 变更时同步存量门客 `current_hp / status`，`gameplay/services/battle_snapshots.py` 也对快照里的越界 `current_hp` 做了钳制，避免帮会任务快照因历史脏值直接断言失败；`guests/templates/guests/detail.html`、`static/css/guest-detail.css` 与 `static/js/guest-detail.js` 则已改为复用共享 `tooltip.js`，不再在详情页保留局部悬浮提示实现。对应聚焦验证：`python -m pytest -q tests/battle_passives/core_cases.py tests/arena_coop_battle_mechanics/state_passives.py tests/battle/snapshot_validation.py tests/load_guest_templates_command/import_sync_cases.py tests/load_guest_templates_command/special_payload_cases.py tests/test_guest_runtime_refresh_views.py tests/guest_item_view_validation/gear_views.py tests/test_guild_home_mission_events.py tests/test_guild_mission_schedule.py tests/test_guild_mission_service.py tests/test_guild_mission_views.py tests/test_management_command_validation.py tests/yaml_schema_new_configs/guest_arena_rules.py tests/test_technical_audit_baseline.py` 通过，结果为 `139 passed`；`python -m flake8 --jobs=1 battle guests guilds core gameplay tests` 通过；`python -m mypy battle guests guilds core gameplay` 通过，结果为 `438 source files` 无报错。
  - `2026-03-25` 已继续推进阶段 5 的超大测试文件收口：`tests/test_mission_sync_report.py` 已收口为兼容入口，并按“防守配置校验 / offense 掉落表校验”拆到 `tests/mission_sync_report/` 子模块；兼容入口 `python -m pytest tests/test_mission_sync_report.py -q` 通过，结果为 `17 passed`。
  - `2026-03-25` 已继续推进阶段 5 的第二个测试收口切口：`tests/guest_summon_card/loot_boxes.py` 中的宝箱配置校验测试已拆到 `tests/guest_summon_card/loot_box_config.py`，兼容入口 `tests/test_guest_summon_card.py` 也已补齐新的子模块导入；`tests/guest_summon_card/loot_boxes.py` 已由 `566` 行降到 `250` 行，且 `python -m pytest tests/test_guest_summon_card.py -q` 通过，结果为 `34 passed`。
  - `2026-03-26` 已继续推进前端工程化试点第二刀：`static/js/chat_widget.js` 中仍混杂的窗口布局/拖拽与连接生命周期职责，已分别下沉到 `static/js/chat_widget_layout.js` 与 `static/js/chat_widget_connection.js`；主入口已由 `455` 行降到 `203` 行，且 `node --check static/js/chat_widget_core.js static/js/chat_widget_renderer.js static/js/chat_widget_layout.js static/js/chat_widget_connection.js static/js/chat_widget.js`、`npm run test:js` 与 `python -m pytest tests/test_core_views.py tests/test_context_processors.py -q` 均通过，结果为 `43 passed`。
  - `2026-03-26` 已继续推进“预算上方的多职责入口”整改第四刀：`trade/services/auction/rounds.py` 中的轮次生命周期编排、拍卖位结算/恢复补偿与中标发货/通知，已分别下沉到 `trade/services/auction/rounds_lifecycle_support.py`、`trade/services/auction/rounds_settlement_support.py` 与 `trade/services/auction/rounds_delivery_support.py`；主文件已由 `540` 行降到 `223` 行，同时保留 `create_auction_round()`、`settle_auction_round()`、`_settle_slot()`、`_refund_losing_bids()`、`_mark_slot_unsold_after_failure()`、`_send_winning_notification_vickrey()` 等兼容包装函数名；`python -m flake8 --jobs=1 trade/services/auction/rounds.py trade/services/auction/rounds_lifecycle_support.py trade/services/auction/rounds_settlement_support.py trade/services/auction/rounds_delivery_support.py`、`python -m mypy --cache-dir=/tmp/mypy-auction-rounds-refactor trade/services/auction/rounds.py trade/services/auction/rounds_lifecycle_support.py trade/services/auction/rounds_settlement_support.py trade/services/auction/rounds_delivery_support.py` 与 `python -m pytest tests/test_auction_rounds_cache.py tests/trade_auction_rounds tests/test_trade_auction_rounds.py tests/test_trade_tasks.py -q` 均通过，结果为 `54 passed`。
  - `2026-04-11` 已继续推进“预算上方的多职责入口”整改第五刀：`guests/services/equipment.py` 中混杂的 payload 校验/模板预览、候选装备聚合/背包同步、槽位容量/套装结算职责，已分别下沉到 `guests/services/equipment_payloads.py`、`guests/services/equipment_inventory.py` 与 `guests/services/equipment_stats.py`；原入口文件保留缓存失效 helper、穿戴/卸下写路径与兼容导出，不再继续把 query、preview 与 stat 逻辑堆在同一文件中。主文件已由 `650` 行降到 `221` 行，新子模块体量分别为 `130 / 232 / 59` 行，未引入新的空转包装层；同时保留 `guests.services.equipment._clear_gear_options_cache` 兼容符号，避免现有视图错误边界测试的 monkeypatch 路径失效。对应验证：`python -m pytest -q tests/test_guest_equipment_split_modules.py tests/test_guest_equipment_service_contracts.py guests/tests/test_equipment.py tests/guest_view_error_boundaries/equipment_views.py tests/guest_item_view_validation/gear_views.py` 通过，结果为 `32 passed`；`make lint` 通过，结果为 `flake8 + mypy（559 source files）通过`。
  - `2026-04-12` 已继续推进“预算上方的多职责入口”整改第六刀：`gameplay/services/arena/coop_core.py` 中混杂的报名生命周期、战斗装配、结算发奖与共斗到期运行职责，已分别下沉到 `gameplay/services/arena/coop_lifecycle.py`、`gameplay/services/arena/coop_battle.py` 与 `gameplay/services/arena/coop_settlement.py`；`coop_core.py` 当前只保留运行期常量、`ArenaCoopRegistrationResult`、公共 API 与 `_run_coop_battle_locked` / `create_message` 等兼容符号，不再继续承载大段内部细节。主文件已由 `812` 行降到 `288` 行，新子模块体量分别为 `273 / 135 / 213` 行；同时 `tests/test_infrastructure_side_effect_contracts.py` 也已改为直接约束 `coop_settlement.py` 使用 `schedule_best_effort_after_commit(...)`，避免 after-commit 契约只停留在旧入口文件名上。对应验证：`python -m pytest -q tests/test_arena_coop_split_modules.py tests/arena_services/coop_registration.py tests/arena_services/coop_resolution.py tests/test_arena_tasks.py tests/test_infrastructure_side_effect_contracts.py` 通过，结果为 `27 passed`；`make lint` 通过，结果为 `flake8 + mypy（562 source files）通过`。
- 当前仍需持续关注的项：
  - 阶段 5 仅保留“持续维护”主题：超大测试文件收缩主线已基本完成，但 env-services / 并发集成环境的外部依赖可用性仍会影响真实环境 gate 的稳定性。
  - `2026-03-25` 新一轮复杂度复核中点名的三处热点：`gameplay/services/manor/core.py`、`gameplay/views/jail.py`、`trade/selector_builders.py` 当前都已压回默认 Python 复杂度预算以内；这轮支线可视为完成一轮收口，但后续仍需持续防止公开入口再次堆回多职责。
  - 最新模板复杂度复核显示，`battle_debugger/templates/battle_debugger/custom_config.html` 已将页面级样式迁出到 `static/css/battle-debugger-custom-config.css`，主模板由 `738` 行降到 `386` 行，退出 `500` 行热点阈值；对应契约已补入 `tests/test_battle_debugger_contracts.py::test_custom_config_template_keeps_page_styles_out_of_main_template`。仓库当前最高体量模板变为 `battle_debugger/templates/battle_debugger/partials/result_detail_styles.html`（`500` 行），其次是 `battle_debugger/templates/battle_debugger/tune_result.html`（`492` 行）与 `trade/templates/trade/partials/_market.html`（`485` 行）；这些项目接近阈值但未超过默认模板热点线，后续若继续增长需优先复核。
  - 最新测试复杂度复核显示，仓库内当前最高体量的三份测试文件依次为 `tests/test_virtual_players_service.py`（`3018` 行）、`tests/test_virtual_player_maintenance_v2.py`（`1861` 行）与 `tests/arena_services/test_virtual_reserve.py`（`1704` 行），三者均已超过 `800` 行热点阈值；其余超过 `500` 行的候选应继续按同一动态统计规则复核，不在此固化容易随测试增删而过时的次级排序。`tests/test_guild_mission_views.py`、`tests/test_guild_mission_service.py`、`tests/test_guild_warehouse_service.py`、`tests/test_guilds_tasks.py`、`tests/test_arena_views.py`、`tests/test_battle_report_view.py` 与 `tests/test_load_guest_templates_command.py` 已回落到兼容入口体量，因此“超大测试文件收口”虽已显著收敛，但当前热点名单必须以新基线为准。
  - 后续若继续推进复杂度治理，应优先复核新的超阈值文件是否真的形成认知热点，再按稳定业务职责切分；默认不再把已经回到预算内的模块作为主线持续拆分对象。
  - 默认门禁、真实环境 gate、复杂度预算与文档基线需要在后续每轮改动后持续复核，不再单列历史批次明细。
  - `2026-03-26` 复核确认：阶段 4 “模板内联脚本清零”虽已完成，但前端工程化并未收口；仓库当前虽已具备最小前端测试执行链路（`package.json` 提供 `npm run test:js`，本轮也已把它补进 CI），但仍缺少前端 lint 与更成体系的构建/模块化约束，`static/js` 当前仍约 `5k+` 行手写页面脚本。这一项应作为新的治理主线，而不是继续把“脚本已迁出模板”误判为前端边界已稳定。
  - `2026-07-11` 复核确认：`templates/base.html` 中原有行动力、贡献积分硬编码和 `href="#"` 占位入口已经清理，侧栏状态改由真实上下文驱动；该项已收口，后续只需防止基模板重新吸收页面专属状态。
  - `2026-03-26` 复核确认：热点复杂度已从“超大文件”演进为“预算上方徘徊的多职责文件”问题。本轮已完成 `trade/services/auction/rounds.py`、`websocket/consumers/world_chat.py`、`gameplay/views/map.py`、`gameplay/views/inventory.py`、`guests/services/equipment.py`、`gameplay/services/arena/coop_core.py` 与 `static/js/chat_widget.js` 的一轮收口；后续应继续重点关注新的候选入口，而不是机械反复拆已经回到预算内的模块。
  - `2026-03-26` 复核确认：类型治理仍处于过渡态；全局 `mypy` 仍保持 `disallow_untyped_defs = false`、`ignore_missing_imports = true` 的宽松基线，后续新增热点重构若不顺带收口输入/输出契约与 `Any` 扩散，将继续放大维护成本。
  - `2026-07-11` 复核确认：交易阈值告警已统一由 `trade/view_helpers.py` 承担，`trade/views.py` 只调用共享入口；原重复 helper 问题已收口。

## 1. 重构优化规则

### R1. 先定边界，再做拆分

- 不以“抽 helper / 拆文件数量增加”作为完成标准。
- 拆分前必须先明确 view、selector、service、infrastructure 的职责边界。
- 优先按业务动作、状态流转和补偿职责组织模块，不按工具函数类型切碎文件。
- 如果复杂度只是从一个大文件搬到多个 orchestrator / runtime / handler 中，不算优化完成。

### R2. 先定错误语义，再谈统一异常处理

- 禁止继续把 `ValueError`、`RuntimeError`、裸 `Exception` 混合作为默认跨层语义。
- 必须显式区分业务错误、基础设施错误、程序错误。
- view 层只负责异常映射，不负责猜测异常类别。
- 基础设施异常翻译应收口到适配层，不继续在业务层和页面层扩散。

### R3. 读写职责必须分离

- selector 必须保持只读，不承担状态推进、副作用和补偿扫描。
- 页面读请求如需读侧投影、缓存补偿或状态刷新，必须走统一入口。
- 禁止把“读取前顺手修状态”继续藏在 accessor、context builder 或 selector 内部。
- 写操作必须由明确 service / command 入口承接。

### R4. 基础设施故障策略必须平台统一

- 单会话、缓存、通知、在线状态、任务分发等故障语义，必须统一定义 `fail-open` 或 `fail-closed`。
- 禁止单个业务模块私自决定全局故障口径。
- 没有真实环境验证前，不得把局部收紧直接视为平台封板结论。

### R5. 测试必须约束边界

- 重构不能只补“这次改动能过”的回归测试，必须补边界契约测试。
- 统一异常映射、统一读路径入口、统一降级策略、公开 service 入口都必须有测试约束。
- 默认 `make test`、`make lint` 任一不绿时，优先恢复绿灯，不继续扩散改动范围。
- 默认门禁不绿时，禁止继续功能开发和结构性重构；确需临时绕过时，必须在优化计划中明确风险、范围和回收时间。
- 真实外部服务 gate 需要逐步覆盖并发、缓存、任务派发和通道语义。

### R6. 文档必须先于第二轮大拆分

- 在继续推进热点重构前，必须先固化模块边界、错误策略和基础设施规则。
- 没有文档约束的大拆分，默认视为高风险操作。
- 优化计划必须服从本文档；若冲突，以本文档为准。

### R7. 依赖方向必须显式受控

- 不能只声明职责边界，必须同时约束依赖方向。
- `selector / query / page_context` 禁止依赖 `view`、模板 helper、HTTP 适配层。
- `service` 禁止依赖 `HttpRequest`、`messages`、模板渲染或页面跳转逻辑。
- `context_processor`、middleware、consumer 等系统级入口禁止 import 热点业务包的聚合导出，只能依赖明确子模块。

### R8. 禁止包级聚合导入扩大耦合面

- 热点业务包的 `__init__.py` 不得继续承担全量 re-export 和跨模块聚合导入职责。
- 禁止为了“导入方便”把整个 `views/`、`selectors/`、`services/` 包在 import 时一次性拉起。
- 新增模块若需要对外暴露入口，应通过显式子模块路径导入，不得依赖隐式包初始化副作用。
- 已存在的聚合导入必须逐步拆除；修复循环依赖时优先删除聚合依赖，而不是继续增加延迟导入补丁。

### R9. 模板与前端边界不得继续恶化

- 在后端边界尚未完全稳定前，也禁止继续把新增页面状态机、AJAX 流程和复杂交互堆入模板内联脚本。
- 新增前端交互默认进入 `static/js` 或明确页面脚本模块，不再接受大段 `onclick`、内联事件处理和模板内业务流程编排。
- 基模板只能承载全局必需能力，不得继续吸纳页面专属逻辑。
- 模板拆分必须与页面脚本边界同步推进，避免只拆 HTML 不收口交互状态。
- 不得在基模板或共享导航中继续保留硬编码业务状态、`href="#"` 伪入口或仅用于“以后再做”的占位菜单；未实现能力要么明确下线，要么给出真实状态说明。
- 页面脚本一旦达到“需要本地状态、重连、缓存、拖拽、补偿、序列化”等中等复杂度，不得继续无限堆在单个裸脚本文件中；必须同步规划模块边界、验证策略和脚本加载约束。

### R10. 必须控制复杂度预算，而不是转移复杂度

- 单文件、单模板、单测试文件体量超过团队可维护阈值时，必须拆分并说明新的边界和调用链。
- 拆分验收标准不是文件数量变多，而是入口更清晰、依赖更少、认知负担下降。
- 新增 `helper / runtime / handler / orchestrator` 前，必须先说明其职责边界以及为什么现有入口无法承接。
- 禁止用“兼容层”“转发层”“薄封装”无限叠加目录层级来掩盖热点复杂度。
- 对于未超过热点阈值、但已长期高于默认预算且承担多种职责的文件，也必须纳入治理候选；不能只等文件冲到 `600+` 行才承认有问题。

### R11. 临时兼容方案必须带退出条件

- 临时兼容、降级开关、桥接适配层、回退逻辑必须在文档或计划中写明退出条件。
- 每个临时方案至少要包含：负责人、适用范围、目标收口阶段或版本、删除条件。
- 没有退出条件的“临时方案”，视同新增长期技术债，必须单独登记和追踪。
- 若兼容逻辑已经阻碍依赖收口、异常收口或测试门禁，应优先清理，不得继续叠加外围补丁。

### R12. 审计文档必须维护当前治理基线

- 审计文档可以省略详细过程，但不能省略当前治理基线、主要未收口项和最近一次门禁验证结论。
- 若仓库现实已与文档假设不一致，应优先更新基线，再继续推进下一轮重构。
- 阶段完成声明必须基于已记录的验证结果，不得仅凭主观判断宣布“已收口”或“已稳定”。
- 默认门禁、聚合导入、热点复杂度等高风险主题，必须在文档或配套计划中保留最新状态摘要。

## 2. 阶段目标

### 阶段 1：先稳边界

目标：

- 收口热点页面入口，降低 view 主文件的职责密度。
- 把读侧 page context 与写动作入口分开。
- 清理热路径中的动态 import、callback 空转层和无意义兼容壳。
- 拆除热点包的聚合导入与循环依赖入口。
- 为后续第二阶段固化更清晰的 view / selector / service 边界。

完成标志：

- 热点 view 不再同时承担页面装配、写动作 orchestration、异常包装和跨域协调。
- 读侧上下文构建与写动作处理各有明确入口。
- 默认 `make test` 与 `make lint` 在边界调整后仍持续为绿。
- 热点模块不再从 `gameplay.selectors`、`gameplay.views` 等包根聚合入口导入核心符号。
- 热点业务包的 `__init__.py` 不再承担跨模块 re-export 责任，或已被明确限制为无副作用的最小导出。
- 已记录一次带日期的依赖图/导入链复核结果，确认主要循环依赖入口已收口。

### 阶段 2：再稳并发与测试

目标：

- 为 `mission / raid / guest recruitment` 固化统一写模型。
- 明确主写入口、after-commit follow-up、refresh command、补偿边界。
- 继续把读路径中的补偿职责外迁，禁止新增隐藏副作用 accessor。
- 为高风险写路径补真实外部服务测试，而不只依赖 hermetic 套件。

完成标志：

- 关键链路的锁职责、状态推进、补偿入口都能被清楚说明。
- 页面读请求不再承担隐式补偿职责。
- 真实环境测试开始覆盖关键并发与任务派发语义。

### 阶段 3：收紧门禁

目标：

- 建立显式异常层次，逐步退出 legacy `ValueError` 兼容语义。
- 收缩 broad `except Exception` 与 runtime marker 猜测。
- 继续缩小 mypy 的 `ignore_errors` 范围。
- 重新评估 coverage 盲区，让门禁覆盖高变更入口。
- 为默认测试、覆盖率或热点路径覆盖建立更明确的失败阈值。

完成标志：

- 高风险主链路的异常类型、降级口径和页面映射关系清晰稳定。
- 类型门禁和覆盖率门禁开始对热点路径形成真实约束。
- 默认门禁失败能够阻断问题继续扩散，而不是只在文档中提示。

### 阶段 4：治理模板与前端边界

目标：

- 在后端边界稳定后，集中拆分最大模板和页面脚本。
- 把内联交互、页面状态逻辑和大段样式逐步从模板中抽离。
- 降低基模板承担的全局大杂烩职责。
- 清理历史内联事件和页面级脚本散落问题，建立稳定的脚本归属规则。
- 为中等复杂度页面脚本补最小可执行验证链路，至少能约束关键状态机、序列化和 DOM 协议不回退。
- 清理基模板中的硬编码状态、伪入口和无责任归属的全局 UI 能力。

完成标志：

- 高复杂页面具备稳定 partial / component 边界。
- 前端交互逻辑不再继续散落在模板内联代码中。
- 新增页面默认不再引入大段模板内联 JS。
- 基模板不再展示硬编码业务值，也不再长期保留 `href="#"` 一类未兑现入口。
- `static/js` 中的热点脚本已建立模块边界和最小验证手段，不再完全依赖人工点页面回归。

### 阶段 5：测试与发布质量

目标：

- 拆分超大测试文件，按业务域整理测试资产。
- 建立更清晰的 hermetic / integration 测试边界。
- 为并发、库存、撤退、报名、任务派发等关键路径增加回归测试。
- 保持默认门禁与真实外部服务门禁都可持续运行。

完成标志：

- 测试目录、fixture、builder、integration gate 的结构更稳定。
- 默认测试和真实环境测试各自覆盖的职责清晰可说明。
- 超大测试文件和“只涨不拆”的测试资产开始收缩。

## 2.1 默认复杂度预算

以下阈值作为默认治理基线；若因明确业务原因暂时超出，必须在 ADR、优化计划或对应模块文档中写明豁免原因与回收时间。

- Python 业务代码文件：默认不超过 400 行；超过 600 行视为热点治理对象。
- 模板文件：默认不超过 300 行；超过 500 行视为热点治理对象。
- 测试文件：默认不超过 500 行；超过 800 行视为热点治理对象。
- 单次新增内联脚本：默认不超过 30 行；超过该阈值应迁移到独立脚本模块。
- 新增 `helper / runtime / handler / orchestrator` 文件时，若只是转发现有调用链且未降低依赖复杂度，默认不予接受。

### 阶段 6：运维与长期治理

目标：

- 补齐结构化日志、任务监控、失败告警和运行手册。
- 评估历史 migration、缓存策略、异步任务治理和运维流程。
- 让文档持续跟随真实目录结构与运行语义，而不是滞后于代码。

完成标志：

- 开发、测试、上线、回滚和排障流程具备统一口径。
- 文档、门禁和运行时语义保持一致。

## 3. 当前执行原则

后续每一轮优化都应满足以下要求：

1. 一轮只推进一个可验证主题，不做大爆炸式重构。
2. 每轮改动都要同步补测试和文档。
3. 每轮结束都要说明这轮改动对应了哪些规则、推进了哪个阶段目标。
4. 如果某项改动仍是临时兼容方案，必须写明下一步收口点，以及负责人、目标阶段/版本和删除条件。
5. 每轮结束都要检查是否新增了违反依赖方向的 import、包级聚合导入或模板内联交互。
6. 涉及热点边界的重构验收，除测试外还必须复核依赖图、导入链和关键调用链是否变短、变清晰。
7. 每轮结束都要记录最近一次默认门禁验证日期、执行命令、结果摘要；没有记录则不得声称门禁已恢复稳定。
