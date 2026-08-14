from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotProfile, Manor
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.archetype_pacing import resolve_archetype_pacing
from gameplay.services.virtual_player_core.config import load_virtual_player_config
from gameplay.services.virtual_player_core.recruitment import (
    VIRTUAL_RECRUITMENT_LOCKED_POOL_PLAN,
    VIRTUAL_RECRUITMENT_POOL_PLAN,
    VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    VirtualRecruitmentStatus,
    finalize_virtual_guest_recruitment,
    iter_virtual_recruitment_schedule,
    load_virtual_recruitment_pool_silver_costs,
    schedule_due_virtual_recruitments,
    start_virtual_recruitment,
    virtual_recruitment_daily_silver_cost,
)
from guests.models import (
    Guest,
    GuestRecruitment,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPool,
    RecruitmentRecord,
)
from guests.services.recruitment_guests import create_guest_from_template


def _create_v2_profile(
    django_user_model,
    *,
    username: str,
    silver: int = 1_000_000,
    prestige: int = 0,
) -> BotProfile:
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    Manor.objects.filter(pk=manor.pk).update(silver=silver, grain=100_000, prestige=prestige)
    now = timezone.localtime(timezone.now()).replace(hour=10, minute=0, second=0, microsecond=0)
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
def test_virtual_recruitment_cashflow_forecast_reuses_real_pool_prices(load_guest_data):
    costs = dict(load_virtual_recruitment_pool_silver_costs())

    assert (
        virtual_recruitment_daily_silver_cost(
            prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD - 1,
            pool_silver_costs=costs,
        )
        == 3 * costs["xiangshi"] + 3 * costs["cunmu"]
    )
    assert (
        virtual_recruitment_daily_silver_cost(
            prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
            pool_silver_costs=costs,
        )
        == 3 * costs["dianshi"] + 3 * costs["xiangshi"] + 3 * costs["cunmu"]
    )


@pytest.mark.django_db
def test_virtual_recruitment_schedule_is_deterministic_and_balanced(django_user_model, load_guest_data):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_schedule")
    now = timezone.localtime(timezone.now()).replace(hour=10, minute=0, second=0, microsecond=0)

    first = iter_virtual_recruitment_schedule(profile.id, now=now)
    second = iter_virtual_recruitment_schedule(profile.id, now=now)

    assert first == second
    assert len(first) == 6
    assert tuple(item.pool_key for item in first) == VIRTUAL_RECRUITMENT_LOCKED_POOL_PLAN
    assert {
        pool_key: sum(row.pool_key == pool_key for row in first) for pool_key in ("dianshi", "xiangshi", "cunmu")
    } == {
        "dianshi": 0,
        "xiangshi": 3,
        "cunmu": 3,
    }
    assert len({item.operation_id for item in first}) == 6


@pytest.mark.django_db
def test_virtual_recruitment_restores_dianshi_at_prestige_threshold(django_user_model, load_guest_data):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_threshold",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    )

    schedule = iter_virtual_recruitment_schedule(profile.id, now=timezone.now())

    assert len(schedule) == 9
    assert tuple(item.pool_key for item in schedule) == VIRTUAL_RECRUITMENT_POOL_PLAN
    assert all(item.dianshi_unlocked for item in schedule)


@pytest.mark.django_db
def test_virtual_recruitment_daily_snapshot_does_not_add_dianshi_midday(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_snapshot_boundary",
        prestige=0,
    )
    now = timezone.localtime(timezone.now()).replace(hour=10, minute=0, second=0, microsecond=0)

    first = iter_virtual_recruitment_schedule(profile.id, now=now)
    assert len(first) == 6

    settled = start_virtual_recruitment(first[0], now=first[0].due_at + timedelta(seconds=1))
    assert settled.status is VirtualRecruitmentStatus.STARTED

    Manor.objects.filter(pk=profile.manor_id).update(prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD)
    same_day = iter_virtual_recruitment_schedule(profile.id, now=now + timedelta(hours=1))
    assert len(same_day) == 6
    assert not any(item.pool_key == "dianshi" for item in same_day)

    next_day = iter_virtual_recruitment_schedule(profile.id, now=now + timedelta(days=1))
    assert len(next_day) == 9
    assert sum(item.pool_key == "dianshi" for item in next_day) == 3


