"""战报清理后，真实对抗仍计入赛季和延迟结算的周战局。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction

from gameplay.models import SeasonAction
from gameplay.selectors.season import get_season_context
from gameplay.services.season.contests import victory_contested_days
from gameplay.services.season.lifecycle import settle_season
from gameplay.services.season.population import victory_qualified
from gameplay.services.season.weekly_campaign import record_site_ticks_locked, settle_campaign_weeks_locked
from tests.test_season_rallies import army_payload, command, create_rally, launch
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401
from tests.test_season_score_archive import scoreboard as _scoreboard  # noqa: F401
from tests.test_season_weekly_campaign import credit
from tests.test_season_weekly_campaign import weekly as _weekly  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def scoreboard(request):
    return request.getfixturevalue("_scoreboard")


@pytest.fixture
def weekly(request):
    return request.getfixturevalue("_weekly")


@pytest.fixture
def rally_world(request):
    return request.getfixturevalue("_rally_world")


def battle(member, opponent, report, at, *, evidence=True, requested=20):
    return SeasonAction.objects.create(
        member=member,
        opponent=opponent,
        report=report,
        token=uuid4(),
        request_hash="settled-expedition" if evidence else "site-progress",
        kind="combat",
        date=at.date(),
        result={
            "occurred_at": at.isoformat(),
            "requested_merit": requested,
            **({"report_url": f"/battle/report/{report.pk}/"} if evidence and report else {}),
        },
    )


def test_cleanup_retains_both_faction_days_but_does_not_count_fake_contests(scoreboard):
    season, members, report, now = scoreboard
    fiction, history = members["fiction"], members["history"]
    for offset in range(3):
        at = now - timedelta(days=offset)
        battle(fiction, history, report, at)
        battle(history, fiction, report, at)
    # 即使流水有贡献，同阵营、没有战报证据以及无有效贡献都不是有效对抗。
    at = now - timedelta(days=4)
    battle(fiction, fiction, report, at)
    battle(fiction, history, None, at, evidence=False)
    battle(fiction, history, report, at, requested=0)
    assert victory_qualified(season)
    report.delete()
    assert victory_contested_days(season) == {"fiction": 3, "history": 3}
    assert victory_qualified(season)
    context = get_season_context(fiction.manor, season.pk)
    assert context["victory_days"] == {"fiction": 3, "history": 3}


def test_week_settlement_after_report_cleanup_uses_original_battle_evidence(weekly):
    from battle.models import BattleReport

    season, members, start = weekly
    at = start + timedelta(days=1)
    for member in members.values():
        credit(member, at)
    report = BattleReport.objects.create(
        manor=members["fiction"].manor, opponent_name="历史", winner="attacker", starts_at=at, completed_at=at
    )
    battle(members["fiction"], members["history"], report, at)
    report.delete()
    with transaction.atomic():
        state = season.faction_states.get(faction="fiction")
        record_site_ticks_locked(season, state, start + timedelta(hours=1), 1, 3600, 100, start)
        settle_campaign_weeks_locked(season, start + timedelta(days=15))
    assert season.weeks.get(number=1).winner == "fiction"
    state.refresh_from_db()
    assert state.economy_state["weekly_campaign"]["weeks"]["1"]["contested_days"] == 1


def test_real_combat_writes_stable_markers_for_both_sides(rally_world):
    from gameplay.models import SeasonExpedition
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defender, guests = player("history", force=1000, agility=1000)
    defense = command(defender, "dispatch", army_payload(guests, mode="garrison", troops=0))
    guard = SeasonExpedition.objects.get(pk=defense["run_id"])
    assert process_expedition(guard.pk, guard.due_at)
    clock[0] = guard.due_at
    attacker, guests = player(force=1, hp=1)
    rally, run = create_rally(attacker, guests, troops=0)
    launch(rally, run)
    assert process_expedition(run.pk, rally.due_at)
    rows = list(SeasonAction.objects.filter(member__season=season, kind="combat", opponent__isnull=False))
    assert len(rows) == 2 and all(row.result["pvp_battle"] is True for row in rows)
    for row in rows:
        row.result.pop("report_url", None)
        row.save(update_fields=["result"])
    rows[0].report.delete()
    assert victory_contested_days(season) == {"fiction": 1, "history": 1}


def test_closed_result_freezes_qualification_and_display(scoreboard):
    season, members, report, now = scoreboard
    for offset in range(3):
        for faction, enemy in (("fiction", "history"), ("history", "fiction")):
            battle(members[faction], members[enemy], report, now - timedelta(days=offset))
    settle_season(season.pk, season.ends_at)
    season.refresh_from_db()
    assert season.closed
    assert season.population["victory_result"] == {"qualified": True, "days": {"fiction": 3, "history": 3}}
    # 即使历史流水后来变化，已归档的结局和天数也保持关闭时的判定。
    SeasonAction.objects.filter(member__season=season, kind="combat").delete()
    context = get_season_context(members["fiction"].manor, season.pk)
    assert context["victory_days"] == {"fiction": 3, "history": 3}
    assert context["season_winner_label"] != "有效对抗不足，未产生胜方"
