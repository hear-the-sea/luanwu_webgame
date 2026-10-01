from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

import pytest
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import override_settings
from django.utils import timezone

from gameplay.models import InventoryItem, Manor, ResourceEvent
from gameplay.services import resources
from tests.gameplay_services.support import ensure_grain_template

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

WAIT_SECONDS = 5


def _guard_test_database():
    assert connection.vendor == "mysql"
    assert str(connection.settings_dict["NAME"]).startswith("test_"), "requires an isolated test_ database"


@pytest.fixture(autouse=True)
def _require_mysql():
    if connection.vendor != "mysql":
        pytest.skip("grain ledger concurrency requires real MySQL row locks")
    _guard_test_database()


@pytest.fixture
def production(monkeypatch):
    now = timezone.now()
    basis = resources.ResourceProductionBasis(
        hourly_rates=(("grain", 100.0),),
        personnel_grain_cost_per_hour=0,
    )
    monkeypatch.setattr(resources, "scale_value", lambda value: value)
    monkeypatch.setattr(
        resources,
        "load_resource_production_bases",
        lambda manors: {int(manor.pk): basis for manor in manors},
    )
    with override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=60):
        yield now, basis


def _event_total(manor):
    return ResourceEvent.objects.filter(manor=manor, resource_type="grain").aggregate(total=Sum("delta"))["total"] or 0


def _create_manor(django_user_model, now, *, quantity=100, missing_row=False):
    username = f"grain_reuse_{uuid.uuid4().hex[:12]}"
    # 绕过用户创建信号，显式控制庄园与缺失粮账的测试初始状态。
    django_user_model.objects.bulk_create([django_user_model(username=username)])
    user = django_user_model.objects.get(username=username)
    template = ensure_grain_template()
    manor = Manor.objects.create(
        user=user,
        grain=quantity,
        resource_updated_at=now - timezone.timedelta(hours=1),
    )
    assert manor.grain_capacity >= quantity + 200
    if not missing_row:
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": quantity},
        )
    else:
        assert not InventoryItem.objects.filter(manor=manor, template=template).exists()
    manor._initial_event_total = _event_total(manor)
    return manor, template


