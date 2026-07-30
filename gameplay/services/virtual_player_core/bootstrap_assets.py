from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from core.config import GUEST
from gameplay.constants import BuildingKeys
from gameplay.services.manor.core import calculate_building_capacity
from guests.services.equipment_stats import slot_capacity
from guests.utils.recruitment_variance import apply_recruitment_variance

from .bootstrap_catalog import BootstrapCatalog, GuestCatalogEntry, SkillCatalogEntry, TroopCatalogEntry
from .contracts import BotProjectionConfig
from .projection import BootstrapAssetTargets, BootstrapGuestTarget, BootstrapInventoryTarget, StrengthSummary
from .random_context import RandomContext
from .strategy import BotDevelopmentPlan

RARITY_ORDER = ("black", "gray", "green", "red", "blue", "purple", "orange")
RARITY_RANK = {rarity: index for index, rarity in enumerate(RARITY_ORDER)}
EMPTY_PAYLOAD_DIGEST = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


class BootstrapAssetPlanningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GuestSeedAttributes:
    force: int
    intellect: int
    defense: int
    agility: int
    luck: int


def guest_random(
    context: RandomContext,
    *,
    ordinal: int,
    template_key: str,
) -> random.Random:
    return context.random(
        domain="roster",
        discriminator={
            "bootstrap_guest_ordinal": int(ordinal),
            "template_key": str(template_key),
        },
    )


def guest_seed_attributes(
    context: RandomContext,
    *,
    ordinal: int,
    template: GuestCatalogEntry,
) -> GuestSeedAttributes:
    rng = guest_random(context, ordinal=ordinal, template_key=template.key)
    if not template.default_gender or template.default_gender == "unknown":
        rng.choice(("male", "female"))
    if not template.default_morality:
        rng.randint(30, 100)
    varied = apply_recruitment_variance(
        {
            "force": template.base_attack,
            "intellect": template.base_intellect,
            "defense": template.base_defense,
            "agility": template.base_agility,
            "luck": template.base_luck,
        },
        rarity=template.rarity,
        archetype=template.archetype,
        rng=rng,
    )
    return GuestSeedAttributes(
        force=int(varied["force"]),
        intellect=int(varied["intellect"]),
        defense=int(varied["defense"]),
        agility=int(varied["agility"]),
        luck=int(varied["luck"]),
    )


def _history_offset(
    context: RandomContext,
    *,
    domain: str,
    discriminator: str | Mapping[str, object],
    historical_age_days: int,
    minimum: int = 0,
) -> int:
    upper = max(0, int(historical_age_days))
    lower = min(upper, max(0, int(minimum)))
    return lower + context.bucket(
        domain=domain,
        discriminator=discriminator,
        bucket_count=upper - lower + 1,
    )


def _configured_max_rarity(
    config: Mapping[str, Any],
    *,
    field: str,
    growth_stage: int,
    fallback: str = "green",
) -> int:
    projection = config.get("projection") or {}
    if not isinstance(projection, Mapping):
        raise BootstrapAssetPlanningError("projection config must be a mapping")
    configured = projection.get(field) or {}
    if not isinstance(configured, Mapping):
        raise BootstrapAssetPlanningError(f"projection.{field} must be a mapping")
    selected = RARITY_RANK[fallback]
    for raw_stage, raw_rarity in configured.items():
        if isinstance(raw_stage, bool):
            continue
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        rarity = str(raw_rarity)
        if 0 < stage <= int(growth_stage) and rarity in RARITY_RANK:
            selected = max(selected, RARITY_RANK[rarity])
    return selected


_EntryT = TypeVar("_EntryT")


def _digest_ranked(
    context: RandomContext,
    *,
    domain: str,
    discriminator: str,
    values: Sequence[_EntryT],
    key,
) -> list[_EntryT]:
    return sorted(
        values,
        key=lambda value: (
            context.digest(
                domain=domain,
                discriminator={
                    "bootstrap_asset": discriminator,
                    "key": key(value),
                },
            ),
            key(value),
        ),
    )


