# 虚拟玩家架构重构与自然化优化方案（2026-07-27）

## 0. 文档状态

- 状态：Readiness Implemented；2026-07-29 修订；当前是测试环境，项目处于开发阶段，环境类别明确为非生产。Gate A-B 契约与 owner 迁移、Gate C-E additive schema 和关闭状态下的 runtime 能力均已实现；Gate D1 具备 exit review 条件，Gate E 六档 Maintenance benchmark、真实锁语义、失败注入和静态门禁已通过。Gate D2 的 content-addressed consumer、原始 candidate artifact 重算、真正的联合异常算法和外部受控 generator attestation 契约已实现，但缺少获批代表性真人 evidence，继续保持关闭。现有数据重建或删除、批量入组或重分段、Gate D1/E routing 切换、生产发布及 Git 历史操作仍未授权。
- 环境标识：`test`（测试）、`non_production`（非生产）、`production=false`；文中的“运行时代码/运行时调用方”仅指当前可执行代码，不代表生产环境。
- 适用范围：虚拟玩家人口、创建、成长维护、门客、装备、技能、护院、库存、竞技场后备协作。
- 范围等级：前半段为 `Structural Shift`，自然化维护切换后整体为 `Architecture Migration`。
- 实施原则：不一次性重写；除本文明确列出的虚拟监牢日清、门客治疗和竞技场虚拟补位满血快照外，不改变现有玩家 PVP、
  竞技场和战利品契约，不把完整 HFSM 或主动 Raid/交易纳入首轮。

本方案解决两个互相关联的问题：

1. `gameplay/services/virtual_players.py` 职责过载，纯计算、ORM 写入、事务锁、人口编排和内容生成混在同一模块。
2. 虚拟玩家的属性虽然具有随机差异，但门客、装备、技能、护院、建筑和科技之间缺少一致的发展路线，玩家可以感受到明显的“直接投影”痕迹。

核心决策如下：

- 保留现有生命周期状态和人口供给模型。
- 先建立清晰的模块与写入边界，再切换自然化生成和维护算法。
- 使用稳定的 `BotDevelopmentPlan` 表达虚拟玩家长期偏好。
- 使用领域内局部评分和有界随机选择，不先引入全局 Utility AI 框架。
- 新建虚拟玩家使用快速历史投影，存量虚拟玩家使用增量维护逐步自然化。
- V1 批量投影写路径放入有明确退场条件的 `legacy/` 隔离区；V2 的纯 `projection.py` 不接收任何 V1 ORM 写函数。
- V2 执行器归属一旦写入档案便保持稳定；当前非生产环境在对应 gate 通过后直接 100% 使用 V2，不做百分比灰度，也不自动把 V2 档案降回 V1。
- V2 声望段固定为 `newbie / junior / middle / senior / veteran / elite / legend / mythic` 八档，覆盖 `[0, +∞)`；当前
  `data/virtual_players.yaml` 的 V1 五档保持不变，八档只在 Gate D1 与 Bootstrap V2、强度保护及显式重分段命令原子启用。
- 八档共用同一组领域成长动作，不复制八套算法；`BotDevelopmentPolicy` 仅叠加分段成长节奏。声望越高，合理历史年龄越长、
  正向成长间隔不缩短、单次综合增幅上限不升高；实际限制始终取真人样本档位、当前/目标声望段和领域动作约束中的最严格值。
- `veteran` 及以上高声望段按真人活跃或地图/竞技场显式需求激活；空高段不预建，低段 Bot 不得计作高段供给，也不得通过
  跨段复活或瞬间抬升声望来填补高段缺口。
- `rng_version` 与执行器、画像和策略版本一同持久化；V2 发展随机派生和独立 policy rollout 使用明确的 SHA-256 规范化编码，不使用 Python 内置 `hash()`。
- 已发布策略先写入不可变 `BotPolicyRelease` 注册表，运行时不直接把可热刷新的 YAML 当作已发布策略事实来源。
- policy payload 永久不可变；当前非生产环境的退役保护期由持久化、只增不减的 `retire_not_before` 判定，不能从日志或当前 YAML 临时推算。
- 首版 Maintenance V2 每周期只提交一个同步发展动作；后续若引入异步动作，必须先增加持久化执行记录或 outbox。
- 后续维护复用真实玩家的资格、成本、资源事件和结果规则，但不为每个虚拟玩家制造大量独立 Celery 倒计时任务。
- 持久化个体特征与可更新策略参数分离，分别版本化。
- 掉落裁剪只计算额度与退休建议，不在 PVP 掉落计算函数内直接修改 `BotProfile`；退休由显式 post-commit 生命周期命令处理。
- H-01 使用非持久、at-most-once post-commit：基础设施异常记录后丢弃，编程错误继续抛出，不引入 outbox。
- PVP 等玩家驱动的强度或声望结果不受 H-01 的非持久例外保护：其原领域事务必须同时写入窄化的持久对账 intent，
  `on_commit` 只负责加速唤醒，周期扫描负责恢复丢失唤醒；该 Gate C schema 与 Gate E worker 已获授权实现，但在对应 gate 证据闭合前保持关闭。
- 外部对账的 profile/population 两阶段各使用带 token 的 5 分钟 claim lease、最多 12 次有界重试和 `QUARANTINED` 终态；旧 worker 失去 token 后不得提交，
  未解决的早期 intent 阻塞同档案后续 intent、正向 Maintenance 和自动匹配，不能因坏 payload 无限重试或乱序跳过。
- Gate D1 使用按地区/声望段唯一合并的持久 `BotPopulationRecomputeDemand`；请求、claim 和完成各有 revision 边界，Celery
  消息只负责加速唤醒，不能作为已交接或已完成人口重算的事实来源。
- Arena `NO_ACTION` 租约绝对期限固定为 member `created_at + 12h`，retry、BUSY、version 和重验不得重置。
- Maintenance V2 不接入真人招募候选、模板晋升或囚犯转化链；竞技场数量阶段允许受限的黑/灰门客模板扩容，工资只以
  `SalaryPayment` 审计，不新增 `ResourceEvent.SALARY`。
- 虚拟玩家监牢由独立日任务清空：对任务 cutoff 前、俘获方为虚拟玩家且仍为 `HELD` 的囚犯批量执行既有
  `RELEASED` 状态迁移。该任务不自动招募、不删除囚犯记录、不重建或返还原门客，也不影响真人玩家监牢；它独立于
  engine/routing 和 Maintenance sequence，在 `v2_cutover`、`v2_paused` 下仍继续执行。
- Maintenance V2 增加单门客 `guest_healing` 同步动作：只使用庄园已有药品，复用门客生命与库存领域规则并原子扣减一件药品；
  不允许免费回满、隐式创建药品或借升级清除重伤。治疗恢复既有战斗状态，不消费 24 小时永久强度增长预算，也不更新
  `last_strength_increase_at`，但仍占用本周期唯一同步动作。
- 普通赛和共斗的竞技场虚拟补位统一以满血快照参赛：每个实际写入虚拟 Entry 的门客 snapshot 必须满足
  `current_hp == max_hp`。这是仅作用于本场虚拟补位快照的规则，不写回 `Guest.current_hp`、不消耗药品、不改变门客状态，
  也不改变真人玩家报名快照继续保留报名时生命值的现有语义。
- 真人注册事务提交后异步触发人口重算；人口存在缺口时立即创建或复活 Bot，注册请求不等待物化，定时人口任务负责兜底。
- 真人样本不足只禁止对应声望段启用 Gate D2 参考分布校准；已通过的段可以独立启用，失败段继续
  `conservative_cold_start`。Gate D1 前不阻止临时 V1，Gate D1 通过后不阻止 V2 Bootstrap 补足人口，且不得因此回退 V1。
- Bootstrap 与 Maintenance 共用四档强度保护：按当前地区、当前声望段的 0、1--4、5--29、30+ 个有效真人样本分别采用版本化新手基准、P50、P75、P95 上限；
  所有提高强度的发展动作都受 24 小时次数、增幅和综合/分项上限约束，Arena 与 Admin 不得旁路。
- 分支锁序固定为 Arena `Event/Tournament -> Demand -> Member -> PopulationControl -> Profile -> Manor`、Population
  `BackfillDemand -> PopulationControl -> Profile -> Manor`、External Reconciliation profile 阶段 `Reconciliation -> Profile -> Manor`、
  population handoff 阶段 `Reconciliation -> PopulationRecomputeDemand`、Maintenance/Retirement/Admin `Profile -> Manor`；人口 demand
  的 claim、容量执行和 finalize 分属短事务，不跨人口执行持有 demand 行锁，禁止持有 Profile 后反向锁 Member 或 Reconciliation。
- YAML 只保存允许模式与初始化默认值；当前 routing 状态由数据库单例 `BotRuntimeRoutingState` 持久化，所有切换均经
  `gameplay.services.runtime_configs` 做 revision CAS，安全暂停不以进程缓存或可热刷新的文件作为事实来源。
- 独立 policy rollout 的 target、enabled 和 percent 同样由 `BotRuntimeRoutingState` 持久化，并与 Bootstrap、Maintenance、
  calibration 共用 routing revision；YAML 只提供严格校验后的初始化/transition 输入。启用、换目标、调比例和停用均通过显式
  transition，停用或换目标时在同一事务内把旧 policy 的 `retire_not_before` 单调延长 720 小时。
- 当前非生产环境的 safety provider 使用有保留期的数据库事件账本和不可变闭合窗口；现有 Celery 累计 counter 及进程内
  fallback 不具备 UTC tumbling window、迟到宽限、`event_id` 去重和完整 heartbeat 语义，不得充当安全门禁事实。
- Gate D1 退出前 V1 只承担兼容和行为对照；Gate D1 通过后当前环境的新建 Bot 全部由 Bootstrap V2 创建。Gate E readiness
  通过后先进入停止 V1/V2 发展写入的 cutover，完成测试数据重建或显式入组并验证运行时有效 V1 数量为 0，再直接 100%
  启用 Maintenance V2 并退出 Gate E。可丢弃 V1 测试数据的重建仍属于破坏性操作，执行前另行确认。
- 本文不授权生产发布，也不冻结生产灰度比例、观察期或存量迁移方案；未来上线前必须依据当时数据和运行条件重新审批。

---

## 1. 目标与非目标

### 1.1 目标

1. 将虚拟玩家核心服务拆成可独立验证的配置、纯规则、持久化和编排边界。
2. 保持现有公开入口、Celery 任务、人口容量、地图可见性和竞技场租约语义兼容。
3. 让一个虚拟玩家内部形成稳定且可解释的发展路线：
   - 门客存在核心、二队和替补差异；
   - 装备与门客属性、技能和套装需求相关；
   - 护院存在主力兵种、次要兵种和侦察储备；
   - 技能学习符合属性、定位、技能位和物品约束；
   - 建筑、科技、库存和资源与玩家画像相互一致。
4. 让同一 `growth_seed`、engine version、RNG version、plan schema、policy version 和维护序号可重放，同时让不同维护周期不重复同一组随机选择。
5. 建立当前非生产环境可直接全量启用、可安全暂停、可观测的 V2 生成与维护路径，并保证已入组档案及独立 policy rollout 的归属稳定；未来生产路由另立设计。
6. 样本充分时用真人同声望段分布校准结果；样本不足时使用明确标识、版本化且更弱的保守 fallback。
7. 保证档案不会因开关或 policy rollout 变化在 V1/V2 执行器之间来回切换。
8. 在启用 V2 前冻结可执行的自然度、经济和性能验收阈值。

### 1.2 非目标

首轮明确不实现：

- 虚拟玩家主动发起 Raid、侦察、市场交易、拍卖或帮会行为；
- 世界聊天、社交关系和在线会话模拟；
- 通用行为树、完整 HFSM 或跨全游戏的 Utility AI 引擎；
- 为历史虚拟玩家伪造完整可见操作记录；
- 未经确认删除兼容入口、生产数据或不可丢弃的测试数据；当前可丢弃测试 Bot 只可在 Gate E readiness 通过后、Gate E 退出前显式重建为 V2；
- 首版 Maintenance V2 中的异步发展动作、通用 outbox 框架或无限增长的动作历史表。
- 真人招募候选、候选转正式门客和候选转家丁链；V2 竞技场数量阶段的受限模板扩容不属于真人招募候选链。
- 本轮不重写 V1 门客招募、模板晋升、稀有度选择或特殊模板资格规则；相关公平性风险保留为独立后续范围。
- 本轮不改变俘获后原门客及装备的既有处置，也不实现赎回、自动归还或按囚犯快照重建门客；“每日清空监牢”仅指
  虚拟玩家囚犯从 `HELD` 幂等迁移到 `RELEASED`。
- 不借本轮自然化重写竞技场后备倍率、租约或目标强度；竞技场拆分继续保持原有随机种子与强度区间，另按本文新增的
  `roster_target_count` 允许合法人数差异化，并接入 V2 数量阶段扩容；其余竞技场行为保持兼容。

这些能力只有在自然化生成与维护稳定后，才进入独立设计和产品风险评估。

---

## 2. 当前事实基线

### 2.1 当前模块规模

| 模块 | 规模 | 当前职责 |
|------|------|----------|
| `gameplay/services/virtual_players.py` | 约 3887 行 | 配置、人口、投影、创建、生命周期、装备、技能、库存、锁、维护和补量 |
| `gameplay/services/virtual_player_core/population.py` | 约 229 行 | 已提取的纯人口规划 |
| `gameplay/services/virtual_player_rules.py` | 约 163 行 | 生命周期、量化和角色倍率纯规则 |
| `gameplay/services/virtual_player_state_policy.py` | 约 123 行 | 状态能力矩阵 |
| `gameplay/services/arena/virtual_reserve.py` | 约 1392 行 | 竞技场虚拟后备需求、租约和加速成长 |
| `gameplay/services/arena/virtual_backfill.py` | 约 519 行 | 阵容评分、候选查询和普通赛/共斗补位物化 |
| `core/utils/yaml_validators/virtual_players.py` | 约 702 行 | YAML 离线校验；当前与运行时加载边界分离 |
| 主要虚拟玩家测试 | 超过 4500 行 | 行为、并发、人口、装备、技能、库存和状态回归 |

现有运行时调用方相对集中：

- `gameplay/tasks/virtual_players.py`
- `gameplay/views/map.py`
- `gameplay/services/arena/virtual_reserve.py`
- `gameplay/services/virtual_player_loot_limits.py`
- `gameplay/services/runtime_configs.py`
- `gameplay/management/commands/generate_virtual_players.py`
- `gameplay/admin/bots.py`
- `gameplay/services/raid/combat/battle.py`
- `core/utils/yaml_validators/virtual_players.py`

大量测试直接导入 `virtual_players.py` 的私有函数，因此测试迁移也是重构的一部分，不能把无限期重导出私有函数当作完成。

### 2.2 当前运行流程

```text
Celery Beat
   |
   +-> plan_virtual_players_task
   |      -> 读取真实玩家分布并记录人口计划
   |
   +-> roll_virtual_players_task
          -> maintain_due_virtual_players
          -> roll_virtual_player_population
                 -> 重定目标段位 / 重新激活 / 创建 / 休眠

地图搜索 -> 记录补量需求 -> 下次人口滚动消费

竞技场需求 -> 租用可参赛 BotProfile -> 必要时加速成长 -> 补位
```

### 2.3 当前生命周期

主生命周期是扁平状态：

```text
ACTIVE -> SLOWING -> ABANDONED -> RETIRED
   ^                         |         |
   +-------------------------+---------+

STALE 为管理员停用状态，不参与自动恢复。
```

生命周期本身不需要改成 HFSM。未来的“培养、经营、战斗准备”等行为属于独立维度，不应继续扩展 `BotProfile.State`。

---

## 3. 审计发现

本轮确认 2 个 `High` 级边界缺陷。H-01 必须在进入 V2 写路径前完成整改和真实并发验证；H-02 已经形成虚拟监牢长期占位和
真人门客缺少后续处理的实际生命周期缺口，必须在 Gate E 退出前接入每日清理。其余问题为 `Medium`，会直接阻碍自然化能力继续扩展，
或形成已确认的竞技场正确性缺口。

### H-01 掉落裁剪路径隐藏生命周期写入

**现状**

`gameplay/services/virtual_player_loot_limits.py::clamp_bot_loot_resources()` 名义上计算掉落上限，却会直接调用 `retire_virtual_player_if_unprotected()`。其调用方 `gameplay/services/raid/combat/battle.py` 已处于 Raid 事务内，并已按顺序锁定 `RaidRun` 和双方 `Manor`；退休命令随后再锁 `BotProfile` 并读取竞技场保护状态。

**影响**

- 纯计算式接口具有未体现在返回值中的持久化副作用。
- PVP 写命令被迫知道虚拟玩家生命周期，锁顺序可能与维护、人口滚动和竞技场租约形成反向等待。
- 掉落计算成功但退休失败时，异常语义会泄漏到战斗结算；反之也无法单独重试退休。

**最小安全修复**

- 将额度计算改成返回 `BotLootClampDecision`，只包含裁剪后资源、预算是否耗尽和退休建议。
- Raid 写命令先在原事务内提交战斗与掉落；提交后调用显式、幂等的生命周期命令处理退休建议，不在计算函数中锁 `BotProfile`。
- 若业务要求退休请求绝不丢失，再单独增加精简 outbox；首轮不得用吞异常的 `on_commit` 回调伪装可靠投递。

**完成证明**

- AST/契约测试证明 `virtual_player_loot_limits.py` 不导入或调用任何 profile 写命令。
- Raid 失败回滚不会退休档案；Raid 成功后重复处理退休建议仍幂等。
- 受 Arena 保护时，同一 `now` 的重复建议不刷新 `updated_at`；不同 `now` 视为不同 Raid 产生的新建议，允许把
  `next_growth_at` 刷新为 `now + 1h`。这只是现有调度副作用，不构成持久退休重试；保护解除后的退休仍依赖新建议或普通
  生命周期。Gate B 迁移 owner 前必须补跨时间 characterization test；该测试不重新打开 Gate A，但语义不能继续标记为待定义。
- 真 MySQL 覆盖 Raid 结算与维护、人口退休、竞技场租约的交叉并发，不出现死锁或部分提交。

### H-02 虚拟监牢缺少自动清理与明确写入 owner

**现状**

Raid 俘获成功后会删除原 `Guest` 并创建 `JailPrisoner(status=HELD)`。监牢满员时只停止新的俘获；当前日任务仅衰减囚犯忠诚度，
虚拟玩家不会主动劝降、招募或释放。`gameplay/services/jail.py::release_prisoner()` 也只把记录改为 `RELEASED`，不会返还或重建原门客。

**影响**

- 虚拟玩家囚犯会长期占用容量，监牢满后永久失去后续俘获能力。
- 真人门客被虚拟玩家俘获后没有任何 Bot 生命周期动作处理，玩家资产结果与监牢容量状态长期脱节。
- 若把清理逻辑直接塞进 Raid、selector 或通用忠诚度衰减任务，会混淆战斗事务、读取边界和监牢领域写入所有权。

**最小安全修复**

- 在 `gameplay/services/jail.py` 增加 actor-neutral、可批处理的虚拟监牢释放 command；它只锁定并迁移
  `captor__bot_profile__isnull=False`、`status=HELD` 且 `captured_at <= cutoff` 的囚犯，状态变化严格为
  `HELD -> RELEASED`。
- 在 `gameplay/tasks/virtual_players.py` 增加每日一次的 transport task，按 `captured_at, id` 稳定分批调用该 command；task
  只传递 cutoff/batch size 并汇总 scanned/locked/released/skipped/failed，不复制 ORM 或状态分支。
- 日清理是强制 housekeeping，不是发展动作，不推进 `maintenance_sequence`，不依赖 Bootstrap/Maintenance mode，也不受
  24 小时强度预算限制。并发任务通过条件状态迁移保持幂等；cutoff 后新俘获的记录留待下一日处理。
- 本轮保持门客招募现状：日清理不调用 `recruit_prisoner()`，不改变真人监牢，不删除 `RELEASED` 历史记录，也不增加原门客返还语义。

**完成证明**

- 固定 cutoff 下重复执行只在第一次释放符合条件的虚拟囚犯，第二次 `released=0`；真人监牢和非 `HELD` 记录逐值不变。
- 清理与同一虚拟庄园的新俘获并发时，cutoff 前记录最终为 `RELEASED`，cutoff 后记录保持 `HELD`，不出现重复状态迁移或容量负数。
- 任一批次失败只回滚该批，重试可继续处理剩余记录；单个坏记录不允许被宽泛异常静默跳过。
- `v2_cutover`、`v2_paused`、V1/V2 混合档案下均执行相同清理语义，且 Celery Beat 每个日历日只产生一个计划实例。

### M-01 核心服务职责过载

**现状**

`virtual_players.py` 同时拥有纯计算、配置缓存、模型查询、行锁、事务、对象创建、库存限额和维护编排。

**影响**

- 生命周期改动可能影响竞技场后备和地图人口。
- 纯算法必须依赖 Django 测试环境才能验证。
- 任何新行为都会继续扩大循环依赖和私有函数耦合。

**最小安全修复**

按第 5 节责任提取纯计划、只读 adapter、`profile_store`、人口/补量、Bootstrap/Maintenance 编排和 V1 `legacy/` 隔离区，保留有限兼容门面；不建立覆盖所有领域 ORM 的通用 repository。

**完成证明**

- 纯模块 AST 测试禁止导入 Django 和 `gameplay.models`。
- 运行时调用方不再导入旧模块私有函数。
- 兼容门面只保留明确列出的公共 API。

### M-02 生成和维护直接覆盖领域状态

**现状**

- 建筑被批量设置为统一等级并清除升级状态。
- 资源被直接重置为容量比例，可以无事件地增加或减少。
- 科技被批量设置为统一等级，没有按玩家路线选择。
- 技能和装备通过内部 ORM 写入生成，不经过真实玩家入口的物品、状态和成本规则。
- 护院数量被直接覆盖。

**影响**

结果能够满足战力目标，但缺少可解释的发展历史；后续领域规则变化也容易与虚拟玩家路径漂移。

**最小安全修复**

区分两类写路径：

1. `bootstrap`：只用于创建时重建一份合理的历史快照。
2. `maintenance`：使用增量行为，复用领域资格、成本和结果规则。

维护路径不必复用 HTTP 或每个真实 Celery 倒计时，但必须复用相同的纯规则或 command 层。

**完成证明**

- 存量维护不会把全部建筑、科技、资源、护院直接重置到目标值。
- 每次维护动作有明确类型、前后值和原因日志。
- 失败在单个档案事务内回滚，维护序号不前进。

### M-03 玩家画像只影响总量，不影响内部组合

**现状**

`balanced / rich / dojo / guard / abandoned` 主要改变资源填充、门客总数、等级和护院总数。门客模板、装备、技能和具体兵种没有共享的长期发展计划。

**影响**

同一庄园中的门客、装备、技能和护院互相缺少关联，角色标签无法形成玩家可感知的流派。

**最小安全修复**

为每个档案生成并持久化版本化的 `BotDevelopmentPlan`，所有投影与维护使用同一计划。

**完成证明**

- 同一档案跨维护周期保持主力门客与主力兵种偏好。
- 不同 archetype 的组合分布有显著差异，但不会完全固定。
- 相同 seed 与版本能够重建相同计划。

### M-04 门客稀有度提升会改变门客身份

**现状**

维护会把已有 `Guest` 的 `template` 替换成更高稀有度模板，同时保留原 `Guest` 主键、技能和装备。

**影响**

- 门客身份会无招募过程地改变。
- 原技能、装备和新模板初始技能可能形成语义不一致。
- 历史记录无法解释该变化。

**最小安全修复**

- V2 禁止修改已有门客模板。
- 阵容提质通过新招募、培养现有门客或替换低优先级席位完成。
- 存量已发生过的模板替换不逆向回滚，避免破坏现有阵容。

**完成证明**

- V2 维护前后每个已有 `Guest.template_id` 保持不变。
- 新稀有门客具有独立招募或 bootstrap 来源。

### M-05 阵容、护院和建筑过度整齐

**现状**

- 门客逐个追向相同的最大目标等级。
- 护院总量平均分配给所有配置兵种。
- 核心建筑被投影到同一等级。
- 所有配置科技被投影到同一等级。

**影响**

真实玩家常见的主力、二队、替补、主修兵种和建筑优先级不会出现。

**最小安全修复**

- 使用稳定的投入权重生成核心、二队和替补层级。
- 每个档案选择主力兵种、次要兵种和侦察储备。
- 建筑和科技按画像优先级形成 0 至 3 级的合理落差。
- 科技等级必须钳制到各自 `max_level`。

**完成证明**

- 门客等级分布不是全员同级，且不超过配置步长和全局上限。
- 非均衡画像的主力兵种占比落在配置区间。
- 任何科技不超过模板上限。

### M-06 装备选择接近局部绝对最优且会删除旧装备

**现状**

装备候选按稀有度和简单战力排序，每个槽位倾向选择允许范围内最强模板；被替换的虚拟装备直接删除。

**影响**

- 门客定位、属性成长、技能公式和套装边际收益没有进入选择。
- 缺少换装惯性和旧装备下放行为。
- 同阶段档案容易形成过于相似的装备结构。

**最小安全修复**

使用角色适配、属性收益、套装收益、库存可得性和换装阈值共同评分；旧装备优先进入仓库或下放给次级门客。

**完成证明**

- 提升不足配置阈值时不换装。
- 同一套装备对不同门客的评分不同。
- 换装不会无条件删除原装备。

### M-07 技能选择只关注可学习与主动/被动比例

**现状**

技能候选满足属性要求后随机排序，高阶技能按概率优先，多技能时倾向一主动加若干被动。

**影响**

- 技能伤害公式、状态效果、被动配置与门客主属性没有充分关联。
- 同队控制效果和功能可能重复。
- 维护可以直接创建书本来源技能，却不消耗对应物品。

**最小安全修复**

根据属性匹配、技能公式、状态覆盖、已有技能协同、技能位机会成本和技能书可得性评分；维护每周期最多学习一个技能。

**完成证明**

- 不学习不满足条件或超过技能位上限的技能。
- 同分位下高适配技能的选择概率显著高于低适配技能。
- bootstrap 技能与维护期技能具有不同且明确的来源语义。

### M-08 真实玩家投影丢失相关性

**现状**

当前分别对建筑等级、门客数量、最高门客等级、护院总量和声望取分位数。这些指标可能来自不同真人档案。

**影响**

单项指标都合理，但组合不一定对应任何真实发展路线；门客内部等级、稀有度、装备和技能分布也没有被采样。

**最小安全修复**

以完整真人档案作为 anchor，在同地区、同声望段候选中按综合强度选择稳定分位档案，再对该档案进行受控扰动。

**完成证明**

- 投影的主要相关指标来自同一 anchor 或同一联合样本。
- 不采样 staff、superuser、虚拟玩家和过期活跃玩家。
- 相同 seed 和样本集合得到相同 anchor。

### M-09 维护随机流在成长阶段封顶后可能固化

**现状**

维护随机源主要由 `growth_seed + growth_stage` 构造。当成长阶段长期不变时，概率选择可能在多个维护周期重复相同结果。

**影响**

封顶档案会表现出固定的资源比例、技能抽样和装备选择，概率事件不再具有跨周期变化。

**最小安全修复**

引入持久化 `rng_version` 与 `maintenance_sequence`，按规范化的 `seed / engine_version / rng_version / plan_schema_version / policy_version / sequence / domain / discriminator` 派生独立随机子流。候选进入随机选择前必须按稳定业务 key 排序，禁止依赖数据库默认顺序、集合迭代顺序或 Python 内置 `hash()`。

**完成证明**

- 同一 sequence 可重放。
- sequence 变化后随机子流变化。
- 任务重试不会重复提交或跳过 sequence。
- 进程重启和支持范围内的 Python 升级不会改变同一 `rng_version` 的派生结果；算法变化必须提升版本并保留旧实现。

### M-10 测试与兼容入口边界不清

**现状**

大量测试直接导入巨型服务的下划线私有函数；旧 `virtual_player_population.py` 仍作为兼容重导出。

**影响**

移动实现会造成大面积无业务意义的测试破损，兼容层容易永久存在。

**最小安全修复**

测试按第 13.6 节的真实 owner 重组，覆盖 config/random/identity/population/backfill/strategy/projection/lifecycle/read adapters/profile store/bootstrap/maintenance/legacy/arena；只为真实公共入口保留兼容测试，并为兼容层规定删除条件。

**完成证明**

- 运行时代码无旧私有入口调用。
- 新测试直接导入真实所有者模块。
- 旧兼容模块的删除条件记录在兼容清单。

### M-11 BotProfile 写入所有权分散

**现状**

除巨型服务外，`gameplay/admin/bots.py` 会直接批量修改状态，`gameplay/services/arena/virtual_reserve.py` 会直接锁定并更新竞技场参与字段，掉落上限路径还会间接触发退休。人口重分段和超量退休也使用散落的 `QuerySet.update()`。

**影响**

同一档案的状态、调度、执行器路由和竞技场元数据没有统一写入门禁。新增 V2 约束后，任何一个旁路都可能绕过 engine stickiness、画像校验、sequence 或锁顺序。

**最小安全修复**

建立只管理 `BotProfile` 的 `profile_store.py`：应用服务仍决定业务动作，所有档案锁定和字段落库经由显式 store command。Admin、竞技场、人口和生命周期不得再直接更新 `BotProfile`。

**完成证明**

