from copy import deepcopy
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import InventoryItem, ItemTemplate, Manor, PlayerTroop
from gameplay.models.season import Season, SeasonFaction, SeasonMember, SeasonSite
from gameplay.services.season.campaign_rewards import claim_final_locked, freeze_rankings_locked
from gameplay.services.season.campaign_rules import campaign_rules, load_campaign_rules, validate_campaign_rules
from gameplay.services.season.economy import (
    ensure_factions_locked,
    grant_construction_locked,
    grant_pve_locked,
    produce_locked,
    reserve_troops_locked,
    return_troops_locked,
    technology_levels,
    upgrade_locked,
)
from gameplay.services.season.registration import assign_factions_locked, register_locked

pytestmark = pytest.mark.django_db


@pytest.fixture
def campaign():
    start = timezone.now() - timedelta(days=5)
    # 已有赛季继续使用旧兵种快照，默认配置升级不能破坏历史周期。
    config = load_campaign_rules()
    config["playability"]["enabled"] = False
    config["simplification"]["enabled"] = False
    config.setdefault("resource_economy", {})["enabled"] = False
    config["economy"].update(
        initial_troops={"dao_ke": 1000, "qiang_ke": 1000, "jian_shi": 800},
        troop_caps={"dao_ke": 10000, "qiang_ke": 10000, "jian_shi": 10000},
        troop_rates={"dao_ke": 15, "qiang_ke": 15, "jian_shi": 10},
        special_troops={},
    )
    return Season.objects.create(
        name="后勤测试", starts_at=start, ends_at=start + timedelta(days=90), rules={"campaign": config}
    )


@pytest.fixture
def special_campaign():
    start = timezone.now() - timedelta(days=5)
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config.setdefault("resource_economy", {})["enabled"] = False
    return Season.objects.create(
        name="特殊兵库测试",
        starts_at=start,
        ends_at=start + timedelta(days=90),
        rules={"campaign": config},
    )


def occupy_special_site(season, faction, at):
    config = season.rules["campaign"]
    site_key = config["economy"]["special_troops"]["bloodthirsty_cavalry"]["site_key"]
    site, _ = SeasonSite.objects.get_or_create(season=season, key=site_key, defaults={"last_score_at": at})
    site.owner = faction
    site.protected_until = at + timedelta(seconds=config["sites"][site_key]["protection_seconds"])
    site.save(update_fields=["owner", "protected_until"])
    return site


def test_new_season_uses_top_tier_troops_and_special_requires_occupation(special_campaign):
    config = special_campaign.rules["campaign"]["economy"]
    ordinary = {"dao_sheng", "qiang_wang", "jian_sheng", "quan_sheng", "arrow_god"}
    assert set(config["initial_troops"]) == ordinary | {"bloodthirsty_cavalry"}
    assert {key: config["initial_troops"][key] for key in ordinary} == {key: 1000 for key in ordinary}
    at = special_campaign.starts_at + timedelta(days=3, seconds=300)
    with transaction.atomic():
        states = produce_locked(special_campaign, at)
    for state in states.values():
        assert state.troops["bloodthirsty_cavalry"] == 0
        assert all(state.troops[key] == 1010 for key in ordinary)


def test_special_training_only_for_owner_with_grain_and_cap(special_campaign):
    start = special_campaign.starts_at + timedelta(days=3)
    config = special_campaign.rules["campaign"]["economy"]
    config["troop_caps"]["bloodthirsty_cavalry"] = 12
    special_campaign.save(update_fields=["rules"])
    with transaction.atomic():
        states = ensure_factions_locked(special_campaign, start)
        state = states["fiction"]
        state.levels["training"] = 2
        state.save(update_fields=["levels"])
        occupy_special_site(special_campaign, "fiction", start)
        states = produce_locked(special_campaign, start + timedelta(seconds=600))
    assert states["fiction"].troops["bloodthirsty_cavalry"] == 12
    assert states["history"].troops["bloodthirsty_cavalry"] == 0
    # 每轮普通兵100名，特殊兵先10后2，共耗212粮，产240粮。
    assert states["fiction"].resources["grain"] == 5028


