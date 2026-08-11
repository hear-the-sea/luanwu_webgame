from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.constants import BuildingKeys
from gameplay.models import (
    BotExternalStrengthReconciliation,
    BotMaintenanceAttempt,
    BotMaintenanceCompletionEvent,
    BotMaintenanceCycle,
    BotMaintenanceExecution,
    BotProfile,
    BotRuntimeRoutingState,
    BotSafetyMetricEvent,
    Building,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
    ResourceEvent,
    ResourceType,
    TroopRecruitment,
)
from gameplay.services.recruitment.recruitment import get_troop_template
from gameplay.services.virtual_player_core import bootstrap, maintenance, maintenance_completion
from gameplay.services.virtual_player_core.archetype_pacing import resolve_archetype_pacing
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    ArenaGrowthObjective,
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    EquipmentEquipActionSpec,
    GuestRecruitmentActionSpec,
    InventoryAcquisitionActionSpec,
    TechnologyUpgradeActionSpec,
)
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.projection import (
    ReferenceCandidate,
    ReferenceSelection,
    ReferenceSource,
    SampleTier,
    StrengthSummary,
)
from gameplay.services.virtual_player_core.reference_snapshots import (
    ReferenceSnapshotError,
    load_manor_strength_summary,
)
from gameplay.services.virtual_player_core.safety_metrics import MAINTENANCE_ATTEMPT_METRIC, record_safety_heartbeat
from gameplay.services.virtual_player_core.safety_preflight import SafetyWritePreflightResult
from gameplay.services.virtual_player_core.safety_provider import HARD_VIOLATION_METRIC_NAME, SafetyProviderError
from gameplay.services.virtual_player_core.virtual_candidate_pools import build_virtual_skill_learning_candidates
from gameplay.services.virtual_player_core.virtual_candidate_pools import (
    build_virtual_troop_candidates as build_virtual_troop_candidates_real,
)
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestSkill,
    GuestStatus,
    GuestTemplate,
    SalaryPayment,
    Skill,
    TrainingLog,
)

FIXED_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


def _set_active_routing() -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
        policy_rollout_target_version=2,
        policy_rollout_enabled=False,
        policy_rollout_percent=0,
        revision=7,
    )


def _create_v2_profile(*, seed: int) -> BotProfile:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "newbie",
        BotProfile.Archetype.BALANCED,
        seed,
        FIXED_NOW,
    )
    with transaction.atomic():
        permit = bootstrap._issue_v2_bootstrap_population_permit(
            region=plan.region,
            prestige_band=plan.prestige_band,
        )
        profile = bootstrap.create_virtual_player_v2(
            plan=plan,
            population_permit=permit,
            now=FIXED_NOW,
        )
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=FIXED_NOW,
        last_strength_increase_at=FIXED_NOW - timedelta(days=1),
        strength_budget_entries=[],
        maintenance_sequence=0,
    )
    Manor.objects.filter(pk=profile.manor_id).update(
        silver=100_000,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
        resource_updated_at=FIXED_NOW,
    )
    profile.refresh_from_db()
    profile.manor.refresh_from_db()
    return profile


@pytest.fixture
def active_v2_profile(released_v2_policy, game_data) -> BotProfile:
    _set_active_routing()
    record_safety_heartbeat("safety_monitor", now=FIXED_NOW)
    return _create_v2_profile(seed=995_001)


@pytest.fixture
def permissive_reference(monkeypatch):
    cap = StrengthSummary(
        composite=1_000_000_000,
        components={
            "arena_lineup_power": 1_000_000_000,
            "core_building_level": 1_000_000_000,
            "guest_count": 1_000_000_000,
            "max_guest_level": 1_000_000_000,
            "prestige": 1_000_000_000,
            "troop_total": 1_000_000_000,
        },
    )

    def select_reference(*, prestige_band, **_kwargs):
        anchor = ReferenceCandidate(
            business_key=f"test-reference:{prestige_band}",
            prestige_band=prestige_band,
            strength=cap,
            features={
                "core_building_level": 100,
                "guest_count": 100,
                "max_guest_level": 100,
            },
        )
        selection = ReferenceSelection(
            prestige_band=prestige_band,
            tier=SampleTier.SPARSE,
            source=ReferenceSource.LOCAL,
            local_sample_count=1,
            anchor=anchor,
            cap=cap,
            nearest_candidate_keys=(anchor.business_key,),
        )
        return 2, selection, "f" * 64

    monkeypatch.setattr(maintenance, "growth_control_reference_selection", select_reference)
    monkeypatch.setattr(maintenance, "build_virtual_troop_candidates", lambda **_kwargs: ((), {}))
    return cap


def _scheduled_plan(profile: BotProfile):
    return maintenance.build_virtual_player_v2_maintenance_plan(
        profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )


@pytest.mark.django_db
def test_arena_candidate_assessment_keeps_disallowed_actions_out_of_selection(
    active_v2_profile,
    permissive_reference,
) -> None:
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        minimum_guest_count=1,
        minimum_guest_level=2,
        max_guest_level_step=6,
    )

    assert plan.intent is None
    rejected_by_kind = {
        assessment.intent.action_kind: assessment.rejection_reasons
        for assessment in plan.candidate_assessments
        if not assessment.allowed
    }
    assert rejected_by_kind[BuildingUpgradeActionSpec.action_kind] == ("trigger_action_disallowed",)
    assert rejected_by_kind[TechnologyUpgradeActionSpec.action_kind] == ("trigger_action_disallowed",)


@pytest.mark.django_db
def test_arena_candidate_assessment_selects_allowed_training_before_disallowed_upgrades(
    active_v2_profile,
    permissive_reference,
) -> None:
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        minimum_guest_count=1,
        minimum_guest_level=20,
        max_guest_level_step=6,
    )

    assert plan.action_kind == "training"
    assert plan.selected_candidate_assessment is not None
    assert plan.selected_candidate_assessment.allowed is True
    assert all(
        assessment.intent.action_kind
        in {
            "guest_healing",
            GuestRecruitmentActionSpec.action_kind,
            "training",
            EquipmentEquipActionSpec.action_kind,
        }
        for assessment in plan.candidate_assessments
        if assessment.allowed
    )


@pytest.mark.django_db
def test_arena_unpaid_salary_cannot_select_non_arena_fallback(
    active_v2_profile,
    permissive_reference,
) -> None:
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=0,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        minimum_guest_count=1,
        minimum_guest_level=20,
        max_guest_level_step=6,
    )

    assert plan.resource_planning_snapshot.salary_shortfall is True
    non_arena_assessments = tuple(
        assessment
        for assessment in plan.candidate_assessments
        if assessment.intent.action_kind
        not in {
            "guest_healing",
            GuestRecruitmentActionSpec.action_kind,
            "training",
            EquipmentEquipActionSpec.action_kind,
        }
    )
    assert non_arena_assessments
    assert all("trigger_action_disallowed" in assessment.rejection_reasons for assessment in non_arena_assessments)
    assert plan.intent is None or plan.action_kind in {
        "guest_healing",
        GuestRecruitmentActionSpec.action_kind,
        "training",
        EquipmentEquipActionSpec.action_kind,
    }


@pytest.mark.django_db
def test_arena_training_projection_applies_immediate_level_for_event_cap(
    active_v2_profile,
    permissive_reference,
) -> None:
    guests = tuple(
        active_v2_profile.manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("id")
    )
    selected_power_before = sum(
        maintenance._guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
        )
        for guest in guests
    )
    target_team_power = (selected_power_before * 100 + 119) // 120
    selected_power_upper_bound = target_team_power * 120 // 100
    objective = ArenaGrowthObjective(
        critical_guest_count=len(guests),
        preferred_guest_count=len(guests),
        selected_power_lower_bound=(target_team_power * 80 + 99) // 100,
        selected_power_upper_bound=selected_power_upper_bound,
        selected_power_before=selected_power_before,
        target_team_power=target_team_power,
        lineup_mode="tournament",
        lineup_event_id=77,
        lineup_max_size=len(guests),
        minimum_guest_level=20,
        recruitment_rarity_cap="blue",
        max_guest_level_step=10,
    )

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        arena_growth_objective=objective,
    )

    training_assessments = tuple(
        assessment for assessment in plan.candidate_assessments if assessment.intent.action_kind == "training"
    )
    assert training_assessments
    assert all(
        assessment.projected_selected_power is not None and assessment.event_power_cap == selected_power_upper_bound
        for assessment in training_assessments
    )
    assert all(
        int(assessment.projected_selected_power or 0) <= selected_power_upper_bound
        or "event_power_cap" in assessment.rejection_reasons
        for assessment in training_assessments
    )
    assert any(
        int(assessment.projected_selected_power or 0) > selected_power_before for assessment in training_assessments
    )
    assert any("event_power_cap" in assessment.rejection_reasons for assessment in training_assessments)


