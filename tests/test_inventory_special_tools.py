from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor

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
