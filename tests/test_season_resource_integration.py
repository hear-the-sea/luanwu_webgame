"""三资源经济跨任务验收：收益档、真实需求、归属与查询只读。"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember
from gameplay.selectors.season import get_season_context
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.resource_rules import resource_caps
from gameplay.services.season.warfare import capture_locked, ensure_sites_locked
from guests.models import Guest, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(user_factory, monkeypatch):
    from gameplay.services.season import actions

    # 连续七次30分钟基础行军仍在同一天，独立核对每日收益档。
    clock = [timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)]
    monkeypatch.setattr(actions.timezone, "now", lambda: clock[0])
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    # 此夹具验证旧赛季快照；新版 NPC 阵容由专项测试覆盖。
    rules["campaign"].pop("npc_encounters", None)
    rules["campaign"]["expeditions"]["guest_daily_limit"] = 0  # 兼容旧快照，限次由专项验收。
    # 保留原有规则回归；新城防与疲敝由专项覆盖。
    rules["campaign"]["fortifications"]["enabled"] = False
    # 保留旧三资源快照回归；提高募训产能在独立专项中验收。
    rules["campaign"]["resource_economy"]["training"].update(
        {
            "normal_daily_capacity_per_active": 200,
            "normal_target_per_active": 600,
            "cavalry_daily_capacity_per_active": 10,
            "cavalry_target_per_active": 50,
        }
    )
    rules["campaign"]["resource_economy"]["pve"].update(
        {
            "grain_run": {"grain": 600, "wood": 60, "iron": 30},
            "wood_run": {"grain": 80, "wood": 420, "iron": 40},
            "iron_run": {"grain": 80, "wood": 40, "iron": 360},
            "mixed_run": {"grain": 240, "wood": 130, "iron": 110},
        }
    )
    rules["campaign"]["resource_economy"]["pve"].pop("action_merit", None)
    rules["campaign"]["resource_economy"]["pve"].pop("effective_material_merit", None)
    # 本文件覆盖旧单场赛季快照；新版多波另有专项验收。
    rules["campaign"]["resource_economy"]["pve"].pop("collection_waves", None)
    rules["campaign"]["resource_economy"]["storage"]["reference_pve_daily"] = {"grain": 1360, "wood": 580, "iron": 460}
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"]["resource_economy"]["enabled"] = True
    population = {
        "mode": "standard",
        "factions": {
            faction: {"count": 10, "economic_count": 10, "rate": 1, "capacity": 1, "threshold": 1}
            for faction in ("fiction", "history")
        },
    }
    season = Season.objects.create(
        name="三资源军务验收",
        starts_at=clock[0] - timedelta(days=5),
        ends_at=clock[0] + timedelta(days=85),
        rules=rules,
        population=population,
        population_updated_at=clock[0],
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="验收门客", rarity="black", archetype="military"
    )
    with transaction.atomic():
        states = ensure_factions_locked(season, clock[0])
        ensure_sites_locked(season, clock[0])
        # 冻结自动产出以单独核对任务的真实资源转移。
        for state in states.values():
            state.resources = {"grain": 10000, "wood": 10000, "iron": 10000}
            state.produced_at = clock[0] + timedelta(days=2)
            state.save(update_fields=["resources", "produced_at"])

    def player(faction="fiction"):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        member = SeasonMember.objects.create(season=season, manor=manor, faction=faction)
        guest = Guest.objects.create(
            manor=manor,
            template=template,
            force=8000,
            defense_stat=8000,
            intellect=8000,
            agility=8000,
            current_hp=1,
        )
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
        return member, guest

    return season, player, clock


def issue(member, guest, clock, mode="pve", node="mountain:inn", **extra):
    result = perform_action(
        member.manor,
        member.season_id,
        "dispatch",
        str(uuid4()),
        {"guest_ids": [guest.pk], "troops": {}, "mode": mode, "node_key": node, **extra},
    )
    return SeasonExpedition.objects.get(pk=result["run_id"])


def advance_resource_run(run, clock):
    run.refresh_from_db()
    clock[0] = run.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()


def finish_resource_run(run, clock):
    for _ in range(4):
        if run.status in {"completed", "failed"}:
            return
        advance_resource_run(run, clock)
    assert run.status == "completed"


def test_pve_materials_have_four_full_two_half_and_no_seventh_resource_reward(resource_campaign):
    _, player, clock = resource_campaign
    member, guest = player()
    received = []
    for _ in range(7):
        run = issue(member, guest, clock)
        advance_resource_run(run, clock)
        reward = member.actions.get(kind="logistics", report_id=run.report_id)
        received.append(reward.result["resources"])
        finish_resource_run(run, clock)
    assert [row.get("grain", 0) for row in received] == [600, 600, 600, 600, 300, 300, 0]
    assert [row.get("wood", 0) for row in received] == [60, 60, 60, 60, 30, 30, 0]
    assert [row.get("iron", 0) for row in received] == [30, 30, 30, 30, 15, 15, 0]


def test_npc_raid_and_pve_use_the_same_material_budget(resource_campaign):
    _, player, clock = resource_campaign
    member, guest = player()
    for _ in range(4):
        finish_resource_run(issue(member, guest, clock), clock)
    raid = issue(member, guest, clock, mode="npc_intercept", node="river:docks")
    advance_resource_run(raid, clock)
    reward = member.actions.get(kind="logistics", report_id=raid.report_id)
    assert reward.result["resources"] == {"grain": 120, "wood": 65, "iron": 55}
    finish_resource_run(raid, clock)
    next_run = issue(member, guest, clock)
    advance_resource_run(next_run, clock)
    assert member.actions.get(kind="logistics", report_id=next_run.report_id).result["resources"]["grain"] == 300


def test_exploration_does_not_consume_a_full_material_reward_tier(resource_campaign):
    _, player, clock = resource_campaign
    member, guest = player()
    finish_resource_run(issue(member, guest, clock, mode="explore", node="mountain:ruins"), clock)
    received = []
    for _ in range(4):
        run = issue(member, guest, clock)
        advance_resource_run(run, clock)
        received.append(member.actions.get(kind="logistics", report_id=run.report_id).result["resources"]["grain"])
        finish_resource_run(run, clock)
    assert received == [600] * 4


def test_transport_requires_real_frontline_demand_and_reserves_it_once(resource_campaign):
    season, player, clock = resource_campaign
    first, guest = player()
    second, second_guest = player()
    with pytest.raises(GameError):
        issue(first, guest, clock, mode="transport", node="river:docks")
    site = season.sites.get(key="mountain:gate")
    site.owner = first.faction
    site.save(update_fields=["owner"])
    with pytest.raises(GameError):
        issue(first, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    site.durability -= 100
    site.save(update_fields=["durability"])
    run = issue(first, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    assert {key: run.cargo.get(key, 0) for key in ("grain", "wood", "iron")} == {"grain": 0, "wood": 120, "iron": 60}
    with pytest.raises(GameError):
        issue(second, second_guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    advance_resource_run(run, clock)
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert {key: site.supplies.get(key, 0) for key in ("grain", "wood", "iron")} == {
        "grain": 0,
        "wood": 120,
        "iron": 60,
    }
    assert not run.cargo
    finish_resource_run(run, clock)
    state = season.faction_states.get(faction=first.faction)
    assert sum(state.resources.values()) + sum(site.supplies.values()) == 30000 - 5
    assert not process_expedition(run.pk, clock[0])


def test_transport_cannot_pay_for_goods_the_target_no_longer_needs(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    full_wall = site.durability
    site.owner, site.durability = member.faction, full_wall - 100
    site.save(update_fields=["owner", "durability"])
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    site.durability = full_wall
    site.save(update_fields=["durability"])
    finish_resource_run(run, clock)
    site.refresh_from_db()
    member.refresh_from_db()
    assert not any(site.supplies.values())
    assert member.merit == 0
    state = season.faction_states.get(faction=member.faction)
    assert state.resources["wood"] == 10000 and state.resources["iron"] == 10000


def test_captured_site_keeps_actual_supplies_and_does_not_create_grain(resource_campaign):
    season, _, clock = resource_campaign
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.supplies = {"grain": 91, "wood": 37, "iron": 23}
    site.control = season.rules["campaign"]["sites"][site.key]["control_required"]
    site.save(update_fields=["owner", "supplies", "control"])
    before_stock = deepcopy(site.supplies)
    with transaction.atomic():
        assert capture_locked(site, "fiction", clock[0])
        site.save()
    site.refresh_from_db()
    assert site.owner == "fiction" and site.supplies == before_stock


def test_resource_budget_selector_uses_current_caps_without_advancing_economy(resource_campaign):
    season, player, _ = resource_campaign
    member, _ = player()
    state = season.faction_states.get(faction=member.faction)
    state.levels = {key: 3 for key in state.levels}
    state.save(update_fields=["levels"])
    before = list(season.faction_states.order_by("pk").values("resources", "troops", "produced_at", "economy_state"))
    context = get_season_context(member.manor, season.pk)
    expected = resource_caps(season, member.faction)
    assert {row["key"]: row["cap"] for row in context["resource_options"]} == expected
    assert (
        list(season.faction_states.order_by("pk").values("resources", "troops", "produced_at", "economy_state"))
        == before
    )


def test_invalid_material_run_refunds_original_day_when_return_crosses_midnight(resource_campaign):
    from gameplay.services.season.material_rewards import material_budget_summary
    from gameplay.services.season.quotas import daily_dispatches_used

    season, player, clock = resource_campaign
    member, guest = player()
    clock[0] = timezone.localtime(clock[0]).replace(hour=23, minute=59, second=0, microsecond=0)
    original_day = timezone.localdate(clock[0])
    run = issue(member, guest, clock)
    assert material_budget_summary(member, clock[0])["used"] == 1
    season.ends_at = run.due_at - timedelta(seconds=1)
    season.save(update_fields=["ends_at"])
    finish_resource_run(run, clock)
    assert timezone.localdate(clock[0]) > original_day
    assert run.report_id is None and run.result.get("material_reward_refunded")
    assert daily_dispatches_used(member, original_day) == 0
    assert material_budget_summary(member, clock[0])["used"] == 0
    prior_noon = timezone.localtime(clock[0]).replace(hour=12) - timedelta(days=1)
    assert material_budget_summary(member, prior_noon)["used"] == 0


def test_frontline_surplus_returns_to_owner_without_cap_loss(resource_campaign):
    from gameplay.services.season.logistics import settle_surplus_locked

    season, player, clock = resource_campaign
    member, _ = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.supplies = {"grain": 300, "wood": 120, "iron": 60}
    site.save(update_fields=["owner", "supplies"])
    state = season.faction_states.get(faction=member.faction)
    before = state.resources.copy()
    with transaction.atomic():
        settle_surplus_locked(season, clock[0])
        settle_surplus_locked(season, clock[0])
    site.refresh_from_db()
    state.refresh_from_db()
    assert site.supplies == {"grain": 0, "wood": 0, "iron": 0}
    assert state.resources == {
        key: before[key] + amount for key, amount in {"grain": 300, "wood": 120, "iron": 60}.items()
    }
