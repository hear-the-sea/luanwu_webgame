"""
乱舞商城服务。

商城货币使用配置指定的仓库物品行（默认春秋币），商城奖励也统一写入仓库，避免引入
另一套货币或库存账本。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from random import SystemRandom
from typing import Any

from django.conf import settings
from django.db import transaction

from core.exceptions import GameError, ItemNotFoundError
from core.utils.yaml_loader import load_yaml_data
from gameplay.models import ItemTemplate, Manor
from gameplay.services.buildings.forge import load_blueprint_catalog
from gameplay.services.inventory.core import (
    add_item_to_inventory_locked,
    consume_inventory_item_for_manor_locked,
    get_item_quantity,
)

logger = logging.getLogger(__name__)

CHUNQIU_COIN_ITEM_KEY = "chunqiu_coin"
LUANWU_SHOP_MAX_PURCHASE_QUANTITY = 100
LUANWU_SHOP_CONFIG_PATH = settings.BASE_DIR / "data" / "luanwu_shop.yaml"
DEFAULT_LUANWU_SHOP_CONFIG = {
    "currency_item_key": CHUNQIU_COIN_ITEM_KEY,
    "items": [],
}


@dataclass(frozen=True, slots=True)
class LuanwuShopProductConfig:
    """商城商品配置。"""

    key: str
    name: str | None
    description: str | None
    price: int
    item_key: str | None = None
    reward_quantity: int = 1
    is_random_device_blueprint: bool = False
    shop_description: str | None = None


@dataclass(frozen=True, slots=True)
class LuanwuShopConfig:
    """乱舞商城完整配置。"""

    currency_item_key: str
    products: tuple[LuanwuShopProductConfig, ...]


def _normalize_luanwu_shop_string(raw_value: Any, *, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid luanwu shop {field_name}: {raw_value!r}")
    return raw_value.strip()


def _normalize_optional_luanwu_shop_string(raw_value: Any, *, field_name: str) -> str | None:
    if raw_value is None:
        return None
    return _normalize_luanwu_shop_string(raw_value, field_name=field_name)


def _normalize_luanwu_shop_positive_int(raw_value: Any, *, field_name: str) -> int:
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise AssertionError(f"invalid luanwu shop {field_name}: {raw_value!r}")
    if raw_value <= 0:
        raise AssertionError(f"invalid luanwu shop {field_name}: {raw_value!r}")
    return raw_value


def _normalize_luanwu_shop_config(raw: Any) -> LuanwuShopConfig:
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid luanwu shop config root: {raw!r}")

    currency_item_key = _normalize_luanwu_shop_string(
        raw.get("currency_item_key"),
        field_name="currency_item_key",
    )
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise AssertionError(f"invalid luanwu shop items: {raw_items!r}")

    products: list[LuanwuShopProductConfig] = []
    product_keys: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        field_prefix = f"items[{index}]"
        if not isinstance(raw_item, dict):
            raise AssertionError(f"invalid luanwu shop item {field_prefix}: {raw_item!r}")

        product_key = _normalize_luanwu_shop_string(raw_item.get("key"), field_name=f"{field_prefix}.key")
        if product_key in product_keys:
            raise AssertionError(f"duplicate luanwu shop product key: {product_key}")
        product_keys.add(product_key)

        reward_type = _normalize_luanwu_shop_string(
            raw_item.get("reward_type"),
            field_name=f"{field_prefix}.reward_type",
        )
        if reward_type not in {"item", "random_device_blueprint"}:
            raise AssertionError(f"invalid luanwu shop {field_prefix}.reward_type: {reward_type!r}")

        raw_item_key = raw_item.get("item_key")
        item_key = (
            None
            if raw_item_key is None
            else _normalize_luanwu_shop_string(raw_item_key, field_name=f"{field_prefix}.item_key")
        )
        if reward_type == "item" and item_key is None:
            raise AssertionError(f"invalid luanwu shop {field_prefix}.item_key: {raw_item_key!r}")
        if reward_type == "random_device_blueprint" and item_key is not None:
            raise AssertionError(f"invalid luanwu shop {field_prefix}.item_key: random reward cannot set item_key")

        name = _normalize_optional_luanwu_shop_string(raw_item.get("name"), field_name=f"{field_prefix}.name")
        description = _normalize_optional_luanwu_shop_string(
            raw_item.get("description"),
            field_name=f"{field_prefix}.description",
        )
        if reward_type == "item":
            if name is not None:
                raise AssertionError(f"invalid luanwu shop {field_prefix}.name: fixed item uses ItemTemplate.name")
            if description is not None:
                raise AssertionError(
                    f"invalid luanwu shop {field_prefix}.description: fixed item uses ItemTemplate.description"
                )
            reward_quantity = _normalize_luanwu_shop_positive_int(
                raw_item.get("reward_quantity"),
                field_name=f"{field_prefix}.reward_quantity",
            )
        else:
            if name is None:
                raise AssertionError(f"invalid luanwu shop {field_prefix}.name: random reward requires a name")
            if description is None:
                raise AssertionError(
                    f"invalid luanwu shop {field_prefix}.description: random reward requires a description"
                )
            if "reward_quantity" in raw_item:
                raise AssertionError(
                    f"invalid luanwu shop {field_prefix}.reward_quantity: random reward does not use quantity"
                )
            reward_quantity = 1

        raw_shop_description = raw_item.get("shop_description")
        shop_description = (
            None
            if raw_shop_description is None
            else _normalize_luanwu_shop_string(
                raw_shop_description,
                field_name=f"{field_prefix}.shop_description",
            )
        )
        products.append(
            LuanwuShopProductConfig(
                key=product_key,
                name=name,
                description=description,
                price=_normalize_luanwu_shop_positive_int(
                    raw_item.get("price"),
                    field_name=f"{field_prefix}.price",
                ),
                item_key=item_key,
                reward_quantity=reward_quantity,
                is_random_device_blueprint=reward_type == "random_device_blueprint",
                shop_description=shop_description,
            )
        )

    return LuanwuShopConfig(currency_item_key=currency_item_key, products=tuple(products))


@lru_cache(maxsize=1)
def load_luanwu_shop_config() -> LuanwuShopConfig:
    """从 YAML 加载乱舞商城配置。"""

    raw = load_yaml_data(
        LUANWU_SHOP_CONFIG_PATH,
        logger=logger,
        context="luanwu shop config",
        default=DEFAULT_LUANWU_SHOP_CONFIG,
    )
    return _normalize_luanwu_shop_config(raw)


def clear_luanwu_shop_config_cache() -> None:
    """清理乱舞商城配置缓存。"""

    load_luanwu_shop_config.cache_clear()


def get_luanwu_shop_product(product_key: str) -> LuanwuShopProductConfig | None:
    """按页面提交的 key 获取商城商品。"""

    normalized_key = str(product_key or "").strip()
    return next(
        (product for product in load_luanwu_shop_config().products if product.key == normalized_key),
        None,
    )


def _get_device_blueprint_keys() -> list[str]:
    """返回所有能合成器械的图纸 key。"""

    catalog = load_blueprint_catalog()
    if not catalog:
        return []

    result_keys = {entry.result_key for entry in catalog.values()}
    device_result_keys = set(
        ItemTemplate.objects.filter(
            key__in=result_keys,
            effect_type="equip_device",
        ).values_list("key", flat=True)
    )
    blueprint_keys = set(ItemTemplate.objects.filter(key__in=catalog.keys()).values_list("key", flat=True))
    return sorted(
        blueprint_key
        for blueprint_key, entry in catalog.items()
        if blueprint_key in blueprint_keys and entry.result_key in device_result_keys
    )


def get_device_blueprint_templates() -> list[ItemTemplate]:
    """构建商城中用于展示的器械图纸列表。"""

    try:
        blueprint_keys = _get_device_blueprint_keys()
    except (AssertionError, AttributeError, KeyError, TypeError, ValueError) as exc:
        # ItemTemplate 尚未完成初始化时，商城仍应能打开；购买时会明确提示配置不可用。
        logger.warning("Unable to build luanwu shop blueprint pool: %s", exc, exc_info=True)
        return []

    if not blueprint_keys:
        return []
    templates = ItemTemplate.objects.filter(key__in=blueprint_keys).order_by("name", "key")
    return list(templates)


def select_random_device_blueprint_key(*, rng: Any | None = None) -> str:
    """从当前有效的器械图纸池中随机选择一张图纸。"""

    try:
        blueprint_keys = _get_device_blueprint_keys()
    except (AssertionError, AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.error("Invalid luanwu shop blueprint pool: %s", exc, exc_info=True)
        raise GameError("机关图纸配置异常，请联系管理员") from exc
    if not blueprint_keys:
        raise GameError("机关图纸暂无可兑换的器械图纸，请联系管理员")
    random_source = rng or SystemRandom()
    return str(random_source.choice(blueprint_keys))


def _normalize_purchase_quantity(quantity: int) -> int:
    if isinstance(quantity, bool):
        raise GameError("购买数量无效")
    try:
        normalized_quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise GameError("购买数量无效") from exc
    if normalized_quantity <= 0:
        raise GameError("购买数量无效")
    if normalized_quantity > LUANWU_SHOP_MAX_PURCHASE_QUANTITY:
        raise GameError(f"单次最多兑换{LUANWU_SHOP_MAX_PURCHASE_QUANTITY}份")
    return normalized_quantity


def _build_reward_summary(grants: dict[str, int], names: dict[str, str]) -> str:
    return "、".join(f"{names.get(item_key, item_key)}×{quantity}" for item_key, quantity in grants.items())


@transaction.atomic
def purchase_luanwu_shop_item(
    manor: Manor,
    product_key: str,
    quantity: int = 1,
    *,
    rng: Any | None = None,
) -> dict[str, object]:
    """购买乱舞商城商品，并原子扣除配置的商城货币、发放仓库奖励。"""

    shop_config = load_luanwu_shop_config()
    product = get_luanwu_shop_product(product_key)
    if product is None:
        raise ItemNotFoundError("商城商品不存在")
    normalized_quantity = _normalize_purchase_quantity(quantity)

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    total_cost = product.price * normalized_quantity
    # 先扣除并锁定货币，再执行随机奖励计算；随机配置异常时事务会整体回滚。
    # 这样余额不足或恶意超大请求都不会触发无意义的奖励池查询。
    consume_inventory_item_for_manor_locked(
        locked_manor,
        shop_config.currency_item_key,
        total_cost,
    )

    grants: dict[str, int] = {}
    if product.is_random_device_blueprint:
        for _ in range(normalized_quantity):
            selected_key = select_random_device_blueprint_key(rng=rng)
            grants[selected_key] = grants.get(selected_key, 0) + 1
    elif product.item_key:
        grants[product.item_key] = product.reward_quantity * normalized_quantity
    else:
        raise GameError("商城商品配置异常，请联系管理员")

    for item_key, grant_quantity in grants.items():
        add_item_to_inventory_locked(locked_manor, item_key, grant_quantity)

    names = dict(ItemTemplate.objects.filter(key__in=grants).values_list("key", "name"))
    product_name = product.name or names.get(product.item_key or "") or product.key
    return {
        "product_key": product.key,
        "product_name": product_name,
        "quantity": normalized_quantity,
        "total_cost": total_cost,
        "granted_items": grants,
        "granted_item_names": names,
        "reward_summary": _build_reward_summary(grants, names),
        "currency_remaining": get_item_quantity(locked_manor, shop_config.currency_item_key),
    }


def build_luanwu_shop_context(manor: Manor) -> dict[str, object]:
    """构建乱舞商城页面展示上下文。"""

    shop_config = load_luanwu_shop_config()
    currency_item_key = shop_config.currency_item_key
    currency_quantity = get_item_quantity(manor, currency_item_key)
    device_blueprints = get_device_blueprint_templates()
    templates = {
        template.key: template
        for template in ItemTemplate.objects.filter(
            key__in=[product.item_key for product in shop_config.products if product.item_key]
        )
    }

    products: list[dict[str, object]] = []
    for product in shop_config.products:
        template = templates.get(product.item_key) if product.item_key else None
        display_name = product.name or (template.name if template else product.key)
        display_description = (
            product.shop_description
            or (template.description if template and template.description else None)
            or product.description
            or ""
        )
        is_configured = bool(template) if product.item_key else bool(device_blueprints)
        max_quantity = (
            min(currency_quantity // product.price, LUANWU_SHOP_MAX_PURCHASE_QUANTITY) if product.price > 0 else 0
        )
        if product.is_random_device_blueprint and not device_blueprints:
            unavailable_reason = "当前没有可兑换的器械图纸"
        elif not is_configured:
            unavailable_reason = "商品配置暂未就绪"
        elif currency_quantity < product.price:
            unavailable_reason = "春秋币不足"
        else:
            unavailable_reason = ""

        products.append(
            {
                "key": product.key,
                "name": display_name,
                "description": display_description,
                "price": product.price,
                "reward_quantity": product.reward_quantity,
                "is_random_device_blueprint": product.is_random_device_blueprint,
                "template": template,
                "rarity": template.rarity if template else "purple",
                "mark": "机" if product.is_random_device_blueprint else display_name[:1],
                "category": "随机图纸" if product.is_random_device_blueprint else "商城道具",
                "preview_blueprints": device_blueprints if product.is_random_device_blueprint else (),
                "preview_count": len(device_blueprints) if product.is_random_device_blueprint else 0,
                "max_quantity": max_quantity,
                "can_purchase": is_configured and not unavailable_reason,
                "unavailable_reason": unavailable_reason,
            }
        )

    return {
        "luanwu_shop_products": products,
        "chunqiu_coin_quantity": currency_quantity,
        "chunqiu_coin_item_key": currency_item_key,
        "device_blueprint_count": len(device_blueprints),
    }
