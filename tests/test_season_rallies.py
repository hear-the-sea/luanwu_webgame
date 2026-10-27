"""真实入口覆盖集结限额、取消返还、整波战斗与后台推进。"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport
from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember, SeasonRally
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.expeditions import advance_due_expeditions, process_expedition
from gameplay.services.season.quotas import daily_dispatches_used
from gameplay.services.season.warfare import ensure_sites_locked
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def rally_world(user_factory, monkeypatch):
    clock = [timezone.now()]
    monkeypatch.setattr("gameplay.services.season.actions.timezone.now", lambda: clock[0])
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    # 此夹具验证旧赛季快照；新版 NPC 阵容由专项测试覆盖。
    rules["campaign"].pop("npc_encounters", None)
    rules["campaign"]["expeditions"]["guest_daily_limit"] = 0  # 兼容旧快照，限次由专项验收。
    # 保留原有规则回归；新城防与疲敝由专项覆盖。
    rules["campaign"]["fortifications"]["enabled"] = False
    rules["campaign"]["playability"]["enabled"] = False
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    # 旧规则快照兼容回归；新人口与差异活动在专项测试覆盖。
    for feature in ("population", "activities", "contribution"):
        rules["campaign"][feature]["enabled"] = False
    season = Season.objects.create(
        name="集结军团测试", starts_at=clock[0] - timedelta(days=40), ends_at=clock[0] + timedelta(days=50), rules=rules
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="乡勇", rarity="black", archetype="military"
    )
    with transaction.atomic():
        for state in ensure_factions_locked(season, clock[0]).values():
            state.produced_at = clock[0]
            state.save(update_fields=["produced_at"])
        ensure_sites_locked(season, clock[0])

    def player(faction="fiction", count=1, force=200, hp=None, agility=20):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        guests = []
        for _ in range(count):
            guest = Guest.objects.create(
                manor=manor,
                template=template,
                force=force,
                defense_stat=force,
                intellect=force,
                agility=agility,
                current_hp=100,
            )
            guest.current_hp = guest.max_hp if hp is None else hp
            guest.save(update_fields=["current_hp"])
            guests.append(guest)
        return member, guests

    return season, player, clock


def command(member, action, payload, token=None):
    return perform_action(member.manor, member.season_id, action, str(token or uuid4()), payload)


def army_payload(guests, *, key="mountain:gate", mode="attack", troops=10):
    return {
        "guest_ids": [guest.pk for guest in guests],
        "node_key": key,
        "mode": mode,
        "troops": {"dao_sheng": troops} if troops else {},
    }


def create_rally(member, guests, **kwargs):
    result = command(member, "rally_create", army_payload(guests, **kwargs))
    return SeasonRally.objects.get(pk=result["rally_id"]), SeasonExpedition.objects.get(pk=result["run_id"])


def join_rally(member, guests, rally, *, token=None, troops=10):
    result = command(member, "rally_join", {"rally_id": rally.pk, **army_payload(guests, troops=troops)}, token)
    return SeasonExpedition.objects.get(pk=result["run_id"])


def launch(rally, run):
    rally.refresh_from_db()
    assert process_expedition(run.pk, rally.gathers_until)
    rally.refresh_from_db()
    run.refresh_from_db()
    assert rally.status == "marching" and run.status == "outbound"


def test_single_player_leaves_after_30_minutes_and_background_scan_progresses(rally_world):
    season, player, clock = rally_world
    member, guests = player()
    rally, run = create_rally(member, guests)
    assert rally.gathers_until == clock[0] + timedelta(minutes=30)
    assert run.status == "gathering"
    assert Guest.objects.get(pk=guests[0].pk).status == GuestStatus.DEPLOYED
    assert not process_expedition(run.pk, rally.gathers_until - timedelta(microseconds=1))
    result = advance_due_expeditions(rally.gathers_until)
    assert result["errors"] == 0
    rally.refresh_from_db()
    run.refresh_from_db()
    assert rally.status == "marching"
    assert rally.launched_at == rally.gathers_until
    assert run.due_at == rally.due_at > rally.gathers_until
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 1
    assert not BattleReport.objects.exists()


def test_five_players_launch_early_and_sixth_cannot_join(rally_world):
    _, player, clock = rally_world
    leader, guests = player()
    rally, _ = create_rally(leader, guests)
    for _ in range(4):
        member, own_guests = player()
        join_token = str(uuid4())
        final_run = join_rally(member, own_guests, rally, token=join_token)
    rally.refresh_from_db()
    assert rally.status == "marching" and rally.launched_at == clock[0]
    assert rally.launched_at < rally.gathers_until
    assert rally.expeditions.filter(status="outbound").count() == 5
    assert join_rally(member, own_guests, rally, token=join_token).pk == final_run.pk
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 1
    outsider, spare = player()
    with pytest.raises(GameError):
        join_rally(outsider, spare, rally)
    assert not outsider.expeditions.exists()
    assert Guest.objects.get(pk=spare[0].pk).status == GuestStatus.IDLE


def test_chosen_single_player_rally_launches_immediately_and_is_idempotent(rally_world):
    _, player, clock = rally_world
    member, guests = player()
    payload = {**army_payload(guests), "rally_target_members": 1}
    token = str(uuid4())
    response = command(member, "rally_create", payload, token)
    assert command(member, "rally_create", payload, token) == response
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    run = SeasonExpedition.objects.get(pk=response["run_id"])
    assert rally.result["target_members"] == 1
    assert rally.status == "marching" and run.status == "outbound"
    assert rally.launched_at == clock[0]
    assert run.due_at == clock[0] + timedelta(seconds=run.travel_seconds)
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 1


def test_chosen_two_player_rally_launches_when_teammate_joins(rally_world):
    _, player, clock = rally_world
    leader, guests = player()
    response = command(leader, "rally_create", {**army_payload(guests), "rally_target_members": 2})
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    assert rally.status == "gathering"
    member, own_guests = player()
    join_rally(member, own_guests, rally)
    rally.refresh_from_db()
    assert rally.result["target_members"] == 2
    assert rally.status == "marching" and rally.launched_at == clock[0]
    assert rally.expeditions.filter(status="outbound").count() == 2


def test_chosen_target_keeps_timeout_fallback(rally_world):
    _, player, clock = rally_world
    member, guests = player()
    response = command(member, "rally_create", {**army_payload(guests), "rally_target_members": 3})
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    run = SeasonExpedition.objects.get(pk=response["run_id"])
    assert rally.gathers_until == clock[0] + timedelta(minutes=30)
    launch(rally, run)
    assert rally.result["player_count"] == 1


@pytest.mark.parametrize("target_members", [2, 3])
def test_late_slow_teammate_can_arrive_only_when_join_completes_target(rally_world, monkeypatch, target_members):
    season, player, clock = rally_world
    cutoff = season.starts_at + timedelta(days=season.rules["campaign"]["expeditions"]["combat_days"])
    clock[0] = cutoff - timedelta(minutes=40)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 60)
    leader, lead_guests = player()
    response = command(leader, "rally_create", {**army_payload(lead_guests), "rally_target_members": target_members})
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    clock[0] = cutoff - timedelta(minutes=25)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 1200)
    teammate, own_guests = player()
    assert clock[0] + timedelta(seconds=1200) < cutoff
    assert rally.gathers_until + timedelta(seconds=1200) >= cutoff
    if target_members == 3:
        with pytest.raises(GameError, match="行动期内抵达"):
            join_rally(teammate, own_guests, rally)
        assert not teammate.expeditions.exists()
        assert Guest.objects.get(pk=own_guests[0].pk).status == GuestStatus.IDLE
        rally.refresh_from_db()
        assert rally.status == "gathering"
    else:
        run = join_rally(teammate, own_guests, rally)
        rally.refresh_from_db()
        assert rally.status == "marching" and rally.launched_at == clock[0]
        assert run.due_at == rally.due_at == cutoff - timedelta(minutes=5)
        assert set(rally.expeditions.values_list("travel_seconds", flat=True)) == {1200}


@pytest.mark.parametrize("target", [0, 6, -1, True, 1.5, "2", None])
def test_invalid_launch_target_does_not_reserve_army_or_guests(rally_world, target):
    season, player, _ = rally_world
    member, guests = player()
    stock = deepcopy(season.faction_states.get(faction="fiction").troops)
    with pytest.raises(GameError, match="出发人数"):
        command(member, "rally_create", {**army_payload(guests), "rally_target_members": target})
    assert not SeasonRally.objects.exists()
    assert not member.expeditions.exists()
    assert season.faction_states.get(faction="fiction").troops == stock
    assert Guest.objects.get(pk=guests[0].pk).status == GuestStatus.IDLE


@pytest.mark.parametrize("member_exits", [False, True])
def test_member_exit_and_leader_cancel_restore_guests_army_and_both_quotas(rally_world, member_exits):
    season, player, clock = rally_world
    leader, lead_guests = player()
    member, own_guests = player()
    before = deepcopy(season.faction_states.get(faction="fiction").troops)
    rally, lead_run = create_rally(leader, lead_guests)
    member_run = join_rally(member, own_guests, rally)
    if member_exits:
        command(member, "recall", {"run_id": member_run.pk})
        member_run.refresh_from_db()
        member.refresh_from_db()
        assert member_run.status == "completed"
        assert member.troops_drawn == 0
        assert member.actions.filter(kind="dispatch_refund").count() == 1
        assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 0
        assert Guest.objects.get(pk=own_guests[0].pk).status == GuestStatus.IDLE
    token = str(uuid4())
    response = command(leader, "rally_cancel", {"rally_id": rally.pk}, token)
    assert command(leader, "rally_cancel", {"rally_id": rally.pk}, token) == response
    rally.refresh_from_db()
    lead_run.refresh_from_db()
    leader.refresh_from_db()
    assert rally.status == "cancelled" and lead_run.status == "completed"
    assert leader.troops_drawn == 0
    assert leader.actions.filter(kind="dispatch_refund").count() == 1
    assert daily_dispatches_used(leader, timezone.localdate(clock[0])) == 0
    assert Guest.objects.get(pk=lead_guests[0].pk).status == GuestStatus.IDLE
    member_run.refresh_from_db()
    member.refresh_from_db()
    assert member_run.status == "completed" and member.troops_drawn == 0
    assert member.actions.filter(kind="dispatch_refund").count() == 1
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 0
    assert Guest.objects.get(pk=own_guests[0].pk).status == GuestStatus.IDLE
    assert season.faction_states.get(faction="fiction").troops == before


def test_rally_membership_faction_and_request_idempotency(rally_world):
    _, player, _ = rally_world
    member, guests = player()
    token = str(uuid4())
    payload = army_payload(guests)
    first = command(member, "rally_create", payload, token)
    assert command(member, "rally_create", payload, token) == first
    assert member.expeditions.count() == 1
    assert SeasonRally.objects.count() == 1
    with pytest.raises(GameError, match="凭证"):
        command(member, "rally_create", {**payload, "mode": "siege"}, token)
    rally = SeasonRally.objects.get(pk=first["rally_id"])
    with pytest.raises(GameError, match="一次"):
        join_rally(member, guests, rally)
    enemy, enemy_guests = player("history")
    with pytest.raises(GameError, match="己方"):
        join_rally(enemy, enemy_guests, rally)
    assert not enemy.expeditions.exists()


def test_each_player_has_one_attack_and_one_garrison_slot_with_three_guests(rally_world):
    season, player, _ = rally_world
    member, guests = player(count=6)
    own_site = season.sites.get(key="forest:granary")
    own_site.owner = "fiction"
    own_site.save(update_fields=["owner"])
    with pytest.raises(GameError, match="集结"):
        command(member, "dispatch", army_payload(guests[:3]))
    with pytest.raises(GameError, match="1—3"):
        create_rally(member, guests[:4])
    assert not SeasonRally.objects.exists()
    create_rally(member, guests[:3])
    command(member, "dispatch", army_payload(guests[3:], key=own_site.key, mode="garrison"))
    assert member.expeditions.count() == 2
    with pytest.raises(GameError, match="出征位"):
        create_rally(member, guests[:1])
    with pytest.raises(GameError, match="驻防位"):
        command(member, "dispatch", army_payload(guests[3:], key=own_site.key, mode="garrison"))
    assert SeasonRally.objects.count() == 1


@pytest.mark.parametrize("change", ["friendly", "protected"])
def test_changed_target_returns_safely_without_duplicate_rewards(rally_world, change):
    season, player, _ = rally_world
    member, guests = player()
    rally, run = create_rally(member, guests)
    launch(rally, run)
    arrival = run.due_at
    site = season.sites.get(key=rally.node_key)
    if change == "friendly":
        site.owner = "fiction"
    else:
        site.protected_until = arrival + timedelta(hours=1)
    site.save(update_fields=["owner", "protected_until"])
    assert process_expedition(run.pk, arrival)
    run.refresh_from_db()
    rally.refresh_from_db()
    assert run.status == "returning" and rally.status == "resolved"
    assert not BattleReport.objects.exists()
    assert not member.actions.filter(kind="combat").exists()
    assert not process_expedition(run.pk, arrival)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    member.refresh_from_db()
    assert run.status == "completed" and member.merit == 0
    assert season.faction_states.get(faction="fiction").construction_points == 0


def test_two_player_wave_fights_only_two_defenders_and_carries_survivors_forward(rally_world, monkeypatch):
    from gameplay.services.season import rallies

    season, player, clock = rally_world
    # 集结等待和行军均可能接近30分钟，守军须驻留到第二波交战。
    season.rules["campaign"]["expeditions"]["station_seconds"] = 7200
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defenders = []
    for _ in range(3):
        member, guests = player("history", count=3, force=1, hp=1, agility=1000)
        result = command(member, "dispatch", army_payload(guests, mode="garrison"))
        run = SeasonExpedition.objects.get(pk=result["run_id"])
        assert process_expedition(run.pk, run.due_at)
        run.refresh_from_db()
        assert run.status == "stationed"
        defenders.append((member, guests, run))
    clock[0] = max(run.launched_at + timedelta(seconds=run.travel_seconds) for _, _, run in defenders)
    first, first_guests = player(count=3, force=200, agility=1)
    second, second_guests = player(count=3, force=200, agility=1)
    initial_hp = {}
    for guest in first_guests + second_guests:
        guest.current_hp = guest.max_hp // 2
        guest.last_hp_recovery_at = clock[0]
        guest.save(update_fields=["current_hp", "last_hp_recovery_at"])
        initial_hp[guest.pk] = guest.current_hp
    rally, first_run = create_rally(first, first_guests)
    second_run = join_rally(second, second_guests, rally)
    for run in (first_run, second_run):
        run.seed = 12345
        run.save(update_fields=["seed"])
    captures = []
    original_fight = rallies.fight_coalitions_locked

    def observe(attackers, enemy_runs, now, **kwargs):
        captures.append(
            {
                "attackers": [run.pk for run in attackers],
                "defenders": [run.pk for run in enemy_runs],
                "hp": {row["guest_id"]: row["current_hp"] for run in attackers for row in run.lineup["guests"]},
                "troops": {run.pk: deepcopy(run.troops) for run in attackers},
            }
        )
        return original_fight(attackers, enemy_runs, now, **kwargs)

    monkeypatch.setattr(rallies, "fight_coalitions_locked", observe)
    launch(rally, first_run)
    first_arrival = rally.due_at
    assert process_expedition(first_run.pk, rally.due_at)
    first_run.refresh_from_db()
    second_run.refresh_from_db()
    rally.refresh_from_db()
    assert len(captures) == 1
    assert captures[0]["defenders"] == [run.pk for _, _, run in defenders[:2]]
    report = first_run.report
    assert report.winner == "attacker"
    assert len([row for row in report.attacker_team if row.get("guest_id")]) == 6
    assert len([row for row in report.defender_team if row.get("guest_id")]) == 6
    untouched = defenders[2][2]
    untouched.refresh_from_db()
    assert untouched.status == "stationed" and untouched.battles == 0 and untouched.report_id is None
    assert all(Guest.objects.get(pk=guest.pk).current_hp == 1 for guest in defenders[2][1])
    retained_hp = {
        row["guest_id"]: row["current_hp"] for run in (first_run, second_run) for row in run.lineup["guests"]
    }
    retained_troops = {run.pk: deepcopy(run.troops) for run in (first_run, second_run)}
    assert all(hp <= initial_hp[guest_id] for guest_id, hp in retained_hp.items())
    assert rally.status == "marching" and rally.result["wave"] == 1
    assert rally.due_at == first_arrival + timedelta(seconds=60)
    assert process_expedition(first_run.pk, rally.due_at)
    assert captures[1]["defenders"] == [untouched.pk]
    assert captures[1]["hp"] == retained_hp
    assert captures[1]["troops"] == retained_troops


def test_losing_wave_returns_and_preserves_injuries(rally_world):
    season, player, clock = rally_world
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defender, guard = player("history", force=1000)
    result = command(defender, "dispatch", army_payload(guard, mode="garrison", troops=0))
    defense = SeasonExpedition.objects.get(pk=result["run_id"])
    assert process_expedition(defense.pk, defense.due_at)
    clock[0] = defense.due_at
    attacker, guests = player(force=1, hp=1)
    rally, run = create_rally(attacker, guests, troops=0)
    launch(rally, run)
    assert process_expedition(run.pk, rally.due_at)
    run.refresh_from_db()
    rally.refresh_from_db()
    assert run.report.winner == "defender"
    assert run.status == "returning" and rally.status == "resolved"
    assert process_expedition(run.pk, run.due_at)
    guests[0].refresh_from_db()
    assert guests[0].status == GuestStatus.INJURED
    assert guests[0].current_hp == 1


def test_siege_rally_completes_all_waves_and_capture_then_returns_once(rally_world):
    from gameplay.services.season.rallies import advance_due_rallies

    season, player, _ = rally_world
    member, guests = player()
    site = season.sites.get(key="mountain:gate")
    site.guards, site.durability = 0, 20
    site.save(update_fields=["guards", "durability"])
    rally, run = create_rally(member, guests, mode="siege", troops=30)
    launch(rally, run)
    for _ in range(10):
        rally.refresh_from_db()
        if rally.status == "resolved":
            break
        at = rally.due_at
        assert process_expedition(run.pk, at)
        earned = member.actions.filter(kind="combat").count()
        assert not process_expedition(run.pk, at)
        assert member.actions.filter(kind="combat").count() == earned
    else:
        raise AssertionError("集结破城未在预期波数内完成")
    site.refresh_from_db()
    run.refresh_from_db()
    member.refresh_from_db()
    assert site.owner == "fiction" and site.control == 0
    assert rally.result["wave"] == 5
    assert run.status == "returning"
    assert member.merit == 150
    assert member.actions.filter(kind="combat").count() == 5
    assert season.faction_states.get(faction="fiction").construction_points == 150
    assert not BattleReport.objects.exists()
    assert advance_due_rallies(at)["rallies"] == 0
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.status == "completed"
    assert run.result["returned_troops"] == {"dao_sheng": 30}
    assert Guest.objects.get(pk=guests[0].pk).status == GuestStatus.IDLE
    assert not process_expedition(run.pk, run.due_at + timedelta(hours=1))
    member.refresh_from_db()
    assert member.merit == 150


def test_rally_program_error_backs_off_every_member_and_retries_without_damage(rally_world, monkeypatch):
    from gameplay.services.season import rallies

    season, player, _ = rally_world
    site = season.sites.get(key="mountain:gate")
    site.guards, site.durability = 0, 20
    site.save(update_fields=["guards", "durability"])
    leader, lead_guests = player()
    member, own_guests = player()
    rally, first = create_rally(leader, lead_guests, mode="siege")
    second = join_rally(member, own_guests, rally)
    launch(rally, first)
    runs = [first, second]
    initial = {}
    for run in runs:
        run.refresh_from_db()
        initial[run.pk] = (deepcopy(run.lineup), deepcopy(run.troops))
    at = rally.due_at
    guest_hp = {guest.pk: guest.current_hp for guest in lead_guests + own_guests}

    def failed_wave(*args, **kwargs):
        # 模拟结算中途已写入部分生命、兵力和贡献后发生程序错误。
        Guest.objects.filter(pk=lead_guests[0].pk).update(current_hp=1)
        SeasonExpedition.objects.filter(pk=first.pk).update(troops={"dao_sheng": 0})
        SeasonMember.objects.filter(pk=leader.pk).update(merit=99)
        raise RuntimeError("模拟程序异常")

    with monkeypatch.context() as patch:
        patch.setattr(rallies, "_wave_locked", failed_wave)
        result = rallies.advance_due_rallies(at)
        assert result == {"rallies": 0, "rally_errors": 1}
        rally.refresh_from_db()
        assert rally.due_at > at and rally.status == "marching"
        for run in runs:
            run.refresh_from_db()
            assert run.due_at == rally.due_at
            assert (run.lineup, run.troops) == initial[run.pk]
            assert run.status == "outbound" and run.report_id is None
        assert rallies.advance_due_rallies(at) == {"rallies": 0, "rally_errors": 0}
    assert not BattleReport.objects.exists()
    assert not season.members.filter(merit__gt=0).exists()
    assert dict(Guest.objects.filter(pk__in=guest_hp).values_list("pk", "current_hp")) == guest_hp
    assert rallies.advance_due_rallies(rally.due_at) == {"rallies": 1, "rally_errors": 0}
    rally.refresh_from_db()
    site.refresh_from_db()
    assert rally.result["wave"] == 1
    assert site.durability == 0
    assert season.members.filter(merit=30).count() == 2
