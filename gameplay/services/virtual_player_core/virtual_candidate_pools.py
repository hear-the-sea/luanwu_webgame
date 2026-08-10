"""Database-facing adapters for the pure virtual asset projections."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import copy
from types import SimpleNamespace
from typing import Any, cast

from core.exceptions import GuestNotRequirementError
from gameplay.models import ItemTemplate, Manor
from gameplay.services.recruitment.recruitment import TroopRecruitmentQuote
from gameplay.services.recruitment.templates import get_troop_template
from guests.models import GearItem, GearTemplate, Guest, GuestSkill, GuestStatus, Skill
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity
from guests.services.skills import MAX_GUEST_SKILL_SLOTS, assert_guest_meets_skill_requirements

from .bootstrap_assets import RARITY_RANK
from .inventory_budget import inventory_daily_cap_limits
from .maintenance_action_specs import (
    EquipmentEquipActionSpec,
    InventoryAcquisitionActionSpec,
    MaintenanceActionSpec,
    SkillLearningActionSpec,
    project_maintenance_action_intent,
)
from .projection import (
    DevelopmentIntent,
    StrengthSummary,
    calculate_guest_arena_power,
    project_troop_recruitment_development_intent,
)
from .skill_policy import is_virtual_player_skill_allowed
from .strategy import BotDevelopmentPlan
from .virtual_assets import (
    VIRTUAL_RARITY_RANK,
    VirtualAssetCandidate,
    draw_inventory_batch,
    equipment_candidate_weight,
    resolve_skill_book_definition,
    skill_candidate_weight,
)

_VIRTUAL_INVENTORY_RARITIES = frozenset(VIRTUAL_RARITY_RANK)


class VirtualCandidatePoolError(ValueError):
    pass


def _virtual_troop_cost(
    troop: Mapping[str, Any],
    *,
    tier: int,
    config: Mapping[str, Any],
) -> tuple[int, int]:
    """Return the configured-or-safe-default silver/grain projection cost."""

    raw_costs = config.get("virtual_troop_costs") or {}
    if not isinstance(raw_costs, Mapping):
        raw_costs = {}
    base_silver = max(1, int(raw_costs.get("silver_base", 100) or 100))
    silver_step = max(0, int(raw_costs.get("silver_per_tier", 75) or 75))
    base_grain = max(1, int(raw_costs.get("grain_base", 50) or 50))
    grain_step = max(0, int(raw_costs.get("grain_per_tier", 25) or 25))
    attack = max(0, int(troop.get("base_attack", 0) or 0))
    defense = max(0, int(troop.get("base_defense", 0) or 0))
    hp = max(0, int(troop.get("base_hp", 0) or 0))
    return (
        base_silver + tier * silver_step + attack * 5 + defense * 3,
        base_grain + tier * grain_step + hp // 4,
    )


def build_virtual_troop_candidates(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    troop_classes: Mapping[str, Any],
    technology_levels: Mapping[str, int],
    archetype: str,
    config: Mapping[str, Any],
) -> tuple[tuple[DevelopmentIntent, ...], dict[str, TroopRecruitmentQuote]]:
    """Build direct PlayerTroop projections without formal recruit prerequisites.

    The selected tier still follows the Manor's completed troop technology.
    This keeps the virtual path independent from equipment/retainer stock while
    preserving the meaningful quality ordering of the real troop catalog.
    """

    if not isinstance(manor, Manor) or not manor.pk:
        raise VirtualCandidatePoolError("virtual troop candidates require a persisted Manor")
    candidates: list[DevelopmentIntent] = []
    quotes: dict[str, TroopRecruitmentQuote] = {}
    normalized_archetype = str(archetype).strip()
    for troop_class, target_weight in development_plan.troop_mix:
        class_info = troop_classes.get(str(troop_class))
        if not isinstance(class_info, Mapping):
            continue
        troop_keys = tuple(str(key).strip() for key in (class_info.get("troops") or ()) if str(key).strip())
        if not troop_keys:
            continue
        eligible: list[tuple[int, str, dict[str, Any], int]] = []
        for tier, troop_key in enumerate(troop_keys):
            troop = get_troop_template(troop_key)
            if not isinstance(troop, Mapping):
                continue
            recruit = troop.get("recruit") or {}
            required_tech_key = str(recruit.get("tech_key") or "").strip() or None
            required_level = max(0, int(recruit.get("tech_level") or 0))
            actual_level = max(0, int(technology_levels.get(required_tech_key, 0))) if required_tech_key else 0
            if required_tech_key and actual_level < required_level:
                continue
            eligible.append((tier, troop_key, dict(troop), actual_level))
        if not eligible:
            continue
        selected = eligible[0] if normalized_archetype == "abandoned" else eligible[-1]
        tier, troop_key, troop, actual_tech_level = selected
        silver_cost, grain_cost = _virtual_troop_cost(troop, tier=tier, config=config)
        current_total = max(0, int(strength_before.components.get("troop_total", 0)))
        components_after = dict(strength_before.components)
        components_after["troop_total"] = current_total + 1
        spent_before = max(0, int(manor.prestige_silver_spent or 0))
        spending_prestige_before = spent_before // 1_000
        pvp_prestige = max(0, int(manor.prestige or 0) - spending_prestige_before)
        components_after["prestige"] = pvp_prestige + (spent_before + silver_cost) // 1_000
        strength_after = StrengthSummary(
            composite=float(components_after["arena_lineup_power"] + 2 * components_after["troop_total"]),
            components=components_after,
        )
        intent = project_troop_recruitment_development_intent(
            troop_key=troop_key,
            quantity=1,
            prestige_band=prestige_band,
            strength_before=strength_before,
            utility_score=max(0.001, float(target_weight) * (1 + tier)),
        )
        # The shared troop projection owns the canonical business key and
        # component validation. Replace only its projected post-state with the
        # same state that the direct virtual write will commit.
        intent = DevelopmentIntent(
            business_key=intent.business_key,
            action_kind=intent.action_kind,
            source_prestige_band=intent.source_prestige_band,
            target_prestige_band=intent.target_prestige_band,
            strength_before=intent.strength_before,
            strength_after=strength_after,
            utility_score=intent.utility_score,
            constraint_violations=intent.constraint_violations,
        )
        quote = TroopRecruitmentQuote(
            manor_id=int(manor.id),
            troop_key=troop_key,
            troop_name=str(troop.get("name") or troop_key),
            quantity=1,
            equipment_costs=(),
            equipment_stock=(),
            retainer_cost=0,
            retainer_count=max(0, int(manor.retainer_count or 0)),
            tech_key=(str((troop.get("recruit") or {}).get("tech_key") or "").strip() or None),
            tech_level_required=max(0, int((troop.get("recruit") or {}).get("tech_level") or 0)),
            tech_level=actual_tech_level,
            max_quantity=1,
            training_ground_level=0,
            ancestral_hall_level=0,
            base_duration=0,
            actual_duration=0,
            source="virtual",
            virtual_silver_cost=silver_cost,
            virtual_grain_cost=grain_cost,
        )
        candidates.append(intent)
        quotes[intent.business_key] = quote
    return tuple(candidates), quotes


def _guest_power(
    guest: Guest,
    *,
    force: int | None = None,
    intellect: int | None = None,
    defense: int | None = None,
    agility: int | None = None,
) -> int:
    return calculate_guest_arena_power(
        force=int(guest.force if force is None else force),
        intellect=int(guest.intellect if intellect is None else intellect),
        defense=int(guest.defense_stat if defense is None else defense),
        agility=int(guest.agility if agility is None else agility),
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def _strength_after_lineup(strength_before: StrengthSummary, delta: int) -> StrengthSummary:
    components = dict(strength_before.components)
    components["arena_lineup_power"] = max(0, int(components["arena_lineup_power"]) + int(delta))
    return StrengthSummary(
        composite=float(components["arena_lineup_power"] + 2 * components["troop_total"]),
        components=components,
    )


def build_virtual_skill_learning_candidates(
    *,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    guests: tuple[Guest, ...],
    skill_books: tuple[ItemTemplate, ...],
    skills: tuple[Skill, ...],
    guest_skills: tuple[GuestSkill, ...] = (),
) -> tuple[tuple[DevelopmentIntent, ...], dict[str, MaintenanceActionSpec]]:
    """Build virtual skill actions only from player-facing skill-book definitions."""

    skills_by_key = {str(skill.key): skill for skill in skills}
    books_by_skill: dict[str, ItemTemplate] = {}
    for template in sorted(skill_books, key=lambda item: (str(item.key), int(item.id))):
        try:
            skill = resolve_skill_book_definition(template, skills_by_key)
        except Exception as exc:
            raise VirtualCandidatePoolError(str(exc)) from exc
        skill_key = str(getattr(skill, "key", "")).strip()
        if not skill_key:
            raise VirtualCandidatePoolError("virtual skill book resolved to a skill without a key")
        books_by_skill.setdefault(skill_key, template)

    learned_by_guest: dict[int, set[int]] = defaultdict(set)
    for guest_skill in guest_skills:
        learned_by_guest[int(guest_skill.guest_id)].add(int(guest_skill.skill_id))
    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    preferred_kinds = set(development_plan.preferred_skill_kinds)
    preferred_roles = set(development_plan.preferred_guest_archetypes)
    candidate_skill_keys = set(books_by_skill)
    for guest in sorted(guests, key=lambda item: int(item.id)):
        if guest.status != GuestStatus.IDLE or guest.training_complete_at is not None:
            continue
        learned = learned_by_guest[int(guest.id)]
        if len(learned) >= MAX_GUEST_SKILL_SLOTS:
            continue
        for skill_key in sorted(candidate_skill_keys):
            if not is_virtual_player_skill_allowed(skill_key):
                continue
            skill = skills_by_key.get(skill_key)
            if skill is None:
                continue
            book_template = books_by_skill.get(skill_key)
            if book_template is None:
                continue
            if int(skill.id) in learned:
                continue
            try:
                assert_guest_meets_skill_requirements(guest, skill)
            except GuestNotRequirementError:
                continue
            spec = SkillLearningActionSpec(
                guest_id=int(guest.id),
                inventory_item_id=0,
                item_template_id=int(book_template.id),
                item_key=str(book_template.key),
                item_quantity_before=0,
                skill_id=int(skill.id),
                skill_key=skill_key,
                source="virtual",
            )
            intent = project_maintenance_action_intent(
                spec=spec,
                source_prestige_band=prestige_band,
                target_prestige_band=prestige_band,
                strength_before=strength_before,
                strength_after=strength_before,
                utility_score=skill_candidate_weight(
                    skill=skill,
                    guest=guest,
                    current_skill_count=len(learned),
                    preferred_kind=str(skill.kind) in preferred_kinds,
                    preferred_role=str(guest.template.archetype) in preferred_roles,
                ),
            )
            candidates.append(intent)
            specs[intent.business_key] = spec
    return tuple(candidates), specs


def _project_virtual_gear_guest(
    guest: Guest,
    template: GearTemplate,
    equipped_items: tuple[GearItem, ...],
) -> Guest:
    """Project the same replacement and set-bonus semantics as real equip."""

    guest_items = tuple(item for item in equipped_items if int(item.guest_id or 0) == int(guest.id))
    slot = str(template.slot)
    same_slot = tuple(item for item in guest_items if str(item.template.slot) == slot)
    replacing = slot_capacity(slot) == 1 and bool(same_slot)
    projected_items = tuple(item for item in guest_items if not (replacing and str(item.template.slot) == slot))

    projected_guest = copy(guest)
    updates: set[str] = set()
    for item in same_slot if replacing else ():
        apply_template_stats_to_guest(projected_guest, item.template, -1, updates)
    apply_template_stats_to_guest(projected_guest, template, +1, updates)

    # The persisted guest already contains the old set bonus.  Passing the
    # projected gear list lets the shared helper remove that old bonus and add
    # the new one without touching the database during planning.
    apply_set_bonuses(
        projected_guest,
        gear_items=(*projected_items, cast(GearItem, SimpleNamespace(template=template))),
        persist=False,
    )
    return projected_guest


def _gear_rarity_cap_for_stage(
    *,
    growth_stage: int,
    stage_caps: Mapping[int | str, str] | None,
) -> int:
    """Resolve the configured gear color ceiling without using item price."""

    if isinstance(growth_stage, bool) or not isinstance(growth_stage, int) or growth_stage < 0:
        raise VirtualCandidatePoolError("growth_stage must be a non-negative integer")
    configured = stage_caps or {
        1: "green",
        7: "blue",
        11: "purple",
        16: "orange",
    }
    if not isinstance(configured, Mapping):
        raise VirtualCandidatePoolError("gear rarity stage caps must be a mapping")
    cap = RARITY_RANK["green"]
    for raw_stage, raw_rarity in configured.items():
        try:
            threshold = int(raw_stage)
        except (TypeError, ValueError):
            continue
        rarity = str(raw_rarity).strip().lower()
        if threshold <= growth_stage and rarity in RARITY_RANK:
            cap = max(cap, RARITY_RANK[rarity])
    return cap


def build_virtual_equipment_candidates(
    *,
    manor_id: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    guests: tuple[Guest, ...],
    gear_templates: tuple[GearTemplate, ...],
    equipped_items: tuple[GearItem, ...] = (),
    growth_stage: int,
    rarity_stage_caps: Mapping[int | str, str] | None = None,
) -> tuple[tuple[DevelopmentIntent, ...], dict[str, MaintenanceActionSpec]]:
    """Build role/slot/power weighted equipment actions from a virtual pool."""

    del manor_id
    rarity_cap = _gear_rarity_cap_for_stage(
        growth_stage=growth_stage,
        stage_caps=rarity_stage_caps,
    )
    equipped_by_guest: dict[int, list[GearItem]] = defaultdict(list)
    for gear in equipped_items:
        if gear.guest_id is not None:
            equipped_by_guest[int(gear.guest_id)].append(gear)
    preferred_roles = set(development_plan.preferred_guest_archetypes)
    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    for guest in sorted(guests, key=lambda item: int(item.id)):
        if guest.status != GuestStatus.IDLE or guest.training_complete_at is not None:
            continue
        before_power = _guest_power(guest)
        for template in sorted(gear_templates, key=lambda item: (str(item.slot), str(item.key), int(item.id))):
            if RARITY_RANK.get(str(template.rarity).strip().lower(), len(RARITY_RANK)) > rarity_cap:
                continue
            slot = str(template.slot)
            same_slot = [item for item in equipped_by_guest[int(guest.id)] if str(item.template.slot) == slot]
            if any(str(item.template.name) == str(template.name) for item in same_slot):
                continue
            if slot_capacity(slot) > 1 and len(same_slot) >= slot_capacity(slot):
                continue
            projected_guest = _project_virtual_gear_guest(guest, template, equipped_items)
            after_power = calculate_guest_arena_power(
                force=int(projected_guest.force),
                intellect=int(projected_guest.intellect),
                defense=int(projected_guest.defense_stat),
                agility=int(projected_guest.agility),
                hp_bonus=int(projected_guest.hp_bonus),
                archetype=str(projected_guest.template.archetype),
                base_hp=int(projected_guest.template.base_hp),
            )
            power_gain = max(0, after_power - before_power)
            if power_gain <= 0:
                continue
            spec = EquipmentEquipActionSpec(
                guest_id=int(guest.id),
                inventory_item_id=0,
                item_template_id=int(template.id),
                item_key=str(template.key),
                item_quantity_before=0,
                slot=slot,
                source="virtual",
            )
            intent = project_maintenance_action_intent(
                spec=spec,
                source_prestige_band=prestige_band,
                target_prestige_band=prestige_band,
                strength_before=strength_before,
                strength_after=_strength_after_lineup(strength_before, power_gain),
                utility_score=equipment_candidate_weight(
                    template=template,
                    guest=guest,
                    growth_stage=growth_stage,
                    preferred_role=str(guest.template.archetype) in preferred_roles,
                ),
            )
            candidates.append(intent)
            specs[intent.business_key] = spec
    return tuple(candidates), specs


def build_virtual_inventory_batch_candidate(
    *,
    manor_id: int,
    prestige_band: str,
    growth_stage: int,
    archetype: str,
    strength_before: StrengthSummary,
    inventory_templates: tuple[ItemTemplate, ...],
    config: Mapping[str, Any],
    seed: int,
    used_keys: set[str] | None = None,
    rare_count_today: int = 0,
) -> tuple[DevelopmentIntent | None, InventoryAcquisitionActionSpec | None]:
    """Return one inventory-slot candidate containing a deterministic batch."""

    del manor_id
    projection = config.get("projection") or {}
    stage_caps = projection.get("inventory_max_rarity_by_stage") or projection.get("gear_max_rarity_by_stage") or {}
    effect_weights = (
        (projection.get("inventory_effect_type_weights") or {}).get(str(archetype), {})
        if isinstance(projection, Mapping)
        else {}
    )
    candidates = tuple(
        VirtualAssetCandidate(
            key=str(template.key),
            kind=str(template.effect_type),
            weight=(
                max(
                    0.05,
                    (
                        float(effect_weights.get(str(template.effect_type), 1.0))
                        if isinstance(effect_weights, Mapping)
                        else 1.0
                    ),
                )
            ),
            rarity=str(template.rarity or "gray").lower(),
            template_id=int(template.id),
        )
        for template in inventory_templates
        if bool(template.tradeable) and str(template.rarity or "gray").strip().lower() in _VIRTUAL_INVENTORY_RARITIES
    )
    batch = draw_inventory_batch(
        candidates,
        archetype=archetype,
        prestige_band=prestige_band,
        growth_stage=growth_stage,
        seed=seed,
        used_keys=used_keys,
        rare_count_today=rare_count_today,
        rare_daily_cap=int(projection.get("rare_item_daily_global_cap", 20) or 20),
        stage_caps=stage_caps,
        color_weights_by_band=projection.get("inventory_color_weights_by_prestige_band") or {},
        rare_colors=tuple(
            str(color).strip().lower()
            for color in (projection.get("inventory_rare_color_set") or ("red", "purple", "orange"))
            if str(color).strip()
        ),
    )
    if batch.is_no_action:
        return None, None
    templates_by_key = {str(template.key): template for template in inventory_templates}
    batch_items = tuple(
        (
            int(templates_by_key[candidate.key].id),
            candidate.key,
            inventory_daily_cap_limits(
                templates_by_key[candidate.key],
                config=dict(config),
            ),
            1,
        )
        for candidate in batch.candidates
    )
    first_template = templates_by_key[batch.candidates[0].key]
    spec = InventoryAcquisitionActionSpec(
        item_template_id=int(first_template.id),
        item_key=str(first_template.key),
        daily_caps=batch_items[0][2],
        batch_id=f"virtual-inventory-{int(seed)}",
        batch_items=batch_items,
        batch_draws=tuple(
            (
                ordinal,
                candidate.rarity,
                candidate.key,
                float(candidate.weight),
            )
            for ordinal, candidate in enumerate(batch.candidates, start=1)
        ),
        source="virtual",
    )
    intent = project_maintenance_action_intent(
        spec=spec,
        source_prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        strength_before=strength_before,
        strength_after=strength_before,
        utility_score=float(batch.applied_quantity),
    )
    return intent, spec


__all__ = [
    "VirtualCandidatePoolError",
    "build_virtual_equipment_candidates",
    "build_virtual_inventory_batch_candidate",
    "build_virtual_skill_learning_candidates",
    "build_virtual_troop_candidates",
]
