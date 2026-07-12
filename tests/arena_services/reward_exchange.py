from __future__ import annotations

import pytest

from core.exceptions import ArenaExchangeError, ArenaRewardLimitError, MessageError
from gameplay.models import ArenaExchangeRecord, InventoryItem, ItemTemplate
from gameplay.services.arena.core import exchange_arena_reward
from gameplay.services.arena.rewards import get_arena_reward_definition, select_weekly_blueprint_key
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
    assert ArenaExchangeRecord.objects.filter(manor=manor, reward_key="grain_pack_small").count() == 1


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

    monkeypatch.setattr("gameplay.services.arena.helpers.random.random", lambda: 0.0)
    result = exchange_arena_reward(manor, "gladiator_chest", quantity=1)

    manor.refresh_from_db()
    assert result.total_cost == 500
    assert manor.arena_coins == 100
    assert manor.silver == initial_silver + 10000
    assert result.credited_resources == {"silver": 10000}
    assert result.granted_items == {"equip_jiaodoushitoukui": 1}
    assert result.random_granted_items == {"equip_jiaodoushitoukui": 1}
    assert InventoryItem.objects.filter(manor=manor, template__key="equip_jiaodoushitoukui", quantity=1).exists()


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
def test_exchange_arena_reward_rejects_rotating_blueprint_absent_from_catalog(monkeypatch):
    user = User.objects.create_user(
        username="arena_exchange_blueprint_invalid",
        password="pass123",
        email="arena_exchange_blueprint_invalid@test.local",
    )
    manor = ensure_manor(user)
    manor.arena_coins = 600
    manor.save(update_fields=["arena_coins"])
    ItemTemplate.objects.create(
        key="blueprint_xiaoweitoukie",
        name="校尉头盔图纸",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        tradeable=True,
        is_usable=False,
    )

    monkeypatch.setattr("gameplay.services.arena.exchange_helpers.load_blueprint_catalog", lambda: {})

    with pytest.raises(ArenaExchangeError, match="图纸兑换配置无效"):
        exchange_arena_reward(manor, "blueprint_blue_exchange", quantity=1)

    manor.refresh_from_db()
    assert manor.arena_coins == 600
    assert not InventoryItem.objects.filter(manor=manor, template__key="blueprint_xiaoweitoukie").exists()


@pytest.mark.django_db
def test_exchange_arena_reward_grants_current_rotating_blueprint_after_catalog_validation(monkeypatch):
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
    assert reward.rotating_blueprint_pool is not None
    blueprint_key = select_weekly_blueprint_key(reward.rotating_blueprint_pool)
    ItemTemplate.objects.create(
        key=blueprint_key,
        name="本周蓝色图纸",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        tradeable=True,
        is_usable=False,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.exchange_helpers.load_blueprint_catalog",
        lambda: {
            blueprint_key: BlueprintCatalogEntry(
                key=blueprint_key,
                rarity="blue",
                result_key="equip_test_blueprint_result",
                result_rarity="blue",
            )
        },
    )

    result = exchange_arena_reward(manor, reward.key, quantity=1)

    manor.refresh_from_db()
    assert manor.arena_coins == 0
    assert result.total_cost == 600
    assert result.granted_items == {blueprint_key: 1}
    assert InventoryItem.objects.filter(manor=manor, template__key=blueprint_key, quantity=1).exists()


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
