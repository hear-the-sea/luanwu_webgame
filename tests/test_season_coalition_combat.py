"""集结军主将、前线聚合与按投入分摊真实战损。"""

from copy import deepcopy
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.season import coalition_combat
from gameplay.services.season.combat import pve_lineup
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def coalition(user_factory):
    now = timezone.now()
    season = Season.objects.create(name="集结军测试", starts_at=now, ends_at=now + timedelta(days=90))
    template = GuestTemplate.objects.create(
        key="base_black_military", name="集结门客", rarity="black", archetype="military"
    )
    TroopTemplate.objects.update_or_create(
        key="dao_ke", defaults={"name": "刀客", "base_attack": 6, "base_defense": 3, "base_hp": 18}
    )

    def make(faction, strengths, troops):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        guests = [
            Guest.objects.create(manor=manor, template=template, status=GuestStatus.DEPLOYED, current_hp=80, force=10)
            for _strength in strengths
        ]
        snapshots = build_guest_battle_snapshots(guests)
        for snapshot, strength in zip(snapshots, strengths, strict=True):
            snapshot["attack"] = strength
        run = SeasonExpedition.objects.create(
            member=member,
            node_key="mountain:gate",
            kind="attack" if faction == "fiction" else "garrison",
            status="outbound",
            guest_ids=[guest.pk for guest in guests],
            lineup={"guests": snapshots},
            troops=troops,
            initial_troops=troops,
            technology={"dao_attack": 1},
            launched_at=now,
            due_at=now + timedelta(minutes=10),
            seed=10,
        )
        run.guests.set(guests)
        return run, guests

    return make, now


def test_coalition_front_line_casualties_and_each_players_guests_are_applied_without_touching_reserves(
    coalition, monkeypatch
):
    make, now = coalition
    a, ag = make("fiction", [1000, 900], {"dao_ke": 100})
    support, sg = make("fiction", [20], {"dao_ke": 200})
    rear, rg = make("fiction", [10], {"dao_ke": 100})
    hero_only, hg = make("fiction", [10], {})
    d, dg = make("history", [500, 400], {"dao_ke": 60})
    ds, dsg = make("history", [10], {"dao_ke": 40})
    old_lineup = deepcopy(a.lineup)
    Guest.objects.filter(pk=ag[0].pk).update(current_hp=13)
    captured = {}

    def simulate(manor, prepared, seed):
        captured.update(prepared=prepared["prepared"])
        assert prepared["attack"][0].current_hp == 13
        return BattleReport(
            manor=manor,
            opponent_name="联合驻军",
            winner="defender",
            starts_at=now,
            completed_at=now,
            attacker_team=[
                {"guest_id": ag[0].pk, "remaining_hp": 0},
                {"guest_id": sg[0].pk, "remaining_hp": 5},
                {"guest_id": hg[0].pk, "remaining_hp": 6},
            ],
            defender_team=[
                {"guest_id": dg[0].pk, "remaining_hp": 7},
                {"guest_id": dsg[0].pk, "remaining_hp": 9},
            ],
            losses={
                "attacker": {"casualties": [{"key": "dao_ke", "lost": 11}]},
                "defender": {"casualties": [{"key": "dao_ke", "lost": 3}]},
            },
        )

    monkeypatch.setattr(coalition_combat, "simulate_combat", simulate)
    with transaction.atomic():
        report, attacker_id, defender_id = coalition_combat.fight_coalitions_locked(
            [a, support, hero_only],
            [d, ds],
            now,
            attacker_allocations={a.pk: {"dao_ke": 30}, support.pk: {"dao_ke": 70}},
            defender_allocations={d.pk: {"dao_ke": 10}, ds.pk: {"dao_ke": 10}},
            commander_guest_limit=1,
        )
    assert (attacker_id, defender_id) == (a.pk, d.pk)
    assert report.battle_type == "season" and report.pk
    front = captured["prepared"]
    assert {key: count for key, count in front.attacker_loadout.items() if count} == {"dao_ke": 100}
    assert {key: count for key, count in front.defender_loadout.items() if count} == {"dao_ke": 20}
    assert len(front.attacker_guests) == 3 and len(front.defender_guests) == 2
    for run, remaining, lost in ((a, 97, 3), (support, 192, 8), (d, 58, 2), (ds, 39, 1)):
        run.refresh_from_db()
        assert run.troops == {"dao_ke": remaining}
        assert run.result["coalition_losses"] == {"dao_ke": lost}
        assert run.battles == 1 and run.report_id == report.pk
    rear.refresh_from_db()
    assert rear.battles == 0 and rear.troops == {"dao_ke": 100} and rear.report_id is None
    a.refresh_from_db()
    assert a.result["defeated_ids"] == [ag[0].pk]
    assert a.lineup["guests"][0]["troop_capacity"] == old_lineup["guests"][0]["troop_capacity"]
    for guest in (ag[1], *rg, dg[1]):
        guest.refresh_from_db()
        assert guest.current_hp == 80
    ag[0].refresh_from_db()
    dg[0].refresh_from_db()
    assert ag[0].current_hp == 1 and dg[0].current_hp == 7
    assert all(guest.status == GuestStatus.DEPLOYED for guest in (*ag, *dg))
    for guest, remaining in ((sg[0], 5), (dsg[0], 9), (hg[0], 6)):
        guest.refresh_from_db()
        assert guest.current_hp == remaining
    hero_only.refresh_from_db()
    assert hero_only.battles == 1 and hero_only.report_id == report.pk
    assert hero_only.result["coalition_front_troops"] == {}


