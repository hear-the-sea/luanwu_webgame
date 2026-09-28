# 数据流边界文档

本文档描述各核心业务领域的数据来源、缓存策略、事务边界、失败补偿行为和关键不变量。

相关代码路径在各节中标注。

---

## 1. 交易系统（trade）

### 1.1 钱庄（bank_service）

**代码**：`trade/services/bank_service.py`

#### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 金条有效供应量 | DB → Cache | 统计最近 14 天活跃玩家持有的金条总量 |
| 当日个人兑换数量 | DB | `GoldBarExchangeLog` 按日期聚合 |
| 金条基础配置 | 代码常量 | 基准价、手续费率、价格上下限等 |
| 金条物品模板 | DB | `ItemTemplate(key="gold_bar")` |

#### 缓存策略

| 缓存键 | TTL | 说明 |
|--------|-----|------|
| `gold_bar:effective_supply` | 300s（5 分钟） | 活跃金条供应量主缓存 |
| `gold_bar:effective_supply:stale` | 3600s（1 小时） | 过期缓存，用于降级 |
| `gold_bar:effective_supply:lock` | 10s | 分布式锁，防止缓存击穿 |

**三级降级机制**：
1. 主缓存命中 → 直接使用（来源 `cache`）
2. 主缓存未命中 + 获取锁成功 → 查询 DB 并刷新缓存（来源 `db`）
3. 主缓存未命中 + 获取锁失败 → 使用过期缓存（来源 `stale_cache`）
4. 均不可用 → 使用默认值 `GOLD_BAR_TARGET_SUPPLY=1000`（来源 `default`）

**失效时机**：每次成功兑换后删除主缓存键（`_safe_cache_delete(SUPPLY_CACHE_KEY)`）。

#### 事务边界

`exchange_gold_bar()` 在 `transaction.atomic()` 内执行完整兑换流程：

```
transaction.atomic():
    1. Manor.select_for_update()        — 用户级行锁
    2. calculate_gold_bar_cost(fail_closed=True)  — 锁内重新计算价格
    3. spend_resources_locked()          — 扣除银两
    4. InventoryItem.select_for_update() — 锁定金条库存行
    5. InventoryItem 增加 / 创建         — 发放金条
    6. GoldBarExchangeLog.create()       — 记录日志
```

**关键设计**：价格计算在锁内执行（`fail_closed=True`），缓存不可用时直接抛出 `GoldBarPricingUnavailableError`，拒绝兑换而非使用降级价格。查看价格（`get_bank_info`）则允许降级展示。

#### 失败/补偿行为

- 兑换事务内任意步骤失败 → 整体回滚，银两不扣、金条不发
- 缓存写入失败 → `_safe_cache_set` 吞掉异常并记录日志，不影响主流程
- 降级展示时（`pricing_degraded=True`）前端禁用兑换按钮，显示提示信息

#### 关键不变量

- 银两扣除与金条发放必须在同一事务中（原子性）
- 价格计算必须基于 `select_for_update` 锁定后的最新状态（防止并发低价买入）
- `fail_closed` 模式下缓存不可用时拒绝交易（宁可不可用也不可错误定价）

---

### 1.2 交易选择器（trade selectors）

**代码**：`trade/selectors.py`

#### 数据来源

所有数据来自 DB 实时查询（通过各 service 函数获取），无 YAML 或缓存层。

#### 失败/补偿行为

全部使用 `_safe_call()` 包装：仅捕获 `DatabaseError`、`ConnectionError`、`OSError`、`TimeoutError`（即 `_is_expected_trade_context_error`），其他异常向上抛出。

降级时各模块返回空默认值（空列表或空字典），页面可以展示但数据不完整。通过 `trade_alerts` 列表向前端传递降级提示。

---

## 2. 踢馆系统（raid）

**代码**：`gameplay/services/raid/`

### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 庄园坐标/声望/资源 | DB | `Manor` 模型 |
| 门客属性/技能 | DB | `Guest`、`GuestSkill` 模型 |
| 兵种模板 | YAML + DB | `data/troop_templates.yaml` → `TroopTemplate` |
| 玩家护院数量 | DB | `PlayerTroop` 模型 |
| 掠夺概率常量 | 代码常量 | `PVPConstants` 类 |
| 近 6 小时防守战斗 | DB | 按已生成战报的实际时间判断开战前是否回满防守血量 |

### 缓存策略

| 缓存 | 说明 |
|------|------|
| 最近攻击缓存 | 旧统计接口按 defender_id 缓存最近 24 小时出征记录，事务提交后通过 `on_commit` 失效；不参与攻击资格判定 |

缓存使用较少——踢馆核心流程基于行级锁和实时 DB 查询，不依赖缓存做关键决策。

### 事务边界

#### start_raid（发起踢馆）

```
pre-check（无锁，快速拒绝不合法请求）
    ↓
transaction.atomic():
    1. _lock_manor_pair(attacker, defender)  — 按 pk 排序加锁防死锁
    2. 二次校验攻击合法性（TOCTOU 防护）
    3. 门客 select_for_update + 状态设置为 DEPLOYED
    4. PlayerTroop.select_for_update — 扣除护院
    5. 创建 RaidRun 记录
    6. on_commit: 失效旧统计接口的最近攻击缓存
    ↓（事务外，最佳努力）
发送来袭警报消息
调度 Celery 战斗任务
```

#### process_raid_battle（战斗结算）

```
transaction.atomic():
    1. 按 pk 锁定双方 Manor 与 RaidRun，状态 MARCHING → BATTLING
    2. 校验新手保护和免战牌
    3. 查询防守方近 6 小时已结算战斗；第 2 场起回满参战门客及城防血量
    4. 执行战斗模拟（simulate_report）并应用门客、城防伤害
    5. 应用掠夺和声望变化
    6. 防守庄园战败则释放吕布，即使未参战；同步清理未开战出征快照
    7. 尝试俘获普通门客并应用战斗回收奖励
    8. 状态设为 RETURNING
    ↓（事务外，最佳努力）
发送战报消息
驱散防守方来袭中的踢馆队伍
调度返程完成任务
```

#### finalize_raid（返程完成）

```
transaction.atomic():
    1. RaidRun.select_for_update
    2. 门客 select_for_update → DEPLOYED 恢复为 IDLE
    3. 归还存活护院（PlayerTroop upsert with F()）
    4. 发放战利品给攻方（grant_resources_locked）
    5. 状态设为 COMPLETED
```

### 失败/补偿行为

- 战斗结算事务失败 → 整体回滚，RaidRun 仍为 MARCHING 状态，下次扫描任务会重试
- 消息发送失败 → 在事务外用 try/except 包裹，仅记录日志，不影响已结算的战斗
- Celery 任务调度失败 → 同步降级执行（如果行军时间已到），否则依赖定时扫描任务兜底
- 庄园不存在（被删除等极端情况） → `_fail_raid_run_due_missing_manor` 释放门客和护院，标记为完成

### 关键不变量

- 攻守双方行锁必须按 pk 升序获取（`sorted([attacker_id, defender_id])`），防止死锁
- 门客状态转换 IDLE → DEPLOYED → IDLE 必须通过 `select_for_update` 保护
- 掠夺扣除按实际可用量裁剪（`min(current, loot)`），绝不允许负数
- 战报消息发送在事务外执行——战斗结算的原子性优先于消息完整性

---

## 3. 侦察系统（scout）

**代码**：`gameplay/services/raid/scout.py`

### 数据来源

| 数据 | 来源 |
|------|------|
| 侦察术等级 | DB（`PlayerTechnology`） |
| 庄园距离 | DB（`Manor` 坐标字段计算） |
| 侦察冷却 | DB（`ScoutCooldown`） |
| 探子数量 | DB（`PlayerTroop(key="scout")`） |
| 情报数据 | DB 实时查询（门客数量、平均等级、护院数量） |

