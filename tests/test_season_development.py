"""低人口、差异化任务、真实补给与贡献防重的端到端回归。"""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonAction, SeasonExpedition, SeasonMember
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked, produce_locked
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.merit import battle_merit_shares, daily_merit_summary, grant_merit_locked
from gameplay.services.season.population import refresh_population_locked, victory_qualified
from gameplay.services.season.warfare import ensure_sites_locked
from guests.models import Guest, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def development(user_factory, monkeypatch):
    from gameplay.services.season import actions

    clock = [timezone.now()]
    monkeypatch.setattr(actions.timezone, "now", lambda: clock[0])
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    # 覆盖旧版开拓战局；新版敌军阵容由专项验收。
    rules["campaign"].pop("npc_encounters", None)
    rules["campaign"]["expeditions"]["guest_daily_limit"] = 0  # 兼容旧快照，限次由专项验收。
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    season = Season.objects.create(
        name="完整开拓战局", starts_at=clock[0] - timedelta(days=5), ends_at=clock[0] + timedelta(days=85), rules=rules
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="乡勇", rarity="black", archetype="military"
    )
    with transaction.atomic():
        ensure_factions_locked(season, clock[0])
        ensure_sites_locked(season, clock[0])
        season.faction_states.update(produced_at=clock[0])

    def player(faction="fiction"):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        guest = Guest.objects.create(
            manor=manor, template=template, force=800, defense_stat=800, intellect=800, agility=800, current_hp=100
        )
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
        return member, guest

    return season, player, clock


def command(member, action, payload, token=None):
    return perform_action(member.manor, member.season_id, action, str(token or uuid4()), payload)


def dispatch(member, guest, node, mode, **extra):
    result = command(
        member, "dispatch", {"guest_ids": [guest.pk], "node_key": node, "mode": mode, "troops": {}, **extra}
    )
    return SeasonExpedition.objects.get(pk=result["run_id"])


def advance(run, clock):
    run.refresh_from_db()
    clock[0] = run.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()


@pytest.mark.parametrize(
    "counts,mode", [((1, 0), "pioneer"), ((1, 1), "skirmish"), ((5, 5), "standard"), ((100, 100), "standard")]
)
def test_population_profiles_use_effective_players_and_freeze_daily(development, django_user_model, counts, mode):
    season, _, clock = development
    for faction, count in zip(("fiction", "history"), counts):
        users = django_user_model.objects.bulk_create(
            [django_user_model(username=f"{faction}-{i}", password="!") for i in range(count)]
        )
        manors = Manor.objects.bulk_create([Manor(user=user, name=f"{faction}{i}") for i, user in enumerate(users)])
        members = SeasonMember.objects.bulk_create(
            [SeasonMember(season=season, manor=manor, faction=faction) for manor in manors]
        )
        SeasonAction.objects.bulk_create(
            [
                SeasonAction(
                    member=member,
                    token=uuid4(),
                    request_hash="effective-activity",
                    kind="logistics",
                    date=timezone.localdate(clock[0]),
                    result={"merit": 1},
                )
                for member in members
            ]
        )
    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0], force=True)
        assert profile["mode"] == mode
        assert tuple(profile["factions"][key]["count"] for key in ("fiction", "history")) == counts
        before = deepcopy(profile)
        assert refresh_population_locked(season, clock[0] + timedelta(minutes=5)) == before


def test_signup_alone_does_not_inflate_active_population(development):
    season, player, clock = development
    player()
    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0], force=True)
    assert profile["factions"]["fiction"]["count"] == 0
    assert profile["factions"]["fiction"]["rate"] == 0


