from __future__ import annotations

import random
from typing import Any

from django.db.models import Count

from battle.models import TroopTemplate
from core.config import GUEST
from gameplay.constants import BuildingKeys
from gameplay.models import BotProfile, Building, BuildingType, ItemTemplate, Manor, PlayerTechnology, PlayerTroop
from gameplay.services.manor.core import calculate_building_capacity
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestRarity,
    GuestSkill,
    GuestTemplate,
    Skill,
    SkillKind,
)
from guests.services.equipment_payloads import build_gear_template_defaults, build_gear_template_preview
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity
from guests.services.recruitment_guests import create_guest_from_template

from ..selectors import configured_keys as _configured_keys
from ..selectors import configured_model_keys as _configured_model_keys
from ..selectors import configured_technology_keys as _configured_technology_keys
from .projection import range_float as _range_float
from .projection import range_value as _range_value

CORE_BUILDING_KEYS = (
    BuildingKeys.SILVER_VAULT,
    BuildingKeys.GRANARY,
    BuildingKeys.JUXIAN_ZHUANG,
    BuildingKeys.JIADING_FANG,
    BuildingKeys.YOUXIA_BAOTA,
    BuildingKeys.LIANGGONG_CHANG,
)
GEAR_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}
GUEST_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}
INITIAL_BOT_GUEST_LEVEL = 1


def _project_buildings(manor: Manor, *, level: int) -> None:
    building_types = list(BuildingType.objects.filter(key__in=CORE_BUILDING_KEYS))
    existing_by_type = {row.building_type_id: row for row in manor.buildings.filter(building_type__in=building_types)}
    to_create: list[Building] = []
    to_update: list[Building] = []
    for building_type in building_types:
        building = existing_by_type.get(building_type.id)
        if building is None:
            to_create.append(Building(manor=manor, building_type=building_type, level=level))
        else:
            building.level = max(1, int(level))
            building.is_upgrading = False
            building.upgrade_complete_at = None
            to_update.append(building)
    if to_create:
        Building.objects.bulk_create(to_create)
    if to_update:
        Building.objects.bulk_update(to_update, ["level", "is_upgrading", "upgrade_complete_at"])

    manor.invalidate_building_cache()
    manor.silver_capacity = calculate_building_capacity(level, is_silver_vault=True)
    manor.grain_capacity = calculate_building_capacity(level, is_silver_vault=False)


def _resource_fill_for(archetype: str, rng: random.Random, config: dict[str, Any]) -> float:
    resource_config = config.get("resources") or {}
    values = resource_config.get(archetype) or resource_config.get(BotProfile.Archetype.BALANCED)
    return _range_float(rng, values, default=(0.25, 0.55))


def _project_resources(manor: Manor, *, archetype: str, rng: random.Random, config: dict[str, Any]) -> None:
    fill = _resource_fill_for(archetype, rng, config)
    manor.silver = max(1, min(manor.silver_capacity, int(manor.silver_capacity * fill)))
    manor.grain = max(1, min(manor.grain_capacity, int(manor.grain_capacity * fill)))


def _project_technologies(manor: Manor, *, level: int, config: dict[str, Any]) -> None:
    keys = _configured_technology_keys(config)
    if not keys:
        return
    target_level = max(0, int(level))
    PlayerTechnology.objects.bulk_create(
        [PlayerTechnology(manor=manor, tech_key=key, level=target_level, is_upgrading=False) for key in keys],
        ignore_conflicts=True,
    )
    technologies = list(PlayerTechnology.objects.filter(manor=manor, tech_key__in=keys))
    for technology in technologies:
        technology.level = target_level
        technology.is_upgrading = False
    PlayerTechnology.objects.bulk_update(technologies, ["level", "is_upgrading"])


def _grant_extra_template_skills(guest: Guest, *, limit: int | None = None) -> int:
    existing = set(guest.guest_skills.values_list("skill_id", flat=True))
    remaining_slots = max(0, int(GUEST.MAX_SKILL_SLOTS) - len(existing))
    if limit is not None:
        remaining_slots = min(remaining_slots, max(0, int(limit)))
    if remaining_slots <= 0:
        return 0

    rows: list[GuestSkill] = []
    for skill in guest.template.initial_skills.exclude(id__in=existing):
        if len(rows) >= remaining_slots:
            break
        rows.append(GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.TEMPLATE))
    if rows:
        GuestSkill.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _guest_meets_skill_requirements(guest: Guest, skill: Skill) -> bool:
    return (
        int(guest.level or 0) >= int(skill.required_level or 0)
        and int(guest.force or 0) >= int(skill.required_force or 0)
        and int(guest.intellect or 0) >= int(skill.required_intellect or 0)
        and int(guest.defense_stat or 0) >= int(skill.required_defense or 0)
        and int(guest.agility or 0) >= int(skill.required_agility or 0)
    )


