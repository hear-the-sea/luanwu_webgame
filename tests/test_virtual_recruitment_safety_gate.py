from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import BotProfile, BotRuntimeRoutingState, BotSafetyMetricEvent, Manor, ResourceEvent
from gameplay.services.virtual_player_core import recruitment
from guests.models import Guest, GuestRecruitment, RecruitmentPool, RecruitmentRecord
from guests.services.recruitment_guests import create_guest_from_template
from guests.services.training import ensure_auto_training, ensure_auto_training_batch, finalize_guest_training
from tests.test_virtual_player_recruitment import _create_v2_profile, _seed_healthy_recruitment_runtime

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(django_user_model, load_guest_data):
    profile = _create_v2_profile(django_user_model, username="recruit_safety")
    schedule = recruitment.iter_virtual_recruitment_schedule(profile.id, now=timezone.now())[0]
    now = schedule.due_at + timedelta(seconds=1)
    _seed_healthy_recruitment_runtime(now)
    return profile, schedule, now


def _pause():
    BotRuntimeRoutingState.objects.update(maintenance_mode="v2_paused", revision=1)


def _assert_no_writes(captured):
    assert not any(
        row["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "REPLACE")) for row in captured
    )


def _assert_no_new_actions(profile):
    profile.refresh_from_db()
    assert profile.next_recruitment_at is None
    assert not profile.recruitment_schedule_snapshot
    assert not GuestRecruitment.objects.filter(bot_profile=profile).exists()
    assert not Guest.objects.filter(manor_id=profile.manor_id).exists()


@pytest.mark.parametrize(
    "field,mode",
    [
        ("maintenance_mode", "v2_paused"),
        ("maintenance_mode", "v2_cutover"),
        ("maintenance_mode", "legacy_before_gate"),
        ("bootstrap_mode", "v2_paused"),
        ("bootstrap_mode", "legacy_before_gate"),
    ],
)
def test_inactive_scheduler_stops_before_profile_scan_and_hint_writes(world, monkeypatch, field, mode):
    profile, _, now = world
    BotRuntimeRoutingState.objects.update(**{field: mode})

    def unexpected_scan(**kwargs):
        pytest.fail("blocked scheduler must not enumerate profiles")

    monkeypatch.setattr(recruitment, "_eligible_profile_queryset", unexpected_scan)
    with CaptureQueriesContext(connection) as captured:
        assert recruitment.schedule_due_virtual_recruitments(now=now) == 0
    _assert_no_writes(captured)
    _assert_no_new_actions(profile)


@pytest.mark.parametrize("entry", ["schedule", "next", "batch"])
def test_direct_call_cannot_bypass_pause(world, entry):
    profile, schedule, now = world
    _pause()
    with CaptureQueriesContext(connection) as captured:
        if entry == "schedule":
            result = recruitment.start_virtual_recruitment(schedule, now=now)
        elif entry == "next":
            result = recruitment.start_next_due_virtual_recruitment(profile.id, now=now)
        else:
            result = recruitment._start_virtual_recruitment_batch_locked(
                profile_id=profile.id, schedules=(schedule,), now=now, settle_future_slots=True
            )
    assert result.status is recruitment.VirtualRecruitmentStatus.SAFETY_BLOCKED
    _assert_no_writes(captured)
    _assert_no_new_actions(profile)


@pytest.mark.parametrize(
    "fault", ["missing", "invalid_mode", "invalid_routes", "unreadable", "stale", "future", "bad_value", "no_heartbeat"]
)
def test_routing_and_monitor_uncertainty_fail_closed(world, monkeypatch, fault):
    profile, schedule, now = world
    if fault == "missing":
        BotRuntimeRoutingState.objects.all().delete()
    elif fault == "invalid_mode":

        def invalid_routing():
            raise recruitment.RuntimeRoutingError("unknown mode")

        monkeypatch.setattr(recruitment, "read_virtual_player_routing", invalid_routing)
        monkeypatch.setattr(recruitment, "lock_virtual_player_routing", invalid_routing)
    elif fault == "invalid_routes":
        BotRuntimeRoutingState.objects.update(calibration_routes={"invalid": True})
    elif fault == "unreadable":

        def fail_read():
            raise DatabaseError("routing cannot be read")

        monkeypatch.setattr(recruitment, "read_virtual_player_routing", fail_read)
        monkeypatch.setattr(recruitment, "lock_virtual_player_routing", fail_read)
    elif fault in {"stale", "future"}:
        BotSafetyMetricEvent.objects.all().delete()
        _seed_healthy_recruitment_runtime(now + timedelta(minutes=-5 if fault == "stale" else 5))
    elif fault == "bad_value":
        BotSafetyMetricEvent.objects.update(value=0)
    else:
        BotSafetyMetricEvent.objects.all().delete()

    with CaptureQueriesContext(connection) as captured:
        assert recruitment.schedule_due_virtual_recruitments(now=now) == 0
        result = recruitment._start_virtual_recruitment_batch_locked(
            profile_id=profile.id, schedules=(schedule,), now=now, settle_future_slots=True
        )
    assert result.status is recruitment.VirtualRecruitmentStatus.SAFETY_BLOCKED
    _assert_no_writes(captured)
    _assert_no_new_actions(profile)


def test_pause_between_scheduler_preflight_and_commit_blocks_batch_and_hint(world, monkeypatch):
    profile, _, now = world
    original = recruitment._next_daily_batch_schedule

    def pause_after_schedule(*args, **kwargs):
        schedules = original(*args, **kwargs)
        _pause()
        return schedules

    monkeypatch.setattr(recruitment, "_next_daily_batch_schedule", pause_after_schedule)
    assert recruitment.schedule_due_virtual_recruitments(now=now) == 0
    _assert_no_new_actions(profile)


def test_due_hint_rechecks_pause_before_profile_lock(world, monkeypatch):
    profile, _, now = world
    _pause()
    pacing = recruitment.resolve_archetype_pacing(recruitment.load_virtual_player_config(), profile.archetype)
    with CaptureQueriesContext(connection) as captured:
        recruitment._refresh_recruitment_due_hint(profile.id, now=now, pacing=pacing)
    _assert_no_writes(captured)
    _assert_no_new_actions(profile)


def test_healthy_scheduler_recruits_spends_and_starts_training_with_routing_first(world, monkeypatch):
    profile, _, now = world
    manor = Manor.objects.get(pk=profile.manor_id)
    before_silver = manor.silver
    locks = []
    original = QuerySet.select_for_update

    def record_lock(queryset, *args, **kwargs):
        locks.append(queryset.model)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", record_lock)
    assert recruitment.schedule_due_virtual_recruitments(now=now, limit=1) == 1
    assert locks.index(BotRuntimeRoutingState) < locks.index(BotProfile) < locks.index(Manor)
    manor.refresh_from_db()
    assert manor.silver < before_silver
    assert GuestRecruitment.objects.filter(bot_profile=profile).count() == 6
    assert Guest.objects.filter(manor=manor, training_complete_at__isnull=False).exists()


def _pending_recruitment(profile, schedule, now):
    pool = RecruitmentPool.objects.get(key=schedule.pool_key)
    return GuestRecruitment.objects.create(
        manor_id=profile.manor_id,
        bot_profile=profile,
        pool=pool,
        source=GuestRecruitment.Source.VIRTUAL,
        operation_id=schedule.operation_id,
        quota_date=schedule.quota_date,
        quota_ordinal=schedule.quota_ordinal,
        pool_snapshot=recruitment._build_pool_snapshot(pool=pool, manor=profile.manor, captured_at=now),
        cost={"silver": 100},
        draw_count=1,
        duration_seconds=60,
        seed=123,
        complete_at=now,
    )


@pytest.mark.parametrize("state", ["paused", "missing", "unreadable", "no_heartbeat", "healthy"])
def test_existing_completion_settles_but_new_training_requires_permission(world, monkeypatch, state):
    profile, schedule, now = world
    pending = _pending_recruitment(profile, schedule, now)
    if state == "paused":
        _pause()
    elif state == "missing":
        BotRuntimeRoutingState.objects.all().delete()
    elif state == "unreadable":

        def fail_read():
            raise DatabaseError("routing unavailable")

        monkeypatch.setattr(recruitment, "lock_virtual_player_routing", fail_read)
    elif state == "no_heartbeat":
        BotSafetyMetricEvent.objects.all().delete()
    before_silver = Manor.objects.get(pk=profile.manor_id).silver
    before_resources = ResourceEvent.objects.count()
    assert recruitment.finalize_virtual_guest_recruitment(pending.id, now=now) is True
    pending.refresh_from_db()
    assert pending.status == GuestRecruitment.Status.COMPLETED
    assert pending.result_count == 1
    guest = RecruitmentRecord.objects.get(manor_id=profile.manor_id).guest
    assert (guest.training_complete_at is not None) is (state == "healthy")
    assert Manor.objects.get(pk=profile.manor_id).silver == before_silver
    assert ResourceEvent.objects.count() == before_resources
    assert recruitment.finalize_virtual_guest_recruitment(pending.id, now=now) is False


def test_global_pause_does_not_gate_generic_human_training(world, django_user_model):
    _, _, now = world
    _pause()
    from gameplay.services.manor.core import ensure_manor
    from guests.models import GuestTemplate

    manor = ensure_manor(django_user_model.objects.create_user(username="human_training"))
    guest = create_guest_from_template(manor=manor, template=GuestTemplate.objects.filter(recruitable=True).first())
    assert ensure_auto_training(guest, now=now) is True
    guest.refresh_from_db()
    assert guest.training_complete_at is not None


def test_existing_virtual_training_finishes_without_starting_another_round(world):
    profile, schedule, now = world
    from guests.models import GuestTemplate

    guest = create_guest_from_template(
        manor=profile.manor, template=GuestTemplate.objects.filter(recruitable=True).first()
    )
    assert ensure_auto_training(guest, now=now)
    previous_level = guest.level
    completed_at = guest.training_complete_at
    _pause()
    assert finalize_guest_training(guest, now=completed_at)
    guest.refresh_from_db()
    assert guest.level == previous_level + 1
    assert guest.training_complete_at is None


def test_mixed_human_virtual_timer_batch_only_fences_virtual_rows(world, django_user_model):
    profile, _, now = world
    from gameplay.services.manor.core import ensure_manor
    from guests.models import GuestTemplate

    template = GuestTemplate.objects.filter(recruitable=True).first()
    human_manor = ensure_manor(django_user_model.objects.create_user(username="mixed_training_human"))
    human = create_guest_from_template(manor=human_manor, template=template)
    virtual = create_guest_from_template(manor=profile.manor, template=template)
    _pause()
    assert ensure_auto_training_batch([human, virtual], now=now) == 1
    assert human.training_complete_at is not None
    assert virtual.training_complete_at is None


def test_timer_update_fences_routing_revision_changed_after_permission_read(world, monkeypatch):
    profile, _, now = world
    from gameplay.services.virtual_player_core import training_gate
    from guests.models import GuestTemplate

    guest = create_guest_from_template(
        manor=profile.manor, template=GuestTemplate.objects.filter(recruitable=True).first()
    )
    original = training_gate.virtual_auto_training_write_guard

    def pause_after_guard(**kwargs):
        expression = original(**kwargs)
        _pause()
        return expression

    monkeypatch.setattr(training_gate, "virtual_auto_training_write_guard", pause_after_guard)
    assert not ensure_auto_training(guest, now=now)
    guest.refresh_from_db()
    assert guest.training_complete_at is None