### 事务边界

```
pre-check（无锁，快速拒绝）
    ↓
transaction.atomic():
    1. _lock_manor_pair — 双方行锁
    2. 二次校验（冷却、探子数量）
    3. 扣除探子（PlayerTroop.select_for_update, count -= 1）
    4. 创建 ScoutRecord
    ↓
调度 complete_scout_task
```

侦察成功时返程完成后归还探子；失败时探子损失（不归还）。撤退时立即归还探子。

### 关键不变量

- 探子扣除与侦察记录创建在同一事务中
- 冷却检查在锁内二次验证（防 TOCTOU）
- 成功率在锁内重新计算（基于锁定后的最新科技等级）

---

## 4. 竞技场系统（arena）

**代码**：`gameplay/services/arena/`

### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 竞技场规则 | YAML | `data/arena_rules.yaml` → `lru_cache` 进程内永久缓存 |
| 奖励目录 | YAML | `data/arena_rewards.yaml` → `lru_cache` 进程内永久缓存 |
| 比赛状态 | DB | `ArenaTournament`、`ArenaEntry`、`ArenaMatch` |
| 门客快照 | DB（JSON 字段） | `ArenaEntryGuest.snapshot` |
| 每日参与计数 | DB + Manor 字段 | 按日期重置的计数器 |

### 缓存策略

| 缓存 | TTL | 说明 |
|------|-----|------|
| `load_arena_rules()` | 进程生命周期 | `@lru_cache(maxsize=1)`，不自动过期，需调 `clear_arena_rules_cache()` 刷新 |
| `load_arena_reward_catalog()` | 进程生命周期 | 同上，`clear_arena_reward_cache()` 可刷新 |

YAML 配置在首次读取后缓存于进程内存中，修改 YAML 后需重启进程或手动调用 `clear_*_cache()` 函数生效。

### 事务边界

#### register_arena_entry（报名）

```
@transaction.atomic:
    1. Manor.select_for_update()
    2. 同步每日参与计数器（跨日自动重置）
    3. 检查是否已有进行中的报名
    4. 门客 select_for_update + 状态设为 DEPLOYED
    5. 扣除报名银两
    6. 获取或创建 RECRUITING 状态的锦标赛
    7. 创建 ArenaEntry + ArenaEntryGuest（含快照）
    8. 如满员则自动开赛
```

#### exchange_arena_reward（兑换奖励）

```
@transaction.atomic:
    1. Manor.select_for_update()
    2. 检查角斗币余额
    3. 检查每日兑换限额
    4. 扣除角斗币（F("arena_coins") - cost）
    5. 发放资源 / 物品（grant_resources_locked / add_item_to_inventory_locked）
    6. 创建 ArenaExchangeRecord
    7. 发送兑换成功消息（最佳努力）
```

#### _run_tournament_round（比赛轮次）

```
transaction.atomic():        — 第一阶段：锁定锦标赛和比赛记录
    1. ArenaTournament.select_for_update
    2. ArenaMatch.select_for_update（本轮 SCHEDULED 状态）
    3. 推后下次扫描时间（防并发 Worker 重复处理）
    ↓
事务外：逐场结算比赛（每场在独立的事务/锁内模拟战斗）
    ↓
transaction.atomic():        — 第二阶段：汇总轮次结果
    1. 更新淘汰选手状态
    2. 安排下一轮或结束锦标赛
```

### 失败/补偿行为

- 战斗模拟失败 → 抛出 `ArenaMatchResolutionError`，比赛保留待重试（`round_retry_seconds` 后重新扫描）
- 报名事务失败 → 整体回滚，银两不扣、门客不锁
- 取消报名 → 退还门客状态和每日参与计数（原子操作）
- 奖励兑换事务失败 → 回滚，角斗币和物品均不变

