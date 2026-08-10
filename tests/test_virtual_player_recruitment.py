from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotMaintenanceCompletionEvent, BotProfile, Manor
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.archetype_pacing import resolve_archetype_pacing
from gameplay.services.virtual_player_core.config import load_virtual_player_config
from gameplay.services.virtual_player_core.maintenance_completion import reconcile_virtual_player_maintenance_completion
from gameplay.services.virtual_player_core.recruitment import (
    VIRTUAL_RECRUITMENT_POOL_PLAN,
    VirtualRecruitmentStatus,
    finalize_virtual_guest_recruitment,
    iter_virtual_recruitment_schedule,
    schedule_due_virtual_recruitments,
    start_virtual_recruitment,
)
from guests.models import Guest, GuestRecruitment, RecruitmentCandidate, RecruitmentPool, RecruitmentRecord
from guests.services.recruitment import finalize_guest_recruitment


def _create_v2_profile(django_user_model, *, username: str, silver: int = 1_000_000) -> BotProfile:
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    Manor.objects.filter(pk=manor.pk).update(silver=silver, grain=100_000)
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=991_000 + int(manor.id),
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


@pytest.mark.django_db
def test_virtual_recruitment_schedule_is_deterministic_and_balanced(django_user_model, load_guest_data):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_schedule")
    now = timezone.now()

    first = iter_virtual_recruitment_schedule(profile.id, now=now)
    second = iter_virtual_recruitment_schedule(profile.id, now=now)

    assert first == second
    assert len(first) == 9
    assert tuple(item.pool_key for item in first) == VIRTUAL_RECRUITMENT_POOL_PLAN
    assert {item.pool_key: sum(row.pool_key == item.pool_key for row in first) for item in first} == {
        "dianshi": 3,
        "xiangshi": 3,
        "cunmu": 3,
    }
    assert len({item.operation_id for item in first}) == 9


@pytest.mark.django_db
def test_virtual_recruitment_schedule_uses_archetype_pool_weights(django_user_model, load_guest_data):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_weighted")
    pacing = resolve_archetype_pacing(load_virtual_player_config(), "dojo")

    schedule = iter_virtual_recruitment_schedule(profile.id, now=timezone.now(), pacing=pacing)
    pool_keys = dict(pacing.recruitment_pool_weights)
    counts = {pool_key: sum(item.pool_key == pool_key for item in schedule) for pool_key in pool_keys}

    assert sum(counts.values()) == 9
    assert counts["xiangshi"] > counts["dianshi"]
    assert counts["cunmu"] >= counts["dianshi"]
    assert counts != {"dianshi": 3, "xiangshi": 3, "cunmu": 3}
    assert all(item.pool_quota == counts[item.pool_key] for item in schedule)


@pytest.mark.django_db
def test_virtual_recruitment_starts_with_snapshot_and_without_action_points(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_start")
    manor = profile.manor
    pool = RecruitmentPool.objects.get(key="cunmu")
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == pool.key
    )
    now = schedule.due_at + timedelta(seconds=1)
    manor.refresh_from_db()
    before_silver = manor.silver
    before_action_points = manor.action_points

    result = start_virtual_recruitment(schedule, now=now)

    assert result.status is VirtualRecruitmentStatus.STARTED
    recruitment = GuestRecruitment.objects.get(operation_id=schedule.operation_id)
    manor.refresh_from_db()
    assert recruitment.source == GuestRecruitment.Source.VIRTUAL
    assert recruitment.bot_profile_id == profile.id
    assert recruitment.quota_date == schedule.quota_date
    assert recruitment.quota_ordinal == schedule.quota_ordinal
    assert recruitment.pool_snapshot["snapshot_version"] == 1
    assert recruitment.pool_snapshot["rarity"]["distribution"]
    assert recruitment.pool_snapshot["pool"]["draw_count"] == pool.draw_count + manor.tavern_recruitment_bonus
    assert manor.silver == before_silver - int((recruitment.cost or {}).get("silver", 0))
    assert manor.action_points == before_action_points
    assert not RecruitmentCandidate.objects.filter(manor_id=manor.id).exists()


