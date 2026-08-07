from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.tasks.virtual_players as virtual_player_tasks
from gameplay.models import BotProfile, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import maintenance, population_runtime
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.profile_management import enroll_virtual_players_batch

pytestmark = pytest.mark.django_db


def _create_profile(
    django_user_model,
    *,
    username: str,
    region: str,
    engine_version: int = 2,
) -> BotProfile:
    now = timezone.now()
    user = django_user_model(username=username, is_active=False)
    user.set_unusable_password()
    user._signup_region = region
    user._virtual_player_internal = True
    user.save()
    fields = {
        "manor": user.manor,
        "archetype": BotProfile.Archetype.BALANCED,
        "state": BotProfile.State.ACTIVE,
        "prestige_band": "newbie",
        "target_prestige_band": "newbie",
        "current_prestige_band": "newbie",
        "growth_seed": 880_000 + int(user.manor.id),
        "next_growth_at": now - timedelta(hours=1),
        "abandon_at": now + timedelta(days=30),
        "retire_at": now + timedelta(days=60),
        "engine_version": engine_version,
    }
    if engine_version == 2:
        fields.update(
            {
                "rng_version": 1,
                "plan_schema_version": 1,
                "policy_version": 1,
                "policy_checksum": "a" * 64,
                "last_strength_increase_at": now,
                "v2_enrolled_at": now,
            }
        )
    return BotProfile.objects.create(**fields)


def _set_v2_routing() -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )


@contextmanager
def _owned_population():
    yield lambda: None


def test_population_plan_manages_all_supported_regions(
    django_user_model,
) -> None:
    _set_v2_routing()
    _create_profile(
        django_user_model,
        username="population_planned_north",
        region="north",
    )
    _create_profile(
        django_user_model,
        username="population_planned_overseas",
        region="overseas",
    )

    plan = population_runtime.plan_virtual_player_population(now=timezone.now())

    assert plan["maintained_bots"] == 2
    assert plan["planned_bots"] == 2
    assert plan["unplanned_bots"] == 0


def test_v2_roll_keeps_profiles_in_all_supported_regions(
    django_user_model,
    monkeypatch,
    caplog,
) -> None:
    _set_v2_routing()
    unprotected = _create_profile(
        django_user_model,
        username="overseas_unprotected",
        region="overseas",
    )
    protected = _create_profile(
        django_user_model,
        username="overseas_protected",
        region="overseas",
    )
    monkeypatch.setattr(
        population_runtime,
        "_population_ownership",
        _owned_population,
    )
    monkeypatch.setattr(
        population_runtime,
        "_arena_protected_bot_manor_ids",
        lambda: {protected.manor_id},
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_periodic_population_cells",
        lambda: (),
    )
    caplog.set_level(logging.WARNING, logger=population_runtime.logger.name)

    processed = population_runtime.roll_virtual_player_population(
        limit=10,
        now=timezone.now(),
    )

    unprotected.refresh_from_db()
    protected.refresh_from_db()
    assert processed == 0
    assert unprotected.state == BotProfile.State.ACTIVE
    assert protected.state == BotProfile.State.ACTIVE
    assert not any(
        getattr(record, "event", None) == "virtual_player_unsupported_region_retired" for record in caplog.records
    )


@pytest.mark.parametrize("apply", [False, True])
def test_v2_enrollment_accepts_overseas_manor_region(
    django_user_model,
    apply: bool,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    release_configured_policy_operation(version=1, apply=True)
    profile = _create_profile(
        django_user_model,
        username=f"overseas_enrollment_{int(apply)}",
        region="overseas",
        engine_version=1,
    )

    summary = enroll_virtual_players_batch(batch_size=10, apply=apply)

    profile.refresh_from_db()
    assert summary.changed == 1
    assert summary.failed == 0
    assert profile.engine_version == (2 if apply else 1)


def test_scheduled_maintenance_logs_safety_preflight_rejection(
    django_user_model,
    caplog,
) -> None:
    _set_v2_routing()
    _create_profile(
        django_user_model,
        username="maintenance_preflight_logging",
        region="north",
    )
    routing = runtime_configs.read_virtual_player_routing()
    caplog.set_level(logging.WARNING, logger=maintenance.logger.name)

    maintained = maintenance._maintain_due_virtual_players_v2(
        current_time=timezone.now(),
        limit=10,
        routing=routing,
    )

    assert maintained == 0
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "virtual_player_v2_maintenance_batch_blocked"
    )
    assert record.reason == "safety_monitor_heartbeat_missing"
    assert record.due_profile_count == 1


def test_roll_task_logs_maintenance_and_population_counts(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(
        virtual_player_tasks,
        "maintain_due_virtual_players",
        lambda **_kwargs: 3,
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "roll_virtual_player_population",
        lambda **_kwargs: 2,
    )
    caplog.set_level(logging.INFO, logger=virtual_player_tasks.logger.name)

    result = virtual_player_tasks.roll_virtual_players_task.run(limit=7)

    assert result == 2
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_roll_completed"
    )
    assert record.maintained_count == 3
    assert record.population_processed_count == 2