### 关键不变量

- 报名费扣除与 Entry 创建必须原子（防止扣费不报名或报名不扣费）
- 每个锦标赛满员后自动开赛，开赛状态转换在 `select_for_update` 保护下
- 门客快照在报名时生成，比赛时使用快照而非实时数据（防止比赛期间修改门客属性影响公平性）
- 轮次处理先推后下次扫描时间再释放锁，防止并发 Worker 重复处理同一轮

---

## 5. 门客招募系统（guests/recruit）

**代码**：`guests/views/recruit.py`、`guests/services/recruitment.py`、`guests/services/recruitment_guests.py`

### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 卡池定义 | DB | `RecruitmentPool` 模型 |
| 门客模板 | DB + YAML | `GuestTemplate`，通过 management command 从 YAML 同步 |
| 招募大厅上下文 | DB + Cache | `get_recruitment_hall_context` 可缓存结果 |
| 候选门客 | DB | `RecruitmentCandidate` |

### 缓存策略

| 缓存 | 说明 |
|------|------|
| 招募大厅渲染缓存 | 按 manor_id 缓存，每次招募/确认/放弃操作后调用 `invalidate_recruitment_hall_cache()` 主动失效 |

### 事务边界

#### start_guest_recruitment（发起招募）

```
@transaction.atomic:
    1. Manor.select_for_update()
    2. 验证招募条件（活跃招募、每日限制等）
    3. 扣除招募费用（spend_resources）
    4. 清除现有候选门客
    5. 创建 GuestRecruitment 记录（PENDING 状态）
    ↓
调度 Celery 任务：倒计时后生成候选门客
```

#### use_magnifying_glass_for_candidates（使用放大镜）

```
@transaction.atomic:
    1. Manor.select_for_update()
    2. InventoryItem.select_for_update()（锁定放大镜道具）
    3. 候选门客批量更新 rarity_revealed=True
    4. 扣除道具（consume_inventory_item_locked）
    ↓
失效招募大厅缓存
```

锁顺序统一为：Manor → InventoryItem → RecruitmentCandidate。

#### bulk_finalize_candidates（批量确认候选）

```
@transaction.atomic:
    1. Manor.select_for_update()
    2. 检查门客容量
    3. 逐个候选 → 创建 Guest + 删除 Candidate
    ↓
失效招募大厅缓存
```

### View 层防并发

`guests/views/recruit.py` 在视图层使用 `acquire_best_effort_lock` 实现请求级去重：

- 锁键格式：`recruit:view_lock:{action}:{manor_id}:{scope}`
- 超时：5 秒
- 获取失败返回 409（"请求处理中，请稍候重试"）
- 无论成功失败都在 finally 中释放锁

这是独立于数据库事务的应用层并发控制，用于防止用户快速重复点击。

### 失败/补偿行为

- 招募事务失败 → 回滚，费用不扣、候选不清
- 道具扣减与显现在同一事务中 → 避免"显现成功但道具未扣"的白嫖问题
- Celery 任务调度失败 → 招募记录保留 PENDING 状态，定时扫描任务兜底
- 缓存失效失败 → `try/except` 包裹仅记录日志，不影响主流程（下次页面加载会自然刷新）

### 关键不变量

- 资源扣除与招募记录创建必须原子
- 放大镜道具消耗与稀有度显现必须原子（锁顺序固定）
- 候选门客确认时逐个处理，容量不足的候选保留不删除（部分成功）

---

## 6. 上下文处理器（context_processors）

**代码**：`gameplay/context_processors.py`

### 数据来源与缓存

| 数据 | 主数据源 | 缓存层 | TTL |
|------|----------|--------|-----|
| 总用户数 | DB COUNT 查询 | Redis + 进程内 L1 | 300s |
| 在线用户数 | Redis ZSET | Redis + 进程内 L1 | 5s |
| 未读消息数 | DB | 无缓存 | - |
| 玩家排名 | Service 层计算 | Redis | 30s |
| 保护状态 | DB | 无缓存 | - |

