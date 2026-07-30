from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.db import DatabaseError
from django.db.models import QuerySet

import gameplay.tasks as gameplay_tasks
import gameplay.tasks.virtual_players as virtual_player_tasks
from gameplay.models import BotProfile, BotRuntimeRoutingState, JailPrisoner
from gameplay.services.jail import (
    VIRTUAL_JAIL_CLEANUP_MAX_BATCH_SIZE,
    VIRTUAL_JAIL_CLEANUP_MAX_BATCHES,
    VirtualJailCleanupError,
    cleanup_virtual_player_jail,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate

CUTOFF = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def _create_profile(
    django_user_model,
    *,
    username: str,
    engine_version: int = 1,
    state: str = BotProfile.State.ACTIVE,
) -> BotProfile:
    manor = ensure_manor(django_user_model.objects.create_user(username=username))
    fields = {
        "manor": manor,
        "archetype": BotProfile.Archetype.BALANCED,
        "state": state,
        "prestige_band": "newbie",
        "target_prestige_band": "newbie",
        "current_prestige_band": "newbie",
        "growth_seed": 271_828 + manor.id,
        "next_growth_at": CUTOFF + timedelta(hours=1),
        "abandon_at": CUTOFF + timedelta(days=30),
        "retire_at": CUTOFF + timedelta(days=60),
        "engine_version": engine_version,
    }
    if engine_version == 2:
        fields.update(
            {
                "rng_version": 1,
                "plan_schema_version": 1,
                "policy_version": 1,
                "policy_checksum": "a" * 64,
                "last_strength_increase_at": CUTOFF - timedelta(days=1),
                "v2_enrolled_at": CUTOFF - timedelta(days=1),
            }
        )
    return BotProfile.objects.create(**fields)


def _create_manor(django_user_model, *, username: str):
    return ensure_manor(django_user_model.objects.create_user(username=username))


def _create_template(*, key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=key,
        rarity="green",
        archetype="military",
        base_attack=100,
        base_intellect=80,
    )


def _create_prisoner(
    *,
    captor,
    original_manor,
    template: GuestTemplate,
    captured_at: datetime,
    name: str,
    status: str = JailPrisoner.Status.HELD,
) -> JailPrisoner:
    prisoner = JailPrisoner.objects.create(
        captor=captor,
        original_manor=original_manor,
        guest_template=template,
        original_guest_name=name,
        original_level=17,
        loyalty=63,
        captured_loyalty=63,
        status=status,
    )
    JailPrisoner.objects.filter(pk=prisoner.pk).update(captured_at=captured_at)
    prisoner.captured_at = captured_at
    return prisoner


@pytest.mark.django_db
def test_cleanup_uses_stable_bounded_batches_and_replays_idempotently(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="virtual_jail_stable_bot",
    )
    original = _create_manor(
        django_user_model,
        username="virtual_jail_stable_original",
    )
    template = _create_template(key="virtual_jail_stable_template")
    prisoners = [
        _create_prisoner(
            captor=profile.manor,
            original_manor=original,
            template=template,
            captured_at=CUTOFF - timedelta(hours=3),
            name="oldest",
        ),
        _create_prisoner(
            captor=profile.manor,
            original_manor=original,
            template=template,
            captured_at=CUTOFF - timedelta(hours=2),
            name="same-time-first",
        ),
        _create_prisoner(
            captor=profile.manor,
            original_manor=original,
            template=template,
            captured_at=CUTOFF - timedelta(hours=2),
            name="same-time-second",
        ),
        _create_prisoner(
            captor=profile.manor,
            original_manor=original,
            template=template,
            captured_at=CUTOFF - timedelta(hours=1),
            name="newest",
        ),
    ]

    first = cleanup_virtual_player_jail(
        cutoff=CUTOFF,
        batch_size=2,
        max_batches=1,
    )

    statuses = dict(JailPrisoner.objects.values_list("id", "status"))
    assert statuses[prisoners[0].id] == JailPrisoner.Status.RELEASED
    assert statuses[prisoners[1].id] == JailPrisoner.Status.RELEASED
    assert statuses[prisoners[2].id] == JailPrisoner.Status.HELD
    assert statuses[prisoners[3].id] == JailPrisoner.Status.HELD
    assert first.batch_count == 1
    assert first.scanned == first.locked == first.released == 2
    assert first.skipped == first.failed == 0
    assert first.oldest_remaining_age_seconds == 2 * 60 * 60
    assert first.batch_limit_reached is True

    second = cleanup_virtual_player_jail(
        cutoff=CUTOFF,
        batch_size=2,
        max_batches=1,
    )
    replay = cleanup_virtual_player_jail(
        cutoff=CUTOFF,
        batch_size=2,
        max_batches=1,
    )

    assert second.released == 2
    assert second.oldest_remaining_age_seconds is None
    assert second.batch_limit_reached is False
    assert replay.batch_count == 0
    assert replay.scanned == replay.locked == replay.released == 0
    assert replay.skipped == replay.failed == 0
    assert replay.oldest_remaining_age_seconds is None
    assert JailPrisoner.objects.count() == 4


@pytest.mark.django_db
@pytest.mark.parametrize(
    "maintenance_mode",
    [
        BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
        BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
    ],
)
def test_cleanup_is_routing_independent_and_preserves_all_boundaries(
    django_user_model,
    maintenance_mode: str,
) -> None:
    BotRuntimeRoutingState.objects.create(maintenance_mode=maintenance_mode)
    v1_profile = _create_profile(
        django_user_model,
        username=f"virtual_jail_v1_{maintenance_mode}",
    )
    v2_profile = _create_profile(
        django_user_model,
        username=f"virtual_jail_v2_{maintenance_mode}",
        engine_version=2,
        state=BotProfile.State.RETIRED,
    )
    real_manor = _create_manor(
        django_user_model,
        username=f"virtual_jail_real_{maintenance_mode}",
    )
    original = _create_manor(
        django_user_model,
        username=f"virtual_jail_original_{maintenance_mode}",
    )
    template = _create_template(key=f"virtual_jail_boundary_{maintenance_mode}")

    v1_eligible = _create_prisoner(
        captor=v1_profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(seconds=1),
        name="v1-eligible",
    )
    v2_eligible = _create_prisoner(
        captor=v2_profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF,
        name="v2-eligible",
    )
    post_cutoff = _create_prisoner(
        captor=v2_profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF + timedelta(microseconds=1),
        name="post-cutoff",
    )
    real_held = _create_prisoner(
        captor=real_manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(days=5),
        name="real-held",
    )
    recruited = _create_prisoner(
        captor=v1_profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(days=5),
        name="already-recruited",
        status=JailPrisoner.Status.RECRUITED,
    )
    released = _create_prisoner(
        captor=v1_profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(days=5),
        name="already-released",
        status=JailPrisoner.Status.RELEASED,
    )
    prisoner_count = JailPrisoner.objects.count()
    guest_count = Guest.objects.count()

    result = cleanup_virtual_player_jail(cutoff=CUTOFF, batch_size=1)

    for prisoner in (v1_eligible, v2_eligible):
        prisoner.refresh_from_db()
        assert prisoner.status == JailPrisoner.Status.RELEASED
        assert prisoner.original_level == 17
        assert prisoner.loyalty == 63
        assert prisoner.original_guest_name in {"v1-eligible", "v2-eligible"}
    for prisoner, expected_status in (
        (post_cutoff, JailPrisoner.Status.HELD),
        (real_held, JailPrisoner.Status.HELD),
        (recruited, JailPrisoner.Status.RECRUITED),
        (released, JailPrisoner.Status.RELEASED),
    ):
        prisoner.refresh_from_db()
        assert prisoner.status == expected_status

    assert result.released == 2
    assert result.skipped == result.failed == 0
    assert result.oldest_remaining_age_seconds is None
    assert JailPrisoner.objects.count() == prisoner_count
    assert Guest.objects.count() == guest_count


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"cutoff": datetime(2026, 7, 29)}, "cutoff"),
        ({"cutoff": CUTOFF, "batch_size": 0}, "batch_size"),
        ({"cutoff": CUTOFF, "batch_size": True}, "batch_size"),
        (
            {"cutoff": CUTOFF, "batch_size": VIRTUAL_JAIL_CLEANUP_MAX_BATCH_SIZE + 1},
            "batch_size",
        ),
        ({"cutoff": CUTOFF, "max_batches": 0}, "max_batches"),
        (
            {"cutoff": CUTOFF, "max_batches": VIRTUAL_JAIL_CLEANUP_MAX_BATCHES + 1},
            "max_batches",
        ),
    ],
)
def test_cleanup_rejects_unbounded_or_ambiguous_inputs(kwargs, match: str) -> None:
    with pytest.raises(VirtualJailCleanupError, match=match):
        cleanup_virtual_player_jail(**kwargs)