- 运行时代码 AST/符号门禁只允许 `profile_store.py` 和 Django migration 执行任何 `BotProfile` DML；覆盖 manager/QuerySet 的 `create / get_or_create / update_or_create / bulk_create / bulk_update / update / delete`，以及实例 `save / delete`，不能只检查 `BotProfile.objects.*`。
- 允许读取 `BotProfile` 的模块使用显式 allowlist；每个只读 owner 都有负例夹具，证明 QuerySet 写入、实例写入、别名导入和通用“代写” helper 均会使门禁失败。
- Admin、竞技场参与记录、退休、重激活、重分段和 sequence 更新均有专门契约测试。
- `profile_store.py` 不查询或修改门客、装备、技能、护院、建筑、科技和库存模型。

### M-12 竞技场虚拟后备再次形成应用服务热点

**现状**

`gameplay/services/arena/virtual_reserve.py` 已约 1392 行，同时处理需求对账、任务派发、候选租约、加速成长、创建预算、到期填充和参与历史；`virtual_backfill.py` 又同时包含可纯测的阵容评分与 ORM 补位写入。赛事启动还形成双向逻辑依赖：`core.py / coop_core.py / coop_lifecycle.py` 调用后备 demand reconcile，而 fill 路径再从 `virtual_reserve.py` 回调 `_start_tournament_locked / move_event_to_preparing_locked`。

**影响**

Maintenance V2 若直接接入该文件，会把虚拟玩家执行器、竞技场需求状态机和阵容物化继续绑在同一改动面上，也会让 `AcceleratedGrowthOutcome` 的新增结果难以安全传播。当前局部 import 虽避免模块加载时立即失败，却没有消除依赖环；拆分后若照搬，fill、赛事生命周期和 demand owner 将继续互相反向依赖。

**最小安全修复**

Gate B 只做行为等价拆分：需求状态、对账协调、后备池、周期扫描、到期填充、reference 读取、观测、阵容纯规则和竞技场保护查询分别拥有模块；`virtual_reserve.py` 保留有限公共门面。赛事从招募态进入运行/准备态的 locked primitive 下沉到现有 `lifecycle_helpers.py`，它不调用 demand/reconcile/pool/fill/scan；调用方先完成 demand reconcile，再调用生命周期 primitive。本轮不改变后备倍率、冷却、租约或填充算法；人数差异化是后续明确批准的版本化策略，`NO_ACTION` 的绝对租约上限属于 Gate E 新结果适配，不混入 Gate B 等价拆分。

**完成证明**

- 原竞技场后备测试按所有者迁移后行为等价。
- 纯阵容模块不导入 Django；保护查询只读；参与历史通过 `profile_store.py` 写入。
- `virtual_reserve_demand / reconcile / pool / fill / scan` 依赖图无强连通分量，且均不导入 `core.py / coop_core.py / coop_lifecycle.py`；`lifecycle_helpers.py` 也不反向导入任何 virtual reserve 模块。
- V2 的 `APPLIED / NO_ACTION / BUSY / PAUSED / INELIGIBLE` 结果均有明确租约处理，不落入宽泛 unknown 分支。

### M-13 虚拟门客战后恢复缺少可规划的治疗动作

**现状**

战败门客会进入 `INJURED` 并依赖全局被动回血扫描。V1 成长可能通过升级顺带把 HP 写满，却不会在同一领域动作内统一处理重伤状态；
V2 训练候选又会排除重伤门客。虚拟玩家当前没有使用自己库存药品治疗门客的明确动作。

**影响**

- 虚拟阵容恢复完全依赖全局扫描频率和队列积压，无法体现“优先救治主力”的稳定发展计划。
- 把回血夹在训练、升级或阵容读取路径中会产生隐藏写入，并可能绕过药品所有权、库存扣减和重伤解除规则。
- 直接免费回满会使虚拟玩家获得真人玩家没有的恢复能力，并改变 PVP 可用阵容而没有可审计成本。

**最小安全修复**

- Maintenance V2 增加 `guest_healing` 候选，硬过滤为本庄园、`IDLE/INJURED`、`current_hp < max_hp` 且仓库存在合法药品；
  优先重伤、投资层级更高和缺失 HP 比例更大的门客，同分时按稳定业务 key 与版本化随机上下文选择。
- `guests/services/health.py` 继续拥有治疗写入；从现有药品入口提取 actor-neutral quote/validate/apply locked primitive，统一解析药品
  `effect_payload.hp`，并保持 `Manor -> InventoryItem -> Guest` 锁序、单件消耗、20% 重伤解除阈值和整事务回滚。
- 被动回血扫描继续作为所有门客的时间恢复机制；`guest_healing` 是额外的主动库存动作，不批量结算所有门客，不创建药品，
  不借训练或升级免费回满。
- 该动作提交为 `MaintenanceResult(APPLIED, action_kind="guest_healing")`，占用本周期唯一同步动作；因为只恢复既有 HP，
  不消费永久强度预算、不更新 `last_strength_increase_at`，但提交后必须重新评估竞技场/踢馆可用阵容。

**完成证明**

- 真人药品入口与 Bot 治疗复用同一资格、治疗量、库存消费、重伤解除和错误语义。
- 同一药品或门客被两个 worker 竞争时最多一个动作成功，失败方不重复扣药、不覆盖 HP、不推进 sequence。
- 无药、满血、非本庄园、工作/出征/竞技状态门客均不生成合法候选；业务不可执行返回结构化原因。
- 治疗提交不改变等级、属性、模板、永久强度预算或 `last_strength_increase_at`，回滚时药品、HP、状态和 sequence 全部恢复。

### M-14 竞技场虚拟补位快照继承庄园残血

**现状**

`gameplay/services/arena/snapshots.py::build_entry_guest_snapshot()` 会把实时 `Guest.current_hp` 钳制后写入通用参赛快照；
`virtual_reserve_pool.py` 只过滤 `IDLE` 门客，不保证其满血，`virtual_backfill.py` 又会把选中的 snapshot 原样物化。因此，当前普通赛和
共斗的虚拟补位门客可能以庄园中的残血值参赛。

**影响**

- 这不满足“竞技场虚拟补位统一满血参赛”的产品契约，同一 Bot 的可用阵容会受竞技场外被动回血扫描时点影响。
- 若直接把共享 `build_entry_guest_snapshot()` 改为总是满血，会同时改变真人玩家报名快照，破坏真人报名时生命值语义。
- 若为补位先治疗真实 `Guest`，会制造无药品成本的领域写入，并把 Entry 物化与 Maintenance、库存及重伤状态错误耦合。

**最小安全修复**

- 保持共享 `build_entry_guest_snapshot()` 的现有语义；由纯 `arena/virtual_lineups.py` 在阵容选择输出边界复制虚拟 lineup snapshot，
  校验 `max_hp` 为正整数后只把副本的 `current_hp` 规范为 `max_hp`。输入 snapshot、ORM `Guest` 和选择 seed 均不得改变。
- `arena/virtual_backfill.py` 在已持有赛事/候选锁、写入 `ArenaEntryGuest/ArenaCoopEntryGuest` 前再次断言每个虚拟 snapshot
  满足 `current_hp == max_hp`；任何不合法或未规范化 snapshot 都 fail closed，并回滚本次填充事务。
- 该规则同时覆盖普通赛、共斗、V1 和 V2 虚拟档案，只改变 `source=VIRTUAL` 的 Entry snapshot；候选仍须满足既有 `IDLE`
  资格，目标强度、阵容组合、随机序列和租约算法保持不变。
- 快照满血不写回 `Guest.current_hp`，不消费药品，不解除 `INJURED` 或其他庄园状态，也不替代 Maintenance `guest_healing`
  与全局被动回血。真人 `source=PLAYER` 报名快照继续保留报名时的合法实时 HP。

**完成证明**

- 纯规则测试证明残血虚拟 snapshot 输出为 `current_hp == max_hp`、输入不变、人数选择仍在合法上限内且 power 区间不变；非法 `max_hp` 明确拒绝。
- 普通赛和共斗服务测试逐条断言已物化虚拟 snapshot 满血，同时原 `Guest.current_hp`、状态和库存逐值不变。
- 直接绕过 `virtual_lineups.py` 向 locked write primitive 传入残血 snapshot 时整笔填充回滚，不产生部分 Entry 或租约消费。
- 真人报名回归测试证明共享 snapshot 仍保留并钳制实时 HP，不被虚拟补位规则改成满血。

---

## 4. 必须保持的业务不变量

重构与行为优化均不得破坏以下契约。

### 4.1 状态能力

| 状态 | 地图可见 | 可攻击 | 自动维护 | 可参加竞技场 | 可重新激活 |
|------|----------|--------|----------|--------------|------------|
| ACTIVE | 是 | 是 | 是 | 是 | 否 |
| SLOWING | 是 | 是，成长放缓 | 是 | 是 | 否 |
| ABANDONED | 是 | 是 | 仅生命周期维护 | 否 | 是 |
| RETIRED | 是 | 是 | 否 | 否 | 是 |
| STALE | 否 | 否 | 否 | 否 | 否 |

### 4.2 人口与并发

- 动态全局硬上限不得被创建、重新激活或竞技场补量绕过。
- 地区和目标声望段人口计划继续按真实玩家活跃量和地图/竞技场显式需求计算；`veteran` 及以上空高段在没有这些信号时
  目标供给必须为 0，不能仅因存在配置区间而预建 Bot。
- Gate D1 启用的 V2 声望段必须连续、互不重叠并完整覆盖 `[0, +∞)`：`[0,500)`、`[500,2000)`、
  `[2000,8000)`、`[8000,30000)`、`[30000,60000)`、`[60000,120000)`、`[120000,240000)`、
  `[240000,+∞)`；当前 V1 五档配置在 Gate D1 前保持原样。
- 可攻击供给只能按档案的 `current_prestige_band` 计数；物化中的 `target_prestige_band` 只能抑制同段重复创建。低段供给
  不得抵扣高段缺口，重新激活必须同段，禁止为补人口瞬间修改 Bot 声望或跨段复活。
- 公共真人玩家提交声望跨段后，必须在 post-commit 异步、合并且幂等地重算旧段和新段；一次跨段只触发供给判断，
  不等同于固定创建一个 Bot，也不得顺带执行 Maintenance。
- Bot 通过正常领域动作跨段时使用同一 post-commit transport；`profile_store.py` 先按持久化 Manor 声望幂等同步
  `current_prestige_band`，再重算旧段和新段，不改历史 `target_prestige_band`。selector 和人口只读快照不得顺手修档案。
- 人口滚动锁必须保持所有权校验、续租和 fail-closed 语义。
- 单个档案维护继续使用事务和行锁，跳过正在被其他 worker 处理的记录。
- 竞技场后备租约和正在参赛的档案不能被休眠或重复租用。
- `BotProfile.engine_version` 是执行器路由的唯一事实来源；V2 档案不得因开关关闭或 policy rollout 调整而重新进入 V1 写路径。
- 除 schema migration 外，运行时代码只有 `profile_store.py` 可以直接写 `BotProfile`；Admin、竞技场、人口、生命周期和修复命令都通过它落库。
- Maintenance V2 首版每次事务最多提交一个同步发展动作，生命周期迁移发生时不再执行发展动作。

### 4.3 经济与掉落

- 单虚拟玩家每日战利品预算继续生效。
- 真人玩家从虚拟玩家获取资源的每日上限继续生效。
- 稀有和强力物品全局每日上限继续使用事务计数并支持失败回滚。
- 自然化不能扩大无上限资源或高价值物品供给。
- 压缩动作可以省略真实等待时间和用户通知，但不能省略资格、成本、库存消费、资源事件或结果约束。
- 掉落额度计算不得写 `BotProfile`；预算耗尽只生成退休建议，Raid 成功提交后再由显式幂等命令处理。
- Maintenance V2 不再直接增加声望；声望只能来自 Bootstrap 历史快照或领域动作已有的资源/声望事件。

### 4.4 可重放与兼容

- 相同 seed、engine version、RNG version、plan schema、policy version、policy checksum、sequence 和输入快照必须得到相同计划。
- V2 发展随机派生使用带命名字段的规范化编码；候选先按稳定业务 key 排序，独立 policy rollout 使用同一版本化摘要工具，不依赖 Python 内置 `hash()`；当前方案不实现 engine enrollment bucket。竞技场基础抽样继续使用第 5.5 节的确定性种子，新增人数目标使用独立、可持久化的 roster policy。
- 创建坐标冲突重试、名称唯一性和历史时间回填继续成立。
- 现有管理命令和 Celery 任务名称保持不变；注册触发新增一个只执行人口重算的专用任务，不复用会先运行
  `maintain_due_virtual_players()` 的现有 `roll_virtual_players_task`。
- 地图补量 API 返回契约保持不变。
- 虚拟玩家继续从真实玩家排行榜和真实玩家样本中排除。
- V2 RNG 版本、画像或已发布策略记录损坏时必须 fail closed 并报警，不能静默回退 V1；禁止重新激活和新增竞技场租约，只允许释放租约、安全退休和有界延后调度。修复只能通过与损坏类型对应的显式、可重放路径完成。
- 已发布 `policy_version` 的规范化 payload 与 checksum 一一对应；进程重启和 YAML 热刷新不能改变该映射。

### 4.5 监牢与门客恢复

- 每个日历日执行一次虚拟监牢清理，范围只包含俘获方存在 `BotProfile` 的 `HELD` 囚犯；真人监牢不受影响。
- “清空”严格表示 `HELD -> RELEASED`，不删除历史记录、不自动招募、不返还或重建原门客，也不改变既有俘获和装备处置规则。
- 日清理独立于 Maintenance routing、sequence 和强度预算；V2 暂停或 cutover 不能停止该公平性 housekeeping。
- 门客主动治疗一次只处理一名门客并消耗庄园已有的一件合法药品，治疗、库存扣减和重伤状态变化必须同事务提交。
- 主动治疗占用 Maintenance V2 本周期唯一同步动作，但不属于永久强度增长；被动回血任务继续作为无药时的恢复兜底。
- 本轮不接入真人招募候选或重写囚犯处置动作，不让虚拟玩家自动招募监牢囚犯；竞技场数量阶段仅允许受限黑/灰模板扩容。

### 4.6 竞技场虚拟补位生命值

- 普通赛和共斗中，每个新物化的虚拟补位门客 snapshot 必须满足 `current_hp == max_hp`；V1/V2 虚拟档案使用同一规则。
- 满血仅属于本场不可变 Entry snapshot，不写回庄园 `Guest`、不扣药、不改变重伤/空闲状态，也不推进 Maintenance sequence。
- 满血规范化不得让非 `IDLE` 门客获得补位资格，不得改变阵容组合、目标 power、随机序列、租约数量或参与历史语义。
- 真人报名 snapshot 继续保留报名时的合法实时 HP；虚拟补位规则不得下沉到共享 snapshot builder 后影响真人入口。

---

## 5. 目标架构

### 5.1 模块布局

```text
gameplay/services/
├── virtual_players.py                    # 有限公共兼容门面；显式 __all__
├── virtual_player_state_policy.py        # 稳定公共状态能力矩阵
├── virtual_player_loot_limits.py         # 只读额度查询与纯 BotLootClampDecision
├── virtual_player_core/
│   ├── __init__.py                       # 不做宽泛重导出
│   ├── contracts.py                      # 纯 plan/snapshot/intent/result 契约
│   ├── config.py                         # YAML/settings 加载、缓存和严格解析接线
│   ├── policy_registry.py                # 已发布 policy 的不可变注册与读取
│   ├── random_context.py                 # 持久化版本的摘要派生、随机子流和 policy bucket
│   ├── identity.py                       # 纯名称风格、候选及稳定重试身份字段
│   ├── population.py                     # 已存在：纯人口规划
│   ├── population_runtime.py             # 容量锁、计划执行、重分段和创建/重激活编排
│   ├── backfill.py                       # 地图补量需求记录、读取、确认和查询入口
│   ├── strategy.py                       # BotDevelopmentPlan 生成、解析和 schema 升级
│   ├── projection.py                     # V2 纯目标、评分、Blueprint 与 Intent 生成
│   ├── lifecycle.py                      # 纯生命周期迁移与退休原因决策
│   ├── catalog.py                        # 只读模板目录快照；评分循环不访问 ORM
│   ├── reference_snapshots.py            # 只读真人联合快照、匿名摘要与 fallback
│   ├── selectors.py                      # 只读 Bot 当前资产和人口快照
│   ├── profile_store.py                  # 唯一 BotProfile 创建、锁定、路由和字段写入 owner
│   ├── economy.py                        # 纯强制结算额度计算；不写资源或预算
│   ├── external_reconciliation.py         # 玩家驱动强度/声望持久 intent、claim 与恢复编排
│   ├── inventory_budget.py               # BotInventoryDailyCounter 预留/释放事务边界
│   ├── bootstrap.py                      # V1/V2 路由、身份创建和历史快照物化
│   ├── maintenance.py                    # V1/V2 路由、单动作事务和结构化结果
│   ├── safety_monitor.py                 # 消费闭合指标窗口并请求幂等 routing 暂停
│   └── legacy/                           # 仅 engine_version=1 使用；禁止新增 V2 逻辑
│       ├── __init__.py                   # 不重导出
│       ├── projection.py                 # V1 建筑/资源/科技/护院批量投影
│       ├── roster.py                     # V1 门客/技能/装备投影
│       └── inventory.py                  # V1 库存池与补充算法
└── arena/
    ├── lifecycle_helpers.py              # 共享赛事启动/准备 locked primitive；不依赖后备模块
    ├── match_store.py                    # ArenaMatch 调度槽位的单一创建 owner
    ├── virtual_reserve.py                # 有限竞技场后备兼容门面
    ├── virtual_reserve_demand.py         # demand state 创建、对账、关闭和任务派发
    ├── virtual_reserve_reconcile.py      # demand state 与 pool 迁移的事务协调
    ├── virtual_reserve_pool.py           # 租约、成长、创建预算和补池
    ├── virtual_reserve_fill.py           # ready 选择、锁定和参与历史提交
    ├── virtual_reserve_scan.py           # 周期候选扫描与 reconcile/pool/fill 编排
    ├── virtual_reserve_references.py     # 真人 reference entry/snapshot 只读 adapter
    ├── virtual_reserve_observability.py  # demand/member 结构化事件
    ├── virtual_lineups.py                 # 纯阵容评分、稳定选择与虚拟满血快照规范化
    ├── virtual_backfill.py               # 普通赛/共斗 Entry 物化与满血断言
    └── virtual_protection.py             # 只读参赛/租约保护查询
```

不建立覆盖所有领域 ORM 的通用 repository。门客、装备、技能、护院、建筑和科技写入继续由各自现有 service/command 拥有；`profile_store.py` 只管理 `BotProfile`。`legacy/` 和竞技场拆分都有真实责任边界，不是按函数数量拆文件：前者隔离重构期间仍需兼容的 V1 ORM 投影，后者隔离三个独立事务状态机。`legacy/` 的删除条件是当前非生产环境中可执行或可重新激活的 V1 档案为零、V2 验收稳定且兼容门禁完成；这不是生产观察期。

### 5.2 依赖方向

```text
tasks / views / admin / arena / raid
                  |
                  v
public facade / application services
                  |
       +----------+-----------+
       |          |           |
       v          v           v
pure planning  read adapters  profile_store -> BotProfile only
and contracts  / snapshots
       |          |
       +----------+
                  v
       existing domain commands
                  |
                  v
      owned aggregate models/events
```

竞技场内部再遵守以下单向子图，禁止依靠函数内 import 掩盖反向依赖：

```text
core / coop_core -----------> virtual_reserve_demand + virtual_reserve_reconcile
core / coop_core -----------> lifecycle_helpers
virtual_reserve_reconcile --> virtual_reserve_demand + virtual_reserve_pool
virtual_reserve_fill -------> virtual_reserve_reconcile + virtual_reserve_pool
virtual_reserve_fill -------> lifecycle_helpers + virtual_backfill / virtual_lineups / profile_store
virtual_reserve_scan -------> virtual_reserve_reconcile + virtual_reserve_pool + virtual_reserve_fill
virtual_reserve_pool -------> virtual_reserve_references + maintenance public command
```

`demand` 只拥有 demand state，不反向调用 pool/fill；`reconcile` 协调 demand state 与 pool 迁移；`scan` 是最外层周期编排。`core / coop_core` 与 fill 都可以调用 `lifecycle_helpers`，但 `lifecycle_helpers` 不回调 demand/pool/fill。正常报名启动路径由 core 先 reconcile demand，再调用赛事转换 primitive；虚拟补位路径由 fill 在同一赛事事务中完成 reconcile、满血 snapshot 规范化、Entry 物化后调用同一个 primitive。`coop_lifecycle.py` 不再导入 virtual reserve 模块。

约束：

- `contracts / random_context / identity / strategy / projection / lifecycle / population / arena.virtual_lineups` 不导入 Django。
- `selectors / catalog / reference_snapshots / arena.virtual_protection` 只读，不包含 `save / update / delete / create` 或隐藏副作用。
- `BotProfile` 的初始只读 allowlist 为 `selectors.py`、`arena/virtual_protection.py`、`virtual_player_loot_limits.py`、`raid/utils.py` 和只读 Admin；Gate A 若发现必要 reader，必须逐文件登记读取目的后才能加入，allowlist 不授予任何 DML 权限。
- `profile_store` 是运行时代码唯一 `BotProfile` 写 owner；它不导入 planner、不决定业务策略、不提供任意字段字典的通用 update 代理，也不写入门客、装备、技能、护院、建筑或科技。
- CI 写入门禁解析 import alias、QuerySet chain 和实例来源，覆盖 manager/QuerySet create/update/delete/bulk/upsert 与实例 `save/delete`；Django migration 仅作为非运行时例外单独登记。
- `bootstrap / maintenance / population_runtime` 拥有事务编排，不复制底层规则。
- `tasks / views / admin` 只做输入输出映射。
- 基础领域服务不反向依赖虚拟玩家模块。
- 各领域 command 明确拥有资格、成本、锁、资源事件和结果写入；虚拟玩家编排器只能组合这些边界。
- `legacy/*` 可以导入 ORM，但只有 V1 路由可调用；V2 模块和领域 command 禁止反向依赖它。
- `virtual_player_loot_limits.py` 只返回决策，不调用 `profile_store`、生命周期命令或竞技场服务。
- `arena.virtual_lineups` 只规范化虚拟补位 snapshot 副本；共享 `arena.snapshots` 保持 actor-neutral，`arena.virtual_backfill`
  只在物化边界验证满血契约，不得借此写真实 `Guest` 或库存。

### 5.3 公共入口清单与归属

阶段 0 必须先用 import characterization test 冻结以下运行时兼容面；实现移动期间 `gameplay.services.virtual_players` 通过显式 `__all__` 重导出，直到调用方逐一迁移完成。

| 当前公共入口 | 目标所有者 |
|--------------|------------|
| `load_virtual_player_config / clear_virtual_player_config_cache` | `config.py` |
| `BotProjectionConfig` | `contracts.py`，标记为 V1 兼容契约，随 V1 退场 |
| `AcceleratedGrowthOutcome / PopulationMutationStatus` | `contracts.py` |
| 新内部契约 `MaintenanceTrigger / MaintenanceScheduleDisposition / MaintenanceResult` | `contracts.py`；纯枚举/结果，不携带 ORM profile |
| `PopulationMutationResult` | `population_runtime.py`；它携带 ORM profile，不能伪装成纯领域契约 |
| `virtual_player_prestige_bands / get_virtual_player_capacity` | `population_runtime.py` |
| `plan_virtual_player_population / roll_virtual_player_population` | `population_runtime.py` |
| `request_virtual_player_backfill_for_region_search` | `backfill.py` |
| `create_virtual_player` | `bootstrap.py` |
| `create_virtual_player_with_capacity / create_virtual_players_for_band` | `population_runtime.py` 调用 `bootstrap.py` |
| `maintain_due_virtual_players / accelerate_virtual_player_growth` | `maintenance.py` |
| 新内部入口 `advance_virtual_player_for_arena` | `maintenance.py`；返回完整结构化结果，竞技场不再依赖有损枚举适配 |
| `reactivate_virtual_player_profile` | `maintenance.py` 调用 `lifecycle.py` 与 `profile_store.py` |
| `reactivate_retired_virtual_player_with_capacity` | `population_runtime.py` 调用 `lifecycle.py` 与 `profile_store.py` |
| `retire_virtual_player_if_unprotected` | `maintenance.py` 调用 `lifecycle.py` 与 `profile_store.py` |

`record_virtual_player_backfill_demand`、`consume_virtual_player_backfill_demands` 以及所有下划线函数只视为内部实现；现有测试迁移到真实所有者模块，不把测试导入反向扩大为永久公共 API。阶段 0 若发现新的运行时调用方，必须先补入清单再移动实现。

竞技场后备兼容面单独冻结，避免把当前跨文件调用误当成全部都要永久保留的公共 API：

| 当前入口簇 | 目标所有者与兼容策略 |
|------------|----------------------|
| `queue_virtual_reserve_reconcile` | `virtual_reserve_demand.py`；仅已确认的公共入口由 `virtual_reserve.py` 暂时重导出 |
| `reconcile_tournament_demand / reconcile_coop_demand`、对应 locked primitive | `virtual_reserve_reconcile.py`；`core.py / coop_core.py` 直接调用真实 locked owner，`coop_lifecycle.py` 不依赖后备模块 |
| `scan_virtual_reserve_demands` | `virtual_reserve_scan.py`；Celery task 直接调用真实 owner，门面只提供兼容重导出 |
| `replenish_virtual_reserve / grow_due_virtual_reserves / create_due_virtual_reserve_profiles` | `virtual_reserve_pool.py`；Celery task 直接调用真实 owner，兼容门面只服务已确认外部消费者 |
| `fill_due_tournament_reserve / fill_due_coop_reserve` | `virtual_reserve_fill.py`；负责一次完整填充事务，兼容门面不承载实现 |
| `ReserveReplenishmentResult` 及 pool 内部 target DTO | 归 `virtual_reserve_pool.py`；只有阶段 0 证明存在公共类型消费者时才重导出，测试导入本身不构成公共契约 |
| `_start_tournament_locked / move_event_to_preparing_locked` | 实现收敛为 `lifecycle_helpers.py` 的 package-internal `start_tournament_locked / move_coop_event_to_preparing_locked`；primitive 只校验并提交赛事转换，不调用 demand/pool/fill |

`start_due_virtual_backfill_tournaments / start_due_virtual_backfill_coop_events` 当前只被测试直接导入。阶段 0 若确认没有外部消费者，测试改用 `virtual_reserve_fill.py` 后删除这两个位于赛事 core 的兼容包装；若存在外部消费者，则只保留无业务逻辑的限时适配并登记退场条件。

普通赛转换 primitive 连同 replay metadata 初始化和首轮调度接线一起下沉；它不得通过参数或局部 import 再向 `core.py` 取得 callback。后续轮次仍由赛事生命周期 owner 调度，但复用同一底层 helper，避免为虚拟 fill 复制启动算法。

### 5.4 现有核心文件处置

拆分依据是是否同时承载多个独立状态机、写 aggregate 或算法/接线职责，而不是单看行数。配置 schema、稳定状态能力和 Celery transport 维持单入口，避免把一个一致性契约拆成多个会漂移的公开面。

| 现有文件 | 决策 | 目标职责与修改 |
|----------|------|----------------|
| `gameplay/services/virtual_players.py` | 拆分后保留 | 只保留第 5.3 节兼容入口、显式 `__all__` 和短期适配；禁止 ORM 查询、事务、锁、配置实现和领域算法 |
| `gameplay/services/virtual_player_core/population.py` | 原样保留 | 继续拥有纯 `PopulationCell / PopulationPlan / plan_population_cells`；不接 ORM 快照和执行逻辑 |
| `gameplay/services/virtual_player_core/__init__.py` | 保留 | 包标记，不做聚合重导出 |
| `gameplay/services/virtual_player_rules.py` | 拆分后退场 | 生命周期函数移到 `lifecycle.py`；V1 quantile/persona/bounded projection 移到 `legacy/projection.py`；仓内和已确认外部调用清零后删除 |
| `gameplay/services/virtual_player_state_policy.py` | 保留并收纯 | 继续作为稳定状态能力入口；改用稳定字符串/纯契约定义，不导入 ORM model，契约测试与 `BotProfile.State` choices 对齐 |
| `gameplay/services/virtual_player_population.py` | 兼容期保留后删除 | 只重导出现有纯 planner；按 `docs/compatibility_inventory_2026-03.md` 登记，调用方清零且兼容窗口结束后删除 |
| `gameplay/services/virtual_player_loot_limits.py` | 修改，不拆 | 只读额度查询和纯裁剪决策；删除退休写副作用，返回 `BotLootClampDecision` |
| `gameplay/services/arena/virtual_reserve.py` | 拆分后保留门面 | 公共入口与显式导出留在门面；需求、pool、fill 分别迁出，不再直接写 `BotProfile` |
| `gameplay/services/arena/virtual_backfill.py` | 按责任拆分 | 纯阵容算法移到 `virtual_lineups.py`；本文件只保留在已持有赛事锁时物化普通赛/共斗 Entry 的写命令 |
| `common/constants/virtual_players.py` | 保留 | 只保存跨层稳定 taxonomy；不放运行开关、policy payload、ORM 或随机状态 |

`virtual_players.py` 的现有私有实现按函数簇迁移，不能凭移动方便随意归类：