**两级缓存降级**：
1. Redis 缓存命中 → 直接返回，同时更新本地 L1 缓存
2. Redis 不可用 → 回退到进程内 L1 缓存（`_LOCAL_STATS_CACHE`，带 `threading.Lock` 保护）
3. L1 也无数据 → 查询 DB 或 Redis ZSET

`_LOCAL_STATS_CACHE` 最大容量 64 条，超出时按过期时间淘汰最旧的 16 条。

### 失败行为

- Redis 不可用时记录 `record_degradation(CACHE_FALLBACK | REDIS_FAILURE)`
- 在线用户数查询降级路径：Redis ZSET → 本地缓存 → DB 近 30 分钟登录用户 COUNT
- 任何单项查询失败都不会影响其他上下文数据的加载（各项独立 try/except）
- AJAX 请求和非 HTML 请求跳过全局统计数据加载（节省开销）

---

## 7. 门客名册 / 详情读路径（guests/views/roster.py）

**代码**：`guests/views/read_helpers.py`、`guests/views/roster.py`

### 数据来源与边界

- 页面 GET 只允许读取 `Manor`、`Guest`、装备与技能等读模型，不再在读路径里调用训练结算、自动续训、回血或套装重算。
- 庄园级资源投影仍通过 `get_prepared_manor_for_read()` 执行，保持与其他页面一致的只读投影语义。
- 新门客的自动训练计时器必须在候选确认写链路里创建，不得依赖名册页或详情页首次访问触发。

### 失败/补偿行为

- 页面读取失败时只沿用既有读侧降级，不承担门客状态补偿职责。
- 训练完成后的显式收口仍由 `guests:check_training`、异步任务和训练服务负责，不回挂到页面 GET。

---

## 8. 装备弹窗读路径（guests/views/equipment.py）

**代码**：`guests/views/equipment.py`、`guests/services/equipment.py`

### 数据来源与边界

- `guests:gear_options` 只允许读取已存在的 durable state：仓库 `InventoryItem` 与已落库的空闲 `GearItem`；不得在 GET 中调用库存同步来创建/删除 `GearItem`。
- 缺少 `GearItem` 实体时，只能在 `guests:equip` 这类显式写入口里按模板键物化，用于承接后续加锁、穿戴和库存扣减。
- 读侧缓存只缓存装备选项投影，不缓存通过 GET 触发的任何状态修正结果。

### 失败/补偿行为

- 装备选项缓存失败时允许降级为直接查库，但仍保持纯读语义。
- 真正的库存扣减、装备占用和属性变更仍由写路径事务负责，GET 不承担补偿或预同步职责。

---

## 9. 护院募兵页面读路径（gameplay/views/recruitment.py）

**代码**：`gameplay/views/recruitment.py`、`gameplay/selectors/troop_recruitment.py`

### 数据来源与边界

- `TroopRecruitmentView` 只负责加载当前庄园并调用显式读 selector，不再在 view 内部混排分类计算、速度展示和募兵上下文装配。
- 护院募兵页面的读模型由 `get_troop_recruitment_context()` 统一返回，包含募兵选项、分类、当前募兵队列与护院库存等展示数据。
- 发起募兵、钱庄存取护院仍保持在 POST 写入口中，页面 GET 不承担任何募兵状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用庄园资源投影的只读降级语义，不新增写路径补偿。
- 募兵完成、队列收口和库存变化仍由显式 service / task 入口负责，GET 页面不回挂 finalize / refresh。

---

## 10. 科技页面读路径（gameplay/views/technology.py）

**代码**：`gameplay/views/technology.py`、`gameplay/selectors/technology.py`

### 数据来源与边界