def test_population_reduction_keeps_existing_overflow_and_does_not_heal_damaged_site(development):
    season, player, clock = development
    player()
    state = season.faction_states.get(faction="fiction")
    state.resources = {key: 500000 for key in state.resources}
    state.troops = {key: 50000 for key in state.troops}
    state.save(update_fields=["resources", "troops"])
    site = season.sites.get(key="mountain:gate")
    site.guards -= 1
    site.save(update_fields=["guards"])
    with transaction.atomic():
        refresh_population_locked(season, clock[0], force=True)
        produce_locked(season, clock[0] + timedelta(hours=1))
    state.refresh_from_db()
    site.refresh_from_db()
    assert all(count == 500000 for count in state.resources.values())
    assert all(count == 50000 for count in state.troops.values())
    assert site.guards == 2


def test_pending_member_can_explore_and_resources_transfer_once_on_assignment(development):
    from gameplay.services.season.registration import assign_factions_locked

    season, player, clock = development
    season.starts_at = clock[0] - timedelta(hours=1)
    season.ends_at = season.starts_at + timedelta(days=90)
    season.save(update_fields=["starts_at", "ends_at"])
    member, guest = player("")
    run = dispatch(member, guest, "mountain:ruins", "explore")
    advance(run, clock)
    member.refresh_from_db()
    assert member.pending_resources == {"grain": 50, "wood": 30, "iron": 20}
    assert member.pending_construction == 10 and member.merit == 20
    assert member.intel_records.get().faction == ""
    advance(run, clock)
    with transaction.atomic():
        at = season.starts_at + timedelta(days=3)
        assert assign_factions_locked(season, at) == 1
        assert assign_factions_locked(season, at) == 0
    member.refresh_from_db()
    assert member.faction and member.pending_resources == {} and member.pending_construction == 0
    assert member.intel_records.get().faction == member.faction
    state = season.faction_states.get(faction=member.faction)
    assert state.resources["grain"] >= 5050 and state.construction_points == 10


def test_exploration_is_shared_and_boosts_only_one_real_pve(development):
    season, player, clock = development
    first, guest = player()
    second, _ = player()
    run = dispatch(first, guest, "mountain:ruins", "explore")
    advance(run, clock)
    assert run.report_id is None
    intel = first.intel_records.get()
    assert intel.payload["bonus_available"]
    with pytest.raises(GameError, match="有效情报"):
        dispatch(second, second.manor.guests.first(), "mountain:ruins", "explore")
    # 情报全阵营共享；盟友立即利用，避免30分钟基础行军后的往返耗尽有效期。
    pve = dispatch(second, second.manor.guests.first(), "mountain:ruins", "pve")
    advance(pve, clock)
    action = second.actions.filter(kind="logistics").first()
    assert action.result["resources"]["iron"] == 432
    intel.refresh_from_db()
    assert not intel.payload["bonus_available"]
    assert not process_expedition(pve.pk, clock[0])


def test_transport_moves_real_cargo_into_owned_site_without_multiplication(development):
    season, player, clock = development
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    state = season.faction_states.get(faction=member.faction)
    before = dict(state.resources)
    run = dispatch(member, guest, "river:docks", "transport", target_site_key=site.key)
    state.refresh_from_db()
    assert state.resources["grain"] == before["grain"] - 300
    advance(run, clock)
    advance(run, clock)
    site.refresh_from_db()
    assert site.supplies == {"grain": 300, "wood": 150, "iron": 100}
    member.refresh_from_db()
    assert member.merit == 90
    assert run.cargo == {} and run.status == "returning"
    before_stock = deepcopy(site.supplies)
    advance(run, clock)
    site.refresh_from_db()
    assert site.supplies == before_stock


def test_npc_order_exists_without_other_players_and_consumes_cargo(development):
    season, player, clock = development
    member, guest = player()
    run = dispatch(member, guest, "river:docks", "transport")
    assert run.result["activity"]["npc_order"]
    advance(run, clock)
    advance(run, clock)
    assert run.result["delivered_resources"] == {"grain": 300, "wood": 150, "iron": 100}
    assert not season.sites.exclude(supplies={}).exists()


