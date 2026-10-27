"""多波配置只影响迁移后新出征，保留原有赛季与队伍。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone

from gameplay.models import Season

pytestmark = pytest.mark.django_db


def season(now, **overrides):
    fields = {
        "name": "采集迁移",
        "starts_at": now - timedelta(days=1),
        "ends_at": now + timedelta(days=89),
        "fiction_score": 42,
        "population": {"keep": 1},
        "rules": {"campaign": {"resource_economy": {"enabled": True, "pve": {"grain_run": {"grain": 6000}}}}},
    }
    fields.update(overrides)
    return Season.objects.create(**fields)


def migrate():
    module = import_module("gameplay.migrations.0268_season_collection_waves")
    with transaction.atomic():
        module.enable_collection_waves(apps, SimpleNamespace(connection=connection))


def test_active_configuration_added_once_without_losing_progress():
    now = timezone.now()
    current = season(now)
    migrate()
    current.refresh_from_db()
    assert current.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] == {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    assert current.population == {"keep": 1} and current.fiction_score == 42
    snapshot = deepcopy(current.rules)
    migrate()
    current.refresh_from_db()
    assert current.rules == snapshot


def test_custom_disabled_and_finished_seasons_are_untouched():
    now = timezone.now()
    custom = season(now)
    custom.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {"enabled": False}
    custom.save(update_fields=["rules"])
    disabled = season(now)
    disabled.rules["campaign"]["resource_economy"]["enabled"] = False
    disabled.save(update_fields=["rules"])
    closed = season(now, closed=True)
    expired = season(now, starts_at=now - timedelta(days=100), ends_at=now - timedelta(days=1))
    rows = (custom, disabled, closed, expired)
    snapshots = [deepcopy(row.rules) for row in rows]
    migrate()
    for row, expected in zip(rows, snapshots):
        row.refresh_from_db()
        assert row.rules == expected
