"""装备首次产出的前缀判定与固定变体。

四种前缀的属性转换比例固定，因此同一基础模板的同名前缀可以共用变体
模板、沿用现有库存堆叠和交易身份。库存流转本身不调用随机生成逻辑。
"""

from __future__ import annotations

import copy
import hashlib
import random
from collections.abc import Mapping
from typing import Any

from django.db import transaction

from common.utils.random_utils import binomial_sample
from core.exceptions import ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate, Manor

AFFIX_CHANCE = 0.7
AFFIX_LABELS = {
    "agility": "敏捷之",
    "force": "力量之",
    "defense": "防御之",
    "intellect": "智力之",
}
AFFIX_STAT_KEYS = tuple(AFFIX_LABELS)
AFFIX_RARITIES = frozenset({"green", "blue", "purple", "orange"})
BASE_KEY_FIELD = "equipment_base_key"
BASE_NAME_FIELD = "equipment_base_name"
AFFIX_FIELD = "equipment_affix"


def get_or_create_item_template_for_gear(gear: Any) -> ItemTemplate:
    """为历史装备模板补齐完整库存表示，保留原有直接攻防加成。"""
    _require_atomic()
    slot = str(gear.slot)
    if slot not in {"helmet", "armor", "shoes", "weapon", "mount", "ornament", "device"}:
        raise AssertionError("invalid legacy equipment slot")
    payload = copy.deepcopy(gear.extra_stats or {})
    payload["required_level"] = int(getattr(gear, "required_level", 1))
    if gear.set_key:
        payload.update(
            set_key=gear.set_key, set_description=gear.set_description, set_bonus=copy.deepcopy(gear.set_bonus)
        )
    for name in ("attack_bonus", "defense_bonus"):
        value = getattr(gear, name)
        if value:
            payload[f"equipment_{name}"] = value
    base_key = getattr(gear, "base_key", "") or gear.key
    base_name = getattr(gear, "base_name", "") or gear.name
    if base_key != gear.key or base_name != gear.name:
        payload.update({BASE_KEY_FIELD: base_key, BASE_NAME_FIELD: base_name})
        for affix in AFFIX_LABELS:
            if gear.key == equipment_affix_key(base_key, affix):
                payload[AFFIX_FIELD] = affix
                break
    item, _ = ItemTemplate.objects.get_or_create(
        key=gear.key,
        defaults={
            "name": gear.name,
            "effect_type": f"equip_{slot}",
            "rarity": gear.rarity,
            "effect_payload": payload,
        },
    )
    return item


def _require_atomic() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("equipment reward generation requires transaction.atomic()")


def equipment_affix_key(base_key: str, affix: str) -> str:
    """稳定且满足现有 SlugField 长度的变体标识。"""
    if affix not in AFFIX_LABELS:
        raise AssertionError("unknown equipment affix")
    candidate = f"affix_{affix}__{base_key}"
    limit = ItemTemplate._meta.get_field("key").max_length or 50
    if len(candidate) <= limit:
        return candidate
    digest = hashlib.sha256(base_key.encode()).hexdigest()[:12]
    prefix = f"affix_{affix}__"
    return f"{prefix}{base_key[:limit-len(prefix)-13]}_{digest}"