@pytest.mark.django_db
def test_skill_candidates_use_the_frozen_catalog_without_orm(
    active_v2_profile,
    django_assert_num_queries,
) -> None:
    skill = Skill.objects.create(
        key=f"v2_skill_snapshot_{active_v2_profile.id}",
        name="V2 snapshot skill",
        rarity="green",
    )
    template = ItemTemplate.objects.create(
        key=f"v2_skill_book_snapshot_{active_v2_profile.id}",
        name="V2 snapshot skill book",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key},
    )
    item = InventoryItem.objects.create(
        manor_id=active_v2_profile.manor_id,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    guests = tuple(Guest.objects.filter(manor_id=active_v2_profile.manor_id).select_related("template").order_by("id"))
    strength = load_manor_strength_summary(
        manor_id=active_v2_profile.manor_id,
        guests=guests,
    )
    development_plan = maintenance.parse_development_plan(
        active_v2_profile.development_profile,
        catalog=maintenance.development_plan_catalog_v1(),
    )

    with django_assert_num_queries(0):
        candidates, specs = maintenance.build_skill_learning_candidates(
            manor_id=int(active_v2_profile.manor_id),
            prestige_band=str(active_v2_profile.current_prestige_band),
            strength_before=strength,
            development_plan=development_plan,
            guests=guests,
            guest_skills=(),
            warehouse_items=(item,),
            skills=(skill,),
        )

    assert candidates
    assert set(specs) == {candidate.business_key for candidate in candidates}


@pytest.mark.django_db
def test_arena_virtual_skill_candidates_exclude_bookless_boss_skills(active_v2_profile) -> None:
    skill = Skill.objects.create(
        key="gl_top_qiankun_holy_flame",
        name="乾坤圣火印",
        rarity="purple",
    )
    guest = Guest.objects.filter(manor_id=active_v2_profile.manor_id).order_by("id").first()
    assert guest is not None
    GuestSkill.objects.filter(guest_id=guest.id).delete()
    guest.refresh_from_db()
    guests = (guest,)
    strength = load_manor_strength_summary(
        manor_id=int(active_v2_profile.manor_id),
        guests=guests,
    )
    development_plan = maintenance.parse_development_plan(
        active_v2_profile.development_profile,
        catalog=maintenance.development_plan_catalog_v1(),
    )

    candidates, specs = build_virtual_skill_learning_candidates(
        prestige_band=str(active_v2_profile.current_prestige_band),
        strength_before=strength,
        development_plan=development_plan,
        guests=guests,
        skill_books=(),
        skills=(skill,),
        guest_skills=(),
    )

    assert candidates == ()
    assert specs == {}

    daily_candidates, daily_specs = build_virtual_skill_learning_candidates(
        prestige_band=str(active_v2_profile.current_prestige_band),
        strength_before=strength,
        development_plan=development_plan,
        guests=guests,
        skill_books=(),
        skills=(skill,),
        guest_skills=(),
    )
    assert daily_candidates == ()
    assert daily_specs == {}


@pytest.mark.django_db
def test_inventory_batch_materializes_once_and_replay_does_not_duplicate_rows(active_v2_profile) -> None:
    manor = Manor.objects.get(pk=active_v2_profile.manor_id)
    templates = tuple(
        ItemTemplate.objects.create(
            key=f"v2_inventory_batch_{active_v2_profile.id}_{index}",
            name=f"V2 batch item {index}",
            effect_type=ItemTemplate.EffectType.TOOL,
            tradeable=True,
        )
        for index in range(5)
    )
    batch_items = tuple((int(template.id), str(template.key), (), 1) for template in templates)
    spec = InventoryAcquisitionActionSpec(
        item_template_id=int(templates[0].id),
        item_key=str(templates[0].key),
        daily_caps=(),
        batch_id="v2-inventory-batch-test",
        batch_items=batch_items,
    )

    with transaction.atomic():
        first = maintenance._apply_inventory_acquisition_locked(manor, spec, now=FIXED_NOW)

    assert first.template_id == templates[0].id
    assert (
        InventoryItem.objects.filter(
            manor_id=manor.id,
            template_id__in=[template.id for template in templates],
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).count()
        == 5
    )

    before = tuple(
        InventoryItem.objects.filter(manor_id=manor.id, template_id__in=[template.id for template in templates])
        .order_by("template_id")
        .values_list("template_id", "quantity")
    )
    with pytest.raises(maintenance.InventoryAcquisitionUnavailable, match="no eligible candidate"):
        with transaction.atomic():
            maintenance._apply_inventory_acquisition_locked(manor, spec, now=FIXED_NOW)
    assert (
        tuple(
            InventoryItem.objects.filter(manor_id=manor.id, template_id__in=[template.id for template in templates])
            .order_by("template_id")
            .values_list("template_id", "quantity")
        )
        == before
    )


def _prepare_guest_healing_plan(
    profile: BotProfile,
    *,
    quantity: int = 2,
    arena_growth_objective: ArenaGrowthObjective | None = None,
):
    guests = tuple(Guest.objects.filter(manor_id=profile.manor_id).select_related("template").order_by("id"))
    target = max(
        guests,
        key=lambda guest: (
            maintenance._guest_arena_power(
                guest,
                force=int(guest.force),
                intellect=int(guest.intellect),
                defense=int(guest.defense_stat),
                agility=int(guest.agility),
            ),
            -int(guest.id),
        ),
    )
    target.current_hp = max(1, int(target.max_hp * 0.1))
    target.status = GuestStatus.INJURED
    target.save(update_fields=["current_hp", "status"])
    template = ItemTemplate.objects.create(
        key=f"v2_healing_{profile.id}",
        name="V2 疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": max(1, int(target.max_hp * 0.5))},
        is_usable=True,
    )
    item = InventoryItem.objects.create(
        manor_id=profile.manor_id,
        template=template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        profile.id,
        trigger=(
            MaintenanceTrigger.SCHEDULED if arena_growth_objective is None else MaintenanceTrigger.ARENA_ACCELERATION
        ),
        now=FIXED_NOW,
        arena_growth_objective=arena_growth_objective,
    )
    assert plan.action_kind == "guest_healing"
    assert plan.target_id == target.id
    assert plan.medicine_quote is not None
    return plan, target, item


def _prepare_troop_recruitment_plan(profile: BotProfile, monkeypatch):
    # The shared permissive-reference fixture suppresses troop projections so
    # unrelated action tests stay deterministic.  This helper explicitly opts
    # back into the policy-2 virtual troop pool and disables the other action
    # families so the assertions exercise the current direct PlayerTroop path.
    monkeypatch.setattr(maintenance, "build_virtual_troop_candidates", build_virtual_troop_candidates_real)
    monkeypatch.setattr(maintenance, "_training_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_equipment_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_skill_learning_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_inventory_batch_candidate", lambda **_kwargs: (None, None))
    monkeypatch.setattr(maintenance, "_building_upgrade_quotes", lambda **_kwargs: ())
    monkeypatch.setattr(maintenance, "_technology_upgrade_quotes", lambda **_kwargs: ())
    Guest.objects.filter(manor_id=profile.manor_id).update(status=GuestStatus.WORKING)
    Manor.objects.filter(pk=profile.manor_id).update(retainer_count=100)
    manor = Manor.objects.get(pk=profile.manor_id)
    troop_classes = maintenance.get_troop_classes()
    equipment_keys: set[str] = set()
    for troop_class, _weight in profile.development_profile["troop_mix"]:
        for troop_key in troop_classes[troop_class]["troops"]:
            troop = get_troop_template(troop_key)
            recruit_config = troop.get("recruit") if troop else None
            if not recruit_config:
                continue
            equipment_keys.update(recruit_config.get("equipment") or ())
            tech_key = recruit_config.get("tech_key")
            if tech_key:
                PlayerTechnology.objects.update_or_create(
                    manor=manor,
                    tech_key=tech_key,
                    defaults={"level": max(1, int(recruit_config.get("tech_level") or 0))},
                )

    templates = {}
    for item_key in sorted(equipment_keys):
        template, _created = ItemTemplate.objects.get_or_create(
            key=item_key,
            defaults={
                "name": item_key,
                "effect_type": ItemTemplate.EffectType.TOOL,
                "effect_payload": {},
                "is_usable": True,
            },
        )
        templates[item_key] = template
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=templates[item_key],
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 100},
        )

    profile.refresh_from_db()
    profile.manor.refresh_from_db()
    plan = _scheduled_plan(profile)
    assert plan.action_kind == "troop_recruitment"
    assert plan.troop_recruitment_quote is not None
    assert plan.intent is not None
    return plan


def _troop_domain_state(plan) -> dict[str, object]:
    return {
        "inventory": tuple(
            InventoryItem.objects.filter(manor_id=plan.manor_id)
            .order_by("template__key", "storage_location")
            .values_list("template__key", "storage_location", "quantity")
        ),
        "recruitments": tuple(
            TroopRecruitment.objects.filter(manor_id=plan.manor_id)
            .order_by("id")
            .values_list(
                "troop_key",
                "quantity",
                "status",
                "equipment_costs",
                "retainer_cost",
                "complete_at",
                "finished_at",
            )
        ),
        "retainer_count": Manor.objects.values_list("retainer_count", flat=True).get(pk=plan.manor_id),
        "troops": tuple(
            PlayerTroop.objects.filter(manor_id=plan.manor_id)
            .order_by("troop_template__key")
            .values_list("troop_template__key", "count")
        ),
    }


def _configure_due_resource_production(
    profile: BotProfile,
    monkeypatch,
) -> None:
    Manor.objects.filter(pk=profile.manor_id).update(
        silver=0,
        grain=0,
        silver_capacity=100_000,
        grain_capacity=100_000,
        resource_updated_at=FIXED_NOW - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {
            ResourceType.SILVER: 50_000,
            ResourceType.GRAIN: 50_000,
        },
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr(
        "gameplay.services.resources.scale_value",
        lambda value: value,
    )
    profile.manor.refresh_from_db()


def _isolate_training_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "build_equipment_equip_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "_building_upgrade_quotes",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        maintenance,
        "_technology_upgrade_quotes",
        lambda **_kwargs: (),
    )


def _isolate_upgrade_candidates(monkeypatch, *, keep: str) -> None:
    monkeypatch.setattr(
        maintenance,
        "_training_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "_troop_recruitment_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "build_skill_learning_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "build_inventory_acquisition_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "build_equipment_equip_candidates",
        lambda **_kwargs: ((), {}),
    )
    if keep != "building":
        monkeypatch.setattr(
            maintenance,
            "_building_upgrade_quotes",
            lambda **_kwargs: (),
        )
    if keep != "technology":
        monkeypatch.setattr(
            maintenance,
            "_technology_upgrade_quotes",
            lambda **_kwargs: (),
        )


def _isolate_equipment_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance,
        "_guest_healing_candidate",
        lambda **_kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        maintenance,
        "_training_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "_troop_recruitment_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "build_skill_learning_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "build_inventory_acquisition_candidates",
        lambda **_kwargs: ((), {}),
    )
    monkeypatch.setattr(
        maintenance,
        "_building_upgrade_quotes",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        maintenance,
        "_technology_upgrade_quotes",
        lambda **_kwargs: (),
    )


def _prepare_cross_band_building_plan(
    profile: BotProfile,
    monkeypatch,
):
    _isolate_upgrade_candidates(monkeypatch, keep="building")
    Manor.objects.filter(pk=profile.manor_id).update(
        prestige=499,
        prestige_silver_spent=999,
        silver=1_000_000,
        grain=1_000_000,
        silver_capacity=1_000_000,
        grain_capacity=1_000_000,
        resource_updated_at=FIXED_NOW,
    )
    monkeypatch.setattr(
        maintenance,
        "next_normal_strength_check_at",
        lambda *, prestige_band, now, **_kwargs: now + timedelta(hours={"newbie": 2, "junior": 4}[prestige_band]),
    )
    profile.manor.refresh_from_db()

    plan = _scheduled_plan(profile)

    assert plan.action_kind == BuildingUpgradeActionSpec.action_kind
    assert plan.intent is not None
    assert plan.intent.source_prestige_band == "newbie"
    assert plan.intent.target_prestige_band == "junior"
    assert plan.target_reference_selection is not None
    assert plan.target_reference_selection.prestige_band == "junior"
    assert plan.next_growth_at_after_no_action == FIXED_NOW + timedelta(hours=2)
    assert plan.next_growth_at_after == FIXED_NOW + timedelta(hours=4)
    return plan


def _prepare_equipment_plan(
    profile: BotProfile,
    monkeypatch,
    *,
    quantity: int = 2,
    arena_growth_objective: ArenaGrowthObjective | None = None,
):
    _isolate_equipment_candidates(monkeypatch)
    gear_template = GearTemplate.objects.create(
        key=f"v2_equipment_{profile.id}",
        name="V2 maintenance device",
        slot=GearSlot.MOUNT,
        rarity="green",
        extra_stats={"force": 10},
    )
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        profile.id,
        trigger=(
            MaintenanceTrigger.SCHEDULED if arena_growth_objective is None else MaintenanceTrigger.ARENA_ACCELERATION
        ),
        now=FIXED_NOW,
        arena_growth_objective=arena_growth_objective,
    )
    assert plan.action_kind == EquipmentEquipActionSpec.action_kind
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    assert plan.target_id == plan.action_spec.guest_id
    assert plan.action_spec.source == "virtual"
    assert plan.action_spec.item_template_id == gear_template.id
    assert plan.action_spec.inventory_item_id == 0
    assert plan.intent is not None
    return plan, gear_template


