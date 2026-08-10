# 虚拟玩家培养与竞技场补位策略审计（2026-08-08，清理版：2026-08-10）

> 文档用途：只保留当前开发需要的业务契约、性能证据、待办和放行条件。
>
> 当前结论：V2 主路径、统一含敏捷的竞技场战力公式、日常 16 槽持久化调度、类型化培养硬约束、业务指标、due 选取结构改造和 exclusive 阶段计时已完成代码收口并通过本地定向回归；竞技场旧固定生命周期、死创建阶段、旧执行入口和运行时 schema-1/2 兼容路径已清理，schema-1 仅保留为历史数据的 fail-closed 迁移检查。2026-08-11 Gate A/D1/E artifact 已在隔离 MySQL/Redis 上生成，recorder 内置 evidence verifier 通过；但 `source_state.worktree_clean=false`，独立 clean-source verifier 会拒绝，当前只能作为 release candidate 证据，不能替代 clean handoff 或生产授权。仍待目标 MySQL EXPLAIN/同口径性能矩阵、类型业务数据校准、竞技场真实补位窗口和 4GB 同机长时容量。
>
> 清理说明：历史审计快照、重复的逐次测试日志和已被后续结论覆盖的状态已删除。原始证据仍以 Gate artifact、测试日志和源码为准；本文不再维护重复的历史时间线。

## 1. 当前状态

| 项目 | 状态 | 开发含义 |
|---|---|---|
| V2 虚拟玩家维护路径 | 已作为当前主路径使用 | 旧 V1 源码、旧专用测试及相关引用已清理，后续只维护 V2。 |
| 数据库迁移与 V2 preflight | 代码检查已完成，目标库待发布 | preflight 已统一检查 gameplay 0167～0177 与 guests 0071；本轮新增 gameplay 0171～0177 与 guests 0071，需在目标业务库按发布窗口执行并复核；Arena demand 为 0、准入高水位 undercount 为 0 仍需以目标库实测为准。 |
| 人口硬上限 | 已暂定为 1000 | 统计维护集合中的 BotProfile；达到上限时停止新增和重激活，不退休已有档案。该值是安全护栏，不代表 4GB 容量已通过。 |
| due 统计 aggregate | 已落地并验证 | backlog 总数和区域 distinct 数合并为一次 aggregate；不改变公平配额、排序、锁顺序和 fail-closed 语义。 |
| 阶段级性能采集 | 已落地，生产默认关闭 | Gate E 测量轮次采集阶段 exclusive/inclusive 耗时、SQL、写入和 query fingerprint；`duration_ms` 已扣除嵌套阶段，2026-08-11 artifact 已通过 recorder 内置 verifier，但因 worktree dirty 未通过独立 clean-source verifier，仍需在 clean handoff 时重录。 |
| 竞技场专属培养规则 | 代码已收敛，真实窗口待完成 | 旧固定生命周期、死创建阶段、旧 claim/准入兼容字段、schema-1/2 运行时入口和对应测试已删除；schema-1 数据只触发 fail-closed preflight，不会进入旧执行器。目标驱动 readiness 与安全预算已有回归，真实窗口完成前不得宣称完全上线。 |
| 日常 16 槽培养与独立招募 | 代码已落地，目标库运行验收待完成 | `BotMaintenanceCycle` 已持久化间隔种子、槽位 due、动作状态、完成来源、下一决策时间、类型/预算快照和累计高价动作数；普通招募以 `source=virtual`、operation identity、配额槽位、due hint 和卡池快照隔离真人候选流；尝试记录已可按类型/触发/动作/结果/原因/资源/runway 聚合。 |
| 结构性 SQL/事务优化 | 代码已落地，目标库性能验收待完成 | due 选取移除 `ROW_NUMBER/OVER`，使用一次 aggregate + 有序 bounded scan + Python 分区配额；普通招募使用持久化 due hint 和复合索引；人口硬上限超限 fail-closed，并补 cycle/recovery/attempt/指标维度索引。目标 MySQL 的 EXPLAIN、batch-200 与 200/500/1000 对照仍待重录。 |
| 4GB 同机长时容量 | 尚未完成 | 还没有目标主机的 RSS、swap、OOM、重启、真实多 worker 队列和 1h/6h/24h 证据。 |
| 竞技场旧生命周期字段 | 已由 0173 物理下线 | `created_profile_count`、历史耗尽基线和 `accelerated_growth_rounds` 已迁移出模型；当前准入只使用成员计数与持久化高水位。 |
| 发布交接 | release candidate，正式生产交接待授权 | 当前 worktree dirty，历史 evidence 不能作为本轮发布凭证；大蜜薯酱未执行 git commit/push、Routing 切换或目标环境数据操作。 |

## 2. 已确认的业务契约

以下是当前实现必须遵守的目标口径；标记为“待实施”的部分不能按已上线理解。

### 2.1 日常培养（周期调度部分实施）

- 一个普通培养周期固定为 16 个顺序动作槽位；第一个动作立即执行，后续间隔由周期 identity、槽位序号和类型快照确定性生成，基准范围为 10～15 分钟，各类型只能在此范围内收窄。重试、重复扫描、worker 重启和 claim takeover 不得重新抽样或顺延原计划。
- 15 个间隔名义上为 150～225 分钟；周期完成到下一周期首槽最长等待 23 小时。未完成周期必须复用原持久化 cycle，不能被下一周期覆盖或重置。
- 周期至少持久化 cycle_identity、间隔种子、当前槽位、next_slot_due_at 和周期状态。profile.next_growth_at 只负责首槽或周期级 due，不承载后续槽位计时。
- 声望段的下一周期间隔允许差异化，但所有上限统一不得超过 23 小时；policy 2、schema/checksum、release snapshot 和 evidence 必须使用同一份归一化配置。
- 普通 V2 的 `archetype_pacing` 快照同时冻结银两/粮食预算比例、建筑/科技目标、最大并行训练数、单周期高价动作上限和招募卡池权重；配置变更不会改写已经打开的周期。
- 只有真正提交的业务动作占用槽位；候选评估失败、锁冲突和可重试 NO_ACTION 不伪造成功槽位。确定性无候选时才允许提前以 NO_ACTION 收束。

