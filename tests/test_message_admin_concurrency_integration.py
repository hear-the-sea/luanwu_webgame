from __future__ import annotations

import threading

import pytest
from django.core.cache import cache
from django.db import close_old_connections, connection, transaction

from gameplay.models import Message
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import set_message_queryset_read_state


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_admin_read_state_waits_for_row_lock_and_invalidates_redis_after_commit(manor_factory):
    if connection.vendor != "mysql":
        pytest.skip("requires MySQL row locks and an external Redis cache")

    manor, _user = manor_factory()
    message = Message.objects.create(manor=manor, title="后台已读并发测试")
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 1, timeout=60)
    locking_query_started = threading.Event()
    finished = threading.Event()
    results = []
    errors = []

    def observe_lock_query(execute, sql, params, many, context):
        if "FOR UPDATE" in sql.upper():
            locking_query_started.set()
        return execute(sql, params, many, context)

    def update_read_state():
        close_old_connections()
        try:
            with connection.execute_wrapper(observe_lock_query):
                results.append(set_message_queryset_read_state(Message.objects.filter(pk=message.pk), is_read=True))
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()
            finished.set()

    worker = threading.Thread(target=update_read_state, daemon=True)
    try:
        with transaction.atomic():
            Message.objects.select_for_update().get(pk=message.pk)
            worker.start()
            assert locking_query_started.wait(timeout=5)
            assert not finished.wait(timeout=0.2)
            assert cache.get(cache_key) == 1

        worker.join(timeout=10)
        assert not worker.is_alive()
        assert errors == []
        assert results == [1]
        message.refresh_from_db()
        assert message.is_read is True
        assert cache.get(cache_key) is None
    finally:
        if worker.ident is not None:
            worker.join(timeout=10)
        cache.delete(cache_key)