def _stat_value(payload: Mapping[str, Any], stat: str) -> int:
    value = payload.get(stat, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssertionError(f"invalid equipment stat {stat}: {value!r}")
    return value


def build_equipment_affix_payload(base: ItemTemplate, affix: str) -> dict[str, Any] | None:
    """转换四维；没有可转移点数时返回 None，不生成空前缀。"""
    if affix not in AFFIX_LABELS:
        raise AssertionError("unknown equipment affix")
    if not isinstance(base.effect_payload, dict):
        raise AssertionError("equipment payload must be a mapping")
    payload = copy.deepcopy(base.effect_payload)
    gain = 0
    for stat in AFFIX_STAT_KEYS:
        value = _stat_value(payload, stat)
        if stat == affix:
            continue
        loss = value // 5
        if stat in payload:
            payload[stat] = value - loss
        gain += loss
    if not gain:
        return None
    payload[affix] = _stat_value(payload, affix) + gain
    payload[BASE_KEY_FIELD] = base.key
    payload[BASE_NAME_FIELD] = base.name
    payload[AFFIX_FIELD] = affix
    return payload


def equipment_can_gain_affix(template: ItemTemplate) -> bool:
    if template.rarity not in AFFIX_RARITIES:
        return False
    if template.effect_type not in {
        "equip_helmet",
        "equip_armor",
        "equip_shoes",
        "equip_weapon",
        "equip_mount",
        "equip_ornament",
    }:
        return False
    payload = template.effect_payload
    if not isinstance(payload, dict):
        raise AssertionError("equipment payload must be a mapping")
    if template.key.startswith("soulorn_") or payload.get(AFFIX_FIELD) or payload.get(BASE_KEY_FIELD):
        return False
    from trade.services.shop_config import get_shop_item_config

    return get_shop_item_config(template.key) is None


def _variant_defaults(base: ItemTemplate, affix: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": f"{AFFIX_LABELS[affix]}{base.name}",
        "description": base.description,
        "effect_type": base.effect_type,
        "effect_payload": payload,
        "icon": base.icon,
        "image": base.image.name if base.image else "",
        "rarity": base.rarity,
        "tradeable": base.tradeable,
        "default_bound": base.default_bound,
        "price": base.price,
        "storage_space": base.storage_space,
        "is_usable": base.is_usable,
    }


def get_or_create_equipment_affix_template(
    base: ItemTemplate,
    affix: str,
    *,
    payload: dict[str, Any] | None = None,
) -> ItemTemplate:
    _require_atomic()
    transformed = payload if payload is not None else build_equipment_affix_payload(base, affix)
    if transformed is None:
        raise AssertionError("cannot create an empty equipment affix")
    variant, _ = ItemTemplate.objects.get_or_create(
        key=equipment_affix_key(base.key, affix),
        defaults=_variant_defaults(base, affix, transformed),
    )
    if variant.effect_payload.get(BASE_KEY_FIELD) != base.key or variant.effect_payload.get(AFFIX_FIELD) != affix:
        raise AssertionError("equipment affix template identity collision")
    return variant


def refresh_equipment_affix_templates(bases: Mapping[str, ItemTemplate]) -> set[str]:
    """导入基础装备时同步已有变体，不为历史物品重抽前缀。"""
    _require_atomic()
    if not bases:
        return set()
    keys = {equipment_affix_key(key, affix): (base, affix) for key, base in bases.items() for affix in AFFIX_LABELS}
    refreshed = set()
    for variant in ItemTemplate.objects.select_for_update().filter(key__in=keys):
        base, affix = keys[variant.key]
        payload = build_equipment_affix_payload(base, affix)
        if payload is None:
            # 仍保留已经存在的变体身份，但不凭空增加任何四维。
            payload = copy.deepcopy(base.effect_payload)
            payload.update({BASE_KEY_FIELD: base.key, BASE_NAME_FIELD: base.name, AFFIX_FIELD: affix})
        defaults = _variant_defaults(base, affix, payload)
        updates = []
        for field, value in defaults.items():
            current = getattr(variant, field)
            if field == "image":
                current = current.name
            if current != value:
                setattr(variant, field, value)
                updates.append(field)
        if updates:
            variant.save(update_fields=updates)
        refreshed.add(variant.key)
    return refreshed


def _resolve_reward_inputs(
    item_quantities: Mapping[str, int],
    templates: Mapping[str, ItemTemplate] | None,
) -> tuple[dict[str, int], dict[str, ItemTemplate]]:
    if not isinstance(item_quantities, Mapping):
        raise AssertionError("equipment rewards must be a mapping")
    normalized: dict[str, int] = {}
    for key, count in item_quantities.items():
        if not isinstance(key, str) or not key.strip():
            raise AssertionError("invalid equipment reward item key")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise AssertionError("equipment reward quantity must be a positive integer")
        key = key.strip()
        normalized[key] = normalized.get(key, 0) + count
    resolved = dict(templates or {})
    missing = set(normalized) - set(resolved)
    if missing:
        resolved.update({t.key: t for t in ItemTemplate.objects.filter(key__in=missing)})
    for key in normalized:
        if key not in resolved:
            raise ItemNotFoundError("物品不存在", item_key=key)
        if resolved[key].key != key or not resolved[key].pk:
            raise AssertionError("reward template must match its item key")
    return normalized, resolved


def roll_equipment_reward_items_locked(
    item_quantities: Mapping[str, int],
    *,
    templates: Mapping[str, ItemTemplate] | None = None,
    rng: random.Random | Any | None = None,
) -> tuple[dict[str, int], dict[str, ItemTemplate]]:
    """把首次产出拆成真实物品身份；不负责库存增减或转移。"""
    _require_atomic()
    normalized, resolved = _resolve_reward_inputs(item_quantities, templates)
    random_source = rng if rng is not None else random.Random()
    actual: dict[str, int] = {}
    actual_templates: dict[str, ItemTemplate] = {}

    def add(template: ItemTemplate, quantity: int) -> None:
        if quantity:
            actual[template.key] = actual.get(template.key, 0) + quantity
            actual_templates[template.key] = template

    for key, count in normalized.items():
        base = resolved[key]
        if not equipment_can_gain_affix(base):
            add(base, count)
            continue
        candidates = {
            affix: payload
            for affix in AFFIX_LABELS
            if (payload := build_equipment_affix_payload(base, affix)) is not None
        }
        if not candidates:
            add(base, count)
            continue
        affixed = binomial_sample(count, AFFIX_CHANCE, random_source)
        add(base, count - affixed)
        remaining = affixed
        for index, (affix, payload) in enumerate(candidates.items()):
            quantity = binomial_sample(remaining, 1 / (len(candidates) - index), random_source)
            if quantity:
                add(get_or_create_equipment_affix_template(base, affix, payload=payload), quantity)
                remaining -= quantity
    return actual, actual_templates


def grant_items_with_equipment_affixes_locked(
    manor: Manor,
    item_quantities: Mapping[str, int],
    *,
    templates: Mapping[str, ItemTemplate] | None = None,
    is_bound: bool | None = None,
    bound_quantities: Mapping[str, int] | None = None,
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
    rng: random.Random | Any | None = None,
) -> dict[str, InventoryItem]:
    """发放新产出，绑定和未绑定份额各自转化后一次性入库。"""
    from gameplay.services.inventory.core import add_items_to_inventory_locked

    _require_atomic()
    if is_bound is not None and not isinstance(is_bound, bool):
        raise AssertionError("is_bound must be a boolean or None")
    normalized, supplied = _resolve_reward_inputs(item_quantities, templates)
    if bound_quantities is None:
        actual, actual_templates = roll_equipment_reward_items_locked(normalized, templates=supplied, rng=rng)
        return add_items_to_inventory_locked(
            manor,
            actual,
            storage_location,
            templates=actual_templates,
            is_bound=is_bound,
        )
    if not isinstance(bound_quantities, Mapping):
        raise AssertionError("bound quantities must be a mapping")
    if set(bound_quantities) - set(normalized):
        raise AssertionError("bound quantities must reference granted item keys")
    actual_grants: dict[str, int] = {}
    actual_bound: dict[str, int] = {}
    actual_templates = {}
    for key, total in normalized.items():
        default_bound = supplied[key].default_bound if is_bound is None else is_bound
        bound = bound_quantities.get(key, total if default_bound else 0)
        if isinstance(bound, bool) or not isinstance(bound, int) or not 0 <= bound <= total:
            raise AssertionError("invalid bound reward quantity")
        for quantity, group_bound in ((bound, True), (total - bound, False)):
            if not quantity:
                continue
            grants, resolved = roll_equipment_reward_items_locked({key: quantity}, templates=supplied, rng=rng)
            actual_templates.update(resolved)
            for actual_key, count in grants.items():
                actual_grants[actual_key] = actual_grants.get(actual_key, 0) + count
                actual_bound[actual_key] = actual_bound.get(actual_key, 0) + (count if group_bound else 0)
    return add_items_to_inventory_locked(
        manor,
        actual_grants,
        storage_location,
        templates=actual_templates,
        bound_quantities=actual_bound,
    )


def grant_item_with_equipment_affixes_locked(
    manor: Manor,
    item_key: str,
    quantity: int = 1,
    *,
    template: ItemTemplate | None = None,
    is_bound: bool | None = None,
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
    rng: random.Random | Any | None = None,
) -> dict[str, InventoryItem]:
    return grant_items_with_equipment_affixes_locked(
        manor,
        {item_key: quantity},
        templates={item_key: template} if template is not None else None,
        is_bound=is_bound,
        storage_location=storage_location,
        rng=rng,
    )