日常动作顺序：

1. 首个动作前检查一次工资并完成当日工资批次，后续槽位不重复触发工资拦截。
2. 先结算资源产出并处理银两瓶颈：产银不足时优先补产银；产出溢出时升级银两上限；两者同时不足时先补产银。
3. 门客目标人数超过容量时再升级聚贤庄；日常建筑升级走正常报价、资源扣除和倒计时。
4. 资源与容量稳定后，依次考虑建筑/科技、正常门客训练、装备和技能；普通训练使用正常计时、资源结算和等级步长。
5. 任何候选缺口都必须记录，不能因为某一类没有候选而阻塞其他合法类别。

声望保底采用“可支付的正常成长动作优先，未完成进度延期”：能支付时优先安排能产生正常声望的建筑或科技升级；不能支付时稳定银两并记录延期，不凭空增加声望。

### 2.2 日常招募（队列已实施，运行验收待完成）

- 每日默认计划为殿试 3 次、乡试 3 次、村募 3 次；当前实现为 9 个确定性日计划槽位，按 profile/date 生成 0～90 分钟稳定错峰。类型快照可用加权池计划改变三池槽位配比与每池配额，但总槽位仍为 9。招募不占 16 个培养槽位、不扣行动力、不推进 `next_growth_at`，使用独立配额、operation identity 和审计记录。
- 候选池、稀有度概率、候选数量和合法门客过滤复用真实玩家规则；`GuestRecruitment.pool_snapshot` 在启动时冻结卡池条目、可招募模板 ID 和稀有度分布，完成时不重新读取真人概率。不得使用新虚拟玩家阶段稀有度硬上限。
- 招募消耗实际持久化卡池配置中的正常银两、时长和候选数，不能调用会扣行动力且限制同庄园并发数的真人提交入口；银两/工资 runway 不满足时延期，不产生免费招募。
- 完成后虚拟分支自动从候选批次中确定性选择门客，更新 `Guest`、`RecruitmentRecord`、模板技能和自动训练；不生成真人可见的 `RecruitmentCandidate`。满员时只有在重新校验门客状态、稀有度、含敏捷的阵容战力以及未来 72 小时工资 runway 后，才允许用更高稀有度且战力不下降的空闲门客替换；无安全替换对象则保留队列等待容量/ roster 状态变化。
- 招募与普通培养共用 BotProfile → Manor → Guest 锁顺序。锁冲突、资源不足和工资保护只延期，不消耗新增配额；同庄园始终保持一条进行中招募队列；完成招募只更新 roster、招募账本和完成事件，不改普通周期槽位或 `next_growth_at`。

### 2.3 竞技场补位培养（当前实施依据）

- 规则只适用于已经进入竞技场补位候选的后备成员。单纯 V2 身份、普通定时维护或没有补位目标的 arena_acceleration 调用不得获得竞技场专项策略。
- 门客数量不足且聚贤庄已满时，先逐级瞬时升级聚贤庄，每次只升一级；容量满足后才生成招募候选。不升级无关建筑，也不把聚贤庄无条件升满。
- 竞技场建筑升级、门客培养、装备和技能动作瞬时完成，不消耗普通物品或资源，但仍必须经过正式 quote/projection、锁内状态复核、影子成本、幂等 receipt 和战力审计。
- 单次门客培养最多提升 10 级，并按等级上限裁剪；规划投影和锁内执行必须使用同一个实际增量。
- `arena_lineup_power` 的统一单门客公式为“按门客类型加权后的攻击 + 防御 + 最大生命值/10 + 敏捷”；敏捷按 `1:1` 纳入，权重由 `GUEST.AGILITY_TO_ARENA_POWER_WEIGHT=1.0` 固化。真实战斗继续用敏捷决定出手顺序，两者是同一属性的不同用途，不能在 lineup power 中漏算，也不能把出手顺序重复折算成额外战力。
- 终止条件是达到目标门客数、最低等级、合法阵容和选中阵容战力下限后的 READY。旧固定 10 轮/80 次成功动作不再是业务失败条件。
- 8 槽/轮、24 小时执行窗口、单任务/单槽位预算、重试退避、无进展熔断、selected-power 上限、claim fencing、receipt/replay 和 fail-closed 是安全边界，必须保留。
- 新虚拟玩家创建由 Arena demand/population 层负责；培养动作只修改已经进入补位候选的 BotProfile。

0173 在移除字段前把已有创建计数、准入高水位和当前成员数固化为新的单调高水位；随后物理移除 `created_profile_count`、历史耗尽兼容基线和 `accelerated_growth_rounds`。运行时不再读取旧竞技场生命周期语义；旧实现、adapter、旧约束、旧测试和旧 evidence 文件不进入兼容周期。

战力公式变更后，新的真人快照、Bot strength summary、Arena Demand 和候选 projection 必须使用同一公式重算；不能拿旧公式生成的 frozen power 与新公式结果直接比较，发布验收需重新录制相关 digest/evidence。当前运行时、lineup、参考快照和虚拟候选 projection 均按 `GUEST.AGILITY_TO_ARENA_POWER_WEIGHT=1.0` 纳入敏捷；Gate D2 的旧静态 artifact schema 没有敏捷字段且已退役，不能被当作当前战力证明。缺敏捷的历史快照已显式标记 `legacy_missing_agility`，不静默与新快照混比。

## 3. 培养动作、经济节奏与类型待办

本节补充日常培养和竞技场培养的流畅性、科学性待办。目标是让虚拟玩家的动作符合建筑/科技/训练/招募的真实完成时间，同时避免为每个 Profile 创建高频独立任务。

