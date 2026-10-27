"""新后勤收益、门客共享日限与已出发快照兼容。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.selectors.season import get_season_context
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.economy import grant_pve_locked
from gameplay.services.season.material_rewards import reserve_material_reward_locked
from gameplay.services.season.quotas import guest_dispatch_counts
from gameplay.services.season.resource_rules import resource_caps
from tests.test_season_resource_integration import advance_resource_run, finish_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def logistics_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    current = load_campaign_rules()
    season.rules["campaign"]["resource_economy"] = current["resource_economy"]
    # 本组验证旧单场资源收益与额度；分波收集由独立专项覆盖。
    season.rules["campaign"]["resource_economy"]["pve"].pop("collection_waves", None)
    season.rules["campaign"]["expeditions"]["guest_daily_limit"] = 2
    season.save(update_fields=["rules"])
    member, guest = player()
    return season, member, guest, player, clock


def test_two_pve_then_third_dispatch_is_rejected_and_next_day_resets(logistics_world):
    season, member, guest, _, clock = logistics_world
    for _ in range(2):
        finish_resource_run(issue(member, guest, clock), clock)
    assert guest_dispatch_counts(season, timezone.localdate(clock[0]))[guest.pk] == 2
    before = member.expeditions.count()
    with pytest.raises(GameError, match="每天最多2次"):
        issue(member, guest, clock)
    assert member.expeditions.count() == before
    clock[0] += timedelta(days=1)
    assert guest_dispatch_counts(season, timezone.localdate(clock[0])).get(guest.pk, 0) == 0
    assert issue(member, guest, clock).pk


def test_pve_and_garrison_share_guest_limit_and_recall_does_not_refund(logistics_world):
    season, member, guest, _, clock = logistics_world
    finish_resource_run(issue(member, guest, clock), clock)
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = issue(member, guest, clock, mode="garrison", node=site.key)
    advance_resource_run(run, clock)
    assert run.status == "stationed"
    perform_action(member.manor, season.pk, "recall", str(uuid4()), {"run_id": run.pk})
    run.refresh_from_db()
    advance_resource_run(run, clock)
    assert guest_dispatch_counts(season, timezone.localdate(clock[0]))[guest.pk] == 2
    with pytest.raises(GameError, match="每天最多2次"):
        issue(member, guest, clock)


def test_cancel_unlaunched_rally_refunds_each_guest_quota(logistics_world):
    season, member, guest, _, clock = logistics_world
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 100
    state.save(update_fields=["troops"])
    result = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "node_key": "mountain:gate",
            "mode": "attack",
            "guest_ids": [guest.pk],
            "troops": {"dao_sheng": 10},
            "rally_target_members": 2,
        },
    )
    assert guest_dispatch_counts(season, timezone.localdate(clock[0]))[guest.pk] == 1
    token = str(uuid4())
    for _ in range(2):
        perform_action(member.manor, season.pk, "rally_cancel", token, {"rally_id": result["rally_id"]})
    assert guest_dispatch_counts(season, timezone.localdate(clock[0])).get(guest.pk, 0) == 0


@pytest.mark.parametrize(
    "node,expected",
    [
        ("mountain:inn", {"grain": 6000, "wood": 600, "iron": 300}),
        ("mountain:ruins", {"grain": 1200, "wood": 600, "iron": 3000}),
        ("forest:lodge", {"grain": 1200, "wood": 3000, "iron": 600}),
        ("river:docks", {"grain": 4000, "wood": 1500, "iron": 1500}),
    ],
)
def test_actual_pve_income_merit_and_training_estimate(logistics_world, node, expected):
    _, member, guest, _, clock = logistics_world
    run = issue(member, guest, clock, node=node)
    advance_resource_run(run, clock)
    action = member.actions.get(request_hash="pve-settlement", report_id=run.report_id)
    assert action.result["resources"] == expected
    assert action.result["merit"] == 30
    if node == "river:docks":
        assert action.result["normal_training_potential"] == 660


def test_full_storage_has_no_fake_material_merit_or_construction(logistics_world):
    season, member, _, _, clock = logistics_world
    state = season.faction_states.get(faction=member.faction)
    state.resources = resource_caps(season, member.faction, state.levels)
    state.save(update_fields=["resources"])
    with transaction.atomic():
        snapshot = reserve_material_reward_locked(member, "pve", clock[0])["material_reward"]
        result = grant_pve_locked(
            member, "mountain:inn", True, clock[0], event_key="full-stock", material_reward=snapshot
        )
        duplicate = grant_pve_locked(
            member, "mountain:inn", True, clock[0], event_key="full-stock", material_reward=snapshot
        )
    assert not any(result["resources"].values())
    assert result["merit"] == 0 and result["construction_points"] == 0
    assert duplicate["duplicate"]


def test_full_storage_is_visible_and_not_recommended_as_more_income(logistics_world):
    season, member, _, _, _ = logistics_world
    state = season.faction_states.get(faction=member.faction)
    state.resources = resource_caps(season, member.faction, state.levels)
    state.save(update_fields=["resources"])
    before = dict(state.resources)
    context = get_season_context(member.manor, season.pk)
    inn = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:inn")
    assert inn["pve_storage_full"]
    assert inn["pve_storage_limited"]
    assert context["command_guidance"].get("support", {}).get("modal") != "season-node-mountain:inn"
    state.refresh_from_db()
    assert state.resources == before


def test_guest_quota_exhaustion_changes_guidance_instead_of_suggesting_dispatch(logistics_world):
    season, member, guest, _, clock = logistics_world
    for _ in range(2):
        finish_resource_run(issue(member, guest, clock), clock)
    context = get_season_context(member.manor, season.pk)
    assert context["available_dispatch_guests"] == 0
    assert context["command_guidance"]["main"]["title"] == "今日门客次数已用完"
    assert context["command_guidance"]["main"]["modal"] == "season-team-status"
    assert not context["command_guidance"]["support"]


def test_migration_preserves_old_outbound_reward_snapshot_and_all_stocks(logistics_world):
    season, member, guest, _, clock = logistics_world
    season.rules["campaign"]["expeditions"].pop("guest_daily_limit")
    season.rules["campaign"]["resource_economy"]["pve"]["grain_run"] = {"grain": 600, "wood": 60, "iron": 30}
    season.rules["campaign"]["resource_economy"]["pve"].pop("action_merit")
    season.rules["campaign"]["resource_economy"]["pve"].pop("effective_material_merit")
    season.save(update_fields=["rules"])
    run = issue(member, guest, clock)
    before = deepcopy(run.result["material_reward"])
    stocks = list(season.faction_states.values("resources", "troops"))
    migration = import_module("gameplay.migrations.0258_season_logistics_and_guest_quota")
    migration.enable_logistics(apps, SimpleNamespace(connection=connection))
    migration.enable_logistics(apps, SimpleNamespace(connection=connection))
    run.refresh_from_db()
    assert run.result["material_reward"] == before
    assert list(season.faction_states.values("resources", "troops")) == stocks
    advance_resource_run(run, clock)
    action = member.actions.get(request_hash="pve-settlement", report_id=run.report_id)
    assert action.result["resources"] == {"grain": 600, "wood": 60, "iron": 30}
    assert action.result["merit"] == 20


def test_group_dispatch_charges_each_guest_and_failed_mixed_selection_charges_none(logistics_world):
    from guests.models import Guest

    season, member, guest, _, clock = logistics_world
    second = Guest.objects.create(
        manor=member.manor,
        template=guest.template,
        force=8000,
        defense_stat=8000,
        intellect=8000,
        agility=8000,
        current_hp=100,
    )
    second.current_hp = second.max_hp
    second.save(update_fields=["current_hp"])
    for _ in range(2):
        finish_resource_run(issue(member, guest, clock, guest_ids=[guest.pk, second.pk]), clock)
    counts = guest_dispatch_counts(season, timezone.localdate(clock[0]))
    assert counts[guest.pk] == counts[second.pk] == 2
    fresh = Guest.objects.create(manor=member.manor, template=guest.template, current_hp=100)
    before = member.expeditions.count()
    with pytest.raises(GameError, match="每天最多2次"):
        issue(member, guest, clock, guest_ids=[guest.pk, fresh.pk])
    assert member.expeditions.count() == before
    assert guest_dispatch_counts(season, timezone.localdate(clock[0])).get(fresh.pk, 0) == 0


def test_partial_material_storage_proportionally_scales_merit_and_construction(logistics_world):
    season, member, _, _, clock = logistics_world
    state = season.faction_states.get(faction=member.faction)
    caps = resource_caps(season, member.faction, state.levels)
    state.resources = {key: caps[key] - amount for key, amount in {"grain": 3000, "wood": 300, "iron": 150}.items()}
    state.save(update_fields=["resources"])
    with transaction.atomic():
        snapshot = reserve_material_reward_locked(member, "pve", clock[0])["material_reward"]
        result = grant_pve_locked(
            member, "mountain:inn", True, clock[0], event_key="half-stock", material_reward=snapshot
        )
    assert result["resources"] == {"grain": 3000, "wood": 300, "iron": 150}
    assert result["merit"] == 15 and result["construction_points"] == 10
