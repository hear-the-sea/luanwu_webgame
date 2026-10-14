import pytest
from django.core.cache import cache
from django.db import connection

from gameplay.models import Message
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.messages import mark_messages_read, unread_message_count


@pytest.mark.django_db
def test_repeated_mark_read_does_not_rewrite_read_messages(manor_with_user):
    manor, _client = manor_with_user
    unread = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="待阅读")
    read = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="已阅读", is_read=True)
    unrelated = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="未选择")
    cache.set(CacheKeys.unread_count(manor.id), 2)
    affected_rows = []

    def record_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.lstrip().upper().startswith("UPDATE"):
            affected_rows.append(context["cursor"].rowcount)
        return result

    with connection.execute_wrapper(record_update):
        mark_messages_read(manor, [unread.pk, read.pk, unread.pk])
        mark_messages_read(manor, [unread.pk, read.pk])

    assert affected_rows == [1, 0]
    unread.refresh_from_db()
    unrelated.refresh_from_db()
    assert unread.is_read is True
    assert unrelated.is_read is False
    assert unread_message_count(manor) == Message.objects.filter(manor=manor, is_read=False).count()
