"""进行中赛季的协作配置迁移不修改进度或既有行动。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone

from gameplay.models import Season
from tests.test_season_resource_integration import issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign

pytestmark = pytest.mark.django_db
resource_campaign = _resource_campaign


def migrate(now, monkeypatch):
    module = import_module("gameplay.migrations.0266_season_small_server_cooperation")
    monkeypatch.setattr(module.timezone, "now", lambda: now)
    with transaction.atomic():
        module.enable_small_server_cooperation(apps, SimpleNamespace(connection=connection))


def make_season(now, **kwargs):
    values = {
        "name": "赛季迁移测试",
        "starts_at": now - timedelta(days=5),
        "ends_at": now + timedelta(days=85),
        "rules": {
            "campaign": {
                "population": {"enabled": True},
                "activities": {"enabled": True},
                "resource_economy": {
                    "enabled": True,
                    "population": {"smoothing_previous_weight": 0.75},
                    "transport": {"cargo_capacity": 15000},
                },
                "sites": {"river:town": {"score_per_tick": 3}},
            }
        },
        "population": {"factions": {"fiction": {"count": 5, "economic_count": 3}}},
        "fiction_score": 27,
    }
    values.update(kwargs)
    return Season.objects.create(**values)


def test_adds_missing_configuration_once_without_changing_progress(monkeypatch):
    now = timezone.now()
    season = make_season(now)
    before_population = deepcopy(season.population)
    migrate(now, monkeypatch)
    season.refresh_from_db()
    campaign = season.rules["campaign"]
    assert campaign["population"]["recommendation_max_active"] == 20
    assert campaign["resource_economy"]["population"] == {
        "smoothing_previous_weight": 0.75,
        "growth_previous_weight": 0,
        "growth_refresh_immediate": True,
    }
    assert campaign["resource_economy"]["transport"] == {
        "cargo_capacity": 15000,
        "participation_full_value": 80,
        "participation_merit": 30,
    }
    assert campaign["sites"]["river:town"] == {
        "score_per_tick": 3,
        "weekday_score_per_tick": 1,
        "weekday_score_effective_at": now.isoformat(),
    }
    assert season.population == before_population and season.fiction_score == 27
    first_rules = deepcopy(season.rules)
    migrate(now + timedelta(hours=2), monkeypatch)
    season.refresh_from_db()
    assert season.rules == first_rules


def test_preserves_custom_values_and_untouched_history(monkeypatch):
    now = timezone.now()
    season = make_season(now)
    campaign = season.rules["campaign"]
    campaign["population"]["recommendation_max_active"] = 8
    campaign["resource_economy"]["population"].update(growth_previous_weight=0.5, growth_refresh_immediate=False)
    campaign["resource_economy"]["transport"].update(participation_full_value=100, participation_merit=45)
    campaign["sites"]["river:town"]["weekday_score_per_tick"] = 2
    season.save(update_fields=["rules"])
    before = deepcopy(season.rules)
    closed = make_season(now, closed=True)
    expired = make_season(now, starts_at=now - timedelta(days=90), ends_at=now - timedelta(days=1))
    before_closed, before_expired = deepcopy(closed.rules), deepcopy(expired.rules)
    migrate(now, monkeypatch)
    season.refresh_from_db()
    closed.refresh_from_db()
    expired.refresh_from_db()
    assert season.rules == before
    assert closed.rules == before_closed and expired.rules == before_expired


def test_disabled_features_and_custom_city_base_are_not_changed(monkeypatch):
    now = timezone.now()
    season = make_season(now)
    campaign = season.rules["campaign"]
    campaign["population"]["enabled"] = False
    campaign["activities"]["enabled"] = False
    campaign["sites"]["river:town"]["score_per_tick"] = 7
    season.save(update_fields=["rules"])
    before = deepcopy(season.rules)
    migrate(now, monkeypatch)
    season.refresh_from_db()
    assert season.rules == before


def test_inflight_transport_retains_its_original_reward_snapshot(resource_campaign, monkeypatch):
    season, player, clock = resource_campaign
    member, guest = player("fiction")
    site = season.sites.get(key="mountain:gate")
    site.owner = "fiction"
    site.durability = site.npc_profile["durability"] - 20
    site.supplies = {}
    site.save(update_fields=["owner", "durability", "supplies"])
    transport = season.rules["campaign"]["resource_economy"]["transport"]
    transport.pop("participation_full_value")
    transport.pop("participation_merit")
    season.save(update_fields=["rules"])
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    before = deepcopy(run.result)
    assert "participation_merit" not in before["activity"]["delivery_reward"]

    migrate(clock[0], monkeypatch)
    run.refresh_from_db()
    season.refresh_from_db()
    assert run.result == before
    assert season.rules["campaign"]["resource_economy"]["transport"]["participation_merit"] == 30