@pytest.mark.django_db
def test_virtual_recruitment_low_prestige_batch_has_no_dianshi_records(django_user_model, load_guest_data):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_no_dianshi_below_target",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD - 1,
    )
    schedule = iter_virtual_recruitment_schedule(profile.id, now=timezone.now())
    assert len(schedule) == 6
    assert all(item.pool_key != "dianshi" for item in schedule)

    result = start_virtual_recruitment(schedule[0], now=schedule[0].due_at + timedelta(seconds=1))

    assert result.status is VirtualRecruitmentStatus.STARTED
    recruitments = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
        quota_date=schedule[0].quota_date,
    )
    assert recruitments.count() == 6
    assert not recruitments.filter(pool__key="dianshi").exists()
    assert {row.pool.key for row in recruitments} == {"xiangshi", "cunmu"}


@pytest.mark.django_db
def test_virtual_recruitment_schedule_uses_archetype_pool_weights(django_user_model, load_guest_data):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_weighted",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    )
    pacing = resolve_archetype_pacing(load_virtual_player_config(), "dojo")

    schedule = iter_virtual_recruitment_schedule(profile.id, now=timezone.now(), pacing=pacing)
    pool_keys = dict(pacing.recruitment_pool_weights)
    counts = {pool_key: sum(item.pool_key == pool_key for item in schedule) for pool_key in pool_keys}

    assert sum(counts.values()) == 9
    assert counts == {"dianshi": 3, "xiangshi": 3, "cunmu": 3}
    assert all(item.pool_quota == counts[item.pool_key] for item in schedule)


@pytest.mark.django_db
def test_virtual_recruitment_starts_with_snapshot_and_without_action_points(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_start",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    )
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
    recruitments = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
        quota_date=schedule.quota_date,
    )
    assert recruitments.count() == 9
    assert not recruitments.filter(status=GuestRecruitment.Status.PENDING).exists()
    recruitment = recruitments.get(operation_id=schedule.operation_id)
    manor.refresh_from_db()
    assert recruitment.source == GuestRecruitment.Source.VIRTUAL
    assert recruitment.bot_profile_id == profile.id
    assert recruitment.quota_date == schedule.quota_date
    assert recruitment.quota_ordinal == schedule.quota_ordinal
    assert recruitment.pool_snapshot["snapshot_version"] == 1
    assert recruitment.pool_snapshot["rarity"]["distribution"]
    preview = recruitment.pool_snapshot["candidate_preview"]
    assert preview["salary"] == recruitment.salary_commitment
    assert preview["template_id"] > 0
    assert preview["custom_name"]
    assert recruitment.pool_snapshot["pool"]["draw_count"] == pool.draw_count + manor.tavern_recruitment_bonus
    total_cost = sum(int((row.cost or {}).get("silver", 0)) for row in recruitments)
    assert manor.silver == before_silver - total_cost
    assert manor.action_points == before_action_points
    assert not RecruitmentCandidate.objects.filter(manor_id=manor.id).exists()


@pytest.mark.django_db
def test_virtual_recruitment_is_immediate_and_idempotent(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_complete",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    )
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    start_result = start_virtual_recruitment(schedule, now=schedule.due_at + timedelta(seconds=1))
    recruitment = GuestRecruitment.objects.get(pk=start_result.recruitment_id)
    recruitment.refresh_from_db()

    first_guest_count = Guest.objects.filter(manor_id=profile.manor_id).count()
    first_record_count = RecruitmentRecord.objects.filter(manor_id=profile.manor_id).count()
    recruitments = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
        quota_date=schedule.quota_date,
    )
    assert recruitments.count() == 9
    assert first_guest_count > 0
    assert first_record_count == sum(int(row.result_count) for row in recruitments)
    assert not RecruitmentCandidate.objects.filter(manor_id=profile.manor_id).exists()
    assert recruitment.duration_seconds == 0
    assert recruitment.pool_snapshot["settlement"]["mode"] == "instant_batch"
    assert recruitment.refresh_from_db() is None
    assert recruitment.status == GuestRecruitment.Status.COMPLETED
    assert recruitment.result_count in {0, 1}

    assert finalize_virtual_guest_recruitment(recruitment.id, now=timezone.now()) is False
    assert Guest.objects.filter(manor_id=profile.manor_id).count() == first_guest_count
    assert RecruitmentRecord.objects.filter(manor_id=profile.manor_id).count() == first_record_count


