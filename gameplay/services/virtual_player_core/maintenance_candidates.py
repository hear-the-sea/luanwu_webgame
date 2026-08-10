from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from core.exceptions import GuestNotRequirementError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.core import GRAIN_ITEM_KEY
from guests.models import GearItem, GearTemplate, Guest, GuestSkill, GuestStatus, Skill
from guests.services.equipment_payloads import (
    GEAR_EXTRA_STAT_FIELDS,
    build_gear_template_preview,
    normalize_active_set_bonus,
    normalize_extra_stats,
)
from guests.services.equipment_stats import slot_capacity
from guests.services.skills import MAX_GUEST_SKILL_SLOTS, assert_guest_meets_skill_requirements
from guests.utils.equipment_utils import SET_STAT_FIELD_MAP, compute_set_bonus

from .bootstrap_assets import RARITY_ORDER, RARITY_RANK
from .inventory_budget import inventory_daily_cap_limits
from .maintenance_action_specs import (
    EquipmentEquipActionSpec,
    InventoryAcquisitionActionSpec,
    MaintenanceActionSpec,
    SkillLearningActionSpec,
    project_maintenance_action_intent,
)
from .projection import DevelopmentIntent, StrengthSummary, calculate_guest_arena_power
from .strategy import BotDevelopmentPlan


class MaintenanceCandidateError(ValueError):
    pass


_PROJECTED_GUEST_FIELDS = (
    "attack_bonus",
    "defense_bonus",
    "force",
    "intellect",
    "defense_stat",
    "agility",
    "luck",
    "hp_bonus",
    "troop_capacity_bonus",
)
_GEAR_STAT_UTILITY_WEIGHTS = {
    "agility": 0.5,
    "attack": 1.0,
    "defense": 1.0,
    "force": 1.0,
    "hp": 0.01,
    "intellect": 1.0,
    "luck": 0.25,
    "troop_capacity": 0.1,
}


def _configured_gear_max_rarity_rank(
    config: Mapping[str, Any],
    *,
    growth_stage: int,
) -> int:
    projection = config.get("projection") or {}
    if not isinstance(projection, Mapping):
        raise MaintenanceCandidateError("projection config must be a mapping")
    configured = projection.get("gear_max_rarity_by_stage") or {}
    if not isinstance(configured, Mapping):
        raise MaintenanceCandidateError("projection.gear_max_rarity_by_stage must be a mapping")

    selected = RARITY_RANK["green"]
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


def _apply_direct_gear_stats(
    projected: dict[str, int],
    template: object,
    *,
    sign: int,
) -> None:
    projected["attack_bonus"] += sign * int(getattr(template, "attack_bonus", 0) or 0)
    projected["defense_bonus"] += sign * int(getattr(template, "defense_bonus", 0) or 0)
    extra_stats = normalize_extra_stats(getattr(template, "extra_stats", None))
    for stat, field in GEAR_EXTRA_STAT_FIELDS.items():
        projected[field] += sign * int(extra_stats.get(stat, 0))


def _apply_active_set_bonus(
    projected: dict[str, int],
    bonus: Mapping[str, int],
    *,
    sign: int,
) -> None:
    for stat, field in SET_STAT_FIELD_MAP.items():
        projected[field] += sign * int(bonus.get(stat, 0))


