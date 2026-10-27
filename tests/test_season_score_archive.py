"""赛季战局与往季奖励入口的只读回归。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ItemTemplate, Manor, Season, SeasonAction, SeasonMember
from gameplay.models.season import SeasonClaim, SeasonWeek
from gameplay.selectors.season import get_season_context
from gameplay.selectors.season_archive import previous_season_reward
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.registration import assign_factions_locked, register_locked
from gameplay.services.season.weekly_campaign import week_number

pytestmark = pytest.mark.django_db


@pytest.fixture
def scoreboard(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    season = Season.objects.create(
        name="前线", starts_at=now - timedelta(days=2), ends_at=now + timedelta(days=88), rules=rules
    )
    members = {}
    for faction in ("fiction", "history"):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        members[faction] = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
    with transaction.atomic():
        states = ensure_factions_locked(season, now)
    current = str(week_number(season, now))
    for faction, value in (("fiction", 21), ("history", 8)):
        state = states[faction]
        state.economy_state = {
            "weekly_campaign": {
                "activated_at": season.starts_at.isoformat(),
                "weeks": {current: {"score": value, "victory_points": 10 if faction == "fiction" else 0}},
            }
        }
        state.save(update_fields=["economy_state"])
    report = BattleReport.objects.create(
        manor=members["fiction"].manor, opponent_name="对阵", winner="attacker", starts_at=now, completed_at=now
    )
    return season, members, report, now


def combat(member, opponent, report, day, merit=20):
    return SeasonAction.objects.create(
        member=member,
        opponent=opponent,
        report=report,
        token=uuid4(),
        request_hash="test",
        kind="combat",
        date=day,
        result={"requested_merit": merit},
    )


def test_week_scores_and_victory_progress_are_read_only_and_distinguish_no_winner(scoreboard):
    season, members, report, now = scoreboard
    fiction, history = members["fiction"], members["history"]
    combat(fiction, history, report, (now - timedelta(days=1)).date())
    combat(fiction, history, report, (now - timedelta(days=1)).date())
    combat(history, fiction, report, now.date(), merit=0)
    context = get_season_context(fiction.manor, season.pk)
    sides = {side["key"]: side for side in context["factions"]}
    assert (sides["fiction"]["weekly_score"], sides["history"]["weekly_score"]) == (21, 8)
    assert (sides["fiction"]["victory_points"], sides["history"]["victory_points"]) == (10, 0)
    assert context["victory_days"] == {"fiction": 1, "history": 0}
    assert context["victory_min_days"] == 3
    assert context["weekly_campaign"]["current_ends_at"] > now
    season.closed = True
    season.winner = ""
    season.save(update_fields=["closed", "winner"])
    assert get_season_context(fiction.manor, season.pk)["season_winner_label"] == "有效对抗不足，未产生胜方"
    season.rules["campaign"]["population"]["enabled"] = False
    season.save(update_fields=["rules"])
    assert get_season_context(fiction.manor, season.pk)["season_winner_label"] == "双方战平"


def test_registration_uses_the_advertised_frozen_prestige(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    season = Season.objects.create(name="报名", starts_at=now, ends_at=now + timedelta(days=90), rules=rules)
    with transaction.atomic():
        a, _ = Manor.objects.get_or_create(user=user_factory())
        b, _ = Manor.objects.get_or_create(user=user_factory())
        a.prestige, b.prestige = 100, 90
        a.save(update_fields=["prestige"])
        b.save(update_fields=["prestige"])
        first = register_locked(season, a, "random", now)
        second = register_locked(season, b, "random", now)
        a.prestige = 5
        a.save(update_fields=["prestige"])
        b.prestige = 1000
        b.save(update_fields=["prestige"])
        assign_factions_locked(season, now + timedelta(days=3))
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.prestige_snapshot, second.prestige_snapshot) == (100, 90)
    assert (first.faction, second.faction) == ("fiction", "history")


@pytest.fixture
def archive(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    old = Season.objects.create(
        name="上一季", starts_at=now - timedelta(days=100), ends_at=now - timedelta(days=10), closed=True, rules=rules
    )
    current = Season.objects.create(
        name="本季", starts_at=old.ends_at, ends_at=old.ends_at + timedelta(days=90), rules=rules
    )
    manor, _ = Manor.objects.get_or_create(user=user_factory())
    member = SeasonMember.objects.create(season=old, manor=manor, faction="fiction")
    return old, current, manor, member, now


def test_previous_final_prize_is_visible_only_if_real_and_unclaimed(archive, user_factory):
    old, current, manor, member, now = archive
    member.final_rank = 1
    member.final_reward = {"items": {"archive_test_item": 3}}
    member.save(update_fields=["final_rank", "final_reward"])
    assert not previous_season_reward(manor, current, now)  # 物品目录缺失不显示可领
    ItemTemplate.objects.create(key="archive_test_item", name="奖励")
    result = previous_season_reward(manor, current, now)
    assert (result["season_id"], result["kind"]) == (old.pk, "排名奖励")
    assert result["url"].endswith(f"?season_id={old.pk}#season-rewards")
    outsider, _ = Manor.objects.get_or_create(user=user_factory())
    assert not previous_season_reward(outsider, current, now)
    SeasonClaim.objects.create(member=member, key="campaign:final")
    assert not previous_season_reward(manor, current, now)


def test_previous_milestones_and_existing_scroll_are_not_mislabelled(archive, monkeypatch):
    old, current, manor, member, now = archive
    first = old.rules["rewards"][0]
    ItemTemplate.objects.create(key=first["items"]["fiction"].keys().__iter__().__next__(), name="装备")
    member.merit = first["threshold"]
    member.save(update_fields=["merit"])
    assert previous_season_reward(manor, current, now)["kind"] == "功勋奖励"
    SeasonClaim.objects.create(member=member, key="reward:" + first["key"])
    member.scroll_available = True
    member.save(update_fields=["scroll_available"])
    monkeypatch.setattr("gameplay.selectors.season_archive.has_summon_catalog", lambda *args: True)
    result = previous_season_reward(manor, current, now)
    assert result["kind"] == "人物卷轴"
    assert result["url"].endswith("#season-summon")
    member.scroll_used = True
    member.save(update_fields=["scroll_used"])
    assert not previous_season_reward(manor, current, now)


def test_previous_week_reward_respects_personal_and_team_claims(archive, user_factory):
    old, current, manor, member, now = archive
    friend_manor, _ = Manor.objects.get_or_create(user=user_factory())
    friend = SeasonMember.objects.create(season=old, manor=friend_manor, faction="fiction")
    with transaction.atomic():
        states = ensure_factions_locked(old, old.starts_at)
    state = states["fiction"]
    state.economy_state = {
        "weekly_campaign": {"activated_at": old.starts_at.isoformat(), "weeks": {"1": {"target": 200}}}
    }
    state.save(update_fields=["economy_state"])
    SeasonWeek.objects.create(season=old, number=1, node_key="campaign", settled_at=old.starts_at + timedelta(days=7))
    for actor in (member, friend):
        stamp = old.starts_at + timedelta(days=1)
        SeasonAction.objects.create(
            member=actor,
            token=uuid4(),
            request_hash="test",
            kind="merit_credit",
            week=1,
            date=stamp.date(),
            result={"occurred_at": stamp.isoformat(), "category": "logistics", "merit": 100},
        )
    ItemTemplate.objects.create(key="experience_fruit", name="经验果")
    ItemTemplate.objects.create(key="xuan_tie_essence", name="玄铁")
    assert previous_season_reward(manor, current, now)["kind"] == "周奖励"
    SeasonClaim.objects.create(member=member, key="campaign:weekly:1:personal")
    assert previous_season_reward(manor, current, now)["kind"] == "周奖励"
    SeasonClaim.objects.create(member=member, key="campaign:weekly:1:team")
    assert not previous_season_reward(manor, current, now)
