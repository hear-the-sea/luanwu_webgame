from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from gameplay.models import Message
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.cache import CacheKeys


def _make_old(message: Message) -> None:
    Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=30))


@pytest.mark.django_db
def test_cleanup_old_data_command_reports_all_protected_rows_and_keeps_batch_semantics(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_command_user", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected reward",
        attachments={"items": {"reward_item": 1}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed reward",
        attachments={"items": {"reward_item": 1}},
        is_claimed=True,
    )
    metadata_only = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="metadata only",
        attachments={"metadata": {"source": "legacy"}},
    )
    for message in (protected, claimed, metadata_only):
        _make_old(message)
    stdout = StringIO()
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)

    try:
        call_command(
            "cleanup_old_data",
            model="gameplay.Message",
            days=7,
            batch_size=1,
            stdout=stdout,
        )

        assert Message.objects.filter(pk=protected.pk).exists() is True
        assert Message.objects.filter(pk__in=[claimed.pk, metadata_only.pk]).exists() is False
        assert cache.get(cache_key) is None
        output = stdout.getvalue()
        assert "已删除 2 条" in output
        assert "保留 1 条未领取附件消息" in output
        assert "清理完成：共删除 2 条记录" in output
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
def test_cleanup_old_data_command_dry_run_reports_all_protected_rows(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_command_dry_run_user", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected reward",
        attachments={"resources": {"silver": 10}},
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain message")
    _make_old(protected)
    _make_old(plain)
    stdout = StringIO()
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)

    try:
        call_command(
            "cleanup_old_data",
            model="gameplay.Message",
            days=7,
            dry_run=True,
            batch_size=1,
            stdout=stdout,
        )

        assert Message.objects.filter(pk__in=[protected.pk, plain.pk]).count() == 2
        assert cache.get(cache_key) == 999
        output = stdout.getvalue()
        assert "将删除 1 条" in output
        assert "保留 1 条未领取附件消息" in output
        assert "模拟完成：共将删除 1 条记录" in output
    finally:
        cache.delete(cache_key)
