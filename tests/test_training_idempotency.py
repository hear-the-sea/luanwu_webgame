from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.exceptions import GuestTrainingInProgressError
from gameplay.models import ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.training import finalize_guest_training, train_guest

User = get_user_model()


@pytest.mark.django_db
def test_train_guest_double_call_spends_once():
    user = User.objects.create_user(username="train_idempotency", password="pass123")
    manor = ensure_manor(user)
    resource_now = timezone.now()
    manor.grain = 10_000
    manor.silver = 10_000
    manor.resource_updated_at = resource_now
    manor.save(update_fields=["grain", "silver", "resource_updated_at"])

    template = GuestTemplate.objects.create(
        key="train_idempotency_tpl",
        name="训练幂等门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=1,
        force=80,
        intellect=80,
        defense_stat=80,
        agility=80,
    )

    manor.refresh_from_db()
    before_grain = manor.grain
    before_silver = manor.silver

    with patch("gameplay.services.resources.timezone.now", return_value=resource_now):
        train_guest(guest, levels=1)

        manor.refresh_from_db()
        after_first_grain = manor.grain
        after_first_silver = manor.silver

        with pytest.raises(GuestTrainingInProgressError):
            train_guest(guest, levels=1)

    manor.refresh_from_db()
    assert manor.grain == after_first_grain
    assert manor.silver == after_first_silver
    assert manor.grain == before_grain - 240
    assert manor.silver == before_silver - 50

    deltas = {
        event.resource_type: event.delta
        for event in ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.TRAINING_COST,
            note="培养 训练幂等门客",
        )
    }
    assert deltas == {ResourceType.GRAIN: -240, ResourceType.SILVER: -50}


@pytest.mark.django_db
def test_finalize_guest_training_is_idempotent():
    user = User.objects.create_user(username="finalize_idempotency", password="pass123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="finalize_idempotency_tpl",
        name="结算幂等门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=1,
        force=80,
        intellect=80,
        defense_stat=80,
        agility=80,
    )

    now = timezone.now()
    guest.training_target_level = 2
    guest.training_complete_at = now - timezone.timedelta(seconds=1)
    guest.save(update_fields=["training_target_level", "training_complete_at"])

    guest.refresh_from_db()
    before_level = guest.level
    before_points = guest.attribute_points

    assert finalize_guest_training(guest, now=now) is True

    guest.refresh_from_db()
    assert guest.level == before_level + 1
    assert guest.attribute_points == before_points + 1

    level_after = guest.level
    points_after = guest.attribute_points

    assert finalize_guest_training(guest, now=timezone.now()) is False

    guest.refresh_from_db()
    assert guest.level == level_after
    assert guest.attribute_points == points_after


def test_train_guest_rejects_unsaved_guest():
    template = GuestTemplate(
        key="train_unsaved_tpl",
        name="未保存训练门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest(
        template=template,
        level=1,
        force=80,
        intellect=80,
        defense_stat=80,
        agility=80,
    )

    with pytest.raises(AssertionError, match="requires a persisted guest"):
        train_guest(guest, levels=1)
