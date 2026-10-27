"""探索者返程后仍可自行利用两小时情报，迁移只替换旧默认时长。"""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


def test_two_hour_intel_is_usable_after_return_and_later_self_dispatch(resource_campaign):
    _, player, clock = resource_campaign
    member, guest = player()
    scout = issue(member, guest, clock, mode="explore", node="mountain:ruins")
    advance_resource_run(scout, clock)
    intel = member.intel_records.get()
    assert intel.expires_at == clock[0] + timedelta(hours=2)
    advance_resource_run(scout, clock)
    clock[0] += timedelta(minutes=20)
    gathering = issue(member, guest, clock, node="mountain:ruins")
    advance_resource_run(gathering, clock)
    intel.refresh_from_db()
    assert not intel.payload["bonus_available"]
    assert member.actions.get(kind="logistics", report=gathering.report).result["resources"]["iron"] == 432


@pytest.mark.parametrize("duration,closed,expected", [(1800, False, 7200), (3600, False, 3600), (1800, True, 1800)])
def test_intel_migration_preserves_existing_records_assets_and_custom_duration(
    resource_campaign, duration, closed, expected
):
    season, player, clock = resource_campaign
    member, guest = player()
    scout = issue(member, guest, clock, mode="explore", node="mountain:ruins")
    advance_resource_run(scout, clock)
    intel = member.intel_records.get()
    expires = intel.expires_at
    season.rules["campaign"]["activities"]["explore"]["intel_seconds"] = duration
    season.closed = closed
    season.save(update_fields=["rules", "closed"])
    before = list(season.faction_states.values())
    run_before = dict(scout.result)
    migration = import_module("gameplay.migrations.0265_season_intel_duration")
    for _ in range(2):
        migration.extend_default_intel(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    scout.refresh_from_db()
    intel.refresh_from_db()
    assert season.rules["campaign"]["activities"]["explore"]["intel_seconds"] == expected
    assert intel.expires_at == expires
    assert scout.result == run_before
    assert list(season.faction_states.values()) == before
