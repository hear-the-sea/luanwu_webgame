from __future__ import annotations

import pytest

from gameplay.constants import BuildingKeys
from gameplay.models import BotProfile, Building, InventoryItem, Manor
from gameplay.services.inventory.core import GRAIN_ITEM_KEY
from gameplay.services.manor.core import quote_building_upgrade
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.contracts import MaintenanceOutcome, MaintenanceTrigger
from gameplay.services.virtual_player_core.maintenance_action_specs import BuildingUpgradeActionSpec
from tests import test_virtual_player_maintenance_v2 as v2_fixtures

FIXED_NOW = v2_fixtures.FIXED_NOW
active_v2_profile = v2_fixtures.active_v2_profile
permissive_reference = v2_fixtures.permissive_reference
released_v2_policy = v2_fixtures.released_v2_policy


@pytest.fixture(params=(False, True), ids=("lazy", "eager"))
def candidate_exclusions(request) -> tuple[str, ...]:
    # Five lock-time exclusions activate the planner's full-candidate retry path.
    return tuple(f"unrelated-retry-{index}" for index in range(5)) if request.param else ()


def _set_capacity_scenario(
    profile: BotProfile,
    *,
    silver: int,
    grain: int,
    silver_capacity: int,
    grain_capacity: int,
) -> None:
    BotProfile.objects.filter(pk=profile.pk).update(guest_count_target=5)
    Building.objects.filter(
        manor_id=profile.manor_id,
        building_type__key__in=(BuildingKeys.JUXIAN_ZHUANG, BuildingKeys.SILVER_VAULT, BuildingKeys.GRANARY),
    ).update(level=1, is_upgrading=False, upgrade_complete_at=None)
    Manor.objects.filter(pk=profile.manor_id).update(
        silver=silver,
        grain=grain,
        silver_capacity=silver_capacity,
        grain_capacity=grain_capacity,
        resource_updated_at=FIXED_NOW,
    )
    InventoryItem.objects.filter(
        manor_id=profile.manor_id,
        template__key=GRAIN_ITEM_KEY,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).update(quantity=grain)
    profile.refresh_from_db()
    profile.manor.refresh_from_db()
    profile.manor.invalidate_building_cache()
    assert profile.manor.guest_capacity == 4


def _capacity_plan(profile: BotProfile, candidate_exclusions: tuple[str, ...]):
    return maintenance.build_virtual_player_v2_maintenance_plan(
        profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
        _cycle_guest_count_target=5,
        _cycle_guest_count_target_frozen=True,
        _candidate_exclusions=candidate_exclusions,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("silver_capacity", "grain_capacity", "resource", "expected_building"),
    (
        (20_000, 100_000, "silver", BuildingKeys.SILVER_VAULT),
        (100_000, 5_000, "grain", BuildingKeys.GRANARY),
    ),
)
def test_roster_capacity_expansion_first_upgrades_storage_that_cannot_hold_its_cost(
    active_v2_profile,
    permissive_reference,
    candidate_exclusions,
    silver_capacity,
    grain_capacity,
    resource,
    expected_building,
) -> None:
    _set_capacity_scenario(
        active_v2_profile,
        silver=silver_capacity,
        grain=grain_capacity,
        silver_capacity=silver_capacity,
        grain_capacity=grain_capacity,
    )
    juxianzhuang = Building.objects.select_related("building_type").get(
        manor_id=active_v2_profile.manor_id,
        building_type__key=BuildingKeys.JUXIAN_ZHUANG,
    )
    quote = quote_building_upgrade(active_v2_profile.manor, juxianzhuang)
    assert dict(quote.resource_cost)[resource] > getattr(active_v2_profile.manor, f"{resource}_capacity")

    plan = _capacity_plan(active_v2_profile, candidate_exclusions)

    assert plan.ordinary_guest_count_target == 5
    assert isinstance(plan.action_spec, BuildingUpgradeActionSpec)
    assert plan.action_spec.building_key == expected_building
    assert plan.selected_candidate_assessment is not None
    assert plan.selected_candidate_assessment.allowed

    prestige_before = active_v2_profile.manor.prestige
    result = maintenance.execute_virtual_player_v2_maintenance_plan(plan)

    active_v2_profile.manor.refresh_from_db()
    upgraded = Building.objects.get(pk=plan.action_spec.building_id)
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert upgraded.is_upgrading
    assert upgraded.level == 1
    assert active_v2_profile.manor.prestige > prestige_before


@pytest.mark.django_db
def test_roster_capacity_expansion_still_prioritizes_juxianzhuang_when_affordable(
    active_v2_profile,
    permissive_reference,
    candidate_exclusions,
) -> None:
    _set_capacity_scenario(
        active_v2_profile,
        silver=100_000,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
    )

    plan = _capacity_plan(active_v2_profile, candidate_exclusions)

    assert isinstance(plan.action_spec, BuildingUpgradeActionSpec)
    assert plan.action_spec.building_key == BuildingKeys.JUXIAN_ZHUANG
    assert plan.selected_candidate_assessment is not None
    assert plan.selected_candidate_assessment.allowed


@pytest.mark.django_db
def test_building_resource_shortage_does_not_report_a_technology_failure(
    active_v2_profile,
    permissive_reference,
    candidate_exclusions,
) -> None:
    _set_capacity_scenario(
        active_v2_profile,
        silver=0,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
    )

    plan = _capacity_plan(active_v2_profile, candidate_exclusions)

    assessment = plan.selected_candidate_assessment
    assert isinstance(plan.action_spec, BuildingUpgradeActionSpec)
    assert assessment is not None
    assert not assessment.allowed
    assert "insufficient_resource" in assessment.rejection_reasons
    assert not any(reason.startswith("technology_") for reason in assessment.rejection_reasons)
