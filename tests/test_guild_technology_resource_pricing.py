from types import SimpleNamespace

import pytest

from core.exceptions import GuildTechnologyError
from guilds import constants
from guilds.services import technology
from guilds.technology_tree import TECHNOLOGY_ROUTES


def test_resource_prices_scale_without_changing_troop_tactics_or_other_resources(monkeypatch):
    assert technology.calculate_tech_upgrade_cost("hero_fruit_blueprint", 0) == {
        "silver": 900000,
        "grain": 600000,
        "gold_bar": 1,
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
    ) == {"silver": 2960, "grain": 10960, "gold_bar": 9, "red_ruby": 11}


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
    assert largest_changed_cost == 93312000


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
    assert guild.silver == 9100000 and grain.quantity == 9400000
    log = GuildResourceLog.objects.get(guild=guild, action="tech_upgrade")
    assert (log.silver_change, log.grain_change, log.gold_bar_change) == (-900000, -600000, -1)
    assert technology.reset_technology_route(guild, "hero", operator) == {
        "silver": 450000,
        "grain": 300000,
        "gold_bar": 0,
    }


@pytest.mark.django_db
def test_upgrade_rejects_old_price_balance_without_spending_any_resources(priced_guild):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    guild.silver = 45000
    guild.save(update_fields=["silver"])
    with pytest.raises(GuildTechnologyError, match="需要900000"):
        technology.upgrade_technology(guild, tech.tech_key, operator)
    tech.refresh_from_db()
    grain.refresh_from_db()
    assert tech.level == 0 and grain.quantity == 10000000
    assert not GuildResourceLog.objects.filter(guild=guild, action="tech_upgrade").exists()


@pytest.mark.django_db
def test_mixed_old_and_new_upgrades_refund_each_actual_cost(priced_guild):
    from guilds.models import GuildResourceLog

    guild, tech, grain, operator = priced_guild
    # 一级在旧价时期购买，二级按新价购买。
    tech.level = 1
    tech.save(update_fields=["level"])
    GuildResourceLog.objects.create(
        guild=guild,
        action="tech_upgrade",
        silver_change=-45000,
        grain_change=-30000,
        gold_bar_change=-1,
        note="升级经验果图纸至1级（消耗银两×45000、粮食×30000、金条×1）",
    )
    technology.upgrade_technology(guild, tech.tech_key, operator)
    assert technology.reset_technology_route(guild, "hero", operator) == {
        "silver": (45000 + 2700000) // 2,
        "grain": (30000 + 1800000) // 2,
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