def test_missing_transport_target_is_blocked_and_npc_intercept_is_explicit_pve(development):
    _, player, clock = development
    member, guest = player()
    with pytest.raises(GameError, match="没有可拦截"):
        dispatch(member, guest, "river:docks", "intercept", target_run_id=999)
    run = dispatch(member, guest, "river:docks", "npc_intercept")
    advance(run, clock)
    assert run.report_id and run.result["activity"]["npc_order"]
    assert member.actions.filter(kind="logistics").exists()
    assert not member.actions.filter(opponent__isnull=False).exists()


def test_real_interception_transfers_only_30_percent_and_preserves_the_remainder(development):
    from gameplay.services.season.activities import transfer_intercept_locked

    season, player, clock = development
    carrier, guest = player("history")
    raider, rg = player("fiction")
    target = dispatch(carrier, guest, "river:docks", "transport")
    advance(target, clock)
    run = dispatch(raider, rg, "river:docks", "intercept", target_run_id=target.pk)
    with transaction.atomic():
        reward = transfer_intercept_locked(run, target, True, clock[0])
        again = transfer_intercept_locked(run, target, True, clock[0])
    assert reward["loot"] == {"grain": 90, "wood": 45, "iron": 30}
    assert target.cargo == {"grain": 210, "wood": 105, "iron": 70}
    assert again["loot"] == {} and target.intercepted


def test_daily_merit_cap_is_shared_and_duplicate_event_cannot_pay_twice(development):
    _, player, clock = development
    member, _ = player()
    with transaction.atomic():
        first = grant_merit_locked(member, 250, "combat", clock[0], event_key="battle:1")
        assert grant_merit_locked(member, 250, "combat", clock[0], event_key="battle:1")["duplicate"]
        second = grant_merit_locked(member, 100, "logistics", clock[0], event_key="shipment:1")
        third = grant_merit_locked(member, 100, "combat", clock[0], event_key="battle:2")
    assert first["merit"] == 250 and second["merit"] == 50 and third["merit"] == 0
    summary = daily_merit_summary(member, clock[0])
    assert summary == {"combat": 250, "logistics": 50, "earned": 300, "cap": 300, "remaining": 0}
    member.refresh_from_db()
    assert member.merit == 300 and member.coins == 300


def test_battle_pool_rewards_participation_and_effective_damage_not_overkill(development):
    _, player, _ = development
    first, g1 = player()
    second, g2 = player()
    runs = [
        SimpleNamespace(pk=i, member=m, guest_ids=[g.pk], result={}, troops={})
        for i, (m, g) in enumerate(((first, g1), (second, g2)), 1)
    ]
    report = SimpleNamespace(
        rounds=[
            {
                "events": [
                    {"side": "attacker", "actor_guest_id": g1.pk, "applied_damage": 200, "overkill_damage": 999999}
                ]
            }
        ],
        attacker_team=[],
        defender_team=[],
    )
    shares = battle_merit_shares(runs, report, "attacker", True)
    assert sum(row["merit"] for row in shares.values()) == 120
    assert shares[2]["merit"] == 18 and shares[1]["merit"] == 102
    assert shares[1]["metrics"]["damage"] == 200


def test_garrison_without_supply_withdraws_safely_and_does_not_gain_idle_merit(development):
    from gameplay.services.season.supply import settle_supplies_locked

    season, player, clock = development
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = dispatch(member, guest, site.key, "garrison")
    advance(run, clock)
    with transaction.atomic():
        settle_supplies_locked(season, clock[0])
        settle_supplies_locked(season, clock[0] + timedelta(minutes=5))
    run.refresh_from_db()
    guest.refresh_from_db()
    assert run.status == "returning" and guest.current_hp == guest.max_hp
    member.refresh_from_db()
    assert member.merit == 0


def test_unopposed_season_cannot_award_competitive_victory(development):
    season, player, clock = development
    player()
    assert not victory_qualified(season)


