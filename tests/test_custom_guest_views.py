"""玩家养成入口、二次确认与重复请求保护。"""

import pytest
from django.core.cache import cache
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.avatar import get_guest_disguise_avatar_filenames
from guests.models import CustomGuestProgress
from guests.services.custom_guest import create_custom_guest, ensure_custom_guest_card

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(client, django_user_model):
    user = django_user_model.objects.create_user(username="custom_view_owner")
    manor = ensure_manor(user)
    ensure_custom_guest_card(manor)
    client.force_login(user)
    cache.clear()
    return manor


@pytest.fixture
def custom(owner):
    card = owner.inventory_items.get(template__key="custom_guest_card")
    result = create_custom_guest(owner, card.pk, "初入春秋", get_guest_disguise_avatar_filenames()[0])
    return owner.guests.get(pk=result["guest_id"])


@pytest.fixture
def equipment(owner):
    template = ItemTemplate.objects.create(
        key="custom_view_affix",
        name="敏捷之长靴",
        rarity="purple",
        effect_type="equip_shoes",
        effect_payload={"equipment_affix": "agility", "equipment_base_key": "view_shoes"},
    )
    return InventoryItem.objects.create(manor=owner, template=template, quantity=5, bound_quantity=2)


def test_create_requires_second_confirmation(client, owner):
    card = owner.inventory_items.get(template__key="custom_guest_card")
    url = reverse("guests:custom_create", args=[card.pk])
    assert client.get(url).status_code == 200
    preview = client.post(url, {"name": "春秋肉身", "avatar_key": get_guest_disguise_avatar_filenames()[0]})
    assert preview.status_code == 200
    assert not owner.guests.filter(template__key="custom_guest_green").exists()
    token = preview.context["confirmation"]
    assert client.post(url, {"confirmation": token}).status_code == 302
    assert owner.guests.filter(template__key="custom_guest_green").count() == 1
    assert client.post(url, {"confirmation": token}).status_code in {302, 404}
    assert owner.guests.filter(template__key="custom_guest_green").count() == 1


def test_swallow_preview_exact_gain_and_once(client, owner, custom, equipment):
    url = reverse("guests:custom_consume", args=[custom.pk])
    preview = client.post(url, {"equipment": f"{equipment.pk}:unbound", "quantity": 2})
    assert preview.status_code == 200
    assert "敏捷权重 +100" in preview.context["preview"]["detail"]
    equipment.refresh_from_db()
    assert equipment.quantity == 5
    token = preview.context["confirmation"]
    client.post(url, {"confirmation": token})
    client.post(url, {"confirmation": token})
    equipment.refresh_from_db()
    assert equipment.quantity == 3
    assert equipment.bound_quantity == 2
    assert CustomGuestProgress.objects.get(manor=owner).weights["agility"] == 600