### P0：统一动作开始、完成和下一次唤醒语义

- [x] 在 durable cycle 中补齐 `interval_seed`、`next_slot_due_at`、当前动作状态、动作完成来源和 `next_decision_at`；`profile.next_growth_at` 只负责首槽或周期级 due，不再承担所有后续槽位计时。后续槽位使用 `interval_seed + slot_ordinal` 稳定计算 10～15 分钟间隔，重复扫描不会重新抽样。
- [x] 为建筑、科技、门客训练和已有招募队列记录可用时间/完成时间快照；动作已经启动但尚未完成时，规划器会排除同一领域对象，有其他可用对象时可以继续培养，没有可用对象时会等待最早完成时间并加确定性抖动。
- [x] 补齐领域完成后的正式 reconcile：建筑、科技、训练和招募完成 worker 写入幂等 `BotMaintenanceCompletionEvent`，直达对账任务；维护扫描按 `available_at` 批量兜底。对账在 `BotProfile → Manor → cycle` 锁顺序内重新读取生效战力、资源、工资 runway 和领域队列，写入有限历史，并按领域对象与原完成时间匹配待处理动作；重复事件不会重复推进周期。
- [x] 按 `busy/domain_constraint`、资源不足、工资保护、锁冲突和真正无候选区分重试策略：计时阻塞在完成时间后加确定性抖动唤醒，资源/工资/领域约束保留开放周期并重试，锁冲突只退避且不消耗业务槽位，真正无候选才收束为 `NO_ACTION`。`MaintenanceReasonCategory` 将原因归一为 `domain_constraint`、`resource`、`salary`、`lock_conflict`、`no_candidate`、`policy_guard` 等审计分类。
- [x] 保留 16 槽和 10～15 分钟确定性间隔的业务设计，并实现为一个持久化周期加有界到期扫描；没有为每个 Profile 创建 16 个 Celery 倒计时任务。`scan_virtual_player_maintenance` 每分钟扫描维护 due，同时以独立批量启动普通招募；`roll_virtual_players` 继续按小时负责口滚动。类型化预算/目标/并行约束、基础尝试指标和完成对账已进入主路径；真实配置校准、性能矩阵和容量验收仍未闭合。

本轮实现边界：gameplay 0171～0177 与 guests 0071 已记录建筑、科技、训练和虚拟招募队列的领域完成时间，并增加类型/触发/动作/原因/银粮/工资 runway 的不可变尝试维度；普通招募底层复用 `GuestRecruitment`，但通过 source、profile、operation identity、配额槽位、持久化 due hint 和快照隔离真人候选流，避免重复异步基础设施。确定性无动作会等待最早领域完成后再重试，资源/工资/领域约束不会关闭开放周期，Profile/锁冲突会退避且不占槽位/招募配额；非末槽动作会保留 `SUBMITTED` 与领域完成来源，不再被通用提交来源覆盖。due 选取已改为无窗口函数的 aggregate + 有序 bounded scan，硬上限异常 fail-closed；完成 worker 和周期扫描只负责完成队列、写入/应用对账；虚拟招募完成时只在独立完成分支写 roster 和招募账本，不改普通周期。剩余风险是生命周期 persona、目标业务库校准、完整指标宽表、真实补位窗口和真实容量验收。

### P1：把资源、时间和并行队列纳入候选评分

- [x] 扩展 `CandidateAssessment`，携带预计完成秒数、银两/粮食成本、占用队列、预期战力增益和最终选择分；选择仍保留现有候选分组与安全硬约束，只在同组允许候选中按效率分排序。
- [~] 使用影子价格和时间成本评分：当前已落地银两、粮食分离计价以及完成时长惩罚，评分为纯函数、可复现且不会把未知资源当成免费；首版参数仍是代码内归一化基线，配置化价格校准和真实玩家样本回归仍待补齐。粮食库存后期可能很大，但门客训练的时间、银两工资和建筑队列仍是真实瓶颈。
- [ ] 将最多 2 个建筑队列、最多 2 个科技队列纳入评分和硬约束；已有长时间升级时，优先选择可在空闲队列完成且回报明确的动作。
- [ ] 聚贤庄只在门客数量目标或竞技场关键人数被容量阻塞时升级；普通日常不能因为随机 `building_focuses` 连续升级高价聚贤庄。祠堂升级需要单独计算建筑时间回本，不得把护院/募兵速度加成当成门客招募加速。
- [ ] 生产建筑、仓储建筑和科技升级增加“缺口触发”条件：只有预计出现资源溢出、资源短缺或目标阵容前置不足时才进入候选，避免为了覆盖动作类型而做低收益升级。

当前成本/时间校准样本，作为经济模拟和回归测试的固定输入：

| 动作 | 低等级样本 | 高等级样本 | 设计含义 |
|---|---|---|---|
| 聚贤庄 | 1→2：29,179 银、5,835 粮、约 78 分钟 | 10→11：约 346 万银、69 万粮、约 49.8 小时 | 只能由容量缺口驱动 |
| 祠堂 | 1→2：20,000 银、10,000 粮、2 小时 | 4→5：160,000 银、80,000 粮、原始 16 小时 | 必须按长期队列回本判断 |
| 普通科技 | 0→1：约 8,000 银、60 秒 | 9→10：约 307,547 银、约 20.7 分钟 | 需结合兵种/阵容目标，不只看银两 |

### P1：统一普通门客训练语义

