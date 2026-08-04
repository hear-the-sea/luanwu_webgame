from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import (
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestOwnershipError,
    GuestTrainingInProgressError,
)
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import RecruitmentPool
from guests.services.recruitment import recruit_guest
from guests.services.recruitment_guests import finalize_candidate
from guests.services.training import ensure_auto_training, use_experience_item_for_guest


def _bootstrap_training_guest(game_data, django_user_model, *, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])

    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    guest = finalize_candidate(candidate)

    ensure_auto_training(guest)
    guest.refresh_from_db()
    guest.training_complete_at = timezone.now() + timedelta(seconds=600)
    guest.training_target_level = guest.level + 1
    guest.save(update_fields=["training_complete_at", "training_target_level"])

    return manor, guest


def _create_experience_item(manor, *, seconds: int = 120) -> InventoryItem:
    suffix = uuid.uuid4().hex[:10]
    template = ItemTemplate.objects.create(
        key=f"test_exp_item_{suffix}",
        name="训练加速符",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        is_usable=True,
        tradeable=False,
        effect_payload={"time": seconds},
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_use_experience_item_for_guest_is_atomic_success(game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_atomic_ok")
    item = _create_experience_item(manor, seconds=120)

    before_eta = guest.training_complete_at
    result = use_experience_item_for_guest(manor, guest, item.pk, 120)

    guest.refresh_from_db()
    assert before_eta is not None
    assert guest.training_complete_at is not None
    assert guest.training_complete_at < before_eta
    assert int(result["time_reduced"]) > 0
    assert int(result["remaining_item_quantity"]) == 0
    assert InventoryItem.objects.filter(pk=item.pk).exists() is False


@pytest.mark.django_db
def test_use_experience_item_for_guest_rolls_back_when_consume_fails(monkeypatch, game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_atomic_rollback")
    item = _create_experience_item(manor, seconds=180)

    before_eta = guest.training_complete_at

    def _boom(*_args, **_kwargs):
        raise RuntimeError("consume failed")

    monkeypatch.setattr("gameplay.services.inventory.core.consume_inventory_item_locked", _boom)

    with pytest.raises(RuntimeError, match="consume failed"):
        use_experience_item_for_guest(manor, guest, item.pk, 180)

    guest.refresh_from_db()
    item.refresh_from_db()

    assert guest.training_complete_at == before_eta
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_experience_item_for_guest_rejects_malformed_reduce_result(monkeypatch, game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_malformed_result")
    item = _create_experience_item(manor, seconds=120)

    def _malformed_reduce(_guest, _seconds):
        return {"time_reduced": "bad", "applied_levels": "bad", "next_eta": _guest.training_complete_at}

    monkeypatch.setattr("guests.services.training.reduce_training_time_for_guest", _malformed_reduce)

    with pytest.raises(AssertionError, match="invalid guest training reduction result time_reduced"):
        use_experience_item_for_guest(manor, guest, item.pk, 120)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert item.quantity == 1
    assert guest.training_complete_at is not None


@pytest.mark.django_db
def test_use_experience_item_for_guest_rejects_missing_item(game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_missing_item")

    with pytest.raises(GuestItemOwnershipError, match="道具不存在或不属于您的庄园"):
        use_experience_item_for_guest(manor, guest, 999999, 120)


@pytest.mark.django_db
def test_use_experience_item_for_guest_rejects_paused_training_without_consuming_item(
    game_data,
    django_user_model,
):
    manor, guest = _bootstrap_training_guest(
        game_data,
        django_user_model,
        username="exp_item_paused_training",
    )
    item = _create_experience_item(manor, seconds=120)
    guest.training_remaining_seconds = 600
    guest.training_complete_at = None
    guest.save(update_fields=["training_remaining_seconds", "training_complete_at"])

    with pytest.raises(GuestTrainingInProgressError, match="训练已暂停"):
        use_experience_item_for_guest(manor, guest, item.pk, 120)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.training_remaining_seconds == 600
    assert guest.training_complete_at is None
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_experience_item_for_guest_rejects_invalid_reduce_seconds(game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_invalid_seconds")
    item = _create_experience_item(manor, seconds=120)

    with pytest.raises(AssertionError, match="invalid guest training reduce_seconds"):
        use_experience_item_for_guest(manor, guest, item.pk, 0)


@pytest.mark.django_db
def test_use_experience_fruit_rejects_guest_level_50_or_above(game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_fruit_level_limit")
    guest.level = 50
    guest.save(update_fields=["level"])
    fruit_template = ItemTemplate.objects.create(
        key="experience_fruit",
        name="经验果",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        is_usable=False,
        effect_payload={"time": 60},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=fruit_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    before_eta = guest.training_complete_at

    with pytest.raises(GuestItemConfigurationError, match="50级以上门客不能使用经验果"):
        use_experience_item_for_guest(manor, guest, item.pk, 60)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.training_complete_at == before_eta
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_experience_item_for_guest_rejects_missing_guest_after_lock(game_data, django_user_model):
    manor, guest = _bootstrap_training_guest(game_data, django_user_model, username="exp_item_missing_guest")
    item = _create_experience_item(manor, seconds=120)
    guest.delete()

    with pytest.raises(GuestOwnershipError, match="门客不存在或不属于您的庄园"):
        use_experience_item_for_guest(manor, guest, item.pk, 120)