def _semantic_stat_deltas(
    *,
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> dict[str, int]:
    return {
        "agility": int(after["agility"] - before["agility"]),
        "defense": int(
            after["defense_stat"] - before["defense_stat"] + after["defense_bonus"] - before["defense_bonus"]
        ),
        "force": int(after["force"] - before["force"]),
        "hp": int(after["hp_bonus"] - before["hp_bonus"]),
        "intellect": int(after["intellect"] - before["intellect"]),
        "luck": int(after["luck"] - before["luck"]),
        "troop_capacity": int(after["troop_capacity_bonus"] - before["troop_capacity_bonus"]),
    }


def _positive_weighted_stat_gain(
    deltas: Mapping[str, int],
    *,
    stats: set[str] | None = None,
) -> float:
    included = set(deltas) if stats is None else stats
    return sum(
        max(0, int(deltas.get(stat, 0))) * _GEAR_STAT_UTILITY_WEIGHTS.get(stat, 0.0) for stat in sorted(included)
    )


def _positive_set_completion_gain(
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> float:
    return sum(
        max(0, int(after.get(stat, 0)) - int(before.get(stat, 0))) * _GEAR_STAT_UTILITY_WEIGHTS.get(stat, 0.0)
        for stat in sorted(set(before) | set(after))
    )


def _equipment_utility_score(
    *,
    guest: Guest,
    item: InventoryItem,
    development_plan: BotDevelopmentPlan,
    stat_deltas: Mapping[str, int],
    power_gain: int,
    set_completion_gain: float,
    replacing: bool,
) -> float:
    preferred_gain = _positive_weighted_stat_gain(
        stat_deltas,
        stats=set(development_plan.preferred_gear_stats),
    )
    role_stats = (
        {"agility", "defense", "hp", "intellect"}
        if str(guest.template.archetype) == "civil"
        else {"agility", "defense", "force", "hp"}
    )
    role_gain = _positive_weighted_stat_gain(stat_deltas, stats=role_stats)
    role_weight = 0.1 + 0.15 * float(development_plan.roster_focus)
    preference_weight = 0.2 + 0.2 * float(development_plan.optimization_bias)
    gross_value = max(
        0.001,
        1.0
        + float(power_gain)
        + preference_weight * preferred_gain
        + role_weight * role_gain
        + 0.2 * set_completion_gain,
    )
    scarcity_cost = (1.0 + max(0, int(item.template.price or 0)) / 5_000.0) * (
        1.0 + 1.0 / max(1, int(item.quantity or 0))
    )
    swap_inertia = 1.0 + float(development_plan.inertia_bias) if replacing else 1.0
    return round(gross_value / scarcity_cost / swap_inertia, 12)


def _normalized_inventory_template_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MaintenanceCandidateError("inventory_template_keys must be a persisted list")
    normalized: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise MaintenanceCandidateError("inventory_template_keys entries must be non-empty strings")
        normalized.append(entry.strip())
    if len(set(normalized)) != len(normalized):
        raise MaintenanceCandidateError("inventory_template_keys entries must be unique")
    return tuple(normalized)


def _warehouse_items(
    *,
    manor_id: int,
    effect_types: tuple[str, ...] | None = None,
    template_keys: tuple[str, ...] | None = None,
) -> tuple[InventoryItem, ...]:
    queryset = (
        InventoryItem.objects.filter(
            manor_id=manor_id,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
        )
        .select_related("template")
        .order_by("template__key", "id")
    )
    if effect_types is not None:
        queryset = queryset.filter(template__effect_type__in=effect_types)
    if template_keys is not None:
        queryset = queryset.filter(template__key__in=template_keys)
    return tuple(queryset)


def build_skill_learning_candidates(
    *,
    manor_id: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    guests: tuple[Guest, ...],
    guest_skills: tuple[GuestSkill, ...] | None = None,
    warehouse_items: tuple[InventoryItem, ...] | None = None,
    skills: tuple[Skill, ...] | None = None,
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, MaintenanceActionSpec],
]:
    candidate_guests = tuple(
        guest for guest in guests if guest.status == GuestStatus.IDLE and guest.training_complete_at is None
    )
    if not candidate_guests:
        return (), {}

    guest_ids = tuple(int(guest.id) for guest in candidate_guests)
    guest_id_set = set(guest_ids)
    learned_by_guest: dict[int, set[int]] = defaultdict(set)
    kind_counts_by_guest: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    loaded_guest_skills = (
        tuple(
            GuestSkill.objects.filter(guest_id__in=guest_ids).select_related("skill").order_by("guest_id", "skill_id")
        )
        if guest_skills is None
        else tuple(
            sorted(
                (guest_skill for guest_skill in guest_skills if int(guest_skill.guest_id) in guest_id_set),
                key=lambda guest_skill: (
                    int(guest_skill.guest_id),
                    int(guest_skill.skill_id),
                ),
            )
        )
    )
    for guest_skill in loaded_guest_skills:
        guest_id = int(guest_skill.guest_id)
        learned_by_guest[guest_id].add(int(guest_skill.skill_id))
        kind_counts_by_guest[guest_id][str(guest_skill.skill.kind)] += 1

    books = (
        _warehouse_items(
            manor_id=manor_id,
            effect_types=(ItemTemplate.EffectType.SKILL_BOOK,),
        )
        if warehouse_items is None
        else tuple(
            sorted(
                (
                    item
                    for item in warehouse_items
                    if int(item.manor_id) == manor_id
                    and item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
                    and int(item.quantity or 0) > 0
                    and item.template.effect_type == ItemTemplate.EffectType.SKILL_BOOK
                ),
                key=lambda item: (str(item.template.key), int(item.id)),
            )
        )
    )
    if not books:
        return (), {}

    book_by_skill_key: dict[str, InventoryItem] = {}
    for item in books:
        payload = item.template.effect_payload
        if not isinstance(payload, dict):
            raise MaintenanceCandidateError(f"skill book {item.template.key!r} has an invalid effect payload")
        raw_skill_key = payload.get("skill_key")
        if not isinstance(raw_skill_key, str) or not raw_skill_key.strip():
            raise MaintenanceCandidateError(f"skill book {item.template.key!r} has no valid skill_key")
        book_by_skill_key.setdefault(raw_skill_key.strip(), item)

    skills_by_key = (
        Skill.objects.in_bulk(book_by_skill_key, field_name="key")
        if skills is None
        else {str(skill.key): skill for skill in skills if str(skill.key) in book_by_skill_key}
    )
    missing_skill_keys = sorted(set(book_by_skill_key) - set(skills_by_key))
    if missing_skill_keys:
        raise MaintenanceCandidateError("skill books reference missing skills: " + ", ".join(missing_skill_keys))

    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    preferred_kinds = set(development_plan.preferred_skill_kinds)
    preferred_archetypes = set(development_plan.preferred_guest_archetypes)
    for guest in candidate_guests:
        guest_id = int(guest.id)
        learned_skill_ids = learned_by_guest[guest_id]
        if len(learned_skill_ids) >= MAX_GUEST_SKILL_SLOTS:
            continue
        for skill_key in sorted(book_by_skill_key):
            skill = skills_by_key[skill_key]
            if int(skill.id) in learned_skill_ids:
                continue
            try:
                assert_guest_meets_skill_requirements(guest, skill)
            except GuestNotRequirementError:
                continue
            item = book_by_skill_key[skill_key]
            spec = SkillLearningActionSpec(
                guest_id=guest_id,
                inventory_item_id=int(item.id),
                item_template_id=int(item.template_id),
                item_key=str(item.template.key),
                item_quantity_before=int(item.quantity),
                skill_id=int(skill.id),
                skill_key=str(skill.key),
            )
            kind = str(skill.kind)
            kind_gap = max(
                0,
                max(kind_counts_by_guest[guest_id].values(), default=0) - kind_counts_by_guest[guest_id][kind],
            )
            preference = 1.0 + (0.25 if kind in preferred_kinds else 0.0)
            role_fit = 1.0 + (
                development_plan.roster_focus if str(guest.template.archetype) in preferred_archetypes else 0.0
            )
            scarcity_cost = max(1, int(item.template.price or 0) + 1_000)
            utility_score = (
                max(1, int(skill.base_power or 0)) * preference * role_fit * (1.0 + 0.1 * kind_gap) / scarcity_cost
            )
            intent = project_maintenance_action_intent(
                spec=spec,
                source_prestige_band=prestige_band,
                target_prestige_band=prestige_band,
                strength_before=strength_before,
                strength_after=strength_before,
                utility_score=utility_score,
            )
            candidates.append(intent)
            specs[intent.business_key] = spec
    return tuple(candidates), specs


def build_equipment_equip_candidates(
    *,
    manor_id: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    growth_stage: int,
    config: Mapping[str, Any],
    guests: tuple[Guest, ...],
    gear_items: tuple[GearItem, ...] | None = None,
    warehouse_items: tuple[InventoryItem, ...] | None = None,
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, MaintenanceActionSpec],
]:
    candidate_guests = tuple(
        sorted(
            (
                guest
                for guest in guests
                if guest.id is not None
                and int(guest.manor_id) == manor_id
                and guest.status == GuestStatus.IDLE
                and guest.training_complete_at is None
            ),
            key=lambda guest: int(guest.id),
        )
    )
    if not candidate_guests:
        return (), {}

    guest_ids = tuple(int(guest.id) for guest in candidate_guests)
    guest_id_set = set(guest_ids)
    loaded_gear_items = (
        tuple(
            GearItem.objects.filter(
                manor_id=manor_id,
                guest_id__in=guest_ids,
            )
            .select_related("template")
            .order_by("guest_id", "id")
        )
        if gear_items is None
        else tuple(
            sorted(
                (
                    gear
                    for gear in gear_items
                    if int(gear.manor_id) == manor_id
                    and gear.guest_id is not None
                    and int(gear.guest_id) in guest_id_set
                ),
                key=lambda gear: (
                    int(gear.guest_id or 0),
                    int(gear.id or 0),
                ),
            )
        )
    )
    gear_by_guest: dict[int, list[GearItem]] = defaultdict(list)
    for gear in loaded_gear_items:
        if gear.guest_id is None:
            continue
        gear_by_guest[int(gear.guest_id)].append(gear)

    loaded_warehouse_items = (
        _warehouse_items(manor_id=manor_id)
        if warehouse_items is None
        else tuple(
            sorted(
                (
                    item
                    for item in warehouse_items
                    if int(item.manor_id) == manor_id
                    and item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
                    and int(item.quantity or 0) > 0
                ),
                key=lambda item: (str(item.template.key), int(item.id)),
            )
        )
    )
    max_rarity_rank = _configured_gear_max_rarity_rank(
        config,
        growth_stage=growth_stage,
    )
    equipment_options: list[tuple[InventoryItem, GearTemplate]] = []
    seen_item_keys: set[str] = set()
    for item in loaded_warehouse_items:
        item_key = str(item.template.key)
        if item_key in seen_item_keys:
            continue
        try:
            preview = build_gear_template_preview(item.template)
        except AssertionError as exc:
            raise MaintenanceCandidateError(f"equipment item {item_key!r} has an invalid payload") from exc
        if preview is None:
            continue
        rarity_rank = RARITY_RANK.get(
            str(preview.rarity),
            len(RARITY_ORDER),
        )
        if rarity_rank > max_rarity_rank:
            continue
        seen_item_keys.add(item_key)
        equipment_options.append((item, preview))
    if not equipment_options:
        return (), {}

    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    lineup_power_before = float(strength_before.components["arena_lineup_power"])
    troop_total = float(strength_before.components["troop_total"])
    replacement_threshold = 0.08 + 0.07 * float(development_plan.inertia_bias)
    for guest in candidate_guests:
        guest_id = int(guest.id)
        equipped = gear_by_guest[guest_id]
        guest_power_before = calculate_guest_arena_power(
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
            hp_bonus=int(guest.hp_bonus),
            archetype=str(guest.template.archetype),
            base_hp=int(guest.template.base_hp),
        )
        persisted_state = {field: int(getattr(guest, field) or 0) for field in _PROJECTED_GUEST_FIELDS}
        current_set_bonus = normalize_active_set_bonus(guest.gear_set_bonus)
        for item, preview in equipment_options:
            slot = str(preview.slot)
            capacity = slot_capacity(slot)
            same_slot = [gear for gear in equipped if str(gear.template.slot) == slot]
            if any(str(gear.template.name) == str(preview.name) for gear in same_slot):
                continue
            if capacity > 1 and len(same_slot) >= capacity:
                continue

            replacing = capacity == 1 and bool(same_slot)
            projected_state = dict(persisted_state)
            _apply_active_set_bonus(
                projected_state,
                current_set_bonus,
                sign=-1,
            )
            if replacing:
                for gear in same_slot:
                    _apply_direct_gear_stats(
                        projected_state,
                        gear.template,
                        sign=-1,
                    )
            _apply_direct_gear_stats(
                projected_state,
                preview,
                sign=1,
            )

            projected_gears: list[object] = [
                gear for gear in equipped if not replacing or str(gear.template.slot) != slot
            ]
            projected_gears.append(SimpleNamespace(template=preview))
            new_set_bonus = normalize_active_set_bonus(compute_set_bonus(projected_gears))
            _apply_active_set_bonus(
                projected_state,
                new_set_bonus,
                sign=1,
            )

            guest_power_after = calculate_guest_arena_power(
                force=projected_state["force"],
                intellect=projected_state["intellect"],
                defense=projected_state["defense_stat"],
                agility=projected_state["agility"],
                hp_bonus=projected_state["hp_bonus"],
                archetype=str(guest.template.archetype),
                base_hp=int(guest.template.base_hp),
            )
            power_gain = guest_power_after - guest_power_before
            if power_gain < 0:
                continue
            if replacing and power_gain / max(1, guest_power_before) < replacement_threshold:
                continue

            lineup_power_after = lineup_power_before + power_gain
            components_after = dict(strength_before.components)
            components_after["arena_lineup_power"] = lineup_power_after
            strength_after = StrengthSummary(
                composite=lineup_power_after + 2.0 * troop_total,
                components=components_after,
            )
            spec = EquipmentEquipActionSpec(
                guest_id=guest_id,
                inventory_item_id=int(item.id),
                item_template_id=int(item.template_id),
                item_key=str(item.template.key),
                item_quantity_before=int(item.quantity),
                slot=slot,
            )
            stat_deltas = _semantic_stat_deltas(
                before=persisted_state,
                after=projected_state,
            )
            utility_score = _equipment_utility_score(
                guest=guest,
                item=item,
                development_plan=development_plan,
                stat_deltas=stat_deltas,
                power_gain=power_gain,
                set_completion_gain=_positive_set_completion_gain(
                    current_set_bonus,
                    new_set_bonus,
                ),
                replacing=replacing,
            )
            intent = project_maintenance_action_intent(
                spec=spec,
                source_prestige_band=prestige_band,
                target_prestige_band=prestige_band,
                strength_before=strength_before,
                strength_after=strength_after,
                utility_score=utility_score,
            )
            candidates.append(intent)
            specs[intent.business_key] = spec
    return tuple(candidates), specs


