import pytest

from core.exceptions import GuestAlreadyOwnedError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate


@pytest.mark.django_db
def test_summon_guest_item_can_require_additional_copies_of_itself(django_user_model):
    user = django_user_model.objects.create_user(username="crayon_summon_cost", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="orig_crayon_shinchan_purple_test",
        name="蜡笔小新",
        archetype="military",
        rarity="purple",
        recruitable=False,
    )
    item_template = ItemTemplate.objects.create(
        key="kasukabe_crayon_test",
        name="小新的蜡笔",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        tradeable=True,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"kasukabe_crayon_test": 149},
            "exclusive_template_keys": [template.key],
            "choices": [{"template_key": template.key, "weight": 100}],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=150,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    result = use_inventory_item(item, manor)

    assert "蜡笔小新" in result["_message"]
    assert manor.guests.filter(template=template).count() == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_summon_guest_item_rejects_duplicate_exclusive_guest_without_consuming_crayons(django_user_model):
    user = django_user_model.objects.create_user(username="crayon_summon_duplicate", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="orig_crayon_shinchan_purple_duplicate_test",
        name="蜡笔小新",
        archetype="military",
        rarity="purple",
        recruitable=False,
    )
    Guest.objects.create(manor=manor, template=template)
    item_template = ItemTemplate.objects.create(
        key="kasukabe_crayon_duplicate_test",
        name="小新的蜡笔",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="blue",
        tradeable=True,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"kasukabe_crayon_duplicate_test": 149},
            "exclusive_template_keys": [template.key],
            "choices": [{"template_key": template.key, "weight": 100}],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=150,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(GuestAlreadyOwnedError, match="庄园已拥有门客「蜡笔小新」，不可重复获得"):
        use_inventory_item(item, manor)

    item.refresh_from_db()
    assert item.quantity == 150
    assert manor.guests.filter(template=template).count() == 1