| 当前行段/函数簇 | 目标 owner |
|-----------------|------------|
| 83-112：公开 dataclass/enum | 纯枚举与 V1 projection 配置到 `contracts.py`；ORM-bearing result 留 `population_runtime.py` |
| 113-598：默认配置、路径、合并、范围读取 | 配置加载到 `config.py`；只服务 V1 的 range/projection helper 到 `legacy/projection.py` |
| 599-700：User/庄园名称/坐标重试 | 纯候选到 `identity.py`；User/Manor 创建与冲突事务到 `bootstrap.py` |
| 701-819：建筑/资源投影与 band/filter helper | V1 写投影到 `legacy/projection.py`；人口 band/filter 到 `population_runtime.py` 或纯 population helper |
| 820-1185：补量需求、重激活与 search request | 需求存储/API 到 `backfill.py`；迁移决策到 `lifecycle.py`；容量协调到 `population_runtime.py`；落库到 `profile_store.py` |
| 1186-1455：人口快照、容量、竞技场保护、重分段 | 只读快照到 `selectors.py`；保护查询到 `arena/virtual_protection.py`；锁与执行到 `population_runtime.py`；档案 update 到 `profile_store.py` |
| 1456-1708：真人 V1 projection、persona、按 band 创建 | V1 采样/投影到 `legacy/projection.py`；批量创建编排到 `population_runtime.py`；V2 不复用该独立分位数实现 |
| 1709-2258：科技、技能、装备、门客、护院投影 | V1 科技/护院到 `legacy/projection.py`；门客/技能/装备到 `legacy/roster.py` |
| 2259-2639：库存池、每日额度、历史时间 | V1 候选/补充到 `legacy/inventory.py`；计数器事务到 `inventory_budget.py`；历史时间到 `bootstrap.py` |
| 2640-2845：生命周期日期、创建与容量包装 | 日期决策到 `lifecycle.py`；创建到 `bootstrap.py`；容量包装到 `population_runtime.py` |
| 2850-3343：维护、加速、退休探测和 due 扫描 | 应用事务到 `maintenance.py`；V1 全量投影到 `legacy/*`；纯状态决策到 `lifecycle.py`；档案写入到 `profile_store.py` |
| 3344-3887：超量退休、人口计划、补量消费和 roll lock | `population_runtime.py` 编排，`backfill.py` 管需求，`arena/virtual_protection.py` 提供保护读，`profile_store.py` 落档案状态 |

行号是 2026-07-27 基线定位，只用于防止函数簇漏迁；实施后以 symbol owner 和 import/AST 契约为准。

竞技场两个现有热点按 symbol 簇迁移，不能只创建空壳文件后继续让门面持有实现：

| 现有 symbol 簇 | 目标 owner 与处理 |
|-----------------|-------------------|
| `virtual_reserve.py` 的 demand mode、reserve target、close/upsert 和 task dispatch | `virtual_reserve_demand.py`；只写 `ArenaVirtualDemand` state，state locked primitive 要求调用方已持有对应赛事锁 |
| `virtual_reserve.py` 的 tournament/coop reconcile application command | `virtual_reserve_reconcile.py`；协调 demand state 与 pool member 迁移，不直接承载 DML |
| `virtual_reserve.py` 的周期候选扫描 | `virtual_reserve_scan.py`；只编排 reconcile/pool/fill，不拥有底层状态迁移 |
| `virtual_reserve.py` 的 member reevaluate/candidate/lease/trim、`replenish_* / grow_* / create_*` 和 growth target | `virtual_reserve_pool.py`；拥有租约及训练态，外部取消/无效快照统一调用显式 release command；Gate E 在此加入基于 `created_at` 的 `NO_ACTION` 绝对期限 |
| `virtual_reserve.py` 的 stable member order、ready profile lock、deferred/completion、`fill_due_*` | `virtual_reserve_fill.py`；持有赛事填充事务，调用 `virtual_backfill.py` 物化 Entry，并通过 `profile_store.py` 提交参与历史 |
| `virtual_reserve.py` 的公共名字 | 实现迁走后仅按第 5.3 节从真实 owner 显式重导出；私有异常、日志 helper 和 ORM 实现不留在门面 |
| `virtual_backfill.py` 的 snapshot power、组合抽样、lineup 评分与选择 | 改造成 `virtual_lineups.py` 的纯函数：输入不可变 snapshot 和显式 RNG/seed context，不接收 `BotProfile` 或 QuerySet |
| `virtual_backfill.py` 的真人 reference entry/snapshot 读取 | 迁到 `virtual_reserve_references.py` 的只读目标快照 adapter；未被使用的 `ArenaReferenceTarget` 经 characterization 确认为死代码后删除 |
| `virtual_backfill.py` 的 Bot candidate 查询、排除集、锁定及锁内重验 | 迁到 `virtual_reserve_fill.py`；不再由 Entry 物化文件选择参赛者 |
| `backfill_tournament_locked / backfill_coop_event_locked` | 拆成 `virtual_backfill.py` 内要求赛事已锁、候选已锁、lineup 已验证的 Entry 写 primitive；高层选择和完成状态机归 `virtual_reserve_fill.py` |
| 虚拟补位 snapshot 的参赛 HP | `virtual_lineups.py` 纯复制并规范为 `current_hp=max_hp`；`virtual_backfill.py` 在写入前 fail-closed 断言；共享 `snapshots.py` 与真人报名路径不改变 |
| `core.py::_start_tournament_locked / coop_lifecycle.py::move_event_to_preparing_locked` | 赛事状态转换实现下沉到 `lifecycle_helpers.py`；demand reconcile 留在上层调用者且必须先发生，旧私有位置不保留反向 wrapper |

### 5.5 新核心文件责任

| 新文件 | 唯一职责 | 明确禁止 |
|--------|----------|----------|
| `virtual_player_core/contracts.py` | 纯计划、快照、intent、维护结果和枚举 | ORM model、QuerySet、settings、文件 IO；ORM-bearing `PopulationMutationResult` 不放这里 |
| `virtual_player_core/config.py` | 加载 YAML/settings、缓存、调用严格 validator、构造只读配置对象 | 查询业务模型、发布/覆盖 policy、吞掉非法配置 |
| `virtual_player_core/policy_registry.py` | 创建/读取不可变 `BotPolicyRelease`、checksum 与兼容性校验 | 评分、rollout 决策、原地 update/delete 已发布 payload |
| `virtual_player_core/random_context.py` | canonical digest、版本化子流、稳定 policy bucket | Python 内置 `hash()`、未持久化全局 RNG、未知 domain 静默接受 |
| `virtual_player_core/identity.py` | 名称风格、候选和冲突重试所需纯身份字段 | 创建 User/Manor、查重、坐标落库 |
| `virtual_player_core/population_runtime.py` | 人口快照编排、容量锁、roll、重分段、创建/重激活协调 | 保存补量需求细节、生成 V2 画像、直接写非 BotProfile 领域资产 |
| `virtual_player_core/backfill.py` | 地图补量需求的记录、读取、确认和 search application entry | 在 HTTP 请求内创建 Bot、执行整个人口 roll |
| `virtual_player_core/strategy.py` | `BotDevelopmentPlan` 生成、严格解析和 schema 升级 | ORM、运行时写入、可热调的 policy 权重所有权 |
| `virtual_player_core/projection.py` | V2 纯评分、Blueprint/Intent 生成和硬约束过滤 | ORM、V1 批量投影、事务和领域 command 调用 |
| `virtual_player_core/lifecycle.py` | 纯状态迁移、退休理由和下一调度决策 | 查询 Raid/Arena、保存 profile、执行资源动作 |
| `virtual_player_core/catalog.py` | 批量构造不可变门客/装备/技能/护院/建筑/科技目录 | 在评分循环中查询 ORM、创建缺失模板 |
| `virtual_player_core/reference_snapshots.py` | 批量读取匿名真人联合快照、fallback 和摘要 | 写真人数据、记录用户 ID、返回 ORM 实例给 planner |
| `virtual_player_core/selectors.py` | 批量读取 Bot 当前资产、状态和人口快照 | `save/update/delete/create`、隐式 refresh/finalize |
| `virtual_player_core/profile_store.py` | `BotProfile` 创建、行锁、engine 路由校验、强度/强制结算预算和字段集受限的明确写命令；提供 Gate E 的精确 V1 count query | 业务评分、写其他 aggregate、调用 HTTP/消息/Celery、接受任意字段映射或暴露通用 `save/update` 代理 |
| `virtual_player_core/economy.py` | 纯计算每周期与 UTC 日强制资源结算剩余额度 | ORM、重置持久预算、写 Manor/ResourceEvent |
| `virtual_player_core/external_reconciliation.py` | 窄化的玩家驱动强度/声望 intent 写入、claim、幂等处理和 pending 恢复扫描 | 回滚玩家已提交结果、复用 H-01 非持久语义、在 selector 中修复档案 |
| `virtual_player_core/inventory_budget.py` | `BotInventoryDailyCounter` 锁定、预留、释放和回滚语义 | 选择物品、写 `InventoryItem`、吞掉额度冲突 |
| `virtual_player_core/bootstrap.py` | V1/V2 路由、锁外 Blueprint 与锁内历史物化事务 | 被存量维护调用、伪造逐条异步历史、复制 V2 评分公式 |
| `virtual_player_core/maintenance.py` | V1/V2 路由、单档案事务、一个动作、sequence 和结果日志 | 直接实现领域成本/资格、事务外发展派发、V2 回退 V1 |
| `virtual_player_core/safety_metrics.py` | 向当前环境的持久事件账本写幂等事件、冻结已闭合窗口并实现 provider 读取协议 | 业务评分、routing 切换、进程内 fallback、以累计 task counter 冒充闭合窗口 |
| `virtual_player_core/safety_monitor.py` | 从共享观测 provider 读取已闭合窗口、按冻结阈值判定并请求 routing CAS | 自建进程内计数真相、修改业务档案、静默放宽阈值 |
| `virtual_player_core/legacy/*` | 冻结并隔离 V1 projection/roster/inventory 写行为 | 新增 V2 分支、被领域服务依赖、没有退场条件的继续扩张 |
| `arena/virtual_reserve_demand.py` | `ArenaVirtualDemand` state 创建/对账/关闭和 reconcile 任务派发 | 调用 pool/fill、周期扫描、创建/训练 Bot、选择 lineup、物化 Entry、写 `BotProfile` |
| `arena/virtual_reserve_reconcile.py` | 在赛事事务中协调 demand state transition、member 释放/重验和 reconcile 观测 | 承载 demand/member 直接 DML、扫描候选、填充赛事或反向调用赛事 core |
| `arena/virtual_reserve_pool.py` | `ArenaVirtualReserveMember` 租约/重验/释放、绝对期限、成长协调、创建预算及 pool 进度 | 完成赛事填充、写 Entry、直接改参与历史、复制 Maintenance 规则 |
| `arena/virtual_reserve_fill.py` | ready member 稳定选择、赛事/Profile 锁、Entry 物化协调、租约消费和 fill 结果事务 | 创建/训练 Bot、计算发展动作、在 `profile_store.py` 之外写 `BotProfile` |
| `arena/virtual_reserve_scan.py` | 周期候选扫描并按顺序编排 reconcile、pool 和 fill | 拥有底层状态写、反向被 demand/pool/fill 调用、解释维护结果 |
| `arena/virtual_reserve_references.py` | 只读真人 reference entry/snapshot 选择与复制 | 写 Entry/Profile/demand/member、返回可修改的共享 snapshot |
| `arena/virtual_reserve_observability.py` | 读取 demand/member 计数并输出结构化事件 | 改写状态、吞异常或参与事务控制 |
| `arena/virtual_lineups.py` | 基于不可变 snapshot 和显式随机上下文的纯阵容评分、组合、稳定选择及虚拟补位满血副本规范化 | ORM、模板查询、Entry 写入、修改输入 snapshot、模块级或隐式随机状态 |
| `arena/virtual_backfill.py` | 在赛事和候选已锁且 lineup 已验证、全员满血时物化普通赛/共斗虚拟 Entry | 查询/选择/锁定 Bot、修改真实 Guest/库存、计算目标阵容、租约或成长状态机 |
| `arena/virtual_protection.py` | 普通赛、共斗和 reserve lease 的批量只读保护查询 | 创建/删除租约、更新 Entry/Profile、被基础领域服务反向依赖 |
| `arena/match_store.py` | 创建并校验新的 `ArenaMatch` 调度槽位 | 对局解析、赛事轮次编排、兼容重导出或 reserve 依赖 |
| `arena/virtual_reserve.py` | 有限公共兼容门面与显式 `__all__` | ORM、事务、任务派发实现、demand/pool/fill/lineup 业务逻辑 |

竞技场允许多个状态机在同一个事务中接触 demand，但必须按“迁移命令”而不是按散落字段区分写所有权：

| aggregate / 状态迁移 | 唯一允许的 owner | 其他调用方规则 |
|----------------------|------------------|----------------|
| `ArenaVirtualDemand` 创建、目标/version 对账、`CLOSED` | `virtual_reserve_demand.py` | 赛事 core 只调用 locked primitive，不直接 `save/update` |
| demand 的 pool 进度、创建预算和 pool retry/failure | `virtual_reserve_pool.py` | task 只调用 application API |
| demand 的 fill retry/failure、`SATISFIED` 和缺口归零 | `virtual_reserve_fill.py` | 必须与 Entry、租约消费和参与历史处于同一填充事务 |
| `ArenaVirtualReserveMember` 创建、`TRAINING/READY` 重验及外部原因释放 | `virtual_reserve_pool.py` | 取消报名、无效快照、生命周期和 Admin 只能调用幂等 release command |
| 成功 fill 消费该 demand 的全部 reserve members | `virtual_reserve_fill.py` | 不提供散落的直接 `delete()` 旁路 |
| `ArenaTournament` 招募到运行、`ArenaCoopEvent` 招募到准备 | `lifecycle_helpers.py` 的 locked primitive | 调用方必须已锁赛事并先 reconcile demand；primitive 不读写 demand/member |
| 虚拟 `ArenaEntry/ArenaCoopEntry` 及其 snapshot links | `virtual_backfill.py` 的 locked write primitive | fill 负责选择和锁；lineups 提供满血副本，backfill 拒绝残血/非法 snapshot，且不决定候选、租约或 demand 状态 |
| `BotProfile` 参与时间/次数及状态 | `profile_store.py` | demand/pool/fill 只调用显式 store command |

Gate B 的竞技场拆分必须逐 seed 保持基础组合抽样：lineup 组合继续复现当前 `random.Random(f"{mode}:{event_id}:{profile_id}")` 序列，ready member 排序仍复现当前 `blake2b` 结果，比例、冷却和扫描顺序也不变；新增人数目标由持久化 `roster_target_count` 明确控制。显式 context 是为去除纯函数对 ORM/隐式状态的依赖，不是在结构迁移中替换基础随机算法；未来若再次升级人数策略，必须另立版本并独立验收，生产发布方式另行审批。

M-14 是 Gate B 行为等价拆分完成后的独立 `Surgical Fix`：它只改变虚拟 Entry snapshot 的 `current_hp`，不重新抽取 lineup，
不改变 power、seed、ready 排序、租约或赛事状态机。

### 5.6 现有接线、模型与配置文件

| 文件 | 决策与具体修改 |
|------|----------------|
| `gameplay/models/bots.py` | 保留同域模型文件，不拆；Gate C 为 `BotProfile` 增加第 6.3 节字段/约束并新增 `BotPolicyRelease`、`BotExternalStrengthReconciliation`、`BotRuntimeRoutingState`；routing 单例后续以 additive 字段持久化独立 policy rollout；Gate D1 增加 `BotPopulationRecomputeDemand`；Gate E 再增加有保留期的 `BotSafetyMetricEvent/BotSafetyMetricWindow`，不加入产品动作历史表 |
| `gameplay/models/__init__.py` | 按现有 Django 模型入口模式显式导出上述模型，不增加服务重导出 |
| `gameplay/migrations/<next>_botprofile_v2_fields.py` | Gate C 只做 profile 字段、policy/reconciliation/routing 表、约束和经 EXPLAIN 批准的索引；不加载 YAML、不创建 routing 数据行、不入组 V2 |
| `gameplay/migrations/<after_gate_c>_bot_population_recompute_demand.py` | Gate D1 只增加按 `(region, prestige_band)` 唯一合并、带 request/claim/completion revision 的人口重算需求表；不创建初始需求、不切换八档或 routing |
| `gameplay/migrations/0141_bot_runtime_policy_rollout.py` | 只为既有 routing 单例增加 rollout target/enabled/percent 与数据库约束；默认 `target=1, disabled, percent=0`，不创建状态行、不启用 rollout、不分配档案 |
| `gameplay/migrations/0116_botprofile.py`、`0119_botprofile_band_semantics.py`、`0134_arena_virtual_reserve.py` 及其他历史迁移 | 冻结，不修改；新约束和数据默认值只进入新 migration，历史数据修复使用受控命令 |
| `gameplay/models/arena_virtual.py` | 不拆、不改现有 schema；继续只定义 demand/member 持久化契约，写入状态机分别归 demand/pool/fill service |
| `gameplay/admin/bots.py` | 展示/filter engine、RNG、plan、policy、暂停原因摘要；V2 字段和 policy release 全只读；`mark_selected_stale` 改调应用命令，不直接 `QuerySet.update()` |
| `gameplay/admin/__init__.py` | 按现有模式接入并导出只读 `BotPolicyReleaseAdmin`；不放修复或入组业务逻辑 |
| `gameplay/admin/arena_virtual.py` | 保持 demand/member 只读 Admin，不拆；不得新增绕过 demand/pool/fill 的变更 action |
| `data/virtual_players.yaml` | 保留 V1 兼容配置并新增 `bot_development_v2` 发布输入；Gate D1/Gate E 退出后当前测试环境的对应能力直接全量启用；使用显式模式状态机，不提供引擎百分比字段，已发布版本不原地改写 |
| `core/utils/yaml_validators/virtual_players.py` | 保留单一 registry validator，不拆；以私有 `bot_development_v2` 校验块增加 routing mode、policy/checksum/引用/未知字段规则，并作为 runtime loader 的共同验证入口，避免离线与在线规则漂移 |
| `core/utils/yaml_validators/registry.py` | 保留现有注册；只补契约测试，除非 validator 接口确需变化 |
| `tests/yaml_schema_new_configs/virtual_players.py` | 增加最小合法 V2、未知字段、非法引用、checksum 和 routing 默认关闭样例 |
| `gameplay/tasks/virtual_players.py` | 保留单一 transport 文件和全部现有 task 名称；新增注册后人口专用 task，只调 `population_runtime` 且不执行 Maintenance；另新增每日虚拟监牢清理 task，只向 `jail.py` command 传 cutoff/batch size 并汇总结果；所有 task 都只做 transport 编排，不含 ORM 或领域分支 |
| `accounts/register_runtime.py`、`gameplay/signals.py` | 公共注册事务在 Manor 已完成 Bootstrap 后发出显式真人注册事件；receiver 只在 commit 后投递人口任务。Bot/Admin/fixture 创建 User 不发该事件，避免 Bot 创建递归触发补量 |
| `gameplay/tasks/arena.py` | 不拆并保持现有 Celery task 名称/返回契约；分别改调 reconcile/pool/scan 的 application API，不再从巨型门面导入或解释维护结果 |
| `gameplay/tasks/__init__.py`、`config/settings/celery_conf.py` | 保持既有 task 导出、路由和 beat 名称不变；为虚拟监牢清理新增独立 timer queue 路由与每日一次的 Beat 项，除此之外不改既有调度频率 |
| `gameplay/views/map.py` | `map_backfill_request_api` 改调 `backfill.py` 公共应用入口；保持 API 返回不变，不在搜索读路径创建 Bot |
| `gameplay/services/runtime_configs.py` | 从 `config.py` 严格刷新允许模式和初始化/transition 输入；当前 routing mode、calibration route 与 policy rollout 只从 `BotRuntimeRoutingState` 读取，并共用行锁 + revision CAS 迁移；不得通过热刷新发布 policy 或改写当前状态 |
| `gameplay/management/commands/generate_virtual_players.py` | 保持命令和参数兼容，改调 population/bootstrap application API；dry-run 不写数据库 |
| `gameplay/services/raid/combat/battle.py` | 消费 `BotLootClampDecision`；Raid 事务只提交裁剪后掉落，成功后交给显式退休命令；不得在 loot helper 内锁 profile |
| `gameplay/services/raid/utils.py` | 保持攻击资格的只读 `BotProfile` 存在性/状态查询；继续只依赖纯 state policy，不接退休或额度写入 |
| `gameplay/services/virtual_player_loot_limits.py` | 移除对 `virtual_players.py` 的写命令导入；配置改从 `config.py` 读取，退休建议进入返回值 |
| `gameplay/services/arena/core.py`、`coop_core.py` | 保留普通赛/共斗应用生命周期；先调用 demand locked owner，再调用 `lifecycle_helpers.py` 的赛事转换 primitive；后备补位包装迁到 fill，不得选择、训练或直接写 BotProfile/lease |
| `gameplay/services/arena/coop_lifecycle.py` | 保留共斗报名、快照和参赛门客生命周期；移出赛事转准备 primitive，删除对 virtual reserve 的 import，不承载 demand/pool/fill 转发 |
| `gameplay/services/arena/lifecycle_helpers.py` | 保留现有轮次/结算 helper，并接收普通赛启动（含 replay 初始化和首轮调度接线）与共斗转准备 locked primitive；新增转换逻辑只拥有赛事生命周期，不导入 virtual reserve、虚拟玩家模块或赛事 core callback |
| `gameplay/services/arena/match_helpers.py` | 保留对局解析；无效虚拟快照时调用 pool 的幂等 release command，不再直接删除 `ArenaVirtualReserveMember` |
| `gameplay/services/arena/virtual_reserve_fill.py` | 通过 `profile_store.py` 写参与时间/次数，保持 demand 与参与历史同一竞技场事务语义 |
| `gameplay/services/arena/virtual_protection.py` | 集中当前普通赛、共斗和 reserve lease 的只读保护查询，供人口/生命周期调用 |
| `docs/compatibility_inventory_2026-03.md` | Gate B 合入前登记 `virtual_players.py`、`virtual_player_population.py`、`arena/virtual_reserve.py`、确需保留的 `virtual_player_rules.py` 入口，以及任何经阶段 0 证明必须保留的赛事 core 包装；逐 symbol 写消费者和退场条件 |

### 5.7 领域命令文件修改边界

| 文件 | 本轮安排 |
|------|----------|
| `guests/services/training.py` | 提取 actor-neutral 的 quote/validate/apply locked primitive；真人 `train_guest()` 与 Bot 同用，原公开入口不改 |
| `guests/services/health.py` | 为 `guest_healing` 提取 actor-neutral 药品 quote/validate/apply locked primitive；统一解析药品 HP 效果，保持 `Manor -> InventoryItem -> Guest` 锁序、单件扣减、重伤阈值和失败回滚；真人入口与 Bot 共用 |
| `guests/services/recruitment.py`、`recruitment_flow.py`、`recruitment_guests.py` | 本次增补保持现状，不为 Maintenance V2 提取或接入门客招募/候选处置链，也不把 Bot archetype 或 policy 传入领域层；未来纳入时重新评审 |
| `guests/services/equipment.py`、`equipment_inventory.py` | 原同步 command 优先直接复用；只有锁矩阵证明嵌套入口不安全时才提取 locked primitive，不做预防性重写 |
| `guests/services/skills.py` | 保留 `learn_guest_skill()` 资格和书本消费语义；按锁矩阵决定是否提取 locked 变体 |
| `guests/services/salary.py` | 从 `pay_all_salaries()` 提取要求 Manor 已锁定的幂等 primitive，保持 `SalaryPayment` 日期唯一语义 |
| `gameplay/services/recruitment/recruitment.py`、`lifecycle.py` | 提取募兵 quote/cost/finalize locked primitive和同步压缩 command；真人 start/complete 继续拥有 durable timer 与任务派发 |
| `gameplay/services/manor/core.py` | 提取建筑升级 quote/validate/apply-result primitive；真人 start/finalize 与 Bot 同用容量、资源、声望和缓存规则 |
| `gameplay/services/technology.py`、`technology_runtime.py` | 提取科技升级 quote/validate/apply-result primitive；保持 facade 兼容，不复制成本和 `max_level` 规则 |
| `gameplay/services/resources.py` | 复用现有 `*_locked` 增量与 `ResourceEvent`，原则上不拆；如锁矩阵发现缺口，只补最小 primitive |
| `gameplay/services/inventory/core.py` | 复用 `add_item_to_inventory_locked()`/消费 primitive，不加入虚拟玩家额度或画像逻辑 |
| `gameplay/services/jail.py` | 增加虚拟监牢日清的 actor-neutral 批处理 command；只执行带 cutoff 的 `HELD -> RELEASED` 条件迁移并返回结构化计数，不依赖 Bot policy、不自动招募、不删除记录、不修改真人监牢 |

任何领域文件修改都必须先有正常玩家 command 与 Bot 压缩 command 的 parity test。若某动作无法在不复制规则的前提下同步完成，该动作从 Maintenance V2 首版删除，而不是在虚拟玩家模块直接写模型。

### 5.8 运维命令责任

| 命令文件 | 写入范围与门禁 |
|----------|----------------|
| `gameplay/management/commands/audit_virtual_player_baseline.py` | 只读生成阶段 0 分布、查询数和锁路径输入报告；不得修改 profile 或 policy |
| `gameplay/management/commands/release_virtual_player_policy.py` | 仅创建 `BotPolicyRelease`；默认 dry-run，同 version 同 checksum 幂等，不同 checksum 拒绝 |
| `gameplay/management/commands/retire_virtual_player_policy.py` | 只调用 `policy_registry.retire_policy_release` 完成 `retired_at: null -> timestamp`；要求 expected checksum、零档案/路由引用且数据库 UTC 时间不早于 `retire_not_before`，默认 dry-run |
| `gameplay/management/commands/reclassify_virtual_player_prestige_bands.py` | 按持久化 Manor 声望将存量档案显式、幂等重分段；默认 dry-run，支持 batch/resume，只改分段归属，不改声望或资产 |
| `gameplay/management/commands/enroll_virtual_players_v2.py` | 分批将合格 V1 档案原子写入 V2 engine/RNG/plan/policy；默认 dry-run，支持 batch/resume，跳过锁竞争 |
| `gameplay/management/commands/repair_virtual_player_rng.py` | 仅把明确选中的暂停档案从预期坏值恢复到仍受支持的 RNG version；要求 expected-current、恢复依据和 dry-run，保持 engine/seed/plan/policy/sequence，不执行发展动作 |
| `gameplay/management/commands/repair_virtual_player_plan.py` | 只修复明确指定或筛选出的损坏 V2 画像；保持 engine、seed 和已发布 policy，不执行发展动作 |
| `gameplay/management/commands/upgrade_virtual_player_policy.py` | 只将 V2 档案指向兼容的已发布 policy；不重建 plan、不改变 engine/RNG，不做隐式降级 |
| `gameplay/management/commands/transition_virtual_player_policy_rollout.py` | 以 expected routing revision 和完整 expected-current 三元组显式迁移持久 rollout；默认 dry-run，停用或换目标时延长旧 policy 退役期限 |
| `gameplay/management/commands/rollout_virtual_player_policy.py` | 在持有 routing 行锁且 revision 匹配时，按 `(profile_id, target_policy_version)` 稳定分桶分批升级 V2 档案；默认 dry-run，降低比例不回退已升级档案 |
| `gameplay/management/commands/requeue_virtual_player_reconciliation.py` | 只把明确选中的 `QUARANTINED` intent 在修复根因后恢复为 `PENDING`；要求 expected failure code、expected attempt count、原因、dry-run，不改写玩家已提交结果 |
| `gameplay/management/commands/transition_virtual_player_routing.py` | 初始化或迁移 routing 单例；要求 expected revision/current modes，复用 `runtime_configs` CAS，不直接更新模型或绕过 Gate D1/E 前置检查 |

这些命令都调用 application service，不在 `handle()` 内复制 ORM 事务；所有写命令输出 scanned/locked/changed/skipped/failed 计数及原因，并提供确定性批次顺序。缺失的 policy release 只能由 `release_virtual_player_policy` 使用与档案 checksum 一致的 canonical payload 补建；已存在但内容损坏的 release 不允许原地覆盖，必须从备份恢复或让受影响档案通过 `upgrade_virtual_player_policy` 显式指向新的兼容 release。policy payload 字段永久不可更新；生命周期元数据只允许 registry 单调延后 `retire_not_before`，以及在门禁满足时写一次 `retired_at`，普通 model save、Admin、发布和升级命令均不得写这两个字段。

---

## 6. BotDevelopmentPlan

### 6.1 目的

`BotDevelopmentPlan` 是虚拟玩家长期发展偏好的稳定快照。它只保存个体特征，不表示当前行为状态，不替代生命周期，也不固化可运营调整的全局评分系数。

建议契约：

```python
@dataclass(frozen=True, slots=True)
class BotDevelopmentPlan:
    schema_version: int
    optimization_bias: float
    inertia_bias: float
    roster_focus: float
    preferred_guest_archetypes: tuple[str, ...]
    primary_troop_class: str
    secondary_troop_class: str
    troop_mix: tuple[tuple[str, float], ...]
    preferred_gear_stats: tuple[str, ...]
    preferred_skill_kinds: tuple[str, ...]
    building_focuses: tuple[str, ...]
    technology_focuses: tuple[str, ...]
```

所有浮点字段在构造时规范到有限范围，持久化前转换成版本化 JSON。读取时必须经过严格解析，不能把任意 JSON 直接传入评分函数。

与之分离的 `BotDevelopmentPolicy` 以版本化 YAML 作为发布输入，拥有评分权重、换装阈值、单周期预算、样本阈值、硬上限和
八档成长节奏。分段节奏只决定 Bootstrap 合理历史年龄、Maintenance 正向成长检查区间、最小正向动作间隔和单次综合增幅
上限，不拥有领域资格、成本或结果公式。`release_virtual_player_policy` 命令严格解析并规范化 payload，计算 checksum 后创建
不可变的 `BotPolicyRelease`；运行时只读取已发布记录，不直接执行 YAML 中尚未发布或被原地改写的内容。