def _grant_skills_from_pool(
    guest: Guest,
    *,
    rng: random.Random,
    skill_keys: list[str],
    target_count: int,
) -> int:
    if not skill_keys:
        return 0
    if target_count <= 0:
        return 0

    existing_ids = set(guest.guest_skills.values_list("skill_id", flat=True))
    remaining_slots = max(0, int(GUEST.MAX_SKILL_SLOTS) - len(existing_ids))
    if remaining_slots <= 0:
        return 0

    skills = list(Skill.objects.filter(key__in=skill_keys).exclude(id__in=existing_ids))
    rng.shuffle(skills)
    rows: list[GuestSkill] = []
    for skill in skills:
        if len(rows) >= min(target_count, remaining_slots):
            break
        if not _guest_meets_skill_requirements(guest, skill):
            continue
        rows.append(GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.BOOK))

    if rows:
        GuestSkill.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _chance_value(value: Any, *, default: float = 0.0) -> float:
    try:
        chance = float(value)
    except (TypeError, ValueError):
        chance = default
    return max(0.0, min(1.0, chance))


def _grant_skills_to_target(
    guest: Guest,
    *,
    rng: random.Random,
    skill_keys: list[str],
    target_total: int,
    preferred_high_tier_keys: set[str],
    prefer_passive_focus: bool,
) -> None:
    existing_records = list(guest.guest_skills.select_related("skill"))
    existing_ids = {record.skill_id for record in existing_records}
    remaining_slots = max(0, int(GUEST.MAX_SKILL_SLOTS) - len(existing_ids))
    needed = min(remaining_slots, max(0, int(target_total) - len(existing_ids)))
    if needed <= 0:
        return

    candidates = list(Skill.objects.filter(key__in=skill_keys).exclude(id__in=existing_ids))
    candidates = [skill for skill in candidates if _guest_meets_skill_requirements(guest, skill)]
    rng.shuffle(candidates)
    candidates.sort(key=lambda skill: 0 if skill.key in preferred_high_tier_keys else 1)

    selected: list[Skill] = []
    if prefer_passive_focus and int(target_total) >= 2:
        desired_kinds = [
            SkillKind.ACTIVE,
            *([SkillKind.PASSIVE] * (min(int(target_total), 3) - 1)),
        ]
        existing_kinds = [record.skill.kind for record in existing_records]
        for kind in desired_kinds:
            if existing_kinds.count(kind) + sum(skill.kind == kind for skill in selected) >= desired_kinds.count(kind):
                continue
            candidate = next(
                (skill for skill in candidates if skill.kind == kind and skill not in selected),
                None,
            )
            if candidate is not None:
                selected.append(candidate)
                if len(selected) >= needed:
                    break
    for candidate in candidates:
        if len(selected) >= needed:
            break
        if candidate not in selected:
            selected.append(candidate)
    if selected:
        GuestSkill.objects.bulk_create(
            [GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.BOOK) for skill in selected],
            ignore_conflicts=True,
        )


