from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import GameError, GuestItemOwnershipError, InvalidAllocationError
from gameplay.models import ItemTemplate
from tests.guest_view_error_boundaries.support import ajax_headers, create_guest, create_item, login_client, messages


@pytest.mark.django_db
def test_use_medicine_item_view_game_error_returns_business_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="medicine_game")
    guest = create_guest(manor, prefix="medicine_game")
    guest.current_hp = max(1, guest.max_hp - 50)
    guest.save(update_fields=["current_hp"])
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
        prefix="medicine_game",
    )

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(GameError("无法用药")),
    )

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        **ajax_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "无法用药"


@pytest.mark.django_db
def test_use_medicine_item_view_database_error_returns_generic_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="medicine_db")
    guest = create_guest(manor, prefix="medicine_db")
    guest.current_hp = max(1, guest.max_hp - 50)
    guest.save(update_fields=["current_hp"])
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
        prefix="medicine_db",
    )

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("guests:use_medicine_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        **ajax_headers(),
    )

    assert response.status_code == 500
    assert response.json()["error"] == "操作失败，请稍后重试"


@pytest.mark.django_db
def test_use_medicine_item_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="medicine_runtime")
    guest = create_guest(manor, prefix="medicine_runtime")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
        prefix="medicine_runtime",
    )

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:use_medicine_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_use_medicine_item_view_legacy_value_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="medicine_value_error")
    guest = create_guest(manor, prefix="medicine_value_error")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
        prefix="medicine_value_error",
    )

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("legacy medicine")),
    )

    with pytest.raises(ValueError, match="legacy medicine"):
        client.post(
            reverse("guests:use_medicine_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_use_medicine_item_view_malformed_result_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="medicine_bad_result")
    guest = create_guest(manor, prefix="medicine_bad_result")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
        prefix="medicine_bad_result",
    )

    monkeypatch.setattr(
        "guests.views.items.use_medicine_item_for_guest",
        lambda *_a, **_k: {
            "remaining_item_quantity": 0,
            "healed": "bad",
            "new_hp": guest.current_hp,
            "max_hp": guest.max_hp,
            "status": guest.status,
            "status_display": guest.get_status_display(),
        },
    )

    with pytest.raises(AssertionError, match="invalid medicine item view result healed"):
        client.post(
            reverse("guests:use_medicine_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_train_view_database_error_degrades_with_message(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="train_db")
    guest = create_guest(manor, prefix="train_db")

    monkeypatch.setattr(
        "guests.views.training.train_guest", lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down"))
    )

    response = client.post(reverse("guests:train"), {"guest": str(guest.pk), "levels": "1"})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert "操作失败，请稍后重试" in messages(response)


@pytest.mark.django_db
def test_train_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="train_runtime")
    guest = create_guest(manor, prefix="train_runtime")

    monkeypatch.setattr(
        "guests.views.training.train_guest", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(reverse("guests:train"), {"guest": str(guest.pk), "levels": "1"})


@pytest.mark.django_db
def test_train_view_value_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="train_value_error")
    guest = create_guest(manor, prefix="train_value_error")

    monkeypatch.setattr(
        "guests.views.training.train_guest", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("legacy train"))
    )

    with pytest.raises(ValueError, match="legacy train"):
        client.post(reverse("guests:train"), {"guest": str(guest.pk), "levels": "1"})


@pytest.mark.django_db
def test_train_view_success_message_uses_updated_eta(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="train_success_eta")
    guest = create_guest(manor, prefix="train_success_eta")
    expected_eta = timezone.now() + timedelta(minutes=5)
    updated_guest = guest
    updated_guest.training_complete_at = expected_eta

    monkeypatch.setattr("guests.views.training.train_guest", lambda *_a, **_k: updated_guest)

    response = client.post(reverse("guests:train"), {"guest": str(guest.pk), "levels": "1"})

    assert response.status_code == 302
    assert any(expected_eta.strftime("%H:%M:%S") in message for message in messages(response))


@pytest.mark.django_db
def test_check_training_view_returns_guest_status_as_payload(django_user_model):
    client, manor = login_client(django_user_model, prefix="training_check_success")
    guest = create_guest(manor, prefix="training_check_success")

    response = client.post(
        reverse("guests:check_training", args=[guest.pk]),
        **ajax_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["guest_id"] == guest.pk
    assert payload["guest_status"] == guest.status
    assert "status" not in payload


@pytest.mark.django_db
def test_use_experience_item_view_database_error_returns_generic_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="exp_db")
    guest = create_guest(manor, prefix="exp_db")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
        prefix="exp_db",
    )

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        **ajax_headers(),
    )

    assert response.status_code == 500
    assert response.json()["error"] == "操作失败，请稍后重试"


