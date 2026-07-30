# 兼容入口清单（2026-03）

只记录当前仍明确保留的兼容入口，不再记录已经删除的历史 facade。

原则：

- 已无仓内消费者、也无明确外部兼容需求的入口，直接删除
- 仍保留的入口必须写清责任人、内外部消费者、保留原因、退场条件和复核日期
- 新兼容层如果不能进入这份清单，就不应该合入

## 当前保留项

### `battle/simulation_core.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：历史导入路径、测试与外部脚本可能仍使用 `battle.simulation_core`。
- 外部消费者登记：当前没有可核验的脚本名称或仓库地址；复核日前未补登记则按“无外部消费者”处理。
- 保留原因：战斗模拟已拆到 `battle/simulation/`，该文件继续重导出公开 API，避免一次性打断旧导入。
- 退场条件：仓内 `rg "simulation_core"` 只剩本文档与退场测试；外部调用方完成迁移后删除该文件。
- 下次复核日期：`2026-09-30`。

### `gameplay/models/__init__.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：大量现有代码仍使用 `from gameplay.models import Manor, InventoryItem, RaidRun, ...`。
- 外部消费者登记：Django app 与仓内生产代码；未登记独立外部消费者。
- 保留原因：`gameplay.models` 已拆包，但 Django app 与历史导入路径仍依赖包根模型导出。
- 退场条件：生产代码改为从具体模型子模块导入；迁移与 Django autodiscovery 不再需要包根导出后，收缩为最小 app 标记。
- 下次复核日期：`2026-09-30`。

### `guilds/models/__init__.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：帮会服务、视图、测试仍使用 `from guilds.models import ...`。
- 外部消费者登记：未登记独立外部消费者。
- 保留原因：`guilds.models` 已拆包，但包根导出仍是当前稳定模型入口。
- 退场条件：生产代码迁移到具体模型子模块；保留期内不得继续扩大 `__all__` 以外的聚合行为。
- 下次复核日期：`2026-09-30`。

### `guests/services/recruitment.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：页面、任务、测试仍依赖 `guests.services.recruitment` 作为门客招募公开入口。
- 外部消费者登记：未登记独立外部消费者。
- 保留原因：查询、候选、结算与 follow-up 已拆到子模块，该文件承接历史公开 API 与 monkeypatch 路径。
- 退场条件：调用方全部迁移到明确子模块入口，并为公开 service contract 补齐替代导入测试后删除兼容导出。
- 下次复核日期：`2026-09-30`。

### `gameplay/services/technology.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：视图、帮会科技联动与测试仍依赖 `gameplay.services.technology`。
- 外部消费者登记：未登记独立外部消费者。
- 保留原因：技术系统规则计算与运行态已拆分，该文件继续承接历史公开 API。
- 退场条件：调用方迁移到 `technology_catalog`、`technology_runtime`、`technology_helpers` 等明确职责模块；退场前不得继续承载新增业务分支。
- 下次复核日期：`2026-09-30`。

### `gameplay/services/virtual_players.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：仓内生产消费者为零；仅为已登记的外部运维脚本保留兼容窗口，仓内 facade 契约测试不计作生产消费者。
- 外部消费者登记：存在登记声明，但本工作区没有脚本名称、仓库地址或迁移联系人；`2026-08-31` 前必须补齐，否则视为无外部消费者。
- 保留原因：Gate B 已把实现和仓内调用迁到第 5.3 节真实 owner；该文件只以显式 `__all__` 重导出冻结的 19 个公共符号，不包含 ORM、事务或业务实现。
- 退场条件：外部运维脚本完成一个发布窗口的迁移后删除；若其中部分符号经独立评审成为稳定公共入口，可继续保留薄门面，但不得恢复私有实现或未登记重导出。
- 下次复核日期：`2026-08-31`。

### `gameplay/services/virtual_player_population.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：现有纯人口规划测试及可能使用旧路径的外部分析脚本。
- 外部消费者登记：当前没有可核验的脚本名称或仓库地址；复核日前未补登记则按“无外部消费者”处理。
- 保留原因：该文件只重导出 `virtual_player_core.population` 的纯规划契约，避免架构迁移同时打断历史导入。
- 退场条件：仓内消费者迁到真实 owner，外部分析脚本完成兼容窗口，且 import characterization test 证明旧路径不再需要。
- 下次复核日期：`2026-08-31`。

### `gameplay/services/arena/virtual_reserve.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：仓内生产消费者为零；仅为兼容窗口内已登记的外部调用保留，仓内 facade 契约测试不计作生产消费者。
- 外部消费者登记：存在登记声明，但本工作区没有调用方名称、仓库地址或迁移联系人；`2026-08-31` 前必须补齐，否则视为无外部消费者。
- 保留原因：Gate B 已将 demand state、reconcile、pool、fill 和 scan 迁到独立 owner；该文件只重导出冻结的 11 个公共入口，DTO、异常和下划线函数不构成公共契约。
- 退场条件：外部消费者完成一个兼容窗口的迁移后删除；若仍需保留稳定入口，该文件必须继续保持无 ORM、无事务、无业务逻辑。
- 下次复核日期：`2026-08-31`。

### `gameplay/services/virtual_player_rules.py`

- 责任人：`daniel`（仓库维护者）。
- 消费者：仓内运行时与测试已迁到真实 owner；旧路径只为兼容窗口内已确认的外部分析脚本保留。
- 外部消费者登记：存在确认声明，但本工作区没有脚本名称、仓库地址或迁移联系人；`2026-08-31` 前必须补齐，否则视为无外部消费者。
- 保留原因：该文件只以显式 `__all__` 重导出 `virtual_player_core.lifecycle` 和 `virtual_player_core.legacy.projection` 的原有公开纯规则，不包含实现，也不改变 V1 固定 seed 结果。
- 退场条件：外部分析脚本完成一个兼容窗口的导入迁移，且 import characterization test 证明旧路径不再需要后删除整个文件。
- 下次复核日期：`2026-08-31`。
