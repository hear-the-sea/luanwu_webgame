# 虚拟玩家重构 Gate A 事实档案（2026-07-27）

> 状态：`PASSED`（开发阶段；2026-07-28 修订）
>
> 环境事实：当前是测试环境，项目处于开发阶段；环境类别明确为非生产，不存在生产流量、生产真人账号或生产存量迁移。
>
> 术语说明：本文的“运行时代码/运行时消费者”均指当前测试环境可执行代码；“生产环境”只指未来另行审批的上线环境。
>
> 分段事实：当前 V1 运行配置仍为五档且本次不修改；V2 八档只在 Gate D1 与 Bootstrap V2、强度保护和显式重分段命令
> 原子启用，不代表当前运行时已经切换。
>
> 成长事实：当前 V1 成长配置同样不修改。V2 八档的 Bootstrap 历史年龄在 Gate D1 启用，实时正向成长间隔和单次上限在
> Gate E 启用；当前测试环境尚未因本档案自动消费这些配置。
>
> 本文是 [`virtual_player_refactor_plan_2026-07-27.md`](virtual_player_refactor_plan_2026-07-27.md)
> 阶段 0 的事实快照和审批清单。`PASSED` 只授权 Gate B 的行为等价结构迁移，不授权 schema migration、
> Gate C--E 的运行时启用或任何生产发布。Gate D1 退出后新建 Bot 直接 100% 使用 Bootstrap V2；Gate E readiness
> 通过后必须先完成 cutover 和运行时 V1 清零，Gate E 退出时 Maintenance V2 才直接 100%。Gate D2 仅控制可选的参考
> 分布校准策略。当前非生产环境不执行 engine enrollment 百分比灰度；独立 policy rollout 只在已经是 V2 的档案间迁移
> 不可变策略，不改变 engine 归属，也不构成生产发布授权。未来生产发布必须在上线前另立方案审批。
>
> 可回放证据索引：[`virtual_player_gate_evidence_manifest_2026-07-28.yaml`](virtual_player_gate_evidence_manifest_2026-07-28.yaml)。
> manifest 记录历史证据与 canonical target 的实际执行状态；历史命令不得替代新 target 的结果。

## 1. Gate A 结论

Gate A 的开发阶段证据已闭合，可以开始 Gate B owner 迁移。Gate C 以后的数据结构、Bootstrap 和
Maintenance 在当前测试环境启用前仍需各自 gate 明确批准。

| 交付项 | 当前证据 | 状态 |
|--------|----------|------|
| 公共入口与运行时消费者 | 两个门面的显式 `__all__`、逐消费者 import characterization | 已冻结 |
| `BotProfile` 直接/间接读写清单 | 全运行时 app AST 扫描、写 owner ratchet、反向实例写负例 | 已冻结 |
| Arena demand/member 状态 owner | `ArenaVirtualDemand` 与 `ArenaVirtualReserveMember` DML ratchet | 已冻结，但现有跨 owner 删除属于债务 |
| 竞技场依赖方向 | 三组反向依赖被显式登记，新增边会使门禁失败 | 债务已冻结，尚未清零 |
| 全局锁图 | 已逐路径记录真实锁边、缺锁和四组明确反序 | 分支目标主序已冻结；现有债务待迁移和实测 |
| 第 8.9 节 command matrix | 已核对 owner、事务、副作用和最小提取边界 | 首版移除门客招募链；工资保持 `SalaryPayment` 审计 |
| H-01 Raid 掉落边界 | 掉落裁剪已纯化；退休建议仅在 Raid 提交后处理；三组真实交叉竞态通过 | 选项 A、实现与聚焦并发证据已冻结 |
| Maintenance trigger | 纯契约、完整 outcome × trigger 矩阵及 payload 互斥测试存在 | 规范已可执行，当前 V1 运行路径尚未消费 |
| `MAX_NO_ACTION_LEASE_AGE` | 最长现有补位等待为 8 小时 | 12 小时绝对期限已冻结并在 reserve pool 实现 |
| 新玩家人口触发 | 当前注册只创建 Manor，Bot 依赖每小时滚动任务；现有 roll task 还会先执行 Maintenance | Gate D1 增加按地区/声望段持久合并的 demand；公共真人注册提交后 merge 并投递专用人口任务，消息只加速唤醒；只补实际缺口、不执行 Maintenance，Bot 建号不递归触发，样本不足不得阻止创建 |
| V2 声望分段 | 当前 V1 为 `0/500/2000/8000/30000/+∞` 五档边界，高段过宽 | Gate D1 启用八档：`0/500/2000/8000/30000/60000/120000/240000/+∞`；空高段按需激活，低段供给不得顶替，禁止跨段复活或瞬间拔高 |
| 稀疏样本强度保护 | V1 无样本会使用声望段随机 fallback，维护仍可继续成长 | 四档强度上限已冻结；事件触发创建不得先于安全保护上线 |
| 分段成长节奏 | 当前 V1 没有 V2 八档历史年龄、正向间隔和单次增长 cap | 统一领域动作上叠加八档节奏；Gate D1 验证 Bootstrap 历史投影，Gate E 验证实时节奏、跨段和 Arena/Admin 无旁路 |
| 分布基线 | schema v2 只读一致性快照、分段样本门禁及隔离 fixture 测试存在 | Gate A 契约已完成；代表性分布实测只属于 Gate D2 |
| 自然度、经济、性能阈值 | 保守默认值已写入机器可读验收配置 | 已冻结；Gate D1/Gate D2/Gate E 在当前非生产环境启用各自能力前按对应指标验证 |
| 真实并发 | 已有竞技场补位与退休竞态，并新增 H-01 × maintenance/population/reserve 三组用例 | 聚焦与完整真实服务 critical gate 均通过 |
| 可回放证据 | canonical 命令、精确套件、nodeid count/checksum 与历史结果进入独立 manifest | 新 canonical target 未实际运行时必须保持 `not_run`，不得从历史结果推导通过 |

Gate A 的范围属于重构前边界治理。H-01 本身是 `Surgical Fix`，Gate B 是 `Structural Shift`，
Gate C 之后整体属于 `Architecture Migration`。当前证据不能把这三类授权合并。

## 2. 公共入口与消费者

### 2.1 `gameplay.services.virtual_players`

当前门面冻结 19 个公开符号：

```text
AcceleratedGrowthOutcome
BotProjectionConfig
PopulationMutationResult
PopulationMutationStatus
accelerate_virtual_player_growth
clear_virtual_player_config_cache
create_virtual_player
create_virtual_player_with_capacity
create_virtual_players_for_band
get_virtual_player_capacity
load_virtual_player_config
maintain_due_virtual_players
plan_virtual_player_population
reactivate_retired_virtual_player_with_capacity
reactivate_virtual_player_profile
request_virtual_player_backfill_for_region_search
retire_virtual_player_if_unprotected
roll_virtual_player_population
virtual_player_prestige_bands
```

运行时消费者及其当前导入如下：

| 消费者 | 导入符号 |
|--------|----------|
| `gameplay/management/commands/audit_virtual_player_baseline.py` | `virtual_player_prestige_bands` |
| `gameplay/management/commands/generate_virtual_players.py` | `create_virtual_players_for_band`、`virtual_player_prestige_bands` |
| `gameplay/services/arena/virtual_reserve.py` | `AcceleratedGrowthOutcome`、`BotProjectionConfig`、`PopulationMutationStatus`、加速/创建/容量/重激活/声望段入口 |
| `gameplay/services/runtime_configs.py` | `clear_virtual_player_config_cache`、`load_virtual_player_config` |
| `gameplay/services/virtual_player_loot_limits.py` | `load_virtual_player_config` |
| `gameplay/tasks/virtual_players.py` | `maintain_due_virtual_players`、`plan_virtual_player_population`、`roll_virtual_player_population` |
| `gameplay/views/map.py` | `request_virtual_player_backfill_for_region_search` |

### 2.2 `gameplay.services.arena.virtual_reserve`

当前门面冻结 11 个运行时入口：

