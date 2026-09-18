from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from tests.guest_item_view_validation.support import bootstrap_guest_client


@pytest.mark.django_db
def test_use_experience_item_view_rejects_invalid_item_id_ajax(game_data, django_user_model):
    _manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_exp_item_invalid")

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": "invalid"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "请选择经验道具" in payload["error"]


@pytest.mark.django_db
def test_use_experience_item_view_message_uses_shanghai_timezone(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_exp_item_timezone")
    template = ItemTemplate.objects.create(
        key=f"view_exp_item_timezone_{manor.id}",
        name="训练加速符",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 120},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    eta = datetime(2026, 9, 18, 1, 2, 3, tzinfo=UTC)
    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_args, **_kwargs: {
            "time_reduced": 120,
            "next_eta": eta,
            "new_level": guest.level,
            "current_hp": guest.current_hp,
            "max_hp": guest.max_hp,
            "remaining_item_quantity": 0,
        },
    )

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    assert "预计完成：09:02:03" in response.json()["message"]


@pytest.mark.django_db
def test_use_medicine_item_view_rejects_invalid_item_id_ajax(game_data, django_user_model):
    _manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_medicine_item_invalid")

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": "invalid"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "请选择药品道具" in payload["error"]


@pytest.mark.django_db
def test_use_medicine_item_view_ajax_success_returns_numeric_item_id(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_medicine_item_success")
    guest.current_hp = max(1, guest.max_hp - 100)
    guest.save(update_fields=["current_hp"])

    template = ItemTemplate.objects.create(
        key=f"view_medicine_item_success_{manor.id}",
        name="测试疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 80},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["item_id"] == item.pk
    assert isinstance(payload["item_id"], int)
    assert payload["new_quantity"] == 1


@pytest.mark.django_db
def test_use_experience_item_view_rejects_invalid_effect_payload_ajax(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_exp_item_bad_payload")
    template = ItemTemplate.objects.create(
        key=f"view_exp_item_bad_payload_{manor.id}",
        name="异常经验配置道具",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": None},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    called = {"count": 0}

    def _unexpected_use(*_args, **_kwargs):
        called["count"] += 1
        return {}

    monkeypatch.setattr("guests.views.training.use_experience_item_for_guest", _unexpected_use)

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "道具未配置有效时间" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_experience_item_view_rejects_boolean_time_effect_payload_ajax(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_exp_item_bool_payload")
    template = ItemTemplate.objects.create(
        key=f"view_exp_item_bool_payload_{manor.id}",
        name="布尔经验配置道具",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": True},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    called = {"count": 0}

    def _unexpected_use(*_args, **_kwargs):
        called["count"] += 1
        return {}

    monkeypatch.setattr("guests.views.training.use_experience_item_for_guest", _unexpected_use)

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "道具未配置有效时间" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_experience_item_view_rejects_non_mapping_effect_payload_ajax(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_exp_item_bad_payload_shape"
    )
    template = ItemTemplate.objects.create(
        key=f"view_exp_item_bad_payload_shape_{manor.id}",
        name="异常经验结构道具",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload=False,
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    called = {"count": 0}

    def _unexpected_use(*_args, **_kwargs):
        called["count"] += 1
        return {}

    monkeypatch.setattr("guests.views.training.use_experience_item_for_guest", _unexpected_use)

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "道具未配置有效时间" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_medicine_item_view_rejects_invalid_effect_payload_ajax(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_medicine_item_bad_payload"
    )
    template = ItemTemplate.objects.create(
        key=f"view_medicine_item_bad_payload_{manor.id}",
        name="异常药品配置道具",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": None},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    called = {"count": 0}

    def _unexpected_use(*_args, **_kwargs):
        called["count"] += 1
        return {}

    monkeypatch.setattr("guests.views.items.use_medicine_item_for_guest", _unexpected_use)

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "道具未配置有效恢复值" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_medicine_item_view_rejects_non_mapping_effect_payload_ajax(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_medicine_item_bad_payload_shape"
    )
    template = ItemTemplate.objects.create(
        key=f"view_medicine_item_bad_payload_shape_{manor.id}",
        name="异常药品结构道具",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload=False,
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    called = {"count": 0}

    def _unexpected_use(*_args, **_kwargs):
        called["count"] += 1
        return {}

    monkeypatch.setattr("guests.views.items.use_medicine_item_for_guest", _unexpected_use)

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "道具未配置有效恢复值" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_magnifying_glass_view_rejects_invalid_item_id_ajax(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="view_magnify_item_invalid", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_magnify_item_invalid", password="pass123")

    response = client.post(
        reverse("guests:use_magnifying_glass"),
        {"item_id": "invalid"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "未找到放大镜道具" in payload["error"]


@pytest.mark.django_db
def test_use_experience_item_view_unexpected_error_bubbles_up(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_exp_item_unexpected")
    template = ItemTemplate.objects.create(
        key=f"view_exp_item_unexpected_{manor.id}",
        name="异常经验道具",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:use_exp_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )


@pytest.mark.django_db
def test_use_medicine_item_view_unexpected_error_bubbles_up(game_data, django_user_model, monkeypatch):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_medicine_item_unexpected"
    )
    template = ItemTemplate.objects.create(
        key=f"view_medicine_item_unexpected_{manor.id}",
        name="异常药品道具",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:use_medicine_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )


@pytest.mark.django_db
def test_use_magnifying_glass_view_unexpected_error_bubbles_up(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_magnify_item_unexpected", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_magnify_item_unexpected", password="pass123")

    monkeypatch.setattr(
        "guests.views.recruit.use_magnifying_glass_for_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:use_magnifying_glass"),
            {"item_id": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
