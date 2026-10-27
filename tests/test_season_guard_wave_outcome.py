"""守军波次与仍存活的箭塔分开判定，城门战和历史贡献保持兼容。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse

from battle.season_outcome import cleared_guard_wave
from gameplay.models import Message
from gameplay.services.season import coalition_combat
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.population import npc_profile
from tests.test_season_rallies import army_payload, command
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    season, player, clock = request.getfixturevalue("_rally_world")
    rules = season.rules["campaign"]
    rules["fortifications"]["enabled"] = True
    rules["fortifications"]["hitpoints"]["enabled"] = True
    rules["population"]["enabled"] = True
    rules["playability"]["enabled"] = True
    season.population = {"mode": "standard", "factions": {"fiction": {"count": 10}, "history": {"count": 10}}}
    season.save(update_fields=["rules", "population"])
    site = season.sites.get(key="mountain:gate")
    profile = npc_profile(season, site.key)
    site.npc_profile = profile
    site.guards, site.durability, site.owner, site.defenses = profile["guards"], profile["durability"], "", {}
    site.save()
    return season, player, clock, site


def _attack(member, guests):
    from gameplay.models import SeasonExpedition, SeasonRally

    payload = {**army_payload(guests, mode="attack", troops=200), "rally_target_members": 1}
    result = command(member, "rally_create", payload)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    run.seed = 123
    run.save(update_fields=["seed"])
    return SeasonRally.objects.get(pk=result["rally_id"]), run


def test_real_npc_guard_clear_with_surviving_tower_is_attack_win_and_continues(world, monkeypatch):
    season, player, _, site = world
    # 对应本地布衣模板，而不是模型测试默认100属性。
    from guests.models import GuestTemplate

    GuestTemplate.objects.filter(key="base_black_military").update(
        base_attack=29,
        base_intellect=8,
        base_defense=24,
        base_agility=20,
        base_luck=25,
        base_hp=100,
        growth_range=[1, 3],
        attribute_weights={"force": 45, "intellect": 15, "defense": 25, "agility": 15},
    )
    member, guests = player(force=100, count=3, agility=100)
    rally, run = _attack(member, guests)
    original = coalition_combat.simulate_combat

    def force_engine_tower_result(*args, **kwargs):
        report = original(*args, **kwargs)
        assert cleared_guard_wave(report)  # 真实战斗清军，塔和进攻门客仍存活。
        report.winner = "defender"  # 稳定复现引擎以残存箭塔判负。
        return report

    monkeypatch.setattr(coalition_combat, "simulate_combat", force_engine_tower_result)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    rally.refresh_from_db()
    site.refresh_from_db()
    assert run.report.winner == "attacker"
    assert run.report.losses["season_effects"]["guard_wave_cleared"] is True
    assert site.guards == 2 and rally.status == "marching"
    assert run.result.get("fatigue_stacks") == 1
    action = member.actions.get(report=run.report, kind="combat")
    assert action.result["requested_merit"] == 30
    mail = Message.objects.get(manor=member.manor, battle_report=run.report)
    assert "本场结果：胜利" in mail.body
    assert "本波守军已击退，箭塔仍在；后续以集结状态为准" in mail.body
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("battle:report_detail", args=[run.report_id])).content.decode()
    assert "本波守军已击退，箭塔仍在；后续以集结状态为准" in html
    assert "胜败乃兵家常事" not in html


def test_old_report_is_read_only_and_uses_historical_guard_wave_evidence(world, monkeypatch):
    from gameplay.models import SeasonAction

    _, player, _, _ = world
    member, guests = player(force=100, count=3, agility=100)
    rally, run = _attack(member, guests)
    original = coalition_combat.simulate_combat

    def battle(*args, **kwargs):
        report = original(*args, **kwargs)
        assert cleared_guard_wave(report)
        report.winner = "defender"
        return report

    monkeypatch.setattr(coalition_combat, "simulate_combat", battle)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    report = run.report
    effects = deepcopy(report.losses)
    effects["season_effects"].pop("guard_wave_cleared")
    report.losses, report.winner = effects, "defender"
    report.save(update_fields=["losses", "winner"])
    action = SeasonAction.objects.get(report=report, kind="combat")
    before = deepcopy(action.result)
    client = Client()
    client.force_login(member.manor.user)
    html = client.get(reverse("battle:report_detail", args=[report.pk])).content.decode()
    assert "本波守军已击退，箭塔仍在；后续以集结状态为准" in html
    assert "胜败乃兵家常事" not in html
    report.refresh_from_db()
    action.refresh_from_db()
    assert report.winner == "defender" and action.result == before


def test_real_player_guards_are_defeated_even_if_arrow_tower_survives(world, monkeypatch):
    from gameplay.models import SeasonExpedition

    season, player, clock, site = world
    site.owner = "history"
    site.save(update_fields=["owner"])
    defender, guards = player("history", count=1, force=1, hp=1, agility=1000)
    stock = season.faction_states.get(faction="history")
    stock.troops = {**stock.troops, "dao_sheng": 100}
    stock.save(update_fields=["troops"])
    result = command(defender, "dispatch", army_payload(guards, mode="garrison", troops=100))
    guard = SeasonExpedition.objects.get(pk=result["run_id"])
    assert process_expedition(guard.pk, guard.due_at)
    clock[0] = guard.due_at
    attacker, guests = player(force=100, count=3, agility=100)
    rally, run = _attack(attacker, guests)
    original = coalition_combat.simulate_combat

    def battle(*args, **kwargs):
        report = original(*args, **kwargs)
        assert cleared_guard_wave(report)
        report.winner = "defender"
        return report

    monkeypatch.setattr(coalition_combat, "simulate_combat", battle)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    guard.refresh_from_db()
    rally.refresh_from_db()
    assert run.report.winner == "attacker" and rally.status == "marching"
    assert guard.status == "returning" and run.result.get("fatigue_stacks") == 1
    # 护院存活不再独立形成另一波；随已战败门客队伍带着实际剩余兵力返程。
    remaining = 100 - run.report.losses["defender"]["troops_lost"]
    assert remaining > 0 and guard.troops["dao_sheng"] == remaining
    assert attacker.actions.get(report=run.report, kind="combat").result["requested_merit"] == 30
    assert defender.actions.get(report=run.report, kind="combat").result["requested_merit"] == 0
    assert "本场结果：失利" in Message.objects.get(manor=defender.manor, battle_report=run.report).body


@pytest.mark.parametrize("case", ["attacker_dead", "defender_alive", "empty_defender", "tower_dead", "gate"])
def test_do_not_reclassify_gate_or_unresolved_and_mutual_destruction(case):
    report = SimpleNamespace(
        attacker_team=[{"guest_id": 1, "remaining_hp": 1}],
        defender_team=[{"guest_id": 2, "remaining_hp": 0}],
        defender_city_defenses=[{"key": "arrow_tower", "hp": 1}],
    )
    if case == "attacker_dead":
        report.attacker_team[0]["remaining_hp"] = 0
    elif case == "defender_alive":
        report.defender_team[0]["remaining_hp"] = 1
    elif case == "empty_defender":
        report.defender_team = []
    elif case == "tower_dead":
        report.defender_city_defenses[0]["hp"] = 0
    else:
        report.defender_city_defenses.append({"key": "wall", "hp": 1})
    assert not cleared_guard_wave(report)