```text
create_due_virtual_reserve_profiles
fill_due_coop_reserve
fill_due_tournament_reserve
grow_due_virtual_reserves
queue_virtual_reserve_reconcile
reconcile_coop_demand
reconcile_coop_demand_locked
reconcile_tournament_demand
reconcile_tournament_demand_locked
replenish_virtual_reserve
scan_virtual_reserve_demands
```

| 消费者 | 导入符号 |
|--------|----------|
| `gameplay/services/arena/core.py` | 两类 fill、queue、普通赛 reconcile/locked reconcile、replenish |
| `gameplay/services/arena/coop_core.py` | 共斗 fill、queue、共斗 reconcile/locked reconcile、replenish |
| `gameplay/services/arena/coop_lifecycle.py` | `reconcile_coop_demand_locked` |
| `gameplay/tasks/arena.py` | create/grow、两类 reconcile、replenish、scan |

`ReserveReplenishmentResult`、`ArenaVirtualGrowthTarget`、下划线函数和仅被测试导入的包装不构成运行时公共 API。
兼容期和退场条件登记在 [`compatibility_inventory_2026-03.md`](compatibility_inventory_2026-03.md)。

## 3. `BotProfile` 读写边界

### 3.1 直接 model import

| 文件 | 当前目的 |
|------|----------|
| `gameplay/admin/bots.py` | 只读展示和旧 `mark-stale` Admin 写动作 |
| `gameplay/management/commands/audit_virtual_player_baseline.py` | 只读、确定性基线抽样 |
| `gameplay/management/commands/generate_virtual_players.py` | 只使用 `Archetype` choices |
| `gameplay/services/arena/virtual_backfill.py` | 旧竞技场候选读取 |
| `gameplay/services/arena/virtual_reserve.py` | 旧竞技场读取、租约和参与历史写入 |
| `gameplay/services/raid/utils.py` | 只读攻击资格判断 |
| `gameplay/services/virtual_player_core/maintenance.py` | 类型化生命周期 command 边界 |
| `gameplay/services/virtual_player_core/profile_store.py` | 目标档案写 owner |
| `gameplay/services/virtual_player_loot_limits.py` | 只读掉落额度决策 |
| `gameplay/services/virtual_player_state_policy.py` | 旧状态分类 |
| `gameplay/services/virtual_players.py` | V1 应用服务读取与写入 |

### 3.2 反向关系 reader

以下模块不直接 import `BotProfile`，但通过 `manor.bot_profile` 或 ORM lookup 读取关系：

| 文件 | 当前目的 |
|------|----------|
| `gameplay/management/commands/audit_virtual_player_baseline.py` | 从真人样本排除虚拟档案 |
| `gameplay/selectors/stats.py` | 从真人活跃统计排除虚拟档案 |
| `gameplay/services/raid/map_search.py` | 应用虚拟档案地图可见状态 |
| `gameplay/services/ranking.py` | 从真人排名排除虚拟档案 |
| `gameplay/services/virtual_player_loot_limits.py` | 识别 Raid 虚拟防守方 |
| `gameplay/services/virtual_players.py` | V1 人口、投影、库存和 Raid 查询 |
| `gameplay/views/map.py` | 应用虚拟档案地图可见状态 |
| `guests/tasks.py` | 从真人自动培养排除虚拟档案 |

### 3.3 当前写 owner

Gate A 只允许以下四个文件对 `BotProfile` 执行 DML：

```text
gameplay/admin/bots.py
gameplay/services/arena/virtual_reserve.py
gameplay/services/virtual_player_core/profile_store.py
gameplay/services/virtual_players.py
```

门禁覆盖 manager、QuerySet、实例 `save/delete`、alias、bulk、upsert，以及
`manor.bot_profile.save()` 这类反向实例写入。它是防止边界继续扩大的 AST ratchet，不替代数据库权限、
运行时 SQL 审计或 Gate B 将写入真正收敛到 `profile_store.py` 的工作。

## 4. Arena 状态 owner 与依赖债务

### 4.1 当前状态迁移 owner

| 模型 | 当前 DML owner | 当前职责 | 目标方向 |
|------|----------------|----------|----------|
| `ArenaVirtualDemand` | `arena/virtual_reserve.py` | create/reconcile/version、retry、close/satisfied、fill 状态 | Gate B 迁到 demand owner；赛事 core 只调用 locked primitive |
| `ArenaVirtualReserveMember` | `arena/virtual_reserve.py` | 候选租约、重验、成长、ready/exhausted、fill 完成 | Gate B 分到 pool/fill owner |
| `ArenaVirtualReserveMember` | `arena/coop_core.py` | 取消、回退和清理虚拟 Entry 时直接删除 member | 改为幂等 lease release command |
| `ArenaVirtualReserveMember` | `arena/match_helpers.py` | 无效 snapshot 判负时直接删除 member | 改为幂等 lease release command |

### 4.2 已登记的反向依赖

| 双向依赖 | 当前边 |
|----------|--------|
| 共斗生命周期与后备池 | `coop_lifecycle -> virtual_reserve`；`virtual_reserve -> coop_lifecycle` |
| 普通赛生命周期与后备池 | `core -> virtual_reserve`；`virtual_reserve -> core` |
| Arena task 与后备池 | `tasks.arena -> virtual_reserve`；`virtual_reserve -> tasks.arena` |

`coop_core -> virtual_reserve` 也是运行时依赖，但目前没有对应的 `virtual_reserve -> coop_core` 反向边。
目标结构要求赛事转换 primitive 位于 `lifecycle_helpers.py`，且该模块不导入 reserve；task 派发由上层或
无反向 import 的 adapter 完成。现有门禁的目的只是确保债务不再增长，不能把“债务已登记”视为“依赖已修复”。

## 5. 当前全局锁顺序矩阵

下面记录的是当前代码观察值，不是已批准的全局锁协议。`->` 表示同一事务中较早取得或写入的对象在左侧；
“未锁”表示存在条件更新或直接写入，但没有统一的 `select_for_update` 边界。