def _building_targets(
    *,
    context: RandomContext,
    plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    target_level: int,
    historical_age_days: int,
) -> tuple[dict[str, int], dict[str, int]]:
    target_level = max(1, int(target_level))
    preferred = set(plan.building_focuses)
    ranked = _digest_ranked(
        context,
        domain="buildings",
        discriminator="levels",
        values=catalog.buildings,
        key=lambda entry: entry.key,
    )
    ranked.sort(key=lambda entry: entry.key not in preferred)
    core_keys = {
        BuildingKeys.SILVER_VAULT,
        BuildingKeys.GRANARY,
        BuildingKeys.JUXIAN_ZHUANG,
        BuildingKeys.JIADING_FANG,
        BuildingKeys.YOUXIA_BAOTA,
        BuildingKeys.LIANGGONG_CHANG,
    }
    anchor = next((entry for entry in ranked if entry.key in core_keys), None)
    if anchor is None:
        raise BootstrapAssetPlanningError("bootstrap catalog has no core building anchor")

    levels: dict[str, int] = {}
    offsets: dict[str, int] = {}
    for index, entry in enumerate(ranked):
        if entry.key == anchor.key:
            level = target_level
        else:
            spread = context.bucket(
                domain="buildings",
                discriminator={"level_spread": entry.key},
                bucket_count=min(3, target_level),
            )
            if entry.key in preferred:
                spread = min(spread, 1)
            level = max(1, target_level - spread)
        if entry.max_level is not None:
            level = min(level, entry.max_level)
        levels[entry.key] = level
        offsets[entry.key] = _history_offset(
            context,
            domain="buildings",
            discriminator={"created": entry.key, "level": level, "index": index},
            historical_age_days=historical_age_days,
        )
    if max(levels.values(), default=0) != target_level:
        raise BootstrapAssetPlanningError("building catalog cannot represent the target core building level")
    return levels, offsets