def test_transport_reserves_only_actual_site_capacity_and_blocks_duplicate_cargo(development):
    season, player, clock = development
    first, guest = player()
    second, other_guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = first.faction
    site.supplies = {"grain": 4990, "wood": 5000, "iron": 5000}
    site.save(update_fields=["owner", "supplies"])
    run = dispatch(first, guest, "river:docks", "transport", target_site_key=site.key)
    assert run.cargo == {"grain": 10, "wood": 0, "iron": 0}
    with pytest.raises(GameError, match="在途补给"):
        dispatch(second, other_guest, "river:docks", "transport", target_site_key=site.key)
    advance(run, clock)
    advance(run, clock)
    site.refresh_from_db()
    first.refresh_from_db()
    assert site.supplies["grain"] == 5000
    assert first.merit == 1


@pytest.mark.parametrize("fought", [False, True])
def test_transport_target_lost_refunds_cargo_and_only_unused_participation(development, fought):
    from battle.models import BattleReport

    season, player, clock = development
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = dispatch(member, guest, "river:docks", "transport", target_site_key=site.key)
    advance(run, clock)
    if fought:
        # 使用真实战斗结算生成报告，验证曾交战运输不能再退额度。
        from gameplay.services.season.expeditions import _fight

        with transaction.atomic():
            _fight(run, None, clock[0])
        assert BattleReport.objects.filter(pk=run.report_id).exists()
    site.owner = "history"
    site.save(update_fields=["owner"])
    before = season.faction_states.get(faction=member.faction).resources.copy()
    advance(run, clock)
    state = season.faction_states.get(faction=member.faction)
    assert state.resources["grain"] >= before["grain"] + 300
    assert run.cargo == {} and run.result["refund_quota"] is (not fought)
    assert run.status == "returning"
    member.refresh_from_db()
    assert member.merit == 0


def test_npc_order_cannot_consume_war_reserve(development):
    season, player, _ = development
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.resources = {"grain": 2500, "wood": 2500, "iron": 2500}
    state.save(update_fields=["resources"])
    with pytest.raises(GameError, match="战备保底"):
        dispatch(member, guest, "river:docks", "transport")
    state.refresh_from_db()
    assert state.resources["grain"] == 2500
    assert not member.expeditions.exists()


def test_legacy_transport_keeps_original_rules_when_new_activities_enabled(development):
    season, player, clock = development
    member, guest = player()
    run = dispatch(member, guest, "river:docks", "transport")
    run.result.pop("activities_version")
    run.result.pop("activity")
    run.save(update_fields=["result"])
    before = season.faction_states.get(faction=member.faction).resources.copy()
    advance(run, clock)
    advance(run, clock)
    state = season.faction_states.get(faction=member.faction)
    assert state.resources["grain"] >= before["grain"] + 375
    assert run.cargo == {} and run.status == "returning"


def test_scout_can_choose_each_strategic_site_and_shared_intel_blocks_repeat(development):
    season, player, clock = development
    for key in season.rules["campaign"]["sites"]:
        member, guest = player()
        run = dispatch(member, guest, "mountain:ruins", "explore", scout_site_key=key)
        advance(run, clock)
        assert member.intel_records.get().payload["target_site_key"] == key
        repeat, rg = player()
        with pytest.raises(GameError, match="有效情报"):
            dispatch(repeat, rg, "forest:lodge", "explore", scout_site_key=key)


def test_daily_demolition_budget_cannot_be_reset_by_repair(development):
    from gameplay.services.season.expeditions import _record_progress

    _, player, clock = development
    member, _ = player()
    run = SimpleNamespace(pk=991, member=member, node_key="mountain:gate", result={}, battles=0)
    with transaction.atomic():
        _record_progress(run, 80, clock[0], "首轮破城")
        run.result["progress_step"] = 1
        _record_progress(run, 80, clock[0], "维修后再次破城")
        run.result["progress_step"] = 2
        _record_progress(run, 80, clock[0], "继续破城")
    member.refresh_from_db()
    assert member.merit == 100
    assert sum(a.result["requested_merit"] for a in member.actions.filter(request_hash="site-progress")) == 100


