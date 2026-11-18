"""周战局边界、真实参与和共同奖励的回归。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport
from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonAction, SeasonMember
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.weekly_campaign import (
    activate_weekly_locked,
    claim_weekly_locked,
    freeze_current_targets_locked,
    record_site_ticks_locked,
    settle_campaign_weeks_locked,
    weekly_campaign_context,
    weekly_victory_scores,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def weekly(user_factory):
    start = timezone.now() - timedelta(days=15)
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["playability"] = {
        "enabled": True,
        "weekly": {
            "battle_points": {
                "attack_win": 8,
                "attack_loss": 2,
                "defense_win": 6,
                "defense_loss": 2,
                "intercept_win": 4,
                "intercept_loss": 1,
            },
            "frontier_points": 3,
        },
    }
    season = Season.objects.create(name="周战局", starts_at=start, ends_at=start + timedelta(days=90), rules=rules)
    members = {}
    for faction in ("fiction", "history"):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        members[faction] = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
    with transaction.atomic():
        states = ensure_factions_locked(season, start)
        activate_weekly_locked(season, states, start)
    return season, members, start


def credit(member, at, merit=100, category="logistics"):
    return SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="test",
        kind="merit_credit",
        date=at.date(),
        result={"merit": merit, "category": category, "occurred_at": at.isoformat()},
    )


def contest(members, at):
    report = BattleReport.objects.create(
        manor=members["fiction"].manor,
        opponent_name="历史",
        winner="attacker",
        starts_at=at,
        completed_at=at,
    )
    SeasonAction.objects.create(
        member=members["fiction"],
        opponent=members["history"],
        report=report,
        token=uuid4(),
        request_hash="test",
        kind="combat",
        date=at.date(),
        result={"occurred_at": at.isoformat(), "requested_merit": 20},
    )


def test_ticks_split_at_week_boundary_without_map_reset(weekly):
    season, _, start = weekly
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 8 * 24, 3600, 10, start)
    state.refresh_from_db()
    weeks = state.economy_state["weekly_campaign"]["weeks"]
    assert weeks["1"]["score"] == 7 * 24 * 10
    assert weeks["2"]["score"] == 24 * 10


def test_weekly_victory_requires_both_participants_and_actual_pvp(weekly):
    season, members, start = weekly
    credit(members["fiction"], start + timedelta(days=1))
    credit(members["history"], start + timedelta(days=1))
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 24, 3600, 10, start)
        assert settle_campaign_weeks_locked(season, start + timedelta(days=7)) == 1
        assert settle_campaign_weeks_locked(season, start + timedelta(days=7)) == 0
    assert season.weeks.get(number=1).winner == ""
    assert weekly_victory_scores(season) == {"fiction": 0, "history": 0}


def test_qualified_draw_and_win_have_bounded_points(weekly):
    season, members, start = weekly
    for day in (1, 8):
        for member in members.values():
            credit(member, start + timedelta(days=day))
        contest(members, start + timedelta(days=day))
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(days=8), 1, 3600, 1000000, start)
        settle_campaign_weeks_locked(season, start + timedelta(days=14))
    assert season.weeks.get(number=1).winner == ""
    assert season.weeks.get(number=2).winner == "fiction"
    assert weekly_victory_scores(season) == {"fiction": 15, "history": 5}


def test_participation_rewards_include_logistics_exclude_idle_and_are_bound_once(weekly, monkeypatch):
    season, members, start = weekly
    member = members["fiction"]
    credit(member, start + timedelta(days=1), 200)
    # 战报展示同一份贡献不能重复累计，也不能替代正式入账。
    SeasonAction.objects.create(
        member=members["history"],
        token=uuid4(),
        request_hash="idle",
        kind="dispatch",
        date=(start + timedelta(days=1)).date(),
        result={"merit": 9999},
    )
    with transaction.atomic():
        freeze_current_targets_locked(season, start + timedelta(days=2))
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    now = start + timedelta(days=7, minutes=1)
    before = dict(season.faction_states.get(faction="fiction").economy_state)
    context = weekly_campaign_context(season, member, now)
    row = context["weeks"][0]
    assert row["merit"] == 200
    assert row["personal_claimable"] and row["team_claimable"]
    assert season.faction_states.get(faction="fiction").economy_state == before
    from gameplay.services.season import actions

    granted = []
    monkeypatch.setattr(actions, "_grant", lambda manor, items: granted.append(items))
    monkeypatch.setattr(timezone, "now", lambda: now)
    with transaction.atomic():
        result = claim_weekly_locked(member, member.manor, 1)
    assert result["rewards"] == ["personal", "team"]
    assert granted == [{"experience_fruit": 5, "xuan_tie_essence": 3}]
    with transaction.atomic(), pytest.raises(GameError, match="已领取"):
        claim_weekly_locked(member, member.manor, 1)
    assert len(granted) == 1
    assert weekly_campaign_context(season, members["history"], now)["weeks"][0]["merit"] == 0


def test_midseason_activation_preserves_old_score_and_skips_earlier_ticks(weekly):
    season, _, start = weekly
    states = {state.faction: state for state in season.faction_states.all()}
    with transaction.atomic():
        for state in states.values():
            state.economy_state = {}
            state.score = 12345
            state.save(update_fields=["economy_state", "score"])
        activated = activate_weekly_locked(season, states, start + timedelta(days=10))
        state = states["fiction"]
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 12 * 24, 3600, 10, activated)
    state.refresh_from_db()
    book = state.economy_state["weekly_campaign"]
    assert state.score == 12345 and book["legacy_score"] == 12345
    assert "1" not in book["weeks"]
    assert book["weeks"]["2"]["score"] == 2 * 24 * 10


def test_final_tie_uses_weekly_points_even_when_raw_scores_differ(weekly):
    from gameplay.models.season import SeasonWeek
    from gameplay.services.season.lifecycle import settle_season

    season, _, start = weekly
    season.rules["campaign"]["population"]["enabled"] = False
    season.save(update_fields=["rules"])
    with transaction.atomic():
        for state in season.faction_states.all():
            state.economy_state["weekly_campaign"]["weeks"] = {"1": {"victory_points": 10}}
            state.score = 1000000 if state.faction == "fiction" else 0
            state.save(update_fields=["economy_state", "score"])
        SeasonWeek.objects.create(season=season, number=1, node_key="campaign", settled_at=start + timedelta(days=7))
    settle_season(season.pk, season.ends_at)
    season.refresh_from_db()
    assert season.closed and season.winner == ""
    assert season.fiction_score > season.history_score
    assert settle_season(season.pk, season.ends_at) == {"weeks": 0, "closed": 0}


def test_final_ranking_changes_only_small_configured_bonus(weekly):
    from gameplay.services.season.campaign_rewards import freeze_rankings_locked

    season, members, _ = weekly
    member = members["fiction"]
    member.merit = 1000
    member.save(update_fields=["merit"])
    with transaction.atomic():
        freeze_rankings_locked(season)
    member.refresh_from_db()
    assert member.final_reward["items"] == {"experience_fruit": 30, "xuan_tie_essence": 24}
    assert member.merit == 1000


def test_battle_events_add_to_weekly_score_and_can_decide_the_week(weekly):
    from gameplay.services.season.weekly_campaign import record_battle_event_locked

    season, members, start = weekly
    for member in members.values():
        credit(member, start + timedelta(days=1))
    contest(members, start + timedelta(days=1))
    with transaction.atomic():
        record_battle_event_locked(season, "fiction", start + timedelta(days=1), 8)
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    week = season.weeks.get(number=1)
    assert (week.fiction_points, week.history_points, week.winner) == (8, 0, "fiction")
    assert weekly_victory_scores(season) == {"fiction": 10, "history": 0}


def test_uncontested_week_grants_frontier_points_to_the_active_faction(weekly):
    season, members, start = weekly
    credit(members["fiction"], start + timedelta(days=1))
    with transaction.atomic():
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
    week = season.weeks.get(number=1)
    assert (week.fiction_points, week.history_points, week.winner) == (0, 0, "")
    assert weekly_victory_scores(season) == {"fiction": 3, "history": 0}
    state = season.faction_states.get(faction="fiction")
    row = state.economy_state["weekly_campaign"]["weeks"]["1"]
    assert row["frontier"] is True and row["victory_points"] == 3


def test_absent_faction_allows_dominance_win_for_the_active_faction(weekly):
    from gameplay.services.season.lifecycle import settle_season

    season, members, _ = weekly
    fiction = members["fiction"]
    fiction.merit = 100
    fiction.save(update_fields=["merit"])
    credit(fiction, season.ends_at - timedelta(days=1))
    with transaction.atomic():
        settle_season(season.pk, season.ends_at)
    season.refresh_from_db()
    assert season.closed
    assert season.winner == "fiction"
    assert season.population["victory_result"]["dominance"] is True
    assert season.population["victory_result"]["qualified"] is True
    winner = season.members.get(faction="fiction")
    winner.refresh_from_db()
    assert winner.final_reward["winner_bonus"] is False


def test_legacy_weekly_snapshot_does_not_get_new_battle_or_frontier_rules(weekly):
    from gameplay.services.season.lifecycle import settle_season
    from gameplay.services.season.weekly_campaign import record_battle_event_locked

    season, members, start = weekly
    playability = season.rules["campaign"]["playability"]
    playability["weekly"].pop("battle_points")
    playability["weekly"].pop("frontier_points")
    season.save(update_fields=["rules"])
    credit(members["fiction"], season.ends_at - timedelta(days=1))
    with transaction.atomic():
        record_battle_event_locked(season, "fiction", start + timedelta(days=1), 8)
        settle_campaign_weeks_locked(season, start + timedelta(days=7))
        state = season.faction_states.get(faction="fiction")
        row = state.economy_state["weekly_campaign"]["weeks"].get("1", {})
        assert row.get("battle_score", 0) == 0
        assert row.get("frontier") is not True
        assert weekly_victory_scores(season) == {"fiction": 0, "history": 0}
        settle_season(season.pk, season.ends_at)
    season.refresh_from_db()
    assert season.winner == ""