| 路径 | 当前事务/锁顺序 | 关键写入与副作用 | Gate E 结论 |
|------|-----------------|------------------|-------------|
| Raid 战斗结算 | 按主键排序的 `Manor` 对 -> `RaidRun` -> 攻方 `Guest` -> 防方 `PlayerTroop` -> 受伤双方 `Guest` -> `Building` -> 再次 `PlayerTroop` -> `InventoryItem` -> 俘获 `Guest` -> `JailPrisoner`；除 Manor 对外，多行对象未全部显式排序 | 战报、兵损、掉落、声望、保护、俘虏、返程；salvage 还会经库存 wrapper 写库存；提交后消息/任务 | 保留 Manor 对确定性顺序并补齐多行稳定排序；退休不得重新嵌入该事务 |
| H-01 退休 command | 独立事务 `BotProfile` -> 只读 Arena member/entry 保护查询 | 退休或推迟 `next_growth_at` | 已批准非持久、at-most-once post-commit；不建 outbox，禁止把写入塞回掉落计算 |
| 定时 V1 维护 | `BotProfile` -> V1 领域写入；Manor/Guest/库存并非全程统一加锁 | 生命周期、资源/工资、成长、调度 | Gate E 必须改为 `BotProfile` + 审计后的领域 primitive 单事务 |
| Arena 加速 | `ArenaVirtualReserveMember` -> `BotProfile` -> V1 领域写入 | 成长后重验 member；当前先改再恢复 `next_growth_at` | 与目标的 profile-first 协议不一致；V2 不得事务外恢复调度 |
| Arena reconcile | `Tournament/Event -> ArenaVirtualDemand -> ArenaVirtualReserveMember` | create/version、关闭、状态重验与 surplus 清理 | 赛事转换与 demand reconcile 必须拆成单向依赖 |
| Arena replenish/create | replenish 整体为 `ArenaVirtualDemand -> 既有 ReserveMember -> BotPopulationControl -> BotProfile`；create 为 `Demand -> PopulationControl -> 新 Profile/Manor -> 新 Member` | 候选重验、租约、重激活或创建 | 与 maintenance/retirement 的 profile-first 路径交叉；Gate E 重审 |
| Arena fill | `Tournament/Event -> ArenaVirtualDemand -> BotProfile -> Entry/Snapshot -> 删除 ReserveMember` | 参与历史、member 消费、demand satisfied、赛事转换 | fill 不得回调 core/coop lifecycle；使用下沉 primitive |
| 地图人口滚动 | Redis ownership token -> 可选 `BotBackfillDemand` -> `BotPopulationControl` -> `BotProfile`；超量退休只锁排序后的 Profile | retire/reactivate/create、人口需求确认 | 当前只有分布式互斥和局部行锁；迁移前冻结确定性顺序和失锁停止语义 |
| Admin `mark-stale` | 单条 QuerySet update，无显式行锁 | `state/next_growth_at/maintenance_stopped_at` | Gate E Admin 必须走显式 trigger 和 profile store command |
| 门客培养 | start 为 `Manor -> Guest -> (粮食时 InventoryItem)`；finalize 只锁 `Guest` | 资源、`TrainingLog`、timer、提交后任务、完成时属性成长并可能继续长期训练 | 提取可注入 RNG 的 actor-neutral 同步 locked primitive；不派发完成任务 |
| 门客招募/候选处置 | start 为 `Manor -> InventoryItem/候选删除 -> GuestRecruitment`；到期 finalize 为 `GuestRecruitment -> Manor -> 候选删除/创建`；候选确认是 `Manor -> RecruitmentCandidate` | 行动力/资源、候选、正式门客或家丁、缓存、timer/通知；正式录用还会启动长期训练 | start/finalize 存在明确反序；先预读 manor_id 并统一 Manor-first，未决定自动训练语义前删除该 V2 首版动作 |
| 装备穿脱 | `Guest -> Gear` 或 `Guest -> InventoryItem -> Gear`；另有道具使用 `InventoryItem -> Guest` 的反向路径，旧装备归库缺统一行锁 | 库存、槽位、属性/套装、HP clamp、旧装备、缓存 | 真人 wrapper 也改为 Manor-first 后再提取多行稳定排序的 locked command |
| 技能学习 | `Guest -> InventoryItem`，未锁 Manor；部分所有权/库位/书本 payload 校验只在 HTTP view | 技能位、技能书消费、`GuestSkill` | 校验下沉到 Manor-first locked command，不可直接复用当前 service |
| 护院招募 | start 为 `Manor -> InventoryItem(多行未排序) -> TroopRecruitment`；finalize 为 `TroopRecruitment -> PlayerTroop`，后者未显式锁 | 当前真实成本只有装备和家丁；timer、最终兵力、通知 | 提取 quote/cost/result locked primitives，并统一 Manor-first；不得臆造银两/粮食成本 |
| 建筑升级 | start 为 `Manor -> Building -> InventoryItem(粮食镜像)`；成本/max 在锁外计算；finalize 无统一事务和行锁 | 资源/声望事件、容量、缓存、timer/通知，存在部分提交窗口 | 锁内重算单级 quote/资格/成本并原子 apply；finalize 不可原样嵌套 |
| 科技升级 | start 为 `Manor -> get_or_create(PlayerTechnology)`，科技行未显式锁；finalize 是无 Manor 锁的条件更新 | 当前真实成本只有银两；等级、声望、缓存、timer/通知；当前 YAML 未见前置科技规则 | 显式 `Manor -> PlayerTechnology`，锁内重算并强制 `max_level`；不得臆造前置规则 |
| 资源生产/扣发 | 外层 `Manor -> InventoryItem(粮食镜像)` | 容量、`ResourceEvent`、生产时间 | 现有 `*_locked` 可复用，但调用方必须先持有 Manor 锁 |
| 工资 | `pay_all_salaries` 事务先锁 `Manor`；Guest 未锁，依靠 `(guest, for_date)` unique 幂等 | `SalaryPayment`、余额整体回滚；当前不写 `ResourceEvent` | 提取要求 Manor 已锁的 primitive；保持 `SalaryPayment`-only，不新增 `ResourceEvent.SALARY` |
| V1 库存补货/额度 | `BotProfile -> InventoryItem -> BotInventoryDailyCounter`，库存多行未稳定排序 | 全局日额度、库存、粮食双写 | 与计划的 Counter-first 目标反序；改为 profile/manor 后先排序预留 Counter，再排序写 Inventory |
| 通用库存增减 | 部分入口仅 `InventoryItem`，粮食分支随后更新 `Manor` | 库存数量和 `Manor.grain` 双写 | 与资源路径形成 `Manor -> InventoryItem` / `InventoryItem -> Manor` 反序风险 |

已证实的反序至少包括：Arena growth 的 `ReserveMember -> BotProfile` 与目标 Maintenance 的 profile-first 协议；
招募 start 的 `Manor -> GuestRecruitment` 与 finalize 的 `GuestRecruitment -> Manor`；V1 库存的
`InventoryItem -> Counter` 与目标额度顺序；通用粮食库存的 `InventoryItem -> Manor` 与资源路径的
`Manor -> InventoryItem`。Admin、Building finalize 和 Technology finalize 还缺完整锁边界。

### 5.1 Gate A 已冻结分支主序（Gate E 必须实测复核）

- Arena 分支固定为 `Tournament|Event -> ArenaDemand -> ReserveMember -> BotPopulationControl -> BotProfile -> Manor`。
- Population 分支固定为 `BackfillDemand -> BotPopulationControl -> BotProfile -> Manor`。
- Maintenance、Retirement 与 Admin 分支固定为 `BotProfile -> Manor`。
- Redis ownership token 是人口写入前的外部所有权前置条件；丢失 token 后停止后续人口写入，但不把它伪装成数据库行锁。
- 不共现的根不强行排成全序，新建行也不伪装成已取得的既有行锁。
- 所有庄园领域 command 先持有 `Manor`；双 Manor 按主键，多 Profile/Member/Inventory/Guest/Gear/Troop
  也按主键稳定排序。Manor 根锁负责串行同庄园子对象，不伪造与当前规则冲突的全子表总序。
- 日额度在已锁 `BotProfile -> Manor` 后，固定 `Counter(category, date) -> InventoryItem(template/id)`。
- 招募队列预读 manor_id 后固定 `Manor -> GuestRecruitment -> RecruitmentCandidate`；异步 finalize 必须改序。
- 建筑/科技固定 `Manor -> Building` 和 `Manor -> PlayerTechnology`；粮食随后进入 Manor 已锁的库存 primitive。
- 装备/技能的真人 wrapper 也先锁 Manor，再进入内部多行稳定排序的 primitive。
- 任何路径取得 `BotProfile` 后都不得反向锁 `ArenaVirtualReserveMember`。Arena 必须沿既定 Member-first 分支前进；
  若实现要求 Profile 成为 Arena 的第一把数据库行锁，就必须先拆事务边界并重新评审主序。

这份分支主序已在 Gate A 获得技术批准。Gate E 负责用真实 MySQL 等待图和交叉并发验证已冻结契约；若实证冲突则
重新打开 Gate A，而不是在编码阶段自行换序。当前代码尚未满足该主序，因此仍不能宣称无冲突的全局锁协议已经实现。

## 6. 第 8.9 节领域 command matrix

