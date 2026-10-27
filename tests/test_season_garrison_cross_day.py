"""驻守贡献按真实日期和赛季周计入，保持日上限与幂等。"""

from datetime import datetime, time, timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.services.season.garrison_merit import garrison_merit_preview, settle_garrison_merit_locked
from gameplay.services.season.merit import grant_merit_locked
from tests.test_season_expeditions import _idle_garrison
from tests.test_season_expeditions import war as _war  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def scenario(request):
    return request.getfixturevalue("_war")


def _prepare(scenario, arrived, until, season_start):
    season, member, run = _idle_garrison(scenario)
    season.starts_at = season_start
    season.rules["campaign"]["contribution"].update(enabled=True, daily_cap=300)
    season.save(update_fields=["starts_at", "rules"])
    run.member.season = season
    run.result = {
        **run.result,
        "stationed_at": arrived.isoformat(),
        "station_seconds": int((until - arrived).total_seconds()),
    }
    run.stationed_until = run.due_at = until
    run.save(update_fields=["result", "stationed_until", "due_at"])
    return season, member, run


def test_cross_midnight_garrison_respects_each_day_cap_and_is_idempotent(scenario):
    midnight = datetime.combine(timezone.localdate(), time.min, tzinfo=timezone.get_current_timezone())
    arrived, until = midnight - timedelta(hours=2), midnight + timedelta(hours=6)
    _, member, run = _prepare(scenario, arrived, until, midnight - timedelta(days=9, hours=3))
    with transaction.atomic():
        grant_merit_locked(member, 260, "logistics", midnight + timedelta(hours=5), event_key="other-activity")
        assert garrison_merit_preview(run, midnight + timedelta(hours=2)) == 40
        settle_garrison_merit_locked(run, until)
        run.save(update_fields=["result"])
        settle_garrison_merit_locked(run, until)
    member.refresh_from_db()
    rows = list(member.actions.filter(kind="merit_credit", result__reason="实际驻守时长贡献").order_by("date"))
    assert [row.date for row in rows] == [timezone.localdate(arrived), timezone.localdate(until)]
    assert [row.result["requested_merit"] for row in rows] == [20, 60]
    assert [row.result["merit"] for row in rows] == [20, 40]
    assert run.result["garrison_service"]["merit"] == 60
    assert member.merit == 320


def test_cross_week_garrison_preserves_exact_total_and_week_attribution(scenario):
    midnight = datetime.combine(timezone.localdate(), time.min, tzinfo=timezone.get_current_timezone())
    boundary = midnight - timedelta(hours=1)
    arrived, until = boundary - timedelta(minutes=30), boundary + timedelta(minutes=30)
    _, member, run = _prepare(scenario, arrived, until, boundary - timedelta(days=7))
    with transaction.atomic():
        settle_garrison_merit_locked(run, until)
        run.save(update_fields=["result"])
    rows = list(member.actions.filter(kind="merit_credit", result__reason="实际驻守时长贡献").order_by("week"))
    assert [(row.week, row.result["merit"]) for row in rows] == [(1, 5), (2, 5)]
    assert run.result["garrison_service"]["seconds"] == 3600