@pytest.mark.django_db
def test_virtual_recruitment_does_not_schedule_completion_worker(
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

    assert recruitment.status == GuestRecruitment.Status.COMPLETED
    assert complete_guest_recruitment.run(recruitment.id) == "skipped"


@pytest.mark.django_db
def test_virtual_recruitment_ignores_salary_runway_and_spends_recruitment_cost(
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
    assert result.status is VirtualRecruitmentStatus.STARTED
    recruitments = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
    )
    assert recruitments.exists()
    assert result.deferred_slots > 0
    assert manor.silver == before_silver - sum(int((row.cost or {}).get("silver", 0)) for row in recruitments)


@pytest.mark.django_db
def test_virtual_recruitment_insufficient_silver_defers_without_consuming_quota(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_deferred", silver=0)
    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    now = schedule.due_at + timedelta(seconds=1)

    deferred = start_virtual_recruitment(schedule, now=now)

    assert deferred.status is VirtualRecruitmentStatus.DEFERRED
    assert deferred.reason == "insufficient_resource"
    assert not GuestRecruitment.objects.filter(operation_id=schedule.operation_id).exists()

    Manor.objects.filter(pk=profile.manor_id).update(silver=1_000_000)
    retried = start_virtual_recruitment(schedule, now=now + timedelta(minutes=15))

    assert retried.status is VirtualRecruitmentStatus.STARTED
    assert GuestRecruitment.objects.filter(operation_id=schedule.operation_id).count() == 1


@pytest.mark.django_db
def test_virtual_recruitment_defers_without_spending_when_roster_is_full(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(django_user_model, username="virtual_recruit_full_roster")
    manor = profile.manor
    full_roster_template = GuestTemplate.objects.create(
        key="virtual_recruit_full_roster_orange",
        name="满员测试门客",
        archetype="military",
        rarity="orange",
        base_attack=10_000,
        base_intellect=10_000,
        base_defense=10_000,
        base_agility=10_000,
        base_hp=10_000,
        recruitable=False,
    )
    capacity = manor.guest_capacity
    for index in range(capacity):
        create_guest_from_template(
            manor=manor,
            template=full_roster_template,
            rarity="orange",
            custom_name=f"满员测试门客{index + 1}",
        )

    schedule = next(
        item for item in iter_virtual_recruitment_schedule(profile.id, now=timezone.now()) if item.pool_key == "cunmu"
    )
    manor.refresh_from_db()
    before_silver = manor.silver
    start_result = start_virtual_recruitment(schedule, now=schedule.due_at + timedelta(seconds=1))

    profile.refresh_from_db()
    manor.refresh_from_db()
    assert start_result.status is VirtualRecruitmentStatus.DEFERRED
    assert start_result.reason == "guest_capacity_full"
    assert not GuestRecruitment.objects.filter(operation_id=schedule.operation_id).exists()
    assert manor.silver == before_silver
    assert Guest.objects.filter(manor_id=manor.id).count() == capacity


@pytest.mark.django_db
def test_virtual_recruitment_scanner_is_bounded_and_does_not_duplicate_pending_queue(
    django_user_model,
    load_guest_data,
):
    profile = _create_v2_profile(
        django_user_model,
        username="virtual_recruit_scan",
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
    )
    schedule_now = timezone.now() + timedelta(days=1)
    scan_now = iter_virtual_recruitment_schedule(profile.id, now=schedule_now)[0].due_at + timedelta(seconds=1)

    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 1
    assert schedule_due_virtual_recruitments(now=scan_now, limit=1) == 0
    recruitments = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
    )
    assert recruitments.count() == 9
    assert not recruitments.filter(status=GuestRecruitment.Status.PENDING).exists()
    assert all(recruitment.duration_seconds == 0 for recruitment in recruitments)


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
