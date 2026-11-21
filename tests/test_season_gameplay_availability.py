"""赛季玩法总开关：普通玩家隐藏入口并拦截，管理员保留访问，后台结算冻结。"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember
from gameplay.tasks.season import advance_season_expedition_task, settle_seasons_task

pytestmark = pytest.mark.django_db


def _create_manor(user):
    manor, _ = Manor.objects.get_or_create(user=user)
    return manor


@pytest.fixture
def active_season_expedition(user_factory):
    user = user_factory()
    manor = _create_manor(user)
    now = timezone.now()
    season = Season.objects.create(
        name="关闭期赛季", starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=85)
    )
    member = SeasonMember.objects.create(season=season, manor=manor, faction="fiction")
    SeasonExpedition.objects.create(
        member=member,
        node_key="mountain:gate",
        kind="siege",
        status="outbound",
        lineup={"guests": [{"display_name": "赵云"}]},
        troops={"qiang_wang": 700},
        launched_at=now,
        due_at=now + timedelta(minutes=30),
    )
    return manor


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_player_home_hides_season_entry_and_events(active_season_expedition, client):
    client.force_login(active_season_expedition.user)
    body = client.get(reverse("home")).content.decode()

    assert ">赛季玩法</a>" not in body
    assert "data-season-home-event" not in body


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_player_sees_closed_notice_and_cannot_use_action_endpoint(active_season_expedition, client):
    client.force_login(active_season_expedition.user)

    page = client.get(reverse("gameplay:season"))
    assert page.status_code == 200
    assert "赛季玩法暂时关闭" in page.content.decode()

    action = client.post(reverse("gameplay:season_action"), {"action": "join"})
    assert action.status_code == 302
    assert action["Location"] == reverse("home")


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_staff_keeps_season_access(user_factory, client):
    user = user_factory(is_staff=True)
    _create_manor(user)
    client.force_login(user)

    response = client.get(reverse("gameplay:season"))

    assert response.status_code == 200
    assert ">赛季玩法</a>" in response.content.decode()


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_staff_cannot_use_action_endpoint_while_disabled(user_factory, client):
    user = user_factory(is_staff=True)
    _create_manor(user)
    client.force_login(user)

    action = client.post(reverse("gameplay:season_action"), {"action": "join"})

    assert action.status_code == 302
    assert action["Location"] == reverse("home")


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_home_skips_previous_season_reward_lookup(active_season_expedition, client, monkeypatch):
    from gameplay.selectors import season_archive

    calls = []
    monkeypatch.setattr(season_archive, "previous_season_reward", lambda manor: calls.append(manor.pk))
    client.force_login(active_season_expedition.user)

    client.get(reverse("home"))

    assert calls == []


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_season_tasks_freeze_while_disabled(active_season_expedition):
    assert settle_seasons_task() == {}
    assert advance_season_expedition_task(1) is False


@override_settings(SEASON_GAMEPLAY_ENABLED=False)
def test_management_commands_refuse_while_disabled():
    with pytest.raises(CommandError):
        call_command("settle_seasons")
    with pytest.raises(CommandError):
        call_command("start_season")
