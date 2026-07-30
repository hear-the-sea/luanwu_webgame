from __future__ import annotations

import random
from typing import Any

from django.db.models import Count
from django.utils import timezone

from common.constants.virtual_players import VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES
from gameplay.models import BotProfile, InventoryItem, ItemTemplate, Manor
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES

from .. import profile_store
from ..config import DEFAULT_VIRTUAL_PLAYER_CONFIG
from ..inventory_budget import apply_inventory_daily_caps as _apply_inventory_daily_caps
from ..selectors import configured_item_keys as _configured_item_keys
from .projection import chance_value as _chance_value
from .projection import range_value as _range_value

RARE_ITEM_RARITIES = {"purple", "orange", "red", "legendary"}
LOW_STAGE_POWERFUL_ITEM_CUTOFF = 5


def _inventory_quantity_multiplier(archetype: str, config: dict[str, Any]) -> float:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_quantity_multipliers") or {}
    if isinstance(configured, dict) and archetype in configured:
        return max(0.0, float(configured[archetype] or 0))
    default_multipliers: dict[str, float] = {
        BotProfile.Archetype.RICH.value: 2.0,
        BotProfile.Archetype.ABANDONED.value: 2.5,
        BotProfile.Archetype.DOJO.value: 1.2,
        BotProfile.Archetype.GUARD.value: 1.0,
    }
    return default_multipliers.get(archetype, 1.0)