def test_delayed_supply_scanner_charges_stationed_time_through_expiry(development):
    from gameplay.services.season.supply import settle_supplies_locked

    season, player, clock = development
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.supplies = {"grain": 100}
    site.save(update_fields=["owner", "supplies"])
    run = dispatch(member, guest, site.key, "garrison")
    advance(run, clock)
    with transaction.atomic():
        settle_supplies_locked(season, clock[0])
        settle_supplies_locked(season, run.due_at + timedelta(minutes=10))
        settle_supplies_locked(season, run.due_at + timedelta(minutes=20))
    site.refresh_from_db()
    # 一小时驻防12次补给；扫描延迟既不免粮，也不扣离开后的粮。
    assert site.supplies["grain"] == 76


def test_effective_healing_and_nested_hits_enter_shared_pool_without_overheal(development):
    _, player, _ = development
    member, guest = player()
    support, healer = player()
    runs = [
        SimpleNamespace(pk=i, member=m, guest_ids=[g.pk], result={}, troops={})
        for i, (m, g) in enumerate(((member, guest), (support, healer)), 1)
    ]
    report = SimpleNamespace(
        rounds=[
            {
                "events": [
                    {
                        "side": "attacker",
                        "actor_guest_id": guest.pk,
                        "applied_damage": 100,
                        "additional_attacks": [{"side": "attacker", "actor_guest_id": guest.pk, "applied_damage": 50}],
                        "passive_events_after": [
                            {"side": "attacker", "source_guest_id": healer.pk, "healed": 99999, "effective_healed": 50}
                        ],
                    }
                ]
            }
        ],
        attacker_team=[],
        defender_team=[],
    )
    shares = battle_merit_shares(runs, report, "attacker", True)
    assert sum(row["merit"] for row in shares.values()) == 120
    assert shares[1]["metrics"]["damage"] == 150
    assert shares[2]["metrics"]["support"] == 50 and shares[2]["merit"] > 18


def test_population_refresh_does_not_resize_an_intact_site_already_targeted(development):
    season, player, clock = development
    member, guest = player()
    result = command(
        member, "rally_create", {"guest_ids": [guest.pk], "node_key": "mountain:gate", "mode": "attack", "troops": {}}
    )
    with transaction.atomic():
        refresh_population_locked(season, clock[0], force=True)
    site = season.sites.get(key="mountain:gate")
    assert site.guards == 3 and site.durability == season.rules["campaign"]["fortifications"]["hitpoints"]["gate_hp"]
    assert SeasonExpedition.objects.get(pk=result["run_id"]).status == "gathering"


def test_new_rally_vs_supplied_garrison_records_actual_performance_for_both_sides(development):
    season, player, clock = development
    defender, dg = player("history")
    attacker, ag = player("fiction")
    site = season.sites.get(key="mountain:gate")
    site.owner, site.supplies, site.supply_updated_at = "history", {"grain": 100}, clock[0]
    site.save(update_fields=["owner", "supplies", "supply_updated_at"])
    defense = dispatch(defender, dg, site.key, "garrison")
    advance(defense, clock)
    result = command(
        attacker, "rally_create", {"guest_ids": [ag.pk], "node_key": site.key, "mode": "attack", "troops": {}}
    )
    attack = SeasonExpedition.objects.get(pk=result["run_id"])
    advance(attack, clock)
    advance(attack, clock)
    attack.refresh_from_db()
    defense.refresh_from_db()
    site.refresh_from_db()
    assert attack.report_id and defense.report_id == attack.report_id
    assert site.supplies["grain"] < 100
    for member, opponent in ((attacker, defender), (defender, attacker)):
        action = member.actions.get(kind="combat", report=attack.report)
        assert action.opponent_id == opponent.pk
        assert action.result["requested_merit"] > 0
        assert sum(action.result["metrics"].values()) > 0
        member.refresh_from_db()
        garrison_merit = defense.result.get("garrison_service", {}).get("merit", 0) if member.pk == defender.pk else 0
        assert member.merit == action.result["merit"] + garrison_merit