- [x] 普通日常培养统一为“一次启动 1 级正式训练”；普通 V2 规划会忽略较大的 `max_guest_level_step`，避免计划多级而执行只启动一级。竞技场专项仍可使用单独的大步长。
- [~] 训练计划已把预计训练时长和 `guest.training_complete_at` 纳入候选及完成对账；完成 worker 会在属性生效后唤醒周期。一级黑/紫/橙门客基础训练约为 30/45/48 分钟，高等级按每级 7% 指数增长；V2 当前约减少 25% 剩余时间，但训练速度作用范围和“完成后再进入下一次投资”的完整类型测试仍待补齐，不能把减时动作误报为等级已经提升。
- [ ] 明确练功场的作用范围。当前 `guard_training_speed_multiplier` 面向护院训练，不应作为门客升级加速依据；如果要加速门客训练，新增独立规则、成本和回本测试。
- [ ] 增加“核心门客优先、已训练门客排除、训练完成后再进入下一次投资”的测试，覆盖小阵容、全员训练中、门客满级和资源不足场景。

### P1：落地正常虚拟招募队列

- [x] 普通虚拟玩家不得直接调用 `create_guest_from_template` 立即增加门客；普通计划的即时招募 spec 已 fail-closed，普通 roster 增长只能通过独立虚拟招募 operation，保存卡池快照、完成时间、候选数量、资源成本和工资承诺。
- [x] 继续采用独立招募配额，不占 16 个培养槽位、不扣真人行动力、不推进 `next_growth_at`；支付持久化卡池银两并遵守一条庄园一条进行中招募队列。扫描使用有界批量，不为每个 Profile 创建招募倒计时任务。
- [~] 以真实卡池作为校准：运行时使用数据库/YAML 已加载的真实池配置，不硬编码替代成本、时长或候选数量；当前部署数据与审计样本的具体数值仍需在目标业务库做一次校准确认。虚拟玩家不得变成免费即时创建。
- [x] 招募完成后重新计算未来 72 小时工资 runway；满员替换重新校验门客状态、阵容战力、稀有度和工资，只有安全时才删除空闲低稀有度门客，否则保留队列等待，不仅按容量删除或替换。

### P1：补齐虚拟玩家类型和生命周期节奏

- [x] 在现有 `daily_action_bias`、战斗倍率和库存权重之外，为类型增加显式的银两/粮食预算、建筑/科技目标、招募卡池权重、最大并行训练数、单周期高价动作上限和最低行动间隔；约束会进入候选拒绝、重试/收束和周期快照。
- [~] 均衡型使用基准节奏；富裕型优先税务司、银库、酒馆和长期回本建筑；道场型提高训练/装备/技能节奏并提高乡试权重；护卫型先满足防御建筑、兵种科技和村募权重；废弃型只做低频资源维护。类型差异已编码并有确定性回归，但工资倍数、建设回本和目标业务库卡池仍需校准，生命周期 persona 也尚未改变完成度。
- [ ] 生命周期 persona（游客、休闲、长期、老玩家）必须影响动作间隔、预算和完成度，而不只影响 active/abandoned 天数；类型权重仍保持确定性随机，但不能让类型目标被全目录随机 `building_focuses` 和 `technology_focuses` 完全覆盖。
- [~] 增加按类型统计的动作分布、资源消耗和工资 runway：不可变 `BotMaintenanceAttempt` 已记录并提供按类型/触发/动作的 SQL 聚合 API，runway 提供 min/avg/max；门客数量、核心阵容战力、建筑完成时间、24/48/72 小时 runway 和完整周期覆盖率仍需补充业务宽表/验收口径。

### P1：竞技场从“动作数量封顶”切换为“目标完成停止”

- [x] 将“关键门客数、最低等级、合法阵容、选中阵容战力下限”作为业务终止条件；达到 READY 后立即停止免费培养，偏好门客只在有时间余量时补充。
- [x] 保留 8 槽/轮、24 小时窗口、每槽重试上限、claim fencing、receipt/replay 和无进展熔断作为安全边界；旧 10 轮/80 个成功动作已删除，不再作为正常失败条件。
- [x] 竞技场动作顺序固定为：恢复可用性 → 容量缺口时逐级免费升级聚贤庄 → 按关键人数招募 → 只培养能够改善目标阵容的门客 → 装备/技能补足 → 达标即停。每次聚贤庄升级或门客招募后都要重新计算目标，不允许无条件升满或培养整套储备。
- [x] 竞技场免费训练允许较大等级步长时，同时校验单门客等级上限、选中战力下限/上限和目标溢出；普通日常训练规则不被竞技场专项补贴反向放宽。
- [ ] 用竞技场实际补位等待窗口验证提前量：记录 demand 创建到 READY、目标战力溢出、动作数、免费影子成本和未达标原因，避免在截止前集中触发大量动作。

### P2：为流畅性建立业务验收指标

- [~] 普通培养：`BotMaintenanceAttempt` 已记录类型、动作、结果、原因分类、银粮影子成本和工资 runway；周期已持久化开始/完成时间、覆盖动作和 retry history。动作生效延迟、计时阻塞重试次数和同对象重复尝试次数的聚合查询仍待补齐。
- [~] 经济节奏：已能按类型/动作聚合银粮成本、工资 runway 快照和尝试次数；24/48/72 小时 runway、建筑/科技/招募承诺成本、升级回本时间和跨声望段增长仍待业务指标口径确认。
- [~] 类型差异：已提供按 `balanced/rich/dojo/guard/abandoned` 聚合的动作/招募基础入口，并有类型节奏回归；门客数量、核心门客等级、建筑等级、战力增长及 lifecycle persona 对照尚未形成完整验收报表。
- [ ] 竞技场：记录 time-to-ready、critical/preferred 达成时间、selected power 上下界、超出目标比例、每个 demand 的实际动作数和安全预算消耗。
- [~] 流畅性验收必须与性能验收联动：代码使用一次有界 due 扫描和持久化 due 状态，不创建 16 个独立 Celery 任务；目标主机的 RSS、队列等待和 oldest-due 仍待长时验收。

## 4. 调度、锁与容量约束

