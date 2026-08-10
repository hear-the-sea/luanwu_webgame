from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotMaintenanceAttempt, BotProfile
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.business_metrics import (
    maintenance_business_metrics_queryset,
    query_maintenance_business_metrics,
)
from gameplay.services.virtual_player_core.maintenance_cycle import CycleTrigger, record_durable_attempt


@pytest.mark.django_db
def test_maintenance_attempt_persists_queryable_type_cost_and_reason_dimensions(django_user_model) -> None:
    user = django_user_model.objects.create_user(username="business_metrics_user", password="pass123")
    manor = ensure_manor(user)
    now = timezone.now()
    active_v2_profile = BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=991_001,
        growth_stage=1,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=2,
        policy_checksum="b" * 64,
        last_strength_increase_at=now,
        v2_enrolled_at=now,
    )
    started_at = timezone.now() - timedelta(seconds=4)
    record_durable_attempt(
        active_v2_profile,
        operation_id="business-metric-applied",
        trigger=CycleTrigger.SCHEDULED,
        attempt_ordinal=1,
        outcome=BotMaintenanceAttempt.Outcome.APPLIED,
        action_kind="training",
        shadow_cost={"resource_costs": {"silver": 120, "grain": 30}},
        salary_runway_days=3,
        salary_runway_silver=900,
        started_at=started_at,
    )
    record_durable_attempt(
        active_v2_profile,
        operation_id="business-metric-no-action",
        trigger=CycleTrigger.SCHEDULED,
        attempt_ordinal=1,
        outcome=BotMaintenanceAttempt.Outcome.NO_ACTION,
        reason="insufficient_resource",
        shadow_cost={"salary_runway_days": 3, "salary_runway_silver": 900},
        salary_runway_days=3,
        salary_runway_silver=900,
        started_at=started_at,
    )
    record_durable_attempt(
        active_v2_profile,
        operation_id="business-metric-arena-no-action",
        trigger=CycleTrigger.ARENA_ACCELERATION,
        attempt_ordinal=1,
        outcome=BotMaintenanceAttempt.Outcome.NO_ACTION,
        action_kind="arena_growth",
        reason="arena_ineligible",
        shadow_cost={"salary_runway_days": 2, "salary_runway_silver": 700},
        salary_runway_days=2,
        salary_runway_silver=700,
        started_at=started_at,
    )

    applied = BotMaintenanceAttempt.objects.get(operation_id="business-metric-applied")
    blocked = BotMaintenanceAttempt.objects.get(operation_id="business-metric-no-action")
    assert applied.archetype == active_v2_profile.archetype
    assert applied.action_kind == "training"
    assert applied.silver_cost == 120
    assert applied.grain_cost == 30
    assert applied.reason_category == ""
    assert blocked.reason_category == "resource"

    rows = query_maintenance_business_metrics(archetypes=["balanced"])
    by_key = {(row.action_kind, row.outcome, row.reason_category): row for row in rows}
    assert by_key[("training", "applied", "")].silver_cost == 120
    assert by_key[("training", "applied", "")].grain_cost == 30
    assert by_key[("no_action", "no_action", "resource")].attempt_count == 1

    arena_rows = query_maintenance_business_metrics(triggers=[CycleTrigger.ARENA_ACCELERATION])
    assert len(arena_rows) == 1
    assert arena_rows[0].trigger == CycleTrigger.ARENA_ACCELERATION
    assert arena_rows[0].salary_runway_days_min == 2
    assert arena_rows[0].salary_runway_days_avg == 2.0

    grouped_query = maintenance_business_metrics_queryset(archetypes=["balanced"])
    assert "GROUP BY" in str(grouped_query.query).upper()
