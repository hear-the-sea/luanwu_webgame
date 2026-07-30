from __future__ import annotations

import random
from datetime import datetime
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GuestNotIdleError
from gameplay.models import Manor, ResourceEvent, ResourceType
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate, TrainingLog
from guests.services.training import apply_training_locked, quote_training


def _create_training_guest(manor: Manor, *, suffix: str) -> Guest:
    template = GuestTemplate.objects.create(
        key=f"locked_training_{suffix}",
        name=f"锁内培养门客 {suffix}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        growth_range=[2, 4],
        attribute_weights={
            "force": 4,
            "intellect": 3,
            "defense": 2,
            "agility": 1,
        },
    )
    return Guest.objects.create(
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


def _fund_manor(manor: Manor) -> datetime:
    fixed_now = timezone.now()
    manor.grain = 20_000
    manor.silver = 20_000
    manor.resource_updated_at = fixed_now
    manor.save(update_fields=["grain", "silver", "resource_updated_at"])
    return fixed_now


@pytest.mark.django_db(transaction=True)
def test_apply_training_locked_requires_an_outer_transaction(manor_factory):
    manor, _user = manor_factory(username="locked_training_requires_transaction")
    guest = _create_training_guest(manor, suffix="requires_transaction")

    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        apply_training_locked(manor, guest.pk, rng=random.Random(1))


@pytest.mark.django_db
def test_apply_training_locked_commits_resources_log_and_guest_together(manor_factory):
    manor, _user = manor_factory(username="locked_training_success")
    fixed_now = _fund_manor(manor)
    guest = _create_training_guest(manor, suffix="success")
    quote = quote_training(guest, levels=2)
    rng = random.Random(17)

    def fixed_allocator(_guest, levels, supplied_rng):
        assert supplied_rng is rng
        return {
            "force": levels,
            "intellect": levels * 2,
            "defense": 0,
            "agility": 0,
        }

    with patch("gameplay.services.resources.timezone.now", return_value=fixed_now):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            applied = apply_training_locked(
                locked_manor,
                guest.pk,
                levels=2,
                rng=rng,
                allocate_level_up_attributes_func=fixed_allocator,
            )

    guest.refresh_from_db()
    manor.refresh_from_db(fields=["grain", "silver"])
    assert applied.pk == guest.pk
    assert guest.level == 3
    assert guest.force == 82
    assert guest.intellect == 84
    assert guest.attribute_points == 2
    assert guest.training_target_level == 0
    assert guest.training_complete_at is None
    assert manor.grain == 20_000 - quote.resource_cost[ResourceType.GRAIN]
    assert manor.silver == 20_000 - quote.resource_cost[ResourceType.SILVER]

    log = TrainingLog.objects.get(manor=manor, guest=guest)
    assert log.delta_level == 2
    assert log.resource_cost == quote.resource_cost
    deltas = {
        event.resource_type: event.delta
        for event in ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.TRAINING_COST,
        )
    }
    assert deltas == {
        ResourceType.GRAIN: -quote.resource_cost[ResourceType.GRAIN],
        ResourceType.SILVER: -quote.resource_cost[ResourceType.SILVER],
    }


@pytest.mark.django_db
def test_apply_training_locked_skips_a_resolved_missing_grain_template_lookup(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="locked_training_missing_grain_template")
    fixed_now = _fund_manor(manor)
    guest = _create_training_guest(manor, suffix="missing_grain_template")
    quote = quote_training(guest)

    with patch(
        "gameplay.services.inventory.core.ItemTemplate.objects.filter",
        side_effect=AssertionError("unexpected grain template lookup"),
    ):
        with patch("gameplay.services.resources.timezone.now", return_value=fixed_now):
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                apply_training_locked(
                    locked_manor,
                    guest.pk,
                    rng=random.Random(41),
                    grain_template_resolved=True,
                )

    manor.refresh_from_db(fields=["grain", "silver"])
    guest.refresh_from_db(fields=["level"])
    assert guest.level == quote.target_level
    assert manor.grain == 20_000 - quote.resource_cost[ResourceType.GRAIN]
    assert manor.silver == 20_000 - quote.resource_cost[ResourceType.SILVER]
    assert TrainingLog.objects.filter(guest=guest).count() == 1


@pytest.mark.django_db
def test_apply_training_locked_rejects_ineligible_guest_before_writes(manor_factory):
    manor, _user = manor_factory(username="locked_training_ineligible")
    _fund_manor(manor)
    guest = _create_training_guest(manor, suffix="ineligible")
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])
    before_resources = (manor.grain, manor.silver)

    with pytest.raises(GuestNotIdleError):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            apply_training_locked(
                locked_manor,
                guest.pk,
                rng=random.Random(2),
            )

    manor.refresh_from_db(fields=["grain", "silver"])
    guest.refresh_from_db()
    assert (manor.grain, manor.silver) == before_resources
    assert guest.level == 1
    assert not TrainingLog.objects.filter(guest=guest).exists()
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.TRAINING_COST,
    ).exists()


@pytest.mark.django_db
def test_apply_training_locked_uses_injected_rng_deterministically(manor_factory):
    manor, _user = manor_factory(username="locked_training_deterministic")
    fixed_now = _fund_manor(manor)
    first = _create_training_guest(manor, suffix="deterministic_first")
    second = _create_training_guest(manor, suffix="deterministic_second")

    with patch("gameplay.services.resources.timezone.now", return_value=fixed_now):
        for guest in (first, second):
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                apply_training_locked(
                    locked_manor,
                    guest.pk,
                    levels=3,
                    rng=random.Random(20260728),
                )

    first.refresh_from_db()
    second.refresh_from_db()
    assert (
        first.level,
        first.force,
        first.intellect,
        first.defense_stat,
        first.agility,
        first.attribute_points,
    ) == (
        second.level,
        second.force,
        second.intellect,
        second.defense_stat,
        second.agility,
        second.attribute_points,
    )


@pytest.mark.django_db
def test_apply_training_locked_rolls_back_all_domain_writes(manor_factory):
    manor, _user = manor_factory(username="locked_training_rollback")
    fixed_now = _fund_manor(manor)
    guest = _create_training_guest(manor, suffix="rollback")
    before_guest = (
        guest.level,
        guest.force,
        guest.intellect,
        guest.defense_stat,
        guest.agility,
        guest.attribute_points,
        guest.current_hp,
    )

    with patch("gameplay.services.resources.timezone.now", return_value=fixed_now):
        with pytest.raises(RuntimeError, match="force rollback"):
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                apply_training_locked(
                    locked_manor,
                    guest.pk,
                    levels=2,
                    rng=random.Random(99),
                )
                raise RuntimeError("force rollback")

    guest.refresh_from_db()
    manor.refresh_from_db(fields=["grain", "silver"])
    assert (
        guest.level,
        guest.force,
        guest.intellect,
        guest.defense_stat,
        guest.agility,
        guest.attribute_points,
        guest.current_hp,
    ) == before_guest
    assert (manor.grain, manor.silver) == (20_000, 20_000)
    assert not TrainingLog.objects.filter(guest=guest).exists()
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.TRAINING_COST,
    ).exists()