def test_coalition_npc_battle_uses_real_simulation_and_guests_from_each_selected_player(coalition):
    make, now = coalition
    a, guests = make("fiction", [1000, 100], {"dao_ke": 100})
    support, support_guests = make("fiction", [50], {"dao_ke": 100})
    npc = pve_lineup("mountain:inn", "守军", 0, 10)
    with transaction.atomic():
        report, attacker_id, defender_id = coalition_combat.fight_coalitions_locked(
            [a, support],
            [],
            now,
            attacker_allocations={a.pk: {"dao_ke": 20}, support.pk: {"dao_ke": 30}},
            defender_allocations={},
            commander_guest_limit=1,
            npc_lineup=npc,
        )
    assert report.rounds
    assert {key: count for key, count in report.attacker_troops.items() if count} == {"dao_ke": 50}
    assert len(report.attacker_team) == 2
    assert attacker_id == a.pk and defender_id is None
    guests[1].refresh_from_db()
    support_guests[0].refresh_from_db()
    assert guests[1].current_hp == 80
    entry = next(row for row in report.attacker_team if row["guest_id"] == support_guests[0].pk)
    assert support_guests[0].current_hp == max(1, entry["remaining_hp"])
    for run, sent in ((a, 20), (support, 30)):
        run.refresh_from_db()
        assert 100 - sent <= run.troops["dao_ke"] <= 100


@pytest.mark.parametrize(
    "invalid", ["overdraw", "negative", "wrong_status", "duplicate", "same_faction", "capacity", "defender_capacity"]
)
def test_coalition_rejects_invalid_participants_before_any_report_or_loss(coalition, invalid):
    make, now = coalition
    a, _ag = make("fiction", [100], {"dao_ke": 10})
    d, dg = make("history", [100], {"dao_ke": 10})
    amount = 11 if invalid == "overdraw" else -1 if invalid == "negative" else 5
    if invalid == "wrong_status":
        Guest.objects.filter(pk=dg[0].pk).update(status=GuestStatus.IDLE)
    if invalid == "same_faction":
        d.member.faction = "fiction"
        d.member.save(update_fields=["faction"])
    if invalid in {"capacity", "defender_capacity"}:
        selected = a if invalid == "capacity" else d
        selected.lineup["guests"][0]["troop_capacity"] = 0
        selected.save(update_fields=["lineup"])
    with pytest.raises(GameError), transaction.atomic():
        coalition_combat.fight_coalitions_locked(
            [a, a] if invalid == "duplicate" else [a],
            [d],
            now,
            attacker_allocations={a.pk: {"dao_ke": amount}},
            defender_allocations={d.pk: {"dao_ke": 5}},
            commander_guest_limit=1,
        )
    assert not BattleReport.objects.exists()
    a.refresh_from_db()
    assert a.troops == {"dao_ke": 10} and a.battles == 0


def test_commander_projection_limits_guests_and_excludes_defeated_without_writes(coalition, django_assert_num_queries):
    make, _now = coalition
    a, _ag = make("fiction", [100, 90, 80], {"dao_ke": 10})
    b, bg = make("fiction", [200], {"dao_ke": 10})
    with django_assert_num_queries(0):
        assert coalition_combat.choose_commander([a, b], 1) == b
        assert coalition_combat.choose_commander([a, b], 3) == a
        b.result["defeated_ids"] = [bg[0].pk]
        assert coalition_combat.choose_commander([a, b], 1) == a
        assert coalition_combat.choose_commander([b], 1) is None


