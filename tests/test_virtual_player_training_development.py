from __future__ import annotations

import random
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import Manor
from gameplay.services.virtual_player_core.projection import (
    calculate_guest_arena_power,
    project_training_development_intent,
)
from gameplay.services.virtual_player_core.reference_snapshots import load_manor_strength_summary
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate, TrainingLog
from guests.services.training import apply_training_locked, project_training_completion


def _guest_arena_power(guest: Guest, *, force: int, intellect: int, defense: int, agility: int) -> int:
    return calculate_guest_arena_power(
        force=force,
        intellect=intellect,
        defense=defense,
        agility=agility,
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def test_guest_arena_power_includes_agility() -> None:
    common = {
        "force": 100,
        "intellect": 100,
        "defense": 100,
        "hp_bonus": 0,
        "archetype": "military",
        "base_hp": 1_000,
    }
    lower_agility = calculate_guest_arena_power(**common, agility=80)
    higher_agility = calculate_guest_arena_power(**common, agility=180)

    assert higher_agility - lower_agility == 100


@pytest.mark.django_db
def test_training_intent_prediction_matches_committed_strength(manor_factory):
    manor, _user = manor_factory(username="v2_training_intent_prediction")
    fixed_now = timezone.now()
    manor.grain = 20_000
    manor.silver = 20_000
    manor.resource_updated_at = fixed_now
    manor.save(update_fields=["grain", "silver", "resource_updated_at"])
    template = GuestTemplate.objects.create(
        key="v2_training_intent_guest",
        name="V2 培养预测门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        base_hp=1_250,
        growth_range=[3, 5],
        attribute_weights={
            "force": 4,
            "intellect": 3,
            "defense": 2,
            "agility": 1,
        },
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=1,
        force=80,
        intellect=80,
        defense_stat=80,
        agility=80,
        initial_force=80,
        initial_intellect=80,
        initial_defense=80,
        initial_agility=80,
    )
    seed = 2026072801
    strength_before = load_manor_strength_summary(manor_id=manor.id)

    completion = project_training_completion(
        guest,
        levels=2,
        rng=random.Random(seed),
    )
    guest.refresh_from_db()
    assert guest.level == 1
    assert not TrainingLog.objects.filter(guest=guest).exists()

    intent = project_training_development_intent(
        guest_id=guest.id,
        prestige_band="newbie",
        strength_before=strength_before,
        guest_level_after=completion.level,
        guest_arena_power_before=_guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
        ),
        guest_arena_power_after=_guest_arena_power(
            guest,
            force=completion.force,
            intellect=completion.intellect,
            defense=completion.defense_stat,
            agility=completion.agility,
        ),
        utility_score=1.0,
    )

    with patch("gameplay.services.resources.timezone.now", return_value=fixed_now):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            apply_training_locked(
                locked_manor,
                guest.pk,
                levels=2,
                rng=random.Random(seed),
            )

    strength_after = load_manor_strength_summary(manor_id=manor.id)
    assert intent.strength_before == strength_before
    assert intent.strength_after == strength_after
    assert intent.action_kind == "training"
    assert intent.business_key == f"training:guest:{guest.id}"
