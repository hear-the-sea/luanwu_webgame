"""小服协作失败退款、真实运输收益及经济人口增长。"""

from datetime import datetime, timedelta
from datetime import timezone as utc_timezone
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import Message, SeasonAction, SeasonIntel
from gameplay.services.season.activities import delivery_reward_policy, estimate_delivery_merit
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.population import recommended_focus, refresh_population_locked
from gameplay.services.season.quotas import daily_dispatches_used, guest_dispatch_counts
from gameplay.services.season.transport_rewards import delivery_merit
from tests.test_season_resource_integration import advance_resource_run, finish_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign

pytestmark = pytest.mark.django_db
resource_campaign = _resource_campaign


def test_simultaneous_shared_exploration_refunds_only_unused_team_after_return(resource_campaign):
    season, player, clock = resource_campaign
    first, first_guest = player("fiction")
    second, second_guest = player("fiction")
    state = season.faction_states.get(faction="fiction")
    state.troops = {**state.troops, "dao_sheng": 100}
    state.save(update_fields=["troops"])
    first_run = issue(first, first_guest, clock, mode="explore", node="mountain:ruins")
    second_run = issue(second, second_guest, clock, mode="explore", node="mountain:ruins", troops={"dao_sheng": 100})
    day = timezone.localdate(clock[0])
    assert daily_dispatches_used(second, day) == 1
    assert guest_dispatch_counts(season, day)[second_guest.pk] == 1
    second.refresh_from_db()
    assert second.troops_drawn == 100

    advance_resource_run(first_run, clock)
    advance_resource_run(second_run, clock)
    assert second_run.status == "returning"
    assert second_run.result["refund_quota"] is True
    assert "归队后退回" in second_run.result["message"]
    assert Message.objects.filter(manor=second.manor, title="阵营任务-探索结果").count() == 1
    assert SeasonIntel.objects.filter(season=season, faction="fiction").count() == 1
    assert SeasonIntel.objects.get(season=season, faction="fiction").payload["bonus_available"] is True
    second.refresh_from_db()
    assert second.merit == 0 and second.troops_drawn == 100  # 尚在返程，额度未提前释放。

    finish_resource_run(second_run, clock)
    second.refresh_from_db()
    assert second_run.result["quota_refunded"] is True
    assert second.troops_drawn == 0
    assert daily_dispatches_used(second, day) == 0
    assert guest_dispatch_counts(season, day).get(second_guest.pk, 0) == 0
    assert daily_dispatches_used(first, day) == 1
    assert not process_expedition(second_run.pk, clock[0])
    assert second.actions.filter(kind="dispatch_refund").count() == 1
    assert Message.objects.filter(manor=second.manor, title="阵营任务-探索结果").count() == 1


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 0), (40, 15), (79, 29), (80, 30), (200, 40), (1200, 90)])
def test_real_delivery_floor_scales_from_zero_and_keeps_maximum(value, expected):
    assert delivery_merit(value, 1200, 90, 30, 200, participation_full_value=80, participation_merit=30) == expected


def test_delivery_policy_is_frozen_and_legacy_orders_keep_original_curve(resource_campaign):
    season, _, _ = resource_campaign
    policy = delivery_reward_policy(season, 15000)
    assert (policy["participation_full_value"], policy["participation_merit"]) == (80, 30)
    assert estimate_delivery_merit(season, {"grain": 80}, 15000) == 30
    assert estimate_delivery_merit(season, {}, 15000) == 0
    transport = season.rules["campaign"]["resource_economy"]["transport"]
    transport["participation_merit"] = 50
    assert policy["participation_merit"] == 30
    assert estimate_delivery_merit(season, {"grain": 80}, 15000) == 50
    transport.pop("participation_full_value")
    transport.pop("participation_merit")
    assert estimate_delivery_merit(season, {"grain": 80}, 15000) == 16
    assert delivery_merit(80, 1200, 90, 30, 200) == 16
    assert delivery_merit(80, 1200, 20, 30, 200, participation_full_value=80, participation_merit=30) == 20


@pytest.mark.parametrize("legacy", [False, True])
def test_small_real_transport_settles_using_its_frozen_policy(resource_campaign, legacy):
    season, player, clock = resource_campaign
    member, guest = player("fiction")
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.durability = site.npc_profile["durability"] - 20
    site.supplies = {}
    site.save(update_fields=["owner", "durability", "supplies"])
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    assert run.cargo == {"grain": 0, "wood": 24, "iron": 12}  # 价值84，确有维修缺口。
    if legacy:
        run.result["activity"]["delivery_reward"].pop("participation_full_value")
        run.result["activity"]["delivery_reward"].pop("participation_merit")
        run.save(update_fields=["result"])
    season.rules["campaign"]["resource_economy"]["transport"]["participation_merit"] = 50
    season.save(update_fields=["rules"])
    advance_resource_run(run, clock)
    assert run.status == "transporting"
    advance_resource_run(run, clock)
    assert run.result["delivered_resources"] == {"grain": 0, "wood": 24, "iron": 12}
    member.refresh_from_db()
    assert member.merit == (16 if legacy else 30)