@pytest.mark.django_db
def test_cleanup_database_failure_rolls_back_and_is_not_returned_as_success(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="virtual_jail_failure_bot",
    )
    original = _create_manor(
        django_user_model,
        username="virtual_jail_failure_original",
    )
    template = _create_template(key="virtual_jail_failure_template")
    prisoner = _create_prisoner(
        captor=profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(days=2),
        name="committed-first-batch",
    )
    retry_prisoner = _create_prisoner(
        captor=profile.manor,
        original_manor=original,
        template=template,
        captured_at=CUTOFF - timedelta(days=1),
        name="must-retry",
    )
    original_update = QuerySet.update
    release_attempts = 0

    def _raising_update(queryset, **kwargs):
        nonlocal release_attempts
        if queryset.model is JailPrisoner and kwargs.get("status") == JailPrisoner.Status.RELEASED:
            release_attempts += 1
            updated = original_update(queryset, **kwargs)
            if release_attempts == 2:
                raise DatabaseError("forced virtual jail cleanup failure")
            return updated
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", _raising_update)

    with pytest.raises(DatabaseError, match="forced virtual jail cleanup failure"):
        cleanup_virtual_player_jail(cutoff=CUTOFF, batch_size=1)

    prisoner.refresh_from_db()
    retry_prisoner.refresh_from_db()
    assert prisoner.status == JailPrisoner.Status.RELEASED
    assert retry_prisoner.status == JailPrisoner.Status.HELD


