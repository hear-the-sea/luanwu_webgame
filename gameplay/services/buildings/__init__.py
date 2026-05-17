"""
建筑系统服务模块

本模块包含所有建筑相关的服务：
- base: 建筑配置和模板加载
- forge: 锻造房（装备生产）
- smithy: 铁匠铺（金属冶炼）
- stable: 马厩（马匹生产）
- ranch: 牧场（牲畜养殖）
"""

from __future__ import annotations

# 建筑配置（base.py）
from .base import (
    clear_building_cache,
    get_all_buildings,
    get_building_categories,
    get_building_config,
    get_building_description,
    get_buildings_by_category,
    load_building_templates,
)

# 锻造房（forge.py）
from .forge import (
    calculate_forging_duration,
    finalize_equipment_forging,
    get_equipment_by_category,
    get_equipment_options,
    get_forge_speed_bonus,
    get_max_forging_quantity,
    has_active_forging,
    refresh_equipment_forgings,
    start_equipment_forging,
)

# 牧场（ranch.py）
from .ranch import (
    calculate_livestock_duration,
    finalize_livestock_production,
    get_active_livestock_productions,
    get_livestock_options,
    get_max_livestock_quantity,
    get_ranch_speed_bonus,
    has_active_livestock_production,
    refresh_livestock_productions,
    start_livestock_production,
)

# 铁匠铺（smithy.py）
from .smithy import (
    calculate_smelting_duration,
    finalize_smelting_production,
    get_active_smelting_productions,
    get_max_smelting_quantity,
    get_max_smithy_medicine_quantity,
    get_metal_options,
    get_smithy_speed_bonus,
    has_active_smelting_production,
    refresh_smelting_productions,
    start_smelting_production,
)

# 马厩（stable.py）
from .stable import (
    calculate_production_duration,
    finalize_horse_production,
    get_active_productions,
    get_horse_options,
    get_max_production_quantity,
    get_stable_speed_bonus,
    has_active_production,
    refresh_horse_productions,
    start_horse_production,
)

__all__ = [
    # base
    "clear_building_cache",
    "get_all_buildings",
    "get_building_categories",
    "get_building_config",
    "get_building_description",
    "get_buildings_by_category",
    "load_building_templates",
    # forge
    "calculate_forging_duration",
    "finalize_equipment_forging",
    "get_equipment_by_category",
    "get_equipment_options",
    "get_forge_speed_bonus",
    "get_max_forging_quantity",
    "has_active_forging",
    "refresh_equipment_forgings",
    "start_equipment_forging",
    # smithy
    "calculate_smelting_duration",
    "finalize_smelting_production",
    "get_active_smelting_productions",
    "get_max_smithy_medicine_quantity",
    "get_max_smelting_quantity",
    "get_metal_options",
    "get_smithy_speed_bonus",
    "has_active_smelting_production",
    "refresh_smelting_productions",
    "start_smelting_production",
    # stable
    "calculate_production_duration",
    "finalize_horse_production",
    "get_active_productions",
    "get_horse_options",
    "get_max_production_quantity",
    "get_stable_speed_bonus",
    "has_active_production",
    "refresh_horse_productions",
    "start_horse_production",
    # ranch
    "calculate_livestock_duration",
    "finalize_livestock_production",
    "get_active_livestock_productions",
    "get_livestock_options",
    "get_max_livestock_quantity",
    "get_ranch_speed_bonus",
    "has_active_livestock_production",
    "refresh_livestock_productions",
    "start_livestock_production",
]