| 领域动作 | 当前规则 owner 与边界 | 当前副作用 | Gate E 前最小提取 |
|----------|-----------------------|------------|-------------------|
| 门客培养 | `guests/services/training.py::train_guest/finalize_guest_training`；start 为 Manor/Guest 事务，finalize 只锁 Guest 并可续排长期训练 | 资源、`TrainingLog`、timer、Celery、等级/四维/属性点/HP | `quote_training` + `validate_training_locked` + 可注入 RNG/allocator 的 `apply_training_locked`；Bot 同事务同步完成且不写 timer |
| 门客招募/候选处置 | `recruitment.py`、`recruitment_guests.py`；start/finalize 反序，资格还涉及行动力、日限、容量、身份和 seed | 行动力/资源、候选、Guest/Retainer、缓存、通知；正式 Guest 会启动自动训练 | Maintenance V2 首版移除整个招募链；未来纳入时另行重开语义和锁序评审 |
| 装备穿脱 | `equipment.py`、`equipment_inventory.py`；同步 command 从 Guest 开始，存在 Inventory/Guest 反向路径 | Inventory/Gear、属性、套装、HP clamp、旧装备、缓存 | 真人与 Bot 共用 Manor-first、按主键排序的 resolve/equip/unequip locked primitive；acquisition 另占周期 |
| 技能学习 | `skills.py::learn_guest_skill`；Guest 后锁技能书，关键书本身份校验仍在 view | `GuestSkill(source=BOOK)`、技能书消费 | 将所有权、库位、effect/payload/skill identity 下沉到 Manor-first locked command |
| 护院招募 | `gameplay/services/recruitment/recruitment.py` 与 `lifecycle.py`；start/finalize 为 durable timer，成本是装备和家丁 | `TroopRecruitment` 审计载体、`PlayerTroop`、通知 | quote、validate、consume cost、apply result locked primitives；同步入账但保留成本审计结构 |
| 建筑升级 | `manor/core.py::start_upgrade/finalize_building_upgrade`；quote 在锁外，完成路径无统一事务/行锁 | 资源/声望事件、Building、容量、缓存、任务/通知 | 锁内重算单级 quote/资格/成本并原子 apply result；cache on_commit，不派通知 |
| 科技升级 | `technology.py`、`technology_runtime.py`；科技行未显式锁，完成路径使用条件 update | 银两/声望、PlayerTechnology、缓存、任务/通知 | 显式 Manor/Technology locked quote/validate/apply result；保持当前真实资格并强制 `max_level` |
| 资源生产与工资 | `resources.py` 的 `*_locked` 可复用；`salary.py::pay_all_salaries` 只有外层 Manor 锁与日期唯一幂等 | 资源路径写 `ResourceEvent`；工资只写 `SalaryPayment` | 资源 primitive 不再拆；提取保持 `SalaryPayment` 日期唯一语义的 `pay_all_salaries_locked`，不新增 `ResourceEvent.SALARY` |
| 库存获取 | `inventory/core.py`；计划中的 `inventory_budget.py` 尚不存在，V1 全局额度仍混在 `virtual_players.py` 且先库存后额度 | 全局额度、Inventory、粮食双写 | 已锁 Profile/Manor 后按 key 先预留全部 Counter，再按 key 写 Inventory；失败同事务回滚额度 |

所有待提取 primitive 还必须满足：不依赖 HTTP/文案/模板；明确事务和锁前置条件；业务不可执行返回结构化原因；
基础设施与编程错误继续抛出；真人 command 与 Bot command 复用同一资格、成本和结果规则。现有 start/finalize
入口不能因为名字相近就被直接嵌套到已经持有 `BotProfile`/Manor 锁的事务。

### 6.1 已冻结决定与剩余提取阻断

1. Maintenance V2 首版移除整个门客招募、候选转正式门客和候选转家丁链，不定义跳过或同步完成
   `ensure_auto_training()` 的新语义。未来纳入时必须另行评审共享 command、审计和任务语义。
2. 工资保持真人现状，只以 `SalaryPayment` 审计；不新增 `ResourceEvent.SALARY`。真人与 Bot command 仍需复用
   同一日期唯一、余额回滚和资格规则。
3. 技能书所有权、库位、`effect_type`、payload 与目标 Skill 一致性目前依赖 HTTP view，service 不能作为 actor-neutral
   边界直接复用。
4. 护院当前没有银两/粮食成本，科技 YAML 当前未见前置科技规则；command matrix 不得把计划性描述冒充现有规则。
5. Maintenance RNG 必须显式注入培养分配器；否则确定性 intent 无法约束最终属性分配。
6. `inventory_budget.py` 尚不存在，现有 V1 是先 Inventory 后 Counter；目标 Counter-first primitive 仍需实现与并发证明。

## 7. Maintenance trigger 与 Arena 租约

### 7.1 已冻结的纯契约

| Trigger | due 条件 | `APPLIED/NO_ACTION` | `next_growth_at` | 当前运行时接线 |
|---------|----------|---------------------|------------------|--------------|
| `SCHEDULED` | 必须 `next_growth_at <= now` | 提交时 sequence +1 | 同事务推进，旧值非空且新值严格更晚 | V1 `maintain_due_virtual_players` 尚未使用新契约 |
| `ARENA_ACCELERATION` | 不要求 due | 提交时 sequence +1 | 原值逐值保留 | V1 仍调用旧加速入口并“先修改、后恢复” |
| `ADMIN` | 调用方必须显式给出 `requires_due` | 提交时 sequence +1 | 必须显式选 advance/preserve；advance 写入非空不同值，非 due 时可早于旧远期值 | 现有 Admin action 直接 QuerySet update |

Schedule disposition 只约束已提交的 `APPLIED / NO_ACTION`。`BUSY`、`PAUSED`、`INELIGIBLE` 不推进 sequence；
`BUSY` 必须逐值保留原调度，`PAUSED / INELIGIBLE` 由生命周期或安全暂停契约决定是否清空、保留或重排；提交前回滚和
基础设施失败也不推进。`APPLIED` 必须只携带非空 action，其他结果必须只携带非空 reason。纯 `contracts.py` 和完整
outcome × trigger 测试已经 fail closed 地表达该矩阵，但全仓只有契约及测试引用
`MaintenanceTriggerPolicy/MaintenanceResult`，所以它目前是 Gate E 的可执行规范，不是当前 V1 的实际运行路径。

`NO_ACTION` 的公开原因词表与优先级固定为
`domain_constraint / strength_cap / band_spacing / band_action_cap / multi_band_transition`；决策返回全部适用原因，首项作为
兼容 primary reason。24 小时动作次数和综合增幅预算拒绝统一归入 `strength_cap`，不再引入未冻结的第六种原因。

### 7.2 `MAX_NO_ACTION_LEASE_AGE`

当前普通赛补位等待为 18,000 秒（5 小时），共斗补位等待为 28,800 秒（8 小时）。Gate A 已冻结：

```text
MAX_NO_ACTION_LEASE_AGE = 12 hours
no_action_lease_deadline = ArenaVirtualReserveMember.created_at + 12 hours
```

该值覆盖当前最长 8 小时等待并保留 4 小时宽限。deadline 不新增模型字段，也不得因 retry、BUSY、demand version
变化或重新评估而重置。到期的 `NO_ACTION` 应在 demand 锁内把 member 转为 `EXHAUSTED`、清空
`next_acceleration_at`，并在同一轮允许补入替代者。

该数值已写入 `virtual_reserve_pool.py` 的显式运行时常量，并覆盖 deadline 前重试、精确到期、active capacity 释放及同轮补位
测试。Gate E 验收及当前非生产环境启用后仍必须观察 shortage、创建预算和锁等待；实证若要求改值，必须重新打开契约评审，
运行时不得自行重置单个租约 deadline。

## 8. H-01 投递语义与故障窗口

### 8.1 当前时序

```text
Raid transaction
  -> 计算只读 BotLootClampDecision
  -> 在 Raid 内强制应用掉落额度
  -> 提交 Raid
  -> 注册/执行 transaction.on_commit callback
  -> callback 另开事务锁 BotProfile，退休或推迟
```

当前实现是 best-effort、每次调用至多处理一次的非持久建议，不是最终必达投递。Raid 的掉落额度已经在主事务内
生效，因此建议丢失不会扩大当次掉落；丢失的是“额度耗尽后尽快退休”的生命周期动作。

