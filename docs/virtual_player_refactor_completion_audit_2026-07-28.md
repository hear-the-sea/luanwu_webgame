# 虚拟玩家重构完成度审计与问题解决清单（2026-07-28）

## 1. 审计口径

本清单以当前工作树为事实来源，对照以下规范逐项判断完成度：

- `virtual_player_refactor_plan_2026-07-27.md`
- `virtual_player_gate_a_acceptance_config_2026-07-27.yaml`
- `virtual_player_gate_a_dossier_2026-07-27.md`

状态含义固定如下：

| 状态 | 含义 |
|------|------|
| `DONE` | 当前源码和相应验证均能直接证明要求已经满足 |
| `PARTIAL` | 已有可执行纵向切片及对应验证，但该行更广的领域覆盖或运行接线尚未闭合 |
| `PURE_ONLY` | 纯契约、解析或决策逻辑已完成，但没有持久化执行器或运行接线 |
| `PENDING_EVIDENCE` | 实现或历史证据存在，但当前要求的 canonical/真实服务证据尚未执行 |
| `NOT_IMPLEMENTED` | 当前源码直接证明要求尚未实现 |
| `INTENTIONALLY_OFF` | 可选能力按设计保持关闭，不应被误判为失败或偷偷启用 |
| `AUTHORIZED_IN_PROGRESS` | 实现或隔离验证已获明确授权，当前尚未完成，不能误报为 `DONE` |
| `REQUIRES_CONFIRMATION` | 涉及 schema、数据库生命周期、数据重建或 V2 运行时启用，必须先取得明确确认 |

`DONE` 只描述对应行，不自动代表整个 Gate 已退出。Gate 退出必须同时满足该 Gate 的全部实现和证据条件。

## 2. Refactor audit 结论

按 `refactor-audit` 的边界治理口径复核当前源码后，没有发现尚未处理的 High 或 Medium 级重构问题：

- `selectors`、reference reader、Arena protection 与候选规划保持只读，AST/DML 门禁没有发现隐藏写入。
- `profile_store.py` 继续是 `BotProfile` 唯一写 owner；Maintenance 事务保持 `BotProfile -> Manor -> domain aggregate` 锁序，领域写由 training、equipment、building、technology、health、skills、troop 与 inventory owner 执行。
- task、Admin、Arena 和 Raid 只负责 transport/编排，不重新实现业务规则；基础设施失败在 provider/adapter 边界转换，领域异常没有被 broad catch 静默吞掉。
- routing guard 与 grain template 优化只消除数据库往返，不缓存 routing 决策、库存数量或锁定对象；guard 漂移仍回退 canonical reader 并 fail closed。

`Boundary governance`：当前约束成立。`Refactor plan`：保留现有 owner，不再做结构性拆分；D2 证据与 cutover 属于运营/数据门禁，不是继续移动代码的理由。`Scope assessment`：整体仍是 `Architecture Migration`，本轮 N+1 与 D2 verifier 修复属于 `Surgical Fix`。`Residual risk`：只剩未授权的 Gate D1/E 状态切换、测试数据处理、可选 Gate D2 的获批代表性真人 artifact/report，以及上线前独立评审的生产签名方案；这些均未执行。

## 3. Gate A-E 完成度矩阵

### Gate A：基线、边界与可执行契约

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| 公共 facade、reader、DML owner 与 Arena owner 冻结 | `DONE` | `tests/test_virtual_player_architecture_gate.py`；`BotProfile` 运行时 DML 仅允许 `profile_store.py` |
| H-01 Raid 提交后退休边界 | `DONE` | post-commit at-most-once 边界、嵌套事务竞态与真实 MySQL/Redis 回归已通过；最新 manifest 已记录一次 `158 passed` canonical execution |
| Maintenance 三 trigger、五 outcome、sequence 和调度矩阵 | `DONE` | `contracts.py` 与 `tests/test_virtual_player_maintenance_contracts.py` |
| `APPLIED`/其他 outcome payload 互斥 | `DONE` | `action_kind` 与 `reason` 构造时 fail closed |
| `MAX_NO_ACTION_LEASE_AGE = 12h` 绝对期限 | `DONE` | `virtual_reserve_pool.py` 使用 member `created_at`，retry 不重置 deadline |
| 八档成长、强度档位、对账/人口 demand/退役/routing/safety 阈值机器可读 | `DONE` | acceptance YAML schema version 13 及契约测试；未把契约存在误报为 runtime 已实现 |
| Gate A manifest 的 suite selection、count 与 checksum | `DONE` | canonical collection 固定 158 个唯一 nodeid，SHA-256 为 `60a18508f075f4a2d7d98477cba6cacdfbc92077f861df0f7c2313da74f4123c`；manifest 已记录 `158 passed` |
| canonical Gate A 完整执行 | `DONE` | 2026-07-29T00:04:08Z 完成：contract `146 passed in 12.35s`，真实服务 `12 passed in 100.40s`，合计 `158 passed` |

结论：Gate A 契约、collection 和最后代码状态下的 canonical 真实服务证据均已闭合。

