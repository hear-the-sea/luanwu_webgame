import pytest

from core.exceptions import ItemNotConfiguredError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_peace_shield_invalid_duration_type_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="peace_shield_invalid_duration_type", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="peace_shield_invalid_duration_type_test",
        name="坏时长免战牌",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": "bad"},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_peace_shield_non_positive_duration_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="peace_shield_invalid_duration_value", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="peace_shield_invalid_duration_value_test",
        name="零时长免战牌",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={"duration": 0},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
