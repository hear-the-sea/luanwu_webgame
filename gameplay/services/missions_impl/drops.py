from __future__ import annotations

import random
from typing import Dict

from django.db import IntegrityError, transaction
from django.db.models import F

from ...models import InventoryItem, ItemTemplate, Manor, ResourceEvent, ResourceType
from ..resources import grant_resources_locked


def _require_mapping_payload(raw, *, field_name: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    normalized: dict = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise AssertionError(f"invalid mission {field_name} key: {key!r}")
        key_str = key.strip()
        if not key_str:
            raise AssertionError(f"invalid mission {field_name} key: {key!r}")
        normalized[key_str] = value
    return normalized


def _split_drop_payload(drops: Dict[str, int]) -> tuple[Dict[str, int], Dict[str, int]]:
    resource_keys = set(ResourceType.values)
    resources: Dict[str, int] = {}
    items: Dict[str, int] = {}
    for key, value in drops.items():
        if not isinstance(key, str):
            raise AssertionError(f"invalid mission drop key: {key!r}")
        key_str = key.strip()
        if not key_str:
            raise AssertionError(f"invalid mission drop key: {key!r}")
        if isinstance(value, bool):
            raise AssertionError(f"invalid mission drop amount: {(key, value)!r}")
        try:
            amount = int(value)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid mission drop amount: {(key, value)!r}") from exc
        if amount < 0:
            raise AssertionError(f"invalid mission drop amount: {(key, value)!r}")
        if key_str in resource_keys:
            resources[key_str] = amount
        else:
            items[key_str] = amount
    return resources, items


def _load_item_templates_with_skillbook_fallback(item_keys: Dict[str, int]) -> Dict[str, ItemTemplate]:
    if not item_keys:
        return {}

    from guests.models import GearTemplate, SkillBook

    from ..buildings.forge import EQUIPMENT_CONFIG
    from ..buildings.forge_helpers import infer_equipment_category

    templates = {it.key: it for it in ItemTemplate.objects.filter(key__in=item_keys.keys())}
    missing_keys = set(item_keys.keys()) - set(templates.keys())
    if not missing_keys:
        return templates

    books = {book.key: book for book in SkillBook.objects.filter(key__in=missing_keys)}
    gears = {gear.key: gear for gear in GearTemplate.objects.filter(key__in=missing_keys)}
    for key in list(missing_keys):
        book = books.get(key)
        if book:
            tmpl = _get_or_create_skill_book_template(key, book)
            templates[key] = tmpl
            continue

        gear = gears.get(key)
        if gear:
            tmpl = _get_or_create_equipment_item_template(key, gear)
            templates[key] = tmpl
            continue

        equipment_category = infer_equipment_category(key, equipment_config=EQUIPMENT_CONFIG)
        if equipment_category:
            tmpl = _get_or_create_equipment_item_template_from_category(key, equipment_category)
            templates[key] = tmpl
    return templates


def _get_or_create_skill_book_template(key: str, book) -> ItemTemplate:
    defaults = {
        "name": book.name,
        "description": book.description,
        "effect_type": ItemTemplate.EffectType.SKILL_BOOK,
        "effect_payload": {"skill_key": book.skill.key, "skill_name": book.skill.name},
    }
    try:
        tmpl, _ = ItemTemplate.objects.get_or_create(key=key, defaults=defaults)
        return tmpl
    except IntegrityError:
        # Another concurrent transaction may have created this template.
        existing = ItemTemplate.objects.filter(key=key).first()
        if existing:
            return existing
        raise


def _infer_equipment_effect_type(slot: str) -> str:
    slot_to_effect_type = {
        "helmet": "equip_helmet",
        "armor": "equip_armor",
        "weapon": "equip_weapon",
        "shoes": "equip_shoes",
        "mount": "equip_mount",
        "ornament": "equip_ornament",
        "device": "equip_device",
    }
    if not isinstance(slot, str):
        raise AssertionError(f"invalid mission drop gear slot: {slot!r}")
    effect_type = slot_to_effect_type.get(slot.strip())
    if not effect_type:
        raise AssertionError(f"invalid mission drop gear slot: {slot!r}")
    return effect_type


def _get_or_create_equipment_item_template(key: str, gear) -> ItemTemplate:
    defaults = {
        "name": gear.name,
        "effect_type": _infer_equipment_effect_type(gear.slot),
        "rarity": gear.rarity,
    }
    try:
        tmpl, _ = ItemTemplate.objects.get_or_create(key=key, defaults=defaults)
        return tmpl
    except IntegrityError:
        existing = ItemTemplate.objects.filter(key=key).first()
        if existing:
            return existing
        raise


def _infer_equipment_effect_type_from_category(category: str) -> str:
    category_to_effect_type = {
        "helmet": "equip_helmet",
        "armor": "equip_armor",
        "shoes": "equip_shoes",
        "device": "equip_device",
        "mount": "equip_mount",
        "ornament": "equip_ornament",
        "sword": "equip_weapon",
        "dao": "equip_weapon",
        "spear": "equip_weapon",
        "bow": "equip_weapon",
        "whip": "equip_weapon",
        "weapon": "equip_weapon",
    }
    if not isinstance(category, str):
        raise AssertionError(f"invalid mission drop equipment category: {category!r}")
    effect_type = category_to_effect_type.get(category.strip())
    if not effect_type:
        raise AssertionError(f"invalid mission drop equipment category: {category!r}")
    return effect_type


def _get_or_create_equipment_item_template_from_category(key: str, category: str) -> ItemTemplate:
    defaults = {
        "name": key,
        "effect_type": _infer_equipment_effect_type_from_category(category),
    }
    try:
        tmpl, _ = ItemTemplate.objects.get_or_create(key=key, defaults=defaults)
        return tmpl
    except IntegrityError:
        existing = ItemTemplate.objects.filter(key=key).first()
        if existing:
            return existing
        raise


def _upsert_warehouse_items_locked(manor: Manor, item_keys: Dict[str, int], templates: Dict[str, ItemTemplate]) -> None:
    for key, amount in item_keys.items():
        if amount <= 0:
            continue

        template = templates.get(key)
        if not template:
            raise AssertionError(f"invalid mission drop item key: {key!r}")

        inventory_item, _created = InventoryItem.objects.select_for_update().get_or_create(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 0},
        )
        InventoryItem.objects.filter(pk=inventory_item.pk).update(quantity=F("quantity") + amount)