def _grant_configured_extra_skills(
    guest: Guest,
    *,
    growth_stage: int,
    rng: random.Random,
    config: dict[str, Any],
    max_new_skills: int | None = None,
) -> None:
    projection = config.get("projection") or {}
    existing_count = guest.guest_skills.count()

    def _bounded_target(target: int) -> int:
        if max_new_skills is None:
            return target
        return min(target, existing_count + max(0, int(max_new_skills)))

    early_stage_max = max(0, int(projection.get("early_stage_skill_max") or 6))
    if int(growth_stage) <= early_stage_max:
        target_total = _bounded_target(_range_value(rng, projection.get("early_stage_skill_count"), default=(0, 1)))
        _grant_skills_to_target(
            guest,
            rng=rng,
            skill_keys=_configured_keys(config, "extra_skill_keys"),
            target_total=target_total,
            preferred_high_tier_keys=set(),
            prefer_passive_focus=False,
        )
        return

    high_tier_keys = _configured_keys(config, "high_tier_skill_keys")
    high_tier_chance = _chance_value(projection.get("high_tier_skill_chance"), default=0.0)
    granted_high_tier_count = 0
    if high_tier_chance > 0 and rng.random() < high_tier_chance:
        granted_high_tier_count = _range_value(rng, projection.get("high_tier_skills_per_guest"), default=(1, 1))
    target_total = _bounded_target(
        min(
            int(GUEST.MAX_SKILL_SLOTS),
            existing_count
            + granted_high_tier_count
            + _range_value(rng, projection.get("extra_skills_per_guest"), default=(0, 0)),
        )
    )
    _grant_skills_to_target(
        guest,
        rng=rng,
        skill_keys=[*high_tier_keys, *_configured_keys(config, "extra_skill_keys")],
        target_total=target_total,
        preferred_high_tier_keys=set(high_tier_keys) if granted_high_tier_count else set(),
        prefer_passive_focus=rng.random()
        < _chance_value(projection.get("multi_skill_passive_focus_chance"), default=0.75),
    )


def _equip_template(guest: Guest, template: GearTemplate) -> None:
    gear = GearItem.objects.create(manor=guest.manor, template=template, guest=guest)
    updates = {"attack_bonus", "defense_bonus"}
    apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))


def _gear_rarity_rank(template: GearTemplate) -> int:
    return int(GEAR_RARITY_RANK.get(str(template.rarity), -1))


def _gear_template_power(template: GearTemplate) -> int:
    extra_stats = template.extra_stats if isinstance(template.extra_stats, dict) else {}
    return (
        int(template.attack_bonus or 0)
        + int(template.defense_bonus or 0)
        + sum(int(value or 0) for value in extra_stats.values() if isinstance(value, int))
    )


def _max_configured_rarity_rank(
    growth_stage: int,
    configured: Any,
    rarity_ranks: dict[str, int],
) -> int:
    if not isinstance(configured, dict):
        return -1

    selected_rank = -1
    for raw_stage, rarity in configured.items():
        if isinstance(raw_stage, bool):
            continue
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        rank = rarity_ranks.get(str(rarity), -1)
        if 0 < stage <= int(growth_stage) and rank >= 0:
            selected_rank = max(selected_rank, rank)
    return selected_rank


