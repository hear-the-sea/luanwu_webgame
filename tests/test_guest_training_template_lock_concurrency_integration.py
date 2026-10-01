from __future__ import annotations

import threading
from collections import Counter
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from guests.models import Guest, GuestRarity, GuestTemplate
from guests.services.training import ensure_auto_training_batch
from guests.utils.training_calculator import get_training_duration

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


def _guard_test_database():
    assert connection.vendor == "mysql"
    assert str(connection.settings_dict["NAME"]).startswith("test_"), "isolated test database required"


@pytest.fixture(autouse=True)
def _require_isolated_mysql():
    if connection.vendor != "mysql":
        pytest.skip("training template lock concurrency requires MySQL row locks")
    _guard_test_database()


def test_auto_training_batch_does_not_lock_shared_template(monkeypatch, manor_factory):
    suffix = uuid4().hex[:12]
    manor, _user = manor_factory(username=f"training_self_lock_{suffix}")
    template = GuestTemplate.objects.create(
        key=f"training_self_lock_{suffix}", name="共享模板训练门客", rarity=GuestRarity.GRAY
    )
    guests = [Guest.objects.create(manor=manor, template=template, level=level) for level in (1, 3)]
    now = timezone.now()
    finished = threading.Event()
    results = []
    errors = []
    dispatched = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda guest, **kwargs: dispatched.append(guest.pk),
    )

    def worker():
        connections.close_all()
        try:
            _guard_test_database()
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
            # 传入旧对象，服务必须重新锁定当前门客状态。
            requested = [Guest(pk=guest.pk) for guest in reversed(guests)]
            results.append(ensure_auto_training_batch(requested, now=now))
            results.append(ensure_auto_training_batch(requested, now=now + timedelta(hours=1)))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            connections.close_all()
            finished.set()

    thread = threading.Thread(target=worker, name="training-with-template-locked", daemon=True)
    completed_while_template_locked = False
    try:
        with transaction.atomic():
            GuestTemplate.objects.select_for_update().get(pk=template.pk)
            thread.start()
            # 模板锁仍被持有时，真实训练事务及其提交回调应已完成。
            completed_while_template_locked = finished.wait(timeout=5)
    finally:
        if thread.ident is not None:
            thread.join(timeout=10)

    assert not thread.is_alive(), "training worker did not finish"
    assert completed_while_template_locked, "training waited for an unrelated shared template lock"
    assert errors == []
    assert results == [2, 0]
    assert Counter(dispatched) == Counter(guest.pk for guest in guests)
    for guest in guests:
        guest.refresh_from_db()
        assert guest.training_target_level == guest.level + 1
        assert guest.training_complete_at == now + timedelta(seconds=get_training_duration(guest))
