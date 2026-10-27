from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from gameplay.models.season import Season, SeasonFaction
from gameplay.services.season.economy import produce_locked

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("start_days", [-10, -1, 10])
def test_enabling_campaign_never_produces_before_registration_end(start_days, monkeypatch):
    from django.db import transaction

    migration = import_module("gameplay.migrations.0247_enable_season_campaign")
    now = timezone.now()
    start = now + timedelta(days=start_days)
    season = Season.objects.create(name="预排迁移", starts_at=start, ends_at=start + timedelta(days=90))
    monkeypatch.setattr(migration.timezone, "now", lambda: now)
    migration.enable_existing_campaigns(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    state = SeasonFaction.objects.get(season=season, faction="fiction")
    expected = max(now, start + timedelta(days=3))
    assert state.produced_at == expected
    before = (dict(state.resources), dict(state.troops))
    with transaction.atomic():
        produced = produce_locked(season, expected)
    assert (produced["fiction"].resources, produced["fiction"].troops) == before