def _equipment_domain_state(plan) -> dict[str, object]:
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    return {
        "gear": tuple(
            GearItem.objects.filter(manor_id=plan.manor_id)
            .order_by("id")
            .values_list(
                "template__key",
                "guest_id",
                "inventory_backed",
            )
        ),
        "guest": Guest.objects.values(
            "attack_bonus",
            "defense_bonus",
            "force",
            "intellect",
            "defense_stat",
            "agility",
            "luck",
            "hp_bonus",
            "troop_capacity_bonus",
            "gear_set_bonus",
        ).get(pk=plan.target_id),
        "inventory": tuple(
            InventoryItem.objects.filter(manor_id=plan.manor_id)
            .order_by("template__key", "storage_location")
            .values_list(
                "template__key",
                "storage_location",
                "quantity",
            )
        ),
    }


@pytest.mark.django_db
def test_arena_equipment_candidate_enforces_event_selected_power_cap(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    guests = tuple(
        active_v2_profile.manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("id")
    )
    selected_power_before = sum(
        maintenance._guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
        )
        for guest in guests
    )
    target_team_power = (selected_power_before * 100 + 119) // 120
    objective = ArenaGrowthObjective(
        critical_guest_count=len(guests),
        preferred_guest_count=len(guests),
        selected_power_lower_bound=(target_team_power * 80 + 99) // 100,
        selected_power_upper_bound=target_team_power * 120 // 100,
        selected_power_before=selected_power_before,
        target_team_power=target_team_power,
        lineup_mode="tournament",
        lineup_event_id=80,
        lineup_max_size=len(guests),
        minimum_guest_level=1,
        recruitment_rarity_cap=GuestRarity.BLUE,
        max_guest_level_step=3,
    )

    plan, _item = _prepare_equipment_plan(
        active_v2_profile,
        monkeypatch,
        arena_growth_objective=objective,
    )

    assessment = plan.selected_candidate_assessment
    assert assessment is not None
    assert assessment.intent.action_kind == EquipmentEquipActionSpec.action_kind
    assert assessment.primary_rejection_reason == "event_power_cap"
    assert assessment.projected_selected_power is not None
    assert assessment.projected_selected_power > objective.selected_power_upper_bound