- 人口滚动继续低频执行；日常维护应拆为独立的到期扫描，采用 1～2 分钟扫描周期和有界批量，不为每个 Profile 创建独立倒计时任务。
- 日常培养、日常招募和竞技场动作统一遵守 BotProfile → Manor → Guest/Building/Inventory 锁顺序。锁冲突只延迟重试，不消耗培养槽位、竞技场槽位或招募配额。
- 当前约 642 个虚拟玩家按 10～15 分钟动作节奏估算，平均约 43～51 次动作/分钟，最坏约 64 次/分钟。普通招募已设置独立有界批量（单次最多 200）并对 profile/date/ordinal 做确定性错峰；仍需在真实容量验收中校准维护、招募和竞技场共享队列的全局吞吐，避免积压后互相争抢。
- 暂定维护集合硬上限为 1000。达到上限后只停止新增/重激活；并发补位不能突破上限。修改 YAML 后必须让 Web、Celery worker/beat 等进程重新加载配置。
- 4GB 单机初始配置为五个 Celery worker concurrency=1、prefetch=1、max tasks per child=200、max memory per child=180000KB；服务声明上限约 2720MB、reservations 约 1728MB。这些是背压基线，不是实际 RSS 或容量结论。
- 任何自动暂停、降速和恢复必须走正式 Routing/pause/recovery 入口；不能直接杀 worker、清空队列、删除 claim 或把未知异常改写成 NO_ACTION。

## 5. 当前性能证据

### 5.1 Gate 与数据库状态

截至 2026-08-10，本轮不把历史 artifact 的通过数量当作当前 canonical evidence：

| 项目 | 当前判断 |
|---|---|
| Gate A/D1/E artifact | 现有 2026-07-30/08-09 文件仅用于历史追溯；source hash、collection count 或 worktree 状态未绑定本轮最终源码，不能作为发布凭证。 |
| Gate source manifest | runtime owner、canonical suite、迁移 0171～0177、guests 0071、类型节奏、指标、招募和 arena owner 已纳入代码清单；clean artifact 仍待外部录制。 |
| 本地 V2 preflight | 代码已覆盖 gameplay 0167～0177 与 guests 0071，缺任一 required `(app, migration)` 时 fail-closed；目标业务库是否全部应用仍待发布窗口只读复核。 |
| 本地质量门禁 | `manage.py check` 通过；`makemigrations --check --dry-run` 无变化；compileall、ruff、git diff check 通过。 |
| 4GB/目标库/生产 handoff | 未完成；仍缺目标主机 RSS、swap、OOM、重启、真实多 worker 队列、MySQL/Redis 连接与 clean handoff 证据。 |

历史 Gate 结果只能证明当时的代码和测试环境内部一致，不证明目标 4GB 主机的 RSS、swap、OOM、MySQL/Redis 连接、共享队列或生产 handoff。新的 artifact 必须由最终 clean source、目标隔离服务和实际执行结果生成，禁止手工沿用旧数量。

本轮最终源码的本地回归结果（各集合有重叠，不能相加）：定向策略/指标/招募/快照集合 `73 passed`；核心维护、周期、类型节奏集合 `111 passed`；架构/配置/阶段/恢复集合 `102 passed, 12 skipped`；竞技场与迁移集合 `149 passed`。以 2026-08-09 artifact 做结构校验时为 `32 passed, 2 failed`，两项失败均为历史 collection metadata 不匹配：D1 contract 记录 382、当前 369；Gate A manifest 记录 179、当前 181。该两项必须在 clean source 重新执行 canonical suite 后由 recorder 生成，不能手工改 artifact。

### 5.2 batch-100 历史基线与 batch-200 当前口径

当前维护扫描、完成对账和普通虚拟招募的默认批次已调整为 200。以下 batch-100 数据只保留为历史对照；Gate E 的冻结 acceptance/evidence 也仍是 batch-100，不能替代本次 batch-200 的新证据。batch-200 的目标 MySQL 和同机容量数据尚未重新录制。

| batch / concurrency | owner p95 | owner p99 | queries max | writes max | lock wait p95/p99 |
|---|---:|---:|---:|---:|---:|
| 100 / 1 | 4848.375ms | 4958.789ms | 2435 | 1002 | 0 / 0ms |
| 100 / 2 | 4758.561ms | 4913.841ms | 2435 | 1002 | 0 / 0ms |

阶段采集入口为 gameplay/services/virtual_player_core/stage_metrics.py，相关无采集/嵌套归属测试为 tests/test_virtual_player_stage_metrics.py；六阶段维护矩阵由 tests/test_virtual_player_maintenance_concurrency_integration.py 覆盖。due selection 位于 `_maintain_due_virtual_players_v2()` 路径。

表中的 2435 queries / 1002 writes 是旧结构的开发态基线，不是本轮结构改造后的性能结论。本轮 due 选取已从窗口函数改为：一次 backlog/region aggregate、一次带确定性排序和 1000 hard cap 的 ordered scan、应用层分区配额截取；普通招募另用持久化 due hint 和复合索引；当 due backlog 超过人口硬上限时 fail-closed，不以截断结果破坏公平。局部回归已确认 SQL 不包含 `ROW_NUMBER` 或 `OVER (`，目标 MySQL 仍需用 EXPLAIN 和 batch-200/200/500/1000 矩阵复测。

旧阶段 p95 诊断中，due selection、planning preload、profile plan/revalidation、action/domain writes、cycle/attempt/receipt 分别约为 12～25ms、3ms、11ms、3ms；safety/task wrapup 约 4.68～4.70s。阶段采集现已按嵌套 child duration 做 exclusive aggregation，并保留 `inclusive_duration_ms` 追查；旧 artifact 仍不能与新口径直接比较，必须重新录制后再对前十 query fingerprint 做 EXPLAIN/索引核对。

### 5.3 单 worker 队列回放

隔离 MySQL/Redis 测试环境、旧批次上限 100 的真实 Broker 回放：

| 同刻 due profile | 任务数 | 最大 queue wait | 最大 owner duration | 最大 oldest-due age |
|---:|---:|---:|---:|---:|
| 100 | 1 | 0.10s | 6.62s | 4.12s |
| 500 | 5 | 65.65s | 6.91s | 85.43s |
| 1000 | 10 | 150.85s | 7.16s | 189.61s |

