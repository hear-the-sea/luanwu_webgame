# 运行期配置与 YAML 数据

> 最近校正：2026-03-26

本文档说明 `data/` 目录下 YAML 文件的当前职责、刷新方式与部署注意事项。这里不再手写字段级 schema；字段契约以 loader 和 `validate_yaml_configs` 为准。

## 当前数据分类

### 1. 运行期规则文件

这类文件由 Python 服务直接读取，并通常带有进程内缓存。

当前已经纳入 `python manage.py reload_runtime_configs` 统一刷新流程的文件：

- `data/shop_items.yaml`
- `data/luanwu_shop.yaml`
- `data/auction_items.yaml`
- `data/warehouse_production.yaml`
- `data/forge_equipment.yaml`
- `data/forge_blueprints.yaml`
- `data/forge_decompose.yaml`
- `data/stable_production.yaml`
- `data/ranch_production.yaml`
- `data/smithy_production.yaml`
- `data/guest_growth_rules.yaml`
- `data/arena_rewards.yaml`
- `data/arena_rules.yaml`
- `data/trade_market_rules.yaml`
- `data/guild_rules.yaml`

推荐操作：

```bash
python manage.py reload_runtime_configs
```

这条命令会刷新对应 loader 的缓存，并输出一份汇总统计。

### 2. 运行期文件，但当前不在统一热刷新入口内

目前最需要注意的是：

- `data/recruitment_rarity_weights.yaml`

它当前会被 `guests.utils.recruitment_utils` 读取并缓存，但不在 `reload_runtime_configs()` 的统一刷新清单里。修改后最稳妥的做法仍然是：

1. 重启相关 Web / Worker 进程
2. 或者在受控脚本里显式清理对应模块缓存

不要假设 `reload_runtime_configs` 会自动覆盖它。

### 3. 需要导入数据库的模板文件

这类文件不是直接作为在线规则读取，而是通过 management command 导入数据库：

- `data/building_templates.yaml`
- `data/technology_templates.yaml`
- `data/item_templates.yaml`
- `data/troop_templates.yaml`
- `data/guest_templates.yaml`
- `data/guest_skills.yaml`
- `data/mission_templates.yaml`

推荐操作：

```bash
python manage.py bootstrap_game_data --skip-images
```

如果只需要单项导入，也可以继续使用各自的 `load_*` 管理命令。

## 常见操作

### 只改运行期规则

适合场景：

- 商铺和拍卖可售项
- 乱舞商城商品、兑换价格和奖励规则
- 仓库、锻造、冶炼、养殖、马房生产规则
- 竞技场奖励和规则
- 帮会规则
- 交易行挂单规则
- 门客成长规则

命令：

```bash
python manage.py reload_runtime_configs
```

### 改模板主数据

适合场景：

- 建筑、科技、物品、兵种、门客、技能、任务模板

命令：

```bash
python manage.py bootstrap_game_data --skip-images
```

### 校验 YAML 契约

推荐在本地和 CI 中执行：

```bash
python manage.py validate_yaml_configs
python manage.py validate_yaml_configs --strict-coverage
```

当前 `data/` 根目录下的 YAML 文件都已经纳入 schema 校验；`--strict-coverage` 主要用于防止未来新增 YAML 后忘记补验证器。

## 部署建议

- 代码部署与 `data/*.yaml` 更新应视为同一版本发布单元。
- 仅涉及运行期规则且在统一刷新范围内时，发布后可执行一次 `reload_runtime_configs`。
- 涉及数据库模板数据时，发布后执行 `bootstrap_game_data --skip-images` 或对应单项导入命令。
- 对于未纳入统一热刷新的运行期文件，发布后仍应重启相关进程，避免旧进程持有旧缓存。

## 图片相关注意事项

- 模板导入命令支持 `--skip-images`，适合 CI、无媒体文件环境或仅验证数据结构时使用。
- 真正依赖图片拷贝到 `media/` 时，请在具备完整 `data/images/` 资源的环境执行不带 `--skip-images` 的导入。
- 不要把大型压缩包或临时归档文件一并塞进仓库；原始图片资源和发布归档要分开管理。