def _gear_max_rarity_for_stage(growth_stage: int, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("gear_max_rarity_by_stage") or {}
    selected_rank = _max_configured_rarity_rank(growth_stage, configured, GEAR_RARITY_RANK)
    if selected_rank >= 0:
        return selected_rank
    return GEAR_RARITY_RANK[GuestRarity.GREEN]


def _guest_max_rarity_for_stage(growth_stage: int, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("guest_max_rarity_by_stage") or {}
    selected_rank = _max_configured_rarity_rank(growth_stage, configured, GUEST_RARITY_RANK)
    if selected_rank >= 0:
        return selected_rank
    return GUEST_RARITY_RANK[GuestRarity.GREEN]


def _remove_virtual_gear(guest: Guest, gear: GearItem, *, updates: set[str]) -> None:
    apply_template_stats_to_guest(guest, gear.template, -1, updates)
    gear.delete()


def _reconcile_guest_gear(
    guest: Guest,
    *,
    growth_stage: int,
    rng: random.Random,
    config: dict[str, Any],
    max_changes: int | None = None,
) -> int:
    templates = [
        template
        for template in _configured_gear_templates(config)
        if _gear_rarity_rank(template) <= _gear_max_rarity_for_stage(growth_stage, config)
    ]
    if not templates:
        return 0

    templates_by_slot: dict[str, list[GearTemplate]] = {}
    for template in templates:
        templates_by_slot.setdefault(str(template.slot), []).append(template)
    for candidates in templates_by_slot.values():
        rng.shuffle(candidates)
        candidates.sort(
            key=lambda template: (
                _gear_rarity_rank(template),
                _gear_template_power(template),
            ),
            reverse=True,
        )

    existing_by_slot: dict[str, list[GearItem]] = {}
    for gear in guest.gear_items.select_related("template"):
        existing_by_slot.setdefault(str(gear.template.slot), []).append(gear)

    updates = {"attack_bonus", "defense_bonus"}
    changed = 0
    for slot in GearSlot:
        if max_changes is not None and changed >= max(0, int(max_changes)):
            break
        slot_key = slot.value
        capacity = slot_capacity(slot_key)
        candidates = templates_by_slot.get(slot_key, [])
        if not candidates:
            continue
        desired = candidates[:capacity]
        current = existing_by_slot.get(slot_key, [])
        current.sort(
            key=lambda gear: (
                _gear_rarity_rank(gear.template),
                _gear_template_power(gear.template),
            ),
            reverse=True,
        )

        kept: list[GearItem] = []
        seen_templates: set[int] = set()
        for gear in current:
            if gear.template_id in seen_templates or len(kept) >= capacity:
                _remove_virtual_gear(guest, gear, updates=updates)
                continue
            seen_templates.add(gear.template_id)
            kept.append(gear)

        for candidate in desired:
            if max_changes is not None and changed >= max(0, int(max_changes)):
                break
            if any(gear.template_id == candidate.id for gear in kept):
                continue
            weaker = [gear for gear in kept if _gear_rarity_rank(gear.template) < _gear_rarity_rank(candidate)]
            if weaker:
                replaced = min(
                    weaker,
                    key=lambda gear: (
                        _gear_rarity_rank(gear.template),
                        _gear_template_power(gear.template),
                    ),
                )
                _remove_virtual_gear(guest, replaced, updates=updates)
                kept.remove(replaced)
            elif len(kept) >= capacity:
                continue
            _equip_template(guest, candidate)
            kept.append(guest.gear_items.select_related("template").get(template=candidate))
            changed += 1

    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)
    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
    return changed


def _configured_gear_templates(config: dict[str, Any]) -> list[GearTemplate]:
    keys = _configured_model_keys(config, "gear_template_keys", GearTemplate)
    if not keys:
        return []
    unique_keys = list(dict.fromkeys(keys))
    templates_by_key = {template.key: template for template in GearTemplate.objects.filter(key__in=unique_keys)}
    missing_keys = [key for key in unique_keys if key not in templates_by_key]
    if missing_keys:
        item_templates = ItemTemplate.objects.filter(key__in=missing_keys, effect_type__startswith="equip_")
        for item_template in item_templates:
            preview = build_gear_template_preview(item_template)
            if preview is None:
                continue
            template, _created = GearTemplate.objects.update_or_create(
                key=item_template.key,
                defaults=build_gear_template_defaults(item_template, slot=preview.slot),
            )
            templates_by_key[template.key] = template
    return [templates_by_key[key] for key in unique_keys if key in templates_by_key]


def _diverse_guest_templates(templates: list[GuestTemplate], *, rng: random.Random) -> list[GuestTemplate]:
    if len(templates) <= 1:
        return templates
    usage_counts = {
        row["template_id"]: row["count"]
        for row in (
            Guest.objects.filter(
                manor__bot_profile__state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
                template__in=templates,
            )
            .values("template_id")
            .annotate(count=Count("id"))
        )
    }
    diversified = list(templates)
    rng.shuffle(diversified)
    diversified.sort(key=lambda template: int(usage_counts.get(template.id, 0)))
    return diversified


def _configured_guest_templates(config: dict[str, Any]) -> list[GuestTemplate]:
    guest_keys = _configured_model_keys(config, "guest_template_keys", GuestTemplate)
    if not guest_keys:
        return []
    return list(GuestTemplate.objects.filter(key__in=guest_keys).order_by("key").prefetch_related("initial_skills"))


def _promote_one_virtual_guest_rarity(
    manor: Manor,
    *,
    growth_stage: int,
    rng: random.Random,
    config: dict[str, Any],
    guest_rarity_cap: str | None = None,
) -> bool:
    templates = _configured_guest_templates(config)
    if not templates:
        return False
    max_rank = _guest_max_rarity_for_stage(growth_stage, config)
    if guest_rarity_cap is not None:
        max_rank = GUEST_RARITY_RANK.get(str(guest_rarity_cap), max_rank)
    owned_template_ids = set(manor.guests.values_list("template_id", flat=True))
    guests = list(manor.guests.select_related("template").order_by("id"))
    guests.sort(
        key=lambda guest: (
            GUEST_RARITY_RANK.get(str(guest.template.rarity), -1),
            int(guest.level),
            int(guest.id),
        )
    )
    for guest in guests:
        current_rank = GUEST_RARITY_RANK.get(str(guest.template.rarity), -1)
        eligible = [
            template
            for template in templates
            if current_rank < GUEST_RARITY_RANK.get(str(template.rarity), -1) <= max_rank
        ]
        if not eligible:
            continue
        next_rank = min(GUEST_RARITY_RANK[str(template.rarity)] for template in eligible)
        candidates = [template for template in eligible if GUEST_RARITY_RANK[str(template.rarity)] == next_rank]
        rng.shuffle(candidates)
        candidates.sort(
            key=lambda template: (
                template.archetype != guest.template.archetype,
                template.id in owned_template_ids,
            )
        )
        target_template = candidates[0]
        projected = create_guest_from_template(
            manor=manor,
            template=target_template,
            rng=rng,
            grant_skills=False,
            save=False,
        )
        guest.template = target_template
        for field_name in ("force", "intellect", "defense_stat", "agility", "luck"):
            setattr(
                guest,
                field_name,
                max(int(getattr(guest, field_name)), int(getattr(projected, field_name))),
            )
        for field_name in (
            "initial_force",
            "initial_intellect",
            "initial_defense",
            "initial_agility",
        ):
            setattr(
                guest,
                field_name,
                max(int(getattr(guest, field_name)), int(getattr(projected, field_name))),
            )
        guest.save(
            update_fields=[
                "template",
                "force",
                "intellect",
                "defense_stat",
                "agility",
                "luck",
                "initial_force",
                "initial_intellect",
                "initial_defense",
                "initial_agility",
            ]
        )
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
        return True
    return False


def _project_guests_and_gear(
    manor: Manor,
    *,
    count: int,
    level: int,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
    growth_stage: int,
    grant_configured_skills: bool = True,
    quality_enabled: bool = True,
) -> None:
    if count <= 0:
        return
    templates = _configured_guest_templates(config)
    if not templates:
        return
    max_rarity_rank = _guest_max_rarity_for_stage(growth_stage, config)
    templates = [
        template for template in templates if GUEST_RARITY_RANK.get(str(template.rarity), -1) <= max_rarity_rank
    ]
    if not templates:
        return
    if not quality_enabled:
        lowest_rank = min(GUEST_RARITY_RANK.get(str(template.rarity), -1) for template in templates)
        templates = [
            template for template in templates if GUEST_RARITY_RANK.get(str(template.rarity), -1) == lowest_rank
        ]
    templates = _diverse_guest_templates(templates, rng=rng)
    for idx in range(max(0, int(count))):
        template = templates[idx % len(templates)]
        guest = create_guest_from_template(
            manor=manor,
            template=template,
            rng=rng,
            grant_skills=quality_enabled,
        )
        guest.level = max(1, int(level))
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["level", "current_hp"])
        if quality_enabled:
            _grant_extra_template_skills(guest)
        if quality_enabled and grant_configured_skills:
            _grant_configured_extra_skills(guest, growth_stage=growth_stage, rng=rng, config=config)
        if quality_enabled:
            _reconcile_guest_gear(guest, growth_stage=growth_stage, rng=rng, config=config)


