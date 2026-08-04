from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import BotProfile
from gameplay.services.manor.core import ensure_manor
from gameplay.services.runtime_configs import BootstrapMode, MaintenanceMode, RuntimeRoutingSnapshot
from gameplay.services.virtual_player_core.profile_store import (
    lock_maintained_profile,
    mark_profiles_stale,
    record_arena_participation,
)


@pytest.mark.django_db
def test_record_arena_participation_updates_only_requested_profiles(
    django_user_model,
) -> None:
    now = timezone.now()
    profiles = []
    for index in range(3):
        manor = ensure_manor(
            django_user_model.objects.create_user(
                username=f"profile_store_arena_{index}",
                password="pass123",
            )
        )
        profiles.append(
            BotProfile.objects.create(
                manor=manor,
                archetype=BotProfile.Archetype.BALANCED,
                state=BotProfile.State.ACTIVE,
                prestige_band="newbie",
                growth_seed=71_000 + index,
                next_growth_at=now,
                abandon_at=now + timedelta(days=30),
                retire_at=now + timedelta(days=60),
            )
        )

    updated = record_arena_participation(
        [profiles[1].id, profiles[0].id, profiles[1].id],
        participated_at=now,
    )

    assert updated == 2
    for profile in profiles:
        profile.refresh_from_db()
    assert profiles[0].last_arena_participated_at == now
    assert profiles[0].arena_participation_count == 1
    assert profiles[1].last_arena_participated_at == now
    assert profiles[1].arena_participation_count == 1
    assert profiles[2].last_arena_participated_at is None
    assert profiles[2].arena_participation_count == 0


@pytest.mark.django_db
def test_record_arena_participation_accepts_an_empty_profile_set() -> None:
    assert record_arena_participation([], participated_at=timezone.now()) == 0


@pytest.mark.django_db(transaction=True)
def test_lock_maintained_profile_rejects_unpersisted_routing_guard(
    django_user_model,
) -> None:
    now = timezone.now()
    manor = ensure_manor(
        django_user_model.objects.create_user(
            username="profile_store_unpersisted_routing",
            password="pass123",
        )
    )
    profile = BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        growth_seed=71_100,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )
    snapshot = RuntimeRoutingSnapshot(
        bootstrap_mode=BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=(),
        revision=None,
        last_hourly_safety_window_end_at=None,
        last_daily_safety_window_end_at=None,
        last_pause_window_id="",
        pause_reason="",
        paused_from_maintenance_mode="",
        persisted=False,
    )

    with transaction.atomic():
        locked = lock_maintained_profile(
            int(profile.id),
            expected_v2_routing=snapshot,
        )

    assert locked is not None
    assert locked.maintenance_routing_matches is False


@pytest.mark.django_db
def test_mark_profiles_stale_updates_each_non_stale_profile_once(
    django_user_model,
) -> None:
    now = timezone.now()
    profiles = []
    for index, state in enumerate((BotProfile.State.ACTIVE, BotProfile.State.STALE, BotProfile.State.RETIRED)):
        manor = ensure_manor(
            django_user_model.objects.create_user(
                username=f"profile_store_stale_{index}",
                password="pass123",
            )
        )
        profiles.append(
            BotProfile.objects.create(
                manor=manor,
                archetype=BotProfile.Archetype.BALANCED,
                state=state,
                prestige_band="newbie",
                growth_seed=72_000 + index,
                next_growth_at=now + timedelta(hours=1),
                abandon_at=now + timedelta(days=30),
                retire_at=now + timedelta(days=60),
            )
        )

    assert mark_profiles_stale([], now=now) == 0
    assert (
        mark_profiles_stale(
            [profiles[0].id, profiles[0].id, profiles[1].id, profiles[2].id],
            now=now,
        )
        == 2
    )

    for profile in profiles:
        profile.refresh_from_db()
    assert all(profile.state == BotProfile.State.STALE for profile in profiles)
    assert profiles[0].next_growth_at == now
    assert profiles[0].maintenance_stopped_at == now
    assert profiles[1].next_growth_at != now
    assert profiles[1].maintenance_stopped_at is None
    assert profiles[2].next_growth_at == now
    assert profiles[2].maintenance_stopped_at == now
