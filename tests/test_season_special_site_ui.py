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
from gameplay.services.season.economy import ensure_factions_locked, produce_locked
from gameplay.services.season.registration import register_locked
from gameplay.services.season.warfare import campaign_nodes, capture_locked, ensure_sites_locked, validate_destination

pytestmark = pytest.mark.django_db


@pytest.fixture
def special_world(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    rules["campaign"]["population"]["enabled"] = False
    season = Season.objects.create(
        name="铁骑营争夺", starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=85), rules=rules
    )
    manor, _ = Manor.objects.get_or_create(user=user_factory())
    with transaction.atomic():
        member = register_locked(season, manor, "fiction", now)
        states = ensure_factions_locked(season, now)
        camp = ensure_sites_locked(season, now)["forest:camp"]
        for state in states.values():
            state.produced_at = now
            state.save(update_fields=["produced_at"])
    return season, member, camp, now


def test_special_camp_keeps_wooden_sign_and_supports_shared_site_actions(special_world):
    season, member, camp, now = special_world
    node = campaign_nodes(season)["forest:camp"]
    assert node["name"] == "林间营寨"
    assert node["kind"] == "pvp"
    assert set(node["modes"]) == {"attack", "siege", "garrison"}
    assert node["unlock_day"] == 1
    assert "特殊护院" in node["description"]
    assert validate_destination(season, "forest:camp", "attack", now)["name"] == "林间营寨"


def test_city_shows_configured_open_and_other_day_scores(special_world):
    season, member, _, _ = special_world
    season.rules["campaign"]["sites"]["river:town"]["weekday_score_per_tick"] = 1
    season.rules["campaign"]["expeditions"]["score_interval_seconds"] = 1800
    season.save(update_fields=["rules"])
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "古城开放日每 30 分钟 3 战果，其余时间 1 战果" in html
    assert "休整期间不计持有战果" in html


def test_special_production_selector_is_readonly_and_retains_lost_faction_stock(special_world):
    season, member, camp, now = special_world
    stock = season.faction_states.get(faction=member.faction)
    stock.troops["bloodthirsty_cavalry"] = 35
    stock.save(update_fields=["troops"])
    camp.owner = "history"
    camp.protected_until = now - timedelta(seconds=1)
    camp.save(update_fields=["owner", "protected_until"])
    before = list(season.faction_states.order_by("pk").values("troops", "resources", "produced_at"))
    context = get_season_context(member.manor, season.pk)
    special = context["special_production_options"][0]
    assert special["name"] == "嗜血铁骑"
    assert special["owner_label"] == "历史阵营"
    assert special["available"] == 35
    assert special["producing"]
    troop = next(row for row in context["troop_options"] if row["key"] == "bloodthirsty_cavalry")
    assert troop["special"] and troop["max_draw"] == 35
    camp_node = next(
        node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "forest:camp"
    )
    assert camp_node["special_production"] == [special]
    assert [row["key"] for row in camp_node["modes"]] == ["attack"]
    assert list(season.faction_states.order_by("pk").values("troops", "resources", "produced_at")) == before
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "历史阵营控制" in html
    assert "本阵营库存 35" in html
    assert "默认满科技" in html


def test_successful_special_capture_settles_old_owner_before_switch(special_world):
    season, member, camp, now = special_world
    config = season.rules["campaign"]
    interval = config["economy"]["tick_seconds"]
    rate = config["economy"]["special_troops"]["bloodthirsty_cavalry"]["rate"]
    previous = now - timedelta(seconds=2 * interval)
    for state in season.faction_states.all():
        state.produced_at = previous
        state.save(update_fields=["produced_at"])
    camp.owner = "fiction"
    camp.protected_until = (
        previous - timedelta(hours=1) + timedelta(seconds=config["sites"]["forest:camp"]["protection_seconds"])
    )
    camp.control = 0
    camp.durability = 0
    camp.guards = 0
    camp.save(update_fields=["owner", "protected_until", "control", "durability", "guards"])
    with transaction.atomic():
        assert capture_locked(camp, "history", now)
        camp.save()
        assert season.faction_states.get(faction="fiction").troops["bloodthirsty_cavalry"] == 2 * rate
        assert season.faction_states.get(faction="history").troops["bloodthirsty_cavalry"] == 0
        produce_locked(season, now + timedelta(seconds=interval))
    assert season.faction_states.get(faction="fiction").troops["bloodthirsty_cavalry"] == 2 * rate
    assert season.faction_states.get(faction="history").troops["bloodthirsty_cavalry"] == rate


@pytest.mark.parametrize(
    "owner,label", [("", "中立 · 未占领"), ("fiction", "小说阵营占领"), ("history", "历史阵营占领")]
)
def test_map_wooden_sign_shows_actual_site_ownership_without_mutating_it(special_world, owner, label):
    season, member, camp, _ = special_world
    camp.owner = owner
    camp.save(update_fields=["owner"])
    before = list(season.sites.values("key", "owner", "guards", "durability", "control"))
    context = get_season_context(member.manor, season.pk)
    nodes = [node for region in context["season_maps"] for node in region["nodes"]]
    node = next(row for row in nodes if row["key"] == camp.key)
    assert node["occupation_label"] == label
    assert node["occupation_class"] == (owner or "neutral")
    assert next(row for row in nodes if row["key"] == "mountain:inn")["occupation_label"] == ""
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert f'class="season-site-owner season-site-owner--{owner or "neutral"}">{label}</small>' in html
    assert "默认满科技" in html
    assert list(season.sites.values("key", "owner", "guards", "durability", "control")) == before