这说明单 worker 串行消费时队列等待会随批次数线性增长；它验证了公平分批、无重复 claim、无漏选，不代表 batch-200、多 worker 或 4GB 全站容量已经通过。batch-200 需要重新记录 queue wait 和 oldest-due。

### 5.4 性能目标口径

- 当前 batch-100 owner p95 基线约为 4.76～4.85s；若保留“提速 20%”作为工程目标，对应约 3.8～3.9s。
- 3.8～3.9s 只是同环境、同动作分布的优化验收目标，不替换现有 Gate 放行阈值，也不能在未完成对照实验前对外承诺。
- 最初阶段的合理预期是每个有效 Profile 减少约 2～5 次读取、0～1 次写入；最终收益必须由同口径 Gate E 和目标主机容量矩阵确认。

## 6. 性能与容量待办

### P0：把耗时归因做成可行动证据

1. [x] 按单 worker sample 实现 exclusive stage aggregation，`duration_ms` 扣除嵌套阶段并保留 inclusive 值；目标 artifact 仍需证明覆盖至少 95% owner wall time。
2. [~] 保留每阶段 SQL/写入数量、p50/p95/p99 和前十 fingerprint；due 无窗口函数、bounded scan 与索引已落地，目标 MySQL 高频/慢 SQL 的 EXPLAIN 和索引核对待完成。
3. [x] 测量开销单独标注；生产路径默认不启用 collector。
4. 在同一六格矩阵中回归重复/漏选、锁等待、receipt/replay、故障回滚和 readiness。

### P1：减少重复读取和不必要写入

1. 先复用 planning preload、批次 aggregate 和已有 projection，定位真正重复的 profile/资源/状态查询。
2. 在锁内 API 或 cycle/attempt 事务重构前，先确认不会破坏 quote、receipt、recovery、审计和并发语义；SQL 数量下降但安全语义变弱视为失败。
3. 每次优化都以 batch-200 和 200/500/1000 调度矩阵做前后对照，分别记录 owner p95/p99、queries、writes、queue wait 和 oldest-due；不能只看总 SQL 数。

### P1：拆分调度并实施背压

1. 将人口滚动与日常维护到期扫描拆开；先完成 shadow/replay 和吞吐门槛，再启用 1～2 分钟扫描。
2. 为普通维护、日常招募和竞技场共享队列设置独立批量/全局吞吐上限；招募按 profile/pool/ordinal 错峰。
3. 观察事务 p95、锁等待、数据库 CPU、MySQL 连接、Redis 内存、队列 oldest-due、RSS 和 OOM/restart；积压时优先降低批量或暂停新增人口，不直接提高 hard cap。

### P0：完成 4GB 同机真实容量验收

在 Web、MySQL、Redis、Celery、Beat、Caddy 和真实请求/battle 基线同时运行的目标主机上，覆盖：

- 200/500/1000 个同刻 due profile，普通维护批次上限 200；
- Arena rearm、人口 demand scan、外部 reconciliation、worker 重启、claim takeover、慢查询和 Redis 短暂抖动；
- 连续 1h、6h、24h，记录每个容器/worker RSS 峰值、CPU、磁盘 I/O、连接数、Redis 内存、swap、OOM、重启、真实 queue wait 和 oldest-due。

计划放行条件：

- 无 OOM、容器反复重启、持续 swap 或连接耗尽；
- owner p95 小于所属扫描周期的 50%；
- oldest-due 不超过两个扫描周期，达到批次上限后 backlog 持续下降；
- 锁等待、重复/漏选、receipt/replay、故障回滚和 fail-closed 语义保持通过；
- queue wait 必须单独统计，不能用同步 owner 的 queue_wait=0 代替。

## 7. 实施顺序

1. [代码完成] 完成竞技场目标驱动 readiness，删除旧固定生命周期代码、adapter、旧字段/约束、旧执行入口和旧测试，保留安全边界并通过定向回归；真实补位等待窗口仍属于目标环境验收。
2. [代码完成] 冻结并落地 policy 2 的 16 槽字段、确定性间隔、领域完成来源和 23 小时上限；类型化预算/目标/并行约束、累计高价动作、基础尝试指标、完成 worker 反向 reconcile 与原因分类已落地；工资/资源和目标业务库校准仍待完成。
3. [代码完成] 拆分人口滚动和日常维护调度；due ordered scan、1000 hard cap、锁冲突退避、招募错峰、持久化 due hint 和基础指标已落地；目标库矩阵与全局吞吐验收仍待完成。
4. [代码完成] 实现独立三池招募配额、事件快照稀有度、正常银两、满编替换和 72 小时 runway 重算；目标业务库校准和运行窗口验收仍待完成。
5. [代码完成] 完成 exclusive stage aggregation、due 选取和索引/事务结构改造；目标 MySQL EXPLAIN、索引核对、同口径矩阵及必要的低风险重复读取/写入优化仍需逐项复测。
6. [外部待办] 在目标 4GB 主机完成 batch-200 下的 1h/6h/24h 验收和至少两轮 clean handoff；通过后才考虑调整 1000 上限或继续放宽吞吐。

## 8. 发布前检查