策略版本一旦发布便不可更新或覆盖；调参必须新增 `policy_version`。旧版本只要仍被任何档案引用就不得退役，全部迁移后仍至少保留到观察和重放窗口结束。注册表、档案和日志中的版本与 checksum 不一致时 fail closed。这样才能在进程重启后继续证明“同一 policy version 对应同一 payload”，而不是只校验 YAML 内一个可同时被改写的自声明 checksum。

### 6.2 画像与稳定差异

现有 archetype 决定基础范围，`growth_seed` 决定范围内的个体差异。

| Archetype | 门客倾向 | 护院倾向 | 装备与技能 | 成长形态 |
|-----------|----------|----------|------------|----------|
| balanced | 文武覆盖、双核心 | 主副兵种较均衡 | 中等优化与换装频率 | 平滑稳定 |
| rich | 阵容较宽、稀有收藏 | 总量偏低至中等 | 装备库存丰富但不总是最优 | 资源充足、投入分散 |
| dojo | 武系核心、资源集中 | 单一主力流派明显 | 攻击和核心技能优先 | 核心强、替补弱 |
| guard | 防御型核心 | 防御和高生存流派 | 防御、生命、反击优先 | 战力稳定、速度较慢 |
| abandoned | 少量历史核心 | 损失后不完全补充 | 旧装备、技能断层 | 长尾停滞 |

计划还包含 `optimization_bias` 和 `inertia_bias`：

- 高 optimization bias 更常选择高评分候选。
- 低 optimization bias 从前若干候选中更分散地选择，但永远不违反硬约束。
- 高 inertia bias 只有达到明显收益阈值才更换装备、技能或发展方向。

画像与策略的职责固定如下：

| 数据 | 所有者 | 更新方式 |
|------|--------|----------|
| 个体偏好、主副兵种、发展重点 | `BotDevelopmentPlan` | 入组时由 seed 生成，只有显式 schema 升级才重建 |
| 评分权重、阈值、预算、硬上限、分段成长节奏 | `BotDevelopmentPolicy` | 发布新的不可变 policy version |
| 当前资产、状态和可行动条件 | immutable snapshot | 每次计划时重新读取，事务内重验 |
| 生命周期状态 | `BotProfile.State` | 继续由生命周期规则迁移 |

### 6.3 持久化字段

建议为 `BotProfile` 增加以下增量字段：

| 字段 | 类型 | 用途 |
|------|------|------|
| `engine_version` | PositiveSmallInteger，默认 1，带索引 | V1/V2 粘性执行器路由；首轮 V2 值为 2 |
| `rng_version` | PositiveSmallInteger，默认 0 | V1 为 0；V2 首版为 1，固定随机派生算法 |
| `plan_schema_version` | PositiveSmallInteger，默认 0 | `0` 表示无 V2 画像；首版画像 schema 为 1 |
| `policy_version` | PositiveSmallInteger，默认 0 | V2 档案当前固定使用的不可变策略版本 |
| `policy_checksum` | CharField(max_length=64)，默认空字符串 | 档案入组或升级时固定的规范化 policy SHA-256 十六进制值 |
| `development_profile` | JSONField，默认空字典 | 持久化规范化后的计划 |
| `maintenance_sequence` | PositiveInteger，默认 0 | 事务并发令牌、重放序号和跨周期随机变化 |
| `strength_budget_entries` | JSONField，默认空列表 | 最近 24 小时最多 4 条 `{applied_at, positive_growth_bps, policy_version}` 有界预算记录 |
| `last_strength_increase_at` | DateTimeField，可空 | V2 最近一次提高综合或受控分项强度的提交时间；V1 保持空值，供分段最小间隔原子校验 |
| `forced_settlement_daily_budget` | JSONField，默认空字典 | 当前 UTC 日银两/粮食/合计已结算量及首次正向结算时的容量快照；只由 `profile_store.py` 在 Profile 锁内更新 |
| `v2_enrolled_at` | DateTimeField，可空 | V2 入组审计和运维诊断 |

数据库约束至少保证：`engine_version >= 1`；当 `engine_version=2` 时，`rng_version >= 1`、`plan_schema_version >= 1`、
`policy_version >= 1`、`policy_checksum` 非空、`last_strength_increase_at` 非空且 `v2_enrolled_at` 非空。新建 V2 的历史物化和
存量 V2 入组都把 `last_strength_increase_at` 初始化为本次 `v2_enrolled_at`，避免刚建立的完整历史快照立即被竞技场或 Admin
再次加速；这只是冷却锚点，不伪造逐条历史动作，也不消费 24 小时动作记录。`strength_budget_entries` 由严格 parser 保证最多
4 条、时间升序、字段完整、基点非负且没有超出允许时钟偏差的未来时间；JSON 内容完整性不能只依赖字段非空。阶段 0 通过
真实查询计划决定是否增加 `(engine_version, state, next_growth_at)` 和 `(policy_version, id)` 复合索引，不能只凭字段直觉加索引。

另增 `BotPolicyRelease`：

| 字段 | 类型 | 用途 |
|------|------|------|
| `version` | PositiveSmallInteger，主键 | 不可复用的策略版本 |
| `checksum` | CharField(max_length=64)，唯一 | 规范化 payload 的 SHA-256 |
| `payload` | JSONField | 已发布的规范化策略快照，运行时和重放的事实来源 |
| `released_at` | DateTimeField | 发布审计时间 |
| `retire_not_before` | DateTimeField | 当前环境退役保护截止；只允许 registry 单调延后，配置变化不得缩短既有值 |
| `retired_at` | DateTimeField，可空 | 仅在无档案引用且观察窗口结束后标记退役 |

该模型没有普通更新入口；发布命令只允许 `create`，相同 version 的重复发布只有 checksum 完全一致时才作为幂等成功。Admin 只读，禁止修改和删除。当前非生产环境冻结 168 小时观察窗口和 720 小时重放/回滚窗口，保护期取两者最大值 720 小时。发布时以数据库 UTC 时间初始化
`retire_not_before = released_at + 720h`；最后一个 profile 引用、routing 引用或 rollout 引用被移除时，在同一 version 级互斥边界内按
`max(existing_deadline, event_time + 720h)` 单调延后。未来配置只能延长既有 deadline，不能缩短；生产窗口仍须上线前另行批准。

`BotRuntimeRoutingState` 以 `policy_rollout_target_version >= 1`、`policy_rollout_enabled` 和
`policy_rollout_percent` 持久化当前 rollout。数据库约束保证 enabled 时 percent 为 `1..100`、disabled 时 percent 只能为 `0`；
`0141_bot_runtime_policy_rollout.py` 的兼容默认值为 `target=1, disabled, percent=0`。这些字段与 mode、calibration route 共用
同一 routing `revision`，因此任一并发 transition 都会使其他 stale writer 失败，而不是形成第二个配置版本轴。

`policy_registry.retire_policy_release(version, expected_checksum, retired_at)` 锁内确认零 `BotProfile` 引用、无 routing/rollout 引用，且数据库 UTC 时间已经到达持久化 `retire_not_before`，才把 `retired_at` 从空值推进为时间戳。该命令与 policy assignment 使用同一 version 级互斥边界；重复相同结果幂等，改写退役时间、反向 unretire、修改 payload/checksum/released_at、缩短 `retire_not_before` 或 delete 均拒绝。退役 payload 永久保留并允许只读 replay；退役只禁止新 assignment，不销毁重放事实。

玩家驱动的强度/声望变化另需窄化持久表 `BotExternalStrengthReconciliation`，它不是通用动作历史或 H-01 outbox：

| 字段 | 用途 |
|------|------|
| `profile_id`、`domain_event_kind`、`domain_event_id` | 唯一键，随原领域结果同事务创建，保证重试不重复记账 |
| `origin_committed_at`、`pre_strength_summary`、`pre_prestige_band` | 保存对账所需的提交前锚点，不保存完整玩家对象 |
| `status`、profile/population `attempt_count`、`available_at` | `PENDING_PROFILE/CLAIMED_PROFILE/PENDING_POPULATION/CLAIMED_POPULATION/APPLIED/QUARANTINED`；两阶段分别有界 |
| `claim_token`、`claimed_at`、`claim_expires_at` | 两阶段复用的 5 分钟 claim lease 与 fencing；失去 token 的旧 worker 不得 finalize |
| `processed_at`、`result_summary` | 证明强度预算、当前段同步、冻结及人口 handoff 是否完成 |
| `quarantined_at`、`quarantined_phase`、`failure_code`、`last_error_digest` | 永久契约错误或任一阶段 12 次重试耗尽后的可审计隔离，不保存敏感 payload |

原领域事务只创建 `PENDING_PROFILE` intent，不等待对账结果；`transaction.on_commit` 投递相同 intent id，周期扫描重新发现所有到期
pending 或过期 claimed 状态。worker 按 `(profile_id, origin_committed_at, id)` 处理每个档案的最早未解决 intent；同档案后续 intent
不得越过任何非 `APPLIED` 前项。每阶段 claim 都生成随机 token、递增该阶段 attempt 并写入 5 分钟 lease；profile 执行事务按
`Reconciliation -> Profile -> Manor` 锁序重验 `CLAIMED_PROFILE + token`，因此租约过期后恢复的旧 worker 无权 finalize。

`profile_store.py` 在同一事务原子记录强度预算、`last_strength_increase_at`、`current_prestige_band` 与超限冻结：无需人口 handoff 时直接进入
`APPLIED`；跨段时进入 `PENDING_POPULATION`。population worker 使用独立 attempt counter 和相同 token fencing，把旧/新两段请求合并进
Gate D1 的持久 `BotPopulationRecomputeDemand` 后才进入 `APPLIED`，周期扫描恢复 pending/expired claim。可重试基础设施错误按 60 秒起始、
2 倍递增、最大 6 小时的 backoff 返回对应阶段 pending；明确永久契约错误立即进入 `QUARANTINED`，其余错误继续抛出并由 lease 恢复，
每阶段最多 claim 12 次，耗尽后同样隔离。已应用 intent 保留 30 天；隔离 intent 保存 `quarantined_phase` 且不自动清理，显式命令只能
按 expected failure/attempt 把它恢复到该阶段的 pending 状态，绝不重复已经完成的 profile 阶段。

人口 handoff 不能只依赖 Celery delivery receipt。Gate D1 增加窄化的 `BotPopulationRecomputeDemand`，每个
`(region, prestige_band)` 只保留一个合并行：

| 字段 | 用途 |
|------|------|
| `region`、`prestige_band` | 唯一合并键；多段写入按地区、八档 ordinal 的稳定顺序锁定 |
| `requested_revision`、`completed_revision` | producer 锁内递增 request revision；`requested > completed` 是唯一 pending 判定 |
| `claimed_revision`、`claim_token`、`claimed_at`、`claim_expires_at` | worker 对 claim 时看到的 request revision 建立 5 分钟 lease 与 fencing |
| `available_at`、`consecutive_failure_count`、`last_error_digest` | 失败不推进完成 cursor，按 60 秒起始、2 倍增长、最大 1 小时退避 |
| `created_at`、`updated_at` | 运维审计；完成行保留，不通过删除表达完成，避免 delete/create 竞态 |

merge 在行锁内只递增 `requested_revision`。worker 以独立短事务 claim 当前 request revision，释放 demand 行锁后才取得既有
`BotPopulationControl` ownership 并按实际缺口执行人口重算，最后再以短事务锁 demand 行，只有 token 和
`claimed_revision` 都匹配且 lease 尚未过期，才把 `completed_revision` 推进到该 claim revision。数据库约束保证
`completed_revision <= requested_revision`、非空 claim revision 落在 `(completed, requested]` 且 claim 字段全空或全非空。claim
期间出现的新 merge 会使
`requested_revision > completed_revision`，旧 worker finalize 后该行仍为 pending，不能误吞新请求；过期 token 不能完成或覆盖新 claim。
失败只清理匹配 claim、增加连续失败数并设置有上限的 backoff，不丢弃 demand；成功清零连续失败数。周期扫描恢复到期 pending
和过期 claim，Celery 投递只缩短延迟，不是 durable truth。新 merge 不缩短尚未到期的失败 backoff，避免事件洪峰把持续故障
变成热循环；旧 claim 完成时若发现更新 revision，则把新 pending 工作设为立即可用。

外部对账 population 阶段在同一事务内按稳定键序合并全部旧/新段 demand 后才把 intent 标为 `APPLIED`；事务回滚则两者都不生效。
注册和普通真人跨段的 `on_commit` callback 也调用同一 merge owner，再投递只携带合并键的唤醒任务；callback 进程丢失仍由原定时
全量人口扫描补偿。无效地区/声望段必须在 merge 前拒绝，不能生成永久 poison row。需求行不表达目标人数，也不按事件次数创建 Bot；
消费者仍在 population lock 内重算当前目标与供给，只补实际缺口。

存在任何未解决 intent 的档案由只读候选规则排除自动匹配，并暂停正向 Maintenance；selector 不得顺手处理或重排 intent。隔离事件进入 safety hard-violation 指标。外部 intent schema、人口 demand schema、对账 worker/清理保留期和故障注入分别属于 Gate C、Gate D1 与 Gate E 的受控实现范围，现已获本轮实现与隔离验证授权，但不得据此批量处理现有数据或启用 V2 routing。

不增加语义含糊的 `last_action_kind / last_action_at`，因为一个维护周期还包含生命周期、结算和调度元数据；
`last_strength_increase_at` 只表达一项安全事实，不替代通用动作历史。首版使用结构化日志记录维护结果，不增加无限增长的
`BotActionLog` 表。`strength_budget_entries` 只是有界安全窗口：在 Profile 锁内先删除 `applied_at <= now - 24h` 的项，再以
剩余条数和基点总和校验当前样本档位。每个提高强度动作按
`ceil(max(post_score - pre_score, 0) / max(pre_score, score_floor) * 10000)` 追加一项，并与 `last_strength_increase_at`、领域写入
原子提交；即使综合增幅为 0，只要受控分项上升也追加 0 基点项、消费动作次数并更新时间。它不用于重放具体动作或产品历史展示。

首版所有发展动作必须在档案事务内同步完成，因此 `maintenance_sequence` 足以防止同一周期重复提交。未来如果允许事务外派发或异步完成，必须先增加带唯一约束的精简执行表或 outbox，例如唯一键 `(profile_id, sequence, action_ordinal)`；该表服务于幂等和恢复，不等同于产品历史记录。

读取规则：

- `engine_version=1` 时忽略空画像，继续执行 V1。
- `engine_version=2` 且 RNG、画像或策略发布记录为空、损坏或不兼容时禁止发展写入、重新激活和新竞技场租约；只允许安全退休、租约释放和有界延后调度，不能自动执行 V1。
- RNG 归属只通过 `repair_virtual_player_rng` 从结构化日志/发布记录证明的坏值恢复到仍受支持的实现；画像只通过 `repair_virtual_player_plan` 按原 seed 和指定 schema 可重放生成，两者都记录修复依据且不推进 sequence。
- policy release 缺失时只允许用匹配档案 checksum 的 canonical payload 补建；已有记录内容损坏时从备份恢复或把受影响档案显式切到新的兼容 release，禁止覆盖原 version 来伪装修复。

---

## 7. 版本化随机上下文

随机子流按以下规范化对象派生；使用 UTF-8、字段名、排序 key 和无 NaN/Infinity 的 canonical JSON，避免简单字符串拼接的边界歧义：

```text
SHA-256(canonical_json({
  "namespace": "virtual-player",
  "rng_version": profile.rng_version,
  "growth_seed": profile.growth_seed,
  "engine_version": profile.engine_version,
  "plan_schema_version": profile.plan_schema_version,
  "policy_version": profile.policy_version,
  "maintenance_sequence": profile.maintenance_sequence,
  "domain": domain_name,
  "discriminator": stable_business_key
}))
```

首轮 domain 至少包括：

- `bootstrap`
- `lifecycle`
- `reference_anchor`
- `roster`
- `training`
- `gear`
- `skills`
- `troops`
- `buildings`
- `technology`
- `inventory`
- `schedule`
- `policy_rollout`

规则：

- 不共享一个可变 `random.Random` 给多个领域。
- 新增一个领域不会改变已有领域的随机序列。
- 同一领域的候选按稳定业务 key 排序；数据库返回顺序、`set`/`dict` 偶然顺序不得进入结果。
- `rng_version` 的实现一旦有持久化档案引用便不可改写；算法升级新增版本并保留旧派生器到重放窗口结束。
- policy version 内容不可原地改写；相同版本必须校验相同 checksum。
- `maintenance_sequence` 与本次写入在同一事务提交。
- 任务失败回滚后重试使用相同 sequence。
- 成功完成但没有可执行发展动作的周期也推进 sequence，避免长期重复同一组候选。
- 管理员和竞技场加速维护也使用同一入口，不单独抽取未持久化 seed。
- policy rollout 复用本模块的摘要分桶并取 `digest_int % 100`，禁止使用 Python 内置 `hash()`；当前方案不实现 V2 engine enrollment bucket。

---

## 8. 自然化算法

### 8.1 真人参考档案

Gate D1 后，V2 的人口、参考 cohort 和强度保护统一使用以下声望段；边界均为下限包含、上限不包含，只有末段开放：

| 声望段 | V2 区间 | 供给语义 |
|--------|---------|----------|
| `newbie` | `[0, 500)` | 常规人口段 |
| `junior` | `[500, 2,000)` | 常规人口段 |
| `middle` | `[2,000, 8,000)` | 常规人口段 |
| `senior` | `[8,000, 30,000)` | 常规人口段 |
| `veteran` | `[30,000, 60,000)` | 高段，按真人活跃或显式需求激活 |
| `elite` | `[60,000, 120,000)` | 高段，按真人活跃或显式需求激活 |
| `legend` | `[120,000, 240,000)` | 高段，按真人活跃或显式需求激活 |
| `mythic` | `[240,000, +∞)` | 开放终段，按真人活跃或显式需求激活 |

这八档让十几万声望玩家落在 `legend`，更高声望仍由 `mythic` 承接。没有活跃真人、地图搜索需求或竞技场需求的空高段
目标供给为 0；一旦出现有效需求，优先创建新 V2 或复活同段 Bot。参考快照、样本档位和强度上限始终按目标 Bot 当前所在
声望段独立计算，不能借低段 Bot 充数，也不能把低段 Bot 瞬间抬高到目标段。`data/virtual_players.yaml` 中当前 V1 的
`veteran: [30000, null]` 在 Gate D1 前继续生效，不能通过热加载提前改成八档。

当前独立分位数改为联合参考快照，但不直接复制某个真人 ORM 对象：

1. `reference_snapshots.py` 一次批量查询同声望段、近期活跃、非 staff、非 superuser、非虚拟玩家的候选庄园。
2. 将候选转换成不可变 `HumanReferenceSnapshot`，只保留校准需要的数值、比例和类别摘要。
3. 同地区、同声望段只要存在有效真人样本就使用本地 cohort，并且强度档位始终只按本地样本数确定；稀疏本地样本不得被
   更充足的全局样本替换或抬高。仅当本地样本为 0 时，才允许九折借用全局同声望段 cohort 的结构和比例作为组合 anchor；
   本地仍处于 0 样本档，最终综合及分项上限取“版本化新手快照 90%”与“九折全局参考上限”中的更严格值。全局同段也为 0
   时完全使用版本化新手快照 fallback。
4. 在候选集合内对声望、核心建筑、门客阵容、护院和装备指标做稳健规范化，并按 P5/P95 或阶段 0 确认的边界裁剪离群值。
5. 根据画像目标分位找到最近的 k 个联合快照，再使用 `reference_anchor` 子流做稳定选择，避免大量档案复制同一个真人。
6. 只读取 anchor 的相关结构和比例，随后按 `BotDevelopmentPlan` 做有限扰动；不复制名称、用户标识、精确时间线或完整库存明细。

建议快照至少包含：声望、核心建筑等级向量、科技类别等级、门客数量及核心/替补等级差、门客稀有度比例、装备槽位与稀有度摘要、技能数量与类别摘要、护院 class 比例、资源容量利用率。综合强度公式、各字段权重、k 值、强度档位、最小样本数和全部 Gate D2 分布阈值都属于版本化 `BotDevelopmentPolicy`。生产 acceptance workflow 只能从不可变 `BotPolicyRelease.payload.reference_calibration_thresholds` 读取这些阈值；该 mapping 参与 policy checksum，policy release 可以声明比 Gate A 基线更严格的值，任何放宽都必须先修订 acceptance contract。参考分布校准策略必须取得 Gate D2 基线后才能启用；Gate D1 的保守冷启动策略不依赖该基线，但必须通过固定 fixture 的强度保护、硬约束和经济测试。

Gate D2 的最小启用单元固定为 `(policy_version, reference_snapshot_version, prestige_band)`。`gate_d2_acceptance_workflow`
按三元组独立聚合并判定证据，版本化虚拟玩家运行配置按声望段保存 routing 状态，只有
`gameplay.services.runtime_configs` 可以把通过的三元组切到参考校准策略。任一段缺样本、缺指标或越界时，只让该三元组
保持 `conservative_cold_start`；不得用全局平均、其他已通过声望段或同段其他 policy/snapshot 版本掩盖失败。发布新的
policy 或 reference snapshot 会形成新的启用单元并重新过门禁，既不继承旧三元组结论，也不关闭已通过安全门禁的
Bootstrap/Maintenance 基础能力。

调用方和 CLI 只能提交上述三元组；通过时由 acceptance workflow 派生并持久化
`policy_checksum / reference_snapshot_digest / evidence_schema_version / evidence_digest`，形成七字段 route。snapshot artifact
和 candidate report 的严格解析、大小/深度限制及 digest 校验在 routing 行锁前完成；锁内只复验不可变 policy release、退役状态和
routing revision CAS。Bootstrap 的只读 resolver 每次都把持久 proof 与当前 config/catalog 及冻结 snapshot 重新绑定，任何 route
删除、同版本热重绑或 artifact 漂移都会让新计划回退 `conservative_cold_start`；已经规划为 `reference_calibrated` 的在途计划则在
任何资产写入前拒绝并要求重规划。resolver 不执行 DML。当前 `reference_snapshot_catalog` 为空，因此没有任何三元组被激活；
Bootstrap consumer 已接线，Maintenance calibrated consumer 必须等待 Gate E 的 V2 executor，禁止接入 legacy Maintenance。

candidate artifact schema v2 已冻结为四类匿名原始 cohort，并绑定 generator version/entrypoint、engine/RNG/plan 版本、seed、catalog、
各 cohort digest 和当前源码 manifest。外部受控生成器必须提供 `hmac_sha256_v1` attestation；可信密钥只允许来自运行时 secret
settings，默认密钥集合为空，并且不得从 artifact、report 或 catalog 获取。attestation payload 覆盖启用三元组、policy/snapshot
digest、生成器身份与版本、root seed、源码 manifest、模板 catalog 和全部原始 cohort，因此攻击者不能靠同步改写 artifact、report 与
catalog 自证来源。该 HMAC 边界只适用于当前非生产环境；未来生产签名、密钥托管与轮换必须在上线前独立评审。

metric algorithm v2 从原始资产与生命周期字段独立重算连续分布、类别分布、组件/联合指纹、硬约束、画像方向和弃坑特征；其中
`robust_joint_outlier_rate` 使用 12 维联合向量，按稳定 business key 确定性拆分 `80% fit / 20% holdout`，仅以 fit 的 median、IQR、
留一最近邻距离与稳健 MAD 阈值建模，holdout 不参与拟合。即使每项边际分布不变，相关结构反转也会被识别。report schema v3
必须声明 artifact schema v2 与 metric algorithm v2，并与重算结果逐字段相等；旧算法 artifact、同步伪造 report metrics/catalog digest
或派生 claim 字段均 fail closed。该实现闭合只证明 verifier 契约，当前 catalog 为空且没有获批代表性真人 artifact/report，所以
Gate D2 保持 `INTENTIONALLY_OFF`，不得把 synthetic fixture 表述为可信分布结论。

这里的“版本化新手快照”不是全局 `newbie` 玩家档案，而是每个声望段各自一份、由确定性游戏规则构造的
`per_prestige_band_conservative_entry_fixture`。它不读取真人数据；应用 90% 上限后的声望仍必须合法落在目标段内，其他
强度分项保持该段的保守起点。这样零样本高段在出现显式需求时仍可创建，但不能借 fallback 获得超出同段保守起点的资产。

强度保护按当前地区/声望段的有效真人样本数独立执行：

| 有效真人样本 | Bootstrap anchor 与最终上限 | 正向扰动上限 | 24 小时提高强度动作 | 24 小时综合强度增幅 |
|--------------|---------------------------|--------------|----------------------|----------------------|
| 0 | 组合可九折借用全局同段结构；最终综合/分项上限取该参考上限与版本化新手快照 `90%` 中的更严格值 | `0%` | `0` | `0%` |
| 1--4 | P50；综合不超过 `105%`，任一分项不超过 `110%` | `0%` | `1` | `3%` |
| 5--29 | anchor 只取 P25/P50；以 P75 为基准，综合不超过 `110%`，任一分项不超过 `115%` | `2%` | `2` | `5%` |
| 30+ | anchor 只取 P25/P50/P75；以 P95 为基准，综合不超过 `115%`，任一分项不超过 `120%` | `5%` | `4` | `10%` |

“提高强度动作”包含任何会提升综合分或受控分项的训练、建筑、科技、装备、技能、护院、声望及竞技场加速结果，
不能只统计战斗任务次数。正向扰动也计入最终上限；超过动作次数、日增幅或综合/分项任一上限时取最严格结果。
生命周期迁移、工资和到期资源结算可以继续，但不得借强制结算夹带强度成长。

在四档样本保护之上，再按当前 V2 声望段应用统一的成长节奏层：

| 声望段 | Bootstrap 合理历史年龄 | 正向成长检查区间 | 最小正向动作间隔 | 单次受控综合增长上限 |
|--------|------------------------|------------------|----------------------|------------------------|
| `newbie` | `1--14` 天 | `4--8h` | `4h` | `4.00%` |
| `junior` | `14--45` 天 | `6--12h` | `6h` | `3.00%` |
| `middle` | `45--120` 天 | `8--16h` | `8h` | `2.50%` |
| `senior` | `120--240` 天 | `12--24h` | `12h` | `2.00%` |
| `veteran` | `240--360` 天 | `14--24h` | `14h` | `2.00%` |
| `elite` | `360--540` 天 | `18--30h` | `18h` | `1.75%` |
| `legend` | `540--720` 天 | `24--36h` | `24h` | `1.50%` |
| `mythic` | `720--1080` 天 | `30--48h` | `30h` | `1.25%` |

高四档采用上述适度减速值，而不是把 `legend/mythic` 拉长到数日一次：过小的单次 cap 可能让不可拆分的合法领域升级持续
`NO_ACTION`，过长间隔也会使已创建 Bot 明显落后于同段真人变化。该调整只增加追赶可用性，不提高最终强度天花板；样本档位
的 24 小时预算、综合/分项 cap 和领域约束仍先于分段节奏阻止反超。

历史年龄只供 Bootstrap 一次性投影合理成熟度和分散时间，不伪造逐条成长记录，也不表示等待对应天数后才创建。正向成长
检查区间由版本化随机上下文确定下一次常规检查时间；生命周期和强制结算可以更早到期，但在
`last_strength_increase_at + minimum_spacing` 之前不得夹带正向成长。真人样本档位的 24 小时次数/总增幅、分段间隔/单次上限和
领域动作自身的资格、成本、资源及结果上限同时生效，任何一项更严格都以该项为准。因此 0 样本仍为零正向成长，不会被
`newbie` 的 4% 单次上限放开；高段也不会因为真人样本充分而获得低段速度。

高段人口不依赖某个低段 Bot 连续成长数月后才可用；高段激活信号出现时，Bootstrap 直接在目标段创建受同段保守 fixture
和强度上限约束的 V2 历史快照。分段减速只约束创建后的增量成长，因此既能及时补足十几万及更高声望供给，也不会让稀少
真人场景中的高段 Bot 在创建后快速反超。

Maintenance 不直接赠送声望，只能复用正常领域动作及其声望副作用。一个受控发展动作最多跨越一个配置边界；预计跨段时
必须同时按来源段和目标段重验，并采用更严格的间隔与上限，提交后下一次间隔按目标段计算。预计跨越两个及以上边界的受控
intent 直接拒绝，不通过裁剪结果规避领域规则。PVP 或其他玩家驱动的既有领域结果不因 Bot 成长策略被拒绝；其强度增量和
跨段在提交后记录并对账，若落在当前上限之上，则冻结后续受控正向成长并退出自动匹配，不自动回滚、降级或改写该领域结果。

同一 seed、policy version 和同一组快照得到相同 anchor。运行日志只记录候选集合版本、样本数量和不可逆摘要指纹，不记录真人用户 ID。候选和关联数据必须批量加载，候选评分循环中禁止 ORM 查询。

### 8.2 门客生成与培养

#### Bootstrap

- 使用真实招募池权重和阶段稀有度上限，不只按全局低使用次数选择。
- 先满足阵容覆盖，再满足画像偏好，最后考虑全局模板多样性。
- 同一庄园避免不合理重复门客；允许配置明确可重复的低稀有度模板。
- 为门客分配稳定投资层级：核心、二队、替补。
- 同时物化与建筑容量、历史招募和现有护院规模一致的家丁储备，不能让 Bootstrap 生成的护院完全绕过其生产前提。
- 门客创建、技能学习和装备获得时间在虚拟玩家历史周期内错开，避免全部同日获得。

#### Maintenance

- 禁止修改已有 `Guest.template_id`。
- 一个维护周期只有在门客培养被选为唯一发展动作时才执行一次培养。
- 增加单门客 `guest_healing` 候选：先过滤本庄园、可治疗状态、未满血和可用药品，再按重伤、投资层级和缺失 HP 比例排序；
  同一周期治疗与培养不能同时提交。
