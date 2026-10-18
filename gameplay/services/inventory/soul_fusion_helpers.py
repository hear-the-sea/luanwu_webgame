"""
Soul Fusion calculations and template generation helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings

from core.config import GUEST
from core.utils.image_utils import compress_and_resize_image
from gameplay.models import ItemTemplate
from guests.growth_rules import CIVIL_ATTRIBUTE_WEIGHTS, MILITARY_ATTRIBUTE_WEIGHTS
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus

SOUL_FUSION_DEFAULT_MIN_LEVEL = 30
SOUL_FUSION_STAT_KEYS = ("force", "intellect", "defense", "agility", "luck")
SOUL_FUSION_GROWTH_STAT_KEYS = ("force", "intellect", "defense", "agility")
SOUL_FUSION_DEFAULT_ALLOWED_RARITIES = frozenset(
    {
        GuestRarity.GREEN,
        GuestRarity.BLUE,
        GuestRarity.PURPLE,
    }
)
SOUL_FUSION_ALLOWED_GUEST_STATUSES = frozenset({GuestStatus.IDLE, GuestStatus.VACATION})
# Retained for callers that inspect the former archetype weights.
SOUL_FUSION_ARCHETYPE_BASE_WEIGHTS: dict[str, dict[str, float]] = {
    GuestArchetype.MILITARY: {
        "force": 44.0,
        "intellect": 18.0,
        "agility": 24.0,
        "luck": 14.0,
    },
    GuestArchetype.CIVIL: {
        "force": 18.0,
        "intellect": 46.0,
        "agility": 18.0,
        "luck": 18.0,
    },
}
SOUL_FUSION_ARCHETYPE_LUCK_WEIGHTS: dict[str, float] = {
    GuestArchetype.MILITARY: 14.0,
    GuestArchetype.CIVIL: 20.0,
}
SOUL_FUSION_RESULT_CONFIG: dict[str, dict[str, object]] = {
    GuestRarity.GREEN: {
        "name": "玉海棠",
        "image": "yuhaitang.png",
        "description": "南方苗人的圣物，融合顶级绿色门客的灵魂，能给佩戴者带来不可思议的能力。",
        "rarity": GuestRarity.BLUE,
        "price": 36000,
        "stat_total_range": (48, 65),
        "hp_range": (600, 1000),
        "required_level": 30,
    },
    GuestRarity.BLUE: {
        "name": "北冥冰链",
        "image": "beiming.png",
        "description": "由北冥永不融化的冰珠串起来，并融合了蓝色精英门客的灵魂，拥有冰封十里的威能。",
        "rarity": GuestRarity.PURPLE,
        "price": 98000,
        "stat_total_range": (67, 85),
        "hp_range": (1000, 1600),
        "required_level": 40,
    },
    GuestRarity.PURPLE: {
        "name": "龙纹赤血佩",
        "image": "chixue.png",
        "description": (
            "传闻由天外陨铁与昆仑暖玉合铸而成，玉身天生血纹，隐约有龙吟之声。"
            "唯有融合了紫色门客的坚韧灵魂，才能镇压住佩玉中暴戾的气血。"
        ),
        "rarity": GuestRarity.ORANGE,
        "price": 220000,
        "stat_total_range": (87, 110),
        "hp_range": (1600, 2200),
        "required_level": 55,
    },
}


def _normalize_soul_fusion_min_level(raw_value: Any) -> int:
    try:
        level = int(raw_value)
    except (TypeError, ValueError):
        return SOUL_FUSION_DEFAULT_MIN_LEVEL
    return max(1, level)


def _normalize_soul_fusion_allowed_rarities(raw_value: Any) -> set[str]:
    if not isinstance(raw_value, (list, tuple, set)):
        return set(SOUL_FUSION_DEFAULT_ALLOWED_RARITIES)
    normalized = {str(rarity).strip() for rarity in raw_value if str(rarity).strip()}
    return normalized or set(SOUL_FUSION_DEFAULT_ALLOWED_RARITIES)


def get_soul_fusion_requirements(payload: Any) -> tuple[int, set[str]]:
    if not isinstance(payload, dict):
        return SOUL_FUSION_DEFAULT_MIN_LEVEL, set(SOUL_FUSION_DEFAULT_ALLOWED_RARITIES)
    return (
        _normalize_soul_fusion_min_level(payload.get("min_level")),
        _normalize_soul_fusion_allowed_rarities(payload.get("allowed_rarities")),
    )


def guest_is_eligible_for_soul_fusion(guest: Guest, *, min_level: int, allowed_rarities: set[str]) -> bool:
    guest_rarity = str(getattr(getattr(guest, "template", None), "rarity", "") or "").strip()
    return (
        guest.status in SOUL_FUSION_ALLOWED_GUEST_STATUSES
        and guest.level >= min_level
        and guest_rarity in allowed_rarities
    )


def _guest_level_ratio(guest: Guest) -> float:
    max_level = max(SOUL_FUSION_DEFAULT_MIN_LEVEL, int(GUEST.MAX_LEVEL))
    if guest.level <= SOUL_FUSION_DEFAULT_MIN_LEVEL:
        return 0.0
    if guest.level >= max_level:
        return 1.0
    span = max_level - SOUL_FUSION_DEFAULT_MIN_LEVEL
    return max(0.0, min(1.0, (guest.level - SOUL_FUSION_DEFAULT_MIN_LEVEL) / span))


def _roll_biased_value(min_value: int, max_value: int, *, bias: float, rng) -> int:
    if max_value <= min_value:
        return int(min_value)
    bounded_bias = max(0.0, min(1.0, float(bias)))
    raw_roll = int(rng.randint(min_value, max_value))
    target = min_value + int(round((max_value - min_value) * bounded_bias))
    blended = int(round(raw_roll * 0.65 + target * 0.35))
    return max(min_value, min(max_value, blended))


def _extract_soul_fusion_source_stats(guest: Guest) -> dict[str, int]:
    gear_bonus = {"force": 0, "intellect": 0, "agility": 0, "luck": 0, "defense": 0}
    for gear in guest.gear_items.select_related("template"):
        extra_stats = getattr(gear.template, "extra_stats", {}) or {}
        for stat in gear_bonus:
            gear_bonus[stat] += int(extra_stats.get(stat, 0) or 0)

    set_bonus = guest.gear_set_bonus or {}
    source_stats = {}
    for stat in ("force", "intellect", "agility", "luck"):
        set_bonus_value = int(set_bonus.get(stat, 0) or 0)
        source_stats[stat] = max(1, int(getattr(guest, stat) - gear_bonus[stat] - set_bonus_value))

    # 装备和套装的防御均写入 defense_stat，灵魂融合只使用门客自身属性。
    source_stats["defense"] = max(
        1,
        int(guest.defense_stat - gear_bonus["defense"] - int(set_bonus.get("defense", 0) or 0)),
    )
    return source_stats


def _build_soul_fusion_weights(guest: Guest, source_stats: dict[str, int], rng) -> dict[str, float]:
    # source_stats remains in the signature for compatibility; allocation now uses template growth weights.
    raw_growth_weights = getattr(getattr(guest, "template", None), "attribute_weights", {}) or {}
    growth_weights = {
        stat: int(raw_growth_weights.get(stat, 0) or 0) if isinstance(raw_growth_weights, dict) else 0
        for stat in SOUL_FUSION_GROWTH_STAT_KEYS
    }
    if sum(growth_weights.values()) <= 0:
        defaults = MILITARY_ATTRIBUTE_WEIGHTS if guest.archetype == GuestArchetype.MILITARY else CIVIL_ATTRIBUTE_WEIGHTS
        growth_weights = {stat: int(defaults.get(stat, 0) or 0) for stat in SOUL_FUSION_GROWTH_STAT_KEYS}

    # Luck is independent of growth allocation and its share stays fixed through stat fluctuation.
    luck_weight = SOUL_FUSION_ARCHETYPE_LUCK_WEIGHTS.get(
        guest.archetype,
        SOUL_FUSION_ARCHETYPE_LUCK_WEIGHTS[GuestArchetype.CIVIL],
    )
    growth_total = sum(growth_weights.values())
    source_weights = {
        stat: growth_weights[stat] / growth_total * (1.0 - luck_weight / 100.0) for stat in SOUL_FUSION_GROWTH_STAT_KEYS
    }
    source_weights["luck"] = luck_weight / 100.0

    fluctuated = {
        stat: source_weights[stat] * (1.0 + float(rng.uniform(-0.12, 0.12))) for stat in SOUL_FUSION_GROWTH_STAT_KEYS
    }
    fluctuated_total = sum(fluctuated.values())
    return {
        **{
            stat: fluctuated[stat] / fluctuated_total * (1.0 - luck_weight / 100.0)
            for stat in SOUL_FUSION_GROWTH_STAT_KEYS
        },
        "luck": luck_weight / 100.0,
    }


def _allocate_soul_fusion_secondary_stats(
    total_budget: int,
    weights: dict[str, float],
) -> dict[str, int]:
    allocations = {stat: 0 for stat in SOUL_FUSION_STAT_KEYS}
    raw_allocations = {stat: weights.get(stat, 0.0) * int(total_budget) for stat in SOUL_FUSION_STAT_KEYS}
    consumed = 0
    fractions = []
    for stat in SOUL_FUSION_STAT_KEYS:
        points = int(raw_allocations[stat])
        allocations[stat] += points
        consumed += points
        fractions.append((raw_allocations[stat] - points, stat))

    leftover = int(total_budget) - consumed
    for _fraction, stat in sorted(fractions, reverse=True):
        if leftover <= 0:
            break
        allocations[stat] += 1
        leftover -= 1

    return allocations


def _roll_soul_fusion_stats(guest: Guest, source_stats: dict[str, int], config: dict[str, Any], rng) -> dict[str, int]:
    level_ratio = _guest_level_ratio(guest)
    stat_total = _roll_biased_value(
        int(config["stat_total_range"][0]),
        int(config["stat_total_range"][1]),
        bias=level_ratio,
        rng=rng,
    )
    weights = _build_soul_fusion_weights(guest, source_stats, rng)
    secondary_stats = _allocate_soul_fusion_secondary_stats(stat_total, weights)

    core_average = max(
        1.0,
        (source_stats["force"] + source_stats["intellect"] + source_stats["agility"] + source_stats["luck"]) / 4.0,
    )
    defense_bias = min(1.0, source_stats["defense"] / (core_average * 1.25))
    hp_bias = level_ratio * 0.55 + defense_bias * 0.45
    hp_value = _roll_biased_value(
        int(config["hp_range"][0]),
        int(config["hp_range"][1]),
        bias=hp_bias,
        rng=rng,
    )

    return {
        "hp": hp_value,
        "force": secondary_stats["force"],
        "intellect": secondary_stats["intellect"],
        "defense": secondary_stats["defense"],
        "agility": secondary_stats["agility"],
        "luck": secondary_stats["luck"],
    }


def _create_soul_fusion_ornament_template(guest: Guest, config: dict[str, Any], stats: dict[str, int]) -> ItemTemplate:
    key = f"soulorn_{uuid4().hex[:20]}"
    spirit_tone = "武魂炽烈" if guest.archetype == GuestArchetype.MILITARY else "灵识澄明"
    description = (
        f"{config['description']} 此器由{guest.display_name}之魂淬炼而成，{spirit_tone}，"
        "因此属性会随原主底蕴而波动。"
    )
    image_path = Path(settings.BASE_DIR) / "data" / "images" / "items" / str(config["image"])
    compressed_image, image_filename = compress_and_resize_image(
        image_path,
        max_size=(200, 200),
        quality=85,
        convert_to_webp=True,
    )
    template = ItemTemplate.objects.create(
        key=key,
        name=str(config["name"]),
        description=description,
        effect_type="equip_ornament",
        effect_payload={**stats, "required_level": int(config["required_level"])},
        rarity=str(config["rarity"]),
        tradeable=False,
        price=int(config["price"]),
        storage_space=100,
        is_usable=False,
    )
    template.image.save(image_filename, compressed_image, save=True)
    return template


def _format_soul_fusion_stat_summary(stats: dict[str, int]) -> str:
    return (
        f"生命+{stats['hp']}、武力+{stats['force']}、智力+{stats['intellect']}、防御+{stats['defense']}、"
        f"敏捷+{stats['agility']}、运势+{stats['luck']}"
    )