- 业务规则、policy 2 checksum/release snapshot、模型约束、迁移、候选/执行回归和 evidence source bundle 一致。
- Gate A、D1、E 通过，evidence verifier 通过，且 clean worktree 上重新生成 artifact；dirty worktree evidence 不能作为发布凭证。
- 目标业务库 preflight 为 ok，所有 required migration 已应用；0173 的旧竞技场字段下线需与本策略代码一并发布，不保留运行时兼容读取。
- 本轮策略发布必须应用 gameplay 0171～0177 与 guests 0071，并核对 `BotMaintenanceCycle` 的 due/status/latest 索引和累计高价动作回填、`BotMaintenanceAttempt` 的类型/trigger 维度索引、`BotMaintenanceRecovery` 的 entity correlation 索引、`BotProfile` 的 due identity 与 recruitment due 索引、`BotMaintenanceCompletionEvent` 的待处理索引、`GuestRecruitment` 虚拟来源/配额索引、旧 open cycle 的 seed/类型/预算快照回填行为和竞技场准入高水位固化结果。
- 建筑/科技/训练/招募完成任务必须保留完成事件写入；维护扫描必须保留待处理事件兜底，对账任务只重算状态和唤醒周期，不直接递归创建新的培养动作。
- 战力公式发布必须核对 `GUEST.AGILITY_TO_ARENA_POWER_WEIGHT=1.0`、真人快照、虚拟 lineup、参考快照和 Gate D2 使用同一敏捷口径；旧公式生成的 frozen power 不得直接与新公式比较。
- 目标 4GB 主机的长时资源和队列证据满足第 6 节门槛。
- Gate D2 的旧静态 artifact 仅作历史审计，不参与当前战力放行；当前运行时和新快照必须含敏捷，历史缺敏捷快照必须带 `legacy_missing_agility` 标记。
- 提交、生产切换、worker 扩容、目标数据库迁移和 retention 清理仍是独立发布动作，不由本审计文档自动授权；本轮未执行 git commit/push 或生产切换。

## 9. 2026-08-10 全面复查补充待办

本节是本轮全面复查的唯一增量清单。每项均同时检查运行时边界、数据一致性、并发/幂等、迁移、可观测性和发布证据；`[~]` 表示代码或结构已完成，但仍缺目标环境、业务口径或 clean artifact，不能按生产完成理解。

### P0：发布阻断与数据安全

- [x] **preflight 覆盖完整迁移闭包**：以 `(app, migration)` 统一声明并检查 gameplay 0167～0177 与 guests 0071；缺列、缺完成事件表、缺虚拟招募约束或缺 attempt trigger 索引时，V2 不得激活。source/governance 回归覆盖完整清单、跨 app 记录和 `app.name` 诊断。
- [~] **Gate D1/E 与 evidence source manifest 覆盖新增 owner**：runtime owner、canonical suite、Makefile 合约和 source manifest 已补齐类型节奏、业务指标、完成对账、普通招募、竞技场引用、0176/0177/0071 与对应测试；2026-08-11 artifact 已由 recorder 重新生成并通过其内置 verifier（Gate A `181`、Gate D1 `403`、Gate E contract `859`、Gate E real `38`），但 source_state 仍是 dirty，独立 clean-source verifier 明确拒绝，clean handoff 仍需按同一命令重录，不能手工改数字。
- [x] **高价动作上限按提交动作次数计数**：`covered_action_kinds` 只负责覆盖去重；每个已应用的重复训练/建筑/科技/征募动作均递增 durable `high_cost_actions_used`，0176 以已应用 attempt 回填并由数据库约束保护；同类重复动作与回放均有回归。

### P1：运行正确性与边界治理

- [x] **完成对账继承 profile 类型**：旧 open cycle 缺少 `archetype_pacing` 时，completion worker 按 profile archetype 解析并补齐周期快照；已有周期优先使用冻结快照，配置热更新不会改变在途周期。
- [x] **普通招募 due 选择无队头阻塞**：使用持久化 `next_recruitment_at`、复合 due 索引和有序 bounded scan；资源/工资/队列延期推进 retry due，已知到期行优先于未初始化的 NULL hint，后续 due profile 不再被前部 profile 截断。
- [x] **业务指标冻结语义和触发维度**：工资 runway 作为每次 attempt 快照，聚合提供 min/avg/max；按 `trigger` 分组/过滤，普通维护、竞技场和 healing 不再混合；触发维度索引与 SQL 聚合回归已覆盖。
- [x] **竞技场审计统一走 durable attempt normalizer**：growth、healing parent/child 统一写原因分类、银粮字段、runway 和类型快照；`INELIGIBLE` 有终态；移除动态 `BotMaintenanceAttempt.Trigger` 探测，并保留 operation identity 竞争/幂等语义。
- [x] **类型预算明确为周期累计预算**：周期开始冻结 spendable 基线，后续按累计 committed cost 扣减剩余额度；预算状态写入 cycle payload，跨槽位、重试和 worker 重启复用同一状态。
- [x] **类型目标为显式优先级且配置可校验**：building/technology targets 在候选排序中优先于全目录 focus；未知 target key、重复 key、非法比例和非法 pool 在加载/快照构造阶段 fail-closed。
- [x] **公开批量入口有硬上限**：completion reconcile、普通维护和普通招募均 clamp caller limit；不会因一次外部调用无限 materialize event 或扫描对象。

### P2：迁移、容量与运营闭环

- [x] **0173 数据迁移已分块写入**：竞技场准入高水位冻结使用 bounded chunk 与明确批次写入，空数据/已有数据/历史 schema 状态均有回归；目标库执行仍需在发布窗口复核耗时与锁等待。
- [~] **durable audit/cycle/completion/recovery retention**：代码保留原始审计和重放所需字段，尚未在本轮擅自删除数据；仍需由运营确定 raw 明细、日聚合、归档 owner、失败重放窗口和索引容量阈值。任何实际删除必须另行确认并在维护窗口执行。
- [~] **due SQL 优化补目标库证据**：本地已验证无窗口函数、aggregate + ordered bounded scan、复合索引和 bounded limit；仍需在目标 MySQL 录 EXPLAIN 与 batch 200/500/1000 的 p95/p99、SQL、writes、lock wait、queue wait、oldest-due 矩阵。
- [~] **生命周期 persona 与全量业务验收**：五类 archetype 的间隔、预算、目标、并行训练和招募权重已类型化；游客/休闲/长期/老玩家 persona 对完成度、门客数、核心阵容战力、建筑/科技完成、24/48/72 小时 runway、重复尝试、周期覆盖和 arena time-to-ready 的业务口径仍需确认与录数。
- [x] **竞技场残余兼容语义收窄**：公共增长回执只接受 V2 schema-3；durable healing sweep 必须带 member/round 上下文，无上下文 direct healing call fail-closed。schema-2 是未 claim 的持久状态哨兵，schema-1 只由 preflight 阻断并交给运维处理，不是可执行的旧兼容路径。
- [x] **类型配置与历史战力快照语义统一**：运行时默认由 typed pacing 提供，YAML 只作覆盖；在途周期保存 pacing/预算快照；运行时、lineup、参考快照和 projection 持续纳入敏捷，历史缺敏捷快照显式写入 `legacy_missing_agility` 且不与新公式静默混比。
- [~] **竞技场等待窗口与容量闭环**：代码已记录 demand/member/round/action/selected-power/影子成本和未达标原因的 durable 维度；仍需目标环境录 time-to-ready、critical/preferred 达成时间、溢出比例、队列 oldest-due、RSS/OOM 和 1h/6h/24h 长时数据。

