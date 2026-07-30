from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import BotProfile, Manor, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.contracts import (
    MaintenanceOutcome,
    MaintenanceTrigger,
    maintenance_trigger_policy,
)
from gameplay.services.virtual_player_core.maintenance import apply_forced_resource_settlement_locked
from gameplay.services.virtual_player_core.profile_store import ProfileStateConflict, commit_maintenance_cycle


def _create_v2_profile(django_user_model, *, suffix: str) -> BotProfile:
    now = timezone.now()
    manor = ensure_manor(
        django_user_model.objects.create_user(
            username=f"maintenance_v2_{suffix}",
            password="pass123",
        )
    )
    manor.silver = 0
    manor.grain = 0
    manor.silver_capacity = 1_000
    manor.grain_capacity = 2_000
    manor.resource_updated_at = now
    manor.save(
        update_fields=[
            "silver",
            "grain",
            "silver_capacity",
            "grain_capacity",
            "resource_updated_at",
        ]
    )
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=81_000,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=1,
        policy_checksum="a" * 64,
        last_strength_increase_at=now,
        v2_enrolled_at=now,
    )


def _apply_settlement(
    profile_id: int,
    *,
    now,
    requested_silver: int,
    requested_grain: int,
):
    with transaction.atomic():
        profile = BotProfile.objects.select_for_update().get(pk=profile_id)
        manor = Manor.objects.select_for_update().get(pk=profile.manor_id)
        return apply_forced_resource_settlement_locked(
            profile,
            manor,
            now=now,
            requested_silver=requested_silver,
            requested_grain=requested_grain,
        )


@pytest.mark.django_db
def test_forced_settlement_commits_resource_events_and_budget_together(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="commit")
    now = timezone.now()

    decision = _apply_settlement(
        profile.id,
        now=now,
        requested_silver=10_000,
        requested_grain=10_000,
    )

    profile.refresh_from_db(fields=["forced_settlement_daily_budget"])
    profile.manor.refresh_from_db(fields=["silver", "grain"])
    assert (decision.silver_units, decision.grain_units) == (100, 200)
    assert (profile.manor.silver, profile.manor.grain) == (100, 200)
    assert profile.forced_settlement_daily_budget == {
        "utc_date": now.astimezone(UTC).date().isoformat(),
        "silver_units": 100,
        "grain_units": 200,
        "combined_units": 300,
        "silver_capacity_snapshot": 1_000,
        "grain_capacity_snapshot": 2_000,
    }
    assert set(
        ResourceEvent.objects.filter(
            manor=profile.manor,
            note="虚拟玩家强制资源结算",
        ).values_list("resource_type", "delta")
    ) == {(ResourceType.SILVER, 100), (ResourceType.GRAIN, 200)}


@pytest.mark.django_db
def test_forced_settlement_rolls_back_resource_events_and_budget_together(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="rollback")
    now = timezone.now()

    with pytest.raises(RuntimeError, match="force rollback"):
        with transaction.atomic():
            locked_profile = BotProfile.objects.select_for_update().get(pk=profile.id)
            locked_manor = Manor.objects.select_for_update().get(pk=locked_profile.manor_id)
            apply_forced_resource_settlement_locked(
                locked_profile,
                locked_manor,
                now=now,
                requested_silver=10_000,
                requested_grain=10_000,
            )
            raise RuntimeError("force rollback")

    profile.refresh_from_db(fields=["forced_settlement_daily_budget"])
    profile.manor.refresh_from_db(fields=["silver", "grain"])
    assert profile.forced_settlement_daily_budget == {}
    assert (profile.manor.silver, profile.manor.grain) == (0, 0)
    assert not ResourceEvent.objects.filter(
        manor=profile.manor,
        note="虚拟玩家强制资源结算",
    ).exists()


@pytest.mark.django_db
def test_forced_settlement_keeps_first_positive_capacity_snapshot_for_utc_day(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="frozen_capacity")
    now = timezone.now()
    _apply_settlement(
        profile.id,
        now=now,
        requested_silver=10_000,
        requested_grain=10_000,
    )
    Manor.objects.filter(pk=profile.manor_id).update(
        silver_capacity=10_000,
        grain_capacity=20_000,
    )

    decision = _apply_settlement(
        profile.id,
        now=now + timedelta(minutes=1),
        requested_silver=10_000,
        requested_grain=10_000,
    )

    profile.refresh_from_db(fields=["forced_settlement_daily_budget"])
    assert (decision.silver_units, decision.grain_units) == (400, 800)
    assert profile.forced_settlement_daily_budget == {
        "utc_date": now.astimezone(UTC).date().isoformat(),
        "silver_units": 500,
        "grain_units": 1_000,
        "combined_units": 1_500,
        "silver_capacity_snapshot": 1_000,
        "grain_capacity_snapshot": 2_000,
    }


