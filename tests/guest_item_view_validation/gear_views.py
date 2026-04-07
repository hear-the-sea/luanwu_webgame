from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django_redis.exceptions import ConnectionInterrupted

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate
from tests.guest_item_view_validation.support import bootstrap_guest_client


@pytest.mark.django_db
def test_gear_options_view_tolerates_cache_backend_failure(game_data, django_user_model, monkeypatch):
    cache.clear()
    user = django_user_model.objects.create_user(username="view_gear_options_cache_failure", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_gear_options_cache_failure", password="pass123")
    before_count = GearItem.objects.filter(manor=manor).count()

    monkeypatch.setattr(
        "guests.views.equipment.cache.get",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(
        "guests.views.equipment.cache.set",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    template = ItemTemplate.objects.create(
        key=f"view_gear_options_cache_failure_{manor.id}",
        name="缓存失败测试装备",
        effect_type="equip_weapon",
        rarity="green",
        effect_payload={"force": 8},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    response = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert response.status_code == 200
    payload = response.json()
    assert payload["slot"] == GearSlot.WEAPON
    assert len(payload["options"]) == 1
    assert payload["options"][0]["name"] == template.name
    assert payload["options"][0]["id"] == template.key
    assert payload["options"][0]["count"] == 2
    assert GearItem.objects.filter(manor=manor).count() == before_count
    assert not GearItem.objects.filter(manor=manor, template__key=template.key).exists()


@pytest.mark.django_db
def test_gear_options_view_runtime_marker_cache_error_bubbles_up(game_data, django_user_model, monkeypatch):
    cache.clear()
    user = django_user_model.objects.create_user(username="view_gear_options_runtime_marker", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_gear_options_runtime_marker", password="pass123")

    monkeypatch.setattr(
        "guests.views.equipment.cache.get",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})


@pytest.mark.django_db
def test_gear_options_view_does_not_materialize_gear_items_from_get(game_data, django_user_model):
    cache.clear()
    user = django_user_model.objects.create_user(username="view_gear_options_read_only", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_gear_options_read_only", password="pass123")
    before_count = GearItem.objects.filter(manor=manor).count()

    template = ItemTemplate.objects.create(
        key=f"view_gear_options_read_only_{manor.id}",
        name="只读测试装备",
        effect_type="equip_weapon",
        rarity="green",
        effect_payload={"force": 12},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    response = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert response.status_code == 200
    payload = response.json()
    assert payload["options"][0]["id"] == template.key
    assert payload["options"][0]["count"] == 1
    assert GearItem.objects.filter(manor=manor).count() == before_count
    assert not GearItem.objects.filter(manor=manor, template__key=template.key).exists()


@pytest.mark.django_db
def test_gear_options_view_lists_free_gear_without_inventory(game_data, django_user_model):
    cache.clear()
    user = django_user_model.objects.create_user(username="view_gear_options_free_gear", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_gear_options_free_gear", password="pass123")

    from guests.models import GearTemplate

    template = GearTemplate.objects.create(
        key=f"view_gear_options_free_gear_{manor.id}",
        name="自由装备测试刀",
        slot=GearSlot.WEAPON,
        rarity="green",
        extra_stats={"force": 11},
    )
    gear = GearItem.objects.create(manor=manor, template=template)

    response = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["options"]) == 1
    assert payload["options"][0]["id"] == gear.id
    assert payload["options"][0]["template_key"] == template.key
    assert payload["options"][0]["name"] == template.name
    assert payload["options"][0]["count"] == 1


@pytest.mark.django_db
def test_gear_options_view_accepts_nested_set_bonus_definition(game_data, django_user_model):
    cache.clear()
    user = django_user_model.objects.create_user(username="view_gear_options_nested_set_bonus", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_gear_options_nested_set_bonus", password="pass123")
    before_count = GearItem.objects.filter(manor=manor).count()

    template = ItemTemplate.objects.create(
        key=f"view_gear_options_nested_set_bonus_{manor.id}",
        name="嵌套套装测试刀",
        effect_type="equip_weapon",
        rarity="blue",
        effect_payload={
            "force": 18,
            "set_key": "nested_set_bonus_suite",
            "set_description": "嵌套套装",
            "set_bonus": {
                "pieces": 4,
                "bonus": {
                    "force": 12,
                    "hp": 80,
                },
            },
        },
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    response = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["options"]) == 1
    assert payload["options"][0]["template_key"] == template.key
    assert payload["options"][0]["name"] == template.name
    assert payload["options"][0]["count"] == 1
    assert GearItem.objects.filter(manor=manor).count() == before_count
    assert not GearItem.objects.filter(manor=manor, template__key=template.key).exists()


@pytest.mark.django_db
def test_equip_view_accepts_template_key_and_materializes_on_write(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_equip_template_key")

    template = ItemTemplate.objects.create(
        key=f"view_equip_template_key_{manor.id}",
        name="模板键测试佩刀",
        effect_type="equip_weapon",
        rarity="green",
        effect_payload={"force": 10},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    response = client.post(
        reverse("guests:equip"),
        {"guest": str(guest.pk), "gear": template.key, "slot": GearSlot.WEAPON},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    guest.refresh_from_db()
    equipped = guest.gear_items.get(template__key=template.key)
    assert equipped.template.slot == GearSlot.WEAPON
    assert equipped.guest_id == guest.id
    assert GearItem.objects.filter(manor=manor, template__key=template.key, guest=guest).count() == 1
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()


@pytest.mark.django_db
def test_equip_view_accepts_free_gear_id_without_inventory(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_equip_free_gear_id")

    from guests.models import GearTemplate

    template = GearTemplate.objects.create(
        key=f"view_equip_free_gear_id_{manor.id}",
        name="自由装备测试枪",
        slot=GearSlot.WEAPON,
        rarity="green",
        extra_stats={"force": 9},
    )
    gear = GearItem.objects.create(manor=manor, template=template)

    response = client.post(
        reverse("guests:equip"),
        {"guest": str(guest.pk), "gear": str(gear.pk), "slot": GearSlot.WEAPON},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    gear.refresh_from_db()
    assert gear.guest_id == guest.id


@pytest.mark.django_db(transaction=True)
def test_dismiss_guest_view_invalidates_cached_gear_options_after_returning_equipment(game_data, django_user_model):
    cache.clear()
    manor, guest, client = bootstrap_guest_client(
        game_data,
        django_user_model,
        username="view_gear_options_dismiss_invalidation",
    )

    template = ItemTemplate.objects.create(
        key=f"view_gear_options_dismiss_invalidation_{manor.id}",
        name="辞退归还测试刀",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="green",
        effect_payload={},
        is_usable=True,
    )
    gear_template = GearTemplate.objects.create(
        key=template.key,
        name="辞退归还测试刀",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    initial = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert initial.status_code == 200
    assert initial.json()["options"] == []

    dismiss = client.post(reverse("guests:dismiss", args=[guest.pk]))

    assert dismiss.status_code == 302
    assert dismiss.url == reverse("guests:roster")

    refreshed = client.get(reverse("guests:gear_options"), {"slot": GearSlot.WEAPON})

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert len(payload["options"]) == 1
    assert payload["options"][0]["template_key"] == template.key
    assert payload["options"][0]["count"] == 1