@pytest.mark.django_db
def test_v2_training_planner_is_read_only_and_deterministic(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_training_candidates(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        first = _scheduled_plan(active_v2_profile)
        second = _scheduled_plan(active_v2_profile)

    write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    writes = [
        query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(write_prefixes)
    ]
    assert writes == []
    assert first == second
    assert first.intent is not None
    assert first.action_kind == "training"
    assert first.target_id is not None
    assert first.rng_seed is not None
    assert len(first.precondition_digest) == 64


@pytest.mark.django_db
def test_v2_building_upgrade_commits_one_frozen_domain_action(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_upgrade_candidates(monkeypatch, keep="building")
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=1_000_000,
        grain=1_000_000,
        silver_capacity=1_000_000,
        grain_capacity=1_000_000,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()

    plan = _scheduled_plan(active_v2_profile)

    assert plan.action_kind == BuildingUpgradeActionSpec.action_kind
    assert isinstance(plan.action_spec, BuildingUpgradeActionSpec)
    building = Building.objects.get(pk=plan.action_spec.building_id)
    level_before = building.level

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    building.refresh_from_db()
    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == BuildingUpgradeActionSpec.action_kind
    assert building.level == level_before
    assert building.is_upgrading is True
    assert building.upgrade_complete_at is not None
    assert active_v2_profile.maintenance_sequence == 1
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


@pytest.mark.django_db
def test_v2_scheduled_cycle_preserves_timed_action_completion_source(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_upgrade_candidates(monkeypatch, keep="building")
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=1_000_000,
        grain=1_000_000,
        silver_capacity=1_000_000,
        grain_capacity=1_000_000,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()

    cycle = maintenance._open_policy2_scheduled_cycle(
        active_v2_profile.id,
        now=FIXED_NOW,
    )
    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    cycle.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == BuildingUpgradeActionSpec.action_kind
    assert cycle.current_action_state == BotMaintenanceCycle.ActionState.SUBMITTED
    assert cycle.last_action_completion_source == "building.upgrade_complete_at"
    assert cycle.next_slot_due_at is not None
    assert cycle.next_slot_due_at > FIXED_NOW


@pytest.mark.django_db
def test_v2_scheduled_cycle_retry_preserves_slot_and_records_reason_category(active_v2_profile):
    cycle = maintenance._open_policy2_scheduled_cycle(
        active_v2_profile.id,
        now=FIXED_NOW,
    )
    result = MaintenanceResult(
        outcome=MaintenanceOutcome.BUSY,
        trigger=MaintenanceTrigger.SCHEDULED,
        profile_id=active_v2_profile.id,
        sequence_before=0,
        sequence_after=0,
        schedule_disposition=MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE,
        next_growth_at_before=FIXED_NOW,
        next_growth_at_after=FIXED_NOW,
        reason="profile_busy",
    )

    maintenance._record_policy2_scheduled_cycle_retry(
        cycle.cycle_id,
        result,
        now=FIXED_NOW,
    )

    cycle.refresh_from_db()
    assert cycle.status == BotMaintenanceCycle.Status.OPEN
    assert cycle.action_ordinal == 0
    assert cycle.current_action_state == BotMaintenanceCycle.ActionState.READY
    assert cycle.payload["last_reason_category"] == "lock_conflict"
    assert cycle.payload["retry_history"][-1]["reason_category"] == "lock_conflict"


@pytest.mark.django_db
def test_domain_completion_reconcile_wakes_latest_cycle_and_is_idempotent(active_v2_profile):
    guest = active_v2_profile.manor.guests.order_by("id").first()
    assert guest is not None
    completion_at = FIXED_NOW + timedelta(hours=1)
    old_due_at = FIXED_NOW + timedelta(minutes=5)
    cycle = BotMaintenanceCycle.objects.create(
        cycle_id="vp-completion-reconcile-cycle",
        interval_seed="vp-completion-reconcile-cycle",
        profile=active_v2_profile,
        cycle_ordinal=1,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        max_actions=16,
        action_ordinal=1,
        current_action_state=BotMaintenanceCycle.ActionState.SUBMITTED,
        last_action_completion_source="guest.training_complete_at",
        next_slot_due_at=old_due_at,
        next_decision_at=old_due_at,
        covered_action_kinds=["training"],
        used_business_keys=["training:guest:1"],
        started_at=FIXED_NOW,
        payload={
            "pending_domain_actions": [
                {
                    "action_kind": "training",
                    "action_ordinal": 1,
                    "completion_source": "guest.training_complete_at",
                    "domain_event_kind": "guest_training",
                    "domain_object_id": int(guest.id),
                    "expected_completion_at": completion_at.isoformat().replace("+00:00", "Z"),
                },
            ],
        },
    )
    event = BotMaintenanceCompletionEvent.objects.create(
        profile=active_v2_profile,
        domain_event_id="vp-test-domain-completion-1",
        domain_event_kind=BotMaintenanceCompletionEvent.DomainKind.GUEST_TRAINING,
        domain_object_id=guest.id,
        origin_completed_at=completion_at,
        available_at=completion_at,
    )

    first = maintenance_completion.reconcile_virtual_player_maintenance_completion(
        event.id,
        now=FIXED_NOW + timedelta(hours=2),
    )

    cycle.refresh_from_db()
    event.refresh_from_db()
    assert first["status"] == BotMaintenanceCompletionEvent.Status.APPLIED
    assert event.status == BotMaintenanceCompletionEvent.Status.APPLIED
    assert cycle.current_action_state == BotMaintenanceCycle.ActionState.PLANNING
    assert cycle.next_slot_due_at > old_due_at
    assert cycle.next_decision_at == FIXED_NOW + timedelta(hours=2)
    assert cycle.payload["completion_reconcile_history"][-1]["matched_pending_action"] is True
    assert cycle.payload["last_completion_reconcile"]["effective_state"]["strength"]["components"]

    history_count = len(cycle.payload["completion_reconcile_history"])
    second = maintenance_completion.reconcile_virtual_player_maintenance_completion(
        event.id,
        now=FIXED_NOW + timedelta(hours=3),
    )
    cycle.refresh_from_db()
    assert second["status"] == BotMaintenanceCompletionEvent.Status.APPLIED
    assert len(cycle.payload["completion_reconcile_history"]) == history_count


@pytest.mark.django_db
def test_domain_completion_reconcile_uses_pending_owner_before_latest_cycle(active_v2_profile):
    guest = active_v2_profile.manor.guests.order_by("id").first()
    assert guest is not None
    completion_at = FIXED_NOW + timedelta(hours=1)
    old_cycle = BotMaintenanceCycle.objects.create(
        cycle_id="vp-completion-reconcile-old-cycle",
        interval_seed="vp-completion-reconcile-old-cycle",
        profile=active_v2_profile,
        cycle_ordinal=1,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        max_actions=16,
        action_ordinal=16,
        current_action_state=BotMaintenanceCycle.ActionState.SUBMITTED,
        last_action_completion_source="guest.training_complete_at",
        status=BotMaintenanceCycle.Status.COMPLETED,
        started_at=FIXED_NOW,
        completed_at=FIXED_NOW + timedelta(minutes=30),
        payload={
            "pending_domain_actions": [
                {
                    "action_kind": "training",
                    "action_ordinal": 16,
                    "completion_source": "guest.training_complete_at",
                    "domain_event_kind": "guest_training",
                    "domain_object_id": int(guest.id),
                    "expected_completion_at": completion_at.isoformat().replace("+00:00", "Z"),
                },
            ],
        },
    )
    latest_cycle = BotMaintenanceCycle.objects.create(
        cycle_id="vp-completion-reconcile-latest-cycle",
        interval_seed="vp-completion-reconcile-latest-cycle",
        profile=active_v2_profile,
        cycle_ordinal=2,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        max_actions=16,
        action_ordinal=1,
        current_action_state=BotMaintenanceCycle.ActionState.SUBMITTED,
        last_action_completion_source="guest.training_complete_at",
        next_slot_due_at=FIXED_NOW + timedelta(minutes=10),
        next_decision_at=FIXED_NOW + timedelta(minutes=10),
        started_at=FIXED_NOW + timedelta(minutes=45),
        payload={},
    )
    event = BotMaintenanceCompletionEvent.objects.create(
        profile=active_v2_profile,
        domain_event_id="vp-test-domain-completion-old-cycle-owner",
        domain_event_kind=BotMaintenanceCompletionEvent.DomainKind.GUEST_TRAINING,
        domain_object_id=guest.id,
        origin_completed_at=completion_at,
        available_at=completion_at,
    )

    result = maintenance_completion.reconcile_virtual_player_maintenance_completion(
        event.id,
        now=FIXED_NOW + timedelta(hours=2),
    )

    old_cycle.refresh_from_db()
    latest_cycle.refresh_from_db()
    assert result["summary"]["cycle_id"] == old_cycle.cycle_id
    assert old_cycle.payload["completion_reconcile_history"][-1]["matched_pending_action"] is True
    assert "completion_reconcile_history" not in latest_cycle.payload


@pytest.mark.django_db
def test_v2_cross_band_building_upgrade_uses_target_reference_and_cadence(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan = _prepare_cross_band_building_plan(active_v2_profile, monkeypatch)

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    active_v2_profile.manor.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert active_v2_profile.manor.prestige == plan.action_spec.prestige_after
    assert active_v2_profile.current_prestige_band == "junior"
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after


@pytest.mark.django_db
def test_v2_cross_band_domain_no_action_keeps_source_band_cadence(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan = _prepare_cross_band_building_plan(active_v2_profile, monkeypatch)
    monkeypatch.setattr(
        maintenance,
        "start_building_upgrade_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(maintenance.BuildingUpgradingError()),
    )

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "no_eligible_candidate"
    assert active_v2_profile.current_prestige_band == "newbie"
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after_no_action


@pytest.mark.django_db
def test_v2_technology_upgrade_commits_one_frozen_domain_action(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_upgrade_candidates(monkeypatch, keep="technology")
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=1_000_000,
        silver_capacity=1_000_000,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()

    plan = _scheduled_plan(active_v2_profile)

    assert plan.action_kind == TechnologyUpgradeActionSpec.action_kind
    assert isinstance(plan.action_spec, TechnologyUpgradeActionSpec)
    level_before = plan.action_spec.level_before

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    technology = PlayerTechnology.objects.get(
        manor_id=plan.manor_id,
        tech_key=plan.action_spec.technology_key,
    )
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == TechnologyUpgradeActionSpec.action_kind
    assert technology.level == level_before
    assert technology.is_upgrading is True
    assert technology.upgrade_complete_at is not None
    assert active_v2_profile.maintenance_sequence == 1
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


@pytest.mark.django_db
def test_v2_equipment_equip_commits_one_locked_frozen_action(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, template = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    gear_count_before = GearItem.objects.filter(manor_id=plan.manor_id).count()
    calls: list[tuple[int, int, int, str | None, str | None]] = []
    original_equip = maintenance.equip_guest_from_virtual_template_locked

    def equip_spy(
        manor,
        locked_guest,
        template_id,
        *,
        expected_template_key=None,
        expected_slot=None,
    ):
        assert transaction.get_connection().in_atomic_block
        calls.append(
            (
                int(manor.id),
                int(locked_guest.id),
                int(template_id),
                expected_template_key,
                expected_slot,
            )
        )
        return original_equip(
            manor,
            locked_guest,
            template_id,
            expected_template_key=expected_template_key,
            expected_slot=expected_slot,
        )

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_virtual_template_locked",
        equip_spy,
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    template.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == EquipmentEquipActionSpec.action_kind
    assert active_v2_profile.maintenance_sequence == 1
    assert calls == [
        (
            plan.manor_id,
            plan.action_spec.guest_id,
            plan.action_spec.item_template_id,
            plan.action_spec.item_key,
            plan.action_spec.slot,
        )
    ]
    assert template.key == plan.action_spec.item_key
    assert (
        GearItem.objects.filter(
            manor_id=plan.manor_id,
            guest_id=plan.target_id,
            template__key=plan.action_spec.item_key,
        ).count()
        == 1
    )
    assert GearItem.objects.filter(manor_id=plan.manor_id).count() == (gear_count_before + 1)
    assert not GearItem.objects.filter(
        manor_id=plan.manor_id,
        guest_id=plan.target_id,
        template__key=plan.action_spec.item_key,
        inventory_backed=True,
    ).exists()
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


@pytest.mark.django_db
def test_v2_equipment_plan_rejects_target_and_spec_mismatch(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, _item = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="guest maintenance target does not match its action spec",
    ):
        replace(plan, target_id=plan.action_spec.guest_id + 1)

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance action spec does not match its intent",
    ):
        replace(
            plan,
            action_spec=replace(
                plan.action_spec,
                item_key=f"{plan.action_spec.item_key}_tampered",
            ),
        )


@pytest.mark.parametrize("drift_kind", ("template", "equivalent_gear"))
@pytest.mark.django_db
def test_v2_equipment_drift_rejects_plan_without_writes(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
    drift_kind: str,
) -> None:
    plan, template = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    if drift_kind == "template":
        GearTemplate.objects.filter(pk=template.pk).update(extra_stats={"force": 25})
    else:
        drift_template = GearTemplate.objects.create(
            key=f"v2_equipment_drift_{active_v2_profile.id}",
            name="Equivalent drift gear",
            slot="helmet",
            rarity="green",
            extra_stats={},
        )
        GearItem.objects.create(
            manor_id=plan.manor_id,
            guest_id=plan.target_id,
            template=drift_template,
        )
    state_after_drift = _equipment_domain_state(plan)

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance_(precondition|plan)_changed",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    template.refresh_from_db()
    assert template.extra_stats == {"force": 25} if drift_kind == "template" else {"force": 10}
    assert _equipment_domain_state(plan) == state_after_drift
    assert GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_equipment_domain_constraint_rolls_back_action_savepoint(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, _template = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    domain_before = _equipment_domain_state(plan)
    original_equip = maintenance.equip_guest_from_virtual_template_locked

    def fail_after_equipment_write(*args, **kwargs):
        original_equip(*args, **kwargs)
        raise maintenance.EquipmentError("forced equipment domain constraint")

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_virtual_template_locked",
        fail_after_equipment_write,
    )

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "no_eligible_candidate"
    assert active_v2_profile.maintenance_sequence == 1
    assert _equipment_domain_state(plan) == domain_before
    assert GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_equipment_final_strength_mismatch_rolls_back_entire_cycle(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, _template = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    domain_before = _equipment_domain_state(plan)
    prestige_before = Manor.objects.values_list("prestige", flat=True).get(pk=plan.manor_id)
    original_equip = maintenance.equip_guest_from_virtual_template_locked

    def equip_with_strength_drift(manor, *args, **kwargs):
        equipped = original_equip(manor, *args, **kwargs)
        manor.prestige += 1
        manor.save(update_fields=["prestige"])
        return equipped

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_virtual_template_locked",
        equip_with_strength_drift,
    )

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="committed equipment equip strength differs from its frozen intent",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert _equipment_domain_state(plan) == domain_before
    assert Manor.objects.values_list("prestige", flat=True).get(pk=plan.manor_id) == prestige_before
    assert GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_scheduled_healing_runs_as_independent_cycle_preamble(
    active_v2_profile,
    permissive_reference,
) -> None:
    target = Guest.objects.filter(manor_id=active_v2_profile.manor_id).order_by("id").first()
    assert target is not None
    target.current_hp = max(1, int(target.max_hp * 0.1))
    target.status = GuestStatus.INJURED
    target.save(update_fields=["current_hp", "status"])

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    target.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind != "guest_healing"
    assert active_v2_profile.maintenance_sequence == 1
    assert target.current_hp == target.max_hp
    assert target.status == GuestStatus.IDLE
    cycle = BotMaintenanceCycle.objects.get(profile_id=active_v2_profile.id)
    healing_sweep = BotMaintenanceAttempt.objects.get(operation_id=cycle.healing_operation_id)
    assert healing_sweep.reason == ""
    assert healing_sweep.shadow_cost["kind"] == "guest_healing_sweep"
    assert healing_sweep.shadow_cost["real_silver"] == 1_000
    assert healing_sweep.shadow_cost["healed_guest_ids"] == [target.id]
    assert cycle.payload["healing_sweep"]["healed_guest_ids"] == [target.id]


@pytest.mark.django_db
def test_completed_scheduled_cycle_advances_ordinal(
    active_v2_profile,
) -> None:
    first = maintenance._open_policy2_scheduled_cycle(
        active_v2_profile.id,
        now=FIXED_NOW,
    )
    first.status = BotMaintenanceCycle.Status.COMPLETED
    first.completed_at = FIXED_NOW
    first.save(update_fields=["status", "completed_at", "updated_at"])

    second = maintenance._open_policy2_scheduled_cycle(
        active_v2_profile.id,
        now=FIXED_NOW + timedelta(minutes=1),
    )

    assert second.cycle_ordinal == first.cycle_ordinal + 1
    assert second.status == BotMaintenanceCycle.Status.OPEN


@pytest.mark.django_db
def test_v2_guest_healing_candidate_is_retired_from_policy2_action_slots(
    active_v2_profile,
    permissive_reference,
) -> None:
    target = Guest.objects.filter(manor_id=active_v2_profile.manor_id).order_by("id").first()
    assert target is not None
    target.current_hp = max(1, int(target.max_hp * 0.1))
    target.status = GuestStatus.INJURED
    target.save(update_fields=["current_hp", "status"])

    plan = _scheduled_plan(active_v2_profile)

    assert plan.medicine_quote is None
    assert all(assessment.intent.action_kind != "guest_healing" for assessment in plan.candidate_assessments)
    assert target.current_hp < target.max_hp


@pytest.mark.django_db
def test_v2_quantity_phase_recruits_before_quality_actions(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    quantity_template = GuestTemplate.objects.create(
        key=f"v2_quantity_phase_gray_{active_v2_profile.id}",
        name="V2 数量阶段灰门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        base_attack=80,
        base_intellect=80,
        base_defense=80,
        base_hp=800,
    )
    monkeypatch.setattr(
        maintenance,
        "_quantity_phase_guest_template",
        lambda **_kwargs: (quantity_template, True),
    )
    current_guest_count = Guest.objects.filter(manor_id=active_v2_profile.manor_id).count()
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        minimum_guest_count=current_guest_count + 2,
        minimum_guest_level=50,
        guest_rarity_cap=GuestRarity.GRAY,
        max_guest_level_step=3,
    )

    assert plan.action_kind == GuestRecruitmentActionSpec.action_kind
    assert isinstance(plan.action_spec, GuestRecruitmentActionSpec)
    assert plan.action_spec.quantity == 1
    assert plan.action_spec.template_id == quantity_template.id

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == GuestRecruitmentActionSpec.action_kind
    created_guests = tuple(
        Guest.objects.filter(manor_id=active_v2_profile.manor_id).exclude(template_id__isnull=True).order_by("-id")[:1]
    )
    assert len(created_guests) == 1
    assert all(guest.template_id == quantity_template.id for guest in created_guests)
    assert all(guest.custom_name and guest.custom_name != quantity_template.name for guest in created_guests)
    assert all(guest.level == 1 for guest in created_guests)
    assert not GuestSkill.objects.filter(guest_id__in=[guest.id for guest in created_guests]).exists()
    assert Guest.objects.filter(manor_id=active_v2_profile.manor_id).count() == current_guest_count + 1


@pytest.mark.django_db
def test_arena_capacity_expansion_precedes_quality_actions(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    """A full manor must expand Juxianzhuang before spending an arena slot on quality."""

    monkeypatch.setattr(maintenance, "remaining_guest_capacity", lambda _manor: 0)
    monkeypatch.setattr(
        Manor,
        "guest_capacity",
        property(lambda manor: manor.guests.count()),
    )
    current_guest_count = Guest.objects.filter(manor_id=active_v2_profile.manor_id).count()
    objective = ArenaGrowthObjective(
        critical_guest_count=current_guest_count + 1,
        preferred_guest_count=current_guest_count + 1,
        selected_power_lower_bound=1,
        selected_power_upper_bound=1,
        selected_power_before=0,
        target_team_power=1,
        lineup_mode="tournament",
        lineup_event_id=79,
        lineup_max_size=current_guest_count + 1,
        minimum_guest_level=1,
        recruitment_rarity_cap=GuestRarity.GRAY,
        max_guest_level_step=3,
    )

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        arena_growth_objective=objective,
    )

    assert plan.action_kind == BuildingUpgradeActionSpec.action_kind
    assert isinstance(plan.action_spec, BuildingUpgradeActionSpec)
    assert plan.action_spec.building_key == BuildingKeys.JUXIAN_ZHUANG


@pytest.mark.django_db
def test_arena_recruitment_candidate_completes_minimum_roster_before_event_cap(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    quantity_template = GuestTemplate.objects.create(
        key=f"v2_event_cap_gray_{active_v2_profile.id}",
        name="V2 活动上限灰门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        base_attack=80,
        base_intellect=80,
        base_defense=80,
        base_hp=800,
    )
    monkeypatch.setattr(
        maintenance,
        "_quantity_phase_guest_template",
        lambda **_kwargs: (quantity_template, True),
    )
    current_guest_count = Guest.objects.filter(manor_id=active_v2_profile.manor_id).count()
    objective = ArenaGrowthObjective(
        critical_guest_count=current_guest_count + 1,
        preferred_guest_count=current_guest_count + 1,
        selected_power_lower_bound=1,
        selected_power_upper_bound=1,
        selected_power_before=0,
        target_team_power=1,
        lineup_mode="tournament",
        lineup_event_id=78,
        lineup_max_size=current_guest_count + 1,
        minimum_guest_level=1,
        recruitment_rarity_cap=GuestRarity.GRAY,
        max_guest_level_step=3,
    )

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        arena_growth_objective=objective,
    )

    assessment = next(
        assessment
        for assessment in plan.candidate_assessments
        if assessment.intent.action_kind == GuestRecruitmentActionSpec.action_kind
    )
    assert plan.action_kind == GuestRecruitmentActionSpec.action_kind
    assert assessment.rejection_reasons == ()
    assert assessment.projected_selected_power is not None
    assert assessment.projected_selected_power > objective.selected_power_upper_bound
    assert assessment.event_power_cap == objective.selected_power_upper_bound


@pytest.mark.django_db
def test_arena_healing_runs_as_a_free_independent_sweep_for_a_durable_round(
    active_v2_profile,
    permissive_reference,
) -> None:
    target = Guest.objects.filter(manor_id=active_v2_profile.manor_id).order_by("id").first()
    assert target is not None
    target.current_hp = max(1, int(target.max_hp * 0.1))
    target.status = GuestStatus.INJURED
    target.save(update_fields=["current_hp", "status"])
    silver_before = Manor.objects.values_list("silver", flat=True).get(pk=active_v2_profile.manor_id)

    maintenance._run_arena_v2_healing_sweep(
        active_v2_profile.id,
        now=FIXED_NOW,
        arena_member_id=123,
        arena_round_ordinal=1,
    )

    target.refresh_from_db()
    assert target.current_hp == target.max_hp
    assert target.status == GuestStatus.IDLE
    assert Manor.objects.values_list("silver", flat=True).get(pk=active_v2_profile.manor_id) == silver_before
    sweep = BotMaintenanceAttempt.objects.get(operation_id="arena-member-123-r1-healing")
    assert sweep.shadow_cost["real_silver"] == 0
    assert sweep.shadow_cost["healed_guest_ids"] == [target.id]

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
    )
    assert all(assessment.intent.action_kind != "guest_healing" for assessment in plan.candidate_assessments)


@pytest.mark.django_db
def test_v2_recruitment_rarity_cap_filters_quantity_templates(
    active_v2_profile,
    permissive_reference,
) -> None:
    GuestTemplate.objects.create(
        key=f"v2_quantity_cap_black_{active_v2_profile.id}",
        name="V2 数量上限黑门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.BLACK,
        base_attack=60,
        base_intellect=60,
        base_defense=60,
        base_hp=600,
    )
    GuestTemplate.objects.create(
        key=f"v2_quantity_cap_orange_{active_v2_profile.id}",
        name="V2 数量上限橙门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.ORANGE,
        base_attack=600,
        base_intellect=600,
        base_defense=600,
        base_hp=6000,
    )
    current_guest_count = Guest.objects.filter(manor_id=active_v2_profile.manor_id).count()

    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        minimum_guest_count=current_guest_count + 1,
        minimum_guest_level=50,
        guest_rarity_cap=GuestRarity.BLACK,
        max_guest_level_step=3,
    )

    assert plan.recruitment_rarity_cap == GuestRarity.BLACK
    assert isinstance(plan.action_spec, GuestRecruitmentActionSpec)
    assert plan.action_spec.rarity == GuestRarity.BLACK


@pytest.mark.django_db
def test_v2_invalid_recruitment_rarity_cap_fails_closed(
    active_v2_profile,
) -> None:
    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        now=FIXED_NOW,
        guest_rarity_cap="mythical",
    )

    assert result.outcome is MaintenanceOutcome.PAUSED
    assert result.reason == "invalid_recruitment_rarity_cap"


@pytest.mark.django_db
def test_accelerated_growth_invalid_rarity_returns_paused_with_safety_evidence(
    active_v2_profile,
) -> None:
    operation_id = "arena-growth-invalid-rarity"

    outcome = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        now=FIXED_NOW,
        operation_id=operation_id,
        guest_rarity_cap="mythical",
    )

    assert outcome is AcceleratedGrowthOutcome.PAUSED
    terminal = BotSafetyMetricEvent.objects.get(
        event_id=f"maintenance:{operation_id}:1:terminal",
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
    )
    assert terminal.dimensions == {
        "result": "paused",
        "trigger": "arena_acceleration",
        "reason": "invalid_recruitment_rarity_cap",
    }
    assert not BotMaintenanceExecution.objects.filter(operation_id=operation_id).exists()


@pytest.mark.django_db
def test_v2_damaged_guests_without_medicine_receive_no_free_recovery(
    active_v2_profile,
    permissive_reference,
) -> None:
    guests = tuple(Guest.objects.filter(manor_id=active_v2_profile.manor_id).select_related("template").order_by("id"))
    hp_before: dict[int, int] = {}
    for guest in guests:
        guest.current_hp = max(1, int(guest.max_hp * 0.1))
        guest.status = GuestStatus.INJURED
        guest.save(update_fields=["current_hp", "status"])
        hp_before[int(guest.id)] = int(guest.current_hp)

    plan = _scheduled_plan(active_v2_profile)
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    assert plan.medicine_quote is None
    assert result.action_kind != "guest_healing"
    assert dict(Guest.objects.filter(pk__in=hp_before).values_list("id", "current_hp")) == hp_before


@pytest.mark.django_db
def test_v2_troop_recruitment_planner_is_read_only_and_deterministic(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    prepared = _prepare_troop_recruitment_plan(active_v2_profile, monkeypatch)

    with CaptureQueriesContext(connection) as captured:
        first = _scheduled_plan(active_v2_profile)
        second = _scheduled_plan(active_v2_profile)

    write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    writes = [
        query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(write_prefixes)
    ]
    assert writes == []
    assert first == second == prepared
    assert first.target_id is None
    assert first.training_levels == 0
    assert first.rng_seed is None


@pytest.mark.django_db
def test_v2_troop_recruitment_commits_audit_without_scheduling_celery(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan = _prepare_troop_recruitment_plan(active_v2_profile, monkeypatch)
    quote = plan.troop_recruitment_quote
    assert quote is not None
    assert quote.source == "virtual"
    domain_before = _troop_domain_state(plan)
    manor_before = Manor.objects.values("silver", "grain").get(pk=plan.manor_id)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda *_args, **_kwargs: pytest.fail("V2 synchronous recruitment scheduled Celery"),
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    domain_after = _troop_domain_state(plan)
    manor_after = Manor.objects.values("silver", "grain").get(pk=plan.manor_id)
    troops_before = dict(domain_before["troops"])
    troops_after = dict(domain_after["troops"])
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == "troop_recruitment"
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after
    assert not TroopRecruitment.objects.filter(manor_id=plan.manor_id).exists()
    assert domain_after["inventory"] == domain_before["inventory"]
    assert domain_after["retainer_count"] == domain_before["retainer_count"]
    assert manor_after["silver"] == (
        manor_before["silver"] - quote.virtual_silver_cost - plan.salary_quote.total_amount
    )
    assert manor_after["grain"] == manor_before["grain"] - quote.virtual_grain_cost
    assert troops_after[quote.troop_key] == (troops_before.get(quote.troop_key, 0) + quote.quantity)
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


@pytest.mark.django_db
def test_v2_training_commits_domain_result_budget_sequence_and_schedule_atomically(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_training_candidates(monkeypatch)
    plan = _scheduled_plan(active_v2_profile)
    assert plan.intent is not None
    assert plan.training_levels == 1
    target = Guest.objects.get(pk=plan.target_id)
    template_id_before = target.template_id
    level_before = target.level

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    target.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == "training"
    assert result.sequence_after == 1
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after
    assert active_v2_profile.next_growth_at > plan.next_growth_at_before
    assert target.template_id == template_id_before
    assert target.level == level_before
    assert target.training_target_level == level_before + plan.training_levels
    assert target.training_complete_at is not None
    assert target.training_remaining_seconds is None
    assert not TrainingLog.objects.filter(manor_id=plan.manor_id, guest_id=plan.target_id).exists()
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after
    if plan.intent.strength_after != plan.intent.strength_before:
        assert active_v2_profile.last_strength_increase_at == FIXED_NOW
        assert len(active_v2_profile.strength_budget_entries) == 1


@pytest.mark.django_db
def test_scheduled_maintenance_reuses_the_frozen_cycle_pacing_snapshot(
    active_v2_profile,
    monkeypatch,
) -> None:
    frozen_pacing = resolve_archetype_pacing(maintenance.load_virtual_player_config(), "dojo")
    BotMaintenanceCycle.objects.create(
        cycle_id="vp-cycle-frozen-pacing",
        profile=active_v2_profile,
        cycle_ordinal=1,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        max_actions=16,
        started_at=FIXED_NOW,
        current_action_state=BotMaintenanceCycle.ActionState.READY,
        next_slot_due_at=FIXED_NOW,
        next_decision_at=FIXED_NOW,
        payload={"archetype_pacing": frozen_pacing.to_payload()},
    )
    captured: dict[str, object] = {}

    def _capture_cycle_pacing(*_args, **kwargs):
        captured["pacing"] = kwargs["_cycle_pacing"]
        raise maintenance._V2MaintenanceOutcomeError(MaintenanceOutcome.PAUSED, "test_cycle_pacing")

    monkeypatch.setattr(maintenance, "build_virtual_player_v2_maintenance_plan", _capture_cycle_pacing)

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    assert result.outcome is MaintenanceOutcome.PAUSED
    assert captured["pacing"] == frozen_pacing


@pytest.mark.django_db
def test_v2_settlement_and_training_commit_in_one_cycle(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    plan = _scheduled_plan(active_v2_profile)

    assert dict(plan.resource_production_deltas) == {
        ResourceType.GRAIN: 50_000,
        ResourceType.SILVER: 50_000,
    }
    assert (
        plan.forced_settlement_decision.silver_units,
        plan.forced_settlement_decision.grain_units,
    ) == (10_000, 10_000)

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    manor = Manor.objects.get(pk=plan.manor_id)
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert active_v2_profile.maintenance_sequence == 1
    assert plan.salary_quote.unpaid_guest_ids
    assert SalaryPayment.objects.filter(
        manor_id=plan.manor_id,
        for_date=plan.salary_quote.for_date,
    ).count() == len(plan.salary_quote.unpaid_guest_ids)
    assert active_v2_profile.forced_settlement_daily_budget == {
        "utc_date": FIXED_NOW.date().isoformat(),
        "silver_units": 10_000,
        "grain_units": 10_000,
        "combined_units": 20_000,
        "silver_capacity_snapshot": 100_000,
        "grain_capacity_snapshot": 100_000,
    }
    assert manor.resource_updated_at == FIXED_NOW
    assert set(
        ResourceEvent.objects.filter(
            manor_id=plan.manor_id,
            reason=ResourceEvent.Reason.PRODUCE,
            note="虚拟玩家强制资源结算",
        ).values_list("resource_type", "delta")
    ) == {
        (ResourceType.GRAIN, 10_000),
        (ResourceType.SILVER, 10_000),
    }
    assert not ResourceEvent.objects.filter(
        manor_id=plan.manor_id,
        reason=ResourceEvent.Reason.PRODUCE,
        note="离线产出",
    ).exists()


@pytest.mark.django_db
def test_v2_unpaid_salary_falls_back_to_non_silver_action(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _prepare_equipment_plan(active_v2_profile, monkeypatch)
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=0,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()
    plan = _scheduled_plan(active_v2_profile)

    assert plan.action_kind == EquipmentEquipActionSpec.action_kind
    assert plan.intent is not None
    assert plan.salary_quote.total_amount > 0
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == EquipmentEquipActionSpec.action_kind
    assert active_v2_profile.maintenance_sequence == 1
    assert not SalaryPayment.objects.filter(manor_id=plan.manor_id).exists()
    assert not TrainingLog.objects.filter(manor_id=plan.manor_id).exists()


@pytest.mark.django_db
def test_v2_arena_cycle_preserves_normal_schedule_exactly(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    future_schedule = FIXED_NOW + timedelta(days=7)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(next_growth_at=future_schedule)
    _configure_due_resource_production(active_v2_profile, monkeypatch)

    outcome = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert outcome is AcceleratedGrowthOutcome.GROWN
    assert active_v2_profile.next_growth_at == future_schedule
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.forced_settlement_daily_budget["combined_units"] > 0


@pytest.mark.django_db
def test_manual_policy2_scheduled_cycle_replay_is_bounded_and_durable(
    active_v2_profile,
    permissive_reference,
) -> None:
    """Drive the real scheduled entry point until one durable cycle closes."""

    results = []
    current_time = FIXED_NOW
    for _ordinal in range(16):
        record_safety_heartbeat("safety_monitor", now=current_time)
        result = maintenance.maintain_virtual_player_v2(
            active_v2_profile.id,
            trigger=MaintenanceTrigger.SCHEDULED,
            now=current_time,
        )
        results.append(result)
        cycle = BotMaintenanceCycle.objects.filter(
            profile_id=active_v2_profile.id,
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        ).latest("cycle_ordinal")
        assert result.outcome in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}, result.reason
        assert 0 <= int(cycle.action_ordinal) <= 16
        if cycle.status == BotMaintenanceCycle.Status.COMPLETED:
            break
        assert cycle.next_slot_due_at is not None
        assert cycle.next_slot_due_at > current_time
        current_time = cycle.next_slot_due_at

    cycle = BotMaintenanceCycle.objects.filter(
        profile_id=active_v2_profile.id,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
    ).latest("cycle_ordinal")
    active_v2_profile.refresh_from_db()
    assert results
    assert all(result.outcome in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION} for result in results)
    assert any(result.outcome is MaintenanceOutcome.APPLIED for result in results)
    assert cycle.status == BotMaintenanceCycle.Status.COMPLETED
    assert cycle.action_ordinal <= cycle.max_actions == 16
    assert cycle.action_ordinal >= 1
    assert cycle.healing_operation_id
    assert BotMaintenanceAttempt.objects.filter(cycle_id=cycle.id).exists()
    assert (
        BotMaintenanceAttempt.objects.filter(
            cycle_id=cycle.id,
            shadow_cost__kind="guest_healing_sweep",
        ).count()
        == 1
    )
    assert active_v2_profile.next_growth_at > FIXED_NOW


@pytest.mark.django_db
def test_v2_arena_execution_receipt_prevents_duplicate_maintenance_sequence(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    future_schedule = FIXED_NOW + timedelta(days=7)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(next_growth_at=future_schedule)
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    kwargs = {
        "now": FIXED_NOW,
        "operation_id": "arena-growth-recovery-test",
        "attempt_ordinal": 1,
        "minimum_guest_count": 1,
        "minimum_guest_level": 2,
        "guest_rarity_cap": "purple",
        "max_guest_level_step": 20,
    }

    first = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        **kwargs,
    )
    second = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        **{**kwargs, "attempt_ordinal": 2},
    )

    active_v2_profile.refresh_from_db()
    receipt = BotMaintenanceExecution.objects.get(operation_id="arena-growth-recovery-test")
    assert first in {
        AcceleratedGrowthOutcome.GROWN,
        AcceleratedGrowthOutcome.NO_ACTION,
    }
    assert second is first
    assert active_v2_profile.maintenance_sequence == 1
    assert receipt.attempt_ordinal == 1
    assert receipt.maintenance_sequence_before == 0
    assert receipt.maintenance_sequence_after == 1


@pytest.mark.django_db
def test_v2_arena_execution_receipt_replay_preserves_no_action_reason(
    active_v2_profile,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_training_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_equipment_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_skill_learning_candidates", lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "build_virtual_troop_candidates", lambda **_kwargs: ((), {}))
    operation_id = "arena-growth-no-action-replay"
    kwargs = {
        "now": FIXED_NOW,
        "operation_id": operation_id,
        "attempt_ordinal": 1,
        "minimum_guest_count": 1,
        "minimum_guest_level": 1,
        "guest_rarity_cap": "purple",
        "max_guest_level_step": 20,
    }

    first = maintenance.accelerate_virtual_player_growth(active_v2_profile.id, **kwargs)
    replay = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        **{**kwargs, "attempt_ordinal": 2},
    )

    assert first is AcceleratedGrowthOutcome.NO_ACTION
    assert replay is first
    terminal = BotSafetyMetricEvent.objects.get(
        event_id=f"maintenance:{operation_id}:1:terminal",
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
    )
    assert terminal.dimensions == {
        "result": "no_action",
        "trigger": "arena_acceleration",
        "reason": "arena_action_unavailable",
    }
    receipt = BotMaintenanceExecution.objects.get(operation_id=operation_id)
    assert receipt.action_kind == ""
    assert not BotSafetyMetricEvent.objects.filter(metric_name=HARD_VIOLATION_METRIC_NAME).exists()


@pytest.mark.django_db
def test_v2_arena_execution_rejects_legacy_digest_schema(active_v2_profile) -> None:
    with pytest.raises(ValueError, match="schema 3"):
        maintenance.accelerate_virtual_player_growth(
            active_v2_profile.id,
            now=FIXED_NOW,
            operation_id="arena-growth-legacy-digest-replay",
            request_digest_schema=1,
        )


@pytest.mark.django_db
def test_v2_arena_execution_receipt_schema_two_rejects_objective_conflict(
    active_v2_profile,
) -> None:
    operation_id = "arena-growth-objective-conflict"
    objective = ArenaGrowthObjective(
        critical_guest_count=1,
        preferred_guest_count=1,
        selected_power_lower_bound=1,
        selected_power_upper_bound=1_000_000_000,
        selected_power_before=0,
        target_team_power=1,
        lineup_mode="tournament",
        lineup_event_id=99,
        lineup_max_size=1,
        minimum_guest_level=20,
        recruitment_rarity_cap="purple",
        max_guest_level_step=20,
    )
    maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        now=FIXED_NOW,
        operation_id=operation_id,
        arena_growth_objective=objective,
    )

    with pytest.raises(
        maintenance.MaintenanceExecutionConflict,
        match="operation_id already belongs to a different request",
    ):
        maintenance.accelerate_virtual_player_growth(
            active_v2_profile.id,
            now=FIXED_NOW,
            operation_id=operation_id,
            attempt_ordinal=2,
            arena_growth_objective=replace(
                objective,
                selected_power_lower_bound=2,
            ),
        )


@pytest.mark.django_db
def test_v2_arena_execution_receipt_rejects_operation_payload_conflict(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    kwargs = {
        "now": FIXED_NOW,
        "operation_id": "arena-growth-conflict-test",
        "attempt_ordinal": 1,
        "minimum_guest_count": 1,
        "minimum_guest_level": 2,
        "guest_rarity_cap": "purple",
        "max_guest_level_step": 20,
    }
    maintenance.accelerate_virtual_player_growth(active_v2_profile.id, **kwargs)

    with pytest.raises(
        maintenance.MaintenanceExecutionConflict,
        match="operation_id already belongs to a different request",
    ):
        maintenance.accelerate_virtual_player_growth(
            active_v2_profile.id,
            **{
                **kwargs,
                "attempt_ordinal": 2,
                "minimum_guest_level": 3,
            },
        )

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 1
    assert BotMaintenanceExecution.objects.filter(operation_id="arena-growth-conflict-test").count() == 1


@pytest.mark.django_db(transaction=True)
def test_v2_arena_execution_receipt_recovers_after_post_commit_failure(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    operation_id = "arena-growth-v2-post-commit-recovery"
    original_finish = maintenance._finish_safety_attempt_best_effort

    def fail_after_business_commit(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated crash after V2 maintenance commit")

    monkeypatch.setattr(
        maintenance,
        "_finish_safety_attempt_best_effort",
        fail_after_business_commit,
    )
    with pytest.raises(RuntimeError, match="after V2 maintenance commit"):
        maintenance.accelerate_virtual_player_growth(
            active_v2_profile.id,
            now=FIXED_NOW,
            operation_id=operation_id,
            attempt_ordinal=1,
            minimum_guest_count=1,
            minimum_guest_level=2,
            guest_rarity_cap="purple",
            max_guest_level_step=20,
        )

    active_v2_profile.refresh_from_db()
    receipt = BotMaintenanceExecution.objects.get(operation_id=operation_id)
    assert active_v2_profile.maintenance_sequence == 1

    monkeypatch.setattr(
        maintenance,
        "_finish_safety_attempt_best_effort",
        original_finish,
    )
    retry = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        now=FIXED_NOW,
        operation_id=operation_id,
        attempt_ordinal=2,
        minimum_guest_count=1,
        minimum_guest_level=2,
        guest_rarity_cap="purple",
        max_guest_level_step=20,
    )

    expected = {
        BotMaintenanceExecution.Outcome.APPLIED: AcceleratedGrowthOutcome.GROWN,
        BotMaintenanceExecution.Outcome.NO_ACTION: AcceleratedGrowthOutcome.NO_ACTION,
    }[receipt.outcome]
    active_v2_profile.refresh_from_db()
    assert retry is expected
    assert active_v2_profile.maintenance_sequence == 1
    assert BotMaintenanceExecution.objects.filter(operation_id=operation_id).count() == 1


@pytest.mark.django_db
def test_v2_settlement_resets_budget_on_first_positive_credit_of_new_utc_day(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(
        forced_settlement_daily_budget={
            "utc_date": (FIXED_NOW.date() - timedelta(days=1)).isoformat(),
            "silver_units": 50_000,
            "grain_units": 50_000,
            "combined_units": 100_000,
            "silver_capacity_snapshot": 100_000,
            "grain_capacity_snapshot": 100_000,
        }
    )
    active_v2_profile.refresh_from_db()

    plan = _scheduled_plan(active_v2_profile)
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert active_v2_profile.forced_settlement_daily_budget == {
        "utc_date": FIXED_NOW.date().isoformat(),
        "silver_units": 10_000,
        "grain_units": 10_000,
        "combined_units": 20_000,
        "silver_capacity_snapshot": 100_000,
        "grain_capacity_snapshot": 100_000,
    }


@pytest.mark.django_db
def test_v2_normal_schedule_does_not_skip_next_salary_day(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        maintenance,
        "next_normal_strength_check_at",
        lambda **_kwargs: FIXED_NOW + timedelta(days=2),
    )

    plan = _scheduled_plan(active_v2_profile)

    next_salary_date = timezone.localdate(FIXED_NOW) + timedelta(days=1)
    expected = timezone.make_aware(
        datetime.combine(next_salary_date, datetime.min.time()),
        timezone.get_current_timezone(),
    )
    assert plan.next_growth_at_after == expected


@pytest.mark.django_db
def test_v2_without_static_reference_catalog_commits_v2_action(
    active_v2_profile,
) -> None:
    plan = _scheduled_plan(active_v2_profile)
    assert plan.intent is not None

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.reason == ""
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after
    assert result.action_kind == plan.action_kind


@pytest.mark.django_db
def test_v2_execution_reuses_a_matching_live_routing_guard(
    active_v2_profile,
    monkeypatch,
) -> None:
    plan = _scheduled_plan(active_v2_profile)
    routing = maintenance.read_virtual_player_routing()

    def _unexpected_routing_read():
        raise AssertionError("matching routing guard must not issue another read")

    monkeypatch.setattr(
        maintenance,
        "read_virtual_player_routing",
        _unexpected_routing_read,
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(
        plan,
        _routing_snapshot=routing,
    )

    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.reason == ""
    assert result.action_kind == plan.action_kind


@pytest.mark.django_db
def test_v2_busy_result_keeps_sequence_and_schedule(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    schedule_before = active_v2_profile.next_growth_at
    monkeypatch.setattr(
        maintenance.profile_store,
        "lock_maintained_profile",
        lambda *_args, **_kwargs: None,
    )

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.BUSY
    assert result.next_growth_at_before == schedule_before
    assert result.next_growth_at_after == schedule_before
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.next_growth_at == schedule_before


@pytest.mark.django_db
def test_v2_stale_precondition_and_sequence_conflicts_write_nothing(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_training_candidates(monkeypatch)
    stale_plan = _scheduled_plan(active_v2_profile)
    Guest.objects.filter(pk=stale_plan.target_id).update(force=Guest.objects.get(pk=stale_plan.target_id).force + 1)

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance_precondition_changed",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(stale_plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert not TrainingLog.objects.filter(manor_id=stale_plan.manor_id).exists()

    fresh_plan = _scheduled_plan(active_v2_profile)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(maintenance_sequence=1)
    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance_sequence_conflict",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(fresh_plan)
    assert not TrainingLog.objects.filter(manor_id=fresh_plan.manor_id).exists()


@pytest.mark.django_db
def test_v2_routing_and_external_reconciliation_fail_closed(
    active_v2_profile,
    permissive_reference,
) -> None:
    plan = _scheduled_plan(active_v2_profile)
    routing_snapshot = maintenance.read_virtual_player_routing()
    routing = BotRuntimeRoutingState.objects.get()
    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED
    routing.revision += 1
    routing.save(update_fields=["maintenance_mode", "revision", "updated_at"])

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance_routing_changed",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(
            plan,
            _routing_snapshot=routing_snapshot,
        )
    assert (
        maintenance.accelerate_virtual_player_growth(
            active_v2_profile.id,
            now=FIXED_NOW,
        )
        is AcceleratedGrowthOutcome.PAUSED
    )

    routing.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    routing.revision += 1
    routing.save(update_fields=["maintenance_mode", "revision", "updated_at"])
    BotExternalStrengthReconciliation.objects.create(
        profile_id=active_v2_profile.id,
        domain_event_kind="maintenance-v2-test",
        domain_event_id="pending-1",
        origin_committed_at=FIXED_NOW,
        pre_strength_summary={},
        pre_prestige_band="newbie",
        available_at=FIXED_NOW,
    )
    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )
    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.PAUSED
    assert result.reason == "external_reconciliation_unresolved"
    assert active_v2_profile.maintenance_sequence == 0


@pytest.mark.django_db
def test_arena_growth_treats_unavailable_routing_as_recoverable_pause(
    active_v2_profile,
    monkeypatch,
) -> None:
    sequence_before = active_v2_profile.maintenance_sequence
    schedule_before = active_v2_profile.next_growth_at

    def unavailable_routing():
        raise maintenance.RuntimeRoutingError("routing unavailable")

    monkeypatch.setattr(maintenance, "read_virtual_player_routing", unavailable_routing)

    outcome = maintenance.accelerate_virtual_player_growth(
        active_v2_profile.id,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert outcome is AcceleratedGrowthOutcome.PAUSED
    assert active_v2_profile.maintenance_sequence == sequence_before
    assert active_v2_profile.next_growth_at == schedule_before


@pytest.mark.django_db
def test_scheduled_maintenance_reenters_after_routing_recovers(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    sequence_before = active_v2_profile.maintenance_sequence
    schedule_before = active_v2_profile.next_growth_at
    routing_available = False
    read_routing = maintenance.read_virtual_player_routing

    def conditional_routing():
        if not routing_available:
            raise maintenance.RuntimeRoutingError("routing unavailable")
        return read_routing()

    monkeypatch.setattr(maintenance, "read_virtual_player_routing", conditional_routing)
    monkeypatch.setattr(
        maintenance,
        "acquire_action_lock",
        lambda *_args, **_kwargs: (True, "scheduled-routing-recovery", "owner-token"),
    )
    monkeypatch.setattr(
        maintenance,
        "release_action_lock",
        lambda *_args, **_kwargs: None,
    )

    assert maintenance.maintain_due_virtual_players(now=FIXED_NOW, limit=1) == 0
    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == sequence_before
    assert active_v2_profile.next_growth_at == schedule_before

    routing_available = True
    assert maintenance.maintain_due_virtual_players(now=FIXED_NOW, limit=1) == 1
    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == sequence_before + 1
    assert active_v2_profile.next_growth_at > FIXED_NOW

    assert maintenance.maintain_due_virtual_players(now=FIXED_NOW, limit=1) == 0
    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == sequence_before + 1

    cycle = BotMaintenanceCycle.objects.get(
        profile_id=active_v2_profile.id,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
    )
    assert cycle.next_slot_due_at is not None
    record_safety_heartbeat("safety_monitor", now=cycle.next_slot_due_at)
    assert maintenance.maintain_due_virtual_players(now=cycle.next_slot_due_at, limit=1) == 1
    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == sequence_before + 2


@pytest.mark.django_db
def test_scheduled_batch_applies_limit_after_unresolved_reconciliation_filter(
    active_v2_profile,
    permissive_reference,
) -> None:
    healthy_profile = _create_v2_profile(seed=995_002)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(
        next_growth_at=FIXED_NOW - timedelta(hours=1),
    )
    BotExternalStrengthReconciliation.objects.create(
        profile_id=active_v2_profile.id,
        domain_event_kind="scheduled-maintenance-test",
        domain_event_id="unresolved-head-of-line",
        origin_committed_at=FIXED_NOW,
        pre_strength_summary={},
        pre_prestige_band="newbie",
        available_at=FIXED_NOW,
    )

    maintained = maintenance._maintain_due_virtual_players_v2(
        current_time=FIXED_NOW,
        limit=1,
        routing=maintenance.read_virtual_player_routing(),
    )

    active_v2_profile.refresh_from_db()
    healthy_profile.refresh_from_db()
    assert maintained == 1
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.next_growth_at == FIXED_NOW - timedelta(hours=1)
    assert healthy_profile.maintenance_sequence == 1
    assert healthy_profile.next_growth_at is not None
    assert healthy_profile.next_growth_at > FIXED_NOW


@pytest.mark.django_db
def test_due_selection_uses_flat_ordered_scan_without_window_sql(
    active_v2_profile,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        maintenance,
        "check_v2_development_write_preflight",
        lambda **_kwargs: SafetyWritePreflightResult(
            allowed=False,
            reason="test_stop_after_selection",
            checked_at=FIXED_NOW,
            monitor_heartbeat_at=FIXED_NOW,
        ),
    )

    with CaptureQueriesContext(connection) as captured:
        maintained = maintenance._maintain_due_virtual_players_v2(
            current_time=FIXED_NOW,
            limit=1,
            routing=maintenance.read_virtual_player_routing(),
        )

    assert maintained == 0
    assert captured.captured_queries
    assert any("LIMIT 1000" in query["sql"].upper() for query in captured.captured_queries)
    assert not any(
        token in query["sql"].upper() for query in captured.captured_queries for token in ("ROW_NUMBER", " OVER (")
    )


@pytest.mark.django_db
def test_scheduled_batch_isolates_a_profile_planning_failure(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    healthy_profile = _create_v2_profile(seed=995_003)
    original_builder = maintenance._scheduled_planning_snapshots

    def build_with_poison(profiles, *, planned_at):
        if len(profiles) > 1:
            raise ReferenceSnapshotError("batch contains a poison profile")
        if int(profiles[0].id) == int(active_v2_profile.id):
            raise ReferenceSnapshotError("poison profile")
        return original_builder(profiles, planned_at=planned_at)

    monkeypatch.setattr(
        maintenance,
        "_scheduled_planning_snapshots",
        build_with_poison,
    )

    maintained = maintenance._maintain_due_virtual_players_v2(
        current_time=FIXED_NOW,
        limit=2,
        routing=maintenance.read_virtual_player_routing(),
    )

    active_v2_profile.refresh_from_db()
    healthy_profile.refresh_from_db()
    assert maintained == 1
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.next_growth_at == FIXED_NOW
    assert healthy_profile.maintenance_sequence == 1
    terminal_results = sorted(
        event.dimensions["result"]
        for event in BotSafetyMetricEvent.objects.filter(
            metric_name=MAINTENANCE_ATTEMPT_METRIC,
            event_id__endswith=":terminal",
        )
    )
    assert terminal_results == ["applied", "failed"]


@pytest.mark.parametrize("error_type", (DatabaseError, SafetyProviderError))
@pytest.mark.django_db
def test_scheduled_batch_propagates_planning_infrastructure_errors(
    active_v2_profile,
    monkeypatch,
    error_type,
) -> None:
    monkeypatch.setattr(
        maintenance,
        "_scheduled_planning_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error_type("planning infrastructure unavailable")),
    )

    with pytest.raises(error_type, match="planning infrastructure unavailable"):
        maintenance._maintain_due_virtual_players_v2(
            current_time=FIXED_NOW,
            limit=1,
            routing=maintenance.read_virtual_player_routing(),
        )

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    terminal = BotSafetyMetricEvent.objects.get(
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
        event_id__endswith=":terminal",
    )
    assert terminal.dimensions["result"] == "failed"


@pytest.mark.django_db
def test_scheduled_batch_propagates_unknown_planning_errors(
    active_v2_profile,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        maintenance,
        "_scheduled_planning_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected planning invariant failure")),
    )

    with pytest.raises(RuntimeError, match="unexpected planning invariant failure"):
        maintenance._maintain_due_virtual_players_v2(
            current_time=FIXED_NOW,
            limit=1,
            routing=maintenance.read_virtual_player_routing(),
        )

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    terminal = BotSafetyMetricEvent.objects.get(
        metric_name=MAINTENANCE_ATTEMPT_METRIC,
        event_id__endswith=":terminal",
    )
    assert terminal.dimensions["result"] == "failed"


@pytest.mark.parametrize(
    ("corrupt_field", "corrupt_value"),
    (
        ("development_profile", {}),
        ("rng_version", 99),
        ("policy_checksum", "f" * 64),
    ),
)
@pytest.mark.django_db
def test_corrupt_v2_identity_or_profile_pauses_without_legacy_fallback(
    active_v2_profile,
    permissive_reference,
    corrupt_field,
    corrupt_value,
) -> None:
    snapshot = load_manor_strength_summary(manor_id=active_v2_profile.manor_id)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(**{corrupt_field: corrupt_value})

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.PAUSED
    assert result.reason == "v2_profile_or_policy_invalid"
    assert active_v2_profile.maintenance_sequence == 0
    assert load_manor_strength_summary(manor_id=active_v2_profile.manor_id) == snapshot


@pytest.mark.django_db
def test_v2_final_strength_mismatch_rolls_back_domain_and_cycle_writes(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _isolate_training_candidates(monkeypatch)
    plan = _scheduled_plan(active_v2_profile)
    assert plan.target_id is not None
    target_before = Guest.objects.values(
        "level",
        "force",
        "intellect",
        "defense_stat",
        "agility",
        "attribute_points",
    ).get(pk=plan.target_id)
    manor_before = Manor.objects.values("silver", "grain", "prestige").get(pk=plan.manor_id)
    original_strength_builder = maintenance._build_locked_snapshot_strength

    def build_with_strength_drift(**kwargs):
        strength = original_strength_builder(**kwargs)
        components = dict(strength.components)
        components["prestige"] = int(components.get("prestige", 0)) + 1
        return StrengthSummary(composite=strength.composite + 1, components=components)

    monkeypatch.setattr(
        maintenance,
        "_build_locked_snapshot_strength",
        build_with_strength_drift,
    )
    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="committed training strength differs from its frozen intent",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.strength_budget_entries == []
    assert Guest.objects.values(*target_before).get(pk=plan.target_id) == target_before
    assert Manor.objects.values(*manor_before).get(pk=plan.manor_id) == manor_before
    assert not TrainingLog.objects.filter(
        manor_id=plan.manor_id,
        guest_id=plan.target_id,
    ).exists()


@pytest.mark.django_db
def test_v2_domain_failure_rolls_back_training_and_cycle_metadata(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    plan = _scheduled_plan(active_v2_profile)
    assert plan.target_id is not None
    target_before = Guest.objects.values(
        "level",
        "force",
        "intellect",
        "defense_stat",
        "agility",
        "attribute_points",
    ).get(pk=plan.target_id)
    manor_before = Manor.objects.values(
        "silver",
        "grain",
        "resource_updated_at",
    ).get(pk=plan.manor_id)
    import guests.services.training as training_service

    original_reduce = training_service.reduce_training_time_for_guest

    def fail_after_timer_write(*args, **kwargs):
        original_reduce(*args, **kwargs)
        raise RuntimeError("forced maintenance rollback")

    monkeypatch.setattr(
        training_service,
        "reduce_training_time_for_guest",
        fail_after_timer_write,
    )
    with pytest.raises(RuntimeError, match="forced maintenance rollback"):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.strength_budget_entries == []
    assert active_v2_profile.forced_settlement_daily_budget == {}
    assert Guest.objects.values(*target_before).get(pk=plan.target_id) == target_before
    assert Manor.objects.values("silver", "grain", "resource_updated_at").get(pk=plan.manor_id) == manor_before
    assert not TrainingLog.objects.filter(
        manor_id=plan.manor_id,
        guest_id=plan.target_id,
    ).exists()
    assert not ResourceEvent.objects.filter(
        manor_id=plan.manor_id,
        reason=ResourceEvent.Reason.PRODUCE,
    ).exists()
    assert not SalaryPayment.objects.filter(manor_id=plan.manor_id).exists()


@pytest.mark.parametrize(
    "drift_kind",
    (
        "quote",
        "resources",
        "troop_distribution",
    ),
)
@pytest.mark.django_db
def test_v2_troop_recruitment_rejects_all_frozen_input_drift(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
    drift_kind,
) -> None:
    plan = _prepare_troop_recruitment_plan(active_v2_profile, monkeypatch)
    quote = plan.troop_recruitment_quote
    assert quote is not None

    if drift_kind == "quote":
        plan = replace(
            plan,
            troop_recruitment_quote=replace(
                quote,
                actual_duration=quote.actual_duration + 1,
            ),
        )
    elif drift_kind == "resources":
        manor = Manor.objects.get(pk=plan.manor_id)
        manor.silver = int(manor.silver) + 1
        manor.save(update_fields=["silver"])
    else:
        troop = PlayerTroop.objects.filter(manor_id=plan.manor_id).order_by("id").first()
        assert troop is not None
        PlayerTroop.objects.filter(pk=troop.pk).update(count=troop.count + 1)

    with pytest.raises(
        maintenance.V2MaintenanceError,
        match="maintenance_(precondition|plan)_changed",
    ):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert not TroopRecruitment.objects.filter(manor_id=plan.manor_id).exists()


@pytest.mark.django_db
def test_v2_troop_domain_constraint_rolls_back_savepoint_consumption(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan = _prepare_troop_recruitment_plan(active_v2_profile, monkeypatch)
    domain_before = _troop_domain_state(plan)
    quote = plan.troop_recruitment_quote
    assert quote is not None
    original_spend = maintenance.spend_resources_locked

    def fail_after_virtual_spend(manor, costs, *args, **kwargs):
        result = original_spend(manor, costs, *args, **kwargs)
        normalized_costs = {str(getattr(key, "value", key)): int(value) for key, value in costs.items()}
        if (
            normalized_costs.get(ResourceType.SILVER.value, 0) > 0
            and normalized_costs.get(
                ResourceType.GRAIN.value,
                0,
            )
            > 0
        ):
            raise maintenance.TroopRecruitmentError("forced virtual troop domain constraint")
        return result

    monkeypatch.setattr(
        maintenance,
        "spend_resources_locked",
        fail_after_virtual_spend,
    )

    result = maintenance.maintain_virtual_player_v2(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "no_eligible_candidate"
    assert active_v2_profile.maintenance_sequence == 1
    assert _troop_domain_state(plan) == domain_before


@pytest.mark.django_db
def test_v2_unexpected_troop_failure_rolls_back_entire_maintenance_cycle(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    plan = _prepare_troop_recruitment_plan(active_v2_profile, monkeypatch)
    domain_before = _troop_domain_state(plan)
    manor_before = Manor.objects.values(
        "silver",
        "grain",
        "resource_updated_at",
    ).get(pk=plan.manor_id)
    quote = plan.troop_recruitment_quote
    assert quote is not None
    original_spend = maintenance.spend_resources_locked

    def fail_after_virtual_spend(manor, costs, *args, **kwargs):
        result = original_spend(manor, costs, *args, **kwargs)
        normalized_costs = {str(getattr(key, "value", key)): int(value) for key, value in costs.items()}
        if (
            normalized_costs.get(ResourceType.SILVER.value, 0) > 0
            and normalized_costs.get(
                ResourceType.GRAIN.value,
                0,
            )
            > 0
        ):
            raise RuntimeError("forced virtual troop maintenance rollback")
        return result

    monkeypatch.setattr(
        maintenance,
        "spend_resources_locked",
        fail_after_virtual_spend,
    )

    with pytest.raises(RuntimeError, match="forced virtual troop maintenance rollback"):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.strength_budget_entries == []
    assert active_v2_profile.forced_settlement_daily_budget == {}
    assert _troop_domain_state(plan) == domain_before
    assert Manor.objects.values("silver", "grain", "resource_updated_at").get(pk=plan.manor_id) == manor_before
    assert not ResourceEvent.objects.filter(
        manor_id=plan.manor_id,
        reason=ResourceEvent.Reason.PRODUCE,
    ).exists()
    assert not SalaryPayment.objects.filter(manor_id=plan.manor_id).exists()
