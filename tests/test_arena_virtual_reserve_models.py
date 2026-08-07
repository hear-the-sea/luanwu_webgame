from __future__ import annotations

from datetime import timedelta
from itertools import count

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEvent,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotPopulationControl,
    BotProfile,
)
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User

_PROFILE_COUNTER = count(1)


def _create_profile() -> BotProfile:
    suffix = next(_PROFILE_COUNTER)
    manor = ensure_manor(
        User.objects.create_user(
            username=f"arena_reserve_model_{suffix}",
            password="pass123",
        )
    )
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=suffix,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


@pytest.mark.django_db
def test_arena_virtual_demand_requires_exactly_one_event():
    empty = ArenaVirtualDemand(status=ArenaVirtualDemand.Status.ACTIVE)
    with pytest.raises(ValidationError):
        empty.full_clean()

    tournament = ArenaTournament.objects.create()
    coop = ArenaCoopEvent.objects.create()
    both = ArenaVirtualDemand(tournament=tournament, coop_event=coop)
    with pytest.raises(ValidationError):
        both.full_clean()


@pytest.mark.django_db
def test_arena_virtual_demand_warm_target_cannot_exceed_replacement_target():
    tournament = ArenaTournament.objects.create()
    demand = ArenaVirtualDemand(
        tournament=tournament,
        reserve_target_count=2,
        warm_target_count=3,
    )

    with pytest.raises(ValidationError):
        demand.full_clean()


@pytest.mark.django_db
def test_bot_profile_arena_history_defaults_to_unused():
    profile = _create_profile()

    assert profile.last_arena_participated_at is None
    assert profile.arena_participation_count == 0


@pytest.mark.django_db
def test_population_control_uses_one_global_primary_key():
    control, created = BotPopulationControl.objects.get_or_create()

    assert control.pk == BotPopulationControl.GLOBAL_KEY
    assert BotPopulationControl.objects.count() == 1
    assert created in {True, False}


@pytest.mark.django_db
def test_reserve_member_profile_is_globally_unique_with_training_defaults():
    tournament = ArenaTournament.objects.create()
    coop = ArenaCoopEvent.objects.create()
    first = ArenaVirtualDemand.objects.create(tournament=tournament)
    second = ArenaVirtualDemand.objects.create(coop_event=coop)
    profile = _create_profile()
    member = ArenaVirtualReserveMember.objects.create(demand=first, profile=profile)

    assert member.state == ArenaVirtualReserveMember.State.TRAINING
    assert member.evaluated_version == 1
    assert member.accelerated_growth_rounds == 0

    with pytest.raises(IntegrityError), transaction.atomic():
        ArenaVirtualReserveMember.objects.create(demand=second, profile=profile)