| 故障点 | Raid 结果 | 档案结果 | 现有证明/缺口 |
|--------|-----------|----------|---------------|
| Raid 内部事务回滚 | 回滚 | 不退休 | 已有测试 |
| 调用方外层事务回滚 | 回滚 | callback 被丢弃，不退休 | 已有测试 |
| 无外层事务：内层 commit 后、注册 callback 前进程退出 | 已提交 | 建议永久丢失 | 无持久记录，设计窗口 |
| 外层 commit 后、callback 执行前进程退出 | 已提交 | 建议永久丢失 | 无持久记录，设计窗口 |
| callback 数据库/基础设施异常 | 已提交 | 记录 degraded 日志后丢弃 | 已有测试证明不回滚 Raid |
| callback 编程错误 | 已提交 | 错误冒泡 | 已有测试证明 cleanup/完成派发仍执行 |
| 已退休档案重复建议 | 不受影响 | 不重复写入 | 已有测试 |
| 受 Arena 保护、同一 `now` 重复建议 | 不受影响 | 第二次不刷新 `updated_at` | 已有测试 |
| 受 Arena 保护、不同 `now` 的新建议 | 不受影响 | 允许把 `next_growth_at` 刷新为 `now + 1h` | 已冻结为当前行为；Gate B 迁移 owner 前补跨时间 characterization test，不重新打开 Gate A |

现有 V1 maintenance 不扫描 `loot_budget_daily`，后续 Raid 或其他生命周期条件可能最终退休档案，但不是该建议的
确定性恢复机制。日志中的 `recommendation dropped` 与当前行为一致。

不同 `now` 的刷新只是现有调度副作用，不构成持久退休重试；Arena 保护解除后的退休仍依赖新的退休建议或普通生命周期。

### 8.2 Gate A 批准结论

| 选项 | 保证 | 代价 | 回退方式 |
|------|------|------|----------|
| A. 非持久、at-most-once post-commit（已批准） | Raid 原子性正确；建议 best-effort，进程/DB 窗口可丢失 | 无 schema/worker；需要告警和运行观察 | 保持掉落纯决策，降级为只记录建议并由普通生命周期处理 |
| B. 同事务精简 outbox（首版不采用） | Raid 与 intent 同事务；worker 重试后最终处理 | additive migration、唯一键、worker/扫描器、积压监控和故障注入 | 停止 worker但保留 outbox 数据；不得把档案写塞回掉落函数 |

Raid 内掉落额度裁剪保持经济权威。退休建议允许偶发丢失，不建立 outbox；callback 基础设施异常记录 degraded 后丢弃，
编程错误继续抛出。若未来业务改为最终必达，必须重新评审并实现 B 的稳定幂等键、claim/retry、失败分类和恢复扫描，
不得把档案写重新塞回掉落计算。

观测上必须维护两个不同计数器：`virtual_player_loot_retirement_recommendation_total` 记录建议产生，
`virtual_player_loot_retirement_post_commit_attempt_total` 只在 callback 真正开始时记录 attempt 结果。暂停阈值使用
`degraded attempts / all callback attempts`，名称固定为 `h01_post_commit_attempt_degraded_rate`，不得宣称 delivery success
rate。内层提交后到 callback 注册前、外层提交后到 callback 开始前的进程退出都可能没有 attempt 事件，是本方案接受的
不可观测窗口。

## 9. 基线、阈值与证据分层

### 9.1 只读基线命令已证明的能力

`audit_virtual_player_baseline` 当前可以：

- 按 `Manor.id ASC` 和 `BotProfile.id ASC` 稳定抽样真人/V1 Bot cohort；
- 在单个一致性快照中完成全部查询：MySQL/PostgreSQL 使用 `REPEATABLE READ`，SQLite 使用 transaction snapshot；
- 汇总门客数量/等级差/稀有度、装备、技能、护院总量与集中度、建筑等级、联合离群率和组合碰撞率；
- 输出 cohort source fingerprint 和排除运行耗时/查询数的稳定 snapshot checksum；
- 记录只读审计耗时、查询数、snapshot contract 和每声望段真人/Bot 样本数；
- 在全局 cohort、任一配置声望段或未分类声望样本不足时配合 `--fail-on-insufficient` 失败，默认每段最少 30；
- 使用独占文件创建，顺序及并发调用都拒绝覆盖已有输出；
- 不执行 `INSERT/UPDATE/DELETE`，测试覆盖六类模型数量不变，并有身份字段泄漏回归测试。

报告不直接输出 username、email、姓名、原始 manor/guest ID。内部 ID 和模板 key 会参与整 cohort SHA-256；该值应称为
稳定一致性指纹，不能仅凭“SHA-256”宣称强匿名。

### 9.2 环境边界与后续实测

当前是测试环境，项目处于开发阶段；环境类别明确为非生产，不承载生产流量、生产真人账号或生产存量迁移。Gate A 是该测试环境的
设计和边界门禁，不读取现有开发库账号，也不要求生产玩家快照。隔离的 Django 测试数据库已经证明
报告 schema、稳定抽样、一致性快照、样本不足 fail closed、只读 SQL 和身份字段排除契约；测试 fixture 仅用于证明工具，
不会被表述为玩家分布结论，也不需要提交一份伪造的 `reports/virtual_player_gate_a_baseline.json`。

代表性分布只属于 Gate D2 的策略校准证据，但样本不足不阻止虚拟玩家创建。新真人提交后必须异步触发人口重算；已有供给
满足目标时不重复物化，存在缺口时立即创建或复活。Gate D1 退出前 V1 仅作为临时兼容实现；Gate D1 通过后，当前环境所有
新建 Bot 直接使用 V2，样本不足时运行 `conservative_cold_start`，而不是关闭人口供给或回退 V1。

人口触发的 durable truth 是 Gate D1 的 `BotPopulationRecomputeDemand`，不是 Celery delivery receipt。每个
`(region, prestige_band)` 行以 `requested_revision > completed_revision` 表示 pending；worker 对 claim revision 使用 5 分钟
token lease，claim 期间的新 merge 必须在旧 finalize 后继续 pending，过期 token 不得提交。完成行保留、失败有上限 backoff，
周期扫描恢复 pending/expired claim；消费者始终在既有人口锁内重算实际缺口，不按事件次数创建 Bot。

Gate D2 不以“整个策略”或“全部声望段”为一个开关；独立启用单元是
`(policy_version, reference_snapshot_version, prestige_band)`。`gate_d2_acceptance_workflow` 按三元组聚合证据，版本化运行配置
保存每段 routing，`gameplay.services.runtime_configs` 执行切换。缺样本、缺指标或越界只让对应三元组保持
`conservative_cold_start`；其他段可独立启用，且全局平均、兄弟段和其他版本均不得掩盖失败。

2026-07-28 实现勘误：transition 输入仍只有上述三元组；`BotRuntimeRoutingState.calibration_routes` 的每个持久项严格保存
`policy_version / reference_snapshot_version / prestige_band / policy_checksum / reference_snapshot_digest /
evidence_schema_version / evidence_digest` 七个字段，后四项 proof 只能由 acceptance workflow 派生。Bootstrap 已消费有效 route，
并在物化前重新核对持久 proof、当前 config/catalog 和冻结 snapshot；新计划遇到 route 缺失或漂移时回退 cold start，已经规划为
calibrated 的在途计划遇到漂移则在资产写入前拒绝并要求重规划。当前 catalog 为空，没有可激活 route；Maintenance calibrated
consumer 仍属于 Gate E V2 executor，不能接入 legacy Maintenance。candidate report 的 `candidate_snapshot_digest` 是 report 强制字段，
由 canonical `evidence_digest` 间接绑定，不是 route 的独立持久字段。

全部 Gate D2 判定阈值、画像方向和弃坑特征定义均由不可变 policy payload 提供并纳入 policy checksum；policy 可以声明比
Gate A 基线更严格的阈值，放宽必须先修订 acceptance contract。2026-07-29 已闭合 candidate correctness 契约：artifact schema v2
保存 `reference / candidate V2 / baseline V1 / inactive reference` 四类匿名原始 cohort，绑定 generator version/entrypoint、
engine/RNG/plan 版本、seed/cohort/catalog digest 及当前源码 manifest。外部受控生成器用 `hmac_sha256_v1` 证明这些输入；可信密钥只从
运行时 secret settings 读取，默认集合为空，artifact/report/catalog 均不能提供密钥。attestation 覆盖启用三元组、policy/snapshot
digest、生成器身份、root seed、源码、模板和全部原始 cohort；该方案只用于当前非生产环境，未来生产签名方案必须上线前独立评审。

