"""
铁匠铺装备锻造服务模块

提供装备锻造相关功能。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Dict, List

from django.conf import settings

from core.utils.yaml_loader import load_yaml_data

from ...models import EquipmentProduction, InventoryItem, ItemTemplate, Manor
from ..recruitment.templates import load_troop_templates
from ..technology import get_player_technology_level
from . import blueprint_catalog as _blueprint_catalog
from . import forge_blueprints as _forge_blueprints
from . import forge_decompose as _forge_decompose
from . import forge_runtime as _forge_runtime
from .forge_config_helpers import (
    DEFAULT_FORGE_BLUEPRINT_CONFIG,
    DEFAULT_FORGE_DECOMPOSE_CONFIG,
    DEFAULT_FORGE_EQUIPMENT_CONFIG,
    _normalize_blueprint_config,
    _normalize_decompose_config,
    _normalize_equipment_config,
)
from .forge_decompose_helpers import random, roll_decompose_rewards
from .forge_flow_helpers import build_equipment_options, build_filtered_equipment_configs, load_material_quantity_map
from .forge_helpers import (
    DECOMPOSE_CATEGORIES,
    build_equipment_option,
    build_inventory_quantity_map,
    collect_material_keys,
)
from .forge_helpers import infer_equipment_category as _infer_equipment_category
from .forge_helpers import to_decompose_category as _to_decompose_category

logger = logging.getLogger(__name__)


# 装备配置
# 锻造技等级需求：1,3,5,7,9级分别解锁不同等级装备
# 材料：铜、锡、铁
@lru_cache(maxsize=1)
def load_forge_equipment_config() -> Dict[str, Dict[str, Any]]:
    """加载铁匠铺装备锻造配置。"""
    path = os.path.join(settings.BASE_DIR, "data", "forge_equipment.yaml")
    raw = load_yaml_data(
        path,
        logger=logger,
        context="forge equipment config",
        default={"equipment": DEFAULT_FORGE_EQUIPMENT_CONFIG},
    )
    return _normalize_equipment_config(raw)


def clear_forge_equipment_cache() -> None:
    """清理铁匠铺装备配置缓存。"""
    global EQUIPMENT_CONFIG
    load_forge_equipment_config.cache_clear()
    EQUIPMENT_CONFIG = load_forge_equipment_config()


EQUIPMENT_CONFIG: Dict[str, Dict[str, Any]] = load_forge_equipment_config()


# 装备类别
EQUIPMENT_CATEGORIES = {
    "helmet": "头盔",
    "armor": "衣服",
    "shoes": "鞋子",
    "sword": "剑",
    "dao": "刀",
    "spear": "枪",
    "bow": "弓",
    "whip": "鞭",
}

# 材料名称兜底映射（优先从 ItemTemplate 读取名称）。
# 若模板缺失，则直接回退为 key，避免在代码中维护第二份名称来源。
MATERIAL_NAMES: Dict[str, str] = {}


def _get_item_name_map(keys: set[str]) -> Dict[str, str]:
    if not keys:
        return {}
    return {tpl.key: tpl.name for tpl in ItemTemplate.objects.filter(key__in=keys).only("key", "name")}


def infer_equipment_category(item_key: str, effect_type: str | None = None) -> str | None:
    return _infer_equipment_category(item_key, effect_type, equipment_config=EQUIPMENT_CONFIG)


def to_decompose_category(equipment_category: str | None) -> str | None:
    return _to_decompose_category(equipment_category)


@lru_cache(maxsize=1)
def load_forge_decompose_config() -> Dict[str, Any]:
    """加载铁匠铺分解配置。"""
    path = os.path.join(settings.BASE_DIR, "data", "forge_decompose.yaml")
    raw = load_yaml_data(
        path,
        logger=logger,
        context="forge decompose config",
        default=DEFAULT_FORGE_DECOMPOSE_CONFIG,
    )
    return _normalize_decompose_config(raw)


def clear_forge_decompose_cache() -> None:
    """清理铁匠铺分解配置缓存。"""
    load_forge_decompose_config.cache_clear()


@lru_cache(maxsize=1)
def load_forge_blueprint_config() -> Dict[str, Any]:
    """加载铁匠铺图纸合成配置。"""
    path = os.path.join(settings.BASE_DIR, "data", "forge_blueprints.yaml")
    raw = load_yaml_data(
        path,
        logger=logger,
        context="forge blueprint config",
        default=DEFAULT_FORGE_BLUEPRINT_CONFIG,
    )
    return _normalize_blueprint_config(raw)


@lru_cache(maxsize=1)
def _build_blueprint_recipe_index() -> Dict[str, Dict[str, Any]]:
    return _forge_blueprints.build_blueprint_recipe_index(load_forge_blueprint_config())


@lru_cache(maxsize=1)
def load_blueprint_catalog() -> dict[str, _blueprint_catalog.BlueprintCatalogEntry]:
    """加载并校验图纸与装备的统一目录。"""
    return _blueprint_catalog.build_blueprint_catalog(load_forge_blueprint_config())


def clear_forge_blueprint_cache() -> None:
    """清理铁匠铺图纸配置缓存。"""
    load_forge_blueprint_config.cache_clear()
    _build_blueprint_recipe_index.cache_clear()
    load_blueprint_catalog.cache_clear()


def get_blueprint_synthesis_options(manor: Manor) -> List[Dict[str, Any]]:
    """获取可显示的图纸合成卡片（仅显示玩家仓库中拥有图纸的配方）。"""
    return _forge_blueprints.get_blueprint_synthesis_options(manor, config=load_forge_blueprint_config())


def synthesize_equipment_with_blueprint(manor: Manor, blueprint_key: str, quantity: int = 1) -> Dict[str, Any]:
    """按图纸合成装备。"""
    return _forge_blueprints.synthesize_equipment_with_blueprint(
        manor,
        blueprint_key,
        quantity=quantity,
        recipe_index=_build_blueprint_recipe_index(),
    )


@lru_cache(maxsize=1)
def get_recruitment_equipment_keys() -> set[str]:
    """获取用于募兵的装备 key 集合（这些装备不可分解）。"""
    return _forge_decompose.collect_recruitment_equipment_keys(load_troop_templates=load_troop_templates)


def get_decomposable_equipment_options(manor: Manor, category: str | None = None) -> List[Dict[str, Any]]:
    """
    获取可分解装备列表。

    规则：
    - 必须是装备（effect_type 以 equip_ 开头）
    - 稀有度为绿色及以上
    - 不在募兵装备列表中
    """
    return _forge_decompose.get_decomposable_equipment_options(
        manor,
        category=category,
        config=load_forge_decompose_config(),
        recruit_equipment_keys=get_recruitment_equipment_keys(),
        infer_equipment_category=infer_equipment_category,
        to_decompose_category=to_decompose_category,
        category_labels=DECOMPOSE_CATEGORIES,
    )


def _roll_decompose_rewards(rarity: str, quantity: int, config: Dict[str, Any]) -> Dict[str, int]:
    return _forge_decompose.roll_decompose_rewards(
        rarity,
        quantity,
        config,
        reward_roller=roll_decompose_rewards,
        randint_func=random.randint,
        random_func=random.random,
    )


def decompose_equipment(manor: Manor, equipment_key: str, quantity: int = 1) -> Dict[str, Any]:
    """
    分解装备并发放材料。

    Args:
        manor: 庄园实例
        equipment_key: 装备 key
        quantity: 分解数量

    Returns:
        {
            "equipment_key": str,
            "equipment_name": str,
            "quantity": int,
            "rewards": dict[str, int],
        }
    """
    return _forge_decompose.decompose_equipment(
        manor,
        equipment_key,
        quantity=quantity,
        recruit_equipment_keys=get_recruitment_equipment_keys(),
        config=load_forge_decompose_config(),
        roll_decompose_rewards=_roll_decompose_rewards,
    )


def get_forge_speed_bonus(manor: Manor) -> float:
    """
    获取铁匠铺速度加成。

    10级满级提升50%，每级约5%。

    Args:
        manor: 庄园实例

    Returns:
        速度加成倍率（如0.5表示减少50%时间）
    """
    return _forge_runtime.get_forge_speed_bonus(manor)


def get_max_forging_quantity(manor: Manor) -> int:
    """
    获取单次锻造装备的最大数量。

    锻造技每级增加50件上限，满级9级=450件。

    Args:
        manor: 庄园实例

    Returns:
        最大锻造数量
    """
    return _forge_runtime.get_max_forging_quantity(manor)


def calculate_forging_duration(base_duration: int, manor: Manor) -> int:
    """
    计算实际锻造时间。

    Args:
        base_duration: 基础时间（秒）
        manor: 庄园实例

    Returns:
        实际锻造时间（秒）
    """
    return _forge_runtime.calculate_forging_duration(base_duration, manor)


def has_active_forging(manor: Manor) -> bool:
    """
    检查是否有正在进行的装备锻造。

    Args:
        manor: 庄园实例

    Returns:
        是否有锻造中的装备
    """
    return _forge_runtime.has_active_forging(manor)


def get_equipment_options(manor: Manor, category: str = None) -> List[Dict[str, Any]]:
    """
    获取装备锻造选项列表。

    Args:
        manor: 庄园实例
        category: 可选的装备类别过滤

    Returns:
        装备选项列表
    """
    forging_level = get_player_technology_level(manor, "forging")
    max_quantity = get_max_forging_quantity(manor)
    is_forging = has_active_forging(manor)
    filtered_configs = build_filtered_equipment_configs(equipment_config=EQUIPMENT_CONFIG, category=category)

    equipment_keys: set[str] = {equip_key for equip_key, _ in filtered_configs}
    material_keys = collect_material_keys(filtered_configs)
    item_name_map = _get_item_name_map(equipment_keys | material_keys)
    material_quantities = load_material_quantity_map(
        inventory_item_model=InventoryItem,
        manor=manor,
        material_keys=material_keys,
        build_inventory_quantity_map=build_inventory_quantity_map,
    )

    return build_equipment_options(
        manor=manor,
        filtered_configs=filtered_configs,
        item_name_map=item_name_map,
        material_quantities=material_quantities,
        material_name_fallback_map=MATERIAL_NAMES,
        equipment_categories=EQUIPMENT_CATEGORIES,
        calculate_forging_duration=calculate_forging_duration,
        build_equipment_option=build_equipment_option,
        forging_level=forging_level,
        max_quantity=max_quantity,
        is_forging=is_forging,
    )


def get_equipment_by_category(manor: Manor) -> Dict[str, Dict[str, Any]]:
    """
    按类别分组获取装备选项。

    Args:
        manor: 庄园实例

    Returns:
        按类别分组的装备选项
    """
    all_options = get_equipment_options(manor)
    grouped = {}
    for category_key, category_name in EQUIPMENT_CATEGORIES.items():
        grouped[category_key] = {
            "name": category_name,
            "items": [opt for opt in all_options if opt["category"] == category_key],
        }
    return grouped


def start_equipment_forging(manor: Manor, equipment_key: str, quantity: int = 1) -> EquipmentProduction:
    """
    开始锻造装备。

    Args:
        manor: 庄园实例
        equipment_key: 装备key
        quantity: 锻造数量

    Returns:
        EquipmentProduction实例

    Raises:
        ForgeOperationError: 参数错误、材料不足、科技等级不足或已有锻造进行中
    """
    return _forge_runtime.start_equipment_forging(
        manor,
        equipment_key,
        quantity=quantity,
        equipment_config=EQUIPMENT_CONFIG,
        material_name_fallback_map=MATERIAL_NAMES,
    )


def cancel_equipment_forging(manor: Manor, production_id: int) -> EquipmentProduction:
    """取消仍在进行的装备锻造，已消耗的材料不返还。"""
    return _forge_runtime.cancel_equipment_forging(manor, production_id)


def _schedule_forging_completion(production: EquipmentProduction, eta_seconds: int) -> None:
    """
    调度锻造完成任务。

    Args:
        production: EquipmentProduction实例
        eta_seconds: 预计完成时间（秒）
    """
    _forge_runtime.schedule_forging_completion(production, eta_seconds)


def finalize_equipment_forging(production: EquipmentProduction, send_notification: bool = False) -> bool:
    """
    完成装备锻造，将装备添加到玩家仓库。

    Args:
        production: EquipmentProduction实例
        send_notification: 是否发送通知

    Returns:
        是否成功完成
    """
    return _forge_runtime.finalize_equipment_forging(
        production,
        send_notification=send_notification,
    )


def refresh_equipment_forgings(manor: Manor) -> int:
    """
    刷新装备锻造状态，完成所有到期的锻造。

    Args:
        manor: 庄园实例

    Returns:
        完成的锻造数量
    """
    return _forge_runtime.refresh_equipment_forgings(manor)


def get_active_forgings(manor: Manor) -> List[EquipmentProduction]:
    """
    获取正在进行的锻造列表。

    Args:
        manor: 庄园实例

    Returns:
        锻造列表
    """
    return _forge_runtime.get_active_forgings(manor, equipment_production_model=EquipmentProduction)