def _assert_balance(manor, template, *, quantity, event_delta):
    manor.refresh_from_db()
    rows = InventoryItem.objects.filter(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert list(rows.values_list("quantity", flat=True)) == [quantity]
    assert manor.grain == quantity
    assert _event_total(manor) - manor._initial_event_total == event_delta


def _spawn(action: Callable):
    results = []
    errors = []
    finished = threading.Event()

    def worker():
        connections.close_all()
        try:
            _guard_test_database()
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
            results.append(action())
        except BaseException as exc:  # pragma: no cover - asserted by each caller
            errors.append(exc)
        finally:
            connections.close_all()
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, results, errors, finished


def _join(worker):
    thread, results, errors, _finished = worker
    thread.join(timeout=WAIT_SECONDS)
    assert not thread.is_alive(), "database worker did not finish within 5 seconds"
    return results, errors


def _wait(event):
    assert event.wait(timeout=WAIT_SECONDS), "database worker did not reach synchronization point"


def _batch(manors, now):
    return resources.sync_resource_production_batch([manor.pk for manor in manors], now=now)


def _pause_batch_after_manor_locks(monkeypatch, basis):
    locked = threading.Event()
    release = threading.Event()

    def load_bases(manors):
        locked.set()
        _wait(release)
        return {int(manor.pk): basis for manor in manors}

    monkeypatch.setattr(resources, "load_resource_production_bases", load_bases)
    return locked, release


def _single_mutation(manor, *, delta, reached_lock=None):
    def trace_lock(execute, sql, params, many, context):
        if reached_lock is not None and "FOR UPDATE" in sql.upper() and Manor._meta.db_table in sql:
            reached_lock.set()
        return execute(sql, params, many, context)

    with connection.execute_wrapper(trace_lock), transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        if delta < 0:
            resources.spend_resources_locked(locked_manor, {"grain": -delta}, note="并发消费", sync_production=False)
            return None
        return resources.grant_resources_locked(locked_manor, {"grain": delta}, note="并发授粮", sync_production=False)


@pytest.mark.parametrize("delta", [-30, 25], ids=["spend", "grant"])
def test_batch_and_single_mutation_read_fresh_ledger_after_manor_lock(
    django_user_model, monkeypatch, production, delta
):
    now, basis = production
    manor, template = _create_manor(django_user_model, now)
    locked, release = _pause_batch_after_manor_locks(monkeypatch, basis)
    batch = _spawn(lambda: _batch([manor], now))
    reached_lock = threading.Event()
    mutation = None
    try:
        _wait(locked)
        mutation = _spawn(lambda: _single_mutation(manor, delta=delta, reached_lock=reached_lock))
        _wait(reached_lock)
        assert not mutation[3].is_set()
    finally:
        release.set()
    assert _join(batch) == ([1], [])
    assert mutation is not None
    mutation_results, errors = _join(mutation)
    assert errors == []
    assert mutation_results == ([None] if delta < 0 else [({"grain": delta}, {})])
    _assert_balance(manor, template, quantity=200 + delta, event_delta=100 + delta)
    assert manor.resource_updated_at == now
    assert (
        ResourceEvent.objects.filter(manor=manor, reason=ResourceEvent.Reason.PRODUCE, resource_type="grain").count()
        == 1
    )


def test_overlapping_batches_cannot_produce_twice(django_user_model, monkeypatch, production):
    now, basis = production
    manor, template = _create_manor(django_user_model, now)
    locked, release = _pause_batch_after_manor_locks(monkeypatch, basis)
    first = _spawn(lambda: _batch([manor], now))
    try:
        _wait(locked)
        second = _spawn(lambda: _batch([manor], now))
        assert _join(second) == ([0], [])
    finally:
        release.set()
    assert _join(first) == ([1], [])
    assert _batch([manor], now) == 0
    _assert_balance(manor, template, quantity=200, event_delta=100)
    assert manor.resource_updated_at == now


def test_skip_locked_batch_settles_other_manors(django_user_model, production):
    now, _basis = production
    blocked, template = _create_manor(django_user_model, now)
    available, _template = _create_manor(django_user_model, now)
    original_timestamp = blocked.resource_updated_at
    with transaction.atomic():
        Manor.objects.select_for_update().get(pk=blocked.pk)
        batch = _spawn(lambda: _batch([blocked, available], now))
        assert _join(batch) == ([1], [])
        _assert_balance(blocked, template, quantity=100, event_delta=0)
        assert blocked.resource_updated_at == original_timestamp
        _assert_balance(available, template, quantity=200, event_delta=100)
        assert available.resource_updated_at == now
    assert _batch([blocked, available], now) == 1
    _assert_balance(blocked, template, quantity=200, event_delta=100)
    _assert_balance(available, template, quantity=200, event_delta=100)


@pytest.mark.parametrize("initial", [0, 100], ids=["empty", "legacy-positive"])
def test_missing_grain_row_is_created_once_under_competing_grant(django_user_model, monkeypatch, production, initial):
    now, basis = production
    manor, template = _create_manor(django_user_model, now, quantity=initial, missing_row=True)
    locked, release = _pause_batch_after_manor_locks(monkeypatch, basis)
    batch = _spawn(lambda: _batch([manor], now))
    reached_lock = threading.Event()
    mutation = None
    try:
        _wait(locked)
        mutation = _spawn(lambda: _single_mutation(manor, delta=25, reached_lock=reached_lock))
        _wait(reached_lock)
        assert not mutation[3].is_set()
    finally:
        release.set()
    assert _join(batch) == ([1], [])
    assert mutation is not None
    assert _join(mutation) == ([({"grain": 25}, {})], [])
    _assert_balance(manor, template, quantity=initial + 125, event_delta=125)


def test_failed_chunk_rolls_back_before_waiting_grant_reads_ledger(django_user_model, monkeypatch, production):
    now, _basis = production
    first, template = _create_manor(django_user_model, now)
    second, _template = _create_manor(django_user_model, now)
    timestamps = {manor.pk: manor.resource_updated_at for manor in (first, second)}
    settled_first = threading.Event()
    release = threading.Event()
    original_sync = resources._sync_resource_production_locked

    def fail_second(manor, **kwargs):
        result = original_sync(manor, **kwargs)
        if manor.pk == first.pk:
            settled_first.set()
            _wait(release)
        elif manor.pk == second.pk:
            raise RuntimeError("injected second-manor failure after ledger and event writes")
        return result

    monkeypatch.setattr(resources, "_sync_resource_production_locked", fail_second)
    batch = _spawn(lambda: _batch([first, second], now))
    reached_lock = threading.Event()
    mutation = None
    try:
        _wait(settled_first)
        mutation = _spawn(lambda: _single_mutation(first, delta=25, reached_lock=reached_lock))
        _wait(reached_lock)
        assert not mutation[3].is_set()
    finally:
        release.set()
    results, errors = _join(batch)
    assert results == []
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert "second-manor failure" in str(errors[0])
    assert mutation is not None
    assert _join(mutation) == ([({"grain": 25}, {})], [])
    _assert_balance(first, template, quantity=125, event_delta=25)
    _assert_balance(second, template, quantity=100, event_delta=0)
    for manor in (first, second):
        assert manor.resource_updated_at == timestamps[manor.pk]
        assert not ResourceEvent.objects.filter(manor=manor, reason=ResourceEvent.Reason.PRODUCE).exists()