@pytest.mark.django_db
def test_use_experience_item_view_known_game_error_returns_business_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="exp_known")
    guest = create_guest(manor, prefix="exp_known")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
        prefix="exp_known",
    )

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(GuestItemOwnershipError()),
    )

    response = client.post(
        reverse("guests:use_exp_item", args=[guest.pk]),
        {"item_id": str(item.pk)},
        **ajax_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "道具不存在或不属于您的庄园"


@pytest.mark.django_db
def test_use_experience_item_view_value_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="exp_value_error")
    guest = create_guest(manor, prefix="exp_value_error")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
        prefix="exp_value_error",
    )

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("legacy exp")),
    )

    with pytest.raises(ValueError, match="legacy exp"):
        client.post(
            reverse("guests:use_exp_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_use_experience_item_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="exp_runtime")
    guest = create_guest(manor, prefix="exp_runtime")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
        prefix="exp_runtime",
    )

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:use_exp_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_use_experience_item_view_malformed_result_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="exp_bad_result")
    guest = create_guest(manor, prefix="exp_bad_result")
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
        prefix="exp_bad_result",
    )

    monkeypatch.setattr(
        "guests.views.training.use_experience_item_for_guest",
        lambda *_a, **_k: {
            "remaining_item_quantity": 0,
            "time_reduced": "bad",
            "next_eta": None,
            "new_level": guest.level,
            "current_hp": guest.current_hp,
            "max_hp": guest.max_hp,
        },
    )

    with pytest.raises(AssertionError, match="invalid experience item view result time_reduced"):
        client.post(
            reverse("guests:use_exp_item", args=[guest.pk]),
            {"item_id": str(item.pk)},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_allocate_points_view_known_game_error_returns_business_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="allocate_value")
    guest = create_guest(manor, prefix="allocate_value")

    monkeypatch.setattr(
        "guests.views.training.allocate_attribute_points",
        lambda *_a, **_k: (_ for _ in ()).throw(InvalidAllocationError("invalid_request", "加点失败")),
    )

    response = client.post(
        reverse("guests:allocate_points", args=[guest.pk]),
        {"guest": str(guest.pk), "attribute": "force", "points": "1"},
        **ajax_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "加点失败"


@pytest.mark.django_db
def test_allocate_points_view_value_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="allocate_value_error")
    guest = create_guest(manor, prefix="allocate_value_error")

    monkeypatch.setattr(
        "guests.views.training.allocate_attribute_points",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("加点失败")),
    )

    with pytest.raises(ValueError, match="加点失败"):
        client.post(
            reverse("guests:allocate_points", args=[guest.pk]),
            {"guest": str(guest.pk), "attribute": "force", "points": "1"},
            **ajax_headers(),
        )


@pytest.mark.django_db
def test_allocate_points_view_database_error_returns_generic_json(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="allocate_db")
    guest = create_guest(manor, prefix="allocate_db")

    monkeypatch.setattr(
        "guests.views.training.allocate_attribute_points",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("guests:allocate_points", args=[guest.pk]),
        {"guest": str(guest.pk), "attribute": "force", "points": "1"},
        **ajax_headers(),
    )

    assert response.status_code == 500
    assert response.json()["error"] == "操作失败，请稍后重试"


@pytest.mark.django_db
def test_allocate_points_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="allocate_runtime")
    guest = create_guest(manor, prefix="allocate_runtime")

    monkeypatch.setattr(
        "guests.views.training.allocate_attribute_points",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:allocate_points", args=[guest.pk]),
            {"guest": str(guest.pk), "attribute": "force", "points": "1"},
            **ajax_headers(),
        )