workflow 使用 metric algorithm v2 从原始门客、装备、技能、护院、兵力、建筑、资源和生命周期字段独立重算全部 metrics。
`robust_joint_outlier_rate` 使用 12 维联合向量，按稳定 business key 拆分 `80% fit / 20% holdout`，holdout 不参与 median/IQR、
留一最近邻距离和 MAD 阈值拟合；边际分布相同但相关结构反转时仍能识别。report schema v3 必须声明 artifact schema v2 和算法 v2，
并与重算结果逐字段相等；旧算法 artifact fail closed。当前 catalog 仍为空，仓内没有获批代表性真人 artifact/candidate report，
因此 Gate D2 保持 `INTENTIONALLY_OFF`；synthetic fixture 只证明实现契约，不得被表述为可信校准或激活 route。

当前 `data/virtual_players.yaml` 的 V1 五档仍保持 `newbie / junior / middle / senior / veteran`，末段从 `30000` 开放到
无穷；本档案不修改该运行配置。Gate D1 才把 V2 原子切换为八档：`[0,500)`、`[500,2000)`、`[2000,8000)`、
`[8000,30000)`、`[30000,60000)`、`[60000,120000)`、`[120000,240000)`、`[240000,+∞)`。`veteran`
及以上没有活跃真人或地图/竞技场显式需求时目标供给为 0；低段 Bot 不计入高段供给，不能跨段复活或瞬间抬高声望补缺口。
公共真人声望跨段提交后异步、合并且幂等地重算旧段和新段，一次跨段不等于固定创建一个 Bot，也不运行 Maintenance。
Bot 通过正常领域动作自然跨段时，`profile_store.py` 在 post-commit 消费者中先按持久化 Manor 声望同步当前段，再重算旧/新
两段；历史目标段不变，selector 和人口只读快照不得隐式修档案。

八档不实现八套独立成长算法，只在同一 `BotDevelopmentPolicy` 中配置不同节奏。Gate D1 的 Bootstrap 根据目标段投影合理
历史年龄，不伪造逐条动作记录；Gate E 的 Maintenance 才按当前段执行正向成长检查区间、最小动作间隔和单次综合增长 cap。
任何提高强度动作同时受真人样本档位、当前/目标声望段及正常领域资格/成本约束，并取最严格值；所以真人样本少时会在分段
节奏基础上进一步减速，0 样本仍完全禁止正向成长。当前 V1 五档及其成长参数不受本项设计修改。

每声望段至少 30 个真人参考档案的门禁只控制“参考分布已校准”策略；V1 的 30 个样本只来自冻结回归 fixture，用于比较
行为和多样性，不要求当前环境保留运行中的 V1 人口。未达到真人门禁时不得伪称已校准，也不得降低阈值；
但可以使用已冻结的强度安全 fallback 创建。Gate D1 已把注册后 durable demand、专用人口 consumer 与 cold-start 强度保护
同批交付；Maintenance 在无参考时的实时成长治理仍属于 Gate E，不能由 D2 或 legacy Maintenance 提前启用。

Gate E readiness 通过只授权开始切换，不等于 Gate E 退出。Maintenance 必须先进入 `v2_cutover`，停止 V1/V2 发展写；
可丢弃 V1 测试数据经明确确认后重建为 V2，需保留的数据通过一次性显式入组处理。验证所有运行中或可重新激活的 Bot
均为 V2、运行时有效 V1 数量为 0 后，才进入 `v2_active`、直接 100% 启用 V2 Maintenance 并退出 Gate E。这里的精确谓词是
`engine_version = 1 AND state IN (active, slowing, abandoned, retired)`；`retired` 因可重新激活而必须计入，只有 `stale`
排除。due time、routing mode、fixture 标签或 Arena membership 都不改变判定；count query 归
`virtual_player_core.profile_store`，事务一致的清零验证归 `gate_e_cutover_workflow`。冻结的 `stale` V1 fixture
可以暂留到明确确认后的清理，但不得被任务、Admin 或竞技场重新激活。`maintenance_runtime` 保持
`not_measured_by_read_only_audit` 是刻意的职责边界。V1/V2 Maintenance 单档案、批量、写查询、锁等待和失败注入归 Gate E，
必须在 disposable database 上完成，不需要真人账号数据；它们不阻塞只做行为等价提取的 Gate B。

### 9.3 已冻结的首版验收配置

机器可读事实来源为
[`virtual_player_gate_a_acceptance_config_2026-07-27.yaml`](virtual_player_gate_a_acceptance_config_2026-07-27.yaml)。
这些值是首版保守准入上限，不是已经取得的测量结果；真实证据不通过时必须阻断，不能自动放宽。

| 类别 | 冻结值 |
|------|--------|
| 创建可用性 | 样本不足不阻止创建；Gate D1 以 `(region, prestige_band)` 持久 demand 的 request/completion revision 保存未完成工作，公共真人注册提交后 merge 并投递专用人口任务，消息仅加速唤醒；注册事务不等待 Bot 物化，仅在实际缺口时创建或复活，Bot 建号不递归触发，周期 demand 扫描和定时全量人口任务兜底 |
| V2 声望分段 | Gate D1 原子启用八档 `newbie [0,500)`、`junior [500,2000)`、`middle [2000,8000)`、`senior [8000,30000)`、`veteran [30000,60000)`、`elite [60000,120000)`、`legend [120000,240000)`、`mythic [240000,+∞)`；当前 V1 五档配置不变；边界连续不重叠且仅一个开放终段，空高段按需激活，低段不得顶替高段，禁止跨段复活和瞬间拔高 |
| 样本 | 参考分布校准要求每个声望段真人档案至少 30、目标 100；V1 仅保留至少 30 个冻结 fixture 用于回归比较，不要求运行时 V1 人口；未分类样本为 0，按段独立判定 |
| 强度安全 | 强度档位只按本地同地区、同声望段样本数计算；每段有一份无需真人数据的版本化保守起点 fixture；0 样本可九折借用全局同段结构，但最终综合/分项上限取该参考上限与同段保守起点 90% 中的更严格值，且停止一切提高强度的发展动作；1–4 样本上限为 P50 的 105%，24 小时最多 1 次/3%；5–29 样本上限为 P75 的 110%，24 小时最多 2 次/5%；30+ 样本上限为 P95 的 115%，24 小时最多 4 次/10% |
| 强度旁路 | 最终 Blueprint 和每次 Maintenance 均校验综合分及声望、建筑、门客、阵容战力、护院分项上限；Profile 只保留最多 4 条近期强度增量并原子校验任意连续 24 小时预算；Arena/Admin 不得绕过，超限档案停止提高强度并退出自动匹配，但不自动降级资产 |
| 分段成长 | Gate D1 启用 Bootstrap 历史年龄，Gate E 启用实时检查区间/最小间隔/单次 cap；实际限制取样本档位、来源/目标段和领域规则最严格值；Maintenance 不直接赠送声望，Arena/Admin 不得绕过，受控动作最多跨一个边界 |
| 连续指标 | 以真人 IQR（最小分母 1）归一化；Wasserstein `<= 0.25`，P10/P50/P90 偏差分别 `<= 0.35/0.25/0.35` |
| 类别分布 | base-2 Jensen-Shannon divergence `<= 0.10 bits` |
| 联合合理性 | 硬约束违规为 0；稳健联合异常率同时 `<= 15%` 且不高于真人 cohort 5 个百分点 |
| 多样性 | 单项指纹碰撞率 `<= 35%`、联合指纹 `<= 15%`，并且不得劣于 V1 |
| 画像与 abandoned | 声明方向的标准化效应量绝对值在 `0.20..0.80`；缺编/旧装备/成长断层率与非活跃真人偏差各 `<= 10` 个百分点 |
| 经济 | 每周期最多一个发展动作，发展动作不得凭空生成资源；强制结算每资源每周期 `<= 10%` 容量、每日 `<= 50%` 容量，且银两+粮食每日 `<= 2,000,000`；稀有/强力物品全局每日仍为 `8/2` |
| Bootstrap | 单档案 plan p95 `<= 250ms`，物化 p95 `<= 2s`，SQL/写查询 `<= 80/25` |
| Maintenance | 单档案 p95/p99 `<= 750ms/2s`、SQL/写查询 `<= 60/12`；批量 100 p95/p99 `<= 60s/120s`、SQL/写查询 `<= 2500/1200` |
| 锁与查询边界 | 锁等待 p95/p99 `<= 100ms/1s`，死锁/锁超时为 0，候选评分循环内 ORM 查询为 0 |
| benchmark | 预热 5 次、测量 30 次；批量 `1/10/100`，并发 worker `1/2`；绝对准入使用本表/YAML 冻结值，同库固定 V1 fixture 只作诊断对照，不需要真人数据且不得自动覆盖阈值 |
| 当前环境启用 | 当前是测试环境，项目处于开发阶段，环境类别为非生产；Gate D1 退出后 Bootstrap V2 对新建 Bot 直接 100%；Gate E readiness 后按 `v2_cutover -> V1 清零 -> v2_active -> Gate E 退出` 执行，退出时 Maintenance V2 直接 100%；Gate D2 只控制参考分布校准；不执行 engine enrollment 百分比灰度或生产观察期 |
| 独立 policy rollout | 当前状态由 `BotRuntimeRoutingState` 的 target/enabled/percent 持久化，与 mode/calibration 共用 revision CAS；YAML 只作严格校验后的初始化/transition 输入。稳定 bucket 只升级 V2 档案，降低比例不回退；换目标或停用时把旧 policy 的退役截止单调延后 720 小时 |
| 未来生产 | 本文不授权生产发布，也不冻结生产灰度比例、迁移顺序或观察期；Bootstrap、Maintenance、存量数据与回滚方案必须在上线前依据当时证据另行审批 |