def _inventory_template_slot_count(archetype: str, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_template_slots_by_archetype") or {}
    if isinstance(configured, dict) and archetype in configured:
        return max(1, int(configured[archetype] or 1))
    default_slots = DEFAULT_VIRTUAL_PLAYER_CONFIG["projection"]["inventory_template_slots_by_archetype"]
    return max(
        1,
        int(default_slots.get(archetype, default_slots[BotProfile.Archetype.BALANCED.value])),
    )


def _inventory_effect_weight(template: ItemTemplate, *, archetype: str, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_effect_type_weights") or {}
    archetype_weights = configured.get(archetype) if isinstance(configured, dict) else None
    if not isinstance(archetype_weights, dict):
        archetype_weights = {}
    return max(1, int(archetype_weights.get(str(template.effect_type), 1) or 1))


def _select_inventory_template_pool(
    profile: BotProfile,
    templates: list[ItemTemplate],
    *,
    archetype: str,
    rng: random.Random,
    config: dict[str, Any],
) -> list[ItemTemplate]:
    """Keep a small archetype-shaped inventory pool, spreading templates across live bots."""
    slot_count = min(_inventory_template_slot_count(archetype, config), len(templates))
    if slot_count <= 0:
        return []

    by_key = {template.key: template for template in templates}
    selected = [by_key[key] for key in profile.inventory_template_keys if key in by_key]
    selected = list(dict.fromkeys(selected))[:slot_count]
    if len(selected) >= slot_count:
        return selected

    usage_counts = {
        row["template_id"]: row["manor_count"]
        for row in (
            InventoryItem.objects.filter(
                manor__bot_profile__state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
                template__in=templates,
            )
            .values("template_id")
            .annotate(manor_count=Count("manor_id", distinct=True))
        )
    }
    candidates = [template for template in templates if template not in selected]
    while candidates and len(selected) < slot_count:
        weighted_candidates = [
            (
                template,
                _inventory_effect_weight(template, archetype=archetype, config=config)
                / (1 + int(usage_counts.get(template.id, 0))),
            )
            for template in candidates
        ]
        total_weight = sum(weight for _template, weight in weighted_candidates)
        target = rng.uniform(0, total_weight)
        cumulative = 0.0
        chosen = weighted_candidates[-1][0]
        for template, weight in weighted_candidates:
            cumulative += weight
            if target <= cumulative:
                chosen = template
                break
        selected.append(chosen)
        candidates.remove(chosen)
        usage_counts[chosen.id] = int(usage_counts.get(chosen.id, 0)) + 1
    return selected


def _is_powerful_item(template: ItemTemplate, config: dict[str, Any]) -> bool:
    projection = config.get("projection") or {}
    powerful_min_price = int(projection.get("powerful_item_min_price") or 100_000)
    return int(template.price or 0) >= powerful_min_price


def _low_stage_powerful_item_chance(config: dict[str, Any]) -> float:
    projection = config.get("projection") or {}
    return _chance_value(projection.get("low_stage_powerful_item_chance"), default=0.03)


def _powerful_item_min_growth_stage(config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    return max(0, int(projection.get("powerful_item_min_growth_stage") or 0))


def _powerful_item_prestige_chance(config: dict[str, Any], prestige: int) -> float:
    projection = config.get("projection") or {}
    raw = projection.get("powerful_item_prestige_chance")
    if not isinstance(raw, list):
        return 0.0

    best_chance = 0.0
    best_min = -1
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            min_prestige = int(row.get("min_prestige", 0) or 0)
        except (TypeError, ValueError):
            continue
        chance = _chance_value(row.get("chance"), default=0.0)
        if prestige >= min_prestige and min_prestige >= best_min:
            best_min = min_prestige
            best_chance = chance
    return best_chance


def _bot_inventory_target_quantity(
    *,
    level: int,
    template: ItemTemplate,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
) -> int:
    projection = config.get("projection") or {}
    default_quantity = max(1, int(level) // 2)
    quantity_config = projection.get("loot_item_quantity")
    quantity = _range_value(rng, quantity_config, default=(default_quantity, default_quantity))
    quantity = int(quantity * _inventory_quantity_multiplier(str(archetype), config))
    return max(0, quantity)


def _should_project_inventory_template(
    template: ItemTemplate,
    *,
    level: int,
    growth_stage: int,
    prestige: int,
    rng: random.Random,
    config: dict[str, Any],
) -> bool:
    is_powerful = _is_powerful_item(template, config)
    is_rare = str(template.rarity or "").lower() in RARE_ITEM_RARITIES
    min_stage = _powerful_item_min_growth_stage(config)
    if min_stage > 0 and int(growth_stage or 0) < min_stage and (is_powerful or is_rare):
        return False
    if int(level or 0) > LOW_STAGE_POWERFUL_ITEM_CUTOFF:
        if is_powerful or is_rare:
            return rng.random() < _powerful_item_prestige_chance(config, int(prestige or 0))
        return True
    if not is_powerful and not is_rare:
        return True
    return rng.random() < min(
        _low_stage_powerful_item_chance(config),
        _powerful_item_prestige_chance(config, int(prestige or 0)),
    )


def _replenish_inventory_stock(
    profile: BotProfile,
    manor: Manor,
    *,
    level: int,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
    growth_stage: int,
    prestige: int,
    now=None,
) -> None:
    keys = [
        *_configured_item_keys(config, "item_template_keys"),
        *_configured_item_keys(config, "loot_item_template_keys"),
    ]
    if not keys:
        return

    unique_keys = list(dict.fromkeys(keys))
    candidate_templates = list(ItemTemplate.objects.filter(key__in=unique_keys, tradeable=True).order_by("key"))
    if not candidate_templates:
        return
    templates = _select_inventory_template_pool(
        profile,
        candidate_templates,
        archetype=str(archetype),
        rng=rng,
        config=config,
    )
    pool_keys = [template.key for template in templates]
    profile_store.set_inventory_template_keys(profile, template_keys=pool_keys)

    now = now or timezone.now()
    stale_template_ids = [
        template.id
        for template in candidate_templates
        if template.key not in pool_keys and template.effect_type in VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES
    ]
    if stale_template_ids:
        stale_item_ids = list(
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template_id__in=stale_template_ids,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .values_list("id", flat=True)
        )
        if stale_item_ids:
            InventoryItem.objects.filter(id__in=stale_item_ids).delete()

    existing_by_template = {
        item.template_id: item
        for item in InventoryItem.objects.select_for_update().filter(
            manor=manor,
            template__in=templates,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
    }
    for template in templates:
        if not _should_project_inventory_template(
            template,
            level=level,
            growth_stage=growth_stage,
            prestige=prestige,
            rng=rng,
            config=config,
        ):
            continue
        target_quantity = _bot_inventory_target_quantity(
            level=level,
            template=template,
            rng=rng,
            config=config,
            archetype=str(archetype),
        )
        existing = existing_by_template.get(template.id)
        current_quantity = int(existing.quantity or 0) if existing is not None else 0
        needed = max(0, target_quantity - current_quantity)
        needed = _apply_inventory_daily_caps(template, quantity=needed, config=config, now=now)
        if needed <= 0:
            continue
        if existing is not None:
            existing.quantity = current_quantity + needed
            existing.save(update_fields=["quantity", "updated_at"])
            continue
        InventoryItem.objects.create(
            manor=manor,
            template=template,
            quantity=needed,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )


__all__ = ["_replenish_inventory_stock"]
