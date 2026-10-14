"""Check batch healing against a deliberately slow, one-bottle reference."""

from __future__ import annotations

import random
from fractions import Fraction
from time import perf_counter
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory import core as inventory_core
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services import health

pytestmark = pytest.mark.django_db


def guest(manor, *, hp=1, maximum=1_000, status=GuestStatus.IDLE):
    template = GuestTemplate.objects.create(
        key=f"healing_plan_guest_{uuid4().hex[:16]}",
        name="批量疗伤验证门客",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
        base_hp=maximum,
    )
    return Guest.objects.create(manor=manor, template=template, defense_stat=0, current_hp=hp, status=status)


def medicine(manor, *, hp=100, quantity=1, bound_quantity=0, template=None, payload=None):
    if template is None:
        template = ItemTemplate.objects.create(
            key=f"healing_plan_medicine_{uuid4().hex[:16]}",
            name="批量疗伤验证药品",
            effect_type=ItemTemplate.EffectType.MEDICINE,
            effect_payload={"hp": hp} if payload is None else payload,
        )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        bound_quantity=bound_quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


def bottle_reference(guests, items):
    """Use exactly one bottle per step, independently of the batch planner."""
    quantities = {item.pk: item.quantity for item in items}
    bound_quantities = {item.pk: item.bound_quantity for item in items}
    states = {row.pk: [row.current_hp, row.status] for row in guests}
    eligible = [
        row for row in guests if row.status in (GuestStatus.IDLE, GuestStatus.INJURED) and row.current_hp < row.max_hp
    ]
    priority = sorted(
        eligible,
        key=lambda row: (
            row.status != GuestStatus.INJURED,
            Fraction(row.current_hp, row.max_hp),
            -(row.max_hp - row.current_hp),
            row.pk,
        ),
    )
    valid = []
    for item in items:
        payload = item.template.effect_payload
        if not isinstance(payload, dict) or isinstance(payload.get("hp"), bool):
            continue
        try:
            dose = int(payload.get("hp"))
        except (TypeError, ValueError):
            continue
        if dose > 0:
            valid.append((item, dose))
    consumed = healed_hp = healed_count = partial_count = 0
    for row in priority:
        before = states[row.pk][0]
        while states[row.pk][0] < row.max_hp:
            available = [(item, dose) for item, dose in valid if quantities[item.pk] > 0]
            if not available:
                break
            missing = row.max_hp - states[row.pk][0]
            finishers = [(item, dose) for item, dose in available if dose >= missing]
            candidates = finishers or available
            item, dose = sorted(
                candidates,
                key=lambda pair: (
                    pair[1] if finishers else -pair[1],
                    pair[0].template.key,
                    pair[0].pk,
                ),
            )[0]
            quantities[item.pk] -= 1
            bound_quantities[item.pk] = max(0, bound_quantities[item.pk] - 1)
            restored = min(dose, missing)
            states[row.pk][0] += restored
            healed_hp += restored
            consumed += 1
            if states[row.pk][1] == GuestStatus.INJURED and states[row.pk][0] * 5 >= row.max_hp:
                states[row.pk][1] = GuestStatus.IDLE
        if states[row.pk][0] == row.max_hp:
            healed_count += 1
        elif states[row.pk][0] > before:
            partial_count += 1
    return (
        {
            "requested_count": len(eligible),
            "healed_count": healed_count,
            "partial_count": partial_count,
            "unhealed_count": len(eligible) - healed_count,
            "consumed_item_count": consumed,
            "healed_hp": healed_hp,
        },
        states,
        quantities,
        bound_quantities,
    )


def assert_matches_reference(manor, guests, items):
    expected, states, quantities, bound_quantities = bottle_reference(guests, items)
    assert health.heal_all_guests_with_medicine(manor) == expected
    for row in guests:
        row.refresh_from_db()
        assert [row.current_hp, row.status] == states[row.pk]
    remaining = dict(InventoryItem.objects.filter(pk__in=quantities).values_list("pk", "quantity"))
    assert {pk: remaining.get(pk, 0) for pk in quantities} == quantities
    remaining_bound = dict(InventoryItem.objects.filter(pk__in=quantities).values_list("pk", "bound_quantity"))
    assert {pk: remaining_bound.get(pk, 0) for pk in quantities} == bound_quantities