def award_mission_drops_locked(
    manor: Manor,
    drops: Dict[str, int],
    note: str,
    *,
    locked_manor: Manor | None = None,
) -> None:
    if not drops:
        return

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("award_mission_drops_locked must be called inside transaction.atomic()")

    manor_locked = locked_manor or Manor.objects.select_for_update().get(pk=manor.pk)

    resources, item_keys = _split_drop_payload(drops)
    blueprint_keys = {key for key in item_keys if key.startswith("blueprint_")}
    if blueprint_keys:
        from ..buildings.forge import load_blueprint_catalog

        catalog = load_blueprint_catalog()
        unknown_blueprints = blueprint_keys - set(catalog)
        if unknown_blueprints:
            raise AssertionError(f"invalid mission blueprint drop: {sorted(unknown_blueprints)!r}")
    if resources:
        grant_resources_locked(
            manor_locked,
            resources,
            note,
            ResourceEvent.Reason.BATTLE_REWARD,
            sync_production=False,
        )

    if not item_keys:
        return

    templates = _load_item_templates_with_skillbook_fallback(item_keys)
    _upsert_warehouse_items_locked(manor_locked, item_keys, templates)


def award_mission_drops(manor: Manor, drops: Dict[str, int], note: str) -> None:
    if not drops:
        return

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        award_mission_drops_locked(manor, drops, note, locked_manor=locked_manor)


def resolve_defense_drops_if_missing(report, mission_drop_table: dict) -> dict:
    """Defense missions may generate a report without drops; fill them deterministically."""
    raw_drops = getattr(report, "drops", None)
    if raw_drops is None:
        raise AssertionError("invalid mission report.drops: None")
    drops = dict(_require_mapping_payload(raw_drops, field_name="report.drops"))
    if drops:
        return drops

    normalized_drop_table = _require_mapping_payload(mission_drop_table, field_name="drop table")

    from common.utils.loot import resolve_drop_rewards

    seed = getattr(report, "seed", None)
    rng = random.Random(seed) if seed is not None else random.Random()
    return resolve_drop_rewards(normalized_drop_table, rng)