def test_real_two_player_teams_all_take_damage_and_survivors_keep_low_hp_next_wave(coalition):
    from guests.models import Skill

    make, now = coalition
    a, ag = make("fiction", [100], {})
    b, bg = make("fiction", [100], {})
    d, dg = make("history", [70], {})
    e, eg = make("history", [70], {})
    skill = Skill.objects.create(
        key="coalition_two_target_strike",
        name="合击",
        base_power=1,
        targets=2,
        passive_config={"active": {"trigger_chance": 1.0}},
    )
    for run in (a, b, d, e):
        for snapshot in run.lineup["guests"]:
            snapshot["current_hp"] = 6000
            snapshot["defense"] = 1
            snapshot["skill_keys"] = [skill.key]
            Guest.objects.filter(pk=snapshot["guest_id"]).update(current_hp=6000)
        run.save(update_fields=["lineup"])
    with transaction.atomic():
        report, _a_id, _d_id = coalition_combat.fight_coalitions_locked(
            [a, b],
            [d, e],
            now,
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
        )
    assert len(report.attacker_team) == len(report.defender_team) == 2
    for guest in (*ag, *bg, *dg, *eg):
        guest.refresh_from_db()
        assert guest.current_hp < 6000, {
            "attacker": [(row["guest_id"], row["remaining_hp"]) for row in report.attacker_team],
            "defender": [(row["guest_id"], row["remaining_hp"]) for row in report.defender_team],
            "rounds": len(report.rounds),
        }
    winners = [a, b] if report.winner == "attacker" else [d, e]
    survivors = [run for run in winners if coalition_combat.choose_commander([run], 3)]
    assert survivors
    first_hp = {
        row["guest_id"]: row["current_hp"]
        for run in survivors
        for row in run.lineup["guests"]
        if row["guest_id"] not in run.result.get("defeated_ids", [])
    }
    npc = pve_lineup("mountain:inn", "第二波守军", 0, 11)
    with transaction.atomic():
        second, _leader, _none = coalition_combat.fight_coalitions_locked(
            survivors,
            [],
            now + timedelta(seconds=1),
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
            npc_lineup=npc,
        )
    assert {row["guest_id"] for row in second.attacker_team} == set(first_hp)
    for row in second.attacker_team:
        assert row["initial_hp"] == first_hp[row["guest_id"]]


def test_partly_defeated_player_keeps_other_guests_fighting_in_next_wave(coalition):
    make, now = coalition
    run, guests = make("fiction", [1000, 900], {})
    run.result["defeated_ids"] = [guests[0].pk]
    run.lineup["guests"][0]["current_hp"] = 1
    run.save(update_fields=["result", "lineup"])
    Guest.objects.filter(pk=guests[0].pk).update(current_hp=1)
    npc = pve_lineup("mountain:inn", "残军再战", 0, 12)
    with transaction.atomic():
        report, _a, _d = coalition_combat.fight_coalitions_locked(
            [run],
            [],
            now,
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
            npc_lineup=npc,
        )
    assert [row["guest_id"] for row in report.attacker_team] == [guests[1].pk]
    guests[0].refresh_from_db()
    assert guests[0].current_hp == 1
    assert run.result["defeated_ids"][0] == guests[0].pk


def test_more_than_five_players_is_rejected(coalition):
    make, now = coalition
    runs = [make("fiction", [100], {})[0] for _ in range(6)]
    with pytest.raises(GameError, match="最多5"), transaction.atomic():
        coalition_combat.fight_coalitions_locked(
            runs,
            [],
            now,
            attacker_allocations={},
            defender_allocations={},
            commander_guest_limit=3,
            npc_lineup=pve_lineup("mountain:inn", "人数限制", 0, 13),
        )