### Gate B：行为等价架构提取

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| `virtual_players.py` 和 `virtual_player_rules.py` 为无实现 facade | `DONE` | 仓内生产消费者已迁到真实 owner；AST 契约通过 |
| `virtual_player_core` 按 config/identity/lifecycle/legacy/bootstrap/maintenance 等 owner 拆分 | `DONE` | owner 文件存在，纯规则 owner 无 Django/model import |
| `profile_store.py` 为唯一 `BotProfile` 写 owner | `DONE` | 完整 manager/QuerySet/instance/bulk/upsert/delete 负例门禁通过 |
| Arena demand/pool/fill/reconcile/scan/lineup/protection 拆分 | `DONE` | owner 模块存在；内部 import 图无环，反向 lifecycle edge 为空 |
| task/Admin/map/loot 等调用迁移到真实 owner | `DONE` | facade 生产 import 集为空；Admin 已调用 profile store command |
| 行为等价数据库回归和查询数对比 | `DONE` | 装备相关 `182 passed`、领域 primitive `30 passed`、最终相邻真实 MySQL 回归 `43 passed`；Gate E 百档查询预算也已通过 |

结论：Gate B 的 owner 迁移、依赖图和数据库行为证据均已闭合；没有继续拆文件的收益依据。

### Gate C：策略模型、持久化和关闭状态下的能力

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| canonical RNG、稳定子流和 policy bucket | `DONE` | `random_context.py` 冻结 SHA-256 向量；RNG/engine/plan/policy identity 已持久化，入组与 repair 使用完整 identity CAS |
| `BotDevelopmentPlan` 严格解析、序列化、checksum 和显式 schema upgrade | `DONE` | `strategy.py`、profile enrollment、plan repair 与七字段陈旧 identity 负例均有回归 |
| V2 配置严格解析、checksum、八档与 routing 单向迁移 | `DONE` | validator/config、运行 YAML 和持久 routing owner 已接通；仓库输入仍保持 `policy_rollout.enabled=false`，没有执行实际切流 |
| 强度预算严格有界 parser | `DONE` | 最多 4 条、UTC、排序、未来偏差、24h 裁剪与消费契约已有测试；字段已持久化，领域写原子消费仍属于 Gate E |
| `BotProfile` 11 个 V2 字段 | `DONE` | `gameplay/models/bots.py` 与 `0139_botprofile_v2_fields.py` 已增加全部字段，V1 默认值保持兼容 |
| V2 `CheckConstraint` 和经 EXPLAIN 决定的索引 | `DONE` | V2 必填约束已实现；入组/policy 分批走主键 range。Maintenance 固定 100 档真实基准在现有索引下满足耗时、查询量与锁等待阈值，因此不增加未经收益证明的候选索引 |
| 不可变 `BotPolicyRelease` | `DONE` | 并发幂等发布、checksum、只读 Admin、profile/routing/rollout 引用门禁与 720h 单调延长均已实现；rollout 当前事实由 `BotRuntimeRoutingState` 持久拥有，目标更换或停用在同一 transition 中延长旧 policy deadline |
| `BotExternalStrengthReconciliation` Gate C 持久壳与受控重排 | `DONE` | `0139` schema/约束、phase-aware expected-current requeue、字段白名单和提交后审计日志已完成；Gate E 两阶段执行能力已另在下文独立验收，不以本行 schema 结论代替 |
| `BotRuntimeRoutingState` Gate C routing owner | `DONE` | 持久单例、revision/current-mode CAS、缺失状态 fail-closed、calibration policy 引用校验和 routing 行锁竞态已有测试；safety window consumer 仍属于 Gate E |
| additive `0139`/`0141` migration | `DONE` | migration 只增加 schema/default；`0141_bot_runtime_policy_rollout.py` 默认关闭，不创建 routing 行、不发布 policy、不分配档案；`makemigrations --check --dry-run` 无漂移 |
| 发布、退役、入组、重分段、RNG/plan 修复、policy upgrade、对账重排、routing 与 rollout transition 命令 | `DONE` | 命令默认 dry-run，application service 拥有事务与 ORM；rollout 使用完整 expected-current + revision CAS、`routing -> policy -> profile` 锁序和稳定 `(profile_id, target_policy_version)` bucket；降低比例不自动回退 |

结论：Gate C 的 additive schema、application service、命令、routing/policy owner 与关闭态能力已闭合；没有执行发布、入组、重分段或 routing 切换。

