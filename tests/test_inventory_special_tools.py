from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import InsufficientStockError, ItemNotUsableError
from gameplay.constants import PVPConstants
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import set_warehouse_grain_quantity_locked
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


def _item_template(*, key: str, name: str, effect_payload: dict) -> ItemTemplate:
    return ItemTemplate.objects.create(
        key=key,
        name=name,
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        is_usable=True,
        effect_payload=effect_payload,
    )


def _make_attackable_pair(source: Manor, target: Manor) -> None:
    now = timezone.now()
    for manor in (source, target):
        manor.prestige = PVPConstants.RAID_PRESTIGE_PROTECTION_CUTOFF
        manor.newbie_protection_until = None
        manor.peace_shield_until = None
        manor.resource_updated_at = now
        manor.save(
            update_fields=[
                "prestige",
                "newbie_protection_until",
                "peace_shield_until",
                "resource_updated_at",
            ]
        )


def test_experience_blueprint_consumes_inputs_and_grants_result(django_user_model):
    user = django_user_model.objects.create_user(username="experience_blueprint_user", password="pass12345")
    manor = ensure_manor(user)
    fruit_template = ItemTemplate.objects.create(
        key="experience_fruit_test",
        name="经验果",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
    )
    result_template = ItemTemplate.objects.create(
        key="experience_peach_test",
        name="经验桃子",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 28800},
    )
    blueprint_template = _item_template(
        key="blueprint_experience_peach_test",
        name="经验桃子合成图纸",
        effect_payload={
            "action": "synthesize_item",
            "result_item_key": result_template.key,
            "result_quantity": 1,
            "required_items": {fruit_template.key: 16},
        },
    )
    fruit = InventoryItem.objects.create(
        manor=manor,
        template=fruit_template,
        quantity=20,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    blueprint = InventoryItem.objects.create(
        manor=manor,
        template=blueprint_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    result = use_inventory_item(blueprint, manor)

    assert result["result_item_key"] == result_template.key
    assert result["result_quantity"] == 1
    fruit.refresh_from_db()
    assert fruit.quantity == 4
    assert not InventoryItem.objects.filter(pk=blueprint.pk).exists()
    assert InventoryItem.objects.get(manor=manor, template=result_template).quantity == 1


def test_experience_blueprint_rolls_back_when_inputs_are_insufficient(django_user_model):
    user = django_user_model.objects.create_user(username="experience_blueprint_short_user", password="pass12345")
    manor = ensure_manor(user)
    fruit_template = ItemTemplate.objects.create(
        key="experience_fruit_short_test",
        name="经验果",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
    )
    result_template = ItemTemplate.objects.create(
        key="experience_pineapple_short_test",
        name="经验菠萝",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 57600},
    )
    blueprint_template = _item_template(
        key="blueprint_experience_pineapple_short_test",
        name="经验菠萝合成图纸",
        effect_payload={
            "action": "synthesize_item",
            "result_item_key": result_template.key,
            "required_items": {fruit_template.key: 32},
        },
    )
    fruit = InventoryItem.objects.create(
        manor=manor,
        template=fruit_template,
        quantity=31,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    blueprint = InventoryItem.objects.create(
        manor=manor,
        template=blueprint_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(InsufficientStockError):
        use_inventory_item(blueprint, manor)

    fruit.refresh_from_db()
    blueprint.refresh_from_db()
    assert fruit.quantity == 31
    assert blueprint.quantity == 1
    assert not InventoryItem.objects.filter(manor=manor, template=result_template).exists()


def test_action_point_potion_restores_points_and_consumes_once(django_user_model):
    user = django_user_model.objects.create_user(username="action_potion_user", password="pass12345")
    manor = ensure_manor(user)
    now = timezone.now()
    manor.action_points = 850
    manor.action_points_updated_at = now
    manor.save(update_fields=["action_points", "action_points_updated_at"])
    potion_template = _item_template(
        key="action_point_potion_test",
        name="行动力药水",
        effect_payload={"action": "restore_action_points", "amount": 200},
    )
    potion = InventoryItem.objects.create(
        manor=manor,
        template=potion_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    result = use_inventory_item(potion, manor)

    assert result["action_points_gained"] == 150
    assert result["action_points"] == 1000
    manor.refresh_from_db()
    assert manor.action_points == 1000
    assert not InventoryItem.objects.filter(pk=potion.pk).exists()


@pytest.mark.parametrize("resource,label,name", [("silver", "银两", "均贫卡"), ("grain", "粮食", "均粮卡")])
@pytest.mark.parametrize(
    "source_amount,target_amount,capacity,expected_source,expected_target,discarded",
    [
        (200, 800, 1000, 500, 500, 0),
        (800, 200, 1000, 500, 500, 0),
        (950, 1001, 1000, 975, 975, 0),
        (100, 3000, 1000, 1000, 1550, 550),
        (2500, 501, 1000, 1000, 1500, 500),
        (500, 500, 1000, 500, 500, 0),
        (0, 0, 1000, 0, 0, 0),
        (0, 2000, 1000, 1000, 1000, 0),
    ],
)
def test_resource_cards_average_both_balances(
    django_user_model,
    resource,
    label,
    name,
    source_amount,
    target_amount,
    capacity,
    expected_source,
    expected_target,
    discarded,
):
    source = ensure_manor(django_user_model.objects.create_user(username="equalization_source"))
    target = ensure_manor(django_user_model.objects.create_user(username="equalization_target"))
    _make_attackable_pair(source, target)
    Manor.objects.filter(pk=source.pk).update(**{resource: source_amount, f"{resource}_capacity": capacity})
    # Only the user's capacity limits their share; the target still receives the average.
    Manor.objects.filter(pk=target.pk).update(**{resource: target_amount, f"{resource}_capacity": 600})
    if resource == "grain":
        grain_template, _ = ItemTemplate.objects.get_or_create(
            key="grain",
            defaults={"name": "粮食", "effect_type": ItemTemplate.EffectType.RESOURCE},
        )
        with transaction.atomic():
            for manor, amount in ((source, source_amount), (target, target_amount)):
                locked = Manor.objects.select_for_update().get(pk=manor.pk)
                set_warehouse_grain_quantity_locked(locked, amount, grain_template=grain_template)
        # The warehouse ledger must win over a stale Manor projection.
        for manor, amount in ((source, source_amount), (target, target_amount)):
            if amount > 0:
                Manor.objects.filter(pk=manor.pk).update(grain=9999)
    card_template = _item_template(
        key=f"guild_{resource}_boost_card_test",
        name=name,
        effect_payload={"action": f"transfer_target_{resource}"},
    )
    card = InventoryItem.objects.create(
        manor=source,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    result = use_inventory_item(card, source, target_manor_id=target.pk)

    source.refresh_from_db()
    target.refresh_from_db()
    assert getattr(source, resource) == expected_source
    assert getattr(target, resource) == expected_target
    assert result[f"平均{label}"] == expected_target
    assert result["超限丢弃"] == discarded
    assert not InventoryItem.objects.filter(pk=card.pk).exists()
    from gameplay.models import ResourceEvent, TargetItemEffectCooldown

    for manor, original, final in (
        (source, source_amount, expected_source),
        (target, target_amount, expected_target),
    ):
        events = list(
            ResourceEvent.objects.filter(
                manor=manor,
                resource_type=resource,
                reason=ResourceEvent.Reason.ITEM_USE,
            ).values_list("delta", flat=True)
        )
        assert sum(events) == final - original
        if resource == "grain":
            row = InventoryItem.objects.filter(
                manor=manor,
                template=grain_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            ).first()
            assert (row.quantity if row else 0) == final
    assert TargetItemEffectCooldown.objects.filter(manor=target, effect_key=f"{resource}_transfer").exists()


@pytest.mark.parametrize("resource,name", [("silver", "均贫卡"), ("grain", "均粮卡")])
def test_resource_card_protection_and_cooldown_preserve_the_card(django_user_model, resource, name):
    from datetime import timedelta

    from core.exceptions import ItemNotUsableError
    from gameplay.models import TargetItemEffectCooldown

    source = ensure_manor(django_user_model.objects.create_user(username="card_protection_source"))
    target = ensure_manor(django_user_model.objects.create_user(username="card_protection_target"))
    _make_attackable_pair(source, target)
    template = _item_template(
        key=f"protected_{resource}_card",
        name=name,
        effect_payload={"action": f"transfer_target_{resource}"},
    )
    card = InventoryItem.objects.create(manor=source, template=template, quantity=2)
    with pytest.raises(ItemNotUsableError, match="请选择目标庄园"):
        use_inventory_item(card, source)
    with pytest.raises(ItemNotUsableError):
        use_inventory_item(card, source, target_manor_id=source.pk)
    Manor.objects.filter(pk=target.pk).update(peace_shield_until=timezone.now() + timedelta(hours=1))
    with pytest.raises(ItemNotUsableError):
        use_inventory_item(card, source, target_manor_id=target.pk)
    Manor.objects.filter(pk=target.pk).update(peace_shield_until=None)
    use_inventory_item(card, source, target_manor_id=target.pk)
    with pytest.raises(ItemNotUsableError, match="近1小时"):
        use_inventory_item(card, source, target_manor_id=target.pk)
    card.refresh_from_db()
    assert card.quantity == 1
    assert TargetItemEffectCooldown.objects.filter(manor=target, effect_key=f"{resource}_transfer").count() == 1


def test_sheep_wrath_consumes_gold_injures_every_guest_and_clears_target_resources(django_user_model):
    source_user = django_user_model.objects.create_user(username="sheep_wrath_source", password="pass12345")
    target_user = django_user_model.objects.create_user(username="sheep_wrath_target", password="pass12345")
    source = ensure_manor(source_user)
    target = ensure_manor(target_user)
    _make_attackable_pair(source, target)
    Manor.objects.filter(pk=target.pk).update(silver=4321, resource_updated_at=timezone.now())

    grain_template, _ = ItemTemplate.objects.get_or_create(
        key="grain",
        defaults={"name": "粮食", "effect_type": ItemTemplate.EffectType.RESOURCE},
    )
    with transaction.atomic():
        locked_target = Manor.objects.select_for_update().get(pk=target.pk)
        set_warehouse_grain_quantity_locked(locked_target, 8765, grain_template=grain_template)

    gold_template, _ = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={"name": "金条", "effect_type": ItemTemplate.EffectType.RESOURCE},
    )
    gold_bars = InventoryItem.objects.create(
        manor=source,
        template=gold_template,
        quantity=12,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    wrath_template = _item_template(
        key="guild_sheep_wrath_test",
        name="羊的愤怒",
        effect_payload={"action": "sheep_wrath_target"},
    )
    wrath = InventoryItem.objects.create(
        manor=source,
        template=wrath_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    guest_template = GuestTemplate.objects.create(
        key="sheep_wrath_guest_test",
        name="受试门客",
        archetype="military",
        rarity="green",
    )
    guests = [
        Guest.objects.create(
            manor=target,
            template=guest_template,
            current_hp=300,
            status=GuestStatus.IDLE,
        )
        for _ in range(2)
    ]

    result = use_inventory_item(wrath, source, target_manor_id=target.pk)

    target.refresh_from_db()
    for guest in guests:
        guest.refresh_from_db()
        assert guest.current_hp == 1
        assert guest.status == GuestStatus.INJURED
    assert result["受影响门客"] == 2
    assert result["清空银两"] == 4321
    assert result["清空粮食"] == 8765
    assert target.silver == 0
    assert target.grain == 0
    gold_bars.refresh_from_db()
    assert gold_bars.quantity == 2
    assert not InventoryItem.objects.filter(pk=wrath.pk).exists()


def test_slave_card_occupies_guest_for_one_hour_then_releases_it(django_user_model):
    from gameplay.models import TargetItemEffectCooldown, TemporaryGuestOccupation
    from gameplay.services.inventory.target_items import release_expired_guest_occupations

    source = ensure_manor(django_user_model.objects.create_user(username="slave_card_source"))
    target = ensure_manor(django_user_model.objects.create_user(username="slave_card_target"))
    _make_attackable_pair(source, target)
    guest_template = GuestTemplate.objects.create(
        key="slave_card_guest_test",
        name="可占用门客",
        archetype="military",
        rarity="green",
    )
    guest = Guest.objects.create(manor=target, template=guest_template, status=GuestStatus.IDLE)
    card_template = _item_template(
        key="slave_card_behavior_test",
        name="奴隶卡",
        effect_payload={"action": "occupy_target_guest"},
    )
    card = InventoryItem.objects.create(manor=source, template=card_template, quantity=1)
    result = use_inventory_item(card, source, target_manor_id=target.pk)

    guest.refresh_from_db()
    occupation = TemporaryGuestOccupation.objects.get(guest=guest)
    assert guest.status == GuestStatus.WORKING
    assert result["受影响门客"] == guest.display_name
    assert abs((occupation.expires_at - timezone.now()) - timedelta(hours=1)) < timedelta(seconds=5)
    assert not InventoryItem.objects.filter(pk=card.pk).exists()
    assert TargetItemEffectCooldown.objects.filter(manor=target, effect_key="slave").exists()

    assert release_expired_guest_occupations(now=occupation.expires_at) == 1
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE
    assert not TemporaryGuestOccupation.objects.filter(pk=occupation.pk).exists()


def test_theft_card_transfers_one_unbound_tradeable_blue_item(django_user_model):
    from gameplay.models import TargetItemEffectCooldown

    source = ensure_manor(django_user_model.objects.create_user(username="theft_card_source"))
    target = ensure_manor(django_user_model.objects.create_user(username="theft_card_target"))
    _make_attackable_pair(source, target)
    eligible = ItemTemplate.objects.create(
        key="theft_card_eligible_test",
        name="可夺取蓝物品",
        rarity="blue",
        tradeable=True,
    )
    low_rarity = ItemTemplate.objects.create(
        key="theft_card_low_rarity_test",
        name="不可夺取绿物品",
        rarity="green",
        tradeable=True,
    )
    bound_only = ItemTemplate.objects.create(
        key="theft_card_bound_test",
        name="不可夺取绑定物品",
        rarity="orange",
        tradeable=True,
    )
    non_tradeable = ItemTemplate.objects.create(
        key="theft_card_non_tradeable_test",
        name="不可交易紫物品",
        rarity="purple",
        tradeable=False,
    )
    target_eligible = InventoryItem.objects.create(manor=target, template=eligible, quantity=2, bound_quantity=1)
    excluded_rows = [
        InventoryItem.objects.create(manor=target, template=low_rarity, quantity=1),
        InventoryItem.objects.create(manor=target, template=bound_only, quantity=1, bound_quantity=1),
        InventoryItem.objects.create(manor=target, template=non_tradeable, quantity=1),
    ]
    source_eligible = InventoryItem.objects.create(manor=source, template=eligible, quantity=2, bound_quantity=1)
    card_template = _item_template(
        key="theft_card_behavior_test",
        name="抢夺卡",
        effect_payload={"action": "steal_target_item"},
    )
    card = InventoryItem.objects.create(manor=source, template=card_template, quantity=1)

    result = use_inventory_item(card, source, target_manor_id=target.pk)

    target_eligible.refresh_from_db()
    source_eligible.refresh_from_db()
    for row in excluded_rows:
        row.refresh_from_db()
        assert row.quantity == 1
    assert target_eligible.quantity == 1
    assert target_eligible.bound_quantity == 1
    assert source_eligible.quantity == 3
    assert source_eligible.bound_quantity == 1
    assert result["获得物品"] == eligible.name
    assert not InventoryItem.objects.filter(pk=card.pk).exists()
    assert TargetItemEffectCooldown.objects.filter(manor=target, effect_key="theft").exists()


def test_injury_card_sets_one_eligible_guest_to_one_hp(django_user_model):
    from gameplay.models import TargetItemEffectCooldown

    source = ensure_manor(django_user_model.objects.create_user(username="injury_card_source"))
    target = ensure_manor(django_user_model.objects.create_user(username="injury_card_target"))
    _make_attackable_pair(source, target)
    guest_template = GuestTemplate.objects.create(
        key="injury_card_guest_test",
        name="可重伤门客",
        archetype="military",
        rarity="green",
    )
    guest = Guest.objects.create(manor=target, template=guest_template, current_hp=300, status=GuestStatus.IDLE)
    already_injured = Guest.objects.create(
        manor=target,
        template=guest_template,
        current_hp=1,
        status=GuestStatus.INJURED,
    )
    card_template = _item_template(
        key="injury_card_behavior_test",
        name="重伤卡",
        effect_payload={"action": "injure_target_guest"},
    )
    card = InventoryItem.objects.create(manor=source, template=card_template, quantity=1)

    result = use_inventory_item(card, source, target_manor_id=target.pk)

    guest.refresh_from_db()
    already_injured.refresh_from_db()
    assert guest.current_hp == 1
    assert guest.status == GuestStatus.INJURED
    assert guest.injury_loyalty_processed_at is not None
    assert already_injured.status == GuestStatus.INJURED
    assert already_injured.injury_loyalty_processed_at is None
    assert result["受伤门客"] == guest.display_name
    assert result["生命"] == 1
    assert not InventoryItem.objects.filter(pk=card.pk).exists()
    assert TargetItemEffectCooldown.objects.filter(manor=target, effect_key="injury").exists()


@pytest.mark.parametrize(
    "action,effect_key",
    [
        ("occupy_target_guest", "slave"),
        ("steal_target_item", "theft"),
        ("injure_target_guest", "injury"),
    ],
)
def test_target_guest_and_theft_cards_preserve_card_when_target_has_no_eligible_object(
    django_user_model,
    action,
    effect_key,
):
    from gameplay.models import TargetItemEffectCooldown

    source = ensure_manor(django_user_model.objects.create_user(username=f"empty_target_{effect_key}_source"))
    target = ensure_manor(django_user_model.objects.create_user(username=f"empty_target_{effect_key}_target"))
    _make_attackable_pair(source, target)
    card_template = _item_template(
        key=f"empty_target_{effect_key}_card_test",
        name="目标卡",
        effect_payload={"action": action},
    )
    card = InventoryItem.objects.create(manor=source, template=card_template, quantity=2)

    with pytest.raises(ItemNotUsableError):
        use_inventory_item(card, source, target_manor_id=target.pk)

    card.refresh_from_db()
    assert card.quantity == 2
    assert not TargetItemEffectCooldown.objects.filter(manor=target, effect_key=effect_key).exists()