- 治疗必须调用 `guests/services/health.py` 的共享领域 command 并原子消耗一件已有药品；不能用训练、升级或 Bot 专用分支免费回满。
- 治疗恢复既有 HP，不消费永久强度预算或推进 `last_strength_increase_at`，但提交后要重新计算可出战阵容；无药时继续依赖全局被动回血。
- 训练优先级参考：

```text
training_score =
    investment_weight
  * target_gap
  * role_importance
  * availability
  / normalized_cost
```

- 资源投入形成非均匀分布，默认配置可从 `60 / 25 / 10 / 5` 开始，再按画像调整。
- 已出征、竞技、受伤或训练中的门客不进入候选。
- 首版不通过 Maintenance 招募或转换候选；本次增补保持 V1 招募/晋升现状，不将其移植为 V2 动作。稀有度提质仅由
  Bootstrap 历史快照和既有门客培养承担，不改变旧门客身份。

### 8.3 装备选择

硬过滤：

- 阶段稀有度上限；
- 装备槽位与容量；
- 庄园所有权和可用状态；
- 不重复装备同一个实例。

候选评分：

```text
gear_score =
    role_stat_fit
  + effective_power_gain
  + set_completion_gain
  + persona_fit
  + roster_distribution_gain
  - scarcity_cost
  - swap_inertia
```

选择规则：

- 高 optimization bias 从最高分附近选择。
- 低 optimization bias 使用 top-k 有界加权抽样。
- 提升低于配置阈值时保留现装，初始建议阈值 `8%` 至 `15%`。
- 被替换装备优先下放给次级门客，其次进入仓库，不直接删除。
- Bootstrap 可以生成历史装备快照；Maintenance 的装备动作只使用已有库存或既有锻造产出，受控 acquisition 必须作为另一个维护周期的独立动作。

### 8.4 技能学习

硬过滤：

- 等级和属性要求；
- 技能位上限；
- 不重复已有技能；
- 门客必须空闲；
- Maintenance 阶段需要已有对应技能书；虚拟获取必须作为另一个维护周期的独立 acquisition 动作。

候选评分考虑：

- `damage_formula` 对门客高属性的利用；
- `passive_config` 与门客定位；
- 主动和被动技能的实际缺口；
- 状态效果与队伍已有功能的重复程度；
- 技能稀有度、书本稀缺度和未来技能位机会成本；
- 玩家画像对爆发、控制、防守或被动的偏好。

规则：

- 每个维护周期最多学习一个技能。
- 不强制所有门客都采用“一主动两被动”。该比例成为画像和角色的一项软目标。
- Bootstrap 创建的历史技能可以直接写入，但必须记录 `TEMPLATE` 或历史 `BOOK` 来源并满足约束。
- Maintenance 优先复用 `learn_guest_skill` 的锁、库存消费和资格规则，或将其底层 command 提取后复用。

### 8.5 护院培养

每个计划确定主力和次要 troop class，使用现有 troop-to-class 索引识别刀、枪、剑、拳、弓和探子。

默认结构建议：

| 类别 | 占比范围 |
|------|----------|
| 主力兵种 | 55% 至 75% |
| 次要兵种 | 15% 至 30% |
| 探子 | 3% 至 10% |
| 其他已解锁兵种 | 0% 至 10% |

规则：

- 只选择科技和生产条件允许的兵种档位。
- 同一 class 内优先当前可持续招募的最高一至两个档位，不平均铺满全部档位。
- 武艺科技优先级与主力 class 一致。
- 战斗损失保留，按每周期招募预算逐步恢复。
- `abandoned` 档案允许明显缺编，不自动恢复到满额。
- 家丁不足时本周期不生成护院恢复 intent；首版 Maintenance 不用候选转家丁链，护院招募不得凭空补充家丁或装备材料。
- Bootstrap 直接建立合理历史库存；Maintenance 通过第 8.9 节定义的同步压缩 command 复用募兵成本、资格和最终入账规则，不创建逐档案倒计时任务。

### 8.6 建筑、科技与资源

#### 建筑

- 不再把全部核心建筑设为同级。
- 画像决定优先级，核心建筑围绕 anchor 等级形成 0 至 3 级落差。
- 单周期最多完成一个建筑成长动作，且该动作与其他发展动作互斥。
- 容量根据实际银库、粮仓等级计算，不使用一个统一 level 代表全部容量建筑。

#### 科技

- 按基础、生产和主力武艺路线选择优先级。
- 每项科技钳制到模板 `max_level`。
- 不相关武艺科技允许明显落后或为零。
- 科技与主力护院 class、锻造和生产画像保持一致。

#### 资源

- 不再每次维护把资源硬重置为容量比例。
- 根据生产、工资、训练、募兵和受攻击结果计算增量。
- 允许通过“目标储备区间”抑制极端值，但使用 bounded approach，不无事件地清空高资源。
- 保留现有战利品预算作为对真人经济的最终边界。

### 8.7 库存

- 保留现有 archetype 库存池、稀有物品全局限额和事务回滚机制。
- 将技能书、装备和募兵材料的 acquisition intent 与发展计划关联。
- 高价值物品继续受声望、阶段、概率和全局每日上限共同约束。
- 库存补充不得因为某个维护动作失败而消耗全局额度。

### 8.8 有界理性选择

所有局部评分统一遵循：

1. 硬约束先过滤，评分不能覆盖合法性。
2. 各 consideration 规范到 `0.0..1.0`。
3. 使用画像权重计算总分。
4. 从 top-k 候选做确定性有界随机选择。
5. `optimization_bias` 控制对高分候选的集中程度。
6. `inertia_bias` 控制改变现有选择所需的最低收益。

这提供 Utility AI 的核心收益，但没有引入跨全游戏行为框架和状态管理成本。

### 8.9 领域命令复用契约

“压缩完成”只压缩等待时间和通知，不代表直接覆盖领域状态。阶段 0 必须产出并评审以下 command matrix；未明确 owner、锁、成本和结果语义的动作不得进入 Maintenance V2。

| 领域动作 | 当前真实规则所有者 | Maintenance V2 组合方式 | 不可省略 |
|----------|----------------------|-------------------------|----------|
| 门客培养 | `guests/services/training.py` | 从 `train_guest()` 的资格、cost、属性成长和日志中提取 actor-neutral locked primitive；同步完成一个培养动作，不派发倒计时 | `Manor -> Guest` 锁序、空闲状态、等级上限、资源扣除、`TrainingLog`、属性成长 |
| 门客回血/治疗 | `guests/services/health.py` | 复用被动恢复规则；主动治疗通过 actor-neutral 药品 locked primitive 一次处理一名门客，作为 `guest_healing` 占用唯一同步动作 | 本庄园所有权、`IDLE/INJURED`、未满血、药品效果解析、`Manor -> InventoryItem -> Guest` 锁序、单件扣减、20% 重伤解除阈值、整事务回滚 |
| 门客招募/候选处置 | `guests/services/recruitment.py`、`recruitment_flow.py`、`recruitment_guests.py` | 本次增补保持现状，Maintenance V2 首版不组合整个招募链；未来纳入时重新评审 actor-neutral command | 不得跳过或伪造 `ensure_auto_training()`、候选、成本、身份及审计语义 |
| 装备穿脱 | `guests/services/equipment.py`、`equipment_inventory.py` | 直接复用同步 command 或提取 locked primitive；只使用已有库存，acquisition 另占周期 | 所有权、门客状态、槽位、库存消费、属性和套装重算、旧装备去向 |
| 技能学习 | `guests/services/skills.py::learn_guest_skill` | 直接复用或提取 locked command | 空闲状态、技能位、属性要求、技能书所有权/消费、来源 |
| 护院招募 | `gameplay/services/recruitment/recruitment.py`、`lifecycle.py` | 从 start/finalize 流程提取 quote、cost 和 finalize locked primitives，再由同步压缩 command 组合 | 科技、装备、家丁、资源、兵种上限、最终入账和审计记录 |
| 建筑升级 | `gameplay/services/manor/core.py::start_upgrade/finalize_building_upgrade` | 提取 quote、资格、cost 和 apply-result primitives；同步压缩 command 一次只完成一级 | `Manor -> Building` 锁序、并发资格、资源扣除、声望/资源事件、容量与缓存重算 |
| 科技升级 | `gameplay/services/technology.py`、`technology_runtime.py` | 提取 quote、资格、cost 和 apply-result primitives；同步压缩 command 一次只完成一级 | Manor 锁、前置/并发条件、资源扣除、声望、`max_level` 和缓存失效 |
| 资源生产与工资 | `gameplay/services/resources.py`、`guests/services/salary.py::pay_all_salaries` | 作为有界强制结算，复用持锁增量 primitive，不计入发展动作 | 生产保持 `ResourceEvent`；工资只写 `SalaryPayment`；保留容量、余额下限、日期幂等和失败回滚 |
| 库存获取 | `gameplay/services/inventory/core.py::add_item_to_inventory_locked` 与 `inventory_budget.py` | 同一事务先预留全局额度，再调用领域 locked primitive 入库 | 稀有度、全局限额、所有权、粮食双写语义和回滚释放 |
| 虚拟监牢日清 | `gameplay/services/jail.py` | 不进入 Maintenance Utility；每日 transport task 按固定 cutoff 分批调用 `HELD -> RELEASED` command | 只限虚拟 captor、真人监牢隔离、稳定批次、条件状态迁移、幂等重试、无自动招募/删除/返还 |

共享 primitive 必须满足：

- 不依赖 HTTP request、消息文案、模板或虚拟玩家模块。
- 明确要求调用方是否已处于 `transaction.atomic()`，并记录锁定对象和顺序。
- 正常玩家 command 与机器人压缩 command 复用同一资格、成本和结果计算，不复制公式。
- 业务不可执行返回结构化原因；基础设施失败和编程错误继续抛出。
- 首版压缩 command 全部同步执行，禁止 `on_commit` 派发发展完成任务。
- 现有 command 若自行打开事务或按不同顺序加锁，必须先提取明确的 `*_locked` primitive；不得在已经持有 `BotProfile`/Manor 锁时盲目嵌套调用。

Bootstrap 是唯一允许直接物化历史结果的边界，但仍必须经过模板存在性、等级上限、槽位、技能位、所有权和全局库存额度校验。它不伪造逐条任务记录，也不能被存量维护调用。

### 8.10 竞技场虚拟补位满血快照契约

竞技场满血是 match snapshot policy，不是门客治疗 command。调用链固定为：

```text
共享实时门客 snapshot（保留合法 current_hp）
  -> virtual_lineups 选择阵容并复制所选 snapshot
  -> 仅虚拟副本设置 current_hp = max_hp
  -> virtual_backfill 锁内重验全员满血
  -> 普通赛/共斗虚拟 Entry 原子物化
```

- 规范化发生在阵容已经按既有 seed 和 power 选定之后；`snapshot_power()` 继续使用 `max_hp`，因此满血规则不能改变组合选择结果。
- `max_hp` 缺失、非整数或小于 1 时不得猜测或从 ORM 补查，整次填充 fail closed；物化层不得静默替调用方修复残血 snapshot。
- `virtual_backfill.py` 只接收已规范化副本并做防御性断言；写入成功后所有 `source=VIRTUAL` snapshot 都满足满血不变量。
- 对真实 `Guest` 的残血、重伤、药品和状态不做任何写入；竞技场结算也不把 snapshot HP 反向同步到庄园。
- 真人报名继续走共享 snapshot 语义，保留报名时 `current_hp`；不得为了复用而给共享 builder 增加 `force_full_hp` 隐式默认值。

---

## 9. Bootstrap 与增量维护

### 9.1 Bootstrap V2

Bootstrap 使用“锁外计划、锁内物化”的两阶段流程，避免在容量锁和档案事务内执行候选扫描与大量评分。

公共注册服务在 User 和 Manor Bootstrap 都成功后发出显式真人注册事件；`gameplay/signals.py` 的 receiver 在创建事务
`on_commit` 中通过 `population_runtime` 合并对应地区/声望段的持久人口需求，再投递一次加速唤醒，不在注册请求内物化 Bot。不能直接复用当前
`roll_virtual_players_task`，因为它还会调用 `maintain_due_virtual_players(limit=100)`；新增的 transport task 只调用
`population_runtime` 人口重算入口。Bot 创建、Admin 建号和 fixture 不发真人注册事件，防止创建 Bot User 时递归排队。

公共真人玩家的持久化声望跨越 V2 分段边界时使用同一 transport，在声望事务提交后投递旧段与新段的人口重算。事件按
地区和声望段合并，消费者幂等；重复、连续跨段或 worker 重试不会重复物化。该事件只负责人口，不运行 Maintenance；
可攻击存量按提交后的 `current_prestige_band` 计数，尚未完成的 Blueprint 仅按 `target_prestige_band` 防止同段重复创建。

Bot 自身通过建筑、科技、PVP 等既有领域规则改变声望并跨段时也投递该 transport。消费者通过 `profile_store.py` 先以
持久化 Manor 声望同步 `current_prestige_band`，再重算旧段和新段；历史 `target_prestige_band` 不变。同步和人口任务都可
重复执行，selector、参考快照和地图查询保持只读，不能在读取时隐式修正分段。

人口 worker 先以第 6.3 节 revision/token 契约 claim `BotPopulationRecomputeDemand`，释放 demand 行锁后再取得现有 ownership
token 和 `BotPopulationControl` 锁并重新计算目标，只创建或复活实际缺口数量；最后 fenced finalize claim。重复事件、并发注册
或已有充足供给不得产生额外 Bot。短时间同地区事件先合并，单次只处理配置允许的有界批次；仍有缺口或 claim 期间出现新 revision
时保持 pending 并续排。投递失败或 worker 暂时不可用由持久 demand 扫描及原定时全量人口任务兜底。因此“新真人出现”表示立即
触发供给判断，不等同于无条件一人创建一个 Bot，也不允许注册事件顺带执行 Maintenance。

该事件接线与当前生效生成器的四档强度保护必须在 Gate D1 同批启用。Gate D1 退出前现有 V1 仍是临时兼容路径，并先应用
相同的最终综合/分项上限；Gate D1 通过后当前环境的新建 Bot 全部由 V2 物化，样本不足时进入
`conservative_cold_start`，不得回退 V1。缺少真人样本不是人口 worker 的终止条件，只有人口无缺口、ownership 丢失或
配置/模板损坏等真实错误才能停止本轮创建。

阶段 A：只读计划，不产生副作用。

1. 加载并严格校验 plan schema、固定 policy version、模板目录和人口目标。
2. 批量建立真人参考快照，按第 8.1 节四档规则选择 anchor 或版本化 fallback；样本不足不能返回“禁止创建”。
3. 由 growth seed 生成 `BotDevelopmentPlan` 和不可变 `BootstrapBlueprint`；按目标声望段从冻结区间确定合理历史年龄，当前环境
   直接入组 V2，不用 bucket 决定是否创建。
4. Blueprint 包含目标建筑、科技、门客、家丁、装备、技能、护院、库存、资源及落在该历史年龄内的分散时间，但不包含 ORM
   实例或伪造的逐条成长记录。

阶段 B：在一个有界数据库事务内物化。

1. 验证人口容量所有权并再次检查全局、地区和声望段缺口；高段激活信号已经消失时不得继续物化。
2. 创建用户和庄园并处理名称、地区和坐标冲突，再通过 `profile_store.py` 创建 `BotProfile`；V2 档案在同一事务写入
   `engine_version=2`、`rng_version`、plan schema、policy version、policy checksum 和 `v2_enrolled_at`，并将
   `last_strength_increase_at` 初始化为同一个入组时间，防止完整历史快照被立即再次加速。
3. 重新校验 Blueprint 引用的模板、当前有效样本档位、综合/分项强度上限和全局库存额度；档位变化时按更严格上限
   重新钳制或放弃本次 Blueprint 后重算，禁止提交已经越界的历史快照。
4. 批量建立非均匀建筑、科技、门客、家丁、装备、技能和护院历史快照。
5. 建立受限库存和资源储备，回填分散历史时间。
6. 提交后记录结构化创建摘要、使用的样本档位、最终综合/分项强度和 anchor 摘要指纹。

坐标或名称冲突只重新选择身份字段并重试阶段 B，不重新抽取画像和 anchor。事务重试次数必须有上限。

Bootstrap 允许直接物化历史结果，因为模拟数月逐条动作没有用户价值，也会造成任务和数据膨胀。

### 9.2 Maintenance V2

Maintenance V2 使用“锁外候选计划、锁内重验并提交一个动作”的流程：

```text
load immutable policy/catalog
    -> build read-only snapshot
    -> generate ranked candidate intents
    -> begin transaction
       -> lock BotProfile and validate engine/trigger due contract/state/expected sequence
       -> apply lifecycle transition and stop, when due
       -> perform bounded mandatory settlement
       -> reload effective human sample tier, current prestige band, band cadence,
          last strength increase time and the profile's 24-hour strength budget
       -> filter positive intents by minimum spacing and per-action growth cap
       -> lock selected aggregate in the audited global order
       -> reload affected fields, resolve post-state band, reload destination sample tier when changed,
          and revalidate the first still-legal intent
       -> reject any intent whose post-state breaches source/destination band, action count,
          daily growth, per-action growth, composite or component cap
       -> execute exactly one synchronous development/support command, or record no-op
       -> atomically update strength budget and last strength increase time when strength rises
       -> advance sequence and apply trigger schedule disposition
       -> commit
    -> emit structured outcome
```

所有维护入口必须显式携带 `MaintenanceTrigger`，调度语义不能由调用栈或 `now` 猜测：

| Trigger | due 前置条件 | `APPLIED / NO_ACTION` 的 sequence | `next_growth_at` 语义 |
|---------|--------------|----------------------------------|-----------------------|
| `SCHEDULED` | 必须满足 `next_growth_at <= now` | 成功提交时递增一次 | 在同一事务按正常维护策略推进；旧值必须非空且新值严格晚于旧值 |
| `ARENA_ACCELERATION` | 不要求 due，但仍校验 engine/state/expected sequence | 成功提交时递增一次 | 原值逐值保留，不得先改后以事务外补写恢复 |
| `ADMIN` | 调用方必须显式给出 `requires_due` | 成功提交时递增一次 | 调用方必须显式选择 `ADVANCE_NORMAL_SCHEDULE` 或 `PRESERVE_NORMAL_SCHEDULE`；advance 必须写入非空且不同的新值，但非 due 管理操作允许从旧远期值重算为更早的未来值；缺省即拒绝 |

上述 schedule disposition 只约束已提交的 `APPLIED / NO_ACTION`。`BUSY`、基础设施失败和提交前回滚不推进
sequence，且 `BUSY` 必须逐值保留原调度；`PAUSED / INELIGIBLE` 不推进 sequence，其清空、保留或重排调度只按
生命周期/安全暂停契约处理，不能被 trigger 的普通 disposition 错误拦截，也不伪装成成功发展周期。当前 V1 竞技场加速会恢复原
`next_growth_at`，迁移时先以该行为作为 characterization contract；`tests/test_virtual_player_backfill.py`
中“已到期”和“未来调度”两种保留用例都必须迁到新 owner。

首版每周期最多提交一个发展动作。工资、到期资源同步和下一次调度属于有界的强制结算，不参与 Utility 评分；它们必须使用已有幂等 primitive，不能借此批量追平多个发展维度。正常调度从当前段的正向成长检查区间确定候选时间，再与更早的生命周期和强制结算期限取最早值；较早进入 Maintenance 只允许处理到期事项，仍不能越过最小正向动作间隔。

`guest_healing` 是同步支持动作而不是永久发展动作：它与训练、装备、技能、护院、建筑、科技和库存动作竞争同一个“每周期最多一个”
提交槽位，但允许在永久正向成长 spacing 尚未到期时执行。治疗只恢复既有 HP，不消费 24 小时强度动作/增幅预算，也不更新
`last_strength_increase_at`；它仍必须推进本次成功 Maintenance 的 sequence，并按 trigger 矩阵处理 `next_growth_at`。规划器先结算
被动恢复快照，再只为仍未满血且存在合法药品的门客生成候选，避免对已自然恢复的门客浪费药品。

强制资源结算的日 cap 不能只靠本次事务推算。`economy.py` 根据 `forced_settlement_daily_budget` 纯计算剩余额度，
`profile_store.py` 在 `BotProfile -> Manor` 锁内按 UTC 日期惰性重置，并在当天首次正向结算时冻结银两/粮食容量快照。
每次取“本周期容量 10%、该日每资源容量快照 50%、该日银两与粮食合计 2,000,000 剩余额度”中的最严格值；预算预留、
`resources.py` 的持锁增量及 `ResourceEvent` 必须同事务提交，失败时一起回滚。读取/规划路径不得重置预算，worker 重启、重试、
容量后续增长或 policy 切换不得清空当日已用量。该新增字段属于已授权的 Gate C additive schema，迁移不得顺带改写存量档案。

一个发展 command 可以原子写入完成该动作必需的成本、资源事件和派生属性，但不得顺带执行另一个可独立评分的发展目标。例如“装备已有武器”是一个动作，“获取武器后立即装备”是两个动作，必须拆到不同周期。

优先级必须稳定：

1. 生命周期或安全退出；
2. 工资和资源约束；
3. 从门客治疗、训练、建筑、科技、装备、技能、护院恢复和库存获取候选中选择一个动作；同等合法时，重伤主力治疗优先于永久成长；
4. 下一次调度。

生命周期发生迁移或安全校验失败时，本周期不得继续执行发展动作。候选在锁外生成只是建议，只有锁内重验通过的 command
才能写入；第一个候选失效时可依次检查已生成候选，但仍只能提交一个。每个可能提高强度的 intent 必须先预测提交后综合分、
各分项和目标声望段；最终值超过当前样本档位上限、24 小时动作/综合增幅预算、分段最小间隔或单次综合增幅上限时，该 intent
视为不可执行。受控动作最多跨一个边界，跨段时本次资格对来源段与目标段规则同时校验并取更严格值；成功后下一次正向动作
deadline 从本次提交时间按目标段 cadence 起算。全部候选因此失效时返回带完整
`domain_constraint / strength_cap / band_spacing / band_action_cap / multi_band_transition` 原因列表的 `NO_ACTION`；
顺序固定为该列顺序，首项是兼容 primary reason，24 小时动作次数或综合增幅预算统一映射为 `strength_cap`。生命周期和有界
强制结算仍可提交。

Arena acceleration 和 Admin 只改变 trigger 语义，不拥有样本保护、分段间隔或单次增长上限豁免。已经超过当前上限的 Bot
不自动降级既有资产，但停止一切提高强度的发展动作，并从自动匹配候选中排除；待真人 cohort 或 policy 上限追上后再恢复。
该排除由只读保护/候选 selector 执行，不能在查询路径隐藏写入。PVP 等玩家驱动的正常领域结果不由 Maintenance 预先拒绝；
原领域事务按第 6.3 节持久写入 reconciliation intent，提交后的快速唤醒和周期恢复均通过
`external_reconciliation.py` 调用显式 `profile_store.py` 命令记录强度增量、更新时间和跨段对账，超限时只执行上述冻结与匹配排除。

维护内部返回结构化 `MaintenanceResult`：

| 状态 | 含义 | 竞技场后备处理 |
|------|------|----------------|
| `APPLIED` | 已提交一个发展动作 | 保留租约，计一次成长轮次并重新评估 |
| `NO_ACTION` | 合法周期但无可执行动作，sequence 已推进 | 在绝对期限内保留租约并按间隔重试，不增加 `accelerated_growth_rounds`，不伪装为成长成功 |
| `BUSY` | 行锁竞争或档案正被其他 worker 处理 | 保留租约，不消费成长轮次 |
| `PAUSED` | V2 开关关闭、RNG/画像/policy release 损坏或安全暂停 | 释放训练租约并记录明确原因，禁止 V1 接管 |
| `INELIGIBLE` | 状态、生命周期或档案条件永久不适用 | 释放租约 |

`MaintenanceResult` 还冻结 payload 互斥：`APPLIED` 必须携带非空 `action_kind` 且不得携带 `reason`；其余四种结果
不得携带 `action_kind`，必须携带非空 `reason`。这样监控和调用方无需从空字段组合猜测结果含义。

兼容枚举 `AcceleratedGrowthOutcome` 在启用 V2 前增加 `NO_ACTION` 与 `PAUSED`，保留原三个值不改名；竞技场运行时调用迁移到完整 `MaintenanceResult` 后，旧枚举只服务兼容门面。

`NO_ACTION` 不能无限占用 pool 容量。`virtual_reserve_pool.py` 使用现有 `ArenaVirtualReserveMember.created_at` 计算 `no_action_lease_deadline = created_at + MAX_NO_ACTION_LEASE_AGE`，其中 `MAX_NO_ACTION_LEASE_AGE = 12 hours` 已在 Gate A 冻结为 pool 显式策略常量和测试契约；不新增模型字段，也不混入虚拟玩家 development policy。deadline 不因 retry、BUSY、demand version 更新或重验而重置。一次 `NO_ACTION` 到达或超过 deadline 时，在 demand 锁内把 member 转为 `EXHAUSTED`、清空 `next_acceleration_at`；`EXHAUSTED` 不计入 active capacity，因此同一轮 replenish 可以补入替代者。该 member 仍由 demand 持有直到正常关闭/消费，避免同一 profile 被另一需求并发租用。

### 9.3 虚拟监牢每日清理

虚拟监牢清理不复用单档案 Maintenance。它是所有 V1/V2 虚拟玩家共享的每日强制 housekeeping，避免暂停、退役、未到期或没有
可执行发展动作的档案永久跳过清理：

```text
Celery Beat once per calendar day
    -> capture one immutable cutoff
    -> select HELD JailPrisoner rows owned by Bot captors and captured_at <= cutoff
    -> order by captured_at, id
    -> process bounded batches
       -> begin transaction
       -> lock selected JailPrisoner rows with skip_locked where supported
       -> conditionally transition HELD -> RELEASED through jail service
       -> commit batch
    -> emit scanned/locked/released/skipped/failed and oldest_remaining_age
```

- `gameplay/tasks/virtual_players.py` 只做 transport；查询、锁和状态迁移由 `gameplay/services/jail.py` 拥有。
- cutoff 在任务开始时冻结并传入每个批次。任务运行期间新产生或时间晚于 cutoff 的囚犯不属于本轮，下一日再处理。
- 清理不锁 `BotProfile`、不写 `Manor`、不推进 Maintenance sequence、不消费强度预算，也不触发人口或竞技场对账。
- `v2_cutover`、`v2_paused`、V1/V2 混合状态和 Bot 生命周期终态都不关闭清理；只要 captor 仍由 `BotProfile` 标识就必须处理。
- `RECRUITED/RELEASED`、真人 captor 和已经被并发处理的记录全部跳过。任务不调用 `recruit_prisoner()`，不删除历史记录，
  不恢复原门客或装备。
- 每批事务有明确上限；失败记录保留 `HELD` 供下次重试，不能为了“清空成功率”吞掉数据库异常或把失败行直接改为终态。

### 9.4 幂等与失败

- 阶段 0 先审计 Raid、Admin、竞技场、门客、装备、技能、护院、建筑、科技、资源和库存现有写路径，形成全局锁顺序矩阵；阶段 4 不得自行假设锁顺序。
- intent 携带 `profile_id + expected_maintenance_sequence + action_kind + target_id + precondition_digest`，用于锁内重验，不把未持久化字符串宣称为数据库幂等键。
- `BotProfile` 行锁、领域写入、强度预算、`last_strength_increase_at`、sequence 和按 trigger 决定的 `next_growth_at` 在同一事务
  提交；sequence 只在 `APPLIED / NO_ACTION` 整个周期成功时递增。
- 事务提交前失败会完整回滚并保留原 sequence；提交后任务重试因 due time 或 expected sequence 已变化而成为 no-op。
- 首版没有事务外发展派发，因此不存在“已扣资源但等待 Celery 完成”的中间状态。未来引入异步动作前必须先实现执行记录/outbox、唯一约束和恢复扫描器。
- 编程错误原样抛出，不能被宽泛捕获并伪装成业务跳过。
- 单个档案失败不阻塞同批其他档案，但必须记录失败分类和档案 ID。
- 业务原因导致所有候选失效时，提交 no-op、推进 sequence，并严格按 trigger 矩阵推进或保留下一次时间；基础设施异常不推进 sequence。

---

## 10. 配置设计

继续使用 `data/virtual_players.yaml`，新增版本化 `bot_development_v2` 节点，避免首轮再引入第二份运行配置；该名称表示
虚拟玩家成长引擎，不是部署环境。部署环境仍由 `environment_mode: test` 唯一表达。

建议结构：

