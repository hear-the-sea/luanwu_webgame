# 竞技场虚拟玩家后备池优化方案（2026-08-06）

## 目标

竞技场虚拟玩家的首要目标是按真人报名状态及时完成补位，同时保持公平性、可回收性和并发安全：

- 阵容人数以真人中位参考阵容为下限，在活动允许的门客上限内做确定性浮动；战力仍必须落在真人参考战力的 80%～120% 区间。
- 虚拟补位只把 Entry snapshot 规范化为满血，不写回真实 Guest，不改变真人报名语义。
- 后备池同时维护“完整替换预算”和“当前预热目标”，避免为短期缺口一次性锁定过多档案。
- 需求输入变化、后备池实际进展和失败退避分别记录，阻塞判断不依赖模糊字段。
- 任何成长重试都不能无限占用后备容量；安全暂停时释放可安全释放的训练租约。

## 虚拟阵容人数

真人门客数只用于确定参考战力和最低阵容规模，不再作为虚拟玩家的硬人数。若真人参考为 3 人、普通竞技场上限为 10 人，虚拟玩家会按 `3～10` 的合法范围尝试阵容；优先使用不同于真人人数的候选，只有更大阵容无法满足战力区间时才回退到 3 人。

这样可以降低“虚拟玩家复制真人阵容”的识别感，同时不会通过无上限增加门客或战力破坏竞技平衡。

## 培养顺序与成长节奏

- 虚拟玩家进入竞技场培养时，先补齐持久化的 `roster_target_count`，再进行等级、技能、装备等质量动作；数量阶段每周期最多新增 2 名黑/灰门客，不授予模板技能。
- 数量扩张不消耗普通“质量成长”预算，但仍受真人参考强度组件上限、门客容量、目标人数和活动最大阵容人数约束，避免用一次大步成长绕过竞技场公平边界。
- 普通 V2 维护单次等级成长上限保持为 3 级；竞技场后备补位允许更快追赶，单次等级成长上限为 6 级，但仍受 25% 追赶比例、战力区间和强度保护约束。补位前每小时尝试一次，补位后每 15 分钟尝试一次，最多 8 轮，单个后备租约最长 12 小时。
- 这些时间参数分开保留，便于通过 `growth_rounds`、`next_acceleration_at`、`roster_target_distribution` 和 `failure_reason` 观察实际节奏，不把“人数不足”和“质量不足”混成同一类失败。

## 目标状态

```text
ACTIVE
  ├─ 真人需求变化 -> version + 1，重新评估已有成员
  ├─ 后备池有实际租约/创建/成长进展 -> 更新 last_progress_at
  ├─ 12 小时没有输入变化和后备进展 -> BLOCKED
  └─ 缺口消失或活动结束 -> CLOSED / SATISFIED

TRAINING
  ├─ APPLIED / GROWN -> 重新评估
  ├─ NO_ACTION / BUSY -> 按原始 created_at + 12h 租约期限退避
  ├─ 到达租约期限 -> EXHAUSTED，允许同轮补入替代者
  └─ PAUSED / 运行时安全暂停 -> 释放训练租约
```

## 后备目标

`reserve_target_count` 表示完整替换预算；`warm_target_count` 表示当前允许实际占用的 READY/TRAINING 槽位。

```text
replacement_target = max(missing * 3, 6)
warm_target = min(replacement_target, max(missing + ceil((missing + 1) / 2), 6))
```

两者都由纯策略模块计算，并持久化到需求，Admin、日志和 `ReserveReplenishmentResult` 使用同一语义。

## 锁和边界

- Demand 是需求状态与后备成员的事务锁入口。
- Profile 创建/重激活继续由 population runtime 负责。
- 阵容选择是纯计算；候选重新评估和最终物化继续在锁内执行。
- Growth worker 在 Arena 事务外执行 Maintenance，完成后用 claim token 和 demand/member version fencing。
- 路由不可用或安全暂停时不发起成长写入，只执行安全租约释放和过期清理。
- BLOCKED 需求只有在出现晚于阻塞时刻的新真人报名时才被扫描唤醒，避免空转查询。
- 每个需求每轮最多创建 2 个新档案，限制单个需求对人口容量服务的突发压力。

## 验证要求

- 固定参考 entry、profile、demand version 时，阵容人数、ready 排序、租约数量和物化结果保持确定性。
- warm target 与完整替换预算分别出现在模型、日志、Admin 和测试中。
- 输入变化不会伪装成后备池进展；重复 reconcile 不增加版本。
- NO_ACTION/BUSY 在 12 小时后都释放 active capacity；PAUSED 不保留训练占位。
- 真实 MySQL/Redis 下覆盖过期 claim、成长 finalize、补位和报名并发。
