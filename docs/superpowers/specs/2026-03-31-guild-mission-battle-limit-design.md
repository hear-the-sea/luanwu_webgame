# 公会任务战斗限制设计

## 背景

当前 `finalize_guild_mission_run` 直接使用 `execute_battle` 默认的 `BattleOptions`，导致任何时候都受 `MAX_SQUAD` 的上限限制。新的需求是：公会任务应该照顾实际选择的攻击方人数和敌方配置，把对应的人数传入战斗执行，同时不动全局战斗配置。

## 目标

1. 只在公会任务 finalization 流程中注入自定义的攻击/防御 troop limit，保持其他 `BattleOptions` 调用不变。
2. 新增回归测试，保护选中 6 名以上攻击方、7 名敌方时的参数向上传递。
3. 保留现有任务完成逻辑（奖励、存活计算）不变。

## 约束条件

1. 不改动 `battle.execution` 或 `BattleOptions` 的默认 `MAX_SQUAD`。
2. 不影响 launch/retreat 等其他 guild mission 路径。
3. 新增测试必须先失败再通过（TDD）。

## 方案

### 限制解析 helper

在 `guilds/services/guild_missions.py` 中 `finalize_guild_mission_run` 之前添加两个局部 helper：

* `_resolve_guild_mission_attacker_limit(run)`：以 `selected_guest_count` 为主、再退回 `len(guest_snapshots)`，并确保至少 1。
* `_resolve_guild_mission_defender_limit(run, *, attacker_limit)`：先尝试取 `template.enemy_guests` 的长度（只在 list 且 non-empty 时），否则退回 `attacker_limit`，并确保 1。

`finalize_guild_mission_run` 在准备 battle options 时调用 helper 并传入 `limit`/`defender_limit`，其余参数不变。

### 回归测试

在 `tests/test_guild_mission_service.py` 的 `finalize_guild_mission` 相关区域新增 `expanded_battle_limits` 测试，步骤：

1. 创建 manager、guild、template（指定 7 个 `enemy_guests`）、6 名选中门客和快照。
2. 模拟 `execute_battle`，获取传入参数、保存 `BattleOptions` 实例。
3. 运行 `finalize`，断言 `options.limit == 6`、`options.defender_limit == 7`、`len(options.defender_setup["guests"]) == 7`。

先运行 `pytest "tests/test_guild_mission_service.py" -q -k "expanded_battle_limits"` 确认失败（缺少 limit），然后实现 helper 使其通过。继续跑全部 guild mission tests 保证不失效。

### 验证流程

1. `pytest "tests/test_guild_mission_service.py" -q -k "expanded_battle_limits"`（预期失败）。
2. 实现 helper -> 重新执行上述命令（预期通过）。
3. `pytest "tests/test_guild_mission_service.py" -q`。
4. 按任务要求再跑合并的四个测试文件（service + 3 其他）。

## 风险与缓解

* 若 helper 读取的字段变化或未填值，limit 至少为 1，避免空参数导致异常。
* 保持 defender_setup 里的 guest 列表不变，避免与其他使用者共享 mutable 对象。

## 下一步

1. 等主人确认设计后再动手编码。
2. 在当前文档目录记录变更内容以备审查和复审。