分段成长的冻结值如下；区间由版本化随机上下文稳定取值，高段的历史年龄和间隔不得缩短，单次 cap 不得升高：

| 声望段 | Bootstrap 历史年龄 | 正向成长检查区间 | 最小正向动作间隔 | 单次综合增长 cap |
|--------|--------------------|------------------|----------------------|-------------------|
| `newbie` | `1--14` 天 | `4--8h` | `4h` | `4.00%` |
| `junior` | `14--45` 天 | `6--12h` | `6h` | `3.00%` |
| `middle` | `45--120` 天 | `8--16h` | `8h` | `2.50%` |
| `senior` | `120--240` 天 | `12--24h` | `12h` | `2.00%` |
| `veteran` | `240--360` 天 | `14--24h` | `14h` | `2.00%` |
| `elite` | `360--540` 天 | `18--30h` | `18h` | `1.75%` |
| `legend` | `540--720` 天 | `24--36h` | `24h` | `1.50%` |
| `mythic` | `720--1080` 天 | `30--48h` | `30h` | `1.25%` |

高四档已按开发阶段评审结果适度加快，避免高段追赶周期达到数月以及不可拆分的合法领域升级因单次 cap 过小而持续
`NO_ACTION`；这不放宽最终真人参考上限、24 小时预算或综合/分项 cap。

任一硬约束、经济 cap、重复或部分提交出现一次即暂停当前环境对应 V2 能力；维护失败率超过 1% 连续两小时、post-commit
退休 degraded 超过 0.1%、性能连续三小时越界、分布连续两个日窗口越界，或 Arena shortage 增加超过 2 个百分点，也必须
进入安全暂停。暂停不会把已持久化的 V2 档案自动交给 V1。

所有 rate/连续窗口由共享观测后端按 UTC fixed tumbling hour/day 聚合，只评估经过 5 分钟迟到宽限并按 `event_id` 去重的
闭合窗口。`virtual_player_safety_monitor` 作决定，`gameplay.services.runtime_configs` 以 `window_id` 幂等执行 routing CAS；
必需指标缺失即 fail closed，完整 heartbeat 下分母为 0 才视为没有 rate breach。Maintenance failure 分母只包含
`APPLIED / NO_ACTION / FAILED`，排除 `BUSY / PAUSED / INELIGIBLE`；分布按 policy/snapshot/band 三元组独立判定。
上述 owner 与 runtime 已实现并通过 readiness/回归验证，但未获本档案授权执行 Gate E cutover 或 runtime transition。

### 9.4 隔离真实服务证据

- canonical 命令固定为 `DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a`；它先运行固定 contract suite，再执行
  MySQL/Redis preflight，最后运行 baseline 与 H-01 real-service suite。精确文件集合、收集数量、nodeid SHA-256、环境和时间见
  [`virtual_player_gate_evidence_manifest_2026-07-28.yaml`](virtual_player_gate_evidence_manifest_2026-07-28.yaml)。
- 2026-07-28 已在固定本机 MySQL/Redis 隔离服务上完成 canonical target：当前为 158 个唯一 nodeid，`146 contract + 12 real-service`
  全部通过；精确 timestamp、checksum 与环境见 manifest。该 evidence 仍只证明 Gate A 执行，不授权任何 D1/E transition。
- 新 target 引入前的历史证据仍有效但分层记录：MySQL `13306` 与 Redis `16379` preflight 曾通过；baseline 聚焦门禁在真实
  MySQL 通过 9 项测试，包括采样中并发更新时仍保持旧快照、事务结束后看到新值的 MVCC 竞态。
- 真实 MySQL 门禁已有“竞技场补位与退休竞争时，不会租用已退休档案”的用例。
- `tests/raid_concurrency_integration/h01_cross_races.py` 已在真实 MySQL/Redis 下覆盖 Raid post-commit callback
  与 `maintain_due_virtual_players()`、`roll_virtual_player_population()`、`replenish_virtual_reserve()` 的竞争；
  聚焦命令结果为 `3 passed, 6 deselected`。用例保留真实 transaction/on_commit、档案锁、掉落裁剪、退休 command
  以及三个竞争入口，证明竞争方不会复用已退休档案或产生锁等待回归。
- `make test-critical DJANGO_TEST_USE_ENV_SERVICES=1` 完整门禁通过：`61 passed in 523.56s`。
- 上述三项是 historical evidence，不等同于 canonical target 已运行。只有完整 target 实际执行并与 manifest 的 suite
  count/checksum 一致后，才能更新 canonical execution 状态；测试树变化会使旧 checksum 失效。

## 10. Gate A 退出清单

- [x] 冻结两个公共门面和运行时消费者。
- [x] 冻结 `BotProfile` 直接/间接 reader 和当前 DML owner。
- [x] 冻结 Arena demand/member 当前 DML owner。
- [x] 登记 Arena 双向依赖债务并阻止新增边。
- [x] 记录当前全局锁图、缺锁边界和已证实反序。
- [x] 记录第 8.9 节各领域 owner、事务、副作用和最小提取边界。
- [x] H-01 掉落计算无档案写副作用，回滚/提交/异常/重复建议有聚焦测试。
- [x] Maintenance trigger 纯决策矩阵可执行且 `ADMIN` 缺省 fail closed。
- [x] 只读基线命令具备一致性快照、分段样本、并发独占输出和身份字段门禁，真实 MySQL MVCC 测试通过。
- [x] 技术评审批准第 5.1 节分支主锁序，并登记现有反序、缺锁和部分提交路径的迁移责任。
- [x] Maintenance V2 首版移除整个门客招募链；工资保持 `SalaryPayment`-only 审计。
- [x] 批准 H-01 选项 A、故障窗口、异常分类和回退方式。
- [x] 冻结 H-01 不同 `now` 新建议会刷新 `next_growth_at` 的当前语义；跨时间 characterization test 列为 Gate B owner 迁移前置证明。
- [x] 批准 `MAX_NO_ACTION_LEASE_AGE = 12h` 及 `created_at + 12h` 不重置契约。
- [x] 明确证据分层：Gate A 不访问现有环境玩家数据；Gate D1 冷启动和 Gate E benchmark 不需要真人数据，代表性分布只归 Gate D2。
- [x] 冻结创建与样本解耦：注册提交后触发人口重算，缺样本时使用保守 fallback，不关闭人口供给。
- [x] 冻结持久人口 demand：request/claim/completion revision、token fencing、claim 期间 merge 不丢失、失败退避与周期恢复；
  Celery 消息只负责加速，不能充当完成事实。
