from __future__ import annotations

import pytest

from battle.random_context import RNG_STREAM_LOOT, BattleRandomContext
from core.exceptions import ArenaExchangeError, ArenaRewardLimitError, MessageError
from gameplay.models import ArenaExchangeRecord, InventoryItem, ItemTemplate
from gameplay.services.arena.core import exchange_arena_reward
from gameplay.services.arena.exchange_helpers import ARENA_EXCHANGE_RANDOM_ITEMS_DISCRIMINATOR
from gameplay.services.arena.helpers import resolve_random_reward_items
from gameplay.services.arena.rewards import get_arena_reward_definition
from gameplay.services.buildings.blueprint_catalog import BlueprintCatalogEntry
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User, ensure_gladiator_item_templates, ensure_sanguoyanyi_arena_item_templates


@pytest.mark.django_db
def test_exchange_arena_reward_deducts_coins_and_creates_record():
    user = User.objects.create_user(username="arena_exchange", password="pass123", email="arena_exchange@test.local")
    manor = ensure_manor(user)
    manor.arena_coins = 1000
    manor.save(update_fields=["arena_coins"])
    initial_grain = manor.grain

    result = exchange_arena_reward(manor, "grain_pack_small", quantity=2)

    manor.refresh_from_db()
    assert result.total_cost == 160
    assert manor.arena_coins == 840
    assert manor.grain > initial_grain
    record = ArenaExchangeRecord.objects.get(manor=manor, reward_key="grain_pack_small")
    assert record.payload["replay"]["base_seed"] > 0
    assert record.payload["replay"]["rng_version"] > 0


