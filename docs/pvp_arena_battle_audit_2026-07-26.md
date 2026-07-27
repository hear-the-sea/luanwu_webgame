# 虚拟玩家、竞技场与 PVP 战斗系统审计及整改方案（2026-07-26）

最近更新：2026-07-27

文档状态：整改代码、目标数据库迁移、hermetic 默认门禁和真实服务并发门禁均已完成；应用进程级发布重启不在本轮范围。

相关文档：

- [架构设计](architecture.md)
- [数据流边界](domain_boundaries.md)
- [第二阶段统一写模型基线](write_model_boundaries.md)
- [技术审计（2026-03）](technical_audit_2026-03.md)
- [优化计划](optimization_plan.md)

## 0. 执行摘要

本轮审计覆盖虚拟玩家、普通竞技场、竞技场共斗、玩家 PVP、帮会 PVP 和共享战斗引擎。

审计与整改确认：

- 原始审计识别的 12 个确定实现问题均已落地代码和回归测试。
- 7 个设计风险中，D-02 至 D-07 已按最小安全边界收口；D-01 只提取了纯 population planner，未进行一次性虚拟玩家架构迁移。
- 当前扩大领域回归为 689 项通过；2026-07-27 最终全量 hermetic 为 4295 项通过、73 项按 integration 标记排除，另有 7 项 subtests 通过。
- Black、isort、flake8、mypy、Django system check、迁移漂移检查和 diff whitespace 检查均已通过。
- 目标 MySQL 验收库已应用 battle 0007、gameplay 0135 至 0137、guilds 0024 至 0026；迁移后 dry-run 审计 findings=0，因此未执行无必要的 --apply 写修复，也未追溯调整历史经济结果。
- 最终真实服务 critical 门禁为 49 项通过；完整 integration 标记集为 73 项通过、4290 项排除。

问题分级如下（保留原始审计分类，当前实施状态见第 12 节）：

| 等级 | 数量 | 范围 |
|------|------|------|
| P1 | 6 | 战斗伤亡不一致、持久任务卡死、解散帮会仍结算、PVP 人数上限、竞技场坏快照 |
| P2 | 6 | 空帮会生成 AI、共斗贡献、随机重放、状态持续时间、竞技场裁决 |
| 设计风险 | 7 | 虚拟玩家职责、状态语义、行军规则、随机上下文、模型约束、修饰器生命周期、遗留 seed 接口 |

实际整改顺序：

1. 先修复战斗 HP、兵力和有效伤害不变量。
2. 再建立出征失败终态、资源补偿和扫描隔离。
3. 收口帮会解散、空阵容和防守人数规则。
4. 建立可重放的随机数上下文。
5. 最后添加数据库约束，并渐进拆分虚拟玩家模块。

## 1. 审计范围与验证基线

### 1.1 审计范围

主要审查文件和调用链包括：

- 虚拟玩家：gameplay/services/virtual_players.py 及竞技场虚拟后备、补量和退役逻辑。
- 玩家 PVP：gameplay/services/raid、gameplay/tasks/pvp.py、RaidRun。
- 帮会 PVP：guilds/services/guild_raids.py、guilds/tasks.py、GuildRaidRun。
- 普通竞技场：gameplay/services/arena/core.py、match_helpers.py、snapshots.py。
- 竞技场共斗：coop_core.py、coop_battle.py、coop_damage.py、coop_settlement.py。
- 战斗引擎：battle/execution.py、battle/simulation、battle/status_manager.py、battle/combatants_pkg。

审查重点：

- 状态机是否存在无法到达终态的路径。
- 事务失败后是否能够补偿资源。
- 批量扫描是否会被单条坏数据中断。
- 战斗结果、奖励和随机行为是否能够重放。
- HP、兵力、伤亡和贡献是否保持一致。
- 虚拟玩家与竞技场后备是否存在重复租约或错误补量。
- 数据库约束是否能保护跨模块调用和异常写入。

### 1.2 初始审计验证

以下数据是整改开始前用于确认问题边界的快速基线；整改后的验证证据统一记录在第 12 节。

| 测试范围 | 结果 |
|----------|------|
| 玩家 PVP | 105 passed |
| 帮会 PVP | 35 passed |
| 普通竞技场与共斗 | 79 passed |
| 战斗系统 | 124 passed |
| 虚拟玩家与竞技场虚拟后备 | 233 passed, 7 skipped |
| 合计 | 576 passed, 7 未执行 |

当前测试使用 SQLite、LocMem cache、InMemoryChannelLayer 和 memory Celery 的 hermetic 快速门禁。

7 个未执行用例属于真实数据库并发测试。单独运行时测试门禁明确要求：

~~~text
DJANGO_TEST_USE_ENV_SERVICES=1 make test-integration
~~~

上述是整改开始前的初始基线，当时尚未验证 MySQL select_for_update、真实 Redis 锁和真实任务派发下的竞争行为；最终整改树的真实服务证据见第 12.4 节。

### 1.3 已确认正常的基线

以下行为在当前实现和测试中表现合理：

- 玩家 PVP 发起路径在事务内复核目标保护、行动点、并发数、门客状态、护院数量和携带容量。
- 帮会 PVP 正常结算使用帮会根锁、状态门槛和 loot_settled，能够避免重复发放战利品。
- 普通竞技场报名和取消会锁定庄园、赛事和门客，并具有赛事报名唯一约束。
- 共斗报名具有庄园锁、赛事锁、报名唯一约束和槽位唯一约束。
- ArenaVirtualReserveMember.profile 的一对一租约可以避免同一虚拟玩家同时参与普通赛和共斗。
- 虚拟玩家退役流程会避开虚拟后备租约和仍在进行的竞技场参与者。
- 地图虚拟玩家补量会重新统计实际可攻击目标，而不是只依赖旧计划数量。

这些基线应在整改过程中保持，不应因为结构调整而退化。

## 2. 实现问题清单

### I-01 护院 HP、兵力和永久伤亡不一致

等级：P1

证据：

