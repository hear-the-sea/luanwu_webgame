from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import JailError
from gameplay.models import JailPrisoner
from gameplay.services.jail import list_held_prisoners, recruit_prisoner
from gameplay.services.jail_expiration import JAIL_MAX_HOLD_DURATION
from gameplay.services.jail_persuasion.interactions import observe_prisoner

pytestmark = pytest.mark.django_db


def _expire(prisoner: JailPrisoner) -> None:
    captured_at = timezone.now() - JAIL_MAX_HOLD_DURATION
    JailPrisoner.objects.filter(pk=prisoner.pk).update(captured_at=captured_at)


def test_listing_releases_expired_prisoners_before_returning_them(persuasion_world):
    _expire(persuasion_world.prisoner)

    assert list_held_prisoners(persuasion_world.captor) == []
    persuasion_world.prisoner.refresh_from_db()
    assert persuasion_world.prisoner.status == JailPrisoner.Status.RELEASED


def test_observe_releases_expired_prisoner_and_rejects_action(persuasion_world):
    _expire(persuasion_world.prisoner)

    with pytest.raises(JailError, match="满30天"):
        observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.pk)

    persuasion_world.prisoner.refresh_from_db()
    assert persuasion_world.prisoner.status == JailPrisoner.Status.RELEASED


def test_recruit_releases_expired_prisoner_before_checking_recruitment(persuasion_world):
    _expire(persuasion_world.prisoner)

    with pytest.raises(JailError, match="满30天"):
        recruit_prisoner(persuasion_world.captor, persuasion_world.prisoner.pk)

    persuasion_world.prisoner.refresh_from_db()
    assert persuasion_world.prisoner.status == JailPrisoner.Status.RELEASED


def test_expiration_boundary_is_inclusive(persuasion_world):
    as_of = timezone.now()
    expired_at = as_of - JAIL_MAX_HOLD_DURATION
    fresh_at = expired_at + timedelta(hours=1)

    JailPrisoner.objects.filter(pk=persuasion_world.prisoner.pk).update(captured_at=expired_at)
    assert list_held_prisoners(persuasion_world.captor) == []

    second = JailPrisoner.objects.create(
        captor=persuasion_world.captor,
        original_manor=persuasion_world.original,
        guest_template=persuasion_world.prisoner_template,
        original_guest_name="边界未过期",
        original_level=1,
        loyalty=20,
        captured_loyalty=20,
    )
    JailPrisoner.objects.filter(pk=second.pk).update(captured_at=fresh_at)

    assert [prisoner.pk for prisoner in list_held_prisoners(persuasion_world.captor)] == [second.pk]
