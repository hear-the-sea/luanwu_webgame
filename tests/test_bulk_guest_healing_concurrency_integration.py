from __future__ import annotations

import threading
from collections.abc import Callable
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection

from core.exceptions import GuestItemOwnershipError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate
from guests.services import health

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def _require_isolated_mysql():
    if connection.vendor != "mysql":
        pytest.skip("bulk healing concurrency requires MySQL row locks")
    assert str(connection.settings_dict["NAME"]).startswith("test_"), "isolated test database required"


def _create_patient(django_user_model, *, quantity: int):
    suffix = uuid4().hex[:16]
    user = django_user_model.objects.create_user(username=f"healing_race_{suffix}")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"healing_race_guest_{suffix}", name="并发疗伤门客", rarity=GuestRarity.GRAY, base_hp=1_000
    )
    guest = Guest.objects.create(
        manor=manor, template=template, defense_stat=0, current_hp=100, status=GuestStatus.INJURED
    )
    medicine = ItemTemplate.objects.create(
        key=f"healing_race_item_{suffix}",
        name="并发疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 300},
    )
    item = InventoryItem.objects.create(manor=manor, template=medicine, quantity=quantity)
    return manor, guest, item


def _run_concurrently(*operations: Callable[[], dict]):
    barrier = threading.Barrier(len(operations))
    results = []
    errors = []

    def worker(operation):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
            barrier.wait(timeout=10)
            results.append(operation())
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(operation,), daemon=True) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads), "concurrent healing did not finish"
    return results, errors


@pytest.mark.parametrize("quantity", [1, 3, 5])
def test_two_bulk_heals_preserve_hp_and_inventory(django_user_model, quantity):
    manor, guest, item = _create_patient(django_user_model, quantity=quantity)
    results, errors = _run_concurrently(
        lambda: health.heal_all_guests_with_medicine(Manor(pk=manor.pk)),
        lambda: health.heal_all_guests_with_medicine(Manor(pk=manor.pk)),
    )

    assert errors == []
    assert len(results) == 2
    used = min(quantity, 3)
    assert sum(result["consumed_item_count"] for result in results) == used
    assert sum(result["healed_hp"] for result in results) == used * 300
    guest.refresh_from_db()
    assert guest.current_hp == 100 + used * 300
    assert guest.status == GuestStatus.IDLE
    assert sum(InventoryItem.objects.filter(pk=item.pk).values_list("quantity", flat=True)) == quantity - used


def test_single_and_bulk_heal_cannot_spend_the_same_last_item(django_user_model):
    manor, guest, item = _create_patient(django_user_model, quantity=1)
    results, errors = _run_concurrently(
        lambda: health.heal_all_guests_with_medicine(Manor(pk=manor.pk)),
        lambda: health.use_medicine_item_for_guest(Manor(pk=manor.pk), Guest(pk=guest.pk), item.pk),
    )

    assert all(isinstance(error, GuestItemOwnershipError) for error in errors)
    assert len(results) + len(errors) == 2
    healed = sum(result.get("healed_hp", result.get("healed", 0)) for result in results)
    assert healed == 300
    guest.refresh_from_db()
    assert guest.current_hp == 400
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


def test_waiting_bulk_heal_sees_inventory_after_failed_transaction_rolls_back(django_user_model, monkeypatch):
    from gameplay.services.inventory import core as inventory_core

    manor, guest, item = _create_patient(django_user_model, quantity=3)
    item.quantity = 1
    item.save(update_fields=["quantity"])
    extra_template = ItemTemplate.objects.create(
        key=f"healing_race_extra_{uuid4().hex[:16]}",
        name="并发疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 300},
    )
    extra_item = InventoryItem.objects.create(manor=manor, template=extra_template, quantity=2)
    failing_transaction_ready = threading.Event()
    waiter_started = threading.Event()
    original_consume = inventory_core.consume_inventory_item_locked
    consume_calls = 0

    def consume_with_second_failure(*args, **kwargs):
        nonlocal consume_calls
        if threading.current_thread().name == "failing-bulk-heal":
            consume_calls += 1
            if consume_calls == 2:
                failing_transaction_ready.set()
                assert waiter_started.wait(timeout=10)
                raise RuntimeError("second medicine failed")
        return original_consume(*args, **kwargs)

    monkeypatch.setattr(inventory_core, "consume_inventory_item_locked", consume_with_second_failure)
    results = []
    errors = []

    def worker(*, fail):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
            if not fail:
                assert failing_transaction_ready.wait(timeout=10)
                waiter_started.set()
            results.append(health.heal_all_guests_with_medicine(Manor(pk=manor.pk)))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, kwargs={"fail": True}, name="failing-bulk-heal", daemon=True),
        threading.Thread(target=worker, kwargs={"fail": False}, name="waiting-bulk-heal", daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == 1 and str(errors[0]) == "second medicine failed"
    assert len(results) == 1
    assert results[0]["consumed_item_count"] == 3
    assert results[0]["healed_hp"] == 900
    guest.refresh_from_db()
    assert guest.current_hp == guest.max_hp == 1_000
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
    assert not InventoryItem.objects.filter(pk=extra_item.pk).exists()


def test_different_manors_sharing_templates_do_not_block_each_other(django_user_model, monkeypatch):
    from gameplay.services.inventory import core as inventory_core

    first, first_guest, first_item = _create_patient(django_user_model, quantity=1)
    second, second_guest, second_item = _create_patient(django_user_model, quantity=1)
    second_guest.template = first_guest.template
    second_guest.save(update_fields=["template"])
    second_item.template = first_item.template
    second_item.save(update_fields=["template"])
    first_holds_locks = threading.Event()
    second_finished = threading.Event()
    consume = inventory_core.consume_inventory_item_locked
    errors = []

    def hold_first_transaction(item, amount, **kwargs):
        if item.manor_id == first.pk:
            first_holds_locks.set()
            assert second_finished.wait(timeout=10), "different manor blocked by shared template lock"
        return consume(item, amount, **kwargs)

    monkeypatch.setattr(inventory_core, "consume_inventory_item_locked", hold_first_transaction)

    def run(manor, *, wait_for_first=False):
        close_old_connections()
        try:
            if wait_for_first:
                assert first_holds_locks.wait(timeout=10)
            health.heal_all_guests_with_medicine(Manor(pk=manor.pk))
        except Exception as exc:
            errors.append(exc)
        finally:
            if wait_for_first:
                second_finished.set()
            connection.close()

    threads = [
        threading.Thread(target=run, args=(first,), daemon=True),
        threading.Thread(target=run, args=(second,), kwargs={"wait_for_first": True}, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    first_guest.refresh_from_db()
    second_guest.refresh_from_db()
    assert first_guest.current_hp == second_guest.current_hp == 400