def test_cleanup_transport_freezes_cutoff_and_serializes_summary(monkeypatch) -> None:
    observed: dict[str, object] = {}
    payload = {
        "cutoff": CUTOFF.isoformat(),
        "batch_size": 17,
        "batch_count": 2,
        "scanned": 20,
        "locked": 20,
        "released": 19,
        "skipped": 1,
        "failed": 0,
        "oldest_remaining_age_seconds": 7200,
        "batch_limit_reached": False,
    }

    def _cleanup(*, cutoff: datetime, batch_size: int):
        observed.update(cutoff=cutoff, batch_size=batch_size)
        return SimpleNamespace(to_payload=lambda: payload)

    monkeypatch.setattr(
        virtual_player_tasks,
        "cleanup_virtual_player_jail",
        _cleanup,
    )

    result = virtual_player_tasks.cleanup_virtual_player_jail_task.run(
        cutoff="2026-07-29T00:00:00Z",
        batch_size=17,
    )

    assert observed == {"cutoff": CUTOFF, "batch_size": 17}
    assert result == payload


def test_cleanup_transport_captures_default_cutoff_once(monkeypatch) -> None:
    cutoff_calls = 0
    observed: list[datetime] = []

    def _now() -> datetime:
        nonlocal cutoff_calls
        cutoff_calls += 1
        return CUTOFF

    monkeypatch.setattr(virtual_player_tasks.timezone, "now", _now)
    monkeypatch.setattr(
        virtual_player_tasks,
        "cleanup_virtual_player_jail",
        lambda *, cutoff, batch_size: observed.append(cutoff)
        or SimpleNamespace(
            to_payload=lambda: {
                "cutoff": cutoff.isoformat(),
                "released": 0,
                "skipped": 0,
                "failed": 0,
                "oldest_remaining_age_seconds": None,
            }
        ),
    )

    virtual_player_tasks.cleanup_virtual_player_jail_task.run(batch_size=9)

    assert cutoff_calls == 1
    assert observed == [CUTOFF]


def test_cleanup_task_is_exported_routed_and_scheduled_once_daily() -> None:
    task = virtual_player_tasks.cleanup_virtual_player_jail_task
    assert gameplay_tasks.cleanup_virtual_player_jail_task is task
    assert task.name == "gameplay.cleanup_virtual_player_jail"
    assert settings.CELERY_TASK_ROUTES[task.name] == {"queue": settings.CELERY_TIMER_QUEUE}

    matching_entries = [entry for entry in settings.CELERY_BEAT_SCHEDULE.values() if entry["task"] == task.name]
    assert len(matching_entries) == 1
    schedule = matching_entries[0]["schedule"]
    assert schedule._orig_hour == 0
    assert schedule._orig_minute == 20