def test_popup_preview_returns_signed_token_without_consuming(client, owner, custom, equipment):
    url = reverse("guests:custom_consume", args=[custom.pk])
    preview = client.post(
        url,
        {"equipment": f"{equipment.pk}:unbound", "quantity": 2},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert preview.status_code == 200
    data = preview.json()
    assert data["success"]
    assert "敏捷权重 +100" in data["preview"]["detail"]
    equipment.refresh_from_db()
    assert equipment.quantity == 5
    assert CustomGuestProgress.objects.get(manor=owner).weights["agility"] == 500
    client.post(url, {"confirmation": data["confirmation"]})
    client.post(url, {"confirmation": data["confirmation"]})
    equipment.refresh_from_db()
    assert equipment.quantity == 3


def test_popup_invalid_quantity_returns_json_without_consuming(client, owner, custom, equipment):
    preview = client.post(
        reverse("guests:custom_consume", args=[custom.pk]),
        {"equipment": f"{equipment.pk}:unbound", "quantity": 999},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert preview.status_code == 400
    assert not preview.json()["success"]
    equipment.refresh_from_db()
    assert equipment.quantity == 5


def test_stale_inventory_confirmation_does_not_consume(client, owner, custom, equipment):
    url = reverse("guests:custom_consume", args=[custom.pk])
    preview = client.post(url, {"equipment": f"{equipment.pk}:unbound", "quantity": 1})
    equipment.quantity = 4
    equipment.save()
    client.post(url, {"confirmation": preview.context["confirmation"]})
    equipment.refresh_from_db()
    assert equipment.quantity == 4
    assert CustomGuestProgress.objects.get(manor=owner).weights["agility"] == 500


def test_token_cannot_move_to_another_action(client, owner, custom, equipment):
    preview = client.post(
        reverse("guests:custom_consume", args=[custom.pk]),
        {
            "equipment": f"{equipment.pk}:unbound",
            "quantity": 1,
        },
    )
    client.post(
        reverse("guests:custom_reset_weights", args=[custom.pk]),
        {
            "confirmation": preview.context["confirmation"],
        },
    )
    equipment.refresh_from_db()
    assert equipment.quantity == 5


def test_detail_panel_and_avatar_form(client, custom, equipment):
    response = client.get(reverse("guests:detail", args=[custom.pk]))
    assert response.status_code == 200
    assert response.context["custom_guest"]["total_weight"] == 2000
    rows = response.context["custom_guest"]["equipment_rows"]
    assert [(row["mode"], row["quantity"]) for row in rows if row["item"].pk == equipment.pk] == [
        ("bound", 2),
        ("unbound", 3),
    ]
    assert b"custom-cultivation-title" in response.content
    response = client.get(reverse("guests:custom_appearance", args=[custom.pk]))
    assert response.status_code == 200
    assert response.context["selected_avatar"]


def test_foreign_guest_and_inventory_inaccessible(client, owner, custom, django_user_model):
    other = django_user_model.objects.create_user(username="custom_other_view")
    client.force_login(other)
    assert client.get(reverse("guests:custom_appearance", args=[custom.pk])).status_code == 404
    assert client.post(reverse("guests:custom_advance", args=[custom.pk])).status_code == 404


def test_invalid_quantity_and_unsigned_confirmation_preserve_equipment(client, owner, custom, equipment):
    url = reverse("guests:custom_consume", args=[custom.pk])
    for quantity in ["0", "-1", "no", "999"]:
        assert client.post(url, {"equipment": f"{equipment.pk}:bound", "quantity": quantity}).status_code == 302
    assert client.post(url, {"confirmation": "forged"}).status_code == 302
    equipment.refresh_from_db()
    assert equipment.quantity == 5


def test_advance_preview_explains_reset_and_does_not_consume(client, owner, custom):
    progress = custom.custom_progress
    progress.weights = {"force": 3500, "intellect": 500, "defense": 500, "agility": 500}
    progress.save()
    material = InventoryItem.objects.create(
        manor=owner, template=ItemTemplate.objects.get(key="yijinjing"), quantity=10
    )
    preview = client.post(reverse("guests:custom_advance", args=[custom.pk]))
    assert preview.status_code == 200
    assert "回到1级" in preview.context["preview"]["detail"]
    assert "技能" in preview.context["preview"]["detail"]
    material.refresh_from_db()
    assert material.quantity == 10


def test_reset_preview_does_not_consume_and_confirm_preserves_rarity(client, owner, custom):
    progress = custom.custom_progress
    progress.weights = {"force": 550, "intellect": 500, "defense": 500, "agility": 500}
    progress.save()
    fruit = InventoryItem.objects.create(
        manor=owner, template=ItemTemplate.objects.get(key="millennium_zhuguo"), quantity=2
    )
    url = reverse("guests:custom_reset_weights", args=[custom.pk])
    preview = client.post(url)
    fruit.refresh_from_db()
    assert fruit.quantity == 2
    client.post(url, {"confirmation": preview.context["confirmation"]})
    fruit.refresh_from_db()
    custom.refresh_from_db()
    assert fruit.quantity == 1
    assert custom.rarity == "green"
    assert CustomGuestProgress.objects.get(manor=owner).weights["force"] == 500


def test_confirmation_cache_outage_does_not_consume(client, owner, custom, equipment, monkeypatch):
    url = reverse("guests:custom_consume", args=[custom.pk])
    preview = client.post(url, {"equipment": f"{equipment.pk}:unbound", "quantity": 1})

    def unavailable(*args, **kwargs):
        raise ConnectionError("cache unavailable")

    monkeypatch.setattr("guests.views.custom_guest.cache.add", unavailable)
    assert client.post(url, {"confirmation": preview.context["confirmation"]}).status_code == 302
    equipment.refresh_from_db()
    assert equipment.quantity == 5
    assert CustomGuestProgress.objects.get(manor=owner).weights["agility"] == 500


def test_orange_confirmation_keeps_level_and_reports_new_growth(client, owner, custom):
    from guests.models import GuestTemplate

    custom.template = GuestTemplate.objects.get(key="custom_guest_orange")
    custom.level = 80
    custom.save()
    InventoryItem.objects.create(manor=owner, template=ItemTemplate.objects.get(key="yijinjing"), quantity=50)
    progress = custom.custom_progress
    progress.failed_attempts = 9
    progress.save()
    url = reverse("guests:custom_advance", args=[custom.pk])
    preview = client.post(url)
    assert "等级与现有属性保留" in preview.context["preview"]["detail"]
    client.post(url, {"confirmation": preview.context["confirmation"]})
    custom.refresh_from_db()
    assert custom.level == 80
    assert custom.custom_progress.enhancement_level == 1