- [battle/simulation/damage_application.py](../battle/simulation/damage_application.py#L105)
- [battle/utils/battle_calculator.py](../battle/utils/battle_calculator.py#L159)

当前行为：

- 每次攻击独立执行 int(damage / unit_hp) 计算死亡人数。
- 不足一名护院的伤害不会累计到后续攻击。
- 目标 HP 小于等于零时，没有强制将 troop_strength 清零。
- 永久伤亡结算依据 initial_troop_strength 与 troop_strength 的差值，因此会少算甚至完全不算损失。

已验证的异常示例：

- 初始 10 名护院。
- 单兵 HP 为 100。
- 连续 17 次承受 60 点伤害。
- 最终得到 hp = -20、troop_strength = 10。

影响：

- 所有包含护院的战斗都可能少算击杀。
- 玩家和帮会 PVP 返程兵力可能多于实际存活兵力。
- 永久伤亡、战利品容量和后续战斗资源都会受到影响。
- 战报显示、资源结算和实际战斗胜负使用了不同状态口径。

建议方案：

建立唯一的伤害状态转换函数，并规定以下不变量：

- HP 始终处于 0 到 max_hp 之间。
- troop_strength 等于 ceil(hp / unit_hp)，HP 为零时兵力必须为零。
- kills 等于攻击前兵力减去攻击后兵力。
- 反伤、反击、普通攻击和后续可能新增的持续伤害都必须调用同一入口。
- 不再分别维护“累计 HP”和“本次击杀”两套互不约束的状态。

伤害结果应区分：

- raw_damage：理论计算伤害。
- applied_damage：实际扣除的 HP。
- overkill_damage：超过剩余 HP 的伤害。

必须补测：

- 多次小伤害与一次等额总伤害应产生相同剩余兵力。
- HP 为零时兵力必为零。
- 反伤和反击也保持相同不变量。
- 重复结算不会再次扣除或返还兵力。

### I-02 玩家 PVP 损坏快照会永久卡在 MARCHING

等级：P1

证据：

- [gameplay/services/battle_snapshots.py](../gameplay/services/battle_snapshots.py#L245)
- [gameplay/services/raid/combat/battle.py](../gameplay/services/raid/combat/battle.py#L262)
- [gameplay/tasks/pvp.py](../gameplay/tasks/pvp.py#L170)
- [gameplay/tasks/pvp.py](../gameplay/tasks/pvp.py#L312)

当前行为：

- 非字典、空对象或字段非法的快照可能抛出 AssertionError、TypeError 或 ValueError。
- 战斗事务回滚后 RaidRun 仍处于 MARCHING。
- Celery 单任务只重试数据库基础设施异常，不处理持久数据损坏。
- 批量扫描也只捕获基础设施异常。
- 排序靠前的坏记录可以持续中断扫描，使后续到期出征无法处理。

影响：

- 出征门客长期保持占用状态。
- 已扣除的护院长期停留在出征记录中。
- 记录无法进入 RETURNING、COMPLETED 或 FAILED。
- 同一坏记录可能形成周期任务的“毒记录”。

建议方案：

- 新增 InvalidBattleSnapshotError，禁止使用 AssertionError 表示持久数据损坏。
- 扩展 RaidRun.FailureReason，增加 INVALID_GUEST_SNAPSHOT 和 INVALID_TROOP_LOADOUT。
- 复用 RaidRun 已有 FAILED 状态。
- 增加幂等的 fail_raid_run_and_release_resources 服务。
- 在同一事务中设置 FAILED、failure_reason、completed_at，并释放门客、返还未结算护院。
- 批量扫描按单条记录捕获 InvalidBattleSnapshotError，完成失败收口后继续处理下一条。
- 未知程序异常仍然向外抛出，不使用 except Exception 掩盖缺陷。

必须补测：

- guest_snapshots 不是列表。
- 列表中包含空对象、字符串或非法数值。
- 第一条为坏记录时第二条正常记录仍能完成。
- 失败补偿执行两次只返还一次资源。
- 失败后门客状态和出征数量统计恢复正常。

### I-03 帮会 PVP 损坏快照会卡住出征并中断扫描

等级：P1

证据：

- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L330)
- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L381)
- [guilds/tasks.py](../guilds/tasks.py#L264)
- [guilds/tasks.py](../guilds/tasks.py#L301)

当前行为：

- 帮会战斗先将状态改为 BATTLING，但该修改处于整体事务内。
- 快照构建异常后事务回滚，记录重新保持 MARCHING。
- GuildRaidRun 没有 FAILED 状态和 failure_reason。
- 单任务和扫描任务都只捕获基础设施异常。

影响：

- 帮会护院可能永久处于在途状态。
- 同一坏记录反复成为扫描首项并阻塞后续记录。
- 运维只能手工修改数据，缺少可审计的业务补偿入口。

建议方案：

- 为 GuildRaidRun 增加 FAILED 状态和 FailureReason。
- 增加 failure_reason 字段，completed_at 可以复用现有字段。
- 建立 fail_guild_raid_and_release_resources 幂等服务。
- 明确失败时是否返还完整出征护院；在尚未产生有效战报时应完整返还。
- 使用与正常返程相同的帮会根锁和仓库锁顺序。
- 扫描任务按记录隔离 InvalidBattleSnapshotError。

必须补测：

- 损坏快照能够进入 FAILED。
- 帮会护院只返还一次。
- 坏记录不会阻塞后续到期出征。
- 两个 worker 同时处理同一坏记录时只有一个完成补偿。

### I-04 已解散帮会仍可继续战斗和领取战利品

等级：P1

证据：

- [guilds/services/guild.py](../guilds/services/guild.py#L261)
- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L344)
- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L471)

当前行为：

- 解散帮会只设置 Guild.is_active = False，并停用成员、删除英雄池。
- 解散过程没有检查或关闭在途帮会 PVP。
- 战斗阶段和返程结算阶段都没有重新检查双方帮会是否活跃。
- 已解散帮会仍可能继续产生战报、返还兵力和获得战利品。

影响：

- 非活跃经济主体仍然参与资源流转。
- 删除英雄池后还可能与空防守自动生成 AI 的问题叠加。
- 解散与战斗并发时缺少清晰的锁顺序和业务结果。

建议方案：

第一阶段采用最小、明确的业务规则：

- 存在 MARCHING、BATTLING、RETURNING 或 RETREATED 的帮会 PVP 时禁止解散。
- 页面明确提示先撤回或等待出征完成。

同时保留防御性检查：

- 防守帮会失效时，进攻方自动撤回，不产生战斗和战利品。
- 进攻帮会失效时，记录进入 FAILED，返还兵力，不发放战利品。

锁顺序必须与帮会 PVP 现有锁顺序一致：

- 按帮会 ID 排序锁定双方帮会。
- 再锁出征记录。
- 最后锁护院仓库和其他资源记录。

需要产品确认：

- 是否允许“强制解散并自动取消所有在途活动”。
- 已战斗但尚未返程的战利品是否仍允许到账。

在没有明确规则前，不建议自动结算复杂状态。

### I-05 玩家 PVP 防守方绕过庄园出战人数上限

等级：P1

证据：

- [gameplay/services/raid/combat/battle.py](../gameplay/services/raid/combat/battle.py#L395)
- [gameplay/models/manor.py](../gameplay/models/manor.py#L262)
- [battle/services.py](../battle/services.py#L274)

当前行为：

- 防守方加载全部 IDLE 门客。
- defender_max_squad 被设置为全部空闲门客数量。
- 庄园的 max_squad_size 没有参与防守阵容截取。

影响：

- 防守方可以使用超过游侠宝塔允许数量的门客。
- 庄园门客越多，防守方获得的额外优势越大。
- 攻防双方使用了不同的阵容容量规则。

建议方案：

- 增加 select_player_defender_lineup(manor) 共享选择器。
- 继续沿用当前稀有度、等级和 ID 排序。
- 查询结果只截取 manor.max_squad_size。
- defender_max_squad 明确传入该上限，而不是传入查询结果总数。
- 只有实际参战门客进入 defender_guest_ids 和伤害回写范围。

必须补测：

- 上限为 3、空闲门客为 10 时只能上阵 3 人。
- 上阵顺序符合现有稀有度和等级规则。
- 未上阵门客不受到战斗伤害。

### I-06 竞技场损坏快照可能卡住普通赛事或共斗扫描

等级：P1

证据：

- [gameplay/services/arena/snapshots.py](../gameplay/services/arena/snapshots.py#L79)
- [gameplay/services/arena/match_helpers.py](../gameplay/services/arena/match_helpers.py#L249)
- [gameplay/services/arena/core.py](../gameplay/services/arena/core.py#L555)
- [gameplay/services/arena/coop_core.py](../gameplay/services/arena/coop_core.py#L280)
- [gameplay/tasks/arena.py](../gameplay/tasks/arena.py#L23)
- [gameplay/tasks/arena.py](../gameplay/tasks/arena.py#L58)

当前行为：

- 快照字段直接执行 dict 和 int 转换，缺少统一 schema 验证。
- 普通赛在进入 simulate_report 的异常保护前加载快照。
- 共斗在事务中改为 RUNNING 后加载快照，异常会使事务回滚到 PREPARING。
- 扫描任务只处理数据库基础设施异常。

影响：

- 普通赛事的某一场对局可能永久保持 SCHEDULED。
- 共斗活动可能永久保持 PREPARING。
- 排序靠前的坏活动可能阻塞后续活动扫描。

建议方案：

- 报名写入时验证快照 schema。
- 读取历史快照时仍执行防御性验证，避免旧数据绕过。
- 普通赛单方快照损坏时将该方判为 FORFEIT，不中断整轮。
- 双方均损坏时使用确定性规则结束对局。
- 共斗单个报名损坏时将该报名改为 CANCELLED 并释放租约。
- 有效报名不足 player_limit 时退回 RECRUITING；活动窗口已经结束时改为 CANCELLED。
- 每个赛事或活动独立处理领域数据异常。

必须补测：

- 普通赛一个坏报名不会阻塞同轮其他比赛。
- 共斗一个坏报名不会阻塞其他活动。
- 虚拟报名损坏时租约能够释放。
- 赛事仍能到达 COMPLETED、FORFEIT 或 CANCELLED 终态。

### I-07 空帮会防守被补成随机 AI

等级：P2

证据：

- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L387)
- [guilds/services/guild_raids.py](../guilds/services/guild_raids.py#L393)
- [battle/defender_setup.py](../battle/defender_setup.py#L55)
- [battle/defender_setup.py](../battle/defender_setup.py#L82)

当前行为：

- 空门客列表通过 defender_guests or None 转换为 None。
- 空护院配置通过 defender_setup or None 转换为 None。
- 战斗引擎将两个 None 解释为“生成默认 AI”。

影响：

- 没有任何防守资源的帮会会凭空获得 AI 门客和护院。
- 战斗胜负、伤亡和战利品均可能错误。
- None 同时承担“未指定”和“明确为空”两种含义。

建议方案：

定义明确的阵容输入契约：

- None 表示调用方请求自动生成 AI。
- 空列表表示明确没有门客。
- 空 troop_loadout 表示明确没有护院。
- PVP 和竞技场等持久玩法默认禁止自动生成 AI，只有调试、普通 PvE 或明确 AI 模式可以启用。

建议进一步为 BattleOptions 增加显式 defender_mode：

- explicit。
- generated_ai。

空防守应产生攻击方胜利的合法战报，或者由编排层直接生成弃权结果，不得回退到 AI。

### I-08 共斗遗漏多目标技能的次要目标伤害

等级：P2

证据：

- [battle/simulation/attack_execution.py](../battle/simulation/attack_execution.py#L35)
- [gameplay/services/arena/coop_damage.py](../gameplay/services/arena/coop_damage.py#L6)

当前行为：

- 多目标攻击的主目标事件位于回合 events。
- 次要目标事件被放入主事件的 additional_targets。
- 共斗伤害聚合只遍历第一层 events。

影响：

- 多目标门客的贡献被系统性少算。
- Boss 和守卫伤害分类也会少算。
- 奖励排名偏向单目标技能。

建议方案：

- 在战斗报告层提供共享 iter_damage_events(rounds)。
- 统一展开主事件和 additional_targets。
- 共斗、战报统计和未来任务目标都复用该迭代器。
- 不建议只在 coop_damage.py 内写一次局部递归，避免其他统计再次遗漏。

必须补测：

- 双目标和三目标攻击全部计入。
- 主目标为 Boss、次目标为守卫时分类正确。
- 主目标为守卫、次目标为 Boss 时分类正确。

### I-09 共斗按理论伤害而非有效伤害计算贡献

等级：P2

证据：

- [battle/simulation/damage_application.py](../battle/simulation/damage_application.py#L105)
- [battle/simulation/attack_execution.py](../battle/simulation/attack_execution.py#L145)
- [gameplay/services/arena/coop_damage.py](../gameplay/services/arena/coop_damage.py#L10)

当前行为：

- 目标只剩 1 HP 时，巨大攻击仍把完整伤害写入事件 damage。
- 共斗直接把 damage 作为贡献。

影响：

- 尾刀可以通过过量伤害获得不成比例的贡献。
- 高爆发阵容可能获得错误奖励优势。
- 所有玩家贡献总和可能远大于敌方实际总 HP。

建议方案：

- 保留 damage 作为兼容显示字段。
- 新增 applied_damage 和 overkill_damage。
- 共斗贡献只使用 applied_damage。
- 反伤和反击如果未来计入贡献，也必须使用各自的有效伤害。

必须补测：

- 对剩余 1 HP 目标造成 10000 点伤害时贡献只增加 1。
- 所有玩家 applied_damage 总和不超过敌方初始总 HP。
- 多目标攻击的每个目标分别钳制有效伤害。

### I-10 指定 seed 无法重放具名 AI 战斗

等级：P2

证据：

- [battle/execution.py](../battle/execution.py#L91)
- [battle/defender_setup.py](../battle/defender_setup.py#L66)
- [battle/combatants_pkg/ai_generator.py](../battle/combatants_pkg/ai_generator.py#L37)
- [battle/combatants_pkg/ai_generator.py](../battle/combatants_pkg/ai_generator.py#L143)
- [guests/growth_engine.py](../guests/growth_engine.py#L78)

当前行为：

- 战斗 RNG 已经根据 seed 创建。
- 具名 AI 构建没有接收该 RNG。
- 等级成长使用新的 random.Random。
- AI 属性点分配使用全局 random.choice。

影响：

- 同一个战报 seed 不能重建相同 AI 属性。
- 相同 seed 可能产生不同回合顺序、伤害和胜负。
- 共斗 Boss 和守卫在生成阶段也可能脱离战斗 seed。

建议方案：

- build_named_ai_guests、allocate_level_up_attributes 和 allocate_ai_attribute_points 都必须显式接收 rng。
- 具名 AI 的随机成长使用 ai_growth 子随机流。
- 共斗活动在创建时保存 base_seed，敌方快照应固定属性或能够从 seed 完整重建。
- 禁止领域逻辑内部自行构造随机源。

必须补测：

- 相同 seed、相同输入生成完全相同 AI 属性。
- 相同 seed 生成相同战报和结算。
- 不同 seed 可以产生不同结果。

### I-11 护院 weakened 状态不会过期

等级：P2

证据：

- [battle/skills.py](../battle/skills.py#L65)
- [battle/utils/status_effects.py](../battle/utils/status_effects.py#L108)
- [battle/status_manager.py](../battle/status_manager.py#L28)

当前行为：

- 护院受到控制时会被转换为 weakened。
- weakened 不具有 skip_action。
- 当前持续时间只在处理 skip_action 状态时递减。
- prepare_combatants_for_round 只提升 pending，不递减 active。

影响：

- 一次削弱可以持续到战斗结束。
- 状态配置中的 duration 与实际行为不一致。
- 护院伤害长期低于设计值。

建议方案：

统一状态行动生命周期：

1. 行动前读取当前 active 状态。
2. 如果控制状态阻止行动，仍视为完成一次行动机会。
3. 行动或跳过结束后，对本次开始前已经 active 的状态统一递减一次。
4. 目标已经行动后新施加的状态写入 pending，下一次行动再生效。
5. 每个状态每个行动机会最多递减一次。

必须补测：

- weakened duration 为 1 时只影响一次行动。
- duration 为 2 时影响两次行动。
- 目标行动前和行动后施加状态的生效时机正确。
- 控制状态不会被重复递减。

### I-12 竞技场平局和双方无门客的裁决不可复现

等级：P2

证据：

- [gameplay/services/arena/match_helpers.py](../gameplay/services/arena/match_helpers.py#L163)
- [gameplay/services/arena/match_helpers.py](../gameplay/services/arena/match_helpers.py#L249)
- [gameplay/services/arena/match_helpers.py](../gameplay/services/arena/match_helpers.py#L314)

当前行为：

- 双方均无可用门客时使用全局 random.choice。
- 战斗报告返回 draw 时再次使用全局 random.choice。
- 随机裁决不与赛事、对局或战报 seed 关联。

影响：

- 相同输入重跑可能晋级不同玩家。
- 无法根据战报解释淘汰结果。
- 多 worker 之间的结果无法稳定复现。

建议方案：

平局首先应用确定性业务规则：

1. 比较剩余有效 HP 比例。
2. 比较本场 applied_damage。
3. 比较有效击杀或剩余单位数量。
4. 仍相同时使用 tie_break 子随机流。

最终裁决依据应写入 ArenaMatch.notes 或结构化结果字段。

需要产品确认：

- 是否接受上述平局优先级。
- 是否希望完全取消随机裁决，最后使用固定报名顺序。

## 3. 设计风险与治理方案

### D-01 virtual_players.py 职责过载

风险：

- 文件约 3900 行。
- 同时处理配置、人口规划、成长、装备、退役、重新激活、锁和补量。
- 纯计算与 ORM 写操作混合，难以独立验证。
- 修改一个生命周期规则可能影响竞技场后备和地图人口。

目标设计：

保留 gameplay/services/virtual_players.py 作为兼容门面，将实现渐进迁移到：

~~~text
gameplay/services/virtual_player_core/
├── config.py
├── population.py
├── projection.py
├── lifecycle.py
├── repository.py
└── maintenance.py
~~~

职责边界：

- config.py：配置读取、规范化和校验。
- population.py：纯人口缺口和分区规划，不访问 ORM。
- projection.py：属性、建筑、门客和装备投影。
- lifecycle.py：状态迁移和业务规则。
- repository.py：ORM 查询、select_for_update 和租约读写。
- maintenance.py：定时任务编排，不实现底层规则。

依赖方向：

~~~text
virtual_players facade
        |
        v
lifecycle / population / projection
        |
        v
repository
~~~

禁止 repository 反向依赖 planner，也禁止纯规划函数直接访问 Django 模型。

实施原则：

- 不进行一次性重写。
- 每次只提取一个稳定职责。
- 旧公开函数继续从 virtual_players.py 导出。
- 每次提取必须有兼容导入测试和行为回归。

### D-02 RETIRED 状态名称与业务语义冲突

风险：

- BotProfile.State.RETIRED 的展示名称为“退场”。
- 现有测试明确要求该状态仍在地图显示且可以攻击。
- 不同模块容易根据名称自行推断错误过滤规则。

目标设计：

短期保留数据库值 retired，只将展示文案改为“休眠”或“暂停维护”。

建立单一状态能力矩阵：

| 状态 | 地图可见 | 可攻击 | 自动成长 | 可参加竞技场 | 可重新激活 |
|------|----------|--------|----------|--------------|------------|
| ACTIVE | 是 | 是 | 是 | 是 | 否 |
| SLOWING | 是 | 是 | 降速 | 是 | 否 |
| RETIRED | 是 | 是 | 否 | 否 | 是 |
| STALE | 否 | 否 | 否 | 否 | 否 |

增加统一策略入口：

- is_virtual_profile_map_visible。
- is_virtual_profile_attackable。
- is_virtual_profile_maintained。
- is_virtual_profile_arena_eligible。
- is_virtual_profile_reactivatable。

地图、PVP、补量和竞技场后备不得自行拼接状态过滤条件。

### D-03 玩家与帮会 PVP 撤退时间规则不一致

风险：

- 玩家 PVP 按已经行军时间计算返程。
- 帮会 PVP 保留最初完整往返 return_at。
- 相同概念在不同玩法中产生不同用户体验和边界行为。

目标设计：

抽取共享 TravelTimeline：

- started_at：出发时间。
- battle_at：预计到达时间。
- travel_time：单程秒数。
- return_at：当前实际完成返程时间。

主动撤退统一使用：

~~~text
elapsed = clamp(now - started_at, 0, travel_time)
return_at = now + elapsed
~~~

因目标保护、目标失效或帮会解散导致的系统撤回也应使用同一规则，除非产品明确规定瞬时返还。

所有侦查、玩家 PVP 和帮会 PVP 共享同一个时间计算模块，禁止复制公式。

### D-04 战斗和经济随机数缺少统一上下文

风险：

- 战斗 seed 只覆盖部分模拟。
- AI 成长、PVP 掠夺、门客抓捕、竞技场平局和共斗稀有掉落使用不同的全局随机源。
- 增加一次随机调用可能意外改变后续经济结果。
- 无法完成战斗与结算的端到端审计重放。

目标设计：

为每个持久活动保存：

- base_seed。
- rng_version。
- battle_engine_version。

使用稳定哈希派生独立子流：

~~~text
combat
ai_growth
loot
capture
tie_break
rare_drop
~~~

派生算法必须使用 SHA-256 或其他稳定算法，不能使用 Python hash。

要求：

- 所有随机函数显式接收 rng。
- 不允许领域服务直接调用模块级 random。
- 子随机流之间相互独立。
- 战报和结算记录保存 seed、版本以及必要输入快照。
- 结算重试必须复用同一个 seed，不能重新生成。

### D-05 ArenaMatch 缺少数据库级完整性保护

风险：

- 缺少 tournament、round_number、match_index 唯一约束。
- 数据库不能阻止攻守报名记录来自其他赛事。
- winner_entry 也没有被约束为攻方或守方。
- 当前主要依赖赛事行锁和服务调用纪律。

目标设计：

增加唯一约束：

~~~text
UniqueConstraint(
    fields=["tournament", "round_number", "match_index"],
    name="unique_arena_match_slot",
)
~~~

所有对局必须由统一 create_scheduled_match 工厂创建，并验证：

- attacker_entry.tournament_id 等于 tournament_id。
- defender_entry 为空或属于同一赛事。
- winner_entry 为空或等于攻方、守方。
- SCHEDULED 不允许 winner_entry、battle_report 和 resolved_at。
- COMPLETED、FORFEIT、BYE 必须有 winner_entry 和 resolved_at。

跨外键的一致性难以直接通过普通 CheckConstraint 表达，因此应同时使用：

- 服务层工厂。
- 模型 clean 或显式验证函数。
- 数据库唯一约束。
- 契约测试。

添加约束前必须先审计现有重复槽位和跨赛事数据。

### D-06 battle_start 修饰器缺少明确生命周期

风险：

- YAML 校验允许 battle_start。
- battle_start 可以配置伤害修饰器。
- 首回合准备会清理非白名单 battle_modifiers。
- 配置在 schema 上合法，但可能在第一次行动前失效。

当前生产 YAML 没有命中该组合，因此属于潜在契约缺陷，而不是当前线上确定故障。

目标设计：

将修饰器按生命周期拆分：

- battle_modifiers：整场持续。
- round_modifiers：当前回合持续。
- action_modifiers：当前行动持续。

默认映射：

| timing | 默认作用域 |
|--------|------------|
| battle_start | battle |
| round_start | round |
| action_before | action |
| attack_before | action |
| hit_taken | action 或显式配置 |
| attack_after | action |

清理规则：

- 战斗开始不清理 battle。
- 回合开始清理旧 round 和 action。
- 行动结束清理 action。
- 被动效果必须声明或能够推导 scope。
- 只有 scope 未提供或为 null 时才允许按 timing 推导；空字符串、空白字符串、非字符串和未知字符串必须拒绝。
- YAML 校验器必须验证 timing、effect 和 scope 的组合是否合法。

不建议继续扩大 PERSISTENT_BATTLE_MODIFIER_KEYS 白名单，因为该方案会随技能扩展持续遗漏。

### D-07 start_raid 的 seed 参数被公开但直接忽略

风险：

- 函数文档宣称支持 seed。
- 实际实现直接删除 seed。
- 调用方可能误以为测试或战斗能够复现。
- 如果未来直接开始使用调用方 seed，又可能允许玩家预测或选择有利结果。

目标设计：

- 普通玩家业务接口不允许传入可控 seed。
- seed 由服务端在创建 RaidRun 时生成并持久化。
- 测试和管理工具使用内部 seed_override。
- 当前 seed 参数先标记废弃并移除文档承诺。
- 下一次明确的兼容清理阶段再删除该参数。

不要直接把现有公开 seed 参数改为生效，因为这可能形成结果预测和重放攻击入口。

## 4. 目标架构

### 4.1 分层边界

目标调用方向：

~~~text
Celery task / HTTP command
          |
          v
PVP / Arena orchestrator
          |
          +--> snapshot validator
          |
          +--> battle engine
          |
          +--> settlement / compensation
          |
          +--> after-commit notification
~~~

职责约束：

- Task 只负责加载 ID、调用编排服务和基础设施重试。
- Orchestrator 负责事务、锁顺序和状态迁移。
- Snapshot validator 只负责结构和数值契约。
- Battle engine 接收完整输入并返回纯战斗结果，不决定持久资源归属。
- Settlement 负责幂等资源变化。
- Notification 只能在事务提交后触发。

### 4.2 战斗结果契约

每次伤害事件至少包含：

~~~text
raw_damage
applied_damage
overkill_damage
kills
target_hp_before
target_hp_after
target_strength_before
target_strength_after
~~~

对旧战报兼容时，可以继续保留 damage 字段，但必须明确其映射到 raw_damage 或 applied_damage，不能让不同调用方自行猜测。

建议为 BattleReport 增加 battle_engine_version，保证未来能够解释旧战报采用的伤亡和状态规则。

### 4.3 出征状态机

玩家和帮会 PVP 应采用相同的主状态语义：

~~~text
MARCHING -> BATTLING -> RETURNING -> COMPLETED
    |           |            |
    +-----------+------------+-> FAILED
    |
    +-> RETREATED -> COMPLETED
~~~

规则：

- FAILED 是终态。
- COMPLETED 是终态。
- 只有编排服务可以写状态。
- Task 不直接修改状态。
- 每个非终态必须存在明确的超时恢复路径。
- 每个资源扣除必须存在且仅存在一个返还或消耗终点。

失败补偿服务必须幂等，并记录：

- failure_reason。
- failure_detail 或结构化日志。
- completed_at。
- resources_released。

### 4.4 阵容输入契约

战斗入口必须区分：

- 未提供阵容。
- 明确空阵容。
- 请求生成 AI。

推荐使用显式模式，而不是继续依赖 None：

~~~text
defender_mode = explicit | generated_ai
~~~

规则：

- 玩家 PVP、帮会 PVP、竞技场和共斗只能使用 explicit。
- PvE、调试器和快速测试可以使用 generated_ai。
- explicit 模式允许空列表，并产生合法的空防守结果。

### 4.5 随机上下文

建议增加 BattleRandomContext：

~~~text
base_seed
rng_version
combat_rng
ai_growth_rng
loot_rng
capture_rng
tie_break_rng
rare_drop_rng
~~~

RandomContext 在持久活动创建时确定。任务重试、扫描补偿和手工恢复都必须重建相同上下文，不能重新抽取 seed。

### 4.6 虚拟玩家领域边界

虚拟玩家规划与执行需要分离：

~~~text
PopulationSnapshot + Config
          |
          v
PopulationPlan
          |
          v
Lifecycle executor with locks
~~~

PopulationPlan 应是可序列化、可测试的纯数据，包括：

- 目标区域和声望段。
- 缺口或过量数量。
- 建议重新激活数量。
- 建议新建数量。
- 建议休眠数量。
- 规划依据版本。

执行器只负责在锁内应用计划，并在实际数据变化后重新验证，避免旧计划直接覆盖新状态。

## 5. 分阶段实施计划

### 阶段 0：锁定业务决策和失败测试

目标：

- 在修改生产逻辑前为所有确定问题增加失败测试。
- 确认解散帮会、竞技场平局和 RETIRED 命名三项业务规则。

交付：

- 护院零碎伤害不变量测试。
- 玩家和帮会坏快照毒记录测试。
- 普通赛和共斗坏报名隔离测试。
- 空帮会不得生成 AI 测试。
- 多目标、过量伤害和 weakened 持续时间测试。
- 相同 seed 重放测试。

完成标准：

- 测试能够稳定复现当前错误。
- 产品决策项有明确结论。

### 阶段 1：修复战斗内核

范围：

- damage_application.py。
- battle_calculator.py。
- attack_execution.py。
- status_effects.py。
- status_manager.py。
- 相关战报序列化和共斗聚合。

改动：

- 建立统一 HP、兵力转换。
- 增加 applied_damage。
- 修复多目标伤害遍历。
- 统一状态递减。

完成标准：

- 所有战斗状态不变量测试通过。
- 共斗贡献总和不超过敌方有效总 HP。
- 现有战报页面和历史战报读取不报错。

### 阶段 2：修复持久任务终态和补偿

范围：

- battle snapshot validator。
- RaidRun 失败原因。
- GuildRaidRun 状态和失败原因。
- PVP、帮会 PVP、普通竞技场和共斗扫描任务。

改动：

- 新增领域快照异常。
- 新增幂等失败补偿。
- 扫描按记录或赛事隔离。
- 为坏数据提供确定终态。

完成标准：

- 任意单条坏数据不阻塞同批次其他记录。
- 所有资源扣除都有唯一补偿终点。
- 两 worker 并发补偿不会重复返还。

### 阶段 3：收口 PVP 业务边界

范围：

- 玩家防守阵容。
- 帮会空防守。
- 帮会解散。
- 玩家、侦查和帮会撤退时间。

改动：

- 统一阵容选择器。
- 显式 defender_mode。
- 解散时检查在途活动。
- 共享 TravelTimeline。

完成标准：

- 攻防人数均遵守明确上限。
- 空帮会不生成 AI。
- 非活跃帮会不产生新战斗或战利品。
- 三类撤退使用同一时间规则。

### 阶段 4：随机数与审计重放

范围：

- BattleReport seed 和 engine version。
- RaidRun、ArenaMatch 或 ArenaCoopEvent 的基础 seed。
- AI 成长、掠夺、抓捕、平局和稀有掉落。

改动：

- 引入 BattleRandomContext。
- 替换领域层全局 random。
- 保存 rng_version。
- 增加端到端重放测试。

完成标准：

- 相同输入、seed 和版本生成相同战报及奖励。
- 子随机流互不影响。
- 任务重试不会改变奖励。

### 阶段 5：数据库约束和虚拟玩家拆分

范围：

- ArenaMatch 约束。
- 虚拟玩家状态能力矩阵。
- virtual_players.py 渐进拆分。

改动：

- 数据审计后添加唯一约束。
- 集中状态策略。
- 提取纯 planner 和 repository。

完成标准：

- 旧导入路径保持可用。
- 规划函数不访问 ORM。
- 地图、PVP 和竞技场共享同一状态能力判断。
- 默认测试、类型门禁和真实并发门禁通过。

## 6. 数据迁移与历史数据修复

### 6.1 模型变更原则

所有模型变更采用先扩展、后启用、最后收紧：

1. 添加可空或有安全默认值的新字段。
2. 部署兼容旧数据的新代码。
3. 执行只读审计。
4. 修复历史数据。
5. 再添加非空和唯一约束。

不建议在同一次迁移中同时添加字段、修复数据并启用严格约束。

### 6.2 建议审计项

历史数据审计至少包括：

- RaidRun 为 MARCHING 且 battle_at 已过期。
- RaidRun 为 BATTLING 且长时间没有战报。
- RaidRun 为 RETURNING 或 RETREATED 且 return_at 已过期。
- GuildRaidRun 的相同异常状态。
- guest_snapshots 不是列表或包含非法元素。
- 已解散帮会关联的非终态 GuildRaidRun。
- ArenaMatch 重复赛事轮次槽位。
- ArenaMatch 的攻守报名记录不属于同一赛事。
- PREPARING 且 prepare_ends_at 已长期过期的共斗活动。

### 6.3 修复命令要求

建议新增专用管理命令，名称可在实现时确定。命令必须：

- 默认 dry-run。
- 输出记录 ID、当前状态、建议动作和资源影响。
- 需要显式参数才允许写入。
- 每次写入使用正式领域补偿服务，不直接 update 多张表。
- 支持按 ID、时间范围和数量限制执行。
- 记录结构化审计日志。

### 6.4 历史战报处理

- 默认不重新计算已经完成战斗的经济结果。
- 对旧 BattleReport 记录 engine_version = legacy 或保留为空并按旧格式解析。
- 只有明确确认经济补偿规则后，才允许批量调整已结算伤亡或奖励。
- 无法确定原始随机上下文时，不应伪造可重放结果。

## 7. 测试与验收矩阵

| 领域 | 必须验证 |
|------|----------|
| 护院伤亡 | 零碎伤害累计、致死清零、反伤、反击、治疗后不变量 |
| 玩家 PVP | 坏快照终态、资源返还幂等、扫描不中断、防守人数上限 |
| 帮会 PVP | 坏快照终态、并发补偿、空防守、非活跃帮会、loot_settled |
| 普通竞技场 | 坏报名弃权、同轮其他比赛继续、确定性平局 |
| 共斗 | 坏报名隔离、租约释放、多目标贡献、有效伤害钳制 |
| 随机数 | 相同 seed 重放、子流隔离、重试不改变结算 |
| 虚拟玩家 | 状态能力矩阵、补量重验、租约互斥、休眠重激活 |
| 数据库 | ArenaMatch 唯一约束、真实 select_for_update 竞争 |

### 7.1 推荐属性测试

战斗系统适合增加属性测试或参数化不变量测试：

- 任意非负伤害序列都不能产生负 HP。
- HP 为零等价于护院兵力为零。
- applied_damage 总和不超过目标初始有效 HP。
- 同一伤害总量的拆分顺序不应改变最终 HP 和兵力。
- 终态补偿调用多次与调用一次结果相同。
- 相同 seed 和版本的结果完全一致。

### 7.2 真实服务门禁

以下用例必须在 MySQL、Redis 和真实任务派发环境中运行：

- 两 worker 同时结算同一 RaidRun。
- 两 worker 同时失败补偿同一 GuildRaidRun。
- 解散帮会与战斗结算竞争。
- 普通竞技场同轮重复扫描。
- 共斗报名、取消和到期结算竞争。
- 虚拟玩家补量和退役竞争。

在真实服务测试通过前，不应宣布并发终态和补偿机制已经封板。

## 8. 需要业务确认的决策

以下内容不能仅由技术实现自行决定：

1. RETIRED 是否继续保持地图可见和可攻击。当前建议保持现有行为，只修改展示名称。
2. 存在在途活动时是否禁止解散帮会。当前建议先禁止。
3. 已战斗但尚未返程时，强制解散是否允许战利品到账。
4. 竞技场平局的确定性比较顺序。
5. 空防守是否需要生成一份零回合战报，还是直接记录弃权。
6. 战报页面继续显示理论伤害还是改为有效伤害。当前建议同时保存两者。
7. 是否需要对历史已完成战斗进行经济补偿。当前建议默认不追溯。

这些决策应先写入 ADR 或本文件的已确认结论，再实施对应业务变化。

## 9. 发布与回滚策略

### 9.1 发布顺序

1. 先发布兼容读取新旧战报格式的代码。
2. 再添加新字段和失败状态。
3. 启用新的失败补偿和扫描隔离。
4. 修复伤害和状态算法。
5. 启用新的随机上下文。
6. 数据审计完成后添加严格约束。

### 9.2 可观测性

至少增加以下结构化事件：

- battle_snapshot_invalid。
- raid_failed_and_resources_released。
- guild_raid_failed_and_resources_released。
- arena_entry_forfeited_invalid_snapshot。
- arena_coop_entry_cancelled_invalid_snapshot。
- inactive_guild_raid_blocked。
- battle_replay_mismatch。

事件应包含：

- run、match 或 event ID。
- 当前状态和目标状态。
- failure_reason。
- 资源返还摘要。
- seed 和 engine version。

### 9.3 回滚

- 模型字段采用添加式变更，旧代码应能忽略新字段。
- 新战报字段使用 JSON 兼容扩展，不删除旧 damage。
- 不在首次发布中删除 legacy seed 参数或旧导入路径。
- 算法回滚时保留 engine_version，避免新旧战报无法区分。

## 10. 完成标准

本轮整改只有同时满足以下条件才能视为完成：

- 12 个实现问题都有对应回归测试。
- P1 问题全部修复。
- 所有非终态出征都有明确超时恢复和失败终态。
- 单条坏记录不能中断批量扫描。
- 护院 HP、兵力和伤亡满足统一不变量。
- 共斗贡献只统计全部目标的有效伤害。
- 同一 seed 和版本可以重放战斗及经济结算。
- 非活跃帮会不能产生新战斗或新战利品。
- 虚拟玩家状态能力由统一策略控制。
- ArenaMatch 数据审计完成并启用唯一约束。
- hermetic 默认门禁通过。
- real-services 关键并发门禁通过。
- 历史数据审计命令完成 dry-run；仅在存在 findings 时经人工确认后执行写入，本次 findings=0，无需 --apply。

## 11. 最终结论

当前系统的正常主流程具备较好的事务锁、唯一约束和幂等结算基础，尤其是玩家 PVP 发起校验、帮会战利品防重复、竞技场报名锁以及虚拟玩家租约机制。

主要问题集中在异常数据和边界语义：

- 战斗引擎没有统一维护 HP、兵力和有效伤害。
- 持久任务缺少统一失败终态和资源补偿。
- None、空阵容和自动 AI 的输入语义混合。
- 随机数只在局部可复现，无法覆盖 AI 和经济结算。
- 虚拟玩家状态和模块职责缺少集中边界。

因此，合理整改方向不是继续增加局部条件，而是优先建立共享契约和状态机，再在这些边界之上修复各玩法。这样可以在保持现有主流程和测试基线的前提下，降低后续新增 PVP 模式、竞技场规则、战斗技能和虚拟玩家行为时的回归风险。

## 12. 整改实施与验收记录

### 12.1 实现问题状态

| 项目 | 状态 | 已实施内容 |
|------|------|------------|
| I-01 护院伤害不变量 | 已实现 | 统一单单位伤害状态转换；HP 钳制到合法范围，护院兵力由剩余 HP 推导；普通伤害、反伤和反击均记录 raw、applied、overkill、击杀及前后状态。 |
| I-02 玩家 PVP 坏快照 | 已实现 | 引入 InvalidBattleSnapshotError、FAILED 原因和 resources_released；正式战斗与审计命令复用同一只读校验器，兼容“空旧快照但仍有关联门客”的合法记录；失败服务在既有锁顺序内幂等释放门客与护院，扫描对单条坏记录隔离。 |
| I-03 帮会 PVP 坏快照 | 已实现 | GuildRaidRun 增加 FAILED、失败原因、replay metadata 和资源释放标记；独立失败服务幂等返还出征护院，坏记录不再中断后续扫描。 |
| I-04 非活跃帮会结算 | 已实现 | MARCHING、BATTLING、RETURNING、RETREATED 均阻止解散；防守帮会失效时系统撤回且不战斗、不产出战利品；进攻帮会失效时失败并按阶段幂等返还资源。 |
| I-05 玩家防守人数 | 已实现 | gameplay.services.pvp_runtime.lineups 统一选择防守门客，并按庄园 max_squad_size 截取；未上阵门客不进入伤害回写集合。 |
| I-06 竞技场坏快照 | 已实现 | 报名快照执行显式 schema 校验；普通赛按单方弃权或双方确定性 tie_break 收口；共斗取消坏报名并在原事务释放虚拟后备租约。 |
| I-07 空帮会生成 AI | 已实现 | 战斗装配严格区分 defender_guests=None 与显式空列表；持久 PVP 的空阵容保持为空，不再回退到默认 AI。 |
| I-08 共斗多目标伤害 | 已实现 | battle.report_events.iter_damage_events 统一展开主目标和 additional_targets，共斗贡献复用该入口。 |
| I-09 共斗过量伤害 | 已实现 | 贡献使用 applied_damage；旧 damage 字段继续兼容显示，新增 overkill 和前后 HP/兵力字段。 |
| I-10 具名 AI 重放 | 已实现 | AI 构建和成长显式接收 rng，使用 ai_growth 子流；相同 seed 可重建相同 AI 属性和战斗输入。 |
| I-11 weakened 生命周期 | 已实现 | battle、round、action 修饰器生命周期分离；行动完成或跳过后统一消费本次已激活状态，pending 状态留到下一行动机会。 |
| I-12 竞技场平局 | 已实现 | 依次比较剩余有效 HP 比例、applied_damage、击杀/剩余单位，仍相同时使用 Match 的 tie_break 子流；裁决依据写入 ArenaMatch.notes。 |

### 12.2 设计风险状态与业务决策

| 项目 | 状态 | 决策与边界 |
|------|------|------------|
| D-01 虚拟玩家职责 | 部分完成 | 纯 population planner 已迁到 gameplay.services.virtual_player_core.population，且 AST 契约保证不导入 Django 或 gameplay.models。旧 gameplay.services.virtual_player_population 仅为兼容 re-export；退出条件是所有内外调用者迁移且兼容窗口结束。其余 projection、repository、maintenance 暂不为拆分而拆分。 |
| D-02 RETIRED 语义 | 已实现 | 数据库值保持 retired，展示改为“休眠”；统一能力矩阵规定其地图可见、可攻击、不维护、不可参赛、可重新激活。地图、PVP、补量和竞技场改用共享策略入口。 |
| D-03 撤退时间 | 已实现 | gameplay.services.pvp_runtime.lifecycle.TravelTimeline 成为玩家 Raid、侦查和帮会 Raid 的共享时间语义；主动撤退与系统撤回均钳制到单程已行军时间。 |
| D-04 随机上下文 | 已实现 | BattleRandomContext 使用 SHA-256 派生 combat、ai_growth、loot、capture、tie_break、rare_drop 子流；持久活动保存 base_seed、rng_version 和 battle_engine_version。兼容参数 rng_source 仅在没有显式 seed 时抽取一次基础种子，战斗仍使用可重放子流。 |
| D-05 ArenaMatch 完整性 | 已实现并迁移 | 统一 create_scheduled_match 写入口、模型 clean/状态不变量和 unique_arena_match_slot 数据库约束已启用；迁移前只读审计无重复槽位、跨赛事引用或非法胜者，迁移后已确认三列唯一索引存在。 |
| D-06 修饰器生命周期 | 已实现 | modifier_lifecycle 明确 battle、round、action 所有权；YAML schema 验证 timing/effect/scope 组合，避免 battle_start 效果首回合前被清理。 |
| D-07 外部 seed | 已收口 | 玩家 start_raid 的旧 seed 参数继续忽略并标记废弃，服务端在创建记录时生成并持久化 seed；调用方无法选择业务随机结果。 |

已确认的其他业务决策：

- 存在任一在途帮会 Raid 时禁止解散，不实施“强制解散并批量取消”。
- 防守帮会在开战前失效时不生成战报和战利品；进攻方按系统撤回返还完整出征护院。
- 进攻帮会失效时不发放预留战利品；已进入 RETURNING 时只返还战报中的幸存护院。
- 空防守通过显式空阵容进入正常战斗契约，不生成默认 AI。
- 历史已完成战斗默认不重新计算经济结果，也不伪造 replay metadata。

### 12.3 数据库、审计命令与可观测性

本轮新增迁移文件：

- battle/migrations/0007_battlereport_replay_versions.py。
- gameplay/migrations/0135_battle_replay_and_failure_state.py。
- gameplay/migrations/0136_alter_botprofile_state.py。
- gameplay/migrations/0137_arena_match_integrity.py。
- guilds/migrations/0026_guild_raid_failure_and_replay.py。

目标 MySQL 验收库的迁移与数据核验已经完成：

- 实际应用 battle 0007、gameplay 0135 至 0137、guilds 0024 至 0026，所有节点均返回 OK，migrate --check 随后通过。
- 迁移前 ArenaMatch 共 0 行；重复赛事轮次槽位、跨赛事报名引用和非法胜者均为 0。
- 迁移后 BattleReport、RaidRun、ArenaCoopEvent、ArenaMatch、ArenaTournament 和 GuildRaidRun 的预期新增字段均存在。
- unique_arena_match_slot 已确认为 tournament_id、round_number、match_index 上的三列唯一 BTREE 索引。
- guilds 0024 将 12 条 mysticism 科技的 max_level 从 1 扩展到 3；guilds 0025 将 2 条历史 completed GuildRaidRun 标记为 loot_settled，结果与迁移前留痕一致。
- audit_pvp_arena_state 默认 dry-run 输出 mode=dry-run findings=0；由于没有建议动作，本次没有运行 --apply。

管理命令 gameplay.management.commands.audit_pvp_arena_state 具有以下约束：

- 默认 dry-run，不调用写服务、不改变状态。
- 只有显式 --apply 才通过正式玩家/帮会失败补偿服务写入。
- 支持 RaidRun、GuildRaidRun ID、时间边界和数量限制。
- 玩家 Raid 的快照判断复用正式战斗只读校验器，避免审计与执行语义漂移。
- 非正 RaidRun/GuildRaidRun ID、非正 limit 和空时间边界在扫描前直接拒绝，不能退化为默认范围。
- 历史玩家 Raid 自动补偿严格限定为 FAILED、missing_attacker_lineup、无战报且 resources_released=False；其他失败原因只保留人工复核路径，避免重复返还。
- 输出当前状态、建议动作、资源影响和结构化审计结果。

后台已展示并只读保护 replay metadata：

- BattleReport、RaidRun、GuildRaidRun。
- ArenaTournament、ArenaMatch、ArenaCoopEvent。
- ArenaExchangeRecord.payload.replay。

同时增加 battle_replay_mismatch 结构化日志，比较持久活动与战报的 seed、rng_version 和 battle_engine_version；不匹配只记录审计事件，不隐藏已经完成的战斗结果。

### 12.4 验证证据

静态与结构门禁：

- 本次变更的 125 个 Python 文件逐个通过 Black 24.10.0 检查。
- 本次变更文件通过 isort 5.13.2。
- flake8 全目标通过。
- mypy：Success: no issues found in 622 source files。
- python manage.py check：0 issues。
- python manage.py makemigrations --check --dry-run：No changes detected。
- git diff --check：通过。

聚焦与扩大回归：

- 竞技场兑换、视图与 helper：57 passed。
- 普通竞技场新增裁决/约束专项：37 passed。
- 帮会失效、解散与补偿专项：13 passed。
- 审计命令与 replay 日志：3 passed。
- 随机重放与 metadata：12 passed。
- population planner 边界：15 passed, 60 deselected。
- replay 后台契约与中文标签：4 passed。
- 全部顶层 arena 测试：187 passed, 3 skipped。
- 竞技场、玩家 Raid、帮会 Raid、战斗内核和虚拟玩家扩大领域集：689 passed。
- 全量首轮：4285 passed, 68 deselected，发现 3 个契约失败；中文玩家提示和动态技术审计基线修复后，对应契约 64 passed、邻接领域 167 passed。
- 2026-07-27 最终全量 hermetic：4295 passed, 73 deselected, 7 subtests passed in 693.19s。
- 本次新增 Raid 审计与随机源契约专项：29 passed；钱庄测试夹具修复后整文件：18 passed。
- 首轮 2026-07-27 全量运行发现钱庄兑换测试的时序抖动：测试余额超过银库容量，在 100 倍时间倍率下触发离线产出时会被合法钳制。测试夹具改为同步提高容量后，第二轮全量通过；生产资源同步逻辑未改动。

真实服务门禁：

- 隔离环境使用 MySQL 8.4.10 与 Redis 7，项目 preflight 确认可达后才进入 pytest。
- 首轮 critical 为 42 passed、2 failed；两项失败均来自旧并发测试工厂手工创建了 guest_snapshots 和 guests 同时为空的非法 RaidRun，生产失败终态正确将其收口为 missing_attacker_lineup。测试工厂改为持久化真实出征门客与合法快照后，定向回归 2 passed in 85.21s。
- 覆盖审计发现原 integration 集尚未直接证明帮会失败补偿竞争、解散与结算竞争、普通竞技场重复扫描、共斗取消与到期结算竞争、虚拟补量与退役竞争；补入 5 项真实 MySQL 回归并接入 critical gate，定向结果 7 passed in 87.47s。
- 最终 critical：49 passed in 516.81s。
- 最终完整 integration：73 passed, 4290 deselected in 1600.63s。

### 12.5 Boundary governance 与 refactor-audit 结论

当前成立的层契约：

- 读侧审计默认不写；所有补偿写入由显式 service/command 所有。
- 玩家 Raid 的持久战斗输入语义由正式战斗模块的共享只读校验器拥有，管理命令只消费该契约。
- 玩家和帮会失败补偿分别由单一服务拥有状态、资源释放、事务和锁边界。
- Task 只负责到期扫描、调用领域服务和窄基础设施重试，不直接拼装多表补偿。
- InvalidBattleSnapshotError 只表示持久快照领域错误；已知领域错误被隔离，未知程序异常继续抛出。
- 随机数由持久活动上下文拥有，调用方不能注入业务 seed；子流之间互不干扰。
- 旧 population 导入路径是有明确退出条件的兼容层，不作为新的聚合入口扩散。

范围评估：本轮整体属于 Structural Shift；战斗状态转换、失败补偿和随机上下文改变了多个共享边界，但没有进行虚拟玩家的一次性 Architecture Migration。D-01 的 planner 提取属于 Surgical Fix。

剩余风险：

- MySQL 不支持项目中既有的条件唯一约束，Django 对 ArenaTournament、ArenaCoopEvent 等模型仍报告 W036；这些约束继续依赖既有事务锁与应用层门禁，本轮新增的非条件 unique_arena_match_slot 不受该限制。
- 真实服务门禁使用隔离 MySQL/Redis，能够证明锁、事务和共享缓存语义，但不能替代发布窗口中的容量监控、慢查询观测与进程健康检查。
- 2026-07-27 的补充修复仅重跑 hermetic 非集成门禁；本节记录的 MySQL/Redis 验收仍是前一轮证据，本次未在缺少目标测试服务的环境中重复执行。
- 当前环境未安装 Black 与 isort，因此 2026-07-27 补充修复没有重复运行这两项；涉及文件 flake8、全量 flake8、全量 mypy、Django system check、迁移漂移检查和 diff whitespace 检查均已通过。
- 工作区中的 Daphne、Celery worker 和 beat 未在本轮重启；工程验收封板不等同于这些运行中进程已经装载最终代码。
- dry-run findings=0，因此 --apply 没有执行；若未来出现历史异常记录，仍必须先复核资源影响并通过正式补偿 service 写入。

### 12.6 当前发布结论

代码实现、目标数据库迁移、历史状态 dry-run 审计、hermetic 领域回归、全量 hermetic 默认门禁、静态检查和 real-services 并发门禁均已完成。迁移只执行已审计的 schema 与随迁移数据更新，历史经济结果未被追溯重算。

当前状态可记为“整改实现完成、工程验收封板”。这表示本文件定义的代码、迁移、数据审计和真实服务并发完成标准已经满足；应用进程重启、发布流量切换和发布后监控仍属于独立运维步骤，不能由本结论替代。