def test_special_occupation_partial_tick_is_not_retroactively_produced(special_campaign):
    start = special_campaign.starts_at + timedelta(days=3)
    with transaction.atomic():
        ensure_factions_locked(special_campaign, start)
        occupy_special_site(special_campaign, "fiction", start + timedelta(seconds=150))
        states = produce_locked(special_campaign, start + timedelta(seconds=300))
        assert states["fiction"].troops["bloodthirsty_cavalry"] == 0
        states = produce_locked(special_campaign, start + timedelta(seconds=600))
        assert states["fiction"].troops["bloodthirsty_cavalry"] == 5


def test_special_handover_keeps_stock_and_discards_split_tick(special_campaign):
    start = special_campaign.starts_at + timedelta(days=3)
    with transaction.atomic():
        ensure_factions_locked(special_campaign, start)
        occupy_special_site(special_campaign, "fiction", start)
        states = produce_locked(special_campaign, start + timedelta(seconds=450))
        assert states["fiction"].troops["bloodthirsty_cavalry"] == 5
        occupy_special_site(special_campaign, "history", start + timedelta(seconds=450))
        states = produce_locked(special_campaign, start + timedelta(seconds=600))
        assert states["history"].troops["bloodthirsty_cavalry"] == 0
        states = produce_locked(special_campaign, start + timedelta(seconds=900))
    assert states["fiction"].troops["bloodthirsty_cavalry"] == 5
    assert states["history"].troops["bloodthirsty_cavalry"] == 5


@pytest.fixture
def member_factory(user_factory):
    def create(season, faction="fiction", merit=0, prestige=0):
        user = user_factory()
        manor, _ = Manor.objects.get_or_create(user=user)
        manor.prestige = prestige
        manor.save(update_fields=["prestige"])
        return SeasonMember.objects.create(
            season=season, manor=manor, faction=faction, merit=merit, prestige_snapshot=prestige
        )

    return create


def test_configuration_snapshots_and_limits(campaign):
    config = campaign_rules(campaign)
    config["economy"]["daily_troop_quota"] = 1
    assert campaign_rules(campaign)["economy"]["daily_troop_quota"] == 1500
    invalid = deepcopy(config)
    invalid["economy"]["tick_seconds"] = 0
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(invalid)


@pytest.mark.parametrize(
    "key,value",
    [
        ("timezone", "Not/AZone"),
        ("timezone", 1),
        ("score_interval_seconds", 0),
        ("score_interval_seconds", True),
        ("garrison_capacity", 0),
        ("delivery_multiplier", float("nan")),
        ("delivery_multiplier", float("inf")),
        ("delivery_multiplier", 2.1),
        ("delivery_multiplier", True),
        ("enemy_levels", {"mountain:inn": 0}),
        ("enemy_levels", {"unknown:place": 5}),
        ("city_weekdays", [7]),
    ],
)
def test_new_configuration_fields_reject_invalid_values(key, value):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["expeditions"][key] = value
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_snake_assignment_separates_top_two_and_preserves_existing(campaign, member_factory):
    members = [member_factory(campaign, "", prestige=value) for value in [100, 90, 80, 70, 60, 50, 40, 30]]
    with transaction.atomic():
        assert assign_factions_locked(campaign, timezone.now()) == 8
        assert assign_factions_locked(campaign, timezone.now()) == 0
    for member in members:
        member.refresh_from_db()
    assert [m.faction for m in members] == ["fiction", "history", "history", "fiction"] * 2
    assert sum(m.prestige_snapshot for m in members if m.faction == "fiction") == sum(
        m.prestige_snapshot for m in members if m.faction == "history"
    )


def test_registration_waits_then_late_player_fills_weaker_side(campaign, user_factory, member_factory):
    user = user_factory()
    manor, _ = Manor.objects.get_or_create(user=user)
    with transaction.atomic():
        pending = register_locked(campaign, manor, "fiction", campaign.starts_at + timedelta(days=1))
        assert pending.faction == ""
        assert assign_factions_locked(campaign, campaign.starts_at + timedelta(days=2)) == 0
        assign_factions_locked(campaign, campaign.starts_at + timedelta(days=3))
    pending.refresh_from_db()
    assert pending.faction == "fiction"
    other = user_factory()
    other_manor, _ = Manor.objects.get_or_create(user=other)
    with transaction.atomic():
        late = register_locked(campaign, other_manor, "fiction", timezone.now())
        assert late.faction == "history"
        with pytest.raises(GameError, match="重复报名"):
            register_locked(campaign, other_manor, "history", timezone.now())


