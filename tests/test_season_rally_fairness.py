"""赛季集结空跑退额与真实参战守军轮换。"""

from datetime import date, timedelta

import pytest

from gameplay.models import SeasonExpedition
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.quotas import daily_dispatches_used, guest_dispatch_counts
from tests.test_season_rallies import army_payload, command, create_rally, join_rally, launch
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def rally_world(request):
    return request.getfixturevalue("_rally_world")


@pytest.mark.parametrize("change", ["friendly", "protected"])
def test_launched_rally_reimburses_each_unfought_member_once_after_return(rally_world, change):
    season, player, _clock = rally_world
    leader, leader_guests = player()
    member, member_guests = player()
    rally, first = create_rally(leader, leader_guests)
    second = join_rally(member, member_guests, rally)
    launch(rally, first)
    second.refresh_from_db()
    site = season.sites.get(key=rally.node_key)
    if change == "friendly":
        site.owner = leader.faction
    else:
        site.protected_until = first.due_at + timedelta(hours=1)
    site.save(update_fields=["owner", "protected_until"])

    assert process_expedition(first.pk, first.due_at)
    for member, guests, run in [(leader, leader_guests, first), (member, member_guests, second)]:
        run.refresh_from_db()
        assert run.status == "returning" and run.battles == 0 and run.report_id is None
        assert run.result["refund_quota"] is True
        assert "归队后退回" in run.result["message"]
        quota_date = date.fromisoformat(run.result["quota_date"])
        assert daily_dispatches_used(member, quota_date) == 1
        assert process_expedition(run.pk, run.due_at)
        run.refresh_from_db()
        member.refresh_from_db()
        assert run.status == "completed" and run.result["quota_refunded"] is True
        assert run.result["returned_troops"] == {"dao_sheng": 10}
        assert daily_dispatches_used(member, quota_date) == 0
        assert guest_dispatch_counts(season, quota_date).get(guests[0].pk, 0) == 0
        assert member.troops_drawn == 0
        assert member.actions.filter(kind="dispatch_refund", result__run_id=run.pk).count() == 1
        assert not process_expedition(run.pk, run.due_at)
        assert member.actions.filter(kind="dispatch_refund", result__run_id=run.pk).count() == 1


@pytest.mark.parametrize("legacy_report_unavailable", [False, True])
def test_prior_battle_is_not_refunded_if_target_changes_before_next_wave(rally_world, legacy_report_unavailable):
    season, player, clock = rally_world
    season.rules["campaign"]["expeditions"]["station_seconds"] = 7200
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defender, guards = player("history", force=1, hp=1)
    defense = command(defender, "dispatch", army_payload(guards, mode="garrison", troops=0))
    guard_run = SeasonExpedition.objects.get(pk=defense["run_id"])
    assert process_expedition(guard_run.pk, guard_run.due_at)
    clock[0] = guard_run.due_at
    attacker, guests = player(force=8000)
    rally, run = create_rally(attacker, guests)
    launch(rally, run)
    assert process_expedition(run.pk, rally.due_at)
    run.refresh_from_db()
    rally.refresh_from_db()
    assert run.status == "outbound" and run.battles == 1 and run.report_id
    if legacy_report_unavailable:
        # 历史战报可能被清理，不能仅因为引用为空就退回已参战额度。
        run.report = None
        run.save(update_fields=["report"])
    quota_date = date.fromisoformat(run.result["quota_date"])
    site.owner = attacker.faction
    site.save(update_fields=["owner"])
    assert process_expedition(run.pk, rally.due_at)
    run.refresh_from_db()
    assert run.status == "returning" and not run.result.get("refund_quota")
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    attacker.refresh_from_db()
    assert run.status == "completed" and not run.result.get("quota_refunded")
    assert daily_dispatches_used(attacker, quota_date) == 1
    assert guest_dispatch_counts(season, quota_date)[guests[0].pk] == 1
    assert attacker.troops_drawn == 10


def test_insufficient_food_does_not_count_a_waiting_garrison_as_fought(rally_world):
    season, player, clock = rally_world
    season.rules["campaign"]["expeditions"]["station_seconds"] = 7200
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defender, guards = player("history", force=1000)
    defense = command(defender, "dispatch", army_payload(guards, mode="garrison", troops=0))
    guard = SeasonExpedition.objects.get(pk=defense["run_id"])
    assert process_expedition(guard.pk, guard.due_at)
    guard.refresh_from_db()
    clock[0] = guard.launched_at + timedelta(seconds=guard.travel_seconds)
    site.refresh_from_db()
    site.supplies = {"grain": 0, "wood": 0, "iron": 0}
    site.supply_updated_at = clock[0]
    site.save(update_fields=["supplies", "supply_updated_at"])
    season.rules["campaign"]["activities"]["enabled"] = True
    season.save(update_fields=["rules"])
    attacker, guests = player(force=8000)
    rally, run = create_rally(attacker, guests, troops=0)
    launch(rally, run)
    assert process_expedition(run.pk, rally.due_at)
    guard.refresh_from_db()
    assert guard.status == "returning"
    assert guard.battles == 0 and guard.report_id is None
    assert "last_defended_at" not in guard.result


@pytest.mark.parametrize("hitpoints_enabled", [False, True])
def test_successive_single_player_attacks_rotate_real_garrisons(rally_world, monkeypatch, hitpoints_enabled):
    from gameplay.services.season import hitpoint_waves, rallies

    season, player, clock = rally_world
    season.rules["campaign"]["expeditions"]["station_seconds"] = 7200
    season.rules["campaign"]["fortifications"]["enabled"] = hitpoints_enabled
    season.rules["campaign"]["fortifications"]["hitpoints"]["enabled"] = hitpoints_enabled
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defenders = []
    for _ in range(3):
        member, guests = player("history", force=1000, agility=1000)
        result = command(member, "dispatch", army_payload(guests, mode="garrison", troops=0))
        run = SeasonExpedition.objects.get(pk=result["run_id"])
        assert process_expedition(run.pk, run.due_at)
        run.refresh_from_db()
        defenders.append(run)
    clock[0] = max(run.launched_at + timedelta(seconds=run.travel_seconds) for run in defenders)
    observed = []
    original = rallies.fight_coalitions_locked

    def observe(attackers, enemy_runs, now, **kwargs):
        observed.append([row.pk for row in enemy_runs])
        return original(attackers, enemy_runs, now, **kwargs)

    monkeypatch.setattr(rallies, "fight_coalitions_locked", observe)
    monkeypatch.setattr(hitpoint_waves, "fight_coalitions_locked", observe)
    for i in range(4):
        attacker, guests = player(force=1, hp=1)
        rally, run = create_rally(attacker, guests, troops=0)
        launch(rally, run)
        assert process_expedition(run.pk, rally.due_at)
        run.refresh_from_db()
        assert run.report and run.report.winner == "defender"
        clock[0] += timedelta(seconds=1)

    assert observed == [[run.pk] for run in [*defenders, defenders[0]]]
    for run in defenders:
        run.refresh_from_db()
        assert run.status == "stationed"
        assert run.result["last_defended_at"]
    assert [SeasonExpedition.objects.get(pk=run.pk).battles for run in defenders] == [2, 1, 1]