def _technology_targets(
    *,
    context: RandomContext,
    plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    building_level: int,
    historical_age_days: int,
) -> tuple[dict[str, int], dict[str, int]]:
    base_level = max(0, int(building_level) // 3)
    preferred = set(plan.technology_focuses)
    troop_classes = {troop_class for troop_class, _ratio in plan.troop_mix}
    levels: dict[str, int] = {}
    offsets: dict[str, int] = {}
    for entry in catalog.technologies:
        if entry.key in preferred:
            level = base_level
        elif entry.troop_class in troop_classes:
            level = max(0, base_level - 1)
        else:
            level = max(0, base_level - 2)
        if entry.troop_class in troop_classes and entry.key.endswith("_recruit"):
            level = max(1, level)
        level = min(level, entry.max_level)
        levels[entry.key] = level
        offsets[entry.key] = _history_offset(
            context,
            domain="technology",
            discriminator={"reached": entry.key, "level": level},
            historical_age_days=historical_age_days,
        )
    return levels, offsets


def _weighted_guest_choice(
    *,
    rng: random.Random,
    candidates: Sequence[GuestCatalogEntry],
    preferred_archetypes: set[str],
) -> GuestCatalogEntry:
    weighted = [
        (
            candidate,
            max(1, int(candidate.recruitment_weight)) * (3 if candidate.archetype in preferred_archetypes else 1),
        )
        for candidate in candidates
    ]
    total = sum(weight for _candidate, weight in weighted)
    target = rng.uniform(0, total)
    cumulative = 0.0
    for candidate, weight in weighted:
        cumulative += weight
        if target <= cumulative:
            return candidate
    return weighted[-1][0]


def _guest_arena_power(
    template: GuestCatalogEntry,
    attributes: GuestSeedAttributes,
) -> int:
    if template.archetype == "civil":
        attack = int(attributes.force * GUEST.CIVIL_FORCE_WEIGHT + attributes.intellect * GUEST.CIVIL_INTELLECT_WEIGHT)
    else:
        attack = int(
            attributes.force * GUEST.MILITARY_FORCE_WEIGHT + attributes.intellect * GUEST.MILITARY_INTELLECT_WEIGHT
        )
    max_hp = max(
        int(GUEST.MIN_HP_FLOOR),
        int(template.base_hp) + attributes.defense * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
    )
    return max(1, attack) + max(1, attributes.defense) + max_hp // 10


def _skill_is_eligible(
    skill: SkillCatalogEntry,
    *,
    level: int,
    attributes: GuestSeedAttributes,
) -> bool:
    return (
        level >= skill.required_level
        and attributes.force >= skill.required_force
        and attributes.intellect >= skill.required_intellect
        and attributes.defense >= skill.required_defense
        and attributes.agility >= skill.required_agility
    )


def _gear_targets(
    *,
    context: RandomContext,
    plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    guest_ordinal: int,
    guest_level: int,
    growth_stage: int,
) -> tuple[str, ...]:
    max_rank = _configured_max_rarity(
        {},
        field="gear_max_rarity_by_stage",
        growth_stage=growth_stage,
    )
    candidates = [
        entry
        for entry in catalog.gear
        if RARITY_RANK.get(entry.rarity, len(RARITY_ORDER)) <= max_rank
        and entry.extra_stats_digest == EMPTY_PAYLOAD_DIGEST
        and entry.set_bonus_digest == EMPTY_PAYLOAD_DIGEST
    ]
    preferred = set(plan.preferred_gear_stats)
    candidates.sort(
        key=lambda entry: (
            -int(
                ("force" in preferred and entry.attack_bonus > 0)
                or ("defense" in preferred and entry.defense_bonus > 0)
            ),
            context.digest(
                domain="gear",
                discriminator={
                    "guest_ordinal": guest_ordinal,
                    "template_key": entry.key,
                },
            ),
            entry.key,
        )
    )
    target_count = min(len(candidates), max(0, 1 + int(guest_level) // 12))
    selected: list[str] = []
    slot_counts: dict[str, int] = {}
    for entry in candidates:
        if len(selected) >= target_count:
            break
        current = slot_counts.get(entry.slot, 0)
        if current >= slot_capacity(entry.slot):
            continue
        selected.append(entry.key)
        slot_counts[entry.slot] = current + 1
    return tuple(selected)


def _guest_targets(
    *,
    context: RandomContext,
    plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    config: Mapping[str, Any],
    guest_count: int,
    max_guest_level: int,
    target_arena_power: int,
    growth_stage: int,
    historical_age_days: int,
) -> tuple[BootstrapGuestTarget, ...]:
    if guest_count <= 0:
        return ()
    max_rarity_rank = _configured_max_rarity(
        config,
        field="guest_max_rarity_by_stage",
        growth_stage=growth_stage,
    )
    allowed = [
        entry
        for entry in catalog.guests
        if entry.recruitable and RARITY_RANK.get(entry.rarity, len(RARITY_ORDER)) <= max_rarity_rank
    ]
    if not allowed:
        raise BootstrapAssetPlanningError("bootstrap catalog has no guest within the stage rarity cap")
    preferred = set(plan.preferred_guest_archetypes)
    selected: list[GuestCatalogEntry] = []
    remaining = list(allowed)
    selection_rng = context.random(
        domain="roster",
        discriminator="bootstrap-weighted-recruitment",
    )
    for archetype in plan.preferred_guest_archetypes:
        if len(selected) >= guest_count:
            break
        candidates = [entry for entry in remaining if entry.archetype == archetype]
        if not candidates:
            continue
        chosen = _weighted_guest_choice(
            rng=selection_rng,
            candidates=candidates,
            preferred_archetypes=preferred,
        )
        selected.append(chosen)
        remaining.remove(chosen)
    while remaining and len(selected) < guest_count:
        chosen = _weighted_guest_choice(
            rng=selection_rng,
            candidates=remaining,
            preferred_archetypes=preferred,
        )
        selected.append(chosen)
        remaining.remove(chosen)
    repeatable = [
        entry for entry in allowed if not entry.is_hermit and RARITY_RANK.get(entry.rarity, 99) <= RARITY_RANK["green"]
    ]
    while repeatable and len(selected) < guest_count:
        selected.append(
            _weighted_guest_choice(
                rng=selection_rng,
                candidates=repeatable,
                preferred_archetypes=preferred,
            )
        )
    if len(selected) != guest_count:
        raise BootstrapAssetPlanningError("bootstrap catalog cannot fill the guest target without illegal repeats")

    skill_by_key = {entry.key: entry for entry in catalog.skills}
    targets: list[BootstrapGuestTarget] = []
    actual_arena_power = 0
    core_count = max(1, math.ceil(guest_count * 0.2))
    secondary_count = max(0, math.ceil(guest_count * 0.35))
    for index, template in enumerate(selected):
        ordinal = index + 1
        if ordinal <= core_count:
            tier = "core"
            level_ratio = 1.0
        elif ordinal <= core_count + secondary_count:
            tier = "secondary"
            level_ratio = 0.75
        else:
            tier = "bench"
            level_ratio = 0.5
        level = max(1, int(max_guest_level * level_ratio))
        if ordinal == 1:
            level = max_guest_level
        attributes = guest_seed_attributes(
            context,
            ordinal=ordinal,
            template=template,
        )
        actual_arena_power += _guest_arena_power(template, attributes)
        if actual_arena_power > target_arena_power:
            raise BootstrapAssetPlanningError("guest catalog minimum strength exceeds the bootstrap arena cap")

        eligible_skills = [
            skill for skill in catalog.skills if _skill_is_eligible(skill, level=level, attributes=attributes)
        ]
        eligible_skills.sort(
            key=lambda skill: (
                skill.key not in template.initial_skill_keys,
                skill.kind not in set(plan.preferred_skill_kinds),
                context.digest(
                    domain="skills",
                    discriminator={
                        "guest_ordinal": ordinal,
                        "skill_key": skill.key,
                    },
                ),
                skill.key,
            )
        )
        skill_target_count = min(
            int(GUEST.MAX_SKILL_SLOTS),
            1 + level // 20,
            len(eligible_skills),
        )
        skill_keys = tuple(skill.key for skill in eligible_skills[:skill_target_count])
        if any(key not in skill_by_key for key in skill_keys):
            raise BootstrapAssetPlanningError("bootstrap selected an unknown skill")
        gear_keys = _gear_targets(
            context=context,
            plan=plan,
            catalog=catalog,
            guest_ordinal=ordinal,
            guest_level=level,
            growth_stage=growth_stage,
        )
        created_offset = _history_offset(
            context,
            domain="roster",
            discriminator={"guest_created": ordinal, "template_key": template.key},
            historical_age_days=historical_age_days,
        )
        gear_offsets = tuple(
            _history_offset(
                context,
                domain="gear",
                discriminator={
                    "guest_ordinal": ordinal,
                    "gear_key": gear_key,
                },
                historical_age_days=historical_age_days,
                minimum=created_offset,
            )
            for gear_key in gear_keys
        )
        skill_offsets = tuple(
            _history_offset(
                context,
                domain="skills",
                discriminator={
                    "guest_ordinal": ordinal,
                    "skill_key": skill_key,
                },
                historical_age_days=historical_age_days,
                minimum=created_offset,
            )
            for skill_key in skill_keys
        )
        targets.append(
            BootstrapGuestTarget(
                ordinal=ordinal,
                template_key=template.key,
                level=level,
                investment_tier=tier,
                gear_template_keys=gear_keys,
                skill_keys=skill_keys,
                created_day_offset=created_offset,
                gear_acquired_day_offsets=gear_offsets,
                skill_learned_day_offsets=skill_offsets,
            )
        )
    return tuple(targets)


def _largest_remainder_counts(
    total: int,
    weighted_keys: Sequence[tuple[str, float]],
) -> dict[str, int]:
    if total <= 0 or not weighted_keys:
        return {}
    weight_total = sum(weight for _key, weight in weighted_keys)
    if weight_total <= 0:
        raise BootstrapAssetPlanningError("troop weights must be positive")
    raw = [(key, total * weight / weight_total) for key, weight in weighted_keys]
    result = {key: int(math.floor(value)) for key, value in raw}
    remaining = total - sum(result.values())
    ranked = sorted(raw, key=lambda row: (-(row[1] - math.floor(row[1])), row[0]))
    for key, _value in ranked[:remaining]:
        result[key] += 1
    return {key: count for key, count in result.items() if count > 0}


def _troop_targets(
    *,
    context: RandomContext,
    plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    troop_total: int,
) -> dict[str, int]:
    if troop_total <= 0:
        return {}
    by_class: dict[str, list[TroopCatalogEntry]] = {}
    for entry in catalog.troops:
        by_class.setdefault(entry.troop_class, []).append(entry)
    weighted_templates: list[tuple[str, float]] = []
    for troop_class, weight in plan.troop_mix:
        candidates = by_class.get(troop_class, [])
        if not candidates:
            raise BootstrapAssetPlanningError(f"bootstrap catalog has no troop for class {troop_class!r}")
        ranked = _digest_ranked(
            context,
            domain="troops",
            discriminator=f"class:{troop_class}",
            values=candidates,
            key=lambda entry: entry.key,
        )
        weighted_templates.append((ranked[0].key, float(weight)))
    scout_candidates = by_class.get("scout", [])
    if scout_candidates and troop_total >= 20:
        ranked_scouts = _digest_ranked(
            context,
            domain="troops",
            discriminator="class:scout",
            values=scout_candidates,
            key=lambda entry: entry.key,
        )
        weighted_templates = [(key, weight * 0.95) for key, weight in weighted_templates]
        weighted_templates.append((ranked_scouts[0].key, 0.05))
    return _largest_remainder_counts(troop_total, weighted_templates)


def _inventory_slot_count(archetype: str, config: Mapping[str, Any]) -> int:
    projection = config.get("projection") or {}
    if not isinstance(projection, Mapping):
        return 0
    configured = projection.get("inventory_template_slots_by_archetype") or {}
    if not isinstance(configured, Mapping):
        return 0
    return max(0, int(configured.get(archetype, 0) or 0))


def _inventory_targets(
    *,
    context: RandomContext,
    archetype: str,
    config: Mapping[str, Any],
    catalog: BootstrapCatalog,
    historical_age_days: int,
) -> tuple[BootstrapInventoryTarget, ...]:
    projection = config.get("projection") or {}
    powerful_min_price = (
        int(projection.get("powerful_item_min_price") or 100_000) if isinstance(projection, Mapping) else 100_000
    )
    candidates = [
        entry
        for entry in catalog.inventory
        if entry.tradeable
        and entry.key != "grain"
        and RARITY_RANK.get(entry.rarity, len(RARITY_ORDER)) <= RARITY_RANK["green"]
        and entry.price < powerful_min_price
    ]
    candidates.sort(
        key=lambda entry: (
            RARITY_RANK.get(entry.rarity, len(RARITY_ORDER)),
            entry.price,
            context.digest(
                domain="inventory",
                discriminator={"template_key": entry.key},
            ),
            entry.key,
        )
    )
    slot_count = min(_inventory_slot_count(archetype, config), len(candidates))
    targets: list[BootstrapInventoryTarget] = []
    for entry in candidates[:slot_count]:
        quantity = 1 + context.bucket(
            domain="inventory",
            discriminator={"quantity": entry.key},
            bucket_count=3,
        )
        targets.append(
            BootstrapInventoryTarget(
                template_key=entry.key,
                quantity=quantity,
                storage_location="warehouse",
                acquired_day_offset=_history_offset(
                    context,
                    domain="inventory",
                    discriminator={"acquired": entry.key},
                    historical_age_days=historical_age_days,
                ),
            )
        )
    return tuple(targets)


def _resource_targets(
    *,
    context: RandomContext,
    archetype: str,
    config: Mapping[str, Any],
    building_levels: Mapping[str, int],
) -> tuple[int, int]:
    resources = config.get("resources") or {}
    configured = resources.get(archetype) if isinstance(resources, Mapping) else None
    if not isinstance(configured, Sequence) or len(configured) != 2:
        configured = (0.25, 0.55)
    low = max(0.0, min(1.0, float(configured[0])))
    high = max(low, min(1.0, float(configured[1])))
    fill = context.random(
        domain="bootstrap",
        discriminator=f"resources:{archetype}",
    ).uniform(low, high)
    silver_capacity = calculate_building_capacity(
        building_levels[BuildingKeys.SILVER_VAULT],
        is_silver_vault=True,
    )
    grain_capacity = calculate_building_capacity(
        building_levels[BuildingKeys.GRANARY],
        is_silver_vault=False,
    )
    return (
        max(1, min(silver_capacity, int(silver_capacity * fill))),
        max(1, min(grain_capacity, int(grain_capacity * fill))),
    )


def build_bootstrap_asset_targets(
    *,
    context: RandomContext,
    development_plan: BotDevelopmentPlan,
    catalog: BootstrapCatalog,
    config: Mapping[str, Any],
    projection: BotProjectionConfig,
    target_strength: StrengthSummary,
    archetype: str,
    historical_age_days: int,
) -> BootstrapAssetTargets:
    building_levels, building_offsets = _building_targets(
        context=context,
        plan=development_plan,
        catalog=catalog,
        target_level=int(projection.building_level),
        historical_age_days=historical_age_days,
    )
    technology_levels, technology_offsets = _technology_targets(
        context=context,
        plan=development_plan,
        catalog=catalog,
        building_level=int(projection.building_level),
        historical_age_days=historical_age_days,
    )
    guests = _guest_targets(
        context=context,
        plan=development_plan,
        catalog=catalog,
        config=config,
        guest_count=int(projection.guest_count),
        max_guest_level=(int(projection.guest_level) if int(projection.guest_count) > 0 else 0),
        target_arena_power=int(target_strength.components.get("arena_lineup_power", 0)),
        growth_stage=max(1, int(projection.building_level)),
        historical_age_days=historical_age_days,
    )
    troop_counts = _troop_targets(
        context=context,
        plan=development_plan,
        catalog=catalog,
        troop_total=int(projection.troop_count),
    )
    jiading_level = building_levels[BuildingKeys.JIADING_FANG]
    retainer_capacity = 50 + jiading_level * 100
    retainer_count = min(
        retainer_capacity,
        max(0, int(projection.troop_count) // 5),
    )
    inventory = _inventory_targets(
        context=context,
        archetype=str(archetype),
        config=config,
        catalog=catalog,
        historical_age_days=historical_age_days,
    )
    inventory_by_key = {entry.key: entry for entry in catalog.inventory}
    inventory_space = sum(inventory_by_key[item.template_key].storage_space * item.quantity for item in inventory)
    if inventory_space > 20_000:
        raise BootstrapAssetPlanningError("bootstrap inventory exceeds the initial storage capacity")
    silver, grain = _resource_targets(
        context=context,
        archetype=str(archetype),
        config=config,
        building_levels=building_levels,
    )
    return BootstrapAssetTargets(
        building_levels=building_levels,
        technology_levels=technology_levels,
        guests=guests,
        retainer_count=retainer_count,
        troop_counts=troop_counts,
        inventory=inventory,
        silver=silver,
        grain=grain,
        catalog_digest=catalog.digest,
        building_created_day_offsets=building_offsets,
        technology_reached_day_offsets=technology_offsets,
    )


__all__ = [
    "BootstrapAssetPlanningError",
    "GuestSeedAttributes",
    "build_bootstrap_asset_targets",
    "guest_random",
    "guest_seed_attributes",
]