def test_production_catches_up_once_and_stops_at_combat_end(campaign):
    first = campaign.starts_at + timedelta(days=3, seconds=300)
    with transaction.atomic():
        state = produce_locked(campaign, first)["fiction"]
        assert state.resources == {"grain": 5080, "wood": 3080, "iron": 2050}
        assert state.troops == {"dao_ke": 1015, "qiang_ke": 1015, "jian_shi": 810}
        before = (state.resources.copy(), state.troops.copy(), state.produced_at)
        state = produce_locked(campaign, first)["fiction"]
        assert (state.resources, state.troops, state.produced_at) == before
        state = produce_locked(campaign, campaign.ends_at + timedelta(days=1))["fiction"]
        cutoff = campaign.starts_at + timedelta(days=83)
        assert state.produced_at == cutoff
        before = (state.resources.copy(), state.troops.copy())
        state = produce_locked(campaign, campaign.ends_at + timedelta(days=10))["fiction"]
        assert (state.resources, state.troops) == before


def test_reserve_and_return_touch_only_public_army(campaign, member_factory):
    member = member_factory(campaign)
    own_rows = list(PlayerTroop.objects.filter(manor=member.manor).values())
    at = campaign.starts_at
    with transaction.atomic():
        result = reserve_troops_locked(member, {"dao_ke": 300, "qiang_ke": 0}, at)
        assert result == {"dao_ke": 300}
        assert SeasonFaction.objects.get(season=campaign, faction="fiction").troops["dao_ke"] == 700
        return_troops_locked(member, {"dao_ke": 250}, at)
        assert SeasonFaction.objects.get(season=campaign, faction="fiction").troops["dao_ke"] == 950
    assert list(PlayerTroop.objects.filter(manor=member.manor).values()) == own_rows
    member.refresh_from_db()
    assert member.troops_drawn == 300
    with transaction.atomic():
        with pytest.raises(GameError, match="额度不足"):
            reserve_troops_locked(member, {"dao_ke": 1201}, at)
        reserve_troops_locked(member, {"dao_ke": 100}, at + timedelta(days=1))
    member.refresh_from_db()
    assert member.troops_drawn == 100


def test_return_preserves_survivors_when_training_filled_pool(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, campaign.starts_at)["fiction"]
        state.troops["dao_ke"] = 10000
        state.save(update_fields=["troops"])
        return_troops_locked(member, {"dao_ke": 250}, campaign.starts_at)
        state = produce_locked(campaign, campaign.starts_at + timedelta(days=3, seconds=300))["fiction"]
        assert state.troops["dao_ke"] == 10250


def test_return_produces_missing_ticks_before_restoring_survivors(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, campaign.starts_at)["fiction"]
        state.troops["dao_ke"] = 9970
        state.save(update_fields=["troops"])
        return_troops_locked(member, {"dao_ke": 100}, campaign.starts_at + timedelta(days=3, seconds=600))
        state.refresh_from_db()
        assert state.troops["dao_ke"] == 10100