@pytest.mark.django_db
def test_zero_credit_does_not_freeze_or_consume_forced_settlement_budget(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="zero_credit")
    Manor.objects.filter(pk=profile.manor_id).update(silver=1_000, grain=2_000)

    decision = _apply_settlement(
        profile.id,
        now=timezone.now(),
        requested_silver=10_000,
        requested_grain=10_000,
    )

    profile.refresh_from_db(fields=["forced_settlement_daily_budget"])
    assert decision.combined_units == 0
    assert profile.forced_settlement_daily_budget == {}


@pytest.mark.django_db
def test_scheduled_no_action_cycle_advances_sequence_and_schedule_atomically(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="scheduled_no_action")
    next_growth_at_after = profile.next_growth_at + timedelta(hours=4)

    with transaction.atomic():
        locked_profile = BotProfile.objects.select_for_update().get(pk=profile.id)
        result = commit_maintenance_cycle(
            locked_profile,
            trigger_policy=maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED),
            expected_sequence=0,
            now=profile.next_growth_at,
            outcome=MaintenanceOutcome.NO_ACTION,
            expected_strength_budget_entries=(),
            strength_budget_entries_after=(),
            expected_last_strength_increase_at=profile.last_strength_increase_at,
            last_strength_increase_at_after=profile.last_strength_increase_at,
            next_growth_at_after=next_growth_at_after,
            reason="domain_constraint",
        )

    profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.NO_ACTION
    assert profile.maintenance_sequence == 1
    assert profile.next_growth_at == next_growth_at_after
    assert profile.last_planned_at == profile.next_growth_at - timedelta(hours=4)


@pytest.mark.django_db
def test_arena_applied_cycle_preserves_normal_schedule_exactly(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="arena_applied")

    with transaction.atomic():
        locked_profile = BotProfile.objects.select_for_update().get(pk=profile.id)
        result = commit_maintenance_cycle(
            locked_profile,
            trigger_policy=maintenance_trigger_policy(MaintenanceTrigger.ARENA_ACCELERATION),
            expected_sequence=0,
            now=timezone.now(),
            outcome=MaintenanceOutcome.APPLIED,
            expected_strength_budget_entries=(),
            strength_budget_entries_after=(),
            expected_last_strength_increase_at=profile.last_strength_increase_at,
            last_strength_increase_at_after=profile.last_strength_increase_at,
            next_growth_at_after=profile.next_growth_at,
            action_kind="salary_settlement",
        )

    profile.refresh_from_db()
    assert result.outcome is MaintenanceOutcome.APPLIED
    assert profile.maintenance_sequence == 1
    assert profile.next_growth_at == result.next_growth_at_before


@pytest.mark.django_db
def test_cycle_sequence_conflict_does_not_modify_profile_metadata(
    django_user_model,
) -> None:
    profile = _create_v2_profile(django_user_model, suffix="sequence_conflict")
    original_next_growth_at = profile.next_growth_at

    with pytest.raises(ProfileStateConflict, match="maintenance sequence changed"):
        with transaction.atomic():
            locked_profile = BotProfile.objects.select_for_update().get(pk=profile.id)
            commit_maintenance_cycle(
                locked_profile,
                trigger_policy=maintenance_trigger_policy(MaintenanceTrigger.SCHEDULED),
                expected_sequence=9,
                now=profile.next_growth_at,
                outcome=MaintenanceOutcome.NO_ACTION,
                expected_strength_budget_entries=(),
                strength_budget_entries_after=(),
                expected_last_strength_increase_at=profile.last_strength_increase_at,
                last_strength_increase_at_after=profile.last_strength_increase_at,
                next_growth_at_after=profile.next_growth_at + timedelta(hours=4),
                reason="domain_constraint",
            )

    profile.refresh_from_db()
    assert profile.maintenance_sequence == 0
    assert profile.next_growth_at == original_next_growth_at
