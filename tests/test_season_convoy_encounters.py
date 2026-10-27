"""运输多人遭遇战：弱队不能抢保护，合并交战，余货继续交付。"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport
from core.exceptions import GameError
from gameplay.models import Message, SeasonExpedition
from gameplay.services.season.expeditions import process_expedition, recall_locked
from gameplay.services.season.quotas import daily_dispatches_used
from guests.models import GuestStatus
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


def player(world, faction, *, weak=False):
    _, make, _ = world
    member, guest = make(faction)
    if weak:
        guest.force = guest.defense_stat = guest.intellect = 1
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["force", "defense_stat", "intellect", "current_hp"])
    return member, guest


def launch(world, *, weak_guard=True, troops=None):
    season, _, clock = world
    owner, guest = player(world, "history", weak=weak_guard)
    site = season.sites.get(key="mountain:gate")
    site.owner = owner.faction
    site.durability = 0
    site.save(update_fields=["owner", "durability"])
    run = issue(
        owner, guest, clock, mode="transport", node="river:docks", target_site_key=site.key, troops=troops or {}
    )
    original = dict(run.cargo)
    advance_resource_run(run, clock)
    assert run.status == "transporting"
    return owner, run, original


def join(world, target, *, escort=False, weak=False, troops=None):
    _, _, clock = world
    member, guest = player(world, "history" if escort else "fiction", weak=weak)
    run = issue(
        member,
        guest,
        clock,
        mode="escort" if escort else "intercept",
        node="river:docks",
        target_run_id=target.pk,
        troops=troops or {},
    )
    return member, guest, run


def teams(report, side):
    return {row["guest_id"] for row in getattr(report, side + "_team") if row.get("guest_id") is not None}


def test_weak_interceptor_cannot_end_window_or_block_later_strong_players(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    _, target, original = launch(world)
    deadline = target.due_at
    weak_member, weak_guest, weak = join(world, target, weak=True)
    advance_resource_run(weak, clock)
    target.refresh_from_db()
    assert weak.status == "transporting" and weak.report_id is None
    assert target.due_at == deadline and target.cargo == original and not target.intercepted
    assert not BattleReport.objects.exists()
    strong_member, strong_guest, strong = join(world, target)
    advance_resource_run(strong, clock)
    assert not BattleReport.objects.exists()
    advance_resource_run(target, clock)
    weak.refresh_from_db()
    strong.refresh_from_db()
    assert weak.report_id == strong.report_id == target.report_id
    assert teams(target.report, "attacker") == {weak_guest.pk, strong_guest.pk}
    assert BattleReport.objects.count() == 1


def test_all_timely_players_merge_even_if_owner_timer_runs_before_arrival_jobs(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    _, target, original = launch(world)
    attackers = [join(world, target) for _ in range(2)]
    escorts = [join(world, target, escort=True, weak=True) for _ in range(2)]
    assert all(run.status == "outbound" and run.due_at < target.due_at for _, _, run in attackers + escorts)
    advance_resource_run(target, clock)
    assert target.report_id and BattleReport.objects.count() == 1
    assert teams(target.report, "attacker") == {guest.pk for _, guest, _ in attackers}
    assert teams(target.report, "defender") == set(target.guest_ids) | {guest.pk for _, guest, _ in escorts}
    for _, _, run in attackers + escorts:
        run.refresh_from_db()
        assert run.report_id == target.report_id and run.battles == 1 and run.status == "returning"
    assert Message.objects.filter(battle_report=target.report).count() == 5
    assert target.status == "returning" and not any(target.cargo.values())
    loot = target.result.get("convoy_loot", {})
    delivered = target.result["delivered_resources"]
    assert delivered == {key: amount - loot.get(key, 0) for key, amount in original.items()}
    assert all(loot.get(key, 0) <= amount * 30 // 100 for key, amount in original.items())
    assert not process_expedition(target.pk, clock[0])
    assert BattleReport.objects.count() == 1


def test_extra_weak_arrival_does_not_occupy_one_of_five_battle_places(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    _, target, _ = launch(world)
    season = world[0]
    key = next(iter(season.rules["campaign"]["economy"]["initial_troops"]))
    state = season.faction_states.get(faction="fiction")
    state.troops[key] = 100
    state.save(update_fields=["troops"])
    weak_member, weak_guest, weak = join(world, target, weak=True, troops={key: 100})
    assert weak.result["operation_costs"]["convoy_battle_balance"] == 3
    attackers = [join(world, target) for _ in range(5)]
    escorts = [join(world, target, escort=True) for _ in range(5)]
    advance_resource_run(target, clock)
    assert target.report_id
    assert teams(target.report, "attacker") == {guest.pk for _, guest, _ in attackers}
    assert weak_guest.pk not in teams(target.report, "attacker")
    assert len(teams(target.report, "defender")) == 5
    assert set(target.guest_ids) <= teams(target.report, "defender")
    assert len([guest.pk for _, guest, _ in escorts if guest.pk in teams(target.report, "defender")]) == 4
    weak.refresh_from_db()
    assert weak.report_id is None and weak.battles == 0 and weak.result["refund_quota"]
    assert weak.result["operation_costs"]["convoy_battle_balance"] == 3
    assert not any(row["kind"] == "battle" for row in weak.result["operation_costs"]["expenses"])
    advance_resource_run(weak, clock)
    book = weak.result["operation_costs"]
    assert book["convoy_battle_balance"] == 0
    assert [row["resources"]["grain"] for row in book["expenses"] if row["kind"] == "unused_rations_refund"] == [3]
    weak_guest.refresh_from_db()
    assert weak.result["quota_refunded"] and weak_guest.status == GuestStatus.IDLE
    assert daily_dispatches_used(weak_member, timezone.localdate(weak.launched_at)) == 0
    assert Message.objects.filter(battle_report=target.report).count() == 10


def test_single_attacker_gets_one_loot_and_seventy_percent_still_delivers(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    _, target, original = launch(world)
    member, guest, attacker = join(world, target)
    advance_resource_run(attacker, clock)
    assert attacker.report_id is None
    advance_resource_run(target, clock)
    attacker.refresh_from_db()
    assert target.report.winner == "attacker"
    loot = {key: amount * 30 // 100 for key, amount in original.items()}
    assert target.result["convoy_loot"] == loot
    assert target.result["delivered_resources"] == {key: amount - loot[key] for key, amount in original.items()}
    member.refresh_from_db()
    merit = member.merit
    assert merit == 90
    assert member.actions.filter(kind="merit_credit").count() == 2
    assert Message.objects.filter(manor=member.manor, battle_report=target.report).count() == 1
    advance_resource_run(attacker, clock)
    member.refresh_from_db()
    assert member.merit == merit and BattleReport.objects.count() == 1


def test_uncontested_friendly_escort_returns_guest_and_troop_quota(resource_campaign):
    world = resource_campaign
    season, _, clock = world
    owner, target, original = launch(world)
    state = season.faction_states.get(faction="history")
    key = next(iter(season.rules["campaign"]["economy"]["initial_troops"]))
    state.troops[key] = 100
    state.save(update_fields=["troops"])
    member, guest, escort = join(world, target, escort=True, troops={key: 100})
    member.refresh_from_db()
    assert member.troops_drawn == 100
    advance_resource_run(escort, clock)
    advance_resource_run(target, clock)
    escort.refresh_from_db()
    assert target.result["delivered_resources"] == original and target.report_id is None
    assert escort.report_id is None and escort.result["refund_quota"]
    assert escort.result["operation_costs"]["convoy_battle_balance"] == 3
    assert not any(row["kind"] == "battle" for row in escort.result["operation_costs"]["expenses"])
    state.refresh_from_db()
    grain_before_return = state.resources["grain"]
    advance_resource_run(escort, clock)
    state.refresh_from_db()
    assert state.resources["grain"] == grain_before_return + 3
    assert escort.result["operation_costs"]["convoy_battle_balance"] == 0
    assert [
        row["resources"]["grain"]
        for row in escort.result["operation_costs"]["expenses"]
        if row["kind"] == "unused_rations_refund"
    ] == [3]
    member.refresh_from_db()
    guest.refresh_from_db()
    assert escort.result["quota_refunded"] and member.troops_drawn == 0 and member.merit == 0
    assert guest.status == GuestStatus.IDLE
    assert daily_dispatches_used(member, timezone.localdate(escort.launched_at)) == 0
    assert daily_dispatches_used(owner, timezone.localdate(target.launched_at)) == 1


def test_late_join_is_rejected_before_occupying_guest_or_creating_mission(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    _, target, _ = launch(world)
    clock[0] = target.due_at - timedelta(seconds=30)
    member, guest = player(world, "fiction")
    before = SeasonExpedition.objects.count()
    with pytest.raises(GameError, match="窗口|抵达"):
        issue(member, guest, clock, mode="intercept", node="river:docks", target_run_id=target.pk)
    guest.refresh_from_db()
    assert SeasonExpedition.objects.count() == before and guest.status == GuestStatus.IDLE
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 0


def test_cancelling_convoy_releases_waiting_and_inflight_joiners_without_battle(resource_campaign):
    world = resource_campaign
    _, _, clock = world
    owner, target, _ = launch(world)
    members = [join(world, target), join(world, target, escort=True)]
    with transaction.atomic():
        recall_locked(owner, target.pk, clock[0] + timedelta(seconds=20))
    target.refresh_from_db()
    assert target.status == "returning" and not BattleReport.objects.exists()
    for member, guest, run in members:
        run.refresh_from_db()
        assert run.status == "returning" and run.result["refund_quota"] and run.report_id is None
        advance_resource_run(run, clock)
        assert run.result["quota_refunded"]
        guest.refresh_from_db()
        assert guest.status == GuestStatus.IDLE


def test_real_encounter_uses_each_side_reserved_food_even_after_both_public_grain_pools_are_empty(resource_campaign):
    world = resource_campaign
    season, _, clock = world
    key = next(iter(season.rules["campaign"]["economy"]["initial_troops"]))
    for state in season.faction_states.all():
        state.troops[key] = 1000
        state.save(update_fields=["troops"])
    _, target, original = launch(world, troops={key: 100})
    _, _, attacker = join(world, target, troops={key: 100})
    assert target.result["operation_costs"]["convoy_battle_balance"] == 3
    assert attacker.result["operation_costs"]["convoy_battle_balance"] == 3
    advance_resource_run(attacker, clock)
    for state in season.faction_states.all():
        state.resources["grain"] = 0
        state.save(update_fields=["resources"])
    advance_resource_run(target, clock)
    attacker.refresh_from_db()
    assert target.report_id and target.report_id == attacker.report_id
    assert target.status == attacker.status == "returning"
    assert target.report.winner == "attacker"
    assert target.result["delivered_resources"] == {
        resource: amount - amount * 30 // 100 for resource, amount in original.items()
    }
    assert all(state.resources["grain"] == 0 for state in season.faction_states.all())
    for run in (target, attacker):
        book = run.result["operation_costs"]
        assert book["convoy_battle_balance"] == 0
        assert [row["resources"]["grain"] for row in book["expenses"] if row["kind"] == "battle"] == [3]