def test_invalid_or_overdrawn_army_is_rejected(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        for loadout in ({"dao_ke": -1}, {"dao_ke": True}, {"bogus": 1}, {"dao_ke": "1"}):
            with pytest.raises(GameError, match="配置无效"):
                reserve_troops_locked(member, loadout, campaign.starts_at)
        state = produce_locked(campaign, campaign.starts_at)["fiction"]
        state.troops = {"dao_ke": 1, "qiang_ke": 0, "jian_shi": 0}
        state.save()
        with pytest.raises(GameError, match="兵库不足"):
            reserve_troops_locked(member, {"dao_ke": 2}, campaign.starts_at)


def test_pve_adds_shared_resources_and_personal_logistics_merit(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        result = grant_pve_locked(member, "mountain:inn", True, campaign.starts_at)
    member.refresh_from_db()
    state = SeasonFaction.objects.get(season=campaign, faction="fiction")
    assert result["resources"] == {"grain": 400, "wood": 40, "iron": 20}
    assert state.resources["grain"] == 5400
    assert state.construction_points == result["construction_points"] == 20
    assert result["total_construction_points"] == 20
    assert (member.merit, member.coins, member.logistics_merit) == (20, 20, 20)


def test_manual_upgrade_is_rejected_without_spending_resources(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        state = produce_locked(campaign, campaign.starts_at)["fiction"]
        before = (dict(state.resources), dict(state.levels))
        with pytest.raises(GameError, match="自动升级"):
            upgrade_locked(member, "technology", campaign.starts_at)
    state.refresh_from_db()
    member.refresh_from_db()
    assert (state.resources, state.levels) == before
    assert member.merit == 0


def test_construction_threshold_exact_boundary_upgrades_all_facilities(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, campaign.starts_at)["fiction"]
        state.construction_points = 975
        state.save(update_fields=["construction_points"])
        before = grant_construction_locked(member, "pve_win", campaign.starts_at)
        assert before["total_construction_points"] == 995
        assert before["upgraded"] == []
        result = grant_construction_locked(member, "pve_loss", campaign.starts_at)
    assert result["construction_points"] == 5
    assert result["total_construction_points"] == 1000
    assert result["levels"] == {"logistics": 2, "training": 2, "technology": 2}
    assert set(result["upgraded"]) == {"logistics", "training", "technology"}
    state.refresh_from_db()
    assert technology_levels(state)["dao_attack"] == 1
    member.refresh_from_db()
    assert (member.merit, member.coins, member.logistics_merit) == (0, 0, 0)
    assert not member.manor.technologies.exists()


def test_construction_can_skip_levels_with_no_public_resources(campaign, member_factory):
    config = campaign.rules["campaign"]["economy"]
    config["auto_development"]["activity_points"]["transport"] = 15000
    config["buildings"]["logistics"]["max_level"] = 3
    config["buildings"]["training"]["max_level"] = 5
    campaign.save(update_fields=["rules"])
    member = member_factory(campaign)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, campaign.starts_at)["fiction"]
        state.resources = {"grain": 0, "wood": 0, "iron": 0}
        state.save(update_fields=["resources"])
        result = grant_construction_locked(member, "transport", campaign.starts_at)
    state.refresh_from_db()
    assert result["levels"] == {"logistics": 3, "training": 5, "technology": 6}
    assert state.construction_points == 15000
    assert state.resources == {"grain": 0, "wood": 0, "iron": 0}


def test_construction_preserves_existing_higher_levels_and_technology(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, campaign.starts_at)["fiction"]
        state.levels = {"logistics": 5, "training": 4, "technology": 6}
        state.technology["dao_attack"] = 9
        state.save(update_fields=["levels", "technology"])
        result = grant_construction_locked(member, "pve_loss", campaign.starts_at)
    state.refresh_from_db()
    assert result["levels"] == {"logistics": 5, "training": 4, "technology": 6}
    assert result["upgraded"] == []
    assert technology_levels(state)["dao_attack"] == 9
    assert technology_levels(state)["dao_hp"] == 5


def test_construction_upgrade_settles_past_production_at_old_level(campaign, member_factory):
    member = member_factory(campaign)
    start = campaign.starts_at + timedelta(days=3)
    with transaction.atomic():
        state = ensure_factions_locked(campaign, start)["fiction"]
        state.construction_points = 980
        state.save(update_fields=["construction_points"])
        grant_construction_locked(member, "pve_win", start + timedelta(seconds=600))
        state.refresh_from_db()
        assert state.resources == {"grain": 5160, "wood": 3160, "iron": 2100}
        assert state.troops["dao_ke"] == 1030
        state = produce_locked(campaign, start + timedelta(seconds=900))["fiction"]
    assert state.resources == {"grain": 5320, "wood": 3320, "iron": 2200}
    assert state.troops["dao_ke"] == 1060


def test_transport_and_losses_use_their_own_construction_configuration(campaign, member_factory):
    member = member_factory(campaign)
    with transaction.atomic():
        loss = grant_construction_locked(member, "attack_loss", campaign.starts_at)
        assert loss["construction_points"] == 12
        transport = grant_pve_locked(member, "river:docks", True, campaign.starts_at, activity_key="transport")
        assert transport["construction_points"] == 15
        assert transport["total_construction_points"] == 27
        with pytest.raises(GameError, match="未知"):
            grant_construction_locked(member, "unknown_activity", campaign.starts_at)


def test_construction_is_shared_with_teammates_and_isolated_from_enemy(campaign, member_factory):
    first = member_factory(campaign)
    second = member_factory(campaign)
    enemy = member_factory(campaign, faction="history")
    with transaction.atomic():
        grant_construction_locked(first, "pve_win", campaign.starts_at)
        combined = grant_construction_locked(second, "pve_win", campaign.starts_at)
        opposing = grant_construction_locked(enemy, "pve_win", campaign.starts_at)
    assert combined["total_construction_points"] == 40
    assert opposing["total_construction_points"] == 20


def test_batch_production_matches_incremental_ticks_when_grain_capacity_binds(campaign):
    rules = campaign.rules["campaign"]
    rules["economy"]["initial_resources"]["grain"] = 100
    rules["economy"]["resource_caps"]["grain"] = 100
    campaign.rules = {"campaign": rules}
    campaign.save(update_fields=["rules"])
    start = campaign.starts_at + timedelta(days=3)
    with transaction.atomic():
        batch = produce_locked(campaign, start + timedelta(seconds=600))["fiction"]
        expected_resources, expected_troops = dict(batch.resources), dict(batch.troops)
        batch.resources = dict(rules["economy"]["initial_resources"])
        batch.troops = dict(rules["economy"]["initial_troops"])
        batch.produced_at = start
        batch.save(update_fields=["resources", "troops", "produced_at"])
        produce_locked(campaign, start + timedelta(seconds=300))
        incremental = produce_locked(campaign, start + timedelta(seconds=600))["fiction"]
    assert incremental.resources == expected_resources
    assert incremental.troops == expected_troops
    assert incremental.resources["grain"] == 60


def test_rankings_are_within_faction_and_frozen_once(campaign, member_factory):
    members = [member_factory(campaign, merit=100 + index) for index in range(10)]
    member_factory(campaign, faction="history", merit=200)
    inactive = member_factory(campaign, merit=99)
    campaign.winner = "fiction"
    with transaction.atomic():
        freeze_rankings_locked(campaign)
    first = members[-1]
    first.refresh_from_db()
    assert (first.final_rank, first.final_tier) == (1, "elite")
    assert first.final_reward["items"] == {"experience_fruit": 70, "xuan_tie_essence": 50}
    assert season_tiers(campaign, "fiction") == {"elite": 1, "distinguished": 2, "participant": 7, "ineligible": 1}
    inactive.refresh_from_db()
    assert inactive.final_rank is None
    before = deepcopy(first.final_reward)
    first.merit = 1
    first.save(update_fields=["merit"])
    with transaction.atomic():
        freeze_rankings_locked(campaign)
    first.refresh_from_db()
    assert first.final_reward == before


def season_tiers(season, faction):
    tiers = {}
    for key in season.members.filter(faction=faction).values_list("final_tier", flat=True):
        tiers[key] = tiers.get(key, 0) + 1
    return tiers


def test_final_reward_claim_is_bound_and_once(campaign, member_factory):
    member = member_factory(campaign, merit=500)
    campaign.closed = True
    campaign.winner = "fiction"
    campaign.save()
    for key in ("experience_fruit", "xuan_tie_essence"):
        ItemTemplate.objects.create(key=key, name=key, effect_type="tool")
    with transaction.atomic():
        freeze_rankings_locked(campaign)
        member.refresh_from_db()
        claim_final_locked(member, member.manor)
        with pytest.raises(GameError, match="已经领取"):
            claim_final_locked(member, member.manor)
    stock = InventoryItem.objects.get(manor=member.manor, template__key="experience_fruit")
    assert stock.quantity == stock.bound_quantity == 70


def test_configuration_uses_existing_troop_and_technology_and_reward_keys():
    import yaml
    from django.conf import settings

    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    root = settings.BASE_DIR / "data"
    troops = yaml.safe_load((root / "troop_templates.yaml").read_text())["troops"]
    technology = yaml.safe_load((root / "technology_templates.yaml").read_text())["technologies"]
    items = yaml.safe_load((root / "item_templates.yaml").read_text())["items"]
    assert set(config["economy"]["initial_troops"]) <= {row["key"] for row in troops}
    assert set(config["economy"]["technology_keys"]) <= {row["key"] for row in technology}
    assert set(config["rewards"]["winner_items"]) <= {row["key"] for row in items}
