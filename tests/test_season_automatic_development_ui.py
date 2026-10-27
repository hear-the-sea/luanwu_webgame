from datetime import timedelta

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.models import Manor, Season
from gameplay.selectors.season import get_season_context
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.registration import register_locked

pytestmark = pytest.mark.django_db


@pytest.fixture
def automatic_ui(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    rules["campaign"]["population"]["enabled"] = False
    season = Season.objects.create(
        name="阵营自动发展", starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=85), rules=rules
    )
    manor, _ = Manor.objects.get_or_create(user=user_factory())
    with transaction.atomic():
        member = register_locked(season, manor, "fiction", now)
        state = ensure_factions_locked(season, now)[member.faction]
    state.construction_points = 500
    state.resources = {key: 0 for key in state.resources}
    state.save(update_fields=["construction_points", "resources"])
    return season, member, state


def test_low_resource_development_displays_points_and_no_manual_forms(automatic_ui):
    season, member, state = automatic_ui
    before = (state.resources.copy(), state.levels.copy(), state.produced_at)
    context = get_season_context(member.manor, season.pk)
    assert context["construction_points"] == 500
    assert context["development_next_threshold"] == 1000
    assert context["development_remaining_points"] == 500
    assert not context["development_maxed"]
    for row in context["upgrade_options"]:
        assert row["level"] == 1
        assert row["next_threshold"] == 1000 and row["remaining_points"] == 500
        assert "cost_label" not in row and "stage_locked" not in row
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert 'name="action" value="upgrade"' not in html
    assert 'name="upgrade_key"' not in html
    assert "升级需要：" not in html
    assert "设施自动升级" in html
    assert "<strong>500</strong> 建设值" in html
    assert "查看护院科技" in html
    state.refresh_from_db()
    assert (state.resources, state.levels, state.produced_at) == before


def test_automatic_development_maximum_level_has_no_next_threshold(automatic_ui):
    season, member, state = automatic_ui
    state.construction_points = 15000
    state.levels = {key: row["max_level"] for key, row in season.rules["campaign"]["economy"]["buildings"].items()}
    state.save(update_fields=["construction_points", "levels"])
    context = get_season_context(member.manor, season.pk)
    assert context["development_maxed"]
    assert context["development_next_threshold"] is None
    assert context["development_remaining_points"] == 0
    assert all(row["maxed"] and row["next_threshold"] is None for row in context["upgrade_options"])
    client = Client()
    client.force_login(member.manor.user)
    assert "阵营建设已全部满级" in client.get(reverse("gameplay:season")).content.decode()


def test_legacy_snapshot_without_auto_configuration_still_renders(automatic_ui):
    season, member, state = automatic_ui
    season.rules["campaign"]["economy"].pop("auto_development")
    season.save(update_fields=["rules"])
    assert get_season_context(member.manor, season.pk)["development_next_threshold"] == 1000
