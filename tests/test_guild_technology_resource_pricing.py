from types import SimpleNamespace

import pytest

from core.exceptions import GuildTechnologyError
from guilds import constants
from guilds.services import technology
from guilds.technology_tree import TECHNOLOGY_ROUTES


@pytest.mark.parametrize(
    "tech_key,expected_silver_grain",
    [
        ("hero_fruit_blueprint", [(990000, 660000), (3240000, 2160000), (7020000, 4680000)]),
        (
            "wolf_berserk",
            [(1100000, 1320000), (2300000, 2760000), (4800000, 5760000), (8750000, 10500000), (15600000, 18720000)],
        ),
        (
            "wolf_force",
            [
                (1050000, 1260000),
                (1050000, 1260000),
                (2100000, 2520000),
                (2200000, 2640000),
                (3300000, 3960000),
                (4400000, 5280000),
                (5750000, 6900000),
                (6900000, 8280000),
                (9200000, 11040000),
                (11500000, 13800000),
            ],
        ),
        ("wolf_bloodthirsty_cavalry", [(4800000, 6000000)]),
        ("sheep_wrath", [(12000000, 12000000)]),
    ],
)
def test_resource_costs_follow_the_reviewed_early_and_late_progression(tech_key, expected_silver_grain):
    for current_level, (silver, grain) in enumerate(expected_silver_grain):
        cost = technology.calculate_tech_upgrade_cost(tech_key, current_level)
        assert (cost["silver"], cost["grain"]) == (silver, grain)


def test_resource_prices_scale_by_resource_and_preserve_troop_tactics(monkeypatch):
    assert technology.calculate_tech_upgrade_cost("hero_fruit_blueprint", 0) == {
        "silver": 990000,
        "grain": 660000,
        "gold_bar": 2,
    }
    assert technology.calculate_tech_upgrade_cost("troop_tactics", 9) == {
        "silver": 107527000,
        "grain": 107527000,
    }
    monkeypatch.setattr(
        constants,
        "TECH_UPGRADE_COST_OVERRIDES",
        {"hero_fruit_blueprint": {"2": {"silver": 123, "grain": 456, "gold_bar": 7, "red_ruby": 9}}},
    )
    assert technology.calculate_tech_upgrade_cost(
        "hero_fruit_blueprint", 1, guild=SimpleNamespace(completed_technology_routes=["wolf"])
    ) == {"silver": 3552, "grain": 13152, "gold_bar": 12, "red_ruby": 15}


def test_all_levels_and_route_premiums_fit_current_resource_and_log_fields():
    largest_changed_cost = 0
    for key, category, max_level in constants.get_supported_guild_technology_configs():
        completed = [route["key"] for route in TECHNOLOGY_ROUTES if route["key"] != category]
        guild = SimpleNamespace(completed_technology_routes=completed)
        for level in range(max_level):
            cost = technology.calculate_tech_upgrade_cost(key, level, guild=guild)
            for resource in ("silver", "grain"):
                assert 0 <= cost.get(resource, 0) <= 2**31 - 1
                if key != "troop_tactics":
                    largest_changed_cost = max(largest_changed_cost, cost.get(resource, 0))
    assert largest_changed_cost == 121305600


@pytest.fixture
def priced_guild(django_user_model, monkeypatch):
    from guilds.models import Guild, GuildTechnology, GuildWarehouse

    operator = django_user_model.objects.create_user(username="priced_tech_operator")
    guild = Guild.objects.create(name="调价测试帮会", founder=operator, silver=10000000, active_technology_route="hero")
    tech = GuildTechnology.objects.create(
        guild=guild, tech_key="hero_fruit_blueprint", category="hero", level=0, max_level=3
    )
    grain = GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=10000000)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=100)
    monkeypatch.setattr(technology, "get_active_membership", lambda *args, **kwargs: SimpleNamespace(can_manage=True))
    monkeypatch.setattr(technology, "create_announcement", lambda *args, **kwargs: None)
    return guild, tech, grain, operator


@pytest.mark.django_db
def test_upgrade_spends_new_price_and_reset_refunds_actual_payment(priced_guild):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    technology.upgrade_technology(guild, tech.tech_key, operator)
    guild.refresh_from_db()
    grain.refresh_from_db()
    assert guild.silver == 9010000 and grain.quantity == 9340000
    log = GuildResourceLog.objects.get(guild=guild, action="tech_upgrade")
    assert (log.silver_change, log.grain_change, log.gold_bar_change) == (-990000, -660000, -2)
    assert technology.reset_technology_route(guild, "hero", operator) == {
        "silver": 495000,
        "grain": 330000,
        "gold_bar": 1,
    }


@pytest.mark.django_db
def test_upgrade_rejects_old_price_balance_without_spending_any_resources(priced_guild):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    guild.silver = 900000
    guild.save(update_fields=["silver"])
    with pytest.raises(GuildTechnologyError, match="需要990000"):
        technology.upgrade_technology(guild, tech.tech_key, operator)
    tech.refresh_from_db()
    grain.refresh_from_db()
    assert tech.level == 0 and grain.quantity == 10000000
    assert not GuildResourceLog.objects.filter(guild=guild, action="tech_upgrade").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("old_silver,old_grain", [(45000, 30000), (900000, 600000)])
