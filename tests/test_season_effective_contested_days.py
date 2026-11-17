"""只有真实有效贡献的对抗可激活周胜点；日贡献封顶不抹去真实交战。"""

from datetime import timedelta

import pytest
from django.db import transaction

from gameplay.models import SeasonAction
from gameplay.services.season.weekly_campaign import (
    record_site_ticks_locked,
    settle_campaign_weeks_locked,
    weekly_victory_scores,
)
from tests.test_season_weekly_campaign import contest, credit
from tests.test_season_weekly_campaign import weekly as _weekly  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def weekly(request):
    return request.getfixturevalue("_weekly")


def prepare_week(weekly, result):
    season, members, start = weekly
    at = start + timedelta(days=1)
    for member in members.values():
        credit(member, at)
    contest(members, at)
    action = SeasonAction.objects.get(member=members["fiction"], kind="combat")
    action.result = {"occurred_at": at.isoformat(), **result}
    action.save(update_fields=["result"])
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 1, 3600, 100, start)
    return season, members, start, action


@pytest.mark.parametrize("result", [{}, {"merit": 0, "requested_merit": 0}])
def test_empty_or_zero_effective_report_does_not_unlock_weekly_victory(weekly, result):
    season, _, start, _ = prepare_week(weekly, result)
    with transaction.atomic():
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    assert season.weeks.get(number=1).winner == ""
    assert weekly_victory_scores(season) == {"fiction": 0, "history": 0}
    state = season.faction_states.get(faction="fiction")
    assert state.economy_state["weekly_campaign"]["weeks"]["1"]["contested_days"] == 0


@pytest.mark.parametrize("result", [{"merit": 0, "requested_merit": 20}, {"merit": 20}])
def test_capped_real_battle_and_positive_legacy_merit_remain_valid_contests(weekly, result):
    season, _, start, _ = prepare_week(weekly, result)
    with transaction.atomic():
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    assert season.weeks.get(number=1).winner == "fiction"
    assert weekly_victory_scores(season) == {"fiction": 10, "history": 0}


def test_already_settled_week_points_are_not_recomputed(weekly):
    season, _, start, action = prepare_week(weekly, {"merit": 20, "requested_merit": 20})
    with transaction.atomic():
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    before = list(season.weeks.values())
    action.result = {**action.result, "merit": 0, "requested_merit": 0}
    action.save(update_fields=["result"])
    with transaction.atomic():
        assert settle_campaign_weeks_locked(season, start + timedelta(days=7)) == 0
    assert list(season.weeks.values()) == before
    assert weekly_victory_scores(season) == {"fiction": 10, "history": 0}
