"""古城开放日战果与规则生效前历史账本保持一致。"""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from gameplay.services.season.campaign_rules import load_campaign_rules, validate_campaign_rules
from gameplay.services.season.warfare import score_sites_locked
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def scoring_world(request):
    season, _, _ = request.getfixturevalue("_resource_campaign")
    start = datetime(2026, 10, 26, tzinfo=ZoneInfo("Asia/Shanghai"))  # 星期一
    season.starts_at = start
    season.ends_at = start + timedelta(days=90)
    season.population = {"mode": "standard", "factions": {}}
    season.rules["campaign"]["playability"]["enabled"] = True
    season.save(update_fields=["starts_at", "ends_at", "population", "rules"])
    site = season.sites.get(key="river:town")
    site.owner = "fiction"
    site.protected_until = None
    site.last_score_at = start
    site.save(update_fields=["owner", "protected_until", "last_score_at"])
    with transaction.atomic():
        score_sites_locked(season, start)  # 冻结周账本启用时刻。
    return season, site, start


def settle(season, at):
    with transaction.atomic():
        score_sites_locked(season, at)
    return season.faction_states.get(faction="fiction")


def test_weekday_and_weekend_rates_credit_each_week_once(scoring_world):
    season, _, start = scoring_world
    state = settle(season, start + timedelta(days=7))
    assert state.score == 5 * 24 + 2 * 24 * 3 == 264
    assert state.economy_state["weekly_campaign"]["weeks"]["1"]["score"] == 264
    state = settle(season, start + timedelta(days=7, hours=1))
    assert state.score == 265
    assert state.economy_state["weekly_campaign"]["weeks"]["2"]["score"] == 1
    assert settle(season, start + timedelta(days=7, hours=1)).score == 265


def test_migrated_rule_preserves_unsettled_ticks_before_effective_time(scoring_world):
    season, _, start = scoring_world
    config = season.rules["campaign"]["sites"]["river:town"]
    config["weekday_score_effective_at"] = (start + timedelta(days=2, hours=12)).isoformat()
    season.save(update_fields=["rules"])
    state = settle(season, start + timedelta(days=7))
    assert state.score == 60 * 3 + 60 + 48 * 3 == 384
    assert state.economy_state["weekly_campaign"]["weeks"]["1"]["score"] == 384


def test_legacy_snapshot_and_custom_open_days(scoring_world):
    season, _, start = scoring_world
    config = season.rules["campaign"]["sites"]["river:town"]
    config.pop("weekday_score_per_tick")
    season.save(update_fields=["rules"])
    assert settle(season, start + timedelta(days=1)).score == 72
    config["weekday_score_per_tick"] = 1
    season.rules["campaign"]["expeditions"]["city_weekdays"] = [1]  # 周二开放
    season.save(update_fields=["rules"])
    assert settle(season, start + timedelta(days=3)).score == 72 + 72 + 24


@pytest.mark.parametrize(
    "field,value",
    [
        ("weekday_score_per_tick", -1),
        ("weekday_score_per_tick", True),
        ("weekday_score_effective_at", "bad"),
        ("weekday_score_effective_at", "2026-10-26T12:00:00"),
    ],
)
def test_invalid_scoring_options_are_rejected(field, value):
    config = deepcopy(load_campaign_rules())
    config["sites"]["river:town"][field] = value
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)