```yaml
bot_development_v2:
  environment_mode: test
  engine_version: 2
  rng_version: 1
  plan_schema_version: 1
  prestige_segmentation:
    band_schema_version: 2
    boundary_semantics: lower_inclusive_upper_exclusive
    configured_band_count: 8
    v2_bands:
      newbie: [0, 500]
      junior: [500, 2000]
      middle: [2000, 8000]
      senior: [8000, 30000]
      veteran: [30000, 60000]
      elite: [60000, 120000]
      legend: [120000, 240000]
      mythic: [240000, null]
    first_high_band: veteran
    empty_high_band_target_supply: 0
    high_band_activation_sources:
      - active_real_player_presence
      - explicit_map_search_demand
      - explicit_arena_demand
    lower_band_supply_counts_for_higher_band: false
    cross_band_reactivation_allowed: false
    cross_band_instant_strength_promotion_allowed: false
  routing:
    activation_mode: direct_after_gate
    bootstrap_mode: legacy_before_gate
    maintenance_mode: legacy_before_gate
  policy_rollout:
    target_version: 1
    enabled: false
    rollout_percent: 0
  policies:
    "1":
      checksum: "<sha256-of-normalized-policy>"
      max_development_actions: 1
      reference_calibration_min_profiles_per_band: 30
      reference_calibration_thresholds:
        normalized_wasserstein_max: 0.25
        normalized_quantile_deviation_p10_max: 0.35
        normalized_quantile_deviation_p50_max: 0.25
        normalized_quantile_deviation_p90_max: 0.35
        js_divergence_max_bits: 0.10
        hard_constraint_violations_max: 0
        robust_joint_outlier_rate_max: 0.15
        robust_joint_outlier_rate_above_real_max: 0.05
        component_fingerprint_collision_rate_max: 0.35
        joint_fingerprint_collision_rate_max: 0.15
        fingerprint_collision_rate_above_v1_max: 0.0
        archetype_standardized_effect_min_absolute: 0.20
        archetype_standardized_effect_max_absolute: 0.80
        archetype_effect_direction_must_match: true
        abandoned_rate_deviation_max: 0.10
      use_local_reference_when_profiles_gte: 1
      borrowed_global_reference_discount_ratio: 0.90
      borrowed_global_reference_usage: composition_anchor_only
      borrowed_global_may_raise_sample_tier: false
      borrowed_global_may_raise_strength_cap: false
      starter_snapshot_scope: per_prestige_band_conservative_entry_fixture
      starter_snapshot_requires_live_player_data: false
      zero_local_sample_cap_strategy: stricter_of_starter_90_percent_and_discounted_global
      anchor_k: 5
      strength_safety:
        no_reference:
          starter_snapshot_ratio: 0.90
          positive_jitter_bps_max: 0
          actions_per_24h_max: 0
          growth_bps_per_24h_max: 0
        sparse_1_4:
          cap_quantile: p50
          composite_cap_ratio: 1.05
          component_cap_ratio: 1.10
          positive_jitter_bps_max: 0
          actions_per_24h_max: 1
          growth_bps_per_24h_max: 300
        limited_5_29:
          cap_quantile: p75
          composite_cap_ratio: 1.10
          component_cap_ratio: 1.15
          positive_jitter_bps_max: 200
          actions_per_24h_max: 2
          growth_bps_per_24h_max: 500
        sufficient_30_plus:
          cap_quantile: p95
          composite_cap_ratio: 1.15
          component_cap_ratio: 1.20
          positive_jitter_bps_max: 500
          actions_per_24h_max: 4
          growth_bps_per_24h_max: 1000
        arena_acceleration_may_bypass: false
        admin_may_bypass: false
      prestige_band_growth:
        effective_limit_rule: strictest_of_sample_tier_band_profile_and_domain_constraints
        direct_prestige_grant_by_maintenance_allowed: false
        profiles:
          newbie:
            bootstrap_history_age_days: [1, 14]
            preferred_strength_check_interval_hours: [4, 8]
            minimum_positive_strength_action_spacing_hours: 4
            composite_growth_bps_per_controlled_action_max: 400
          junior:
            bootstrap_history_age_days: [14, 45]
            preferred_strength_check_interval_hours: [6, 12]
            minimum_positive_strength_action_spacing_hours: 6
            composite_growth_bps_per_controlled_action_max: 300
          middle:
            bootstrap_history_age_days: [45, 120]
            preferred_strength_check_interval_hours: [8, 16]
            minimum_positive_strength_action_spacing_hours: 8
            composite_growth_bps_per_controlled_action_max: 250
          senior:
            bootstrap_history_age_days: [120, 240]
            preferred_strength_check_interval_hours: [12, 24]
            minimum_positive_strength_action_spacing_hours: 12
            composite_growth_bps_per_controlled_action_max: 200
          veteran:
            bootstrap_history_age_days: [240, 360]
            preferred_strength_check_interval_hours: [14, 24]
            minimum_positive_strength_action_spacing_hours: 14
            composite_growth_bps_per_controlled_action_max: 200
          elite:
            bootstrap_history_age_days: [360, 540]
            preferred_strength_check_interval_hours: [18, 30]
            minimum_positive_strength_action_spacing_hours: 18
            composite_growth_bps_per_controlled_action_max: 175
          legend:
            bootstrap_history_age_days: [540, 720]
            preferred_strength_check_interval_hours: [24, 36]
            minimum_positive_strength_action_spacing_hours: 24
            composite_growth_bps_per_controlled_action_max: 150
          mythic:
            bootstrap_history_age_days: [720, 1080]
            preferred_strength_check_interval_hours: [30, 48]
            minimum_positive_strength_action_spacing_hours: 30
            composite_growth_bps_per_controlled_action_max: 125
        last_strength_increase_at_required: true
        arena_acceleration_may_bypass_band_spacing: false
        admin_may_bypass_band_spacing: false
        configured_boundaries_crossed_per_controlled_action_max: 1
        cross_band_uses_stricter_source_or_destination_limit: true
        external_domain_result_may_be_rejected_by_bot_growth_policy: false
        bootstrap_fake_per_action_history_records: false
      gear_upgrade_threshold: [0.08, 0.15]
      roster_tiers:
        core: [0.85, 1.00]
        secondary: [0.65, 0.85]
        bench: [0.35, 0.65]
      troop_mix:
        primary: [0.55, 0.75]
        secondary: [0.15, 0.30]
        scout: [0.03, 0.10]
      personas:
        balanced: {}
        rich: {}
        dojo: {}
        guard: {}
        abandoned: {}
```

当前测试环境使用显式 mode，不用两组可能产生非法组合的 enrollment/capability 布尔开关：

| 能力 | Gate 前 | 切换中 | 全量启用 | 安全暂停 |
|------|---------|--------|----------|----------|
| Bootstrap | `legacy_before_gate`：仅允许临时 V1 创建 | 不需要独立中间态 | `v2_active`：所有新 Bot 均为 V2 | `v2_paused`：停止物化，不允许 V1 接管 |
| Maintenance | `legacy_before_gate`：仅 V1 可维护，已创建 V2 保持暂停 | `v2_cutover`：V1/V2 发展写均停止，只允许入组/重建命令 | `v2_active`：仅 V2 可维护 | `v2_paused`：停止发展写，不允许 V1 接管 |

Gate D1 退出时 Bootstrap 从 `legacy_before_gate` 单向进入 `v2_active`；异常时只能进入 `v2_paused`，不能退回 V1。Gate E
readiness 通过后 Maintenance 先进入 `v2_cutover`，完成测试数据处理并验证运行时有效 V1 为 0 后进入 `v2_active` 并退出
Gate E；cutover 异常时保持 `v2_cutover`，Gate E 退出后的运行异常才进入 `v2_paused`。当前方案不提供 new/existing profile
百分比字段或 engine enrollment bucket；未来生产若
需要灰度，必须另立设计，而不是提前在测试环境实现未使用的路由。

示例中的 persona 权重、k 值和发展选择参数仍不是已发布值，必须在 Gate C 发布 policy version 1 时另行评审；这也不构成
未来生产批准。样本、自然度、经济、性能与当前环境启用/暂停准入值已在 Gate A 冻结于
[`virtual_player_gate_a_acceptance_config_2026-07-27.yaml`](virtual_player_gate_a_acceptance_config_2026-07-27.yaml)；
它是验收事实来源，不是当前运行时 routing 配置。`release_virtual_player_policy --version 1` 对不包含 `checksum`
字段本身的规范化内容计算 SHA-256，核对声明值并创建 `BotPolicyRelease`。发布记录本身不授权 routing；Gate D1 的
固定 fixture 强度保护、硬约束和经济门禁通过后，当前环境直接全量启用 `conservative_cold_start` Bootstrap；Gate D2
门禁通过前只有参考分布校准 routing 保持关闭。Gate E readiness 的 Maintenance 性能/锁门禁通过前保持
`maintenance_mode=legacy_before_gate`，通过后按 cutover 顺序切换，不能直接跳过 V1 清零证明进入 `v2_active`。未来生产
routing 不在本文授权范围内。

配置校验必须覆盖：

- engine、RNG、plan schema、policy 版本为正整数，目标 policy 必须同时存在于 YAML 发布输入和不可变注册表；
- `environment_mode` 首版必须明确为 `test`，`activation_mode` 必须为 `direct_after_gate`；Bootstrap mode 只允许
  `legacy_before_gate / v2_active / v2_paused`，Maintenance mode 只允许
  `legacy_before_gate / v2_cutover / v2_active / v2_paused`；
- mode 只允许第 10 节状态表定义的单向迁移；Gate D1/Gate E 退出后禁止回到 `legacy_before_gate`，`v2_cutover` 下禁止任何
  V1/V2 发展写，`v2_paused` 下禁止 V1 fallback；
- new/existing profile 百分比字段和旧 enrollment/capability 布尔字段都属于未知字段并拒绝，独立 policy rollout 仍校验在 `0..100`；
- V2 声望段必须恰好连续覆盖 `[0, +∞)`、互不重叠、下限小于上限且只有一个开放终段；段名顺序与 policy、人口及
  参考快照引用一致，`configured_band_count` 必须为 8；
- 高段无真人活跃或显式需求时目标供给必须为 0，低段供给不得计入高段，跨段复活和瞬间强度/声望提升必须为 false；
- 首版 `max_development_actions` 必须严格等于 1；
- 已发布 policy version 的规范化内容与声明、`BotPolicyRelease` 及档案持久化的 checksum 一致；同版本不同 checksum 的发布必须失败；
- 范围为有限数值且低值不大于高值；
- 比例总和可规范化且至少一个正值；
- persona key 只能使用现有 archetype；
- troop class 必须来自技术目录；
- 建筑和科技 key 必须存在；
- anchor 样本数和 k 为正整数，k 不超过可用候选数时正常选择，否则显式使用全部候选；
- 四个强度档位只按本地同地区、同声望段样本数覆盖 `0 / 1--4 / 5--29 / 30+` 且不重叠，比例和基点非负，档位越充足才允许
  放宽；全局借样不得提高档位，0 样本档位必须禁止正向扰动和提高强度动作，最终上限取新手快照 90% 与九折全局参考中
  更严格者；
- 每个 V2 声望段必须提供独立、版本化且不依赖真人数据的保守起点 fixture；应用 90% 上限后仍落在目标声望段，缺少、
  越段或引用其他段 fixture 时配置拒绝加载；
- 分段成长 profile 必须与八档名称和顺序一一对应，不得缺段或增加未知段；历史年龄、检查区间和最小正向动作间隔均为
  有限非负数且下限不大于上限；段位越高，历史年龄及各间隔上下界只能不减，单次综合增长基点上限只能不增；
- `last_strength_increase_at_required` 必须为 true，Maintenance 直接赠送声望、Arena/Admin 绕过分段间隔、单个受控动作跨越
  多于一个边界及伪造 Bootstrap 逐条动作历史均必须被拒绝；跨段动作必须同时校验来源段和目标段并取更严格值；
- 综合、声望、核心建筑、门客、竞技阵容和护院分项使用已登记的版本化评分，强度预算在 Profile 锁内原子消费；
- Arena acceleration 和 Admin 的旁路值首版必须为 false；
- 未知字段拒绝，不能静默忽略拼写错误。

`core/utils/yaml_validators/virtual_players.py` 是离线和运行时共享的结构校验入口；`config.py` 在深合并前拒绝未知字段和非法类型，不能只依赖部署前命令。热刷新只允许执行合法 routing mode 迁移并重新解析发布输入，不创建或覆盖 `BotPolicyRelease`，也不自动重建画像或改变档案 policy。声望段边界不得热改；任何调整必须提升 `band_schema_version`，通过显式重分段命令和对应 gate 后切换。

画像 schema 升级和 policy 升级是两个独立批处理：前者重建个体 JSON，后者只允许指向已发布记录并原子更新 `policy_version + policy_checksum`。二者都使用稳定 SHA-256 bucket、行锁、批次上限和结构化结果。

---

## 11. 数据迁移与存量策略

### 11.1 迁移原则

- 只做增量字段迁移，不删除旧字段，不批量改写门客、装备和护院。
- schema migration 只添加默认值，不在迁移事务中加载 YAML 或运行复杂策略。
- Gate C migration 为存量档案写入 `engine_version=1`、`rng_version=0`、`plan_schema_version=0`、`policy_version=0`、空 policy checksum 和空画像，并创建空的 `BotPolicyRelease`、`BotExternalStrengthReconciliation`、`BotRuntimeRoutingState` 表；不在 schema migration 中加载 YAML、创建 routing 单例、发布策略或自动入组 V2。Gate E 的安全事件与闭合窗口表使用后续 additive migration，不塞入 Gate C 以伪装 safety provider 已完成。
- Gate D1 使用后续 additive migration 创建空的 `BotPopulationRecomputeDemand` 表；默认 revision 均为 0，不预建地区/分段行，
  不在 migration 中投递任务、重算人口、切换八档或改变 routing。该 migration 必须在 Gate C 之后、Gate D1 runtime 接线之前独立验证。
- `0141_bot_runtime_policy_rollout.py` 只给 routing 单例增加受约束的 policy rollout 字段；默认关闭且比例为 0，不创建单例、
  不读取 YAML、不发布或分配 policy。当前 rollout 事实只能在后续显式 transition 中改变。
- 存量计划在首次 V2 入组事务中生成；当前非生产环境使用一次性显式全量入组命令，物理上可按批次处理，但不做百分比抽样，也不执行独立“先回填画像、以后再决定执行器”的模糊状态。
- 回填使用 `select_for_update(skip_locked=True)` 和批次上限；批次只是控制事务规模，不代表灰度比例。

### 11.2 存量虚拟玩家

- 当前是测试环境且属于非生产，不存在生产存量迁移。Gate E readiness 通过后先进入 `v2_cutover`；可丢弃的 V1 fixture/Bot
  在 Gate E 退出前重建为 V2。任何删除或批量重建执行前仍需明确确认。
- Gate D1 切换八档前，显式重分段命令以持久化 Manor 声望为唯一事实来源，按新 `band_schema_version` 幂等处理现有档案；
  它只更新分段归属，不修改声望、资产、阵容、生命周期或执行器版本。dry-run、边界计数和重复执行无变化是切换前置证据。
- 必须保留的测试数据不撤销历史模板替换、不重建已有门客主键；通过一次性显式命令把所有 ACTIVE/SLOWING/ABANDONED 及可重新激活档案入组 V2。
- 入组事务同时写入 `engine_version=2`、`rng_version`、plan、policy version、policy checksum 和 `v2_enrolled_at`；首次 V2 维护采用当前阵容作为历史起点。
- 装备、技能和护院从当前值逐步收敛，不在切换当天整体洗牌。
- `STALE` 不自动原地入组；若只是可丢弃测试数据则随测试库重建，若必须保留则冻结为不可运行记录，不能由任务、Admin
  或 Arena 重新激活。`RETIRED` 仍可能被生命周期复活，因此属于 Gate E 必须处理的运行时有效 V1，不能以终态名义留存。
- `engine_version=2` 的 RNG 版本、画像或 policy release 任一损坏都不会被 V1 接管，按第 6.3 节 fail closed，并按损坏类型进入显式修复或安全暂停队列。

### 11.3 环境路由与粘性归属

当前测试环境不实现 engine enrollment bucket 抽样：Gate D1 退出后每个新建 Bot 都进入 V2；Gate E readiness 通过后一次性处理全部
运行中或可重新激活的存量测试档案。routing mode 只表达“gate 前兼容、切换中、V2 已启用或 V2 已安全暂停”，不表达
1%/5% 等 engine 灰度阶段。独立 policy rollout 可以在已经是 V2 的档案之间使用稳定百分比分桶，它不改变 engine、RNG 或画像，
也不构成生产 rollout 授权。未来生产若需要 engine 百分比路由，必须在上线前另立设计、验证 canonical bucket 和回滚边界。

`data/virtual_players.yaml` 的 routing 块只定义允许模式和初始化默认值，不是当前状态。Gate C migration 不创建数据行；显式
`transition_virtual_player_routing` 命令第一次以 expected-absent 初始化 `BotRuntimeRoutingState(key="virtual_players")`，随后所有命令、
Gate workflow 和 safety monitor 均通过 `gameplay.services.runtime_configs` 在行锁内校验 expected revision/current modes 并 CAS。
状态行保存 bootstrap/maintenance mode、revision、小时/日 safety window cursor、最近 pause window 和原因；窗口按类型严格单调处理，
重复或更旧 window 幂等 no-op，中间 window 缺失则 fail closed。任何 V2 入组前缺行按 `legacy_before_gate` 处理；存在 V2 档案后缺行或损坏必须暂停，不能回退 YAML 或进程缓存。

当前 engine 转换只允许 `engine_version=1 -> 2`：

- Gate E 的“运行时有效 V1”精确谓词是
  `engine_version = 1 AND state IN (active, slowing, abandoned, retired)`；`stale` 明确排除。due time、routing mode、fixture
  标签和 Arena membership 均不改变该集合，`retired` 因可重新激活而必须计入。`virtual_player_core.profile_store`
  拥有该精确 count query，`gate_e_cutover_workflow` 在事务一致快照上拥有清零验证；只有计数严格为 0 才能退出 Gate E。

- `bootstrap_mode=legacy_before_gate` 只允许存在于 Gate D1 退出前；退出后直接选择全部新档案，异常时切到 `v2_paused`，不等待
  生产观察期，也不回退 V1。
- `maintenance_mode=v2_cutover` 会停止 V1/V2 发展写，只允许显式入组、重建和验证命令；运行时有效 V1 清零后才能切到
  `v2_active` 并退出 Gate E。
- `maintenance_mode=v2_paused` 只保留共享生命周期能力检查和有界调度；V2 档案不会改走 V1。
- 安全暂停不推进 `maintenance_sequence`，并把下一次检查时间推迟到配置的 bounded interval，避免持续扫描同一批到期档案。
- routing mode 切换不修改已入组档案的 engine/RNG/plan/policy 归属；不提供自动 `engine_version=2 -> 1` 路径。确需降级时
  必须另做数据兼容审计和显式操作确认。

Policy rollout 当前状态不是热 YAML 事实，而是同一 `BotRuntimeRoutingState` 行上的 target/enabled/percent。初始化前保持
disabled；启用、换目标、改变比例和停用必须调用 `transition_virtual_player_policy_rollout`，提供 expected revision 与完整
expected-current 三元组。该 transition 先锁 routing 行，再按 version 顺序锁 policy release；停用或换目标在同一事务内将旧
target 的 `retire_not_before` 延长到至少数据库事件时间后 720 小时。rollout batch 持有 routing 行锁并校验相同 revision，
因此 calibration、mode 或另一 rollout transition 的任何并发提交都会让 stale batch fail closed。

Policy 升级使用独立稳定 bucket，只更新已是 V2 的档案：

```text
policy_bucket = sha256_bucket(
    domain="policy_rollout",
    discriminator=(profile.id, target_policy_version),
) % 100
eligible = policy_rollout.enabled and policy_bucket < policy_rollout.rollout_percent
```

分桶身份由 `(profile_id, target_policy_version)` 固定；50% 提高到 100% 只补齐剩余档案。降低 policy rollout 不自动降级已更新档案；
策略回滚通过新的显式 rollout target 或人工 `upgrade_virtual_player_policy` 指定上一份仍保留的兼容 policy version 执行，不改变 engine 或画像。

---

## 12. 分阶段实施

### 阶段 0：锁定基线

**目标**

在移动运行时逻辑前补齐行为和边界证明。

**交付**

- 第 5.3 节公共入口清单的逐项核对、显式 `__all__` 草案和 import 契约测试。
- 生命周期转换表测试。
- 创建、维护、人口、库存额度、竞技场保护和 Raid 掉落退休建议的 characterization tests。
- 全部相关写路径的锁顺序矩阵，包括 Raid 结算、竞技场、地图人口、Admin、门客、装备、技能、护院、建筑、科技、资源和库存。
- 第 8.9 节领域 command matrix，标明现有 owner、待提取 primitive、事务和副作用。
- 全仓 `BotProfile` 读写清单、只读 allowlist，以及覆盖 manager/QuerySet/instance/bulk/upsert/delete 的写入门禁与负例夹具；同时建立 `ArenaVirtualDemand / ArenaVirtualReserveMember` 状态迁移清单和允许 owner。
- H-01 已冻结为 Raid 成功提交后的非持久、at-most-once command，并接受进程/基础设施窗口丢失退休建议；完成对应故障注入和交叉并发证明。
- `SCHEDULED / ARENA_ACCELERATION / ADMIN` trigger characterization、sequence/调度矩阵，以及竞技场 `MAX_NO_ACTION_LEASE_AGE = 12h` 和 `created_at + 12h` 不重置契约。
- 竞技场依赖图与 import 契约，明确赛事转换 primitive 下沉到 `lifecycle_helpers.py` 后不存在 core/fill 双向依赖。
- 只读基线报告契约及隔离 fixture 证据：门客等级差、稀有度、装备、技能、护院集中度、联合异常率、
  一致性快照、分段样本 fail closed 和身份字段排除；不访问现有环境玩家数据。
- 代表性分布采集归 Gate D2，禁止用开发 fixture 冒充真人基线；Maintenance 耗时、写查询量和锁等待 benchmark 归
  Gate E，使用 disposable database 的固定场景 fixture，不需要真人数据。两者均不阻塞 Gate B 的行为等价结构提取。
- 冻结的自然度阈值表、最小样本数、无样本 fallback、查询预算及当前非生产环境启用/暂停条件；不冻结生产观察周期。
- 冻结注册提交后异步人口重算、只补缺口、定时兜底，以及样本不足不阻止创建的可用性契约。
- 冻结 0、1--4、5--29、30+ 样本四档 Bootstrap/Maintenance 综合强度、分项、正向扰动和 24 小时成长预算。
- 冻结八档 Bootstrap 历史年龄、Maintenance 检查区间、最小正向动作间隔和单次综合增长上限；高段单调减速，实际限制与
  样本档位及领域规则取最严格值，当前 V1 成长配置不变。

**退出条件**

- 现有虚拟玩家相关单测全绿。
- 关键并发集成测试在真实 MySQL/Redis 环境通过。
- 基线报告可在隔离测试数据上重复生成，且结果不包含账号、展示身份或原始对象 ID。
- 公共入口、锁顺序和 command matrix 均有明确 owner，未决项为零。
- H-01 采用的投递保证、故障窗口和回退方式已经批准；否则禁止进入 Gate B。
- Maintenance trigger 矩阵与 `MAX_NO_ACTION_LEASE_AGE` 已冻结为可执行契约，不留默认语义。
- 自然度、经济和性能阈值已填写为实际数值并完成评审，不能以“合理区间”代替。
- 创建可用性与参考分布校准门禁已解耦；冷启动强度保护缺失时不得启用注册事件接线。

#### Gate A 证据清单与回放

Gate A 的 canonical 命令固定为 `DJANGO_TEST_USE_ENV_SERVICES=1 make test-virtual-player-gate-a`，机器可读记录位于
[`virtual_player_gate_evidence_manifest_2026-07-28.yaml`](virtual_player_gate_evidence_manifest_2026-07-28.yaml)。manifest
必须记录精确环境类别、Makefile 中的 contract/real-service 文件集合、预期收集数量、nodeid SHA-256、证据时间和执行结果。
checksum 输入严格为 pytest nodeid 按字典序排序后逐项追加 LF，并保留最后一个 LF；不能对控制台摘要、文件名集合或未排序
输出计算摘要。

回放时先核对 manifest 的 suite selection 与 Makefile 变量一致，再核对收集数量和 checksum，随后才运行 canonical target。
测试文件、参数化 case 或 Makefile 选择发生变化时，旧 checksum 立即失效；只有完整 canonical target 实际通过后才能把本次
执行标为 `passed`。此前 baseline、H-01 聚焦命令和完整 critical suite 的结果保留为历史证据，不能冒充新 target 的执行
结果。manifest 及其契约测试只治理 Gate A 证据；Gate C-E 的 schema、worker 和关闭状态 runtime 能力由本轮独立授权实施，不得从 Gate A 结果推导出运行模式启用权限。

### 阶段 1：架构提取（H-01 边界收口已作为前置 Surgical Fix 完成）

**目标**

唯一 High 风险 H-01 已在 Gate B owner 迁移前作为独立 `Surgical Fix` 完成：退休动作位于 Raid 成功提交之后，
并通过聚焦故障语义与真实 MySQL/Redis 交叉竞态门禁。Gate B 从模块所有权提取开始；后续只能迁移 H-01 owner，
不得改变已冻结的投递保证。H-01 与后续结构提取仍保持独立部署和回退边界。

**纵向交付顺序**

1. `[已完成的 Gate B 前置项]` H-01：`virtual_player_loot_limits.py` 只返回决策，Raid 成功提交后按 Gate A 选定的投递保证执行幂等退休命令，并完成提交/回滚/故障窗口与三组交叉竞态证明。后续只迁移 owner，不再改变投递契约。
2. 建立显式公共门面、纯 `contracts.py` 和 import 契约，不移动实现。
3. 提取 `config.py / identity.py / random_context.py`；V1 仍使用原 seed 结果，V2 版本化路径保持关闭。
4. 拆解 `virtual_player_rules.py`：生命周期日期/决策进入 `lifecycle.py`，V1 分位、persona 和 bounded projection 进入 `legacy/projection.py`，原文件仅在确认外部兼容需求后短期保留。
5. 提取 `backfill.py / population_runtime.py`，纯 `population.py` 保持不动；需求存储与人口执行不互相伪装为 selector。
6. 提取 `inventory_budget.py` 和 `legacy/inventory.py`，把纯候选计算与全局计数器事务分开。
7. 提取 `catalog.py / reference_snapshots.py / selectors.py / profile_store.py`；前三者只读，后者成为唯一 BotProfile 写 owner，并启用完整 DML 门禁。
8. 提取 `legacy/projection.py / legacy/roster.py`，再接入行为等价的 `bootstrap.py / maintenance.py`，不引入 V2 算法。
9. 拆分竞技场 `virtual_reserve.py / virtual_backfill.py`，同时下沉赛事转换 primitive、消除 core/fill 双向依赖；按 demand state、reconcile、pool、fill、scan、references、observability、lineup、protection 迁移所有权，不改变租约算法。
10. 迁移 Admin、tasks、map view、runtime config、arena、loot 和管理命令调用后缩小 `virtual_players.py` 门面。

每一项都是可独立合并、部署和回退的小阶段，必须在行为等价测试通过后再开始下一项。禁止先建立多个只转发调用的薄模块，再一次性移动全部实现。

**退出条件**

- 现有测试行为等价。
- 纯模块无 Django/model 导入。
- 运行时调用方只使用公开入口。
- 没有新增循环导入。
- `selectors` 无写副作用，`profile_store` 不写其他领域模型。
- `virtual_player_loot_limits.py` 无写副作用，Raid 回滚/提交后的退休语义通过。
- H-01 的实际实现与 Gate A 选定的投递保证一致，故障注入没有出现提交前退休或重复副作用。
- `virtual_players.py`、Admin 和 arena 不再直接写 `BotProfile`。
- `BotProfile` 完整 DML 门禁和只读 allowlist 负例通过，不存在实例 `save/delete` 或 alias/bulk/upsert 旁路。
- `virtual_reserve.py` 门面无 ORM/事务且仓内生产消费者为零；赛事 core、match helper、task 和 Admin 不再直接写 demand/member，demand/pool/fill 的迁移级 owner 门禁通过。
- 竞技场 import 契约证明 demand/reconcile/pool/fill/scan 子图无强连通分量且不反向依赖赛事 core/coop lifecycle，赛事转换 helper 不依赖 virtual reserve。
- 固定赛事、profile 和 demand version 下，竞技场 lineup、ready 排序、租约数量及填充结果与 Gate A 基线逐项一致。
- `legacy/*` 只有 V1 router 调用且有明确退场测试。
- 每个小阶段都有对应 owner、回退点和查询数对比。

### 阶段 2：策略模型与配置

**目标**

加入 V2 所需数据，但保持 Bootstrap/Maintenance routing mode 为 `legacy_before_gate`、policy rollout 为关闭；这是尚未通过功能
gate，而不是为当前环境安排 0% 灰度。

**交付**

- BotProfile 增量字段（含 V2 必填的 `last_strength_increase_at`）、`BotPolicyRelease`、带 fencing/quarantine 的外部对账 intent、持久 routing 单例和 additive migration。
- `strength_budget_entries` 的严格有界 parser，以及 Profile 锁内裁剪、并发校验和原子消费契约。
- `BotDevelopmentPlan` 生成、规范化、序列化、schema 升级和修复测试。
- 持久化 RNG 版本、canonical digest、稳定 policy bucket 和旧版本保留测试。
- 不可变 `BotDevelopmentPolicy` 发布注册、checksum 校验和 routing 热刷新行为。
- policy `retire_not_before` 单调生命周期、持久 policy rollout、共享 routing revision CAS、缺失状态 fail-closed 和并发命令测试。
- 八档成长 profile 的严格解析、同序完整性、单调减速和安全常量校验；缺段、乱序或尝试开放旁路时发布失败。
- 策略发布、新建/存量独立粘性入组、RNG/画像修复、policy 恢复/升级、rollout transition 与 rollout batch 命令；此阶段只提供能力，不启用。
- 结构化维护动作结果契约。

**退出条件**

- 两个 routing mode 均为 `legacy_before_gate` 时，阶段 2 的存量档案保持 `engine_version=1`，数据库业务行为与 V1 相同。
- 相同 seed、engine/RNG/plan schema 和 policy release 得到相同计划及随机子流。
- 非法 JSON 或不兼容 schema 在 V2 上 fail closed，显式修复可重放且不会回退 V1。
- routing mode 或 policy rollout 调整不改变任何已入组档案的 engine、RNG、plan 或 policy 归属。
- policy 内容被原地改写或 checksum 不符时加载失败。
- 同一 policy version 在重启后不能以不同 payload 再发布。