@pytest.mark.parametrize("seed", range(12))
def test_random_stock_and_guest_results_match_one_bottle_reference(manor_with_user, seed):
    manor, _ = manor_with_user
    rng = random.Random(seed)
    guests = [
        guest(
            manor,
            hp=rng.randrange(1, maximum),
            maximum=maximum,
            status=rng.choice([GuestStatus.INJURED, GuestStatus.IDLE]),
        )
        for maximum in [400, 700, 1_000, 400, 1_000]
    ]
    items = []
    for dose in [25, 25, 60, 125, 300]:
        quantity = rng.randrange(1, 9)
        items.append(medicine(manor, hp=dose, quantity=quantity, bound_quantity=rng.randrange(quantity + 1)))
    assert_matches_reference(manor, guests, items)


def test_reselects_small_finishing_bottle_after_large_dose_block(manor_with_user):
    manor, _ = manor_with_user
    row = guest(manor, hp=1, maximum=252)
    large = medicine(manor, hp=100, quantity=10)
    small = medicine(manor, hp=60, quantity=10)
    assert_matches_reference(manor, [row], [large, small])
    large.refresh_from_db()
    small.refresh_from_db()
    assert (large.quantity, small.quantity) == (8, 9)


def test_stock_shortage_prioritizes_injury_then_missing_fraction(manor_with_user):
    manor, _ = manor_with_user
    healthy = guest(manor, hp=100, maximum=1_000)
    injured = guest(manor, hp=100, maximum=200, status=GuestStatus.INJURED)
    less_hurt = guest(manor, hp=200, maximum=500)
    item = medicine(manor, hp=100, quantity=2)
    assert_matches_reference(manor, [less_hurt, healthy, injured], [item])
    assert injured.current_hp == 200
    assert healthy.current_hp == 200
    assert less_hurt.current_hp == 200


def test_equal_missing_fraction_prioritizes_larger_absolute_gap(manor_with_user):
    manor, _ = manor_with_user
    smaller = guest(manor, hp=100, maximum=500)
    larger = guest(manor, hp=200, maximum=1_000)
    item = medicine(manor, hp=100, quantity=1)
    assert_matches_reference(manor, [smaller, larger], [item])
    assert smaller.current_hp == 100
    assert larger.current_hp == 300


@pytest.mark.parametrize("bound_quantity", [2, 7, 10])
def test_bound_units_are_consumed_before_unbound_units_in_same_stack(manor_with_user, bound_quantity):
    manor, _ = manor_with_user
    row = guest(manor, hp=450)
    item = medicine(manor, hp=100, quantity=10, bound_quantity=bound_quantity)
    assert_matches_reference(manor, [row], [item])
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (4, max(0, bound_quantity - 6))


def test_invalid_medicine_payloads_do_not_block_valid_stock(manor_with_user):
    manor, _ = manor_with_user
    row = guest(manor, hp=100)
    invalid = [
        medicine(manor, quantity=7, payload=payload)
        for payload in [{"hp": True}, {"hp": 0}, {"hp": -4}, {"hp": "bad"}, {}, [100]]
    ]
    valid = medicine(manor, hp=100, quantity=4)
    assert_matches_reference(manor, [row], [*invalid, valid])
    assert row.current_hp == 500
    assert all(InventoryItem.objects.get(pk=item.pk).quantity == 7 for item in invalid)


def test_unavailable_guest_statuses_are_not_healed_or_counted(manor_with_user):
    manor, _ = manor_with_user
    guests = [
        guest(manor, hp=1, status=status)
        for status in [GuestStatus.WORKING, GuestStatus.DEPLOYED, GuestStatus.VACATION, GuestStatus.ARENA]
    ]
    eligible = guest(manor, hp=900)
    item = medicine(manor, hp=100, quantity=5)
    assert_matches_reference(manor, [*guests, eligible], [item])
    assert all(row.current_hp == 1 for row in guests)


def test_recoverable_stock_failure_replans_without_double_consumption(manor_with_user, monkeypatch):
    manor, _ = manor_with_user
    row = guest(manor, hp=840, maximum=1_000)
    large = medicine(manor, hp=100, quantity=1)
    unavailable = medicine(manor, hp=60, quantity=10)
    fallback = medicine(manor, hp=25, quantity=10)
    original = inventory_core.consume_inventory_item_locked
    failed = False

    def transient_failure(item, amount=1, **kwargs):
        nonlocal failed
        if item.pk == unavailable.pk:
            failed = True
            raise health.InsufficientStockError(item.template.name, amount, 0)
        return original(item, amount, **kwargs)

    monkeypatch.setattr(inventory_core, "consume_inventory_item_locked", transient_failure)
    result = health.heal_all_guests_with_medicine(manor)
    assert failed
    assert result["consumed_item_count"] == 4
    assert result["healed_hp"] == 160
    row.refresh_from_db()
    assert row.current_hp == 1_000
    for item in [unavailable, fallback]:
        item.refresh_from_db()
    assert not InventoryItem.objects.filter(pk=large.pk).exists()
    assert (unavailable.quantity, fallback.quantity) == (10, 7)


