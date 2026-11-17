"""首页只读展示赛季队伍，阶段倒计时与赛季详情一致。"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember, SeasonRally
from gameplay.selectors.home_season import get_home_season_events
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


@pytest.fixture
def season_home(user_factory):
    now = timezone.now()
    user = user_factory()
    manor, _ = Manor.objects.get_or_create(user=user)
    season = Season.objects.create(name="首页战局", starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=85))
    member = SeasonMember.objects.create(season=season, manor=manor, faction="fiction")

    def expedition(**overrides):
        values = {
            "member": member,
            "node_key": "mountain:gate",
            "kind": "siege",
            "status": "outbound",
            "lineup": {"guests": [{"display_name": "赵云"}, {"display_name": "关羽"}]},
            "troops": {"qiang_wang": 700, "jian_shen": 200},
            "launched_at": now,
            "due_at": now + timedelta(minutes=30),
        }
        values.update(overrides)
        return SeasonExpedition.objects.create(**values)

    return manor, member, now, expedition


def test_home_season_filters_to_own_unfinished_teams_and_reads_once(
    season_home, user_factory, django_assert_num_queries
):
    manor, member, now, create = season_home
    own = create()
    create(status="completed")
    foreign_manor, _ = Manor.objects.get_or_create(user=user_factory())
    foreign_member = SeasonMember.objects.create(season=member.season, manor=foreign_manor, faction="history")
    create(member=foreign_member)
    with django_assert_num_queries(1):
        events = get_home_season_events(manor, now=now)
    assert [event["id"] for event in events] == [own.pk]
    assert events[0]["guest_names"] == "赵云、关羽"
    assert events[0]["troop_count"] == 900
    assert events[0]["node_name"] == "青石关"
    assert events[0]["details_url"] == f"{reverse('gameplay:season')}?season_id={member.season_id}#season-team-status"


@pytest.mark.parametrize(
    ("kind", "status", "title", "deadline_label"),
    [
        ("pve", "outbound", "赛季出征", "预计抵达"),
        ("garrison", "stationed", "赛季驻军", "驻守结束"),
        ("garrison", "returning", "赛季驻军", "预计归队"),
        ("transport", "transporting", "赛季运输", "运输结算"),
        ("intercept", "transporting", "赛季出征", "遭遇战结算"),
        ("escort", "transporting", "赛季护送", "遭遇战结算"),
    ],
)
def test_home_season_stage_deadlines(season_home, kind, status, title, deadline_label):
    manor, _, now, create = season_home
    stationed_until = now + timedelta(hours=8)
    run = create(kind=kind, status=status, stationed_until=stationed_until)
    event = get_home_season_events(manor, now=now)[0]
    assert event["title"] == title
    assert event["deadline_label"] == deadline_label
    assert event["deadline"] == (stationed_until if status == "stationed" else run.due_at)
    if kind in {"intercept", "escort"}:
        assert event["status_label"] == "等待遭遇战"


def test_home_gathering_uses_shared_rally_deadline_and_remains_readonly_when_due(season_home):
    manor, member, now, create = season_home
    rally = SeasonRally.objects.create(
        season=member.season,
        leader=member,
        node_key="mountain:gate",
        kind="siege",
        gathers_until=now - timedelta(seconds=5),
    )
    run = create(status="gathering", rally=rally, due_at=now + timedelta(minutes=1))

    def readonly(execute, sql, params, many, context):
        assert sql.lstrip().split()[0].upper() == "SELECT"
        return execute(sql, params, many, context)

    with connection.execute_wrapper(readonly):
        event = get_home_season_events(manor, now=now)[0]
    assert event["title"] == "赛季集结"
    assert event["deadline"] == rally.gathers_until
    assert event["awaiting_settlement"] is True
    run.refresh_from_db()
    assert run.status == "gathering"


def test_home_wheel_battle_and_failed_team_do_not_show_a_misleading_arrival(season_home):
    manor, member, now, create = season_home
    rally = SeasonRally.objects.create(
        season=member.season,
        leader=member,
        node_key="mountain:gate",
        kind="siege",
        status="marching",
        gathers_until=now,
        result={"wave": 2},
    )
    run = create(rally=rally)
    event = get_home_season_events(manor, now=now)[0]
    assert event["status_label"] == "连续交战中"
    assert event["deadline_label"] == "下轮交战"
    run.status = "failed"
    run.save(update_fields=["status"])
    event = get_home_season_events(manor, now=now)[0]
    assert event["status_label"] == "等待处理"
    assert event["deadline"] is None


def test_home_page_displays_only_season_events_without_empty_placeholder(season_home, client):
    manor, _, _, create = season_home
    ensure_manor(manor.user)
    run = create()
    client.force_login(manor.user)
    response = client.get(reverse("home"))
    body = response.content.decode()
    assert response.status_code == 200
    assert f'data-season-home-event="{run.pk}"' in body
    assert 'data-season-deadline="' in body
    assert 'data-season-revision="' in body
    assert "赛季出征：青石关" in body
    assert "赵云、关羽" in body
    assert 'data-complete-text="等待结算"' in body
    assert "#season-team-status" in body
    assert "暂无进行中的事件" not in body
    run.refresh_from_db()
    assert run.status == "outbound"


def test_home_without_season_teams_keeps_event_empty_state(client, user_factory):
    user = user_factory()
    ensure_manor(user)
    client.force_login(user)
    body = client.get(reverse("home")).content.decode()
    assert "data-season-home-event" not in body
    assert "暂无进行中的事件" in body


def test_home_shows_estimated_garrison_merit_without_settling(season_home, client):
    manor, _, now, create = season_home
    ensure_manor(manor.user)
    run = create(
        kind="garrison",
        status="stationed",
        stationed_until=now + timedelta(minutes=30),
        result={
            "stationed_at": (now - timedelta(minutes=90)).isoformat(),
            "station_seconds": 7200,
            "garrison_merit_per_hour": 10,
        },
    )
    client.force_login(manor.user)
    body = client.get(reverse("home")).content.decode()
    assert "预计驻守贡献 15" in body
    run.refresh_from_db()
    assert "garrison_service" not in run.result


def test_home_exposes_previous_season_reward_link(season_home, client, monkeypatch):
    manor, _, _, _ = season_home
    ensure_manor(manor.user)
    monkeypatch.setattr(
        "gameplay.selectors.season_archive.previous_season_reward",
        lambda _manor: {
            "season_name": "第一季",
            "kind": "排名奖励",
            "url": "/manor/season/?season_id=9#season-rewards",
        },
    )
    client.force_login(manor.user)
    body = client.get(reverse("home")).content.decode()
    assert "第一季 · 排名奖励待领取" in body
    assert "/manor/season/?season_id=9#season-rewards" in body


@pytest.mark.parametrize("status", ["gathering", "outbound", "stationed", "transporting", "failed", "returning"])
def test_home_recall_controls_follow_team_status(season_home, status):
    manor, member, _, create = season_home
    create(status=status)
    event = get_home_season_events(manor)[0]
    assert event["can_recall"] is (status != "returning")
    assert event["season_id"] == member.season_id
    assert UUID(event["action_token"])


def test_home_gathering_controls_distinguish_leader_and_member(season_home, user_factory):
    manor, member, now, create = season_home
    leader_manor, _ = Manor.objects.get_or_create(user=user_factory())
    leader = SeasonMember.objects.create(season=member.season, manor=leader_manor, faction="fiction")
    rally = SeasonRally.objects.create(
        season=member.season, leader=leader, node_key="mountain:gate", kind="siege", gathers_until=now
    )
    create(status="gathering", rally=rally)
    assert get_home_season_events(manor)[0]["recall_label"] == "退出集结"
    rally.leader = member
    rally.save(update_fields=["leader"])
    assert get_home_season_events(manor)[0]["recall_label"] == "取消集结"


def test_home_recall_posts_existing_action_and_returns_home_idempotently(season_home, client):
    manor, member, now, create = season_home
    run = create(launched_at=now - timedelta(minutes=5), travel_seconds=1800)
    client.force_login(manor.user)
    payload = {
        "season_id": member.season_id,
        "action": "recall",
        "action_token": str(uuid4()),
        "run_id": run.pk,
        "next": reverse("home"),
    }
    response = client.post(reverse("gameplay:season_action"), payload)
    assert response.url == reverse("home")
    run.refresh_from_db()
    assert run.status == "returning"
    assert run.troops == {"qiang_wang": 700, "jian_shen": 200}
    deadline = run.due_at
    response = client.post(reverse("gameplay:season_action"), payload)
    run.refresh_from_db()
    assert response.url == reverse("home")
    assert run.due_at == deadline


def test_home_recall_cannot_recall_other_players_or_redirect_off_site(season_home, client, user_factory):
    manor, member, _, create = season_home
    run = create()
    outsider, _ = Manor.objects.get_or_create(user=user_factory())
    outsider_member = SeasonMember.objects.create(season=member.season, manor=outsider, faction="history")
    other = create(member=outsider_member)
    client.force_login(manor.user)
    response = client.post(
        reverse("gameplay:season_action"),
        {
            "season_id": member.season_id,
            "action": "recall",
            "action_token": str(uuid4()),
            "run_id": other.pk,
            "next": "https://example.com/",
        },
    )
    assert response.url == f"{reverse('gameplay:season')}?season_id={member.season_id}"
    other.refresh_from_db()
    run.refresh_from_db()
    assert other.status == run.status == "outbound"
