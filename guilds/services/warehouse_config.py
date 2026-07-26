"""
帮会仓库科技产出配置加载器

将产出配置从代码中提取到 YAML 文件，便于维护和调整。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List

from django.conf import settings

from core.utils import safe_int, safe_non_negative_int, safe_positive_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data

WAREHOUSE_PRODUCTION_PATH = settings.BASE_DIR / "data" / "warehouse_production.yaml"
logger = logging.getLogger(__name__)


@dataclass
class ProductionItem:
    """单个产出物品配置"""

    item_key: str
    quantity: int
    contribution_cost: int
    weekly_personal_limit: int = 0


@dataclass
class TechProduction:
    """科技产出配置"""

    tech_key: str
    levels: Dict[int, List[ProductionItem]]

    def get_items(self, level: int) -> List[ProductionItem]:
        """获取指定等级的产出物品列表"""
        return self.levels.get(level, [])


def load_warehouse_production() -> Dict[str, TechProduction]:
    """加载仓库产出配置"""
    raw = load_yaml_data(
        WAREHOUSE_PRODUCTION_PATH,
        logger=logger,
        context="guild warehouse production config",
        default={},
    )
    data = ensure_mapping(raw, logger=logger, context="warehouse production root")

    result: Dict[str, TechProduction] = {}

    for tech_key_raw, tech_data_raw in data.items():
        tech_key = str(tech_key_raw or "").strip()
        if not tech_key:
            continue
        tech_data = ensure_mapping(tech_data_raw, logger=logger, context=f"warehouse production [{tech_key}]")
        levels_data = ensure_mapping(tech_data.get("levels"), logger=logger, context=f"{tech_key}.levels")
        levels: Dict[int, List[ProductionItem]] = {}

        for level_raw, items_raw in levels_data.items():
            level_int = safe_int(level_raw, default=None)
            if level_int is None or level_int < 0:
                logger.warning("Skip invalid warehouse production level: tech_key=%s level=%r", tech_key, level_raw)
                continue

            items_list = ensure_list(items_raw, logger=logger, context=f"{tech_key}.levels[{level_int}]")
            normalized_items: List[ProductionItem] = []
            for item_raw in items_list:
                item = ensure_mapping(item_raw, logger=logger, context=f"{tech_key}.levels[{level_int}].item")
                if not item:
                    continue
                item_key = str(item.get("item_key") or "").strip()
                if not item_key:
                    continue
                quantity = safe_positive_int(item.get("quantity"), 1)
                contribution_cost = safe_non_negative_int(item.get("contribution_cost"))
                weekly_personal_limit = safe_non_negative_int(item.get("weekly_personal_limit"))
                normalized_items.append(
                    ProductionItem(
                        item_key=item_key,
                        quantity=quantity,
                        contribution_cost=contribution_cost,
                        weekly_personal_limit=weekly_personal_limit,
                    )
                )
            levels[level_int] = normalized_items

        result[tech_key] = TechProduction(tech_key=tech_key, levels=levels)

    return result


@lru_cache(maxsize=1)
def _load_warehouse_production_cached() -> Dict[str, TechProduction]:
    """缓存版本的配置加载"""
    return load_warehouse_production()


def get_warehouse_production() -> Dict[str, TechProduction]:
    """获取仓库产出配置（带缓存）"""
    return _load_warehouse_production_cached()


def reload_warehouse_production() -> None:
    """重新加载配置（清除缓存）"""
    _load_warehouse_production_cached.cache_clear()


def get_tech_production(tech_key: str) -> TechProduction | None:
    """获取指定科技的产出配置"""
    return get_warehouse_production().get(tech_key)


def get_production_items(tech_key: str, level: int) -> List[ProductionItem]:
    """获取指定科技指定等级的产出物品列表"""
    tech = get_tech_production(tech_key)
    if tech:
        return tech.get_items(level)
    return []


def get_warehouse_production_item_keys() -> frozenset[str]:
    """获取所有帮会科技会产出的仓库物品键。"""
    return frozenset(
        item.item_key
        for production in get_warehouse_production().values()
        for items in production.levels.values()
        for item in items
        if item.item_key
    )


def get_weekly_personal_limit(item_key: str) -> int:
    """获取物品每周个人兑换上限；0 表示不限购。"""
    normalized_key = str(item_key or "").strip()
    if not normalized_key:
        return 0
    limits: list[int] = []
    for production in get_warehouse_production().values():
        for items in production.levels.values():
            for item in items:
                if item.item_key == normalized_key and item.weekly_personal_limit > 0:
                    limits.append(item.weekly_personal_limit)
    return max(limits, default=0)