_INVENTORY_EFFECT_PRIORITY = {
    ItemTemplate.EffectType.SKILL_BOOK: 5,
    "equip_helmet": 5,
    "equip_armor": 5,
    "equip_shoes": 5,
    "equip_weapon": 5,
    "equip_mount": 5,
    "equip_ornament": 5,
    "equip_device": 5,
    ItemTemplate.EffectType.MEDICINE: 4,
    ItemTemplate.EffectType.EXPERIENCE_ITEM: 3,
    ItemTemplate.EffectType.TOOL: 2,
    ItemTemplate.EffectType.LOOT_BOX: 1,
    ItemTemplate.EffectType.RESOURCE_PACK: 1,
    ItemTemplate.EffectType.RESOURCE: 1,
}


def build_inventory_acquisition_candidates(
    *,
    manor_id: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    inventory_template_keys: object,
    inventory_cap_config: dict[str, Any],
    inventory_templates: tuple[ItemTemplate, ...] | None = None,
    warehouse_items: tuple[InventoryItem, ...] | None = None,
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, MaintenanceActionSpec],
]:
    template_keys = _normalized_inventory_template_keys(inventory_template_keys)
    if not template_keys:
        return (), {}
    template_key_set = set(template_keys)

    templates_by_key = (
        ItemTemplate.objects.in_bulk(template_keys, field_name="key")
        if inventory_templates is None
        else {str(template.key): template for template in inventory_templates if str(template.key) in template_key_set}
    )
    missing_keys = sorted(set(template_keys) - set(templates_by_key))
    if missing_keys:
        raise MaintenanceCandidateError("inventory pool references missing templates: " + ", ".join(missing_keys))
    invalid_keys = sorted(key for key, template in templates_by_key.items() if not template.tradeable)
    if invalid_keys:
        raise MaintenanceCandidateError("inventory pool references non-tradeable templates: " + ", ".join(invalid_keys))

    loaded_items = (
        _warehouse_items(
            manor_id=manor_id,
            template_keys=template_keys,
        )
        if warehouse_items is None
        else tuple(
            item
            for item in warehouse_items
            if int(item.manor_id) == manor_id
            and item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
            and int(item.quantity or 0) > 0
            and str(item.template.key) in template_key_set
        )
    )
    stocked_keys = {str(item.template.key) for item in loaded_items}
    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    for item_key in template_keys:
        if item_key == GRAIN_ITEM_KEY or item_key in stocked_keys:
            continue
        template = templates_by_key[item_key]
        spec = InventoryAcquisitionActionSpec(
            item_template_id=int(template.id),
            item_key=str(template.key),
            daily_caps=inventory_daily_cap_limits(
                template,
                config=inventory_cap_config,
            ),
        )
        effect_priority = _INVENTORY_EFFECT_PRIORITY.get(
            str(template.effect_type),
            1,
        )
        utility_score = float(effect_priority)
        intent = project_maintenance_action_intent(
            spec=spec,
            source_prestige_band=prestige_band,
            target_prestige_band=prestige_band,
            strength_before=strength_before,
            strength_after=strength_before,
            utility_score=utility_score,
        )
        candidates.append(intent)
        specs[intent.business_key] = spec
    return tuple(candidates), specs


__all__ = [
    "MaintenanceCandidateError",
    "build_equipment_equip_candidates",
    "build_inventory_acquisition_candidates",
    "build_skill_learning_candidates",
]