### 阶段 3：Bootstrap V2

**目标**

让人口缺口在真人注册提交后及时补足，并让当前测试环境的所有新建虚拟玩家使用受强度保护的 V2 自然化历史快照；
参考样本不足时使用保守冷启动，不停止人口供给，也不回退 V1。

**交付**

- 按 `(region, prestige_band)` 唯一合并的持久 `BotPopulationRecomputeDemand`、revision/token claim/finalize service 和周期恢复扫描；
  demand schema 先独立落地且保持空表，不能借 migration 切换 routing。
- 公共注册事务 `on_commit` 后按地区/声望段合并持久需求并投递幂等的专用人口重算任务；它不运行 Maintenance，注册请求不等待物化，
  原定时人口任务保留为兜底，Bot/Admin/fixture 建号不触发。
- 八档 V2 声望 schema、边界校验和显式幂等重分段命令；当前 V1 五档配置保持不变，直到 Gate D1 与 V2 生成器及强度保护
  原子切换。
- 公共真人声望跨段提交后合并重算旧段和新段；空高段仅由真人活跃、地图搜索或竞技场显式需求激活，低段 Bot 不计入
  高段供给，禁止跨段复活或瞬间抬升声望。
- Bot 通过合法领域动作自然跨段后由 `profile_store.py` 幂等同步当前段并重算旧/新两段；不改历史目标段，读路径无修复副作用。
- 人口锁内只补实际缺口；Gate D1 切换前为临时 V1 路径补齐同一安全保护，Gate D1 通过后当前环境只允许 V2 生成器创建新 Bot。
- 本地样本优先；仅本地为 0 时九折借用全局同段的组合结构，但仍保持 0 样本档，最终强度上限取九折全局参考与版本化
  新手快照 90% 中的更严格值。
- 只读计划、锁内物化的 `BootstrapBlueprint`。
- 非均匀建筑和科技路线。
- 分层门客阵容。
- 与容量和历史发展一致的家丁储备。
- 角色适配装备与技能。
- 主副护院流派。
- 按八档冻结年龄区间投影的分散历史时间；不伪造逐条成长记录，历史物化仍服从最终强度上限。

**退出条件**

- V1 与 V2 人口、容量和掉落契约相同。
- 注册事务回滚不派发人口任务；提交后任务可重复且不超量，人口无缺口时不创建，worker 暂时失败后定时任务可补偿；
  Bot User 创建不递归投递，专用任务测试证明没有调用任何 Maintenance 入口。
- demand claim 期间的新 merge 在旧 claim 完成后仍为 pending；过期 token 无法 finalize，新旧段 handoff 与 intent `APPLIED`
  同事务，任务投递丢失后周期扫描仍能完成，失败 backoff 不丢弃 request revision。
- 八档边界值、连续性、开放终段和存量重分段测试通过；真人跨段提交后只重算旧/新两段且可合并重试，回滚不投递。
- 每段零样本保守起点 fixture 都无需真人数据且在应用 90% 上限后仍合法落段；Bot 自然跨段同步当前段并重算人口，
  重试不重复写且不修改历史目标段。
- 八档历史年龄使用固定 seed 落在各自冻结区间，段位越高区间不倒退；Bootstrap 不生成动作流水，并将
  `last_strength_increase_at` 初始化为入组时间，防止创建后立即被加速。
- 空高段无激活信号时目标供给为 0；有显式需求时只创建新 V2 或复活同段 Bot，低段供给、跨段复活和瞬间声望提升均不能
  填补高段缺口。
- 0、1--4、5--29、30+ 样本下均可在存在人口缺口时创建，并逐档满足强度、正向扰动和 Blueprint 最终上限；
  注册事件接线不得早于当前生效生成器的保护门禁。
- 参考分布校准 routing 启用时，V2 分布达到阶段 0 冻结的数值阈值；样本未达门禁时仅运行
  `conservative_cold_start`，不得伪称已经分布校准。
- 创建事务、坐标冲突和库存额度回滚通过。
- 创建性能在约定阈值内。
- 日志不记录真人用户 ID，候选评分无 N+1 查询。
- Gate D1 退出时 `bootstrap_mode=v2_active`，当前环境所有新 Bot 均由 Bootstrap V2 创建，不存在仍由 V1 创建的新 Bot。

### 阶段 4：Maintenance V2

**目标**

让当前测试环境中符合条件的 V2 档案全部使用增量维护行为。

**进入条件**

- 阶段 0 的锁顺序矩阵和领域 command matrix 已重新对照当前代码并获批准。
- 所需共享 primitive 已明确由对应领域维护者拥有，不在虚拟玩家模块复制。
- 首版范围确认只包含同步单动作；任何异步动作另立设计。
- 第 9.2 节 trigger/调度矩阵和 `MAX_NO_ACTION_LEASE_AGE` 已按 Gate A 冻结值实现为测试契约。

**交付**

- expected sequence、precondition digest、锁内重验和事务提交协议。
- 每周期最多一个门客治疗、门客训练、装备调整、技能学习、护院恢复、建筑、科技或库存同步 intent；治疗是非永久强度支持动作，
  其余质量动作继续受强度预算约束；竞技场数量阶段另有每周期最多 2 名低稀有度门客的受限扩容动作，不接入 V1 招募候选或模板晋升。
- `guest_healing` 复用生命与库存领域 command，一次只治疗一名门客、原子消耗一件已有药品并保持重伤解除语义；无药时不创造免费恢复。
- 独立于 Maintenance/routing 的每日虚拟监牢清理 task，按固定 cutoff 分批把虚拟 captor 的 `HELD` 囚犯迁移为 `RELEASED`；
  不自动招募、不删除记录、不返还原门客、不影响真人监牢。
- 第 8.9 节真人 command 与压缩 command 的共享 primitive 和契约一致性测试。
- 资源增量与预算。
- archetype 特有停滞和投入行为。
- Profile 锁内重验当前样本档位，并原子消费 24 小时提高强度动作数和正向综合增幅预算。
- Profile 锁内重验当前/目标声望段、`last_strength_increase_at`、最小正向动作间隔和单次综合增长上限；提高强度时与领域
  写入原子更新时间，正常检查时间按目标段推进。
- 管理员和竞技场加速入口复用同一维护边界。
- `SCHEDULED` 只处理 due 档案并推进正常调度，`ARENA_ACCELERATION` 不要求 due 且原值保留 `next_growth_at`，`ADMIN` 不允许缺省调度语义。
- 竞技场明确处理 `APPLIED / NO_ACTION / BUSY / PAUSED / INELIGIBLE`，不会因新状态落入未知分支。
- 普通赛/共斗虚拟补位按第 8.10 节在 `virtual_lineups.py` 生成满血 snapshot 副本，并由 `virtual_backfill.py` 在锁内物化前
  fail closed 重验；该规则跨 V1/V2 生效，不依赖 Maintenance routing，也不改变真人报名或真实 `Guest`。
- `NO_ACTION` 不消费成长轮次，并在基于 member `created_at` 的绝对期限到达后转 `EXHAUSTED`，同轮补池可补替代者。
- 外部对账两阶段的 token fencing、各 12 次有界 retry、永久错误 quarantine、同档案顺序和跨段 `PENDING_POPULATION -> APPLIED` 恢复协议。
- `BotSafetyMetricEvent/BotSafetyMetricWindow` 有保留期的 provider、闭合窗口聚合、heartbeat 完整性、迟到/重复事件和 routing CAS。

**退出条件**

- 重试、并发和回滚测试通过。
- 提交前失败、提交后任务重投、业务 no-op 和基础设施失败的 sequence 语义全部通过。
- 三种 trigger 的 due、sequence 与 `next_growth_at` 矩阵全部通过；竞技场加速不会漂移正常维护时间。
- 连续 `NO_ACTION` 不会无限占用 active reserve capacity，deadline 重试不延长且无需 schema 字段。
- 过期 claim 的旧 worker 无法 finalize；坏 payload 不会无限重试，quarantine 不会被自动匹配或后续 intent 越过，跨段人口 handoff 可从独立 pending/claimed 阶段恢复且不重复 profile 预算。
- 维护不会改变门客模板身份。
- 门客治疗与真人药品入口保持资格、效果、库存消费和回滚 parity；治疗不消费永久强度预算，不更新
  `last_strength_increase_at`，且不能与同周期其他同步动作同时提交。
- 虚拟监牢日清重复执行幂等，跨 V1/V2、cutover/paused 均生效；真人监牢、非 `HELD` 记录和 cutoff 后新囚犯逐值不变。
- 普通赛和共斗的所有虚拟 Entry guest snapshot 均为满血；残血输入在物化边界整笔回滚，庄园 Guest/库存/状态不变，
  真人报名 snapshot 仍保留实时 HP。
- 四档样本的 24 小时动作/增幅预算和综合/分项上限均有事务并发测试；Arena/Admin 不能旁路，超限 Bot 不进入自动匹配。
- 八档检查区间、最小间隔和单次上限逐档测试通过；Arena/Admin 不能绕过间隔，受控动作最多跨一个边界且同时满足来源/目标
  段的更严格限制，PVP 等玩家驱动结果不被成长策略回滚但会触发增量记录、跨段对账及必要的成长冻结。
- V2 经济注入不超过 V1 上限。
- 单批维护耗时和查询量达到运行目标。
- Maintenance V2 没有按档案派发的长期 Celery 完成任务。
- safety provider 未健康、窗口缺指标/heartbeat、窗口断档或晚于 finalize 的事件都会通过持久 routing CAS 进入 `v2_paused`；累计 task counter 和进程内 fallback 无法让 Gate E 退出。
- Gate E 退出时 `maintenance_mode=v2_active`，当前环境所有可执行或可重新激活的测试档案均为 V2，运行时有效 V1 数量严格
  为 0。只允许冻结 fixture 或不可运行的终态 V1 测试记录等待明确确认后的清理。

### 阶段 5：非生产全量验收与调参

**顺序**

```text
Bootstrap/Maintenance mode 均为 legacy_before_gate
  -> Gate D1 通过：bootstrap_mode=v2_active，当前测试环境的新建 Bot 全部使用 Bootstrap V2
  -> Gate E readiness 通过：只授权开始切换，不等于 Gate E 退出
  -> maintenance_mode=v2_cutover：停止 V1/V2 发展写入
  -> 可丢弃 V1 测试数据经明确确认后重建；需保留数据一次性显式入组 V2
  -> 验证所有运行中或可重新激活的档案均为 V2，运行时有效 V1 数量为 0
  -> maintenance_mode=v2_active：当前环境 Maintenance V2 直接全量启用
  -> Gate E 退出
  -> V1 仅保留行为对照和兼容测试，满足退场门禁后删除
  -> policy version 升级仍使用独立验证与粘性归属
```

Gate D2 是独立、可选的参考分布校准门禁，不在上述 Bootstrap/Maintenance 切换主链上；缺少真人聚合样本时保持
`conservative_cold_start`，不会阻止 Gate D1 或 Gate E。

当前阶段没有生产流量、生产真人数据或生产存量，因此不设置 `1% -> 5% -> 25% -> 50% -> 100%` 灰度，也不等待生产
观察期。未来生产 Bootstrap、Maintenance、存量迁移和 policy rollout 的比例与顺序全部延后到上线前独立评审，不能沿用
当前非生产环境的 100% 作为生产授权。

每个阶段至少观察：

- 维护成功率和延迟；
- 每种 action 的选择、跳过和失败原因；
- 数据库锁等待和 Celery 队列长度；
- 虚拟玩家资源及高价值物品供给；
- 地图可攻击目标和竞技场可用后备数量；
- 固定模拟 cohort 下的强度、硬约束和分布差异；仅在参考分布校准策略具备合规聚合样本后才比较真人同段位分布。

出现异常时把对应能力切到 `v2_paused`，立即停止新增影响面。安全暂停不会自动改变已入组档案或回退 V1，也无需回滚
schema；Gate E cutover 中出现异常则保持 `v2_cutover`，修复并重验清零计数后才能进入 `v2_active`。

### 阶段 6：兼容清理

**条件**

- V2 已在当前测试环境全量通过功能、并发、性能和失败注入验收；不附加生产观察期。
- 运行时调用方和测试不再导入旧私有函数。
- 运维脚本和文档已经使用新入口。
- 当前环境所有仍可能执行或重新激活的档案都已是 V2，运行时有效 V1 数量为 0；仅有冻结 fixture 或不可运行的终态 V1 测试记录可以暂留并必须有明确处置记录。
- `docs/compatibility_inventory_2026-03.md` 中每个待删除 symbol 都有仓内调用清零证明、已确认外部消费者结论、兼容截止日期和回退 owner；不能仅凭全仓 `rg` 结果删除兼容入口。

**清理项**

- `virtual_player_population.py`：仓内及已确认外部导入为零、兼容窗口结束且纯 planner 已由真实 owner 覆盖后，删除整个兼容重导出文件及其 import 测试。
- `virtual_player_rules.py`：生命周期调用全部迁到 `lifecycle.py`，V1 quantile/persona/bounded projection 调用全部迁到 `legacy/projection.py`，新 owner 的等价测试通过，仓内及已确认外部导入为零且兼容窗口结束后，删除原文件；不得把它保留成无期限聚合门面。
- `virtual_players.py`：删除全部私有实现和已过期重导出，只保留第 5.3 节仍有运行时或已确认外部消费者的公共入口、显式 `__all__` 与必要参数/返回值适配；每删除一个入口都须满足兼容清单门禁，稳定公共门面本身不以 V1 数据清零为删除条件。
- `arena/virtual_reserve.py`：仓内调用已迁到 demand/reconcile/pool/fill/scan 等真实 owner，当前仓内生产消费者为零；只为已登记外部兼容窗口保留公共入口和显式 `__all__`，窗口结束后删除。`virtual_backfill.py` 继续作为 Entry 物化 owner，不把已拆出的 reference/observability/lineup/protection/demand/reconcile/pool/fill/scan 逻辑并回门面。
- `legacy/*` 与 V1 router：只有当当前环境可执行或可重新激活的 `engine_version=1` 档案计数为零、保留的 V1 终态测试档案已有批准的重建/不可重新激活处置、运行任务与管理命令不再路由 V1，并且兼容及重放门禁完成后，才删除；删除须单独做不可逆操作评审。此前保留 V1 characterization tests，不以单一 V2 happy-path 测试替代退场门禁。
- 更新架构、数据边界和兼容入口文档。

---

## 13. 验证矩阵

### 13.1 纯规则测试

- 配置深合并、未知字段、范围和引用校验。
- V2 八档边界连续、互不重叠、仅一个开放终段；非法缺口/重叠/乱序/多开放终段和热改边界均拒绝。
- 高段按需激活、供给分段计数和跨段重算决策；低段供给不得抵扣高段缺口。
- 每段保守起点 fixture 的完整性、确定性、90% 上限后合法落段及不依赖真人数据。
- 八档成长 profile 与声望段一一对应；历史年龄和各间隔随段位单调不减，单次综合增长 cap 单调不增，缺段、乱序、负值、
  非有限值、跨多段或旁路开关均 fail closed。
- plan schema、不可变 policy、checksum 和已发布版本禁止原地改写。
- 画像生成稳定性、不同 seed 的多样性以及画像与 policy 参数隔离。
- canonical 编码、稳定候选排序、policy bucket、随机子流隔离和 engine/RNG/plan/policy/sequence 可重放；当前环境不实现 engine enrollment bucket。
- 生命周期决策与能力矩阵。
- `MaintenanceTrigger` 的 due/sequence/schedule disposition 决策矩阵，`ADMIN` 缺少显式语义时 fail closed。
- `NO_ACTION` deadline 由 member `created_at` 稳定计算，重试和 demand version 更新不重置。
- 虚拟 lineup 满血规范化覆盖残血、已满血和非法 `max_hp`；只修改输出副本的 `current_hp`，输入、组合选择、power 和随机序列不变。
- 联合 anchor 选择、最小样本回退、离群裁剪和相同快照可重放。
- 门客投资层级、护院配比和科技上限。
- 门客治疗候选硬过滤、重伤/投资层级/缺失 HP 排序、稳定同分选择，以及无药、满血和不可治疗状态的拒绝原因。
- 装备、技能候选硬过滤、评分和换装惯性。

### 13.2 ORM 与服务测试

- Bootstrap 创建、坐标重试、名称唯一和按段历史年龄；不写伪造动作流水，`last_strength_increase_at` 与入组时间一致。
- 存量档案按持久化 Manor 声望显式重分段且重复执行不再写入，资产、声望、生命周期和 engine 均不变化。
- 真人声望跨段只在提交后合并投递旧/新两段人口重算，回滚不投递，任务不运行 Maintenance。
- `BotPopulationRecomputeDemand` 同键并发 merge 不丢 revision；claim 期间 merge、claim 过期再认领、旧 token finalize、失败退避、
  无缺口完成和任务投递丢失恢复均通过；完成行不删除，消费者不跨人口执行持有 demand 行锁。
- Bot 正常领域动作跨段后由 `profile_store.py` 幂等同步当前段，再重算旧/新两段；selector/查询路径无档案写副作用。
- Bootstrap 锁外 Blueprint 不写数据库，锁内模板重验和物化原子提交。
- `BotPolicyRelease` 同版本幂等发布、不同 checksum 拒绝、运行时重启后仍不可改写。
- RNG 修复要求 expected-current 和可审计恢复依据；画像修复不改变 RNG/policy/sequence；缺失 policy release 只能按既有 checksum 补建，损坏记录不能原地覆盖。
- 维护单动作写入和失败回滚。
- `guest_healing` 与真人药品入口共用治疗量、20% 重伤解除、药品扣减和错误语义；治疗后重新评估可出战阵容，但不改变等级、属性、模板或永久强度预算。
- 虚拟监牢日清只处理 cutoff 前、虚拟 captor 的 `HELD` 记录；重复运行幂等，真人监牢、其他状态和 cutoff 后记录不变，
  `v2_cutover/v2_paused` 不关闭该任务。
- Maintenance 在八档逐一执行最小正向动作间隔、单次综合增幅和下一检查区间；较早到期的生命周期/强制结算不夹带强度成长。
- `engine_version=2` 档案在开关关闭、policy rollout 调整以及 RNG、画像或 policy release 损坏时都不会进入 V1。
- 画像 schema 升级与 policy 升级互不重建对方数据。
- 技能位、属性要求和书本消费。
- 装备所有权、槽位容量、套装重算和旧装备去向。
- 护院招募条件、总量、损失恢复和弃坑缺编。
- Bootstrap 家丁历史储备和募兵消费不会无来源增减；Maintenance 首版不会调用候选转家丁链。
- 建筑容量和科技 max_level。
- 稀有库存额度的预留、释放和事务竞争。
- 真人 command 与压缩 command 的资格、成本、资源事件和结果 parity tests。
- Admin、竞技场、人口和生命周期均通过字段集受限的 `profile_store.py` command 写档案；AST/符号门禁覆盖 manager、QuerySet、实例、alias、bulk 和 upsert 写法，并以负例夹具证明每类旁路都会失败。
- `BotProfile` 只读 allowlist 中的 selector、保护查询、掉落判断、Raid 判断和 Admin 可读不可写；未登记运行时模块新增直接 model import 时门禁失败。
- demand/pool/fill 各状态迁移的 owner 契约通过，reconcile/scan 只做应用协调；取消报名和无效快照只调用幂等 lease release，不存在赛事 core/match helper 的直接 member delete。
- 竞技场依赖契约证明 demand/reconcile/pool/fill/scan 子图无强连通分量，拒绝其导入 `core / coop_core / coop_lifecycle`，也拒绝 `lifecycle_helpers` 导入 virtual reserve；正常报名和虚拟 fill 共用赛事转换 primitive。
- 普通赛/共斗虚拟补位逐条持久化 `current_hp == max_hp`；locked write primitive 拒绝残血/非法 snapshot 并整笔回滚，
  `Guest.current_hp`、状态和库存不变；真人报名快照仍保留并钳制报名时实时 HP。
- `BotLootClampDecision` 计算无写副作用，Raid 提交后退休建议幂等处理。
- 竞技场对五种 `MaintenanceResult` 状态都有确定的租约和成长轮次语义。
- `SCHEDULED / ARENA_ACCELERATION / ADMIN` 分别覆盖 due 与未来调度、`APPLIED / NO_ACTION`、回滚和重投；arena trigger 在整个事务中不改 `next_growth_at`。
- Arena/Admin 与 Scheduled 共用分段间隔和单次增长 cap；受控跨段同时校验来源/目标段，跨两个以上边界拒绝；玩家驱动结果
  可提交，随后完成强度记录、跨段对账、超限冻结和自动匹配排除。
- 连续 `NO_ACTION` 在 deadline 前重试且不增加成长轮次，到期原子转 `EXHAUSTED`，active capacity 立即允许补入替代 member。

### 13.3 并发测试

- 同一档案双 worker 维护只有一个成功推进 sequence。
- 两个治疗 worker 竞争同一药品或门客时最多一个成功；药品、HP、重伤状态和 sequence 不发生部分写入。
- 两个日清任务重叠执行时每个囚犯最多迁移一次；日清与 Raid 新俘获并发时按冻结 cutoff 划分本轮与下一轮，不误处理新记录。
- 虚拟补位物化与同一庄园门客的治疗/被动回血并发时，Entry snapshot 始终满血，补位事务不覆盖 Guest 的并发 HP/状态结果，
  失败重试不产生部分 Entry 或重复租约消费。
- 定时 worker、竞技场加速和 Admin 同时命中同一档案时，只有持有 expected sequence 的一个提交；失败方不覆盖胜方的正常调度语义。
- 两个入口同时越过同一 `last_strength_increase_at + minimum_spacing` 边界时，只有一个提高强度动作提交，时间戳、24 小时预算和
  sequence 不发生部分写入。
- 在写入前、领域写入后提交前、提交后任务确认前注入失败，验证 sequence、资源和动作结果。
- 人口滚动与竞技场重新激活不能共同突破容量。
- 维护与休眠、竞技场租约、战利品耗尽并发。
- Raid 结算与维护、人口退休、竞技场租约交叉并发；Raid 回滚不得退休，成功提交后重复建议不得重复副作用。
- 缓存锁续租失败时停止后续人口写入。
- 数据库死锁重试不重复动作。
- 按阶段 0 锁顺序矩阵覆盖与现有真人 command、竞技场加速和库存额度的交叉并发。

### 13.4 分布测试

Gate A 冻结报告契约和保守阈值，Gate D2 在准备参考分布校准策略时才生成代表性匿名真人基线，并把获批阈值写入不可变 `BotPolicyRelease.payload.reference_calibration_thresholds`、纳入 policy checksum。分布测试使用冻结的真人参考快照、固定 seed 和足够样本，至少计算：

| 检查维度 | 指标 | 通过规则 |
|----------|------|----------|
| 硬约束 | 非法技能、槽位、等级、模板身份和资源越界数量 | 必须为 0 |
| 连续数值 | 规范化 Wasserstein 距离及 P10/P50/P90 偏差 | 不超过阶段 0 为该声望段冻结的阈值 |
| 类别分布 | 门客稀有度、文武、技能类别、护院 class 的 Jensen-Shannon divergence | 不超过冻结阈值 |
| 联合合理性 | 建筑、阵容、装备、护院联合向量的稳健异常率 | 不超过真人留出集基线加批准容差 |
| 多样性 | 阵容、装备、技能和护院组合指纹碰撞率 | 不超过冻结阈值且不劣于 V1 基线 |
| 画像差异 | 各 archetype 关键指标的方向与效应量 | 符合 policy 声明且达到冻结最小效应量 |
| 弃坑特征 | 缺编、旧装备和成长断层比例 | 落在单独的 abandoned 阈值内 |

在分布测试之前，固定 fixture 必须覆盖有效样本数 `0/1/4/5/29/30` 的边界，分别证明人口缺口仍可物化、最终
Blueprint 不越综合/分项上限、正向 jitter 受限，并且 24 小时动作数/增幅预算在并发下不会超支。该安全套件不读取
现有环境玩家数据，也是任何 `conservative_cold_start` 或注册事件接线的硬门禁。

Gate D1 的固定 fixture 还必须覆盖八档所有上下边界及远高于 `240000` 的开放终段值，证明每个声望只归属一段；并覆盖
空高段目标为 0、三类激活来源、低段供给不抵扣高段缺口、同段复活优先、禁止瞬间跨段提升，以及每段零样本 fixture 在
90% 上限后合法落段。每段还要用固定 seed 证明 Bootstrap 历史年龄落在对应冻结区间、区间随段位不倒退、不生成逐条历史
动作，并且最终 Blueprint 仍通过样本档位与分项强度门禁。该套件同样不需要真人数据。

Gate E 的固定场景必须逐档验证实时成长节奏：边界前一瞬拒绝、边界时刻允许、单次增长恰好等于 cap 允许而高 1 基点拒绝；
跨一段时取来源/目标段更严格值，跨多段拒绝；0 样本仍禁止正向成长。Arena/Admin 和并发 worker 使用相同用例矩阵，不能
通过 trigger 绕过。PVP 等玩家驱动结果单独验证“领域结果提交成功、Bot 增量随后记录、必要时冻结并退出自动匹配”。

分布阈值缺失、代表性样本不足或基线版本不匹配时，只跳过“参考分布已校准”策略并给出明确原因，不能自动放宽，也不能
阻止已通过安全套件的 V2 `conservative_cold_start` 补足人口。Gate D1 退出前 V1 只作为临时兼容实现，退出后不得因样本不足
重新接管创建。强度安全配置本身缺失、非法或保护测试失败则必须 fail closed：对应生成器和注册事件接线保持关闭，这属于
配置/实现错误，不得伪装成普通样本不足。当前测试环境和 CI 使用固定模拟 cohort 与冻结快照；未来生产监控是否使用
滚动匿名聚合真人样本，留待上线前数据边界评审。

candidate report 不得作为自身正确性的唯一证据。Gate D2 退出证据必须从 artifact schema v2 按 metric algorithm v2 重算上述
metrics，并验证 generator version、源码 manifest 与外部受控 `hmac_sha256_v1` attestation；report schema v3 必须逐字段匹配重算值。
若采用替代证明，必须在 acceptance contract 中明确其等价性。只校验 report schema、路径和 canonical digest 可以阻止登记后的
篡改，但不能阻止错误生成器或虚构指标。当前 HMAC 只属于非生产信任边界，未来生产签名方案必须在上线前另行评审。

首版具体阈值、benchmark 矩阵、当前环境直接启用与自动暂停条件以
[`virtual_player_gate_a_acceptance_config_2026-07-27.yaml`](virtual_player_gate_a_acceptance_config_2026-07-27.yaml)
为准。该配置不冻结未来生产灰度时长。所有分布阈值逐声望段独立执行；任一段失败不能由全局平均掩盖。

### 13.5 性能验证

- 记录单档案和单批维护查询数。
- 目录数据一次批量加载，禁止候选评分中的逐项 ORM 查询。
- 避免为每个候选装备、技能或门客执行独立查询。
- 不为每个虚拟玩家动作派发独立长期 Celery 任务。
- 对计划批量大小执行数据库和队列压力测试。
- 虚拟监牢日清按现有 `(captor, status, -captured_at)`/`captured_at` 索引验证批量查询计划、锁等待和 backlog 清空时间；不得单事务锁定全部囚犯。
- Bootstrap 计划阶段、物化事务和 Maintenance 锁等待分别计时；绝对准入阈值以 Gate A 验收 YAML 的冻结数值为准。
- V1 对照只使用同一 disposable database、同一固定 fixture 和同一并发矩阵生成诊断报告，不需要真人数据，也不得自动覆盖
  或放宽冻结阈值；若实证要求改值，必须重新打开验收配置评审。

### 13.6 测试文件迁移

测试跟随所有者逐批迁移，不先做一次性重命名。最终责任如下：

| 现有测试文件 | 目标安排 |
|--------------|----------|
| `tests/test_virtual_players_service.py` | 逐步拆到 `test_virtual_player_identity.py`、`test_virtual_player_bootstrap_v1.py`、`test_virtual_player_legacy_inventory.py`、`test_virtual_player_population_runtime.py`；最终只保留 facade 公共契约测试或删除 |
| `tests/test_virtual_player_backfill.py` | 拆到 `test_virtual_player_backfill.py`（需求存储/API）、`test_virtual_player_population_runtime.py`（消费/补量）、`test_virtual_player_lifecycle.py`（退休/重激活） |
| `tests/test_virtual_player_progression_diversity.py` | V1 等价部分迁到 `test_virtual_player_legacy_projection.py`；V2 统计部分进入 `test_virtual_player_distributions.py` |
| `tests/test_virtual_player_rules.py` | 生命周期用例迁到 `test_virtual_player_lifecycle.py`，V1 quantile/persona 用例迁到 legacy projection；原文件退场 |
| `tests/test_virtual_player_population_planning.py` | 保留，继续只测纯 `population.py`；兼容 import 测试在 Gate B 后删除 |
| `tests/test_virtual_player_state_policy.py` | 保留并增加 model choices 对齐与纯模块 AST 测试 |
| `tests/test_virtual_player_loot_limits.py` | 保留纯/read-only clamp 契约；Raid 提交/回滚行为放到 raid service integration test |
| `tests/test_virtual_player_lock_integration.py`、`tests/test_arena_virtual_population_concurrency_integration.py` | 保留并扩展真实 MySQL/Redis 锁序与 H-01 场景，不拆成纯单测 |
| `tests/arena_services/test_virtual_reserve.py` | 按 `demand / reconcile / pool / fill / scan / lineups / protection` 拆分；lineups 覆盖满血副本规范化、输入不变和选择结果不变，pool 覆盖 `NO_ACTION` 绝对 deadline，公共 facade 只留 import/dispatch 契约 |
| `tests/arena_services/test_virtual_backfill.py` | 纯评分迁到 lineups 测试，候选/锁/事务失败迁到 fill 测试，本文件最终只测已验证且全员满血 lineup 的普通赛/共斗 Entry 原子物化及残血输入回滚 |
| `tests/test_arena_snapshots.py` | 保留共享 snapshot 的实时 HP 钳制契约，增加残血真人报名不被虚拟补位规则强制回满的回归测试 |
| `tests/test_arena_tasks.py` | 保留 Celery 名称和返回契约测试；monkeypatch 改指向 demand/pool/fill 真实 owner，不再绑定兼容门面 |
| `tests/test_arena_virtual_reserve_models.py` | 保留 demand/member model 约束测试；不承载 service 状态机用例 |
| `tests/arena_services/registration_rounds.py`、`coop_registration.py`、`coop_resolution.py`、`tests/test_arena_schedule.py` | 保留赛事生命周期回归；更新 demand locked hook、共享 lifecycle primitive、fill 和幂等 lease release 的接线断言，并增加无双向 import 契约 |
| `tests/map_views/map_page.py`、`tests/test_map_attack_field_reuse.py` | 保留地图 API/攻击字段回归；补充搜索读路径不直接创建 Bot 的断言 |
| `tests/test_virtual_player_ops.py` | 保留并扩展 policy 发布、缺失 release 补建、RNG/画像修复、dry-run、resume 和 runtime config fail-closed 测试 |

