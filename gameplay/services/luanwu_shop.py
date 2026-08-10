"""
乱舞商城服务。

商城货币使用仓库中的春秋币物品行，商城奖励也统一写入仓库，避免引入
另一套货币或库存账本。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from random import SystemRandom
from typing import Any

from django.db import transaction

from core.exceptions import GameError, ItemNotFoundError
from gameplay.models import ItemTemplate, Manor
from gameplay.services.buildings.forge import load_blueprint_catalog
from gameplay.services.inventory.core import (
    add_item_to_inventory_locked,
    consume_inventory_item_for_manor_locked,
    get_item_quantity,
)

logger = logging.getLogger(__name__)

CHUNQIU_COIN_ITEM_KEY = "chunqiu_coin"
RANDOM_DEVICE_BLUEPRINT_PRODUCT_KEY = "device_blueprint"
LUANWU_SHOP_MAX_PURCHASE_QUANTITY = 100


@dataclass(frozen=True, slots=True)
class LuanwuShopProductConfig:
    """商城商品的静态配置。"""

    key: str
    name: str
    description: str
    price: int
    item_key: str | None = None
    reward_quantity: int = 1
    is_random_device_blueprint: bool = False
    shop_description: str | None = None


LUANWU_SHOP_PRODUCTS: tuple[LuanwuShopProductConfig, ...] = (
    LuanwuShopProductConfig(
        key="fangdajing",
        name="放大镜",
        description="招募候选区使用的侦鉴道具，帮助你看清门客的稀有度。",
        price=1,
        item_key="fangdajing",
        reward_quantity=10,
    ),
    LuanwuShopProductConfig(
        key="mission_card",
        name="任务卡",
        description="在任务详情中使用，为指定任务增加今日挑战次数。",
        price=1,
        item_key="mission_card",
        reward_quantity=2,
    ),
    LuanwuShopProductConfig(
        key="recruitment_card",
        name="招募卡",
        description="在聚贤庄卡池旁使用，为指定卡池增加今日招募次数。",
        price=1,
        item_key="recruitment_card",
        reward_quantity=3,
        shop_description="用于增加每日招募次数",
    ),
    LuanwuShopProductConfig(
        key=RANDOM_DEVICE_BLUEPRINT_PRODUCT_KEY,
        name="机关图纸宝箱",
        description="从全部器械图纸中随机获得一张，入库后可前往铁匠铺合成对应器械。",
        price=10,
        is_random_device_blueprint=True,
    ),
)

_PRODUCTS_BY_KEY = {product.key: product for product in LUANWU_SHOP_PRODUCTS}


def get_luanwu_shop_product(product_key: str) -> LuanwuShopProductConfig | None:
    """按页面提交的 key 获取商城商品。"""

    return _PRODUCTS_BY_KEY.get(str(product_key or "").strip())


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
    """购买乱舞商城商品，并原子扣除仓库春秋币、发放仓库奖励。"""

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
        CHUNQIU_COIN_ITEM_KEY,
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
    return {
        "product_key": product.key,
        "product_name": product.name,
        "quantity": normalized_quantity,
        "total_cost": total_cost,
        "granted_items": grants,
        "granted_item_names": names,
        "reward_summary": _build_reward_summary(grants, names),
        "currency_remaining": get_item_quantity(locked_manor, CHUNQIU_COIN_ITEM_KEY),
    }


def build_luanwu_shop_context(manor: Manor) -> dict[str, object]:
    """构建乱舞商城页面展示上下文。"""

    currency_quantity = get_item_quantity(manor, CHUNQIU_COIN_ITEM_KEY)
    device_blueprints = get_device_blueprint_templates()
    templates = {
        template.key: template
        for template in ItemTemplate.objects.filter(
            key__in=[product.item_key for product in LUANWU_SHOP_PRODUCTS if product.item_key]
        )
    }

    products: list[dict[str, object]] = []
    for product in LUANWU_SHOP_PRODUCTS:
        template = templates.get(product.item_key) if product.item_key else None
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
                "name": template.name if template else product.name,
                "description": (
                    product.shop_description
                    or (template.description if template and template.description else product.description)
                ),
                "price": product.price,
                "reward_quantity": product.reward_quantity,
                "is_random_device_blueprint": product.is_random_device_blueprint,
                "template": template,
                "rarity": template.rarity if template else "purple",
                "mark": "机" if product.is_random_device_blueprint else product.name[:1],
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
        "chunqiu_coin_item_key": CHUNQIU_COIN_ITEM_KEY,
        "device_blueprint_count": len(device_blueprints),
    }
