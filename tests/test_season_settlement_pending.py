"""跨周和闭季时等待可能追溯入账的队伍。"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import SeasonExpedition
from gameplay.services.season.garrison_merit import settle_garrison_merit_locked
from gameplay.services.season.lifecycle import settle_season
from gameplay.services.season.population import refresh_population_locked
from gameplay.services.season.weekly_campaign import record_site_ticks_locked, settle_campaign_weeks_locked
from tests.test_season_weekly_campaign import contest, credit
from tests.test_season_weekly_campaign import weekly as _weekly  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def weekly(request):
    return request.getfixturevalue("_weekly")


def test_cross_week_garrison_is_credited_before_week_is_frozen(weekly):
    season, members, start = weekly
    boundary = start + timedelta(days=7)
    arrived, until = boundary - timedelta(minutes=30), boundary + timedelta(minutes=30)
    run = SeasonExpedition.objects.create(
        member=members["history"],
        node_key="mountain:gate",
        kind="garrison",
        status="stationed",
        launched_at=arrived,
        due_at=until,
        stationed_until=until,
        result={"stationed_at": arrived.isoformat(), "station_seconds": 3600, "garrison_merit_per_hour": 10},
    )
    credit(members["fiction"], start + timedelta(days=1))
    contest(members, start + timedelta(days=1))
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 1, 3600, 10, start)
        assert settle_campaign_weeks_locked(season, boundary) == 0
        assert not season.weeks.exists()
        settle_garrison_merit_locked(run, until)
        run.status = "returning"
        run.save(update_fields=["status", "result"])
        assert settle_campaign_weeks_locked(season, until) == 1
    assert season.weeks.get(number=1).winner == "fiction"


def test_closing_waits_for_stationed_garrison_and_ignores_returning(weekly):
    season, members, start = weekly
    end = season.ends_at
    run = SeasonExpedition.objects.create(
        member=members["fiction"],
        node_key="mountain:gate",
        kind="garrison",
        status="stationed",
        launched_at=start + timedelta(days=82),
        due_at=start + timedelta(days=83),
    )
    assert settle_season(season.pk, end)["closed"] == 0
    season.refresh_from_db()
    assert not season.closed
    run.status = "returning"
    run.save(update_fields=["status"])
    assert settle_season(season.pk, end)["closed"] == 1


def test_population_uses_actual_event_time_not_delayed_insert_time(weekly):
    season, members, start = weekly
    now = timezone.now()
    season.rules["campaign"]["population"]["enabled"] = True
    season.save(update_fields=["rules"])
    old = now - timedelta(days=8)
    credit(members["fiction"], old)
    credit(members["history"], now - timedelta(hours=1))
    # 模拟未来凭证已经写入，不能污染按当前时间回放的人口。
    credit(members["fiction"], now + timedelta(hours=1))
    with transaction.atomic():
        population = refresh_population_locked(season, now, force=True)
    assert population["factions"]["fiction"]["count"] == 0
    assert population["factions"]["history"]["count"] == 1
