import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.training import finalize_guest_training

User = get_user_model()


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
