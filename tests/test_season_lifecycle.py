from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.exceptions import GameError
from gameplay.models import Manor
from gameplay.models.season import Season, SeasonAction, SeasonMember, SeasonWeek
from gameplay.services.season.lifecycle import calculate_week_scores, create_season, settle_season, settle_seasons

pytestmark = pytest.mark.django_db
START = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)


@pytest.fixture
def season():
    return Season.objects.create(name="测试赛季", starts_at=START, ends_at=START + timedelta(days=90), rules={})


@pytest.fixture
def participant(user_factory):
    def make(season, faction, points=0, week=1):
        user = user_factory()
        manor, _ = Manor.objects.get_or_create(user=user)
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        if points:
            SeasonAction.objects.create(
                member=member,
                token=uuid4(),
                request_hash="test",
                kind="challenge",
                date=START.date(),
                week=week,
                war_points=points,
            )
        return member

    return make


def test_create_season_freezes_rules_and_rejects_overlap(monkeypatch):
    from gameplay.services.season import catalog

    rules = {"daily_actions": 6, "nested": {"value": 1}}
    monkeypatch.setattr(catalog, "load_rules", lambda: rules)
    season = create_season(" 初季 ", START)
    rules["nested"]["value"] = 9
    season.refresh_from_db()
    assert season.name == "初季"
    assert season.ends_at - season.starts_at == timedelta(days=90)
    assert season.rules["nested"]["value"] == 1
    with pytest.raises(GameError, match="已有赛季"):
        create_season("重叠", START + timedelta(days=89))
    second = create_season("下季", season.ends_at)
    assert second.starts_at == season.ends_at


def test_create_rejects_invalid_time_and_name():
    with pytest.raises(GameError, match="时区"):
        create_season("测试", datetime(2026, 1, 1))
    with pytest.raises(GameError, match="名称"):
        create_season(" ", START)


def test_no_week_is_settled_before_exact_boundary(season):
    assert settle_season(season.pk, START - timedelta(seconds=1)) == {"weeks": 0, "closed": 0}
    assert settle_season(season.pk, START + timedelta(days=7) - timedelta(microseconds=1)) == {"weeks": 0, "closed": 0}
    assert not SeasonWeek.objects.exists()
    assert settle_season(season.pk, START + timedelta(days=7)) == {"weeks": 1, "closed": 0}


def test_scoring_uses_equal_number_of_contributors(season, participant):
    participant(season, "fiction", 50)
    participant(season, "fiction", 20)
    participant(season, "fiction", 10)
    participant(season, "history", 40)
    participant(season, "history", 35)
    assert calculate_week_scores(season, 1) == {
        "fiction_points": 70,
        "history_points": 75,
        "matched_members": 2,
        "winner": "history",
    }
    settle_season(season.pk, START + timedelta(days=7))
    season.refresh_from_db()
    assert (season.fiction_score, season.history_score) == (0, 1)
    assert SeasonWeek.objects.get(number=1).matched_members == 2


def test_single_sided_participation_does_not_award_free_win(season, participant):
    participant(season, "fiction", 100)
    settle_season(season.pk, START + timedelta(days=7))
    week = SeasonWeek.objects.get(number=1)
    assert (week.fiction_points, week.history_points, week.matched_members, week.winner) == (0, 0, 0, "")


def test_settlement_is_idempotent_and_keeps_player_rewards(season, participant):
    member = participant(season, "fiction", 50)
    member.merit = 3000
    member.scroll_available = True
    member.save(update_fields=["merit", "scroll_available"])
    participant(season, "history", 40)
    now = START + timedelta(days=14)
    assert settle_season(season.pk, now) == {"weeks": 2, "closed": 0}
    assert settle_season(season.pk, now) == {"weeks": 0, "closed": 0}
    season.refresh_from_db()
    member.refresh_from_db()
    assert season.fiction_score == 1
    assert (member.merit, member.scroll_available) == (3000, True)
    assert season.weeks.count() == 2


def test_short_final_week_settles_at_day_84_start_but_closes_at_day_91(season, participant):
    participant(season, "history", 9, week=12)
    participant(season, "fiction", 1, week=12)
    before = settle_season(season.pk, START + timedelta(days=83) - timedelta(microseconds=1))
    assert before == {"weeks": 11, "closed": 0}
    assert settle_season(season.pk, START + timedelta(days=83)) == {"weeks": 1, "closed": 0}
    season.refresh_from_db()
    assert not season.closed
    assert settle_season(season.pk, season.ends_at) == {"weeks": 0, "closed": 1}
    season.refresh_from_db()
    assert season.closed and season.winner == "history"
    assert season.settled_weeks == 12
    assert settle_season(season.pk, season.ends_at + timedelta(days=1)) == {"weeks": 0, "closed": 0}


def test_background_scan_ignores_future_and_closed_seasons(season):
    future = Season.objects.create(
        name="未来", starts_at=START + timedelta(days=100), ends_at=START + timedelta(days=190)
    )
    result = settle_seasons(START + timedelta(days=90))
    assert result == {"seasons": 1, "weeks": 12, "closed": 1}
    future.refresh_from_db()
    assert future.settled_weeks == 0
    assert settle_seasons(START + timedelta(days=91)) == {"seasons": 0, "weeks": 0, "closed": 0}


def test_start_command_rejects_invalid_timestamp():
    with pytest.raises(CommandError, match="ISO"):
        call_command("start_season", starts_at="not-a-date")


def test_rollover_creates_fresh_membership_period_without_losing_archive(season, participant):
    from gameplay.services.season.lifecycle import advance_seasons

    member = participant(season, "fiction", 50)
    member.merit = 3000
    member.scroll_available = True
    member.save()
    result = advance_seasons(season.ends_at)
    assert result["started"] == 1
    successor = Season.objects.exclude(pk=season.pk).get()
    assert successor.starts_at == season.ends_at
    assert successor.ends_at == season.ends_at + timedelta(days=90)
    assert not successor.members.exists()
    member.refresh_from_db()
    assert member.merit == 3000 and member.scroll_available
    assert advance_seasons(season.ends_at)["started"] == 0


def test_schedule_and_admin_use_existing_read_only_and_scan_queue():
    from django.conf import settings
    from django.contrib import admin

    from core.admin_records import ReadOnlyAdmin
    from gameplay.tasks.season import settle_seasons_task

    assert "gameplay.settle_seasons" in settings.CELERY_TIMER_SCAN_TASKS
    assert settings.CELERY_BEAT_SCHEDULE["settle-seasons"]["task"] == "gameplay.settle_seasons"
    assert settle_seasons_task.name == "gameplay.settle_seasons"
    assert isinstance(admin.site._registry[Season], ReadOnlyAdmin)
