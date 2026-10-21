"""蚩尤甲运势迁移兼容已穿戴装备，且重复执行不叠加。"""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from gameplay.models import InventoryItem, ItemTemplate
from guests.models import GearItem, GearSlot, GearTemplate
from tests.test_equipment_template_sync import _create_guest

rules = import_module("gameplay.migrations.0230_chiyou_armor_luck")


@pytest.mark.django_db
@pytest.mark.parametrize("key", rules.ARMOR_KEYS)
def test_luck_sync_updates_existing_armor_once_and_preserves_other_stats(django_user_model, key):
    manor, guest = _create_guest(django_user_model, key)
    payload = {"hp": 600, "force": 40, "defense": 48, "required_level": 80}
    item, _ = ItemTemplate.objects.update_or_create(
        key=key, defaults={"name": "蚩尤甲", "effect_type": "equip_armor", "effect_payload": payload}
    )
    template, _ = GearTemplate.objects.update_or_create(
        key=key,
        defaults={"name": "蚩尤甲", "slot": GearSlot.ARMOR, "extra_stats": {"force": 40, "hp": 600}},
    )
    gear = GearItem.objects.create(manor=manor, template=template, guest=guest)
    stock = InventoryItem.objects.create(manor=manor, template=item, quantity=2, bound_quantity=1)
    guest_before = type(guest).objects.filter(pk=guest.pk).values().get()
    template_before = dict(template.extra_stats)

    for _ in range(2):
        rules.sync_chiyou_armor_luck(apps, SimpleNamespace(connection=connection))

    item.refresh_from_db()
    template.refresh_from_db()
    stock.refresh_from_db()
    gear.refresh_from_db()
    assert item.effect_payload == {**payload, "luck": 15}
    assert template.extra_stats == {**template_before, "luck": 15}
    assert type(guest).objects.filter(pk=guest.pk).values().get() == {
        **guest_before,
        "luck": guest_before["luck"] + 15,
    }
    assert (stock.quantity, stock.bound_quantity) == (2, 1)
    assert (gear.template_id, gear.guest_id) == (template.pk, guest.pk)
