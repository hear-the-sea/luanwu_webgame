import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.models import SeasonExpedition, SeasonIntel
from gameplay.selectors.season import get_season_context
from tests.test_season_coalition_ui import coalition_ui as _coalition_ui  # noqa: F401


@pytest.fixture
def coalition_ui(request):
    return request.getfixturevalue("_coalition_ui")


def map_node_for(member, season, key):
    client = Client()
    client.force_login(member.manor.user)
    response = client.get(reverse("gameplay:season"), {"season_id": season.pk})
    html = response.content.decode()
    raw = html.split('<script id="season-map-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    maps = json.loads(raw)
    return next(node for region in maps for node in region["nodes"] if node["key"] == key)


@pytest.mark.django_db
def test_enemy_site_supply_inventory_is_not_exposed_in_map_json(coalition_ui):
    season, viewer, _ally, _allied, _own, _enemy = coalition_ui
    site = season.sites.get(key="river:docks")
    site.owner = "history"
    site.supplies = {"grain": 918273, "wood": 473829, "iron": 584736}
    site.save(update_fields=["owner", "supplies"])
    node = map_node_for(viewer, season, site.key)
    assert node.get("site_supplies", {}) == {}
    assert node["site"]["recovery"] == {}


@pytest.mark.django_db
def test_own_site_supply_inventory_remains_visible(coalition_ui):
    season, viewer, _ally, _allied, _own, _enemy = coalition_ui
    site = season.sites.get(key="river:docks")
    site.supplies = {"grain": 918273, "wood": 473829, "iron": 584736}
    site.save(update_fields=["supplies"])
    node = map_node_for(viewer, season, site.key)
    assert node["site_supplies"] == site.supplies
    assert node["site"]["recovery"]["enabled"] is True


@pytest.mark.django_db
def test_intel_reveals_enemy_garrison_but_not_private_supply_inventory(coalition_ui):
    season, viewer, _ally, _allied, _own, enemy_rally = coalition_ui
    site = season.sites.get(key="river:docks")
    site.owner = "history"
    site.supplies = {"grain": 918273}
    site.save(update_fields=["owner", "supplies"])
    SeasonExpedition.objects.create(
        member=enemy_rally.leader,
        node_key=site.key,
        kind="garrison",
        status="stationed",
        guest_ids=[],
        lineup={
            "guests": [{"guest_id": 999, "display_name": "可侦察敌军", "current_hp": 100, "attack": 100, "defense": 50}]
        },
        troops={"dao_sheng": 100},
        launched_at=timezone.now(),
        due_at=timezone.now() + timedelta(hours=1),
    )
    SeasonIntel.objects.create(
        season=season,
        member=viewer,
        faction=viewer.faction,
        node_key="mountain:ruins",
        kind="exploration",
        payload={"target_site_key": site.key},
        expires_at=timezone.now() + timedelta(hours=1),
    )
    node = map_node_for(viewer, season, site.key)
    assert node["garrison"]["players"][0]["guest_names"] == "可侦察敌军"
    assert (node["garrison"]["member_count"], node["garrison"]["total_troops"]) == (1, 100)
    assert node["site_supplies"] == {}
    assert node["site"]["recovery"] == {}


@pytest.mark.django_db
def test_enemy_garrison_counts_stay_private_until_intel_and_after_expiry(coalition_ui):
    season, viewer, _ally, _allied, _own, enemy_rally = coalition_ui
    site = season.sites.get(key="river:docks")
    site.owner = "history"
    site.save(update_fields=["owner"])
    now = timezone.now()
    SeasonExpedition.objects.create(
        member=enemy_rally.leader,
        node_key=site.key,
        kind="garrison",
        status="stationed",
        guest_ids=[],
        lineup={
            "guests": [{"guest_id": 999, "display_name": "隐秘敌将", "current_hp": 100, "attack": 100, "defense": 50}]
        },
        troops={"dao_sheng": 731},
        launched_at=now,
        due_at=now + timedelta(hours=1),
    )

    def inspect(hidden):
        node = map_node_for(viewer, season, site.key)
        garrison = node["garrison"]
        context = get_season_context(viewer.manor, season.pk)
        row = next(item for item in context["site_rows"] if item["key"] == site.key)
        assert row["garrison"] == garrison
        client = Client()
        client.force_login(viewer.manor.user)
        html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
        if hidden:
            assert garrison == {
                "member_count": None,
                "guest_count": None,
                "total_troops": None,
                "players": [],
                "hidden": True,
            }
            assert "隐秘敌将" not in html
            assert "1 位玩家 · 731 护院" not in html
            assert "驻防待命 1 人 / 1 门客 · 护院 731" not in html
            assert "人数与兵力未知" in html
            assert "玩家驻防未知 · 可先侦察" in html
        else:
            assert (garrison["member_count"], garrison["guest_count"], garrison["total_troops"]) == (1, 1, 731)
            assert garrison["players"][0]["guest_names"] == "隐秘敌将"
            assert "驻防待命 1 人 / 1 门客 · 护院 731" in html
            assert "此处为侦察时记录，当前驻防请查看目标据点" in html

    inspect(hidden=True)
    intel = SeasonIntel.objects.create(
        season=season,
        member=viewer,
        faction=viewer.faction,
        node_key="mountain:ruins",
        kind="exploration",
        payload={"target_site_key": site.key},
        expires_at=now + timedelta(hours=1),
    )
    inspect(hidden=False)
    intel.expires_at = now - timedelta(seconds=1)
    intel.save(update_fields=["expires_at"])
    inspect(hidden=True)


@pytest.mark.django_db
def test_own_garrison_counts_remain_visible_without_intel(coalition_ui):
    season, viewer, _ally, _allied, _own, _enemy = coalition_ui
    garrison = map_node_for(viewer, season, "river:docks")["garrison"]
    assert (garrison["member_count"], garrison["guest_count"], garrison["total_troops"]) == (2, 2, 4000)
    assert not garrison.get("hidden", False)