新增独立测试：`test_virtual_player_config.py`、`test_virtual_player_random_context.py`、`test_virtual_player_strategy.py`、`test_virtual_player_projection.py`、`test_virtual_player_reference_snapshots.py`、`test_virtual_player_profile_store.py`、`test_virtual_player_profile_store_boundary.py`、`test_virtual_player_inventory_budget.py`、`test_virtual_player_bootstrap_v2.py`、`test_virtual_player_maintenance_v1.py` 和 `test_virtual_player_maintenance_v2.py`。其中 boundary 测试包含可运行的违规夹具，不用只匹配当前源码文本的脆弱 grep 代替 AST/符号判断。

完成 Gate B 时，现有测试不得再从 `gameplay.services.virtual_players` 导入下划线函数；monkeypatch 必须指向真实 owner 模块，避免门面永久承载测试耦合。

---

## 14. 可观测性

每次维护记录结构化字段：

```text
event
profile_id
manor_id
engine_version
rng_version
plan_schema_version
policy_version
policy_checksum
maintenance_sequence_before
maintenance_sequence_after
archetype
state_before
state_after
policy_bucket
prestige_band_before
prestige_band_after
band_growth_profile
reference_snapshot_version
reference_snapshot_digest
selected_action
skipped_action_reasons
maintenance_trigger
maintenance_schedule_disposition
next_growth_at_before
next_growth_at_after
last_strength_increase_at_before
last_strength_increase_at_after
band_spacing_deadline
controlled_growth_bps
outcome
failure_reason
duration_ms
query_count_sampled
before_summary
after_summary
```

建议指标：

- `virtual_player_enrollment_total{source,result}`
- `virtual_player_maintenance_total{engine_version,policy_version,result}`
- `virtual_player_action_total{action,result}`
- `virtual_player_band_growth_rejected_total{prestige_band,reason,trigger}`
- `virtual_player_controlled_band_transition_total{from_band,to_band,result}`
- `virtual_player_maintenance_duration_ms`
- `virtual_player_rng_repair_total{from_version,to_version,result}`
- `virtual_player_plan_repair_total{reason}`
- `virtual_player_policy_release_recovery_total{reason,result}`
- `virtual_player_policy_assignment_total{from_version,to_version,result}`
- `virtual_player_v2_paused_total{reason}`
- `virtual_player_population_mutation_total{kind,result}`
- `virtual_player_inventory_cap_rejected_total{category}`
- `virtual_player_guest_healing_total{result,injury_cured}`
- `virtual_player_jail_cleanup_total{result}`
- `virtual_player_jail_cleanup_prisoners_total{result}`
- `virtual_player_jail_cleanup_oldest_held_age_seconds`
- `virtual_player_arena_reserve_shortage_total{reason}`
- `virtual_player_arena_reserve_lease_exhausted_total{reason}`
- `virtual_player_arena_backfill_guest_snapshot_total{mode,result}`
- `virtual_player_loot_retirement_recommendation_total{result}`
- `virtual_player_loot_retirement_post_commit_attempt_total{result}`
- `virtual_player_policy_release_total{version,result}`
- `virtual_player_external_reconciliation_total{phase,result}`
- `virtual_player_external_reconciliation_quarantined_total{failure_code}`
- `virtual_player_safety_provider_total{operation,result}`
- `virtual_player_routing_transition_total{from_mode,to_mode,result}`

竞技场 snapshot 指标的 `mode` 只允许 `tournament/coop`，`result` 只允许 `full/rejected_invalid_max_hp/rejected_not_full`，
不得使用 event、profile 或 guest ID 作为 label。

日志不得包含完整大对象、技能候选列表、真人用户 ID 或其他敏感用户数据。摘要指纹只能用于版本和一致性诊断，不能反向恢复真人档案。

仓库当前没有满足安全门禁语义的通用 Prometheus/StatsD 业务指标层。`core.utils.task_monitoring` 是进程/Redis 累计 task counter，
缓存异常时还会回退进程内状态；它没有事件 ID、UTC 窗口、迟到处理或可证明完整的 heartbeat，因此只能用于健康诊断，禁止作为
safety truth。当前非生产环境明确选择数据库型共享 provider，而不是留下未指定的“未来指标后端”：

- `BotSafetyMetricEvent` 是有保留期的 append-only 事件账本，`event_id` 唯一；相同 ID 和相同 canonical payload 幂等，不同 payload
  是 hard violation。事件只保存指标名、UTC `occurred_at`、有限维度和数值，不保存真人 ID 或完整业务对象。
- `BotSafetyMetricWindow` 以 `window_id` 唯一保存不可变最终快照。aggregator 只能在 UTC 小时/日窗口结束再加 5 分钟后 finalize，
  先按 `event_id` 去重；finalize 后到达的旧窗口事件不改写快照，而是记录 hard violation 并触发暂停。
- `maintenance_attempt_emitter / h01_callback_attempt_emitter / arena_shortage_emitter / safety_aggregator` 每 60 秒写 heartbeat，任意相邻
  heartbeat 间隔超过 120 秒即为不完整。只有相关 heartbeat 完整时，零分母才表示无 rate breach。
- 原始事件在窗口完成后保留 35 天，闭合窗口保留 90 天；清理由独立命令/任务按最终窗口水位删除，未 finalize 或仍被 decision cursor
  引用的数据不得清理。事件写失败不回滚已经成功的业务事务，但 provider 缺失、写入/heartbeat 断档或读取失败都使门禁 fail closed。

结构化日志继续用于诊断，但不能回填或覆盖已 finalize 的 safety window。上述模型、provider 健康检查、迟到/去重故障注入和清理门禁
均已实现并完成 readiness 验证；这不授权 `v2_active`，实际启用仍必须经过 cutover、V1 清零和独立确认。

H-01 必须分开记录“产生退休建议”和“post-commit callback 实际开始尝试”两个计数器。暂停比率名为
`h01_post_commit_attempt_degraded_rate`，分子是 attempt 中的 `result=degraded`，分母是全部 callback attempt；它不是 delivery
成功率，也不能用 recommendation 数量作分母。内层提交后到 callback 注册前、以及外层提交后到 callback 开始前的进程退出
都可能只有 recommendation 而没有 attempt，这两个窗口是已批准的不可观测投递窗口，不能伪装成可测的 attempt failure。

安全指标由共享观测后端按 UTC 固定、不重叠的已闭合窗口聚合：小时和日窗口均为 fixed tumbling window，关闭前保留 5 分钟迟到
宽限并按 `event_id` 去重。`virtual_player_safety_monitor` 只读取已闭合窗口并作决定，
`gameplay.services.runtime_configs` 是 routing 执行 owner；它用 `BotRuntimeRoutingState` 的 revision 和每类窗口 cursor 以闭合
`window_id` 幂等、使用 CAS 将当前
`v2_active`（安全门禁触发时也包括 `v2_cutover`）切到 `v2_paused`。必需指标缺失时 fail closed；只有完整 heartbeat 明确证明
分母为 0 时才判定“无 rate breach”。

`MaintenanceResult` 的领域枚举仍严格只有 `APPLIED / NO_ACTION / BUSY / PAUSED / INELIGIBLE`，不得为了指标添加第六种状态。
每个 transport/application 调用携带 operation ID 和 attempt ordinal，并在独立的 `virtual_player_maintenance_attempt` 观测事件命名空间
产生一次 terminal event；只有在没有形成已提交 `MaintenanceResult` 的执行异常时，该事件才使用观测专属的 `FAILED`。提交结果不确定
另记 `duplicate_or_partial_commit` hard violation，不能普通化为 `FAILED`。failure rate 分母只包含观测事件的
`APPLIED / NO_ACTION / FAILED`，分子只包含 `FAILED`，排除 `BUSY / PAUSED / INELIGIBLE`；业务提交后单纯的指标写失败由 provider
健康缺口触发 fail closed，不把已成功周期改写成失败。分布窗口按
`(policy_version, reference_snapshot_version, prestige_band)` 独立判定，全局平均不得掩盖单段越界。

---

## 15. 当前非生产环境的暂停与恢复方案

1. V2 Bootstrap 异常时把 `bootstrap_mode` 从 `v2_active` 切到 `v2_paused`；立即停止新 Bot 物化。已经创建的 V2 档案仍是
   合法庄园，不删除、不改写 engine，也不让 V1 接管新建。
2. Gate E cutover 的普通工作流错误保持 `maintenance_mode=v2_cutover`，V1/V2 发展写继续停止；若已闭合安全窗口触发阈值或
   缺少必需指标，则 safety monitor 请求 runtime config owner 以 CAS 切到 `v2_paused`。修复后必须重新进入 cutover、重跑
   幂等入组与精确 V1 清零检查，不得直接跳到 `v2_active`。
3. V2 Maintenance 异常时把 `maintenance_mode` 从 `v2_active` 切到 `v2_paused`。V2 档案只执行共享生命周期能力检查和
   有界调度，不执行任何 V1 或 V2 发展写入。
4. Policy 异常时先暂停 Maintenance，再通过显式、分批、带兼容检查的命令把受影响档案指向仍保留的上一 policy version。
5. 已发布 `BotPolicyRelease` 不删除、不覆盖；恢复只改变档案引用，保留原 payload 供诊断和重放。
6. routing mode 或 policy rollout 的变化只停止相应能力或未来升级，不改变已持久化的 engine/RNG/plan/policy 归属。
7. 不自动执行 `engine_version=2 -> 1`；已经提交的成长不逆向批量修改。
8. additive migration 不回退删除字段或 policy release，避免锁表和数据损失。
9. 可丢弃测试数据可以在明确确认后重建为 V2；需要保留的测试数据只能通过显式修复或一次性入组处理，不能用静默回退 V1 掩盖问题。
10. 人口、地图和竞技场继续通过原公开门面运行；Gate E readiness 前遗留 `engine_version=1` 档案只能由隔离的 V1 路径维护，
    且不得新增；进入 `v2_cutover` 后 V1/V2 发展写均停止。
11. H-01 运行异常时可以回退为“只记录退休建议、交由正常维护退休”，但不得把 profile 写入重新塞回掉落计算函数。
12. `v2_paused` 和 `v2_cutover` 会停止主动 `guest_healing`，门客继续由全局被动回血恢复；虚拟监牢每日清理属于独立公平性
    housekeeping，除非监牢数据库本身不可用，否则不得随 Maintenance routing 一起暂停。

安全暂停路径必须在 Gate E 当前环境全量启用前实现并测试，避免关闭 Maintenance 后形成持续到期扫描。只有在 V2 全量通过
当前非生产环境验收、所有可恢复档案均已重建或入组且兼容清理完成后，才能删除 V1；删除代码或测试数据前另做不可逆操作
确认。未来生产环境需要独立的暂停、回滚和数据恢复方案，本文不授权直接复用本节。

本节定义的共享指标 adapter、safety monitor、routing CAS 以及所需持久字段属于已授权的 Gate E 实现范围；本次 Gate A 证据
治理只冻结契约，不能替代 Gate E 实现与证据，也不授权切换 V2 runtime mode。

---

## 16. 验收标准

### 架构

- `virtual_players.py` 只承担明确公共门面，不再包含领域实现。
- 纯规则模块不导入 Django 和模型。
- `selectors / catalog / reference_snapshots / arena.virtual_protection` 保持只读，`profile_store` 是唯一 BotProfile 写 owner；完整 DML 门禁覆盖 QuerySet、实例、bulk/upsert 和 alias，领域模型由各自 command 写入。
- `bootstrap / maintenance / population_runtime` 只拥有应用事务编排，不复制领域资格、成本和结果公式。
- `legacy/*` 只有 V1 路由依赖且具有可执行退场门禁。
- `virtual_player_loot_limits.py` 无写副作用；竞技场 demand/reconcile/pool/fill/scan/reference/observability/lineup/protection 边界分离。
- `virtual_lineups.py` 唯一拥有虚拟 lineup 满血副本规范化，`virtual_backfill.py` 只物化并防御性断言；共享 snapshot builder
  不包含虚拟玩家专用分支。
- 赛事启动/准备 primitive 位于 `lifecycle_helpers.py`；demand/reconcile/pool/fill/scan 子图无强连通分量且不反向导入赛事 core/coop lifecycle，`lifecycle_helpers.py` 不导入 virtual reserve。
- 无运行时调用方依赖旧私有函数。
- 兼容入口有清晰删除条件。

### 正确性

- 原虚拟玩家、地图、PVP、竞技场和库存上限测试通过。
- 关键 MySQL/Redis 并发测试通过。
- 维护任务可重试且不重复写入。
- 虚拟玩家可以通过 `guest_healing` 使用自己仓库中的合法药品治疗一名未满血门客；治疗、药品扣减、重伤解除和 sequence 原子提交，
  不创造免费 HP、不消费永久强度预算，也不与同周期其他同步动作并存。
- 每日虚拟监牢清理将固定 cutoff 前、虚拟 captor 的全部 `HELD` 囚犯幂等迁移为 `RELEASED`；不自动招募、不删除历史、
  不返还原门客，真人监牢和 cutoff 后新俘虏不受影响。
- 门客招募、候选处置、V1 模板晋升和稀有度选择保持本次增补前行为，不被监牢日清或治疗动作暗中修改。
- 普通赛和共斗的虚拟补位门客全部以 `current_hp == max_hp` 的 Entry snapshot 参赛；该规则跨 V1/V2 生效且只作用于
  `source=VIRTUAL`，不改庄园 Guest、状态、库存或真人报名 HP。
- `SCHEDULED / ARENA_ACCELERATION / ADMIN` 的 due、sequence 和正常调度语义与第 9.2 节矩阵一致；竞技场加速逐值保留原 `next_growth_at`。
- `NO_ACTION` 不消费竞技场成长轮次，绝对租约到期后不再占 active capacity，且重试不能延长 deadline。
- 每周期最多一个同步发展动作，首版没有事务外发展派发。
- 八档使用统一领域动作和各自冻结节奏；历史年龄/间隔随段位单调不减、单次 cap 单调不增，运行时同时取样本档位、
  来源/目标段及领域规则的最严格值。
- 当前环境在 Gate D1/Gate E 分别退出后对应 V2 能力直接达到 100%；Gate E 退出时运行中或可重新激活的 Bot 全部为 V2，
  routing mode 变化不会使档案回到 V1。Gate D2 缺少真人聚合样本只保持参考校准关闭。
- RNG、画像或 policy release 损坏时 fail closed，显式修复可重放。
- 同一 policy version 不能发布两个 payload；相同 policy rollout 输入跨进程得到同一 bucket。
- 门客模板身份不被 V2 维护改变。
- 科技、技能位、稀有度和装备槽位不超过硬约束。
- 真人 command 与压缩 command 的资格、成本、事件和结果契约一致。
- Raid 回滚不退休 Bot，成功提交后的退休建议可重复处理且无锁序回归。
- 家丁、募兵材料、技能书和装备 acquisition 都有来源，不由发展动作隐式创造。
- 真人注册提交后触发幂等人口重算且只补实际缺口；样本不足使用保守 fallback，注册请求不等待 Bot 物化。
- 人口唤醒消息不是 durable truth；持久 demand 的 request/completion revision 可证明未完成工作，claim fencing 不吞并发 merge，
  外部对账只在旧/新段需求全部同事务合并后进入 `APPLIED`。
- Gate D1 后八档连续覆盖任意非负声望；真人跨段提交后幂等重算旧/新两段，空高段按需激活，低段 Bot 不计为高段供给，
  存量重分段不修改声望或资产，也不存在跨段复活或瞬间抬升声望。
- Bot 通过正常领域动作自然跨段时只同步当前段并重算人口，不改历史目标段；所有读取路径保持无写副作用。
- 0、1--4、5--29、30+ 四档的 Blueprint 与 Maintenance 均不突破综合/分项上限和 24 小时强度预算，
  Arena/Admin 无旁路，超限档案不进入自动匹配。
- Bootstrap 按目标段投影冻结范围内的合理历史年龄，不伪造逐条动作；实时 Maintenance 不直接赠送声望，受控动作最多跨一段，
  Arena/Admin 不得绕过 `last_strength_increase_at` 间隔。玩家驱动结果不被 Bot 策略回滚，超限后按契约冻结成长并退出自动匹配。

### 自然度

- 同一档案的门客、装备、技能、护院和科技体现同一发展计划。
- 门客形成可观察的核心、二队和替补差异。
- 护院不再平均分配所有兵种。
- 装备和技能不是所有档案都选择同一局部最优组合。
- 启用参考分布校准策略的声望段达到第 13.4 节冻结的全部分布指标；冷启动档案只声明通过强度、硬约束和经济门禁，
  不冒充分布已校准。
- `abandoned` 档案具有稳定、可解释的不完整成长特征。

### 运行

- 单档案和批量维护满足 Gate A 验收 YAML 的绝对耗时、查询量和锁等待上限；同库固定 V1 fixture 结果只作诊断对照。
- 没有显著增加数据库锁等待和 Celery 积压。
- 虚拟监牢日清以有界批次完成每日 backlog，重复 task 不放大写入；指标能区分 released/skipped/failed 并暴露最老未处理记录年龄。
- 竞技场普通赛/共斗虚拟补位指标能区分满血 snapshot 成功物化与非法/残血 snapshot 拒绝，拒绝时不留下部分 Entry。
- 资源及高价值物品供给不超过现有预算上限。
- 关闭 V2 开关可以停止新增影响和进入安全暂停，但不会自动把 V2 档案交给 V1；重新开启前必须重过失败项。
- 安全暂停不会造成持续到期扫描、任务风暴或竞技场重复租用。
- 连续 `NO_ACTION` 不会造成 reserve pool 永久少槽或阻止替代者补入。
- 真人样本减少或 cohort 上限下降时，既有超限 Bot 停止提高强度并退出自动匹配，不自动降级资产，也不形成重试风暴。

---

## 17. 工作量评估

以下为单名熟悉代码库的后端开发者粗略范围，不含生产观察等待时间：

| 阶段 | 预计工作量 |
|------|------------|
| 阶段 0 基线与边界 | 3 至 5 天 |
| 阶段 1 架构提取、竞技场拆分与 H-01 收口 | 10 至 16 天 |
| 阶段 2 路由、RNG、画像与策略发布数据 | 5 至 9 天 |
| 阶段 3 Bootstrap V2 | 7 至 11 天 |
| 阶段 4 Maintenance V2 与领域 command 提取 | 12 至 22 天 |
| 阶段 5 非生产全量验收与调参 | 5 至 10 个工作日 |
| 阶段 6 兼容清理 | 3 至 6 天 |

完整落地初步属于约 9 至 16 周的架构迁移；当前估算只覆盖测试环境，不包含未来生产准备、灰度或观察时间。阶段 0
会揭示真实锁图、command 提取量和基线数据质量，完成后必须重新估算。可以在阶段 1 后停止并获得结构治理与 H-01 风险
收口，也可以在阶段 3 后先于当前环境全量观察新建 V2 虚拟玩家效果，再决定是否投入风险最高的 Maintenance V2。

---

## 18. 推荐实施范围

本轮建议批准设计方向，但不一次性批准阶段 0 至阶段 4 的全部代码改动。按以下 gate 单独确认：

1. Gate A：阶段 0，基线报告契约与隔离 fixture、保护性测试、边界清单，以及 H-01 投递选择和 Maintenance/租约数值契约；除独立的 H-01 `Surgical Fix` 外，不改变其余运行时行为，也不读取现有环境玩家数据。
2. Gate B：阶段 1，以已验收的 H-01 post-commit 边界为前置条件，再做纵向架构提取和竞技场行为等价拆分。
3. Gate C：阶段 2 的 additive migration、RNG/粘性路由、画像和不可变 policy release 数据结构。
4. Gate D1：阶段 3 持久人口 demand、注册后人口触发与 Bootstrap V2；demand additive schema 先独立验证，事件接线和当前生成器强度保护必须同批交付。固定 fixture 的
   `conservative_cold_start`、八档历史年龄投影、硬约束和经济门禁通过后允许在样本不足时创建，不需要真人数据。
5. Gate D2：content-addressed catalog/evidence 激活机制、Bootstrap/Maintenance calibrated consumer、原始 candidate artifact 指标重算和 generator provenance 均已实现；唯一剩余退出条件是获批代表性匿名真人 artifact/report。参考分布校准是独立可选门禁，不阻塞 D1 或 Gate E；当前 catalog/route 继续为空。
6. Gate E：阶段 4 Maintenance V2；readiness 必须重新评审锁顺序、领域 command matrix、单动作事务、24 小时强度预算、
   八档实时成长节奏/跨段语义、`guest_healing` 的生命/库存 parity、虚拟监牢日清的 cutoff/批次/暂停态语义，以及竞技场
   普通赛/共斗虚拟补位满血 snapshot 的 V1/V2、真人隔离、fail-closed 物化和并发不写 Guest 证明；
   随后按 `v2_cutover -> V1 清零 -> v2_active -> Gate E 退出` 顺序执行。

本轮明确延后：主动 Raid、市场、拍卖、帮会、聊天和完整 HFSM。这样可以解决当前已经确认的结构与自然度问题，同时不把产品风险扩展到真人资产和社交系统。

范围评估：H-01、复用既有 `RELEASED` 状态的虚拟监牢日清，以及只改变虚拟 Entry snapshot HP 的 M-14 分别是
`Surgical Fix`；`guest_healing` 的 actor-neutral locked primitive 和 Maintenance 接入属于 Gate E 的 `Structural Shift`；Gate B 是
`Structural Shift`；从 Gate C 开始涉及持久化执行器、策略发布和当前测试数据的一次性 V2 入组，整体属于
`Architecture Migration`。各 gate 不得因为同属一份文档而合并授权。

Gate A 五项设计决定、保守验收配置、H-01 独立修复、隔离基线契约及交叉竞态证据已经闭合，Gate B owner
迁移可以开始。当前是测试环境，项目处于开发阶段，环境类别为非生产：Gate D1 通过固定 fixture 门禁后，Bootstrap V2 对所有
新 Bot 直接 100%；Gate E readiness 通过 disposable database benchmark、锁等待和失败注入门禁后，先进入 `v2_cutover`，
完成一次性测试数据处理并验证运行时有效 V1 为 0，再让 Maintenance V2 与所有运行中或可重新激活的测试档案直接 100%，
随后退出 Gate E。缺少代表性样本时只关闭 Gate D2 参考分布校准策略，已通过安全门禁的冷启动人口供给不关闭，也不回退 V1；
缺少 Maintenance benchmark 时不得进入 Gate E cutover。Gate C 的模型增量字段，以及 Gate E 的一次性测试数据入组与领域写路径修改，
仍需要在上一 gate 验收后再次明确确认。未来生产发布、灰度、观察期和存量迁移全部不在本次授权内。

---

## 19. 残余风险与待确认事项

即使按本方案完成，仍保留以下风险：

- 低活跃地区或高声望段可能没有足够真人样本；保守 cold-start 会牺牲分布自然度和竞争性，但优先保证 Bot 不明显强于
  少量真人。质量只能随样本积累并经参考分布校准策略改善。
- 开放终段 `mythic: [240000, +∞)` 能承接未来更高声望，但当该段真人数量或内部强度跨度足以使同段参考失真时仍会过宽；
  届时必须提升 `band_schema_version`、新增更高段并显式重分段，不能热改边界或让现有 Bot 瞬间追平。
- 冻结的分段节奏是当前非生产环境首版保守值；高段样本少时会被四档样本保护进一步减速甚至冻结，可能造成显式需求下的
  高段 Bot 长期偏弱。只能通过新的不可变 policy 和对应 gate 调整，不能在 Arena/Admin 临时放宽。
- `last_strength_increase_at` 能约束受控动作，但 PVP 等外部领域结果采用提交后记录，进程故障可能形成短暂对账延迟；Gate E
  必须验证幂等补偿和匹配侧 fail closed，不能让读取路径顺手修复或回滚已提交的玩家结果。
- 稀疏 cohort 可能由单个异常真人主导。P50/P75、正向扰动、综合/分项上限和 24 小时预算会限制影响，但不能把
  1--29 个样本包装成稳定分布结论；已有 Bot 在上限下降后只冻结成长和退出自动匹配，不自动改写资产。
- 真人样本本身可能包含历史版本遗留、不健康经济或极端玩法，联合快照必须持续做离群和版本漂移监控。
- candidate correctness verifier 已从四类原始 cohort 独立重算全部指标，以 12 维 fit/holdout 模型识别联合异常，并校验 generator/source provenance 与外部 HMAC attestation；剩余风险是当前没有获批代表性真人 artifact/report，且未来生产签名方案尚待独立评审。任何 synthetic passed fixture 仍不得升级为 Gate D2 退出证据。
- 当前环境启用/暂停验收值是在无代表性快照时冻结的保守上限，可能阻断 Gate D2 参考分布校准策略或 Gate E Maintenance；
  实测不通过时必须重新评审，不能静默放宽，也不能反向关闭已通过强度门禁的冷启动人口供给。
- 领域 command 提取量取决于阶段 0 的实际锁图，特别是募兵、建筑和科技的 start/complete 拆分，可能扩大阶段 4 工期。
- 本次明确保持门客招募与 V1 模板晋升现状，因此当前特殊/任务模板进入虚拟阵容及被真人俘获招募的公平性风险不会由本增补消除；
  后续必须以独立资格 selector、俘获边界和存量数据处理方案解决，不能把监牢日清误报为该问题已关闭。
- 虚拟监牢日清只把囚犯状态改为 `RELEASED`，不会恢复俘获时已删除的原门客或装备；若产品以后要求赎回/返还，现有
  `JailPrisoner` 快照不足以完整重建，需要独立数据模型与迁移设计。长期保留 `RELEASED` 记录也需要另行冻结保留期和清理水位。
- 主动治疗只消费已有药品；虚拟庄园无药或 Maintenance 暂停时仍依赖全局被动回血。若被动扫描容量不足，阵容恢复仍可能延迟，
  需要用 backlog age 指标评估，而不能改成免费回满。
- 竞技场虚拟补位满血是明确的 match snapshot 特例，因此虚拟门客可能在庄园仍为残血时以满血参加竞技场；它不会让非 `IDLE`
  门客参赛，也不会治愈真实 Guest。若未来要求竞技场消耗庄园 HP 或禁止残血 Bot 进入候选池，需要另立跨赛事/门客状态设计，
  不能把 snapshot 反向同步伪装成治疗。
- 新增虚拟 Entry 写入口若绕过 `virtual_backfill.py` 仍可能漏掉满血断言；所有虚拟 snapshot link 创建必须继续受 owner/AST 契约和
  `current_hp == max_hp` 集成测试约束。
- Raid 成功后的退休建议使用已批准的非持久 post-commit，在进程崩溃窗口可能永久丢失；该风险由掉落主事务内的经济裁剪、degraded 日志和普通生命周期兜底，不提供最终必达保证。
- `BotPolicyRelease` 保证应用层版本不可变，但数据库管理员仍可绕过服务直接改表；生产权限、审计和备份必须把该表视为配置发布资产。
- 竞技场后备拆分只降低代码耦合，不自动消除跨 `BotProfile / Manor / Guest / ArenaVirtualDemand` 的锁风险，真实 MySQL 锁图仍是 Gate E 前置证据。
- 已冻结的 `MAX_NO_ACTION_LEASE_AGE = 12h` 仍需在 Gate E 验收及当前非生产环境全量启用后观察 shortage、创建预算和锁等待；任何改值都要重新评审，运行时不得用重试重置单个租约 deadline。
- 统计分布接近参考快照不等于主观体验自然，当前非生产全量验收仍需盲测阵容样本和实际 PVP 对局，而不能只看单项指标。
- V2 会改变虚拟玩家资产组合，即使战利品上限不变，也可能改变攻击难度、竞技场可用性和真人目标选择，需要持续观察跨系统指标。
- 首轮没有主动 Raid、交易、聊天和社交行为，因此只解决“档案与成长自然度”，不会让虚拟玩家具备完整在线玩家行为。

这些风险不是扩大首轮范围的理由。任何主动战斗或经济交互都应在本迁移稳定后另立方案，重新评估真人资产、反作弊和产品告知边界。