### Gate D1：Bootstrap V2 与人口事件

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| 八档 Bootstrap 历史年龄与零/稀疏/有限/充分样本规则 | `DONE` | 生产 planner 已批量读取同地区、同段真人 cohort；`tests/test_virtual_player_reference_snapshots_v2.py` 覆盖 `0/1/4/5/29/30` 边界、本地优先及仅 0 本地样本借全局，`tests/test_virtual_player_bootstrap_v2.py` 覆盖八档历史年龄和真实 ORM 物化 |
| `BotPopulationRecomputeDemand` 与 revision/token consumer | `DONE` | 模型、`0140` migration、merge/claim/token fencing/finalize/backoff/周期扫描及 durable cell 到真实 V2 ORM 图均已实现；consumer 在同一事务持有 routing、全局容量和 cell 缺口并签发一次性 permit。真实 MySQL 已证明同键 merge 保留两个 revision、双 worker 单缺口只物化一个档案；只读核验确认隔离 `test_webgame` 有且仅有一条 `0140` migration 记录，测试后 demand 表为 0 行 |
| 注册提交后按地区合并的专用人口任务 | `DONE` | Manor 成功后 `on_commit` 合并 demand 并投递专用 task；Bot/staff/fixture 排除，投递丢失可由周期扫描恢复 |
| 专用人口任务不运行 Maintenance | `DONE` | `gameplay.tasks.virtual_players` 的人口 task 仅调用单 cell consumer，回归测试明确阻止 Maintenance 调用 |
| 八档 runtime schema 原子切换 | `DONE` | 持久 routing 支持 `legacy_before_gate / v2_active / v2_paused` 单向迁移；人口 create/reactivate/hourly roll 按 mode 分派且 V2 粘性禁止回退 V1，实际切流仍未授权 |
| V2 `BootstrapBlueprint` 只读计划与锁内物化 | `DONE` | Blueprint 深度不可变并完整表达建筑、科技、分层门客、家丁、装备、技能、护院、库存、资源及历史偏移；阶段 A 零 DML，阶段 B 使用 V2 专用 materializer，锁内重验真人 cap、catalog digest、容量/资格/槽位/稀有度/科技前置和库存额度，实际 ORM 强度越界整体回滚。无 permit、consumer 外同步创建/复活和提交前 ownership/routing 丢失均 fail closed |
| 真人和 Bot 自然跨段后的旧/新段人口重算 | `DONE` | 真人 post-commit handoff 与 Bot profile 同步后 handoff 均使用同一 durable demand；测试覆盖回滚、连续合并、PVP、幂等与周期恢复 |
| 存量按真实声望幂等重分段命令 | `DONE` | 命令支持 dry-run/batch/resume；八档边界、二次 no-op 及不改变资产/声望/engine/lifecycle 已验证 |
| 高段三类激活信号 | `DONE` | 真人活跃、地图搜索和 active Arena 缺口都会进入 population target；Arena 同时持久合并对应 `(region, prestige_band)` recompute cell 并提交后唤醒 consumer。回归证明参考真人超出 30 天活跃窗口时，Arena 信号本身仍能抬高空高段 target |
| Gate D1 固定 fixture、经济、回滚和性能门禁 | `DONE` | `virtual_player_gate_d1_evidence_2026-07-28.yaml` 固定精确源码摘要与 suite checksum；定向契约 `297 passed`，核心 MySQL/Redis `9 passed`，相邻真实竞态 `8 passed`。名称首次唯一冲突复用原 Blueprint，坐标首次冲突仅物化一次资产，五次耗尽恢复完整持久图；库存 cap loser 同事务回滚。预热 5 次、测量 30 次的 planning/materialization P95 为 `98.739ms / 165.264ms`，低于 `250ms / 2000ms`；`SQL<=80` 与 `write<=25` 契约通过 |
| 参考样本不足时继续 conservative cold start | `DONE` | 一般 V2 Bootstrap consumer 已接入且 0 样本不会回退 V1；这不代表 Gate D2 的代表性分布证据已经通过 |

结论：Gate D1 的实现和要求内证据已闭合，当前为 `READY_FOR_GATE_EXIT_REVIEW`，但尚未执行 Gate exit，也没有取得 Bootstrap routing 切换授权。

