"""真实派遣生命周期、阵营护院守恒与战场共享状态。"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from core.exceptions import GameError
from gameplay.models import Manor, PlayerTroop, Season, SeasonExpedition, SeasonMember
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.warfare import ensure_sites_locked, score_sites_locked
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def war(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    # 此夹具覆盖旧版出征快照，新版敌军阵容由专项验收。
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
    rules["campaign"]["coalition"]["enabled"] = False
    # 保留在途旧赛季快照的兼容性回归；新兵种配置另有专项测试。
    economy = rules["campaign"]["economy"]
    economy["initial_troops"] = {"dao_ke": 1000, "qiang_ke": 1000, "jian_shi": 800}
    economy["troop_caps"] = {key: 10000 for key in economy["initial_troops"]}
    economy["troop_rates"] = {"dao_ke": 15, "qiang_ke": 15, "jian_shi": 10}
    economy["special_troops"] = {}
    rules["campaign"]["economy"]["daily_troop_quota"] = 10000
    season = Season.objects.create(
        name="真实战役", starts_at=now - timedelta(days=40), ends_at=now + timedelta(days=50), rules=rules
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="乡勇", rarity="black", archetype="military"
    )
    with transaction.atomic():
        states = ensure_factions_locked(season, now)
        for state in states.values():
            state.produced_at = now
            state.save(update_fields=["produced_at"])
        ensure_sites_locked(season, now)

    def player(faction="fiction", force=200, hp=None):
        user = user_factory()
        manor, _ = Manor.objects.get_or_create(user=user)
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        guest = Guest.objects.create(
            manor=manor,
            template=template,
            force=force,
            defense_stat=force,
            intellect=force,
            agility=force,
            current_hp=100,
        )
        guest.current_hp = guest.max_hp if hp is None else hp
        guest.save(update_fields=["current_hp"])
        return member, guest

    return season, player, now


def dispatch(member, guest, key="mountain:inn", mode="pve", troops=None, token=None):
    result = perform_action(
        member.manor,
        member.season_id,
        "dispatch",
        str(token or uuid4()),
        {
            "guest_ids": [guest.pk],
            "node_key": key,
            "mode": mode,
            "troops": troops or {},
        },
    )
    return SeasonExpedition.objects.get(pk=result["run_id"])


def arrive(run):
    run.refresh_from_db()
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    return run


def home(run):
    if run.status != "returning":
        raise AssertionError(run.status)
    return arrive(run)


def test_dispatch_reserves_public_troops_and_real_guests_once(war):
    season, player, now = war
    member, guest = player()
    troop, _ = TroopTemplate.objects.get_or_create(key="dao_ke", defaults={"name": "刀客"})
    PlayerTroop.objects.create(manor=member.manor, troop_template=troop, count=77)
    before = season.faction_states.get(faction="fiction").troops["dao_ke"]
    token = uuid4()
    run = dispatch(member, guest, troops={"dao_ke": 30}, token=token)
    assert dispatch(member, guest, troops={"dao_ke": 30}, token=token).pk == run.pk
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert run.due_at > run.launched_at and run.guests.filter(pk=guest.pk).exists()
    assert season.faction_states.get(faction="fiction").troops["dao_ke"] == before - 30
    assert PlayerTroop.objects.get(manor=member.manor).count == 77
    assert not process_expedition(run.pk, run.due_at - timedelta(seconds=1))
    with pytest.raises(GameError):
        dispatch(member, guest)


def test_pve_timed_battle_and_return_do_not_restore_lost_hp(war):
    season, player, now = war
    member, guest = player(hp=1000)
    run = dispatch(member, guest, troops={"dao_ke": 20})
    before = guest.current_hp
    arrive(run)
    assert run.status == "returning" and run.report_id
    guest.refresh_from_db()
    assert guest.current_hp <= before and guest.status == GuestStatus.DEPLOYED
    state = season.faction_states.get(faction="fiction")
    survivors = run.troops["dao_ke"]
    state.produced_at = run.due_at
    state.save(update_fields=["produced_at"])
    stock = state.troops["dao_ke"]
    final_hp = guest.current_hp
    home(run)
    guest.refresh_from_db()
    assert guest.current_hp == final_hp
    assert run.status == "completed"
    assert season.faction_states.get(faction="fiction").troops["dao_ke"] == stock + survivors
    assert not process_expedition(run.pk, run.due_at + timedelta(hours=1))
    assert BattleReport.objects.filter(battle_type="season").count() == 1


def test_recall_refunds_unfought_troops_and_cargo_once(war):
    season, player, now = war
    member, guest = player()
    state = season.faction_states.get(faction="fiction")
    before = deepcopy(state.troops)
    run = dispatch(member, guest, key="river:docks", mode="transport", troops={"dao_ke": 20})
    token = str(uuid4())
    args = (member.manor, season.pk, "recall", token, {"run_id": run.pk})
    assert perform_action(*args) == perform_action(*args)
    run.refresh_from_db()
    assert run.status == "returning" and not run.cargo
    state.refresh_from_db()
    state.produced_at = run.due_at
    state.save(update_fields=["produced_at"])
    home(run)
    state.refresh_from_db()
    guest.refresh_from_db()
    assert state.troops == before and guest.status == GuestStatus.IDLE


def test_daily_quota_concurrency_and_invalid_loadout_roll_back(war):
    season, player, now = war
    member, guest = player()
    before = deepcopy(season.faction_states.get(faction="fiction").troops)
    for loadout in ({"dao_ke": 999999}, {"dao_ke": -1}, {"invalid": 2}):
        with pytest.raises(GameError):
            dispatch(member, guest, troops=loadout)
    assert season.faction_states.get(faction="fiction").troops == before
    assert not member.expeditions.exists()
    dispatch(member, guest)
    other_guests = [Guest.objects.create(manor=member.manor, template=guest.template, current_hp=100) for _ in range(2)]
    dispatch(member, other_guests[0])
    with pytest.raises(GameError, match="同时派遣"):
        dispatch(member, other_guests[1])
    assert member.expeditions.count() == 2


def test_garrison_pvp_writes_both_sides_and_return_injuries(war):
    season, player, now = war
    defender, gd = player("history", force=1, hp=1)
    attacker, ga = player("fiction", force=1000)
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save()
    defense = dispatch(defender, gd, key=site.key, mode="garrison", troops={"dao_ke": 10})
    arrive(defense)
    assert defense.status == "stationed"
    offense = dispatch(attacker, ga, key=site.key, mode="attack", troops={"dao_ke": 30})
    arrive(offense)
    defense.refresh_from_db()
    assert offense.report_id and defense.report_id == offense.report_id
    assert defense.status == "returning"
    assert defender.actions.filter(report=offense.report).exists()
    assert offense.report.losses["attacker"]["troop_loss_rate"] == 0.5
    assert offense.report.losses["defender"]["troop_loss_rate"] == 0.5
    home(defense)
    gd.refresh_from_db()
    assert gd.status == GuestStatus.INJURED
    assert gd.injury_loyalty_processed_at is not None


def test_shared_guards_demolition_capture_and_protection(war):
    season, player, now = war
    member, guest = player(force=1000)
    enemy, eg = player("history")
    site = season.sites.get(key="mountain:gate")
    site.guards, site.durability = 1, 50
    site.save()
    with pytest.raises(GameError, match="守军"):
        dispatch(member, guest, key=site.key, mode="siege", troops={"dao_ke": 100})
    run = dispatch(member, guest, key=site.key, mode="attack")
    arrive(run)
    home(run)
    site.refresh_from_db()
    assert site.guards == 0
    run = dispatch(member, guest, key=site.key, mode="siege", troops={"dao_ke": 100})
    arrive(run)
    home(run)
    site.refresh_from_db()
    assert site.durability == 0
    site.control = 75
    site.attacker = "fiction"
    site.save()
    run = dispatch(member, guest, key=site.key, mode="siege", troops={"dao_ke": 30})
    arrive(run)
    site.refresh_from_db()
    assert site.owner == "fiction" and site.protected_until
    with pytest.raises(GameError, match="休整期"):
        dispatch(enemy, eg, key=site.key, mode="attack")


def test_weekend_window_rechecked_at_arrival(war, monkeypatch):
    from gameplay.services.season import actions
    from gameplay.services.season.warfare import city_open

    season, player, now = war
    member, guest = player()
    monday = now + timedelta(days=(0 - now.weekday()) % 7)
    # 配置显式使用北京时间，测试取本地中午消除UTC日期偏差。
    monday = timezone.localtime(monday).replace(hour=12, minute=0, second=0)
    monkeypatch.setattr(actions.timezone, "now", lambda: monday)
    assert not city_open(season, monday)
    with pytest.raises(GameError, match="周六"):
        dispatch(member, guest, key="river:town", mode="attack")


def test_transport_can_be_intercepted_only_once(war):
    season, player, now = war
    carrier, gc = player("history", force=1, hp=1)
    pirate, gp = player("fiction", force=1000)
    transport = dispatch(carrier, gc, key="river:docks", mode="transport", troops={"dao_ke": 10})
    arrive(transport)
    assert transport.status == "transporting"
    intercept = dispatch(pirate, gp, key="river:docks", mode="intercept", troops={"dao_ke": 20})
    arrive(intercept)
    transport.refresh_from_db()
    assert transport.intercepted
    assert transport.status == "returning"
    assert not transport.cargo
    assert intercept.report_id


def test_score_before_capture_and_freeze_at_end(war):
    season, player, now = war
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.last_score_at = now - timedelta(hours=2)
    site.save()
    with transaction.atomic():
        score_sites_locked(season, now)
        score_sites_locked(season, now)
    assert season.faction_states.get(faction="history").score == 2


def test_failure_rolls_back_battle_and_can_be_recalled(war, monkeypatch):
    from gameplay.services.season import expeditions

    season, player, now = war
    member, guest = player()
    run = dispatch(member, guest, troops={"dao_ke": 10})
    monkeypatch.setattr(
        expeditions, "grant_pve_locked", lambda *args, **kwargs: (_ for _ in ()).throw(GameError("内容暂不可用"))
    )
    arrive(run)
    assert run.status == "returning" and not run.report_id
    assert not BattleReport.objects.exists()
    assert run.troops == run.initial_troops
    home(run)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_every_visible_node_has_an_executable_activity(war):
    from gameplay.services.season.warfare import campaign_nodes

    season, player, now = war
    rules = season.rules["campaign"]
    nodes = campaign_nodes(season)
    assert nodes["river:market"]["kind"] == "pvp"
    assert "siege" in nodes["river:market"]["modes"]
    for key, node in nodes.items():
        if "pve" in node["modes"]:
            assert key in rules["economy"]["pve"], key
        if node["kind"] == "economy":
            assert not node["modes"]


def test_expired_garrison_does_not_fight_when_scanner_is_delayed(war):
    season, player, now = war
    defender, gd = player("history", force=1000)
    attacker, ga = player("fiction", force=1000)
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save()
    defense = dispatch(defender, gd, key=site.key, mode="garrison")
    arrive(defense)
    # 扫描器未处理驻防到期，进攻结算仍须排除过期驻军。
    defense.due_at = now - timedelta(seconds=1)
    defense.save(update_fields=["due_at"])
    prior_report = defense.report_id
    second = dispatch(attacker, ga, key=site.key, mode="attack")
    arrive(second)
    defense.refresh_from_db()
    assert defense.report_id == prior_report
    assert second.report_id != prior_report


def test_transport_completion_delivers_once_and_returns_troops(war):
    season, player, now = war
    member, guest = player()
    run = dispatch(member, guest, key="river:docks", mode="transport", troops={"dao_ke": 10})
    arrive(run)
    assert run.status == "transporting"
    assert not member.actions.filter(kind="logistics").exists()
    arrive(run)
    assert run.status == "returning" and not run.cargo
    member.refresh_from_db()
    merit = member.merit
    assert merit == season.rules["campaign"]["economy"]["pve"]["river:docks"]["merit"]
    assert member.actions.filter(kind="logistics").count() == 1
    state = season.faction_states.get(faction=member.faction)
    assert state.construction_points == 30
    home(run)
    member.refresh_from_db()
    assert member.merit == merit
    state.refresh_from_db()
    assert state.construction_points == 30
    assert not process_expedition(run.pk, run.due_at + timedelta(hours=1))


def test_action_cutoff_refunds_outbound_transport_and_releases_guests(war):
    season, player, now = war
    member, guest = player()
    run = dispatch(member, guest, key="river:docks", mode="transport", troops={"dao_ke": 10})
    cutoff = season.starts_at + timedelta(days=season.rules["campaign"]["expeditions"]["combat_days"])
    assert process_expedition(run.pk, cutoff)
    run.refresh_from_db()
    assert run.status == "returning" and not run.cargo
    assert not member.actions.filter(kind="logistics").exists()
    home(run)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_scan_failure_backoff_does_not_block_other_due_armies(war, monkeypatch):
    from gameplay.services.season import expeditions

    season, player, now = war
    first, g1 = player()
    second, g2 = player()
    broken = dispatch(first, g1)
    healthy = dispatch(second, g2)
    due = max(broken.due_at, healthy.due_at)
    original = expeditions.process_expedition

    def fail_first(pk, at):
        if pk == broken.pk:
            raise RuntimeError("temporary database error")
        return original(pk, at)

    monkeypatch.setattr(expeditions, "process_expedition", fail_first)
    result = expeditions.advance_due_expeditions(due, limit=1)
    assert result == {"processed": 0, "errors": 1}
    broken.refresh_from_db()
    assert broken.due_at > due
    result = expeditions.advance_due_expeditions(due, limit=1)
    assert result == {"processed": 1, "errors": 0}
    healthy.refresh_from_db()
    assert healthy.status == "returning"


def test_losing_pve_applies_real_troop_losses_and_injury(war):
    season, player, now = war
    member, guest = player(force=1, hp=1)
    run = dispatch(member, guest, key="mountain:ruins", troops={"dao_ke": 20})
    arrive(run)
    assert run.report.winner == "defender"
    assert run.troops["dao_ke"] < run.initial_troops["dao_ke"]
    assert guest.pk in run.result["defeated_ids"]
    member.refresh_from_db()
    assert member.merit == season.rules["campaign"]["economy"]["pve"][run.node_key]["failure_merit"]
    home(run)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.INJURED


def test_redundant_repair_refunds_reserved_materials(war):
    season, player, now = war
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = "fiction"
    site.durability -= 50
    site.save()
    run = dispatch(member, guest, key=site.key, mode="repair")
    reserved = dict(run.cargo)
    assert reserved
    state = season.faction_states.get(faction="fiction")
    state.produced_at = run.due_at
    state.save(update_fields=["produced_at"])
    before = dict(state.resources)
    site.durability = season.rules["campaign"]["sites"][site.key]["durability"]
    site.save(update_fields=["durability"])
    arrive(run)
    assert run.status == "returning" and not run.cargo
    state.refresh_from_db()
    for key, amount in reserved.items():
        assert state.resources[key] == before[key] + amount
    assert not member.actions.filter(kind="combat").exists()


def test_campaign_closes_with_frozen_rankings_and_starts_fresh_season(war):
    from gameplay.services.season.lifecycle import advance_seasons

    season, player, now = war
    member, guest = player()
    member.merit = 200
    member.save(update_fields=["merit"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "fiction"
    site.last_score_at = now
    site.save()
    result = advance_seasons(season.ends_at)
    season.refresh_from_db()
    member.refresh_from_db()
    assert result["closed"] == 1 and result["started"] == 1
    assert season.closed and season.winner == "fiction"
    assert member.final_rank == 1 and member.final_reward["winner_bonus"]
    final_reward = deepcopy(member.final_reward)
    successor = Season.objects.exclude(pk=season.pk).get()
    assert successor.starts_at == season.ends_at
    assert not successor.members.exists()
    assert successor.faction_states.count() == 2
    assert successor.sites.count() == len(successor.rules["campaign"]["sites"])
    assert advance_seasons(season.ends_at)["started"] == 0
    member.refresh_from_db()
    assert member.final_reward == final_reward


def test_disabled_day_gates_open_all_maps_and_upgrades_on_day_one(war):
    from gameplay.selectors.season import get_season_context
    from gameplay.services.season.economy import grant_construction_locked
    from gameplay.services.season.warfare import campaign_nodes

    season, player, now = war
    season.starts_at = now - timedelta(minutes=1)
    season.ends_at = season.starts_at + timedelta(days=90)
    season.save(update_fields=["starts_at", "ends_at"])
    member, guest = player()
    assert season.rules["campaign"]["day_gates_enabled"] is False
    assert all(node["unlock_day"] == 1 for node in campaign_nodes(season).values())
    context = get_season_context(member.manor, season.pk)
    assert all(node["unlocked"] for region in context["season_maps"] for node in region["nodes"])
    assert all(building["level"] == 1 for building in context["upgrade_options"])
    season.rules["campaign"]["economy"]["auto_development"]["level_thresholds"] = [0, 20, 100, 200, 300, 400]
    season.save(update_fields=["rules"])
    member.season = season
    with transaction.atomic():
        assert grant_construction_locked(member, "pve_win", now)["levels"]["logistics"] == 2
    run = dispatch(member, guest, key="forest:granary", mode="pve")
    assert run.status == "outbound"


def test_upgrading_army_roster_preserves_stock_deployed_troops_and_progress(war):
    from gameplay.services.season.army_setup import upgrade_armies_locked

    season, player, now = war
    member, guest = player()
    run = dispatch(member, guest, troops={"dao_ke": 30})
    state = season.faction_states.get(faction="fiction")
    old_stock = state.troops["dao_ke"]
    member.refresh_from_db()
    drawn = member.troops_drawn
    with transaction.atomic():
        locked = Season.objects.select_for_update().get(pk=season.pk)
        result = upgrade_armies_locked(locked, now)
    assert result == {"factions": 2, "expeditions": 1}
    state.refresh_from_db()
    run.refresh_from_db()
    member.refresh_from_db()
    assert state.troops["dao_sheng"] == old_stock
    assert state.troops["quan_sheng"] == 1000 and state.troops["arrow_god"] == 1000
    assert state.troops["bloodthirsty_cavalry"] == 0
    assert run.troops == {"dao_sheng": 30} and run.initial_troops == {"dao_sheng": 30}
    assert member.troops_drawn == drawn and member.merit == 0
    before = deepcopy(state.troops)
    with transaction.atomic():
        locked = Season.objects.select_for_update().get(pk=season.pk)
        assert upgrade_armies_locked(locked, now)["expeditions"] == 0
    state.refresh_from_db()
    assert state.troops == before
    arrive(run)
    home(run)
    assert run.status == "completed"
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_pve_settlement_automatically_upgrades_all_facilities_exactly_once(war):
    season, player, now = war
    automatic = season.rules["campaign"]["economy"]["auto_development"]
    automatic["level_thresholds"] = [0, 20, 100, 200, 300, 400]
    season.save(update_fields=["rules"])
    member, guest = player(force=1000)
    run = dispatch(member, guest)
    arrive(run)
    state = season.faction_states.get(faction=member.faction)
    assert state.construction_points == 20
    assert state.levels == {"logistics": 2, "training": 2, "technology": 2}
    assert all(level == 1 for level in state.technology.values())
    action = member.actions.get(kind="logistics")
    assert action.result["construction_points"] == 20
    assert set(action.result["upgraded"]) == {"logistics", "training", "technology"}
    home(run)
    assert not process_expedition(run.pk, run.due_at + timedelta(hours=1))
    state.refresh_from_db()
    assert state.construction_points == 20


@pytest.mark.parametrize("agility,seconds", [(0, 1800), (100, 1620), (500, 900), (1000, 900)])
def test_thirty_minute_dispatch_uses_personal_mission_agility_reduction(war, agility, seconds):
    season, player, now = war
    assert set(season.rules["campaign"]["expeditions"]["travel_seconds"].values()) == {1800}
    member, guest = player()
    guest.agility = agility
    guest.save(update_fields=["agility"])
    run = dispatch(member, guest)
    assert run.travel_seconds == seconds
    assert (run.due_at - run.launched_at).total_seconds() == seconds


def test_dispatch_duration_migration_keeps_inflight_deadline_and_other_rules(war):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    season, player, now = war
    season.rules["campaign"]["expeditions"]["travel_seconds"]["mountain:inn"] = 180
    season.save(update_fields=["rules"])
    member, guest = player()
    run = dispatch(member, guest)
    before = (run.travel_seconds, run.launched_at, run.due_at)
    season.refresh_from_db()
    expected = deepcopy(season.rules)
    expected["campaign"]["expeditions"]["travel_seconds"] = {
        key: 1800 for key in expected["campaign"]["expeditions"]["travel_seconds"]
    }
    migration = import_module("gameplay.migrations.0261_season_dispatch_duration")
    migration.update_dispatch_duration(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    run.refresh_from_db()
    assert season.rules == expected
    assert (run.travel_seconds, run.launched_at, run.due_at) == before


@pytest.mark.parametrize("multiplier,expected", [(2, 720), (10, 144), (100, 14)])
def test_season_march_applies_global_speed_exactly_once(war, settings, multiplier, expected):
    season, player, now = war
    settings.GAME_TIME_MULTIPLIER = multiplier
    member, guest = player(force=200)
    run = dispatch(member, guest)
    assert run.travel_seconds == expected
    assert (run.due_at - run.launched_at).total_seconds() == expected


def _idle_garrison(war):
    season, player, now = war
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = dispatch(member, guest, key=site.key, mode="garrison")
    arrive(run)
    return season, member, run


def test_uncontested_garrison_awards_time_merit_once_even_when_scanner_is_late(war):
    season, member, run = _idle_garrison(war)
    due = run.due_at
    assert process_expedition(run.pk, due + timedelta(hours=3))
    run.refresh_from_db()
    member.refresh_from_db()
    assert run.status == "returning" and run.report_id is None
    assert run.result["garrison_service"]["seconds"] == 3600
    assert run.result["garrison_service"]["merit"] == member.merit == 10
    credit_count = member.actions.filter(kind="merit_credit").count()
    arrive(run)
    member.refresh_from_db()
    assert member.merit == 10
    assert member.actions.filter(kind="merit_credit").count() == credit_count


@pytest.mark.parametrize("seconds,expected", [(0, 0), (1800, 5), (3599, 9)])
def test_recalled_garrison_earns_only_actual_station_time(war, seconds, expected):
    from django.utils.dateparse import parse_datetime

    from gameplay.services.season.expeditions import recall_locked

    season, member, run = _idle_garrison(war)
    arrived = parse_datetime(run.result["stationed_at"])
    with transaction.atomic():
        recall_locked(member, run.pk, arrived + timedelta(seconds=seconds))
    run.refresh_from_db()
    member.refresh_from_db()
    assert run.result["garrison_service"]["seconds"] == seconds
    assert member.merit == expected


def test_idle_garrison_merit_respects_daily_contribution_cap(war):
    season, member, run = _idle_garrison(war)
    season.rules["campaign"]["contribution"]["enabled"] = True
    season.rules["campaign"]["contribution"]["daily_cap"] = 3
    season.save(update_fields=["rules"])
    # 固定在同一自然日，验证单日上限而非跨日拆账。
    noon = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
    run.result = {**run.result, "stationed_at": noon.isoformat()}
    run.due_at = noon + timedelta(hours=1)
    run.stationed_until = run.due_at
    run.save(update_fields=["result", "due_at", "stationed_until"])
    arrive(run)
    member.refresh_from_db()
    assert member.merit == 3
    assert run.result["garrison_service"]["requested_merit"] == 10


def test_eight_hour_garrison_option_can_dispatch_and_earn_eighty_merit(war):
    season, player, now = war
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    result = perform_action(
        member.manor,
        season.pk,
        "dispatch",
        str(uuid4()),
        {"guest_ids": [guest.pk], "node_key": site.key, "mode": "garrison", "station_hours": 8, "troops": {}},
    )
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    arrive(run)
    assert run.result["station_seconds"] == 8 * 3600
    arrive(run)
    member.refresh_from_db()
    assert member.merit == 80
    assert run.result["garrison_service"]["seconds"] == 8 * 3600


def test_daily_troop_quota_allows_fifteen_hundred_and_rejects_next_trooper(war):
    from gameplay.services.season.economy import reserve_troops_locked

    season, player, now = war
    season.rules["campaign"]["economy"]["daily_troop_quota"] = 1500
    season.save(update_fields=["rules"])
    member, guest = player()
    with transaction.atomic():
        reserve_troops_locked(member, {"dao_ke": 1000, "qiang_ke": 500}, now)
    member.refresh_from_db()
    assert member.troops_drawn == 1500
    with transaction.atomic(), pytest.raises(GameError, match="额度"):
        reserve_troops_locked(member, {"qiang_ke": 1}, now)


def test_garrison_migration_preserves_existing_draws_and_inflight_progress(war):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    season, member, run = _idle_garrison(war)
    member.troops_drawn = 700
    member.save(update_fields=["troops_drawn"])
    before = (run.status, run.travel_seconds, run.due_at, run.stationed_until, run.result.copy())
    import_module("gameplay.migrations.0262_season_garrison_merit_and_quota").update_garrison_rules(
        apps, SimpleNamespace(connection=connection)
    )
    season.refresh_from_db()
    run.refresh_from_db()
    member.refresh_from_db()
    config = season.rules["campaign"]
    assert config["economy"]["daily_troop_quota"] == 1500
    assert config["expeditions"]["station_hours_options"] == [1, 2, 4, 8]
    assert config["expeditions"]["garrison_merit_per_hour"] == 10
    assert member.troops_drawn == 700
    assert (run.status, run.travel_seconds, run.due_at, run.stationed_until, run.result) == before
