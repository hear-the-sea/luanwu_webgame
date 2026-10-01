"""按出发时的侦查术等级采集情报，仅查询目标庄园。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.models import Guest, GuestRarity
from guests.rarity import GUEST_RARITY_ORDER

from ...models import ItemTemplate, Manor, PlayerTroop
from ..city_defense_rules import CITY_DEFENSE_MAX_HP
from ..inventory.core import GRAIN_ITEM_KEY, get_warehouse_grain_quantity
from ..pvp_runtime.lineups import select_player_defender_lineup
from ..scout_intel_rules import SCOUT_INTEL_SCHEMA_VERSION, normalize_scout_level
from ..technology import get_player_technologies, get_technologies_by_category, get_troop_class_for_key
from .combat.loot import _build_loot_item_queryset, _virtual_defender_loot_rarity_cap
from .utils import get_asset_level, get_troop_description


def _gather_warehouse_summary(defender: Manor) -> dict[str, Any]:
    items = _build_loot_item_queryset(defender, max_rarity=_virtual_defender_loot_rarity_cap(defender))
    equipment = items.filter(template__effect_type__startswith="equip_")
    rarity_counts = list(equipment.order_by().values("template__rarity").annotate(count=Sum("quantity")))
    rarity_rank = {rarity: rank for rank, rarity in enumerate(GUEST_RARITY_ORDER)}
    rarity_counts.sort(key=lambda row: rarity_rank.get(row["template__rarity"], -1), reverse=True)
    equipment_count = sum(row["count"] for row in rarity_counts)
    rarity_labels = dict(GuestRarity.choices)
    highest_rarities = [
        {"name": rarity_labels.get(row["template__rarity"], "未知品质"), "count": row["count"]}
        for row in rarity_counts[:2]
    ]
    materials = items.filter(template__effect_type=ItemTemplate.EffectType.RESOURCE).exclude(
        template__key=GRAIN_ITEM_KEY
    )
    others = items.exclude(template__effect_type__startswith="equip_").exclude(
        template__effect_type=ItemTemplate.EffectType.RESOURCE
    )
    return {
        "has_loot": items.exists(),
        "equipment_count": equipment_count,
        "equipment_rarities": highest_rarities,
        "equipment_other_count": equipment_count - sum(row["count"] for row in highest_rarities),
        "material_type_count": materials.aggregate(count=Count("template_id", distinct=True))["count"],
        "other_type_count": others.aggregate(count=Count("template_id", distinct=True))["count"],
    }


def _describe_guest(guest: Guest, *, include_details: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": guest.display_name,
        "rarity": guest.template.get_rarity_display(),
        "level": guest.level,
    }
    if include_details:
        stats = resolve_guest_combat_stats(guest)
        row["attributes"] = {
            "attack": stats.attack,
            "defense": stats.defense,
            "max_hp": stats.max_hp,
            "force": guest.force,
            "intellect": stats.intellect,
            "agility": guest.agility,
            "luck": guest.luck,
            "troop_capacity": stats.troop_capacity,
        }
        row["skills"] = [
            {"name": skill.name, "kind": skill.get_kind_display(), "description": skill.description}
            for skill in guest.skills.all()
        ]
    return row


def gather_scout_intel(defender: Manor, scout_level: int = 0, *, now: datetime | None = None) -> dict[str, Any]:
    level = normalize_scout_level(scout_level)
    collected_at = now or timezone.now()
    troops = PlayerTroop.objects.filter(manor=defender, count__gt=0)
    total_troops = troops.aggregate(total=Sum("count"))["total"] or 0
    guest_summary = defender.guests.aggregate(count=Count("id"), average=Avg("level"))
    asset_level, _ = get_asset_level(defender)
    intel: dict[str, Any] = {
        "intel_schema_version": SCOUT_INTEL_SCHEMA_VERSION,
        "scout_level": level,
        "troop_description": get_troop_description(total_troops),
        "guest_count": guest_summary["count"],
        "avg_guest_level": round(guest_summary["average"] or 0),
        "asset_level": asset_level,
        "scouted_at": collected_at.isoformat(),
    }
    troop_rows: list[dict[str, Any]] = []
    if level >= 50:
        troop_rows = [
            {"key": troop.troop_template.key, "name": troop.troop_template.name, "count": troop.count}
            for troop in troops.select_related("troop_template").order_by("troop_template__key")
        ]
        intel.update(total_troops=total_troops, troops=troop_rows)
    if level >= 100:
        intel["defending_guests"] = [
            _describe_guest(guest, include_details=level >= 200) for guest in select_player_defender_lineup(defender)
        ]
    if level >= 150:
        intel.update(
            silver=defender.silver,
            warehouse_grain=get_warehouse_grain_quantity(defender),
            warehouse_summary=_gather_warehouse_summary(defender),
        )
    if level >= 200:
        troop_classes = {get_troop_class_for_key(row["key"]) for row in troop_rows}
        technology_levels = get_player_technologies(defender)
        intel["martial_technologies"] = [
            {"name": technology["name"], "level": technology_levels.get(technology["key"], 0)}
            for technology in get_technologies_by_category("martial")
            if technology.get("troop_class") in troop_classes
        ]
        intel["city_defenses"] = [
            {"name": building.building_type.name, "level": building.level}
            for building in defender.buildings.select_related("building_type")
            .filter(building_type__key__in=CITY_DEFENSE_MAX_HP, level__gt=0)
            .order_by("building_type__key")
        ]
    return intel