- [x] 冻结 V2 八档声望边界及 Gate D1 原子切换：当前 V1 五档不改；真人跨段重算旧/新两段，空高段按需供给，低段不得
  顶替高段，存量只按真实声望显式重分段且不得改变资产或声望。
- [x] 冻结每段独立的零样本保守起点 fixture，以及 Bot 自然跨段由 `profile_store.py` 同步当前段的写 owner；读取路径不修数据。
- [x] 冻结零样本、稀疏、有限和充分四档强度上限及有界的连续 24 小时持久预算，且 Maintenance/Arena/Admin 均不得旁路。
- [x] 冻结八档分段成长节奏及 Gate D1/Gate E 分离启用：高段单调减速，取样本/分段/领域最严格值，当前 V1 配置不变。
- [x] 按用户授权填写并冻结自然度、经济、性能阈值及当前非生产环境的启用/暂停条件。
- [x] 明确当前是测试环境且属于非生产：Gate D1/Gate E 退出后对应 V2 能力直接 100%，Gate D2 独立可选，未来生产发布另行审批。
- [x] 运行并通过包含 H-01 的真实 MySQL/Redis 交叉并发门禁。
- [x] 建立 canonical Gate A target 的证据 manifest、精确 suite count/checksum 与独立契约测试；未执行的新 target 保持诚实状态。
- [x] 用户明确要求按全局判断合理配置并继续完成，视为 Gate A 五项决定及证据分层的最终确认。

## 11. 审批结论与后续门禁

2026-07-27 已冻结五项结论：H-01 采用非持久 at-most-once post-commit；租约年龄为 12 小时绝对期限；
Maintenance V2 首版移除整个门客招募链；工资只以 `SalaryPayment` 审计；锁序采用第 5.1 节三条分支协议，
且禁止持有 Profile 后反向锁 Member。

同日按用户授权冻结首版保守验收配置，并于 2026-07-28 收口切换语义：当前是测试环境，项目处于开发阶段，环境类别明确为
非生产。Gate D1 退出后 Bootstrap V2 直接 100%；Gate E readiness 通过后必须先进入 `v2_cutover`、完成 V1 清零，再进入
`v2_active` 并退出 Gate E，此时 Maintenance V2 直接 100%。Gate D2 仅控制可选的参考分布校准策略。当前环境不走生产式
engine enrollment 百分比灰度；独立 policy rollout 不改变这一启用语义。缺指标或越界均 fail closed，不能在部署时自动放宽。

Gate A 已通过，Gate B 的行为等价 owner 迁移不再被数据采集阻塞。Gate A 本身仍不提前授权 Gate D1/Gate D2/Gate E 的运行时能力；
对应 gate 验收完成后，授权范围仅是当前非生产环境直接启用 V2，不构成任何生产发布授权。

同日补充冻结创建与强度不变量：出现新真人时人口供给必须及时重算，样本不足只能降低生成/成长强度，不能阻断创建；
本地无样本时可以九折借用全局同段结构，但仍属于 0 样本档；最终综合/分项上限取该参考上限与同段版本化保守起点 90% 中的更严格值，
并暂停一切提高强度的发展动作。稀疏样本最多为 P50 的 105%。这些属于产品安全边界，不允许被竞技场加速、Admin 或全局
样本 fallback 绕过。

同日冻结 V2 八档分段：`newbie / junior / middle / senior / veteran / elite / legend / mythic` 连续覆盖
`[0, +∞)`，其中 `legend` 覆盖十几万声望，`mythic` 承接 `240000+`。该 schema 在 Gate D1 与 Bootstrap V2 和强度保护
同批启用；当前 V1 五档运行配置不提前修改。高段只按真人活跃或地图/竞技场显式需求供给，低段 Bot 不得充当高段供给，
禁止跨段复活和瞬间拔高；存量档案只允许依据持久化真实声望显式、幂等重分段，不改变声望或资产。
每段零样本 fallback 都是无需真人数据的版本化保守起点 fixture，90% 上限后仍须合法落在该段。Bot 只能通过正常领域动作
自然跨段，提交后由 `profile_store.py` 同步当前段并重算旧/新两段，不能在 selector 中隐藏写入。

同日冻结八档成长节奏：Bootstrap 历史年龄依次为 `1--14 / 14--45 / 45--120 / 120--240 / 240--360 / 360--540 /
540--720 / 720--1080` 天；实时最小正向动作间隔依次为 `4/6/8/12/14/18/24/30h`，单次综合增长 cap 依次为
`4/3/2.5/2/2/1.75/1.5/1.25%`。Gate D1 只启用历史投影，Gate E 才启用实时节奏。V2 使用一个字段明确记录最近正向
增长时间，并在 Profile 锁内与强度预算及领域写入原子更新；Arena/Admin 不得旁路。受控动作最多跨一个边界并同时满足
来源/目标段的更严格限制；PVP 等玩家驱动结果不被 Bot 策略拒绝，但提交后必须记录、对账，超限时冻结后续受控成长并退出
自动匹配。当前 V1 五档和成长配置保持原样。

后续 gate 仍有以下硬条件：

1. Gate D1 的固定 fixture 强度、硬约束和经济门禁通过后，当前环境的新建 Bot 全部使用 Bootstrap V2；没有足够真人样本时
   使用 `conservative_cold_start`。同批必须通过八档边界/开放终段、高段按需人口、真人跨段 post-commit 重算、低段不顶替
   高段、禁止跨段复活/瞬间拔高、每段零样本 fixture、八档历史年龄投影且不伪造动作记录、Bot 自然跨段同步及存量幂等
   重分段测试，以及持久 demand 的并发 merge、过期 claim fencing、失败恢复和消息丢失用例。Gate D1 不需要真人数据。
2. Gate D2 是独立可选门禁；只有启用参考分布校准策略前，才需要合规的代表性匿名聚合数据和分段样本门禁。启用单元严格为
   `(policy_version, reference_snapshot_version, prestige_band)`，失败三元组保持 cold start，不影响其他通过段，也不阻塞 D1
   或 Gate E。
3. Gate E readiness 在当前环境切换 Maintenance V2 前，用 disposable database 测量单档案/批量耗时、写查询数、锁等待和
   失败注入，并逐档验证最小正向间隔、单次 cap、跨段双重校验、`last_strength_increase_at` 原子更新及 Arena/Admin 无旁路；
   这些测试不需要真人账号数据。readiness 通过后按 `v2_cutover -> 测试数据处理 -> V1 清零 -> v2_active` 执行，随后才能
   退出 Gate E 并直接 100% 启用。
4. 代表性分布证据缺失或越界时仅关闭参考分布校准策略；Maintenance benchmark 缺失或越界时不得进入 Gate E cutover。
   两类阈值不得静默放宽；通过固定 fixture 强度门禁的冷启动人口供给不因普通样本不足而关闭，也不回退 V1。
5. 当前环境的可丢弃 V1 数据在 Gate E readiness 通过后、退出前重建为 V2；需保留的测试数据使用一次性显式入组。Gate E
   退出时 `engine_version=1 AND state IN (active, slowing, abandoned, retired)` 必须为 0；只允许冻结且不可重新激活的
   `stale` V1 fixture 等待后续清理。本文不自动执行删除、批量重建或
   生产式迁移，实施任何破坏性数据操作前仍需单独确认。
6. 未来若建立生产环境，必须重新审批生产灰度比例、观察期、存量迁移、回滚和真人聚合数据边界；当前文档不预设这些值。

本次 Gate A 文档与证据 manifest 没有授权 Gate C additive schema、`BotExternalStrengthReconciliation`、强制结算预算字段、
safety monitor 或任何 V2 runtime/worker 接线；这些仍需后续 gate 的单独明确批准。