@pytest.mark.django_db
def test_virtual_recruitment_completion_is_idempotent_and_records_roster_only(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_complete")
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    start_result = start_virtual_recruitment(schedule, now=schedule.due_at + timedelta(seconds=1))
    recruitment = GuestRecruitment.objects.get(pk=start_result.recruitment_id)
    GuestRecruitment.objects.filter(pk=recruitment.pk).update(complete_at=timezone.now() - timedelta(seconds=1))
    recruitment.refresh_from_db()

    assert finalize_guest_recruitment(recruitment, now=timezone.now(), send_notification=True) is True
    first_guest_count = Guest.objects.filter(manor_id=profile.manor_id).count()
    first_record_count = RecruitmentRecord.objects.filter(manor_id=profile.manor_id).count()
    assert first_guest_count == 1
    assert first_record_count == 1
    assert not RecruitmentCandidate.objects.filter(manor_id=profile.manor_id).exists()
    assert recruitment.refresh_from_db() is None
    assert recruitment.status == GuestRecruitment.Status.COMPLETED
    assert recruitment.result_count == 1

    assert finalize_virtual_guest_recruitment(recruitment.id, now=timezone.now()) is False
    assert Guest.objects.filter(manor_id=profile.manor_id).count() == first_guest_count
    assert RecruitmentRecord.objects.filter(manor_id=profile.manor_id).count() == first_record_count


@pytest.mark.django_db
def test_virtual_recruitment_completion_event_is_recorded_by_existing_worker(
    django_user_model,
    load_guest_data,
):
    from guests.tasks import complete_guest_recruitment

    profile = _create_v2_profile(django_user_model, username="virtual_recruit_event")
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    start_result = start_virtual_recruitment(schedule, now=schedule.due_at + timedelta(seconds=1))
    recruitment = GuestRecruitment.objects.get(pk=start_result.recruitment_id)
    GuestRecruitment.objects.filter(pk=recruitment.pk).update(complete_at=timezone.now() - timedelta(seconds=1))

    assert complete_guest_recruitment.run(recruitment.id) == "completed"
    event = BotMaintenanceCompletionEvent.objects.get(
        profile_id=profile.id,
        domain_event_kind=BotMaintenanceCompletionEvent.DomainKind.GUEST_RECRUITMENT,
        domain_object_id=recruitment.id,
    )
    assert BotMaintenanceCompletionEvent.objects.filter(pk=event.pk).count() == 1
    reconcile_result = reconcile_virtual_player_maintenance_completion(event.id, now=timezone.now())
    assert reconcile_result["summary"]["cycle_id"] is None
    assert reconcile_result["summary"]["independent_domain_queue"] is True


@pytest.mark.django_db
def test_virtual_recruitment_defers_before_spending_when_runway_is_unsafe(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_runway", silver=1_000)
    manor = profile.manor
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    manor.refresh_from_db()
    before_silver = manor.silver

    result = start_virtual_recruitment(schedule, now=schedule.due_at + timedelta(seconds=1))

    manor.refresh_from_db()
    assert result.status is VirtualRecruitmentStatus.DEFERRED
    assert result.reason == "salary_runway_protected"
    assert manor.silver == before_silver
    assert not GuestRecruitment.objects.filter(bot_profile_id=profile.id).exists()


@pytest.mark.django_db
def test_virtual_recruitment_scanner_is_bounded_and_does_not_duplicate_pending_queue(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_scan")
    schedule_now = timezone.now() + timedelta(days=1)
    scan_now = iter_virtual_recruitment_schedule(profile.id, now=schedule_now)[0].due_at + timedelta(seconds=1)

    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 1
    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 0
    recruitment = GuestRecruitment.objects.get(bot_profile_id=profile.id)
    assert recruitment.source == GuestRecruitment.Source.VIRTUAL
    assert recruitment.status == GuestRecruitment.Status.PENDING


@pytest.mark.django_db
def test_virtual_recruitment_due_index_skips_future_head_and_reaches_later_due_profile(
    django_user_model,
    load_guest_data,
):
    head = _create_v2_profile(django_user_model, username="virtual_recruit_future_head")
    due = _create_v2_profile(django_user_model, username="virtual_recruit_later_due")
    schedule_now = timezone.now() + timedelta(days=1)
    scan_now = iter_virtual_recruitment_schedule(due.id, now=schedule_now)[0].due_at + timedelta(seconds=1)
    BotProfile.objects.filter(pk=head.pk).update(next_recruitment_at=scan_now + timedelta(days=1))
    BotProfile.objects.filter(pk=due.pk).update(next_recruitment_at=scan_now - timedelta(minutes=1))

    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 1
    assert GuestRecruitment.objects.filter(bot_profile_id=due.id, source=GuestRecruitment.Source.VIRTUAL).exists()
    assert not GuestRecruitment.objects.filter(bot_profile_id=head.id, source=GuestRecruitment.Source.VIRTUAL).exists()


@pytest.mark.django_db
def test_virtual_recruitment_due_index_prioritizes_known_due_rows_over_uninitialized_hints(
    django_user_model,
    load_guest_data,
):
    uninitialized = _create_v2_profile(django_user_model, username="virtual_recruit_uninitialized")
    due = _create_v2_profile(django_user_model, username="virtual_recruit_known_due")
    schedule_now = timezone.now() + timedelta(days=1)
    scan_now = iter_virtual_recruitment_schedule(due.id, now=schedule_now)[0].due_at + timedelta(seconds=1)
    BotProfile.objects.filter(pk=due.pk).update(next_recruitment_at=scan_now - timedelta(minutes=1))

    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 1
    assert GuestRecruitment.objects.filter(bot_profile_id=due.id, source=GuestRecruitment.Source.VIRTUAL).exists()
    assert not GuestRecruitment.objects.filter(
        bot_profile_id=uninitialized.id,
        source=GuestRecruitment.Source.VIRTUAL,
    ).exists()