### Gate D2：可选参考分布校准

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| 三元组 calibration evaluator | `DONE` | `calibration.py` 的完整指标/verdict 已由生产 acceptance workflow 调用；最小样本数和全部分布阈值均从不可变 `BotPolicyRelease.payload.reference_calibration_thresholds` 读取并受 policy checksum 绑定，运行 YAML 只允许等于或严于 Gate A 基线 |
| 代表性匿名真人 artifact/candidate report | `PENDING_EVIDENCE` | artifact/report 严格 schema 与 verifier 已实现，但当前 YAML catalog 为 `{}`，仓内没有获批代表性真人 artifact/candidate report，因此没有真实 passed unit |
| candidate metrics 正确性与 generator provenance | `DONE` | artifact schema v2 的四类匿名原始 cohort 绑定 generator/engine/RNG/plan、seed、catalog/cohort digest 和源码 manifest；非生产外部生成器使用仅由 runtime secret settings 信任的 HMAC attestation，默认无密钥且 artifact/report/catalog 不得自带密钥；workflow 以 metric algorithm v2 独立重算全部指标，report schema v3 必须逐字段相等，旧算法 artifact、派生 claim、同步伪造 artifact/report/catalog、原始硬约束违规和源码漂移负例均 fail closed |
| 稳健联合异常率 | `DONE` | metric algorithm v2 使用 12 维联合向量；按稳定 business key 确定性拆分 `80% fit / 20% holdout`，只用 fit 的 median/IQR、留一最近邻距离和 MAD 阈值建模，holdout 不参与拟合；测试证明边际分布完全相同但相关结构反转仍能阻断 |
| calibration route 数据契约、持久化与 revision CAS | `DONE` | 调用方只提交三元组；持久 route 额外保存 policy/snapshot/evidence 四项 proof，严格解析、canonical 排序、policy 引用/退役保护和 revision CAS 已实现 |
| snapshot catalog、artifact loader 与 report 完整性 workflow | `DONE` | V2 config 已有严格 `reference_snapshot_catalog` 与按 policy/band 登记的 evidence digest；snapshot/report/artifact loader 限制路径、字节数、深度、节点数和字段，workflow 绑定 policy checksum、snapshot/report/artifact digest、schema 和重算算法版本 |
| `(policy_version, snapshot_version, band)` fail-closed 独立 transition | `DONE` | CLI 不再接收自报 approved 集合；证据 I/O 在 routing 行锁前完成，锁内复验不可变 policy release 与 revision CAS；失败不写 routing。只有 candidate correctness 缺口闭合并取得真实证据后，才能把该机制称为可信 acceptance |
| Bootstrap calibrated consumer | `DONE` | 只读 resolver 复验持久 proof、当前 config/catalog 和冻结 snapshot，Bootstrap 使用冻结 cohort anchor；route 删除及 policy/snapshot/evidence proof 或 artifact 漂移时，新计划 cold start，在途 calibrated plan 在物化前零 DML 拒绝 |
| Maintenance calibrated consumer | `DONE` | V2 planner/executor 的全部同步动作共用持久 routing proof、冻结 reference 和锁内重验；catalog/route 仍为空，因此本行只证明 consumer 完成，不宣称 D2 代表性证据通过 |
| 无代表性真人样本时保持关闭 | `INTENTIONALLY_OFF` | D2 是可选门禁，不得阻塞 D1 或 E，也不得用 fixture 冒充真人数据 |

结论：Gate D2 的 evaluator、content-addressed routing、Bootstrap/Maintenance consumer、原始 artifact 指标重算和 provenance 均已实现；但 catalog 为空且没有获批代表性真人证据，所以该可选能力继续保持 `INTENTIONALLY_OFF`。

### Gate E：Maintenance V2、对账和安全暂停

| 要求 | 状态 | 当前证据或缺口 |
|------|------|----------------|
| 五 outcome、三 trigger 和 schedule disposition | `DONE` | scheduled、Arena 与 Admin 共用 `MaintenanceResult`；Admin 必须显式选择 due 与 schedule 语义，Arena 精确保留正常调度且五 outcome 均有稳定映射 |
| 八档 spacing/action cap、24h budget、跨段严格限制 | `DONE` | 所有正向动作共用 `maintenance_rules.py`，来源/目标/sample/domain 取最严格值；最多跨一段，Arena/Admin 无旁路 |
| 每周期最多一个同步领域动作 intent | `DONE` | planner 在培养、治疗、装备、技能、库存获取、护院、建筑与科技中冻结一个 intent；executor 只调用对应 actor-neutral locked primitive 一次，不接入门客招募链或长期倒计时 |
| profile 锁内重验、sequence、预算和领域写同事务 | `DONE` | executor 在 `BotProfile -> Manor -> domain aggregate` 锁序下重建计划，领域写、资源/工资、强度与库存预算、sequence、schedule 原子提交；失败注入证明完整回滚 |
| Arena/Admin 复用同一 V2 maintenance 边界 | `DONE` | scheduled、Arena execution receipt 与 CSRF/权限保护的 Admin POST 都调用同一 V2 boundary，V2 错误不回退 V1 |
| 强制结算日预算 | `DONE` | planner 复用 `resources.py` 的只读离线生产投影冻结请求量、UTC 日预算和工资 quote；executor 在 Profile/Manor 锁内钳制正向银两/粮食、保留负向耗粮、推进生产时间并优先支付工资。预算、`ResourceEvent`、`SalaryPayment`、训练和周期元数据同事务提交，跨 UTC 日、Arena 调度保留及失败回滚已有回归 |
| 外部强度/声望结果异步对账 | `DONE` | Raid 双方在原事务内以提交前强度锚点创建幂等 intent；profile/population 两阶段拥有 5 分钟 lease、token fencing、各 12 次上限、退避/quarantine、同档案严格顺序和原子人口 handoff；Celery 单 intent worker、分钟扫描与 timer queue 已接线，Arena 排除未解决及隔离档案。SQLite 定向回归和真实 MySQL 四项锁语义证据均通过 |
| 已完成外部对账后的 Arena 真人 cap 动态保护 | `DONE` | `virtual_protection` 对 unresolved/quarantined 和 `APPLIED` 后实时超 cap 都 fail closed；scan、lease 前锁内复验与 member reevaluation 覆盖漂移且保持只读保护边界 |
| safety monitor、闭合窗口指标和 routing CAS | `DONE` | `BotSafetyMetricEvent/Window`、幂等事件、5 分钟宽限、UTC 窗口、heartbeat、35/90 天保留、preflight、monitor 与 routing revision CAS 已接线；SQLite 套件及真实 MySQL finalizer/event writer 串行化通过 |
| 虚拟监牢日清与竞技场满血 snapshot | `DONE` | 日任务按 cutoff 有界批量释放虚拟 captor 的 `HELD` 记录且 routing 独立；普通赛/共斗只复制满血虚拟 snapshot，不修改真实 Guest，锁内写前再校验 |
| `v2_cutover -> V1 清零 -> v2_active` workflow | `REQUIRES_CONFIRMATION` | workflow、精确 V1 count 与 CAS 前置条件已实现；实际数据处理、cutover、清零与启用未授权、未执行，这正是 Gate exit 与 readiness 的边界 |
| disposable database benchmark、锁等待和失败注入 | `DONE` | 六档 `batch_size=1/10/100 x concurrency=1/2` 全部通过；百档 queries 为 `2422/2418`，writes 为 `712/709`，deadlock/lock timeout 为 0；其余真实竞态与故障注入 `43 passed` |

