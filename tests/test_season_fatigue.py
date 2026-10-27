"""胜方疲敝按出征累积，属性临时削减且保留真实生命。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.db import transaction

from battle.models import BattleReport
from gameplay.services.season import coalition_combat, expeditions
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.combat import proxies
from gameplay.services.season.fatigue import fatigue_snapshot
from tests.test_season_coalition_combat import coalition as _coalition  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def coalition(request):
    return request.getfixturevalue("_coalition")


def enable(run):
    season = run.member.season
    season.rules = {
        **season.rules,
        "campaign": {"fortifications": {"enabled": True, "fatigue": {"per_stack_percent": 5, "max_stacks": 2}}},
    }
    season.save(update_fields=["rules"])


def test_fatigue_uses_original_snapshot_preserves_bonuses_hp_and_does_not_compound():
    run = SimpleNamespace(
        result={"fatigue_stacks": 2},
        member=SimpleNamespace(
            season=SimpleNamespace(
                rules={
                    "campaign": {
                        "fortifications": {"enabled": True, "fatigue": {"per_stack_percent": 5, "max_stacks": 2}}
                    }
                }
            )
        ),
    )
    original = {
        "force": 100,
        "intellect": 200,
        "agility": 100,
        "defense_stat": 100,
        "archetype": "military",
        "attack": 150,
        "attack_attribute_component": 100,
        "defense": 100,
        "max_hp": 1000,
        "current_hp": 987,
    }
    before = deepcopy(original)
    reduced = fatigue_snapshot(run, original)
    assert [reduced[key] for key in ("force", "agility", "defense_stat")] == [90, 90, 90]
    assert reduced["intellect"] == 180
    assert reduced["attack"] - reduced["attack_attribute_component"] == 50
    assert (reduced["max_hp"], reduced["current_hp"]) == (1000, 987)
    assert reduced["defense"] == 90
    assert fatigue_snapshot(run, original) == reduced
    assert original == before
    run.result["fatigue_stacks"] = 100
    assert fatigue_snapshot(run, original) == reduced
    run.member.season.rules = {}
    assert fatigue_snapshot(run, original) == original


@pytest.mark.parametrize("winner", ["attacker", "defender"])
def test_only_actual_winning_survivors_gain_fatigue_next_battle(coalition, monkeypatch, winner):
    make, now = coalition
    attack, ag = make("fiction", [100], {})
    defense, dg = make("history", [100], {})
    reserve, _ = make("history", [100], {})
    enable(attack)
    defense.member.season = attack.member.season
    before = deepcopy(attack.lineup)
    seen = []

    def simulate(manor, prepared, seed):
        seen.append((prepared["attack"][0].force, prepared["defend"][0].force))
        assert prepared["attack"][0].current_hp == 80
        assert prepared["defend"][0].current_hp == 80
        return BattleReport(
            manor=manor,
            opponent_name="疲敝守军",
            winner=winner,
            starts_at=now,
            completed_at=now,
            attacker_team=[{"guest_id": ag[0].pk, "remaining_hp": 80}],
            defender_team=[{"guest_id": dg[0].pk, "remaining_hp": 80}],
            losses={},
        )

    monkeypatch.setattr(coalition_combat, "simulate_combat", simulate)
    for _ in range(3):
        with transaction.atomic():
            coalition_combat.fight_coalitions_locked(
                [attack],
                [defense],
                now,
                attacker_allocations={},
                defender_allocations={},
                commander_guest_limit=3,
            )
    victors = attack if winner == "attacker" else defense
    losers = defense if winner == "attacker" else attack
    assert victors.result["fatigue_stacks"] == 2
    assert victors.report.losses["season_effects"][winner][0]["fatigue_stacks"] == 2
    assert victors.report.losses["season_effects"][winner][0]["fatigue_percent"] == 10
    assert losers.result.get("fatigue_stacks", 0) == 0
    assert seen == ([(10, 10), (9, 10), (9, 10)] if winner == "attacker" else [(10, 10), (10, 9), (10, 9)])
    reserve.refresh_from_db()
    assert reserve.result.get("fatigue_stacks", 0) == 0 and reserve.battles == 0
    assert attack.lineup == before
    for guest in [*ag, *dg]:
        guest.refresh_from_db()
        assert guest.force == 10 and guest.current_hp == 80
    # 临时快照在实际代理路径仍保持生命，不会按降防后的值重新计算上限。
    reduced = fatigue_snapshot(victors, victors.lineup["guests"][0])
    proxy = proxies({"guests": [reduced]}, preserve_state=True)[0]
    assert proxy.max_hp == victors.lineup["guests"][0]["max_hp"]
    fresh, _ = make(victors.member.faction, [100], {})
    fresh.member.season = attack.member.season
    assert fatigue_snapshot(fresh, fresh.lineup["guests"][0]) == fresh.lineup["guests"][0]


def test_winning_team_with_no_survivors_gets_no_fatigue(coalition, monkeypatch):
    make, now = coalition
    attack, ag = make("fiction", [100], {})
    defense, dg = make("history", [100], {})
    enable(attack)
    defense.member.season = attack.member.season

    def simulate(manor, prepared, seed):
        return BattleReport(
            manor=manor,
            opponent_name="相互战败",
            winner="attacker",
            starts_at=now,
            completed_at=now,
            attacker_team=[{"guest_id": ag[0].pk, "remaining_hp": 0}],
            defender_team=[{"guest_id": dg[0].pk, "remaining_hp": 0}],
            losses={},
        )

    monkeypatch.setattr(coalition_combat, "simulate_combat", simulate)
    with transaction.atomic():
        coalition_combat.fight_coalitions_locked(
            [attack],
            [defense],
            now,
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
        )
    assert attack.result.get("fatigue_stacks", 0) == defense.result.get("fatigue_stacks", 0) == 0


def test_legacy_single_team_battle_applies_existing_fatigue_and_rewards_surviving_winner(coalition, monkeypatch):
    make, now = coalition
    attack, ag = make("fiction", [100], {})
    defense, dg = make("history", [100], {})
    attack.member.season.rules = {"campaign": load_campaign_rules()}
    enable(attack)
    defense.member.season = attack.member.season
    attack.result = {"fatigue_stacks": 1}
    attack.save(update_fields=["result"])
    monkeypatch.setattr(expeditions, "_record_combat", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        expeditions, "campaign_rules", lambda season: {"expeditions": {"defend_merit": 20, "max_battles": 3}}
    )
    seen = []

    def simulate(manor, prepared, seed):
        seen.append((prepared["attack"][0].force, prepared["defend"][0].force))
        return BattleReport(
            manor=manor,
            opponent_name="单队守军",
            winner="defender",
            starts_at=now,
            completed_at=now,
            attacker_team=[{"guest_id": ag[0].pk, "remaining_hp": 50}],
            defender_team=[{"guest_id": dg[0].pk, "remaining_hp": 70}],
            losses={},
        )

    monkeypatch.setattr(expeditions, "simulate_combat", simulate)
    with transaction.atomic():
        assert not expeditions._fight(attack, defense, now)
    assert seen == [(9, 10)]
    attack.refresh_from_db()
    defense.refresh_from_db()
    assert attack.result["fatigue_stacks"] == defense.result["fatigue_stacks"] == 1
    assert attack.lineup["guests"][0]["force"] == 10
    assert attack.lineup["guests"][0]["current_hp"] == 50
    assert defense.lineup["guests"][0]["current_hp"] == 70
    assert attack.report.losses["season_effects"]["attacker"][0]["fatigue_stacks"] == 1


def test_coalition_tower_integration_settles_saved_report_and_keeps_fatigue_metadata(coalition, monkeypatch):
    make, now = coalition
    attack, ag = make("fiction", [100], {})
    defense, dg = make("history", [100], {})
    enable(attack)
    defense.member.season = attack.member.season
    site = SimpleNamespace(owner="history")
    monkeypatch.setattr(coalition_combat, "tower_state", lambda _: {"hp": 300, "max_hp": 300})
    calls = []

    def prepare(prepared, supplied_site, faction):
        assert supplied_site is site and faction == "history"
        calls.append("prepare")
        return {"initial_hp": 300}

    def simulate(manor, prepared, seed):
        assert calls == ["prepare"]
        return BattleReport(
            manor=manor,
            opponent_name="箭塔协防",
            winner="attacker",
            starts_at=now,
            completed_at=now,
            attacker_team=[{"guest_id": ag[0].pk, "remaining_hp": 50}],
            defender_team=[{"guest_id": dg[0].pk, "remaining_hp": 0}],
            losses={},
        )

    def settle(supplied_site, report, snapshot):
        assert supplied_site is site and report.pk and snapshot["initial_hp"] == 300
        calls.append("settle")
        return {"remaining_hp": 250}

    monkeypatch.setattr(coalition_combat, "prepare_tower_support", prepare)
    monkeypatch.setattr(coalition_combat, "settle_tower_support_locked", settle)
    monkeypatch.setattr(coalition_combat, "simulate_combat", simulate)
    with transaction.atomic():
        report, _, _ = coalition_combat.fight_coalitions_locked(
            [attack],
            [defense],
            now,
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
            site=site,
        )
    report.refresh_from_db()
    assert calls == ["prepare", "settle"]
    assert report.losses["season_effects"]["tower"]["remaining_hp"] == 250
    assert report.losses["season_effects"]["attacker"][0]["fatigue_stacks"] == 0
    assert attack.result["fatigue_stacks"] == 1