def test_sql_cost_does_not_grow_with_bottle_count(manor_with_user):
    manor, _ = manor_with_user
    query_counts = []
    for bottles in [1, 100, 10_000]:
        maximum = max(200, bottles + 1)
        row = guest(manor, hp=maximum - bottles, maximum=maximum)
        item = medicine(manor, hp=1, quantity=bottles)
        with CaptureQueriesContext(connection) as captured:
            result = health.heal_all_guests_with_medicine(manor)
        query_counts.append(len(captured))
        row.refresh_from_db()
        assert row.current_hp == row.max_hp
        assert not InventoryItem.objects.filter(pk=item.pk).exists()
        assert result["consumed_item_count"] == bottles
    assert max(query_counts) - min(query_counts) <= 2, query_counts
    assert max(query_counts) < 30, query_counts
    print(f"batch healing SQL count for 1/100/10000 bottles: {query_counts}")


@pytest.mark.parametrize("count", [20, 100])
def test_high_hp_roster_saves_once_per_guest_and_consumes_once_per_stack(manor_with_user, monkeypatch, count):
    manor, _ = manor_with_user
    guests = [guest(manor, hp=1, maximum=1_000_001) for _ in range(count)]
    item = medicine(manor, hp=1, quantity=count * 1_000_000 + 3)
    calls = {"heal": 0, "consume": 0}
    original_heal = health.heal_guest
    original_consume = inventory_core.consume_inventory_item_locked

    def heal(*args, **kwargs):
        calls["heal"] += 1
        return original_heal(*args, **kwargs)

    def consume(*args, **kwargs):
        calls["consume"] += 1
        return original_consume(*args, **kwargs)

    monkeypatch.setattr(health, "heal_guest", heal)
    monkeypatch.setattr(inventory_core, "consume_inventory_item_locked", consume)
    started = perf_counter()
    result = health.heal_all_guests_with_medicine(manor)
    elapsed = perf_counter() - started
    assert calls == {"heal": count, "consume": 1}
    assert result["healed_count"] == count
    assert result["consumed_item_count"] == count * 1_000_000
    assert (
        list(Guest.objects.filter(pk__in=[row.pk for row in guests]).values_list("current_hp", flat=True))
        == [1_000_001] * count
    )
    item.refresh_from_db()
    assert item.quantity == 3
    print(f"batch healing {count} guests / {count * 1_000_000} bottles: {elapsed:.4f}s, calls={calls}")


@pytest.mark.parametrize("failure", ["guest_save", "inventory_consume"])
def test_late_database_failure_rolls_back_all_guests_and_stock(manor_with_user, monkeypatch, failure):
    manor, _ = manor_with_user
    guests = [guest(manor, hp=101, maximum=201, status=GuestStatus.INJURED) for _ in range(2)]
    items = [medicine(manor, hp=100, quantity=1) for _ in range(2)]
    before_guests = list(Guest.objects.filter(pk__in=[row.pk for row in guests]).values())
    before_items = list(InventoryItem.objects.filter(pk__in=[item.pk for item in items]).values())
    calls = 0

    if failure == "guest_save":
        original = Guest.save

        def failing_save(instance, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseError("late guest write failed")
            return original(instance, *args, **kwargs)

        monkeypatch.setattr(Guest, "save", failing_save)
    else:
        original = inventory_core.consume_inventory_item_locked

        def failing_consume(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseError("late inventory write failed")
            return original(*args, **kwargs)

        monkeypatch.setattr(inventory_core, "consume_inventory_item_locked", failing_consume)
    with pytest.raises(DatabaseError, match="late .* write failed"):
        health.heal_all_guests_with_medicine(manor)
    assert calls == 2
    assert list(Guest.objects.filter(pk__in=[row.pk for row in guests]).values()) == before_guests
    assert list(InventoryItem.objects.filter(pk__in=[item.pk for item in items]).values()) == before_items
