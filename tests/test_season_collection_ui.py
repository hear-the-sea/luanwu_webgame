"""资源收集分场时的网页预览与首页进度保持只读并兼容旧队伍。"""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from gameplay.models import SeasonExpedition
from gameplay.selectors.home_season import get_home_season_events
from gameplay.selectors.season import get_season_context
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    pve = season.rules["campaign"]["resource_economy"]["pve"]
    pve["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    pve["grain_run"] = {"grain": 6000, "wood": 600, "iron": 300}
    season.save(update_fields=["rules"])
    return season, player, clock


def test_collection_preview_matches_five_wins_and_scaled_interval(world, client):
    season, player, _ = world
    member, _ = player()
    with override_settings(GAME_TIME_MULTIPLIER=2):
        context = get_season_context(member.manor, season.pk)
        inn = next(
            node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:inn"
        )
        assert context["collection_rules"]["interval_label"] == "150 秒"
        assert context["collection_rules"]["maximum_percent"] == 125
        assert {item["key"]: item["amount"] for item in inn["pve_preview"]} == {
            "grain": 7500,
            "wood": 750,
            "iron": 375,
        }
        client.force_login(member.manor.user)
        html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "全部获胜预计物资" in html
    assert "抵达后每 150 秒 战斗一次" in html
    assert "全胜最高 125%" in html
    assert "战败或召回时按已胜场结算" in html
    assert "零胜无物资" in html and "整趟占用一次当日收益档" in html


def test_collection_stationed_progress_and_deadline_on_both_screens(world, client):
    season, player, _ = world
    member, _ = player()
    now = timezone.now()
    deadline = now + timedelta(minutes=5)
    run = SeasonExpedition.objects.create(
        member=member,
        node_key="mountain:inn",
        kind="pve",
        status="stationed",
        guest_ids=[],
        lineup={"guests": []},
        troops={},
        launched_at=now,
        due_at=deadline,
        stationed_until=now + timedelta(minutes=15),
        result={"collection_waves": {"version": 1, "max_waves": 5, "win_percent": 25, "wins": 2, "completed": 2}},
    )
    event = get_home_season_events(member.manor, now=now)[0]
    assert (event["title"], event["status_label"], event["deadline_label"], event["deadline"]) == (
        "赛季采集",
        "采集中",
        "下一场战斗",
        deadline,
    )
    assert event["collection"] == {"maximum": 5, "completed": 2, "wins": 2, "next_wave": 3, "reward_percent": 50}
    context = get_season_context(member.manor, season.pk)
    active = next(row for row in context["active_expeditions"] if row["id"] == run.pk)
    assert active["status_label"] == "采集中"
    assert active["deadline_label"] == "下一场战斗"
    assert active["collection"] == event["collection"] and active["can_recall"]
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "采集已胜 2 / 5 场" in html
    assert "等待第 3 场" in html
    assert "召回时按已胜场结算" in html
    home = client.get(reverse("home")).content.decode()
    assert "赛季采集：山脚驿站" in home and "采集已胜 2 / 5 场" in home
    run.refresh_from_db()
    assert run.status == "stationed" and run.result["collection_waves"]["wins"] == 2


def test_legacy_season_keeps_single_battle_preview_and_labels(world):
    season, player, _ = world
    member, _ = player()
    season.rules["campaign"]["resource_economy"]["pve"].pop("collection_waves")
    season.save(update_fields=["rules"])
    context = get_season_context(member.manor, season.pk)
    inn = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:inn")
    assert not context["collection_rules"]["enabled"]
    assert next(item["amount"] for item in inn["pve_preview"] if item["key"] == "grain") == 6000