### 本节完成定义与剩余决策

1. P0 的实现与本地回归已完成；新的 Gate artifact 仍必须在 clean source 和目标隔离服务中生成，历史/dirty artifact 不得冒充发布凭证。
2. P1 的代码、并发/幂等边界和定向回归已完成，当前版本可标记为 release candidate；不等于已切生产。
3. P2 的目标 MySQL、4GB 长时容量、retention 执行、业务指标校准和竞技场等待窗口属于外部环境验收。
4. 本文所有数字、测试数量、迁移版本和 source manifest 以最终验证命令为准；不得沿用旧 Gate 快照的通过数量。

当前不需要老蹬儿为代码路线做额外决策。正式生产发布前仍需要明确三项外部授权：是否允许 clean worktree 后执行 commit/push 与部署；目标 MySQL/Redis/4GB 验收窗口和凭据；以及 retention 清理、persona/业务阈值的运营确认。大蜜薯酱本轮只完成代码、文档和本地 release-candidate 验证，未执行上述外部或破坏性操作。

## 10. 2026-08-11 工作区全面复查与 Gate artifact 复核

### 10.1 本轮发现并修复

- **完成对账跨周期归属**：按待处理动作的真实 owner 优先匹配 locked cycle，只有缺少 owner 时才按受约束的最新 submitted cycle 回退；避免旧周期完成事件被错误推进到新周期。
- **测试确定性与扫描边界**：普通招募扫描测试改用实际 schedule due slot；维护任务的 owner limit、完成对账 cap 和招募 cap 分离，`None` 使用安全默认上限，不允许外部调用无限扫描。
- **重试语义收口**：普通 V2 已提交训练在领域完成可见后，下一次计划正确收束为 `NO_ACTION/no_eligible_candidate`，不伪造 Arena receipt、不重复启动同一训练。
- **结构性 SQL/事务优化**：全局稀有库存计数折叠进模板查询；领域可用性合并为单次 `UNION ALL`；cycle/profile 锁定复用同一已锁 cycle，周期结果使用 join、终态字段合并写入；库存模板与仓库物品使用一次带 `LEFT JOIN` 的锁定查询；日稀有库存正常路径使用条件原子 `UPDATE`，耗尽/缺行才回退锁内补偿路径。
- **战力与 Arena 边界**：门客敏捷继续按 `1:1` 纳入 lineup power；旧 Arena 固定生命周期和运行时兼容入口保持物理删除，schema-1 只作为 fail-closed 历史数据检查。

### 10.2 验证结果

- canonical 静态检查通过：Black、isort、flake8、全量 mypy（733 个 source files）、JavaScript syntax/tests、Django check、makemigrations dry-run、compileall 与 `git diff --check` 均通过。
- Gate A：`181 passed`（169 contract + 12 real-service）；Gate D1：`403 passed`。
- Gate E：contract `859 passed`；隔离 MySQL/Redis real-service `38 passed`，六个 batch/concurrency 单元格全部通过；最大批次保留 `queries_max=2435`、`write_queries_max=1002`、deadlock/lock-timeout/lock-wait 均为零；阶段指标共六阶段，exclusive duration 明确不把嵌套阶段重复求和。
- recorder 内置最终 evidence verifier：`467 passed`，无失败；此前暴露的 stage scope 文案契约已通过源头修复后随完整 recorder 重录验证。
- 独立 `record_virtual_player_evidence.py --verify`：按设计拒绝当前 artifact，原因是 `source_state.worktree_clean=false`；这验证了 clean-source 发布门槛仍然生效，不是业务测试失败。
- 当前 artifact：
  - [Gate manifest](/home/daniel/code/web_game_v5/docs/virtual_player_gate_evidence_manifest_2026-08-11.yaml)
  - [Gate D1 evidence](/home/daniel/code/web_game_v5/docs/virtual_player_gate_d1_evidence_2026-08-11.yaml)
  - [Gate E readiness evidence](/home/daniel/code/web_game_v5/docs/virtual_player_gate_e_readiness_evidence_2026-08-11.yaml)

### 10.3 放行边界与剩余待办

本轮 artifact 的 `git_commit` 为 `648488b183318ca85625efa763bcf9257ddddc28`，`worktree_clean=false`，且 artifact 明确标记为 development/non-production、未执行 gate exit、runtime activation、commit、push 或生产切换。因此本轮结论是“代码与隔离门禁通过的 release candidate”，不是生产已发布。

仍未闭合的项目保持为外部验收：目标业务库 migration/preflight 与 EXPLAIN、类型化节奏和 24/48/72 小时业务指标校准、Arena 真实 time-to-ready/补位窗口、4GB 同机 1h/6h/24h RSS/OOM/swap/重启/队列证据，以及 retention 策略的运营确认。上述项目不应通过修改 artifact 数字或放宽测试阈值来标记完成。