- `TechnologyView` 只负责加载当前庄园并调用显式读 selector，不再在 view 内部混排 tab 归一化、武艺兵种筛选和页面上下文装配。
- 科技页面的读模型由 `get_technology_page_context()` 统一返回，负责基础 / 武艺 / 生产三个标签页的展示数据组织。
- 科技升级仍保持在 POST 写入口 `upgrade_technology_view` 中，GET 页面不承担科技状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用庄园资源投影的只读降级语义，不新增写路径补偿。
- 科技升级完成、收口与通知仍由显式 service / task 入口负责，GET 页面不回挂 finalize / refresh。

---

## 11. 打工页面读路径（gameplay/views/work.py）

**代码**：`gameplay/views/work.py`、`gameplay/selectors/work.py`

### 数据来源与边界

- `WorkView` 只负责加载当前庄园并调用显式读 selector，不再在 view 内部混排工作区分页、候选门客筛选和进行中任务映射。
- 打工页面的读模型由 `get_work_page_context()` 统一返回，负责工作区标签、分页结果、卡片绑定的活动任务和可派遣门客列表。
- 派遣、召回、领取报酬仍保持在 POST 写入口中，GET 页面不承担打工状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用庄园资源投影的只读降级语义，不新增写路径补偿。
- 打工完成、报酬领取和召回仍由显式 service / task 入口负责，GET 页面不回挂 finalize / refresh。

---

## 12. 生产页面读路径（gameplay/views/production.py）

**代码**：`gameplay/views/production.py`、`gameplay/selectors/production.py`

### 数据来源与边界

- `StableView / RanchView / SmithyView / ForgeView` 只负责加载当前庄园并调用显式读 selector，不再继续依赖 `production_page_context` 在 view helper 中混排页面装配。
- 生产页面的读模型由 `get_stable_page_context()`、`get_ranch_page_context()`、`get_smithy_page_context()`、`get_forge_page_context()` 统一返回，负责各自页面的展示数据组织。
- 生产、分解、图纸合成等动作仍保持在 POST 写入口中，GET 页面不承担生产状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用庄园资源投影的只读降级语义，不新增写路径补偿。
- 生产完成、分解收口和合成结果落库仍由显式 service / task 入口负责，GET 页面不回挂 finalize / refresh。

---

## 13. 监牢 / 结义林页面读路径（gameplay/views/jail.py）

**代码**：`gameplay/views/jail.py`、`gameplay/selectors/jail.py`

### 数据来源与边界

- `JailView` 与 `OathGroveView` 只负责加载当前庄园并调用显式读 selector，不再在 view 内部拼装囚徒列表、结义容量和候选门客列表。
- 页面读模型由 `get_jail_page_context()` 与 `get_oath_grove_page_context()` 统一返回，负责监牢和结义林展示数据组织。
- 招募囚徒、画饼、释放、结义添加/移除仍保持在 POST / API 写入口中，GET 页面不承担状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用庄园资源投影的只读降级语义，不新增写路径补偿。
- 囚徒状态变化、结义关系变更和相关资源扣减仍由显式 service / locked action 入口负责，GET 页面不回挂 finalize / refresh。

---

## 14. 核心页面读路径（gameplay/views/core.py）

**代码**：`gameplay/views/core.py`、`gameplay/selectors/core.py`

### 数据来源与边界

- `DashboardView / SettingsView / RankingView` 只负责加载当前庄园并调用显式读 selector，不再在 view 内部拼装建筑展示、改名卡统计和排行榜上下文。
- 核心页面的读模型由 `get_dashboard_context()`、`get_settings_page_context()`、`get_ranking_page_context()` 统一返回，负责各自页面的展示数据组织。
- 庄园更名等状态变更仍保持在 POST 写入口中，GET 页面不承担状态推进或补偿刷新职责。

### 失败/补偿行为

- 页面读取继续沿用现有只读降级语义，不新增写路径补偿。
- 更名、声望变化和首页活动状态仍由显式 service / selector 入口负责，GET 页面不回挂 finalize / refresh。
