from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import (
    BotExternalStrengthReconciliation,
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
from gameplay.services.virtual_player_core import bootstrap, maintenance
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    MaintenanceOutcome,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    EquipmentEquipActionSpec,
    GuestRecruitmentActionSpec,
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
from gameplay.services.virtual_player_core.reference_snapshots import load_manor_strength_summary
from gameplay.services.virtual_player_core.safety_metrics import MAINTENANCE_ATTEMPT_METRIC, record_safety_heartbeat
from gameplay.services.virtual_player_core.safety_provider import HARD_VIOLATION_METRIC_NAME
from guests.models import (
    GearItem,
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
    return release_configured_policy_operation(version=1, apply=True)


def _set_active_routing() -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
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
    record_safety_heartbeat("safety_monitor", now=timezone.now())
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
        return {}, cap, selection

    monkeypatch.setattr(maintenance, "select_policy_reference", select_reference)
    return cap


def _scheduled_plan(profile: BotProfile):
    return maintenance.build_virtual_player_v2_maintenance_plan(
        profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
    )


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


def _prepare_guest_healing_plan(
    profile: BotProfile,
    *,
    quantity: int = 2,
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
    plan = _scheduled_plan(profile)
    assert plan.action_kind == "guest_healing"
    assert plan.target_id == target.id
    assert plan.medicine_quote is not None
    return plan, target, item


def _prepare_troop_recruitment_plan(profile: BotProfile):
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
):
    _isolate_equipment_candidates(monkeypatch)
    InventoryItem.objects.filter(
        manor_id=profile.manor_id,
        template__effect_type__startswith="equip_",
    ).delete()
    item_template = ItemTemplate.objects.create(
        key=f"v2_equipment_{profile.id}",
        name="V2 maintenance device",
        effect_type="equip_device",
        effect_payload={"force": 10},
        rarity="green",
        price=0,
    )
    item = InventoryItem.objects.create(
        manor_id=profile.manor_id,
        template=item_template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    plan = _scheduled_plan(profile)
    assert plan.action_kind == EquipmentEquipActionSpec.action_kind
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    assert plan.target_id == plan.action_spec.guest_id
    assert plan.action_spec.inventory_item_id == item.id
    assert plan.intent is not None
    return plan, item


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
    assert building.level == level_before + 1
    assert active_v2_profile.maintenance_sequence == 1
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


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
        "apply_building_upgrade_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(maintenance.BuildingUpgradingError()),
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "domain_constraint"
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
    assert technology.level == level_before + 1
    assert active_v2_profile.maintenance_sequence == 1
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after


@pytest.mark.django_db
def test_v2_scheduled_planning_snapshot_preloads_equipment_candidate_inputs(
    active_v2_profile,
    django_assert_num_queries,
) -> None:
    guest = Guest.objects.filter(manor_id=active_v2_profile.manor_id).first()
    assert guest is not None
    guest.status = GuestStatus.IDLE
    guest.training_complete_at = None
    guest.save(update_fields=["status", "training_complete_at"])
    template = GearTemplate.objects.create(
        key=f"v2_snapshot_equipment_{active_v2_profile.id}",
        name="V2 snapshot equipment",
        slot="helmet",
        rarity="green",
        extra_stats={},
    )
    gear = GearItem.objects.create(
        manor_id=active_v2_profile.manor_id,
        guest=guest,
        template=template,
    )
    item_template = ItemTemplate.objects.create(
        key=f"v2_snapshot_candidate_{active_v2_profile.id}",
        name="V2 snapshot candidate",
        effect_type="equip_device",
        effect_payload={"force": 40},
        rarity="green",
        price=0,
    )
    item = InventoryItem.objects.create(
        manor_id=active_v2_profile.manor_id,
        template=item_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    profile = BotProfile.objects.select_related("manor").get(pk=active_v2_profile.pk)

    snapshot = maintenance._scheduled_planning_snapshots(
        (profile,),
        planned_at=FIXED_NOW,
    )[int(profile.id)]

    assert gear.id in {item.id for item in snapshot.gear_items}
    with django_assert_num_queries(0):
        candidates, specs = maintenance.build_equipment_equip_candidates(
            manor_id=int(profile.manor_id),
            prestige_band=str(profile.current_prestige_band),
            strength_before=snapshot.strength,
            development_plan=maintenance.parse_development_plan(snapshot.profile.development_profile),
            growth_stage=int(profile.growth_stage),
            config=maintenance.load_virtual_player_config(),
            guests=snapshot.guests,
            gear_items=snapshot.gear_items,
            warehouse_items=snapshot.warehouse_items,
        )

    expected_key = f"equipment_equip:guest:{guest.id}:item:{item.template.key}"
    assert expected_key in {candidate.business_key for candidate in candidates}
    assert expected_key in specs


@pytest.mark.django_db
def test_v2_equipment_equip_commits_one_locked_frozen_action(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, item = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    gear_count_before = GearItem.objects.filter(manor_id=plan.manor_id).count()
    calls: list[tuple[int, int, int, str | None, str | None]] = []
    original_equip = maintenance.equip_guest_from_inventory_locked

    def equip_spy(
        manor,
        locked_guest,
        inventory_item_id,
        *,
        expected_template_key=None,
        expected_slot=None,
    ):
        assert transaction.get_connection().in_atomic_block
        calls.append(
            (
                int(manor.id),
                int(locked_guest.id),
                int(inventory_item_id),
                expected_template_key,
                expected_slot,
            )
        )
        return original_equip(
            manor,
            locked_guest,
            inventory_item_id,
            expected_template_key=expected_template_key,
            expected_slot=expected_slot,
        )

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_inventory_locked",
        equip_spy,
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    item.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == EquipmentEquipActionSpec.action_kind
    assert active_v2_profile.maintenance_sequence == 1
    assert calls == [
        (
            plan.manor_id,
            plan.action_spec.guest_id,
            plan.action_spec.inventory_item_id,
            plan.action_spec.item_key,
            plan.action_spec.slot,
        )
    ]
    assert item.quantity == 1
    assert (
        GearItem.objects.filter(
            manor_id=plan.manor_id,
            guest_id=plan.target_id,
            template__key=plan.action_spec.item_key,
        ).count()
        == 1
    )
    assert GearItem.objects.filter(manor_id=plan.manor_id).count() == (gear_count_before + 1)
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


@pytest.mark.parametrize("drift_kind", ("inventory", "equivalent_gear"))
@pytest.mark.django_db
def test_v2_equipment_drift_rejects_plan_without_writes(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
    drift_kind: str,
) -> None:
    plan, item = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    if drift_kind == "inventory":
        InventoryItem.objects.filter(pk=item.pk).update(quantity=3)
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
    item.refresh_from_db()
    assert active_v2_profile.maintenance_sequence == 0
    assert item.quantity == (3 if drift_kind == "inventory" else 2)
    assert _equipment_domain_state(plan) == state_after_drift
    assert not GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_equipment_domain_constraint_rolls_back_action_savepoint(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, _item = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    domain_before = _equipment_domain_state(plan)
    original_equip = maintenance.equip_guest_from_inventory_locked

    def fail_after_equipment_write(*args, **kwargs):
        original_equip(*args, **kwargs)
        raise maintenance.EquipmentError("forced equipment domain constraint")

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_inventory_locked",
        fail_after_equipment_write,
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "domain_constraint"
    assert active_v2_profile.maintenance_sequence == 1
    assert _equipment_domain_state(plan) == domain_before
    assert not GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_equipment_final_strength_mismatch_rolls_back_entire_cycle(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, _item = _prepare_equipment_plan(active_v2_profile, monkeypatch)
    assert isinstance(plan.action_spec, EquipmentEquipActionSpec)
    domain_before = _equipment_domain_state(plan)
    prestige_before = Manor.objects.values_list("prestige", flat=True).get(pk=plan.manor_id)
    original_equip = maintenance.equip_guest_from_inventory_locked

    def equip_with_strength_drift(manor, *args, **kwargs):
        equipped = original_equip(manor, *args, **kwargs)
        manor.prestige += 1
        manor.save(update_fields=["prestige"])
        return equipped

    monkeypatch.setattr(
        maintenance,
        "equip_guest_from_inventory_locked",
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
    assert not GearTemplate.objects.filter(key=plan.action_spec.item_key).exists()


@pytest.mark.django_db
def test_v2_guest_healing_commits_one_item_without_strength_budget(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, target, item = _prepare_guest_healing_plan(active_v2_profile)
    quote = plan.medicine_quote
    assert quote is not None
    guest_before = Guest.objects.values(
        "level",
        "template_id",
        "force",
        "intellect",
        "defense_stat",
        "agility",
        "attribute_points",
    ).get(pk=target.pk)
    last_strength_before = active_v2_profile.last_strength_increase_at

    def _unexpected_growth_evaluation(**_kwargs):
        pytest.fail("guest healing entered permanent strength evaluation")

    monkeypatch.setattr(
        maintenance,
        "evaluate_controlled_action",
        _unexpected_growth_evaluation,
    )
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    target.refresh_from_db()
    item.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == "guest_healing"
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.strength_budget_entries == []
    assert active_v2_profile.last_strength_increase_at == last_strength_before
    assert target.current_hp == quote.new_hp
    assert target.status == quote.status_after
    assert item.quantity == 1
    assert Guest.objects.values(*guest_before).get(pk=target.pk) == guest_before


@pytest.mark.django_db
def test_v2_guest_healing_unexpected_failure_rolls_back_item_hp_and_sequence(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    plan, target, item = _prepare_guest_healing_plan(active_v2_profile)
    target_before = (target.current_hp, target.status)
    original_apply = maintenance.apply_medicine_item_for_guest_locked

    def _fail_after_healing(*args, **kwargs):
        original_apply(*args, **kwargs)
        raise RuntimeError("forced guest healing rollback")

    monkeypatch.setattr(
        maintenance,
        "apply_medicine_item_for_guest_locked",
        _fail_after_healing,
    )
    with pytest.raises(RuntimeError, match="forced guest healing rollback"):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    target.refresh_from_db()
    item.refresh_from_db()
    assert (target.current_hp, target.status) == target_before
    assert item.quantity == 2
    assert active_v2_profile.maintenance_sequence == 0
    assert active_v2_profile.strength_budget_entries == []


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
    assert plan.action_spec.quantity == 2
    assert plan.action_spec.template_id == quantity_template.id

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == GuestRecruitmentActionSpec.action_kind
    created_guests = tuple(
        Guest.objects.filter(manor_id=active_v2_profile.manor_id).exclude(template_id__isnull=True).order_by("-id")[:2]
    )
    assert len(created_guests) == 2
    assert all(guest.template_id == quantity_template.id for guest in created_guests)
    assert all(guest.level == 1 for guest in created_guests)
    assert not GuestSkill.objects.filter(guest_id__in=[guest.id for guest in created_guests]).exists()
    assert Guest.objects.filter(manor_id=active_v2_profile.manor_id).count() == current_guest_count + 2


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
) -> None:
    prepared = _prepare_troop_recruitment_plan(active_v2_profile)

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
    plan = _prepare_troop_recruitment_plan(active_v2_profile)
    quote = plan.troop_recruitment_quote
    assert quote is not None
    domain_before = _troop_domain_state(plan)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda *_args, **_kwargs: pytest.fail("V2 synchronous recruitment scheduled Celery"),
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    recruitment = TroopRecruitment.objects.get(manor_id=plan.manor_id)
    domain_after = _troop_domain_state(plan)
    troops_before = dict(domain_before["troops"])
    troops_after = dict(domain_after["troops"])
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == "troop_recruitment"
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after
    assert recruitment.status == TroopRecruitment.Status.COMPLETED
    assert recruitment.complete_at == FIXED_NOW
    assert recruitment.finished_at == FIXED_NOW
    assert recruitment.actual_duration == quote.actual_duration
    assert recruitment.equipment_costs == dict(quote.equipment_costs)
    assert domain_after["retainer_count"] == (int(domain_before["retainer_count"]) - quote.retainer_cost)
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
    target = Guest.objects.get(pk=plan.target_id)
    template_id_before = target.template_id

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
    assert (
        TrainingLog.objects.filter(
            manor_id=plan.manor_id,
            guest_id=plan.target_id,
        ).count()
        == 1
    )
    assert load_manor_strength_summary(manor_id=plan.manor_id) == plan.intent.strength_after
    if plan.intent.strength_after != plan.intent.strength_before:
        assert active_v2_profile.last_strength_increase_at == FIXED_NOW
        assert len(active_v2_profile.strength_budget_entries) == 1


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
def test_v2_unpaid_salary_has_priority_over_training(
    active_v2_profile,
    permissive_reference,
) -> None:
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(
        silver=0,
        resource_updated_at=FIXED_NOW,
    )
    active_v2_profile.manor.refresh_from_db()
    plan = _scheduled_plan(active_v2_profile)

    assert plan.intent is not None
    assert plan.salary_quote.total_amount > 0
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "domain_constraint"
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
    assert active_v2_profile.forced_settlement_daily_budget["combined_units"] == 20_000


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
) -> None:
    operation_id = "arena-growth-no-action-replay"
    kwargs = {
        "now": FIXED_NOW,
        "operation_id": operation_id,
        "attempt_ordinal": 1,
        "minimum_guest_count": 1,
        "minimum_guest_level": 2,
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
        "reason": "strength_cap",
    }
    assert not BotSafetyMetricEvent.objects.filter(metric_name=HARD_VIOLATION_METRIC_NAME).exists()


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
def test_v2_no_reference_commits_structured_no_action_without_domain_write(
    active_v2_profile,
) -> None:
    plan = _scheduled_plan(active_v2_profile)
    assert plan.intent is not None

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "strength_cap"
    assert active_v2_profile.maintenance_sequence == 1
    assert active_v2_profile.next_growth_at == plan.next_growth_at_after
    assert not TrainingLog.objects.filter(manor_id=plan.manor_id).exists()


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

    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "strength_cap"


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
    original_apply = maintenance.apply_training_locked

    def apply_with_strength_drift(manor, *args, **kwargs):
        trained_guest = original_apply(manor, *args, **kwargs)
        manor.prestige += 1
        manor.save(update_fields=["prestige"])
        return trained_guest

    monkeypatch.setattr(
        maintenance,
        "apply_training_locked",
        apply_with_strength_drift,
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
    original_apply = maintenance.apply_training_locked

    def fail_after_domain_write(*args, **kwargs):
        original_apply(*args, **kwargs)
        raise RuntimeError("forced maintenance rollback")

    monkeypatch.setattr(
        maintenance,
        "apply_training_locked",
        fail_after_domain_write,
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
        "inventory",
        "retainer",
        "technology",
        "building",
        "troop_distribution",
    ),
)
@pytest.mark.django_db
def test_v2_troop_recruitment_rejects_all_frozen_input_drift(
    active_v2_profile,
    permissive_reference,
    drift_kind,
) -> None:
    plan = _prepare_troop_recruitment_plan(active_v2_profile)
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
    elif drift_kind == "inventory":
        item_key = quote.equipment_costs[0][0]
        item = InventoryItem.objects.get(
            manor_id=plan.manor_id,
            template__key=item_key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        InventoryItem.objects.filter(pk=item.pk).update(quantity=item.quantity + 1)
    elif drift_kind == "retainer":
        Manor.objects.filter(pk=plan.manor_id).update(retainer_count=quote.retainer_count + 1)
    elif drift_kind == "technology":
        assert quote.tech_key is not None
        PlayerTechnology.objects.filter(
            manor_id=plan.manor_id,
            tech_key=quote.tech_key,
        ).update(level=quote.tech_level + 1)
    elif drift_kind == "building":
        building = Building.objects.get(
            manor_id=plan.manor_id,
            building_type__key="lianggongchang",
        )
        Building.objects.filter(pk=building.pk).update(level=building.level + 1)
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
    plan = _prepare_troop_recruitment_plan(active_v2_profile)
    domain_before = _troop_domain_state(plan)
    original_recruit = maintenance.recruit_troops_locked

    def fail_after_recruitment_write(*args, **kwargs):
        original_recruit(*args, **kwargs)
        raise maintenance.TroopRecruitmentError("forced domain constraint")

    monkeypatch.setattr(
        maintenance,
        "recruit_troops_locked",
        fail_after_recruitment_write,
    )

    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert result.reason == "domain_constraint"
    assert active_v2_profile.maintenance_sequence == 1
    assert _troop_domain_state(plan) == domain_before


@pytest.mark.django_db
def test_v2_unexpected_troop_failure_rolls_back_entire_maintenance_cycle(
    active_v2_profile,
    permissive_reference,
    monkeypatch,
) -> None:
    _configure_due_resource_production(active_v2_profile, monkeypatch)
    plan = _prepare_troop_recruitment_plan(active_v2_profile)
    domain_before = _troop_domain_state(plan)
    manor_before = Manor.objects.values(
        "silver",
        "grain",
        "resource_updated_at",
    ).get(pk=plan.manor_id)
    original_recruit = maintenance.recruit_troops_locked

    def fail_after_recruitment_write(*args, **kwargs):
        original_recruit(*args, **kwargs)
        raise RuntimeError("forced troop maintenance rollback")

    monkeypatch.setattr(
        maintenance,
        "recruit_troops_locked",
        fail_after_recruitment_write,
    )

    with pytest.raises(RuntimeError, match="forced troop maintenance rollback"):
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