def test_mixed_old_and_new_upgrades_refund_each_actual_cost(priced_guild, old_silver, old_grain):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    # 一级在旧价时期购买，二级按新价购买。
    tech.level = 1
    tech.save(update_fields=["level"])
    GuildResourceLog.objects.create(
        guild=guild,
        action="tech_upgrade",
        silver_change=-old_silver,
        grain_change=-old_grain,
        gold_bar_change=-1,
        note=f"升级经验果图纸至1级（消耗银两×{old_silver}、粮食×{old_grain}、金条×1）",
    )
    technology.upgrade_technology(guild, tech.tech_key, operator)
    assert technology.reset_technology_route(guild, "hero", operator) == {
        "silver": (old_silver + 3240000) // 2,
        "grain": (old_grain + 2160000) // 2,
        "gold_bar": 2,
    }


@pytest.mark.django_db
def test_previous_reset_payments_are_not_refunded_again(priced_guild):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    GuildResourceLog.objects.create(
        guild=guild,
        action="tech_upgrade",
        silver_change=-9000000,
        grain_change=-6000000,
        note="升级经验果图纸至1级（上轮升级）",
    )
    GuildResourceLog.objects.create(
        guild=guild,
        action="tech_reset",
        note="取消英雄系路线并清零等级，返还已投入资源的50%",
    )
    # 无流水的历史等级采用原基准价，不能使用上轮流水或调价后的费用。
    tech.level = 1
    tech.save(update_fields=["level"])
    assert technology.reset_technology_route(guild, "hero", operator) == {
        "silver": 22500,
        "grain": 15000,
        "gold_bar": 0,
    }


@pytest.mark.parametrize(
    "tech_key,resource,expected",
    [
        ("hero_marrow_pill", "red_ruby", [8, 27, 60]),
        ("wolf_golden_white_horse", "gold_bar", [27, 95, 216]),
        ("wolf_golden_white_horse", "red_ruby", [45, 158, 360]),
        ("wolf_soul_container", "red_ruby", [300, 1050, 2400]),
        ("forging_mechanism_blueprint", "gold_bar", [7, 14, 32, 63, 120]),
        ("forging_mechanism_blueprint", "red_ruby", [7, 14, 32, 63, 120]),
        ("hero_skill_books", "gold_bar", [4, 8, 16, 28, 54, 90, 184, 368, 900, 2100]),
        ("forging_equipment_forge", "gold_bar", [4, 8, 16, 28, 54, 90, 184, 368, 900, 2100]),
    ],
)
def test_high_quality_production_has_explicit_rare_resource_costs(tech_key, resource, expected):
    assert [
        technology.calculate_tech_upgrade_cost(tech_key, level)[resource] for level in range(len(expected))
    ] == expected


@pytest.mark.django_db
@pytest.mark.parametrize("paid_gold,expected_refund", [(0, 0), (1, 0), (2, 1)])
def test_historical_gold_refund_uses_actual_ledger_including_zero(priced_guild, paid_gold, expected_refund):
    from guilds.models import GuildResourceLog

    guild, tech, _grain, operator = priced_guild
    tech.level = 1
    tech.save(update_fields=["level"])
    GuildResourceLog.objects.create(
        guild=guild,
        action="tech_upgrade",
        gold_bar_change=-paid_gold,
        note="升级经验果图纸至1级（历史升级记录）",
    )
    assert technology.reset_technology_route(guild, "hero", operator)["gold_bar"] == expected_refund


@pytest.mark.django_db
@pytest.mark.parametrize(
    "suffix,expected_refund",
    [
        ("（消耗红宝石×200）", 100),
        ("（消耗红宝石×0）", 0),
        ("（历史升级记录）", 100),
        ("（历史备注、红宝石×999）", 100),
        ("（消耗银两×0）其他说明、红宝石×999）", 100),
        ("（消耗红宝石×999", 100),
    ],
)
def test_historical_ruby_refund_requires_complete_consumption_details(priced_guild, suffix, expected_refund):
    from guilds.models import GuildResourceLog, GuildTechnology

    guild, _tech, _grain, operator = priced_guild
    guild.active_technology_route = "wolf"
    guild.save(update_fields=["active_technology_route"])
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_soul_container", category="wolf", level=1, max_level=3)
    GuildResourceLog.objects.create(guild=guild, action="tech_upgrade", note="升级灵魂容器至1级" + suffix)
    assert technology.reset_technology_route(guild, "wolf", operator) == {"red_ruby": expected_refund}


@pytest.mark.django_db
def test_ruby_refund_combines_old_and_new_prices_but_excludes_previous_round(priced_guild):
    from guilds.models import GuildResourceLog, GuildTechnology

    guild, _tech, _grain, operator = priced_guild
    guild.active_technology_route = "wolf"
    guild.save(update_fields=["active_technology_route"])
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_soul_container", category="wolf", level=2, max_level=3)
    GuildResourceLog.objects.create(guild=guild, action="tech_upgrade", note="升级灵魂容器至1级（消耗红宝石×9999）")
    GuildResourceLog.objects.create(
        guild=guild, action="tech_reset", note="取消狼系路线并清零等级，返还已投入资源的50%"
    )
    GuildResourceLog.objects.create(guild=guild, action="tech_upgrade", note="升级灵魂容器至1级（消耗红宝石×200）")
    GuildResourceLog.objects.create(guild=guild, action="tech_upgrade", note="升级灵魂容器至2级（消耗红宝石×1050）")
    assert technology.reset_technology_route(guild, "wolf", operator) == {"red_ruby": 625}