结论：Gate E 实现与 readiness 证据已闭合，状态为 `READY_FOR_CUTOVER_CONFIRMATION`。Gate E 尚未退出：没有处理现有 V1 测试数据，没有进入 `v2_cutover`，没有验证环境内 V1 清零，也没有启用 `v2_active`。readiness 证据见 `virtual_player_gate_e_readiness_evidence_2026-07-28.yaml`。

## 4. 设计问题及解决清单

| # | 发现的问题 | 已确定的解决方案 | 状态 |
|---|------------|------------------|------|
| 1 | 历史测试结果可能被误当成新 canonical Gate A 结果 | 独立 manifest 固定环境、文件集合、nodeid count/checksum、采集时间和诚实执行状态 | `DONE` |
| 2 | outcome、sequence 和 trigger 调度语义容易互相推断并产生非法组合 | `MaintenanceResult` 构造时验证完整 5x3 矩阵；只有 `APPLIED/NO_ACTION` 提交周期 | `DONE` |
| 3 | `APPLIED` 与失败/no-op payload 可同时为空或同时存在 | `APPLIED` 仅允许非空 `action_kind`；其他 outcome 仅允许非空 `reason` | `DONE` |
| 4 | Admin advance 若强制晚于旧远期 deadline，会阻止显式重新调度 | Admin 只要求新 deadline 非空且不同；scheduled committed 仍必须严格后移 | `DONE` |
| 5 | Bootstrap 历史年龄若接受 float/bool，会破坏离散天数和稳定 RNG | validator 与领域 parser 统一为非负整数区间，checksum 负例先重算以排除旁路 | `DONE` |
| 6 | 多个 Maintenance 拒绝原因顺序不稳定，监控只能猜主因 | 冻结 `domain_constraint -> strength_cap -> band_spacing -> band_action_cap -> multi_band_transition`，同时保留完整原因列表 | `DONE` |
| 7 | 跨段动作可能只检查来源段、遗漏目标 cap，或成功后仍用来源 cadence | 校验来源/目标/sample/domain 最严格限制；最多跨一段；成功后按目标段 cadence 调度 | `DONE` |
| 8 | `NO_ACTION` 可无限占用 Arena active capacity | 使用既有 member `created_at + 12h` 绝对期限，过期转 `EXHAUSTED`，retry/version 不重置 | `DONE` |
| 9 | 巨型 service 与 facade 让调用方依赖私有实现，读取路径可能夹带写入 | 提取真实 owner、建立薄 facade、只读 owner 和完整 DML AST 门禁 | `DONE` |
| 10 | 测试环境若预留 engine enrollment 百分比灰度，会形成无用途的双重路由状态 | engine 使用单向 mode 状态机；独立 policy rollout 只在既有 V2 档案间按稳定 bucket 分配版本，不改变 engine routing | `DONE` |
| 11 | 热 YAML 若直接作为已发布策略或 rollout 当前事实，会允许漂移且无法可靠退役 | 不可变 `BotPolicyRelease` 加持久 `BotRuntimeRoutingState` rollout owner；显式 transition 对旧 target 同事务单调延长 720h | `DONE` |
| 12 | 注册人口补足与定时 Maintenance 绑在同一 task，既不及时也扩大副作用 | 公共真人注册提交后投递专用幂等人口任务；定时扫描只作 demand 恢复兜底 | `DONE` |
| 13 | 样本不足若停止创建，会导致空高段或新环境永久无供给 | D1 一般 V2 consumer 使用每段 conservative fixture，0 样本继续创建且不回退 V1；真人 cohort 与物化前最新 cap 已接线并有 `0/1/4/5/29/30` 回归 | `DONE` |
| 14 | V2 JSON 损坏时回退 V1 会改变粘性执行器并掩盖数据错误 | engine=2 粘性字段、显式 RNG/plan/policy 修复和 expected-current 门禁已实现；Maintenance V2 对损坏 plan/RNG/policy identity 返回 `PAUSED` 且不回退 V1，已有零领域写回归 | `DONE` |
| 15 | PVP 等外部结果不能被 Bot 策略回滚，但会绕开 24h 预算和分段状态 | Raid 双方同事务 intent、两阶段 profile/人口对账、`last_strength_increase_at` 单调推进及 `APPLIED` 后实时 Arena cap 保护均已实现 | `DONE` |
| 16 | schema migration 若顺便发布 YAML、入组或重建数据，无法独立回滚和证明 | `0139` 只加字段/表/约束/default；发布、入组、重分段分别使用默认 dry-run application command | `DONE` |
| 17 | readiness 通过若直接启用 Maintenance，会让仍可复活的 V1 档案混跑 | routing CAS 已强制 `v2_cutover -> runtime-eligible V1 count=0 -> v2_active`，`retired` 计入清零；实现已完成，实际数据处理与启用仍需单独确认 | `REQUIRES_CONFIRMATION` |
| 18 | 纯单测绿色容易被误报为 Gate C-E 已完成 | 本清单把 pure contract、ORM executor、runtime wiring、真实服务证据分层记录 | `DONE` |
| 19 | 外部对账只有 `PENDING/CLAIMED/APPLIED` 却声称有界 retry，坏 payload 会无限重试，过期旧 worker 也可能在新 claim 后重复提交 | profile/population 分阶段 claim、5 分钟 lease、随机 token fencing、各 12 次上限、60 秒起始且最多 6 小时退避、永久错误/耗尽 quarantine 已实现；真实 MySQL 证明过期新 token 可 reclaim，旧 token finalize 返回 `claim_lost` 且不能覆盖新 claim | `DONE` |
| 20 | 同档案 intent 可乱序，且 profile 已对账后到人口重算之间没有可恢复、独立 fencing 的中间态 | worker 按 `origin_committed_at -> id` 拒绝越序；跨段 profile 完成进入独立 `PENDING_POPULATION`，人口 demand 与 `APPLIED` 同事务提交。真实 MySQL 证明扫描器跳过被锁 intent 时不会选择同档案后项，只会领取独立档案工作 | `DONE` |
| 21 | policy 退役只有“观察与重放窗口已结束”布尔条件，没有时长或事实来源，命令无法可靠判断 | 发布、profile/routing/rollout 最后引用移除和数据库 UTC 退役门禁均已实现，deadline 只允许单调延长 | `DONE` |
| 22 | safety 需要 UTC 闭合窗口、迟到宽限、event 去重和 heartbeat，但现有 Redis/cache counter 是累计值且会进程内降级 | `BotSafetyMetricEvent/BotSafetyMetricWindow` 持久 provider、5 分钟宽限、60s heartbeat、35/90 天保留、清理水位与 fail-closed preflight 已完整实现 | `DONE` |
| 23 | YAML mode 可热刷新，却又要求 safety monitor 对 routing 做 CAS；没有持久 revision owner 会形成双重真相和重启漂移 | `BotRuntimeRoutingState` 是 routing 唯一事实；monitor 消费闭合窗口并用 revision/cursor CAS 幂等暂停，冲突有界重读 | `DONE` |
| 24 | 五状态 `MaintenanceResult` 与 safety rate 引用第六个 `FAILED` 互相矛盾 | 领域仍为五状态；`FAILED` 只存在于 operation/attempt 观测事件，提交不确定另记 hard violation，指标故障不反写业务结果 | `DONE` |
| 25 | 设计要求 population handoff 持久合并，但并发 merge 可能被旧 claim finalize 吞掉 | `BotPopulationRecomputeDemand` 已以 request/completion revision、claim revision + token fencing、完成行保留和周期扫描实现；真实 MySQL 同键并发 merge 得到 revision `1/2`，最终 requested revision 为 2 且未被旧完成吞掉 | `DONE` |
| 26 | D2 route 由 transport CLI 同时声明目标与“已批准”集合，业务批准权泄漏到 transport | 已删除调用方自报授权；routing service 在锁外读取并校验 content-addressed catalog/evidence，锁内复验 policy release 后持久化四项 proof 与三元组 | `DONE` |
| 27 | V2 Bootstrap planner 固定空真人 cohort，纯规则通过却永远不会进入非零样本档 | 只读 reference repository 已批量加载匿名 strength summary；planner 与物化前重验复用同一四档规则，本地非零样本不被全局替换 | `DONE` |
| 28 | 摘要 Blueprint 与 legacy projector 无法证明提交结果仍满足综合/分项强度和库存上限 | Blueprint 已扩充为完整不可变资产目标；V2 专用 materializer 按最新 cohort、实际 ORM 强度、锁定 catalog 和库存额度重验，任何差异或越界整体回滚 | `DONE` |
| 29 | `create_virtual_player_v2`、批量命令和 Arena 容量入口可绕过 durable demand、population ownership 与 cell 缺口直接写 V2 | materializer 必须消费绑定当前事务和目标 cell 的一次性 permit；permit 仅由 durable demand consumer 在 routing/容量/缺口锁内签发，公开同步入口在 V2 模式 fail closed，并在提交前复验 ownership/routing | `DONE` |
| 30 | candidate report 自报 metrics 即可驱动 D2 verdict，digest 只能防登记后篡改，不能证明生成器或指标正确 | 已冻结四类原始 cohort artifact、版本化独立重算和 generator/source provenance；report schema v3 必须声明 artifact schema v2/metric algorithm v2 并与重算结果逐字段相等。真实 catalog/route 仍为空，未宣称代表性校准通过 | `DONE` |
| 31 | scheduled batch 对每个 Profile 重读 routing，百档形成 100 次额外往返 | 在 Profile `SELECT FOR UPDATE` 中用 `Exists` 校验完整 routing snapshot；命中时复用批次快照，漂移/缺失时回退 canonical reader，逐档保持 fail closed | `DONE` |
| 32 | 基准数据缺少 `grain` 模板时，每次训练重复查询目录 | planning snapshot 批量解析一次 grain 模板或“已确认缺失”，透传解析状态；库存数量仍逐 Manor 事务重新加锁，百档查询降至 `2422/2418` | `DONE` |
| 33 | D1/E `source_state.files` 只校验已列出的哈希，遗漏关键 owner、领域 primitive、Arena/监牢实现或测试时仍可能错误放行 readiness | `gate_evidence` 为 common、D1、E 分别冻结必需源码集合；verifier 在逐文件哈希前检查集合完备性，激活证据测试逐项证明缺任一文件都会 fail closed；两份 readiness YAML 已补齐并重算 SHA-256 | `DONE` |
| 34 | 全仓顺序下 transaction test flush 会清空建筑模板，V2 Admin 自动列名和技术审计热点基线也会随新增代码失配 | 共享 fixture 在 `game_data` 与 `manor_factory` 消费前幂等恢复六个 V2 核心模板和 `forge`；Admin 改用可排序中文 display callable；审计基线按动态前三名更新。完整非 integration 回归 `5500 passed, 118 deselected, 7 subtests passed` | `DONE` |
| 35 | acceptance YAML 的 pause evaluator 已标记为 readiness 验证完成，但嵌套 provider 仍保留 Gate A 时期的“未实现”状态，形成同一契约内的事实矛盾 | provider 状态同步为 `implemented_readiness_verified_not_activated`，并由 acceptance contract test 同时冻结 evaluator/provider 状态；仅修正实现状态，不授权 cutover 或 `v2_active` | `DONE` |
| 36 | candidate artifact 内的 provenance、源码 digest 与 cohort digest 可以同步自声明，形成“artifact 自己证明 artifact 可信”的循环 | artifact schema v2 增加外部 `hmac_sha256_v1` attestation；可信密钥只来自 runtime secret settings，默认无密钥，且不得来自 artifact/report/catalog。MAC 覆盖启用单元、policy/snapshot、生成器、root seed、源码、模板和全部原始 cohort；同步伪造负例 fail closed。当前方案限非生产，未来生产签名单独评审 | `DONE` |
| 37 | `robust_joint_outlier_rate` 实际是逐字段异常并集，无法识别边际都正常但变量组合不可能的联合异常 | metric algorithm v2 改为 12 维稳健最近邻模型，稳定拆分独立 fit/holdout 并只在 fit 上拟合；相关结构反转测试保持所有边际分布相同，仍稳定检出 candidate 异常 | `DONE` |
| 38 | runtime verifier 已要求 report schema v3，但 Gate A 机器可读 acceptance 只冻结 artifact schema v2 和 metric algorithm v2，未显式声明 report 版本 | `reference_snapshot_versioning.candidate_report_schema_version` 冻结为 `3` 并加入契约断言；补齐后重新执行完整 canonical Gate A，避免以旧证据覆盖新契约 | `DONE` |