def test_real_full_five_player_waves_keep_all_fifteen_guests_and_enforce_both_frontline_capacities(coalition):
    make, now = coalition
    attackers = [make("fiction", [1000, 900, 800], {"dao_ke": 601}) for _ in range(5)]
    defenders = [make("history", [1000, 900, 800], {"dao_ke": 601}) for _ in range(5)]
    rear, rear_guests = make("fiction", [2000, 1900, 1800], {"dao_ke": 601})
    attack_runs = [run for run, _guests in attackers]
    defend_runs = [run for run, _guests in defenders]
    attack_front = {run.pk: {"dao_ke": 600} for run in attack_runs}
    defend_front = {run.pk: {"dao_ke": 600} for run in defend_runs}

    # 每边15门客各200带兵量，3000合法；双方各自多投入1名都必须拒绝。
    for over_side in ("attacker", "defender"):
        bad_attack, bad_defend = deepcopy(attack_front), deepcopy(defend_front)
        bad = bad_attack if over_side == "attacker" else bad_defend
        bad[next(iter(bad))]["dao_ke"] += 1
        with pytest.raises(GameError, match="实际带兵容量"), transaction.atomic():
            coalition_combat.fight_coalitions_locked(
                attack_runs,
                defend_runs,
                now,
                attacker_allocations=bad_attack,
                defender_allocations=bad_defend,
                commander_guest_limit=3,
            )
        assert not BattleReport.objects.exists()

    with transaction.atomic():
        report, attacker_leader, defender_leader = coalition_combat.fight_coalitions_locked(
            attack_runs,
            defend_runs,
            now,
            attacker_allocations=attack_front,
            defender_allocations=defend_front,
            commander_guest_limit=3,
        )
    assert len(report.attacker_team) == len(report.defender_team) == 15
    assert {row["guest_id"] for row in report.attacker_team} == {
        guest.pk for _run, guests in attackers for guest in guests
    }
    assert {row["guest_id"] for row in report.defender_team} == {
        guest.pk for _run, guests in defenders for guest in guests
    }
    assert sum(report.attacker_troops.values()) == sum(report.defender_troops.values()) == 3000
    assert attacker_leader in attack_front and defender_leader in defend_front
    assert report.manor_id == next(run.member.manor_id for run in attack_runs if run.pk == attacker_leader)
    for side, rows in (("attacker", attackers), ("defender", defenders)):
        reported = report.attacker_team if side == "attacker" else report.defender_team
        hp_by_id = {entry["guest_id"]: entry["remaining_hp"] for entry in reported}
        for run, guests in rows:
            run.refresh_from_db()
            assert run.battles == 1 and run.report_id == report.pk
            assert run.result["coalition_side"] == side
            assert set(run.result["coalition_guest_ids"]) == {guest.pk for guest in guests}
            assert run.troops["dao_ke"] >= 1  # 未投入前线的后备护院不能被扣除。
            for guest in guests:
                guest.refresh_from_db()
                assert guest.current_hp == max(1, hp_by_id[guest.pk])
    rear.refresh_from_db()
    assert rear.battles == 0 and rear.report_id is None and rear.troops == {"dao_ke": 601}
    for guest in rear_guests:
        guest.refresh_from_db()
        assert guest.current_hp == 80 and guest.status == GuestStatus.DEPLOYED


def test_season_cavalry_uses_full_technology_on_both_sides_even_with_old_snapshots(coalition):
    from gameplay.services.season.combat import prepare_combat
    from gameplay.services.season.technology import season_technology_levels

    make, _ = coalition
    TroopTemplate.objects.update_or_create(
        key="bloodthirsty_cavalry",
        defaults={"name": "嗜血铁骑", "base_attack": 17, "base_defense": 10, "base_hp": 72, "speed_bonus": 28},
    )
    attacker, _ = make("fiction", [100], {"bloodthirsty_cavalry": 10})
    defender, _ = make("history", [100], {"bloodthirsty_cavalry": 10})
    legacy = {"dao_attack": 1, "wolf_force": 0, "wolf_berserk": 0}
    before = deepcopy(legacy)
    sides = [{**run.lineup, "troops": run.troops, "technology": legacy} for run in (attacker, defender)]
    prepared = prepare_combat(attacker.member.manor, *sides, "铁骑驻军", 42, preserve_state=True)["prepared"]
    for troop in (prepared.attacker_troops[0], prepared.defender_troops[0]):
        assert troop.unit_attack == 34 and troop.unit_defense == 20 and troop.unit_hp == 144
        assert troop.agility == 42
        assert troop.skills[0]["name"] == "铁骑冲阵"
        assert troop.skills[0]["trigger_chance"] == 0.5
    assert legacy == before and season_technology_levels(legacy)["dao_attack"] == 1
