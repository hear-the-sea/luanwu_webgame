from datetime import timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.models.bots import BotProfile
from guests.models import GearItem, GearTemplate, Guest, GuestTemplate, WorldUniqueGuest

rules = import_module("gameplay.migrations.0225_remove_ordinary_yang_zheng")


def test_yang_zheng_has_only_the_world_unique_yaml_definition():
    heroes = []
    for path in (Path(__file__).resolve().parents[1] / "data/guests").glob("*.yaml"):
        roster = yaml.safe_load(path.read_text()).get("heroes", {})
        heroes.extend(row for rows in roster.values() for row in rows if row.get("name") == "杨铮")
    assert len(heroes) == 1
    assert heroes[0]["key"] == rules.UNIQUE_KEY
    assert heroes[0]["is_world_unique"] is True
    assert heroes[0]["recruitable"] is False


@pytest.fixture
def yang_templates(db):
    unique, _ = GuestTemplate.objects.update_or_create(
        key=rules.UNIQUE_KEY,
        defaults={"name": "杨铮", "rarity": "purple", "is_world_unique": True, "recruitable": False},
    )
    old = GuestTemplate.objects.create(key=rules.OLD_KEY, name="杨铮", rarity="green", recruitable=True)
    return old, unique


@pytest.mark.django_db
def test_remove_is_repeatable_and_preserves_unique_template_and_state(yang_templates):
    _old, unique = yang_templates
    state, _ = WorldUniqueGuest.objects.get_or_create(template=unique)
    before = GuestTemplate.objects.filter(pk=unique.pk).values().get()
    state_before = WorldUniqueGuest.objects.filter(pk=state.pk).values().get()
    for _ in range(2):
        rules.remove_ordinary_yang_zheng(apps, SimpleNamespace(connection=connection))
    assert not GuestTemplate.objects.filter(key=rules.OLD_KEY).exists()
    assert GuestTemplate.objects.filter(pk=unique.pk).values().get() == before
    assert WorldUniqueGuest.objects.filter(pk=state.pk).values().get() == state_before


@pytest.mark.django_db
def test_real_yang_zheng_returns_equipment_and_gets_diaochan_scroll(yang_templates, manor_factory):
    old, _unique = yang_templates
    manor, _ = manor_factory()
    guest = Guest.objects.create(manor=manor, template=old)
    gear_template = GearTemplate.objects.create(key="migration_gear", name="旧杨铮装备", slot="weapon")
    gear = GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    scroll = ItemTemplate.objects.create(key=rules.REAL_PLAYER_COMPENSATION_ITEM_KEY, name="貂蝉招募卷轴")
    InventoryItem.objects.create(manor=manor, template=scroll, quantity=3, bound_quantity=1)

    rules.remove_ordinary_yang_zheng(apps, SimpleNamespace(connection=connection))

    assert not GuestTemplate.objects.filter(pk=old.pk).exists()
    assert not Guest.objects.filter(pk=guest.pk).exists()
    gear.refresh_from_db()
    assert gear.manor_id == manor.pk and gear.guest_id is None
    stock = InventoryItem.objects.get(manor=manor, template=scroll)
    assert (stock.quantity, stock.bound_quantity) == (4, 1)


@pytest.mark.django_db
def test_virtual_yang_zheng_and_equipment_are_deleted_without_compensation(yang_templates, manor_factory):
    old, _unique = yang_templates
    manor, _ = manor_factory()
    guest = Guest.objects.create(manor=manor, template=old)
    gear_template = GearTemplate.objects.create(key="virtual_migration_gear", name="虚拟装备", slot="weapon")
    gear = GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    now = timezone.now()
    BotProfile.objects.create(
        manor=manor,
        prestige_band="test",
        growth_seed=1,
        next_growth_at=now,
        abandon_at=now + timedelta(days=1),
        retire_at=now + timedelta(days=2),
    )
    scroll = ItemTemplate.objects.create(key=rules.REAL_PLAYER_COMPENSATION_ITEM_KEY, name="貂蝉招募卷轴")

    rules.remove_ordinary_yang_zheng(apps, SimpleNamespace(connection=connection))

    assert not GuestTemplate.objects.filter(pk=old.pk).exists()
    assert not Guest.objects.filter(pk=guest.pk).exists()
    assert not GearItem.objects.filter(pk=gear.pk).exists()
    assert not InventoryItem.objects.filter(manor=manor, template=scroll).exists()


@pytest.mark.django_db
def test_unrelated_template_reference_rolls_back_legacy_cleanup(yang_templates):
    old, _unique = yang_templates
    state = WorldUniqueGuest.objects.create(template=old)
    with pytest.raises(RuntimeError, match="已保留模板"):
        rules.remove_ordinary_yang_zheng(apps, SimpleNamespace(connection=connection))
    assert GuestTemplate.objects.filter(pk=old.pk).exists()
    assert WorldUniqueGuest.objects.filter(pk=state.pk).exists()


@pytest.mark.django_db
def test_missing_unique_identity_prevents_removing_the_old_template(yang_templates):
    old, unique = yang_templates
    unique.is_world_unique = False
    unique.save(update_fields=["is_world_unique"])
    with pytest.raises(RuntimeError, match="必须先同步全服唯一紫色杨铮"):
        rules.remove_ordinary_yang_zheng(apps, SimpleNamespace(connection=connection))
    assert GuestTemplate.objects.filter(pk=old.pk).exists()
