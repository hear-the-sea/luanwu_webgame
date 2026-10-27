"""轮换集市的真实购买入口、占领权限、刷新边界与绑定发奖。"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import InventoryItem, ItemTemplate, Manor, Season, SeasonMember
from gameplay.models.season import SeasonSite
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.market_stock import market_stock_context, validate_market_control

pytestmark = pytest.mark.django_db


@pytest.fixture
def market_world(user_factory, monkeypatch):
    clock = [timezone.now()]
    monkeypatch.setattr("gameplay.services.season.actions.timezone.now", lambda: clock[0])
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["market_control"] = {
        "enabled": True,
        "refresh_hours": 24,
        "offer_count": 3,
        "owner_return_bonus_percent": 5,
        "pool": [
            {
                "key": f"parcel_{index}",
                "name": f"军需包{index}",
                "cost": 50,
                "limit": 1,
                "items": {"test_market_token": 2},
            }
            for index in range(6)
        ],
    }
    ItemTemplate.objects.get_or_create(key="test_market_token", defaults={"name": "测试军需", "effect_type": "tool"})
    season = Season.objects.create(
        name="轮换集市测试",
        starts_at=clock[0] - timedelta(days=4, hours=7),
        ends_at=clock[0] + timedelta(days=85),
        rules=rules,
    )
    site = SeasonSite.objects.create(season=season, key="river:market", owner="fiction", last_score_at=clock[0])

    def player(faction="fiction", coins=1000):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        return SeasonMember.objects.create(season=season, manor=manor, faction=faction, coins=coins)

    return season, site, player, clock


def buy(member, offer, period, token=None):
    return perform_action(
        member.manor,
        member.season_id,
        "market_purchase",
        str(token or uuid4()),
        {"item_key": offer["key"], "market_period": period},
    )


def test_rotation_is_read_only_deterministic_and_anchored_to_season(market_world):
    season, _, player, clock = market_world
    member = player()
    context = market_stock_context(season, member, clock[0])
    assert context["period"] == 4
    assert context["refresh_at"] == season.starts_at + timedelta(days=5)
    assert len({row["key"] for row in context["offers"]}) == 3
    assert market_stock_context(season, member, clock[0]) == context
    assert not member.claims.exists() and not member.actions.exists()
    assert not InventoryItem.objects.filter(manor=member.manor, template__key="test_market_token").exists()


def test_purchase_bound_reward_and_same_token_retry_charge_once(market_world):
    season, _, player, clock = market_world
    member = player()
    context = market_stock_context(season, member, clock[0])
    offer = context["offers"][0]
    token = uuid4()
    result = buy(member, offer, context["period"], token)
    assert buy(member, offer, context["period"], token) == result
    member.refresh_from_db()
    assert member.coins == 950
    stock = InventoryItem.objects.get(manor=member.manor, template__key="test_market_token")
    assert stock.quantity == stock.bound_quantity == 2
    assert member.claims.get().quantity == 1
    assert len(member.claims.get().key) <= 64
    assert member.actions.filter(kind="market_purchase").count() == 1
    with pytest.raises(GameError, match="限购"):
        buy(member, offer, context["period"])


def test_owner_change_does_not_reroll_or_reset_personal_limits(market_world):
    season, site, player, clock = market_world
    member = player()
    enemy = player("history")
    before = market_stock_context(season, member, clock[0])
    offer = before["offers"][0]
    buy(member, offer, before["period"])
    site.owner = "history"
    site.save(update_fields=["owner"])
    after = market_stock_context(season, enemy, clock[0])
    assert [row["key"] for row in after["offers"]] == [row["key"] for row in before["offers"]]
    with pytest.raises(GameError, match="占领"):
        buy(member, before["offers"][1], before["period"])
    buy(enemy, offer, before["period"])
    # 即使测试环境改变成员阵营，个人本轮次数仍然沿用原有记录。
    member.faction = "history"
    member.save(update_fields=["faction"])
    with pytest.raises(GameError, match="限购"):
        buy(member, offer, before["period"])
    site.owner = "fiction"
    site.save(update_fields=["owner"])
    with pytest.raises(GameError, match="占领"):
        buy(enemy, before["offers"][1], before["period"])


def test_rotation_boundary_rejects_stale_page_and_renews_limits(market_world):
    season, _, player, clock = market_world
    member = player()
    before = market_stock_context(season, member, clock[0])
    buy(member, before["offers"][0], before["period"])
    clock[0] = before["refresh_at"]
    with pytest.raises(GameError, match="刷新"):
        buy(member, before["offers"][0], before["period"])
    refreshed = market_stock_context(season, member, clock[0])
    assert refreshed["period"] == before["period"] + 1
    assert all(row["remaining"] == 1 for row in refreshed["offers"])
    buy(member, refreshed["offers"][0], refreshed["period"])
    member.refresh_from_db()
    assert member.coins == 900


@pytest.mark.parametrize(
    "failure", ["missing_template", "coins", "unselected", "wrong_period", "closed", "missing_config"]
)
def test_failed_purchase_does_not_charge_or_record_claim(market_world, failure):
    season, _, player, clock = market_world
    member = player(coins=1 if failure == "coins" else 1000)
    context = market_stock_context(season, member, clock[0])
    offer = context["offers"][0]
    period = context["period"]
    if failure == "missing_template":
        for row in season.rules["campaign"]["market_control"]["pool"]:
            row["items"] = {"not_loaded_test_market_item": 1}
        season.save(update_fields=["rules"])
    elif failure == "unselected":
        selected = {row["key"] for row in context["offers"]}
        offer = next(row for row in season.rules["campaign"]["market_control"]["pool"] if row["key"] not in selected)
    elif failure == "wrong_period":
        period += 1
    elif failure == "closed":
        season.closed = True
        season.save(update_fields=["closed"])
    elif failure == "missing_config":
        season.rules["campaign"].pop("market_control")
        season.save(update_fields=["rules"])
        assert market_stock_context(season, member, clock[0]) == {"enabled": False, "offers": []}
    coins = member.coins
    with pytest.raises(GameError):
        buy(member, offer, period)
    member.refresh_from_db()
    assert member.coins == coins
    assert not member.claims.exists() and not member.actions.exists()
    assert not InventoryItem.objects.filter(manor=member.manor, template__key="test_market_token").exists()


@pytest.mark.parametrize(
    "field,value",
    [("refresh_hours", 0), ("refresh_hours", 169), ("offer_count", 7), ("owner_return_bonus_percent", 11)],
)
def test_market_config_rejects_unsafe_values(market_world, field, value):
    season, _, _, _ = market_world
    config = deepcopy(season.rules["campaign"]["market_control"])
    config[field] = value
    with pytest.raises(ImproperlyConfigured):
        validate_market_control(config)


@pytest.mark.parametrize(
    "field,value",
    [("key", "a" * 25), ("key", "parcel_1"), ("cost", -1), ("limit", 0), ("items", {"test_market_token": -2})],
)
def test_market_config_rejects_invalid_goods(market_world, field, value):
    season, _, _, _ = market_world
    config = deepcopy(season.rules["campaign"]["market_control"])
    config["pool"][0][field] = value
    with pytest.raises(ImproperlyConfigured):
        validate_market_control(config)


@pytest.mark.parametrize("owner", ["", "history"])
def test_non_owner_cannot_see_rotating_goods_or_refresh_details(market_world, owner):
    season, site, player, clock = market_world
    member = player("fiction")
    site.owner = owner
    site.save(update_fields=["owner"])
    context = market_stock_context(season, member, clock[0])
    assert not context["is_owner"]
    assert context["offers"] == []
    assert "period" not in context and "refresh_at" not in context


def test_owner_goods_include_item_image_and_quantity(market_world):
    season, _, player, clock = market_world
    offer = market_stock_context(season, player(), clock[0])["offers"][0]
    assert offer["goods"][0]["key"] == "test_market_token"
    assert offer["goods"][0]["quantity"] == 2
    assert offer["goods"][0]["image"].endswith("season-item-parcel.svg")