@pytest.mark.django_db
def test_exchange_arena_reward_gladiator_chest_grants_silver_and_weighted_item(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_gladiator",
        password="pass123",
        email="arena_exchange_gladiator@test.local",
    )
    manor = ensure_manor(user)
    ensure_gladiator_item_templates()
    manor.arena_coins = 600
    manor.save(update_fields=["arena_coins"])
    initial_silver = manor.silver

    monkeypatch.setattr("battle.random_context.generate_base_seed", lambda: 9)
    result = exchange_arena_reward(manor, "gladiator_chest", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 500
    assert manor.arena_coins == 100
    assert manor.silver == initial_silver + 10000
    assert result.credited_resources == {"silver": 10000}
    assert result.granted_items == {"equip_jiaodoushitoukui": 1}
    assert result.random_granted_items == {"equip_jiaodoushitoukui": 1}
    assert InventoryItem.objects.filter(manor=manor, template__key="equip_jiaodoushitoukui", quantity=1).exists()
    record = ArenaExchangeRecord.objects.get(manor=manor, reward_key="gladiator_chest")
    replay = record.payload["replay"]
    replay_context = BattleRandomContext.create(replay["base_seed"], rng_version=replay["rng_version"])
    assert (
        resolve_random_reward_items(
            result.reward.random_items,
            result.quantity,
            rng=replay_context.rng(
                RNG_STREAM_LOOT,
                discriminator=ARENA_EXCHANGE_RANDOM_ITEMS_DISCRIMINATOR,
            ),
        )
        == result.random_granted_items
    )


@pytest.mark.django_db
def test_exchange_arena_reward_gladiator_chest_respects_daily_limit():
    user = User.objects.create_user(
        username="arena_exchange_gladiator_limit",
        password="pass123",
        email="arena_exchange_gladiator_limit@test.local",
    )
    manor = ensure_manor(user)
    ensure_gladiator_item_templates()
    manor.arena_coins = 3000
    manor.save(update_fields=["arena_coins"])

    exchange_arena_reward(manor, "gladiator_chest", quantity=2)

    with pytest.raises(ArenaRewardLimitError, match="角斗士宝箱 今日最多可兑换 2 次"):
        exchange_arena_reward(manor, "gladiator_chest", quantity=1)


@pytest.mark.django_db
def test_exchange_arena_reward_rejects_random_blueprint_when_rarity_pool_is_empty(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_blueprint_invalid",
        password="pass123",
        email="arena_exchange_blueprint_invalid@test.local",
    )
    manor = ensure_manor(user)
    manor.arena_coins = 600
    manor.save(update_fields=["arena_coins"])
    monkeypatch.setattr("gameplay.services.arena.exchange_helpers.load_blueprint_catalog", lambda: {})

    with pytest.raises(ArenaExchangeError, match="图纸兑换配置无效"):
        exchange_arena_reward(manor, "blueprint_blue_exchange", quantity=1)

    manor.refresh_from_db()
    assert manor.arena_coins == 600
    assert not InventoryItem.objects.filter(manor=manor).exists()


@pytest.mark.django_db
def test_exchange_arena_reward_randomly_grants_from_all_matching_blueprints(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_blueprint_valid",
        password="pass123",
        email="arena_exchange_blueprint_valid@test.local",
    )
    manor = ensure_manor(user)
    manor.arena_coins = 600
    manor.save(update_fields=["arena_coins"])
    reward = get_arena_reward_definition("blueprint_blue_exchange")
    assert reward is not None
    assert reward.name == "随机蓝色装备图纸"
    assert reward.random_blueprint_pool is not None
    assert reward.random_blueprint_pool.rarity == "blue"
    blueprint_key = "blueprint_random_blue_b"
    ItemTemplate.objects.create(
        key=blueprint_key,
        name="随机蓝色图纸B",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        tradeable=True,
        is_usable=False,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.exchange_helpers.load_blueprint_catalog",
        lambda: {
            "blueprint_random_blue_a": BlueprintCatalogEntry(
                key="blueprint_random_blue_a",
                rarity="blue",
                result_key="equip_test_blueprint_result_a",
                result_rarity="blue",
            ),
            blueprint_key: BlueprintCatalogEntry(
                key=blueprint_key,
                rarity="blue",
                result_key="equip_test_blueprint_result_b",
                result_rarity="blue",
            ),
            "blueprint_random_purple": BlueprintCatalogEntry(
                key="blueprint_random_purple",
                rarity="purple",
                result_key="equip_test_blueprint_result_purple",
                result_rarity="purple",
            ),
        },
    )
    monkeypatch.setattr("battle.random_context.generate_base_seed", lambda: 2)

    result = exchange_arena_reward(manor, reward.key, quantity=1)

    manor.refresh_from_db()
    assert manor.arena_coins == 0
    assert result.total_cost == 600
    assert result.granted_items == {blueprint_key: 1}
    assert result.random_granted_items == {blueprint_key: 1}
    assert InventoryItem.objects.filter(manor=manor, template__key=blueprint_key, quantity=1).exists()


def test_wulin_chest_config_has_requested_rewards_and_probability_tiers():
    reward = get_arena_reward_definition("wulin_chest")

    assert reward is not None
    assert reward.cost_coins == 1000
    assert reward.weekly_limit == 2
    options = {option.item_key: option for option in reward.random_items}
    purple_keys = {
        "equip_duoqinghuan",
        "equip_libiegou",
        "equip_duomingfeidao",
        "equip_tulongdao",
        "equip_biyudao",
        "equip_ziweiruanjian",
        "equip_xiuhuazhen",
        "equip_xuedao",
        "equip_jinshejian",
        "equip_yuanyangdao",
        "equip_shenghuoling",
    }
    orange_keys = {
        "equip_ruanweijia",
        "equip_yitianjian",
        "equip_yuanyuewandao",
        "equip_tianyamingyuedao",
        "equip_kongquelin",
        "equip_dagoubang",
        "equip_xuantiechongjian",
        "equip_shangshanfaeling",
    }

    assert set(options) == {"chunqiu_coin", *purple_keys, *orange_keys}
    assert sum(option.weight for option in options.values()) == 100
    assert (options["chunqiu_coin"].weight, options["chunqiu_coin"].amount) == (48, 10)
    assert all(options[key].weight == 4 and options[key].amount == 1 for key in purple_keys)
    assert all(options[key].weight == 1 and options[key].amount == 1 for key in orange_keys)


@pytest.mark.django_db
def test_exchange_arena_reward_wulin_chest_respects_weekly_limit(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_wulin_limit",
        password="pass123",
        email="arena_exchange_wulin_limit@test.local",
    )
    manor = ensure_manor(user)
    ItemTemplate.objects.get_or_create(
        key="chunqiu_coin",
        defaults={
            "name": "春秋币",
            "effect_type": ItemTemplate.EffectType.RESOURCE,
            "rarity": "blue",
            "tradeable": True,
            "is_usable": False,
        },
    )
    manor.arena_coins = 3000
    manor.save(update_fields=["arena_coins"])
    monkeypatch.setattr("battle.random_context.generate_base_seed", lambda: 10)

    result = exchange_arena_reward(manor, "wulin_chest", quantity=2)

    manor.refresh_from_db()
    assert result.total_cost == 2000
    assert manor.arena_coins == 1000
    assert result.granted_items == {"chunqiu_coin": 20}
    assert InventoryItem.objects.filter(manor=manor, template__key="chunqiu_coin", quantity=20).exists()

    with pytest.raises(ArenaRewardLimitError, match="武林宝箱 本周最多可兑换 2 次"):
        exchange_arena_reward(manor, "wulin_chest", quantity=1)

    manor.refresh_from_db()
    assert manor.arena_coins == 1000
    assert InventoryItem.objects.get(manor=manor, template__key="chunqiu_coin").quantity == 20


@pytest.mark.django_db
def test_exchange_arena_reward_panfeng_guest_card_grants_item():
    user = User.objects.create_user(
        username="arena_exchange_panfeng_card",
        password="pass123",
        email="arena_exchange_panfeng_card@test.local",
    )
    manor = ensure_manor(user)
    ensure_sanguoyanyi_arena_item_templates()
    manor.arena_coins = 1200
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, "panfeng_guest_exchange", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 1000
    assert manor.arena_coins == 200
    assert result.granted_items == {"panfeng_guest_card": 1}
    assert InventoryItem.objects.filter(manor=manor, template__key="panfeng_guest_card", quantity=1).exists()


@pytest.mark.django_db
def test_exchange_arena_reward_xingdaorong_guest_card_grants_item():
    user = User.objects.create_user(
        username="arena_exchange_xingdaorong_card",
        password="pass123",
        email="arena_exchange_xingdaorong_card@test.local",
    )
    manor = ensure_manor(user)
    ensure_sanguoyanyi_arena_item_templates()
    manor.arena_coins = 1200
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, "xingdaorong_guest_exchange", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 1000
    assert manor.arena_coins == 200
    assert result.granted_items == {"xingdaorong_guest_card": 1}
    assert InventoryItem.objects.filter(manor=manor, template__key="xingdaorong_guest_card", quantity=1).exists()


@pytest.mark.django_db
def test_exchange_arena_reward_peerless_general_upgrade_grants_item():
    user = User.objects.create_user(
        username="arena_exchange_peerless_upgrade",
        password="pass123",
        email="arena_exchange_peerless_upgrade@test.local",
    )
    manor = ensure_manor(user)
    ensure_sanguoyanyi_arena_item_templates()
    manor.arena_coins = 1200
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, "peerless_general_upgrade_reward", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 1000
    assert manor.arena_coins == 200
    assert result.granted_items == {"peerless_general_upgrade_token": 1}
    assert InventoryItem.objects.filter(
        manor=manor, template__key="peerless_general_upgrade_token", quantity=1
    ).exists()


@pytest.mark.django_db
def test_exchange_arena_reward_peerless_general_upgrade_2_grants_item():
    user = User.objects.create_user(
        username="arena_exchange_peerless_upgrade_2",
        password="pass123",
        email="arena_exchange_peerless_upgrade_2@test.local",
    )
    manor = ensure_manor(user)
    ensure_sanguoyanyi_arena_item_templates()
    manor.arena_coins = 12000
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, "peerless_general_upgrade_reward_2", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 10000
    assert manor.arena_coins == 2000
    assert result.granted_items == {"peerless_general_upgrade_token_2": 1}
    assert InventoryItem.objects.filter(
        manor=manor, template__key="peerless_general_upgrade_token_2", quantity=1
    ).exists()


@pytest.mark.django_db
def test_exchange_arena_reward_keeps_success_when_explicit_message_error(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_message_fail",
        password="pass123",
        email="arena_exchange_message_fail@test.local",
    )
    manor = ensure_manor(user)
    manor.arena_coins = 1000
    manor.save(update_fields=["arena_coins"])

    monkeypatch.setattr(
        "gameplay.services.arena.exchange_helpers.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    result = exchange_arena_reward(manor, "grain_pack_small", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 80
    assert manor.arena_coins == 920
    assert ArenaExchangeRecord.objects.filter(manor=manor, reward_key="grain_pack_small").count() == 1


@pytest.mark.django_db
def test_exchange_arena_reward_runtime_marker_error_bubbles_up(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_runtime_fail",
        password="pass123",
        email="arena_exchange_runtime_fail@test.local",
    )
    manor = ensure_manor(user)
    manor.arena_coins = 1000
    manor.save(update_fields=["arena_coins"])

    monkeypatch.setattr(
        "gameplay.services.arena.exchange_helpers.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        exchange_arena_reward(manor, "grain_pack_small", quantity=1)
