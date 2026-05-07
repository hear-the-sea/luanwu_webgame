# 兼容入口清单（2026-03）

只记录当前仍明确保留的兼容入口，不再记录已经删除的历史 facade。

原则：

- 已无仓内消费者、也无明确外部兼容需求的入口，直接删除
- 仍保留的入口必须写清消费者、保留原因和退场条件
- 新兼容层如果不能进入这份清单，就不应该合入

## 当前保留项

### `battle/simulation_core.py`

- 消费者：历史导入路径、测试与外部脚本可能仍使用 `battle.simulation_core`。
- 保留原因：战斗模拟已拆到 `battle/simulation/`，该文件继续重导出公开 API，避免一次性打断旧导入。
- 退场条件：仓内 `rg "simulation_core"` 只剩本文档与退场测试；外部调用方完成迁移后删除该文件。

### `gameplay/models/__init__.py`

- 消费者：大量现有代码仍使用 `from gameplay.models import Manor, InventoryItem, RaidRun, ...`。
- 保留原因：`gameplay.models` 已拆包，但 Django app 与历史导入路径仍依赖包根模型导出。
- 退场条件：生产代码改为从具体模型子模块导入；迁移与 Django autodiscovery 不再需要包根导出后，收缩为最小 app 标记。

### `guilds/models/__init__.py`

- 消费者：帮会服务、视图、测试仍使用 `from guilds.models import ...`。
- 保留原因：`guilds.models` 已拆包，但包根导出仍是当前稳定模型入口。
- 退场条件：生产代码迁移到具体模型子模块；保留期内不得继续扩大 `__all__` 以外的聚合行为。

### `guests/services/recruitment.py`

- 消费者：页面、任务、测试仍依赖 `guests.services.recruitment` 作为门客招募公开入口。
- 保留原因：查询、候选、结算与 follow-up 已拆到子模块，该文件承接历史公开 API 与 monkeypatch 路径。
- 退场条件：调用方全部迁移到明确子模块入口，并为公开 service contract 补齐替代导入测试后删除兼容导出。

### `gameplay/services/technology.py`

- 消费者：视图、帮会科技联动与测试仍依赖 `gameplay.services.technology`。
- 保留原因：技术系统规则计算与运行态已拆分，该文件继续承接历史公开 API。
- 退场条件：调用方迁移到 `technology_catalog`、`technology_runtime`、`technology_helpers` 等明确职责模块；退场前不得继续承载新增业务分支。