## 5. 剩余实施顺序

在取得相应确认后，按以下顺序推进，禁止越级启用：

1. Gate D1 已具备 exit review 条件；是否切换 Bootstrap routing 是独立授权，当前继续保持不变。
2. Gate E readiness 已通过；只有明确确认测试数据处理后，才能依次执行 `v2_cutover -> runtime-eligible V1 清零 -> v2_active`。readiness 不授权其中任何一步。
3. Gate D2 保持可选关闭；未来取得合规代表性 artifact/report 后，使用已实现的重算/provenance verifier 逐三元组验收。它不阻塞 D1/E。

## 6. 当前验证记录

- Gate A canonical collection：`158` 个 nodeid，SHA-256 `60a18508f075f4a2d7d98477cba6cacdfbc92077f861df0f7c2313da74f4123c`；2026-07-29T00:04:08Z canonical 为 `146 contract in 12.35s + 12 real-service in 100.40s = 158 passed`。
- 完整非 integration 回归：`5500 passed, 118 deselected, 7 subtests passed in 822.64s`；此前建筑升级夹具顺序污染、Maintenance V2 setup error、Admin 中文标签和技术审计热点基线四项异常均未复现。
- rollout 定向回归：`70 passed`；历史 D1/D2 定向回归：`174 passed`。本轮最终 D1 定向契约：`297 passed in 42.22s`。
- Gate D1 最终核心真实 MySQL/Redis：`9 passed in 246.08s`；相邻 routing/Arena/坐标竞态：`8 passed in 5.46s`。
- Gate D1 最终 P95：planning `98.739ms`、materialization `165.264ms`，冻结阈值分别为 `250ms / 2000ms`；本轮未观察到 deadlock 或 lock timeout。
- Gate C reconciliation 独立回归：`25 passed`；真实 MySQL Gate C 并发：`6 passed`，覆盖 rollout/routing/policy/profile 锁序与既有并发场景；MySQL `13306`、Redis `16379` 健康且未重启。
- Gate D2、calibration、YAML/config/architecture 聚焦回归：`351 passed in 28.28s`；覆盖 artifact schema v2、report schema v3、metric algorithm v2、旧算法拒绝、外部 HMAC attestation、12 维 fit/holdout 联合异常、同步伪造 artifact/report/catalog、硬约束重算、active route、snapshot/proof 热漂移及在途计划写前拒绝。resolver 零 DML、evidence I/O 先于 routing 行锁和冻结 cohort 消费均已覆盖。
- Gate D2 真实 MySQL routing 并发：隔离测试库完整生命周期下 `1 passed in 98.32s`；第一个 writer 停在锁外 evidence preflight 时第二个 writer 可完成，前者恢复后由 revision CAS 拒绝，最终持久 route 为七字段 proof。
- Gate E 外部对账 SQLite 合并定向回归：`137 passed in 35.96s`；其中 Raid 完整兼容入口 `76 passed`，Arena/reconciliation/schedule `63 passed`。原领域回滚、严格幂等、两阶段提交、重试/quarantine、Celery transport/schedule 和 Arena 未解决档案排除均已覆盖。
- Gate E 外部对账真实 MySQL：`4 passed in 96.66s`；覆盖同 intent 并发 claim 单赢家、过期 lease 新 token reclaim、旧 token fencing、同 profile 顺序和 scanner `skip_locked`。新增文件已纳入 `CRITICAL_INTEGRATION_TESTS`，本机隔离 MySQL `13306`、Redis `16379` 健康且未重启。
- Gate E Maintenance V2 最终单文件：`45 passed in 16.89s`，覆盖全部同步动作、锁内漂移、receipt、结算、预算、调度与回滚。
- Gate E safety/Admin/Arena cap/满血 snapshot/监牢/动作规格合并：`373 passed, 2 skipped in 29.78s`；两个真实 MySQL 时钟/行锁语义用例由最终专用 follow-up `2 passed in 93.92s` 覆盖，未把 skip 当作绿色证据。
- Maintenance 真实 MySQL 非 benchmark 与相邻领域：`43 passed, 6 deselected in 217.12s`；覆盖并发单赢家、提交前/领域写后/提交后故障，technology、population/prestige 和 equipment。
- Maintenance 六档冻结矩阵全部通过。`[1,100]`：P95 `3086.509ms`、P99 `3146.188ms`、queries `2422`、writes `712`；`[2,100]`：P95 `3209.885ms`、P99 `3225.507ms`、queries `2418`、writes `709`；deadlock/lock timeout 均为 0。其余五档合并为 `5 passed in 407.47s`。
- routing 与 grain N+1 定向 `3 passed`；profile routing guard/safety metrics 最终 `13 passed`；装备回归 `182 passed`、领域 primitive `30 passed`、safety provider/metrics 历史定向 `42 passed`。
- 证据治理、Admin 与共享夹具最终聚焦回归 `216 passed in 29.08s`；此前跨文件 SQLite 的 `51 passed, 1 setup error` 已通过幂等建筑模板恢复闭合，并由上述完整回归确认不再复现。
- 最终 D1/E/manifest/activation evidence verifier：`192 passed in 8.75s`；两份 source-state 逐文件 SHA-256 无漂移，Gate A collection 仍为 `158` 个唯一 nodeid 且 checksum 不变。
- enrollment 新增证据：`legacy_before_gate/v2_active/v2_paused` 下 dry-run/apply 均拒绝，`v2_cutover` 下允许；锁外 seed/archetype/policy identity 陈旧时完整 CAS 拒写。
- 最终静态门禁：full mypy `695 source files`、Django check、`makemigrations --check --dry-run`、Ruff lint/本轮格式、compileall、`git diff --check` 全部通过。
- 全部真实服务验证只使用隔离 `test_webgame`、MySQL `13306` 和 Redis `16379`；服务未重启，未连接或迁移 `webgame`。未发布 policy、切换 routing、批量入组、重分段、重建或删除现有数据。

## 7. 当前授权边界

以下工作已由用户明确授权，可在保持运行模式关闭的前提下实施：

- 编辑 Gate C-E additive schema/migration、模型、service、worker 与管理命令；
- 执行 migration 的隔离测试，不对现有数据库运行迁移；
- 创建、迁移和销毁 canonical Gate A 隔离测试数据库；
- 接入 Gate D1/E 关闭状态下的 V2 runtime 能力和故障验证。

以下工作仍未授权：

- 批量入组、重分段、重建或删除任何现有测试数据；
- 将 Bootstrap/Maintenance mode 切换到 `v2_active`；
- Git commit、branch 或 push；
- 生产发布或对现有环境数据库应用 migration。

实现代码、执行数据库门禁、处理测试数据和启用运行模式是四个独立边界。每个边界只能在其前置证据满足且取得明确确认后执行。
