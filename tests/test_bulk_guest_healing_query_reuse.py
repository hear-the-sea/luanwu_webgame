from __future__ import annotations

from datetime import timedelta
from statistics import median
from time import perf_counter
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.db.models import DateTimeField, Value
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.exceptions import InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from guests.models import Guest, GuestStatus
from guests.services import health
from tests.test_bulk_guest_healing import _guest_template, _medicine


def _snapshot(manor):
    return {
        "guests": list(Guest.objects.filter(manor=manor).order_by("id").values()),
        "items": list(InventoryItem.objects.filter(manor=manor).order_by("id").values()),
    }


def _freeze_healing_clock(monkeypatch, now):
    monkeypatch.setattr(health.timezone, "now", lambda: now)
    monkeypatch.setattr(health.inventory_core, "Now", lambda: Value(now, output_field=DateTimeField()))


def _run_with_reloaded_objects(manor, apply_locked):
    """重现优化前每瓶重新查询门客和库存的执行方式。"""

    def reload_objects(locked_manor, guest, item, **kwargs):
        reloaded_item = health._load_locked_medicine_item(locked_manor, item.pk)
        reloaded_guest = (
            Guest.objects.select_for_update().select_related("template").get(pk=guest.pk, manor=locked_manor)
        )
        return apply_locked(locked_manor, reloaded_guest, reloaded_item, **kwargs)

    with patch.object(health, "_apply_medicine_to_locked_objects", reload_objects):
        return health.heal_all_guests_with_medicine(manor)


@pytest.mark.django_db
@pytest.mark.parametrize("bottle_count", [1, 10, 100])
def test_bulk_healing_reuses_locked_rows_with_identical_results_and_fewer_queries(
    manor_with_user, monkeypatch, bottle_count
):
    manor, _client = manor_with_user
    now = timezone.now()
    _freeze_healing_clock(monkeypatch, now)
    template = _guest_template()
    Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.IDLE,
        current_hp=1,
    )
    _medicine(manor, heal_amount=1_000 // bottle_count, quantity=bottle_count + 1)
    apply_locked = health._apply_medicine_to_locked_objects

    with transaction.atomic():
        with CaptureQueriesContext(connection) as baseline_queries:
            expected = _run_with_reloaded_objects(manor, apply_locked)
        expected_state = _snapshot(manor)
        transaction.set_rollback(True)

    with CaptureQueriesContext(connection) as optimized_queries:
        actual = health.heal_all_guests_with_medicine(manor)

    assert actual == expected
    assert _snapshot(manor) == expected_state
    assert actual["consumed_item_count"] == bottle_count
    baseline_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in baseline_queries)
    optimized_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in optimized_queries)
    assert baseline_selects - optimized_selects == 2 * bottle_count
    assert optimized_selects == 3
    timings: dict[str, list[float]] = {"baseline": [], "optimized": []}
    for _ in range(5):
        for mode in timings:
            with transaction.atomic():
                # 两条路径从完全相同的已经治疗完毕的状态开始，重置为受伤并补足库存。
                Guest.objects.filter(manor=manor).update(current_hp=1)
                InventoryItem.objects.filter(
                    manor=manor, template__effect_type=ItemTemplate.EffectType.MEDICINE
                ).update(quantity=bottle_count + 1)
                started = perf_counter()
                if mode == "baseline":
                    _run_with_reloaded_objects(manor, apply_locked)
                else:
                    health.heal_all_guests_with_medicine(manor)
                timings[mode].append((perf_counter() - started) * 1_000)
                transaction.set_rollback(True)
    print(
        f"bulk_healing_query_reuse vendor={connection.vendor} bottles={bottle_count} "
        f"baseline_sql={len(baseline_queries)} optimized_sql={len(optimized_queries)} "
        f"baseline_selects={baseline_selects} optimized_selects={optimized_selects} "
        f"baseline_median_ms={median(timings['baseline']):.3f} "
        f"optimized_median_ms={median(timings['optimized']):.3f} samples=5"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("medicine_quantity", [1, 5])
def test_bulk_healing_preserves_priority_loyalty_training_and_medicine_selection(
    manor_with_user, monkeypatch, medicine_quantity
):
    manor, _client = manor_with_user
    now = timezone.now()
    _freeze_healing_clock(monkeypatch, now)
    template = _guest_template()
    Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.IDLE,
        current_hp=700,
    )
    injured = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=1,
        loyalty=80,
        injury_loyalty_processed_at=now - timedelta(hours=6),
        training_remaining_seconds=90,
    )
    _medicine(manor, heal_amount=150, quantity=medicine_quantity)
    _medicine(manor, heal_amount=500, quantity=medicine_quantity)
    invalid = _medicine(manor, heal_amount=100, quantity=1)
    ItemTemplate.objects.filter(pk=invalid.template_id).update(effect_payload={"hp": 0})
    apply_locked = health._apply_medicine_to_locked_objects
    queued: list[tuple[int, int, str, object]] = []

    def record_training(guest, *, countdown, source):
        queued.append((guest.pk, countdown, source, guest.training_complete_at))

    monkeypatch.setattr("guests.services.training._try_enqueue_complete_guest_training", record_training)

    with transaction.atomic():
        callback_start = len(connection.run_on_commit)
        with CaptureQueriesContext(connection) as baseline_queries:
            expected = _run_with_reloaded_objects(manor, apply_locked)
        expected_state = _snapshot(manor)
        for _savepoints, callback, _robust in connection.run_on_commit[callback_start:]:
            callback()
        expected_queued = list(queued)
        transaction.set_rollback(True)

    queued.clear()
    callback_start = len(connection.run_on_commit)
    with CaptureQueriesContext(connection) as optimized_queries:
        actual = health.heal_all_guests_with_medicine(manor)
    for _savepoints, callback, _robust in connection.run_on_commit[callback_start:]:
        callback()

    assert actual == expected
    assert _snapshot(manor) == expected_state
    assert queued == expected_queued
    assert len(queued) == 1
    injured.refresh_from_db()
    assert injured.loyalty == 78
    assert injured.status == GuestStatus.IDLE
    assert injured.training_remaining_seconds is None
    assert injured.training_complete_at == now + timedelta(seconds=90)
    assert len(baseline_queries) - len(optimized_queries) == 2 * actual["consumed_item_count"]


