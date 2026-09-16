from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import BotProfile, Building, Manor
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.contracts import (
    MaintenanceOutcome,
    MaintenanceTrigger,
    maintenance_trigger_policy,
)
from gameplay.services.virtual_player_core.profile_store import commit_maintenance_cycle


@pytest.fixture
def growth_profile(django_user_model, game_data) -> BotProfile:
    now = timezone.now()
    manor = ensure_manor(django_user_model.objects.create_user(username="growth_stage_sync"))
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        current_prestige_band="newbie",
        target_prestige_band="newbie",
        growth_seed=87_000,
        growth_stage=1,
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=2,
        policy_checksum="f" * 64,
        last_strength_increase_at=now,
        v2_enrolled_at=now,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


def _commit(profile: BotProfile, outcome: MaintenanceOutcome) -> None:
    with transaction.atomic():
        locked_profile = BotProfile.objects.select_for_update().get(pk=profile.pk)
        Manor.objects.select_for_update().get(pk=profile.manor_id)
        commit_maintenance_cycle(
            locked_profile,
            trigger_policy=maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED),
            expected_sequence=locked_profile.maintenance_sequence,
            now=locked_profile.next_growth_at,
            outcome=outcome,
            action_kind="building_upgrade" if outcome is MaintenanceOutcome.APPLIED else "",
            reason="no_candidate" if outcome is MaintenanceOutcome.NO_ACTION else "",
            expected_strength_budget_entries=(),
            strength_budget_entries_after=(),
            expected_last_strength_increase_at=locked_profile.last_strength_increase_at,
            last_strength_increase_at_after=locked_profile.last_strength_increase_at,
            next_growth_at_after=locked_profile.next_growth_at + timedelta(hours=4),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", [MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION])
def test_maintenance_repairs_old_stage_from_completed_buildings(growth_profile, outcome) -> None:
    building = Building.objects.filter(manor_id=growth_profile.manor_id).order_by("id").first()
    assert building is not None
    building.level = 12
    building.save(update_fields=["level"])
    before = tuple(Building.objects.filter(manor_id=growth_profile.manor_id).order_by("id").values_list("id", "level"))

    _commit(growth_profile, outcome)

    growth_profile.refresh_from_db()
    assert growth_profile.growth_stage == 12
    assert growth_profile.maintenance_sequence == 1
    assert (
        tuple(Building.objects.filter(manor_id=growth_profile.manor_id).order_by("id").values_list("id", "level"))
        == before
    )


@pytest.mark.django_db
def test_growth_stage_waits_for_building_upgrade_completion(growth_profile) -> None:
    building = Building.objects.filter(manor_id=growth_profile.manor_id).order_by("id").first()
    assert building is not None
    building.level = 10
    building.is_upgrading = True
    building.upgrade_complete_at = timezone.now() + timedelta(days=1)
    building.save(update_fields=["level", "is_upgrading", "upgrade_complete_at"])

    _commit(growth_profile, MaintenanceOutcome.APPLIED)

    growth_profile.refresh_from_db()
    building.refresh_from_db()
    assert growth_profile.growth_stage == 10
    assert building.is_upgrading
    assert building.level == 10

    building.level = 11
    building.is_upgrading = False
    building.upgrade_complete_at = None
    building.save(update_fields=["level", "is_upgrading", "upgrade_complete_at"])
    _commit(growth_profile, MaintenanceOutcome.NO_ACTION)

    growth_profile.refresh_from_db()
    assert growth_profile.growth_stage == 11
    assert growth_profile.maintenance_sequence == 2


@pytest.mark.django_db
def test_growth_stage_sync_rolls_back_with_maintenance_metadata(growth_profile) -> None:
    building = Building.objects.filter(manor_id=growth_profile.manor_id).order_by("id").first()
    assert building is not None
    building.level = 12
    building.save(update_fields=["level"])
    next_growth_at_before = growth_profile.next_growth_at

    with pytest.raises(RuntimeError, match="rollback maintenance"):
        with transaction.atomic():
            _commit(growth_profile, MaintenanceOutcome.APPLIED)
            raise RuntimeError("rollback maintenance")

    growth_profile.refresh_from_db()
    assert growth_profile.growth_stage == 1
    assert growth_profile.maintenance_sequence == 0
    assert growth_profile.next_growth_at == next_growth_at_before