def _project_troops(manor: Manor, *, count: int, config: dict[str, Any]) -> None:
    troop_keys = _configured_model_keys(config, "troop_template_keys", TroopTemplate)
    if not troop_keys:
        return
    templates = list(TroopTemplate.objects.filter(key__in=troop_keys).order_by("key"))
    if not templates:
        return
    PlayerTroop.objects.filter(manor=manor).exclude(troop_template__in=templates).update(count=0)
    per_type, remainder = divmod(max(0, int(count)), len(templates))
    target_counts = {
        template.id: per_type + (1 if index < remainder else 0) for index, template in enumerate(templates)
    }
    PlayerTroop.objects.bulk_create(
        [PlayerTroop(manor=manor, troop_template=template, count=target_counts[template.id]) for template in templates],
        ignore_conflicts=True,
    )
    troops = list(PlayerTroop.objects.filter(manor=manor, troop_template_id__in=target_counts))
    for troop in troops:
        troop.count = target_counts[troop.troop_template_id]
    PlayerTroop.objects.bulk_update(troops, ["count"])


__all__ = [
    "_grant_configured_extra_skills",
    "_grant_extra_template_skills",
    "_project_buildings",
    "_project_guests_and_gear",
    "_project_resources",
    "_project_technologies",
    "_project_troops",
    "_promote_one_virtual_guest_rarity",
    "_reconcile_guest_gear",
]