@pytest.mark.django_db
def test_bulk_healing_discards_mutated_objects_and_training_callback_after_savepoint_failure(
    manor_with_user, monkeypatch, django_capture_on_commit_callbacks
):
    manor, _client = manor_with_user
    now = timezone.now()
    monkeypatch.setattr(health.timezone, "now", lambda: now)
    guest = Guest.objects.create(
        manor=manor,
        template=_guest_template(),
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
        loyalty=80,
        injury_loyalty_processed_at=now - timedelta(hours=6),
        training_remaining_seconds=90,
    )
    failed_item = _medicine(manor, heal_amount=900, quantity=1)
    _medicine(manor, heal_amount=500, quantity=2)
    consume = health.inventory_core.consume_inventory_item_locked

    def fail_after_consumption(item, amount):
        item_id = item.pk
        consume(item, amount)
        if item_id == failed_item.pk:
            raise InsufficientStockError("疗伤药", 1, 0)

    monkeypatch.setattr(health.inventory_core, "consume_inventory_item_locked", fail_after_consumption)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = health.heal_all_guests_with_medicine(manor)

    guest.refresh_from_db()
    failed_item.refresh_from_db()
    assert guest.current_hp == guest.max_hp
    assert guest.loyalty == 78
    assert guest.status == GuestStatus.IDLE
    assert guest.training_complete_at == now + timedelta(seconds=90)
    assert guest.training_remaining_seconds is None
    assert failed_item.quantity == 1
    assert result["consumed_item_count"] == 2
    assert result["healed_hp"] == 900
    assert len(callbacks) == 1


@pytest.mark.django_db
def test_bulk_healing_rolls_back_previous_success_when_later_consumption_fails(manor_with_user, monkeypatch):
    manor, _client = manor_with_user
    guest = Guest.objects.create(
        manor=manor,
        template=_guest_template(),
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
        training_remaining_seconds=90,
    )
    _medicine(manor, heal_amount=200, quantity=5)
    before = _snapshot(manor)
    consume = health.inventory_core.consume_inventory_item_locked
    calls = 0

    def fail_second_consumption(item, amount):
        nonlocal calls
        calls += 1
        consume(item, amount)
        if calls == 2:
            raise RuntimeError("second consumption failed")

    monkeypatch.setattr(health.inventory_core, "consume_inventory_item_locked", fail_second_consumption)
    with pytest.raises(RuntimeError, match="second consumption failed"):
        health.heal_all_guests_with_medicine(manor)

    assert _snapshot(manor) == before
    guest.refresh_from_db()
    assert guest.training_remaining_seconds == 90