def credit(member, clock):
    return SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="economic-population-check",
        kind="merit_credit",
        date=timezone.localdate(clock),
        result={"merit": 1, "requested_merit": 1},
    )


def test_effective_population_grows_promptly_and_declines_smoothly(resource_campaign):
    season, player, clock = resource_campaign
    members = [player("fiction")[0] for _ in range(11)]
    receipts = [credit(member, clock[0]) for member in members[:10]]
    season.population["factions"]["fiction"].update(count=1, economic_count=1)
    season.population["factions"]["history"].update(count=0, economic_count=0)
    season.population_updated_at = clock[0]
    season.save(update_fields=["population", "population_updated_at"])

    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0] + timedelta(minutes=5))
    assert profile["factions"]["fiction"]["count"] == 10
    assert profile["factions"]["fiction"]["economic_count"] == 10
    # 第11人没有完成有效活动，无法虚增产能；重复扫描不重复追溯产出。
    with transaction.atomic():
        repeated = refresh_population_locked(season, clock[0] + timedelta(minutes=10))
    assert repeated == profile

    SeasonAction.objects.filter(pk__in=[r.pk for r in receipts[1:]]).update(created_at=clock[0] - timedelta(days=8))
    with transaction.atomic():
        reduced = refresh_population_locked(season, clock[0] + timedelta(days=1, minutes=5))
    assert reduced["factions"]["fiction"]["count"] == 1
    assert reduced["factions"]["fiction"]["economic_count"] == 7.75
    # 玩家真正恢复活动后及时回升，无需等待下一次每日刷新。
    resume_at = clock[0] + timedelta(days=1, minutes=10)
    resumed = [credit(member, resume_at).pk for member in members[1:10]]
    SeasonAction.objects.filter(pk__in=resumed).update(created_at=resume_at)
    with transaction.atomic():
        restored = refresh_population_locked(season, resume_at)
    assert restored["factions"]["fiction"]["count"] == 10
    assert restored["factions"]["fiction"]["economic_count"] == 10


def test_configured_growth_weight_is_not_reapplied_by_forced_same_day_refresh(resource_campaign):
    season, player, clock = resource_campaign
    for _ in range(10):
        credit(player("fiction")[0], clock[0])
    season.rules["campaign"]["resource_economy"]["population"]["growth_previous_weight"] = 0.5
    season.population["factions"]["fiction"].update(count=1, economic_count=1)
    season.population["factions"]["history"].update(count=0, economic_count=0)
    season.population_updated_at = clock[0]
    season.save(update_fields=["rules", "population", "population_updated_at"])
    with transaction.atomic():
        first = refresh_population_locked(season, clock[0] + timedelta(minutes=5))
    assert first["factions"]["fiction"]["economic_count"] == 5.5
    with transaction.atomic():
        repeated = refresh_population_locked(season, clock[0] + timedelta(minutes=10), force=True)
    assert repeated["factions"]["fiction"]["economic_count"] == 5.5
    with transaction.atomic():
        daily = refresh_population_locked(season, clock[0] + timedelta(days=1, minutes=10))
    assert daily["factions"]["fiction"]["economic_count"] == 7.75


def test_old_population_snapshot_keeps_slow_growth(resource_campaign):
    season, player, clock = resource_campaign
    members = [player("fiction")[0] for _ in range(10)]
    for member in members:
        credit(member, clock[0])
    population = season.rules["campaign"]["resource_economy"]["population"]
    population.pop("growth_previous_weight")
    population.pop("growth_refresh_immediate")
    season.population["factions"]["fiction"].update(count=1, economic_count=1)
    season.population["factions"]["history"].update(count=0, economic_count=0)
    season.population_updated_at = clock[0]
    season.save(update_fields=["rules", "population", "population_updated_at"])
    with transaction.atomic():
        early = refresh_population_locked(season, clock[0] + timedelta(minutes=5))
    assert early["factions"]["fiction"]["economic_count"] == 1
    with transaction.atomic():
        daily = refresh_population_locked(season, clock[0] + timedelta(days=1))
    assert daily["factions"]["fiction"]["economic_count"] == 3.25


def test_standard_small_server_recommendation_does_not_change_score_hotspot(resource_campaign):
    season, _, _ = resource_campaign
    now = datetime(2026, 10, 27, 12, tzinfo=utc_timezone.utc)
    season.starts_at = now - timedelta(days=5)
    season.ends_at = now + timedelta(days=85)
    season.population["factions"]["fiction"]["count"] = 10
    season.population["factions"]["history"]["count"] = 10
    season.population.update(mode="standard", hotspot="", hotspot_multiplier=1)
    before = dict(season.population)
    result = recommended_focus(season, now)
    assert result["key"] in season.rules["campaign"]["sites"]
    assert result["key"] != "river:town"  # 周二古城不开战。
    assert result["scoring_bonus"] is False
    assert recommended_focus(season, now) == result
    assert season.population == before
    season.population["factions"]["history"]["count"] = 11
    assert recommended_focus(season, now) == {}
    season.population["factions"]["history"]["count"] = 10
    season.rules["campaign"]["population"].pop("recommendation_max_active")
    assert recommended_focus(season, now) == {}
