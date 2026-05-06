"""
拍卖行配置加载服务

提供YAML配置文件的加载和缓存功能，参考 shop_config.py 的实现模式。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

from django.conf import settings

from core.utils import safe_float, safe_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data

AUCTION_CONFIG_PATH = settings.BASE_DIR / "data" / "auction_items.yaml"
logger = logging.getLogger(__name__)


@dataclass
class AuctionSettings:
    """拍卖行全局设置"""

    cycle_days: int  # 拍卖周期（天）
    min_increment_ratio: float  # 最小加价幅度比例
    default_min_increment: int  # 默认最小加价幅度


@dataclass
class AuctionItemConfig:
    """拍卖商品配置"""

    item_key: str
    slots: int  # 拆分成多少个独立拍卖位
    quantity_per_slot: int  # 每个拍卖位的物品数量
    starting_price: int  # 起拍价（金条）
    min_increment: int  # 最小加价幅度（金条）
    enabled: bool  # 是否启用


@dataclass
class AuctionConfig:
    """拍卖行完整配置"""

    settings: AuctionSettings
    items: List[AuctionItemConfig]


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return default


def load_auction_config() -> AuctionConfig:
    """加载拍卖行配置"""
    data = load_yaml_data(
        AUCTION_CONFIG_PATH,
        logger=logger,
        context="auction config",
        default={},
    )
    payload = ensure_mapping(data, logger=logger, context="auction config root")

    # 解析全局设置
    settings_data = ensure_mapping(payload.get("settings"), logger=logger, context="auction config settings")
    auction_settings = AuctionSettings(
        cycle_days=safe_int(settings_data.get("cycle_days"), default=3, min_val=1),
        min_increment_ratio=safe_float(settings_data.get("min_increment_ratio"), default=0.1, min_val=0.0),
        default_min_increment=safe_int(settings_data.get("default_min_increment"), default=1, min_val=1),
    )

    # 解析商品列表
    items_data = ensure_list(payload.get("items"), logger=logger, context="auction config items")
    items = []

    for raw_item in items_data:
        item = ensure_mapping(raw_item, logger=logger, context="auction config item")
        if not item:
            continue
        item_key = str(item.get("item_key") or "").strip()
        if not item_key:
            continue

        config = AuctionItemConfig(
            item_key=item_key,
            slots=safe_int(item.get("slots"), default=1, min_val=1),
            quantity_per_slot=safe_int(item.get("quantity_per_slot"), default=1, min_val=1),
            starting_price=safe_int(item.get("starting_price"), default=1, min_val=1),
            min_increment=safe_int(
                item.get("min_increment"),
                default=auction_settings.default_min_increment,
                min_val=1,
            ),
            enabled=_coerce_bool(item.get("enabled"), default=True),
        )
        items.append(config)

    return AuctionConfig(settings=auction_settings, items=items)


@lru_cache(maxsize=1)
def _load_auction_config_cached() -> AuctionConfig:
    """缓存版本的配置加载"""
    return load_auction_config()


def get_auction_config() -> AuctionConfig:
    """获取拍卖行配置（带缓存）"""
    return _load_auction_config_cached()


def reload_auction_config() -> None:
    """重新加载配置（清除缓存）"""
    _load_auction_config_cached.cache_clear()


def get_auction_settings() -> AuctionSettings:
    """获取拍卖行全局设置"""
    return get_auction_config().settings


def get_enabled_auction_items() -> List[AuctionItemConfig]:
    """获取启用的拍卖商品列表"""
    return [item for item in get_auction_config().items if item.enabled]


def get_auction_item_config(item_key: str) -> Optional[AuctionItemConfig]:
    """获取单个拍卖商品配置"""
    for item in get_auction_config().items:
        if item.item_key == item_key:
            return item
    return None
