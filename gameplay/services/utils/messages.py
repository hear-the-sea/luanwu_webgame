"""
消息管理服务
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from heapq import nsmallest
from threading import Lock
from typing import Callable, Dict

from django.core.cache import cache
from django.db import transaction
from django.db.models import F, QuerySet
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from core.config import MESSAGE
from core.exceptions import AttachmentAlreadyClaimedError, MessageNotFoundError, NoAttachmentError
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from ...models import InventoryItem, ItemTemplate, Manor, Message, ResourceEvent
from ...models.items import (
    _allow_prevalidated_message_deletions,
    message_has_protected_attachments,
    normalize_message_attachment_buckets,
)
from ..resources import grant_resources_locked
from .cache import CACHE_TIMEOUT_SHORT, CacheKeys

# 从 core.config 导入配置
MESSAGE_RETENTION_DAYS = MESSAGE.RETENTION_DAYS
logger = logging.getLogger(__name__)

_LOCAL_CLEANUP_FALLBACK: dict[int, float] = {}
_LOCAL_CLEANUP_FALLBACK_LOCK = Lock()
_LOCAL_CLEANUP_FALLBACK_MAX_SIZE = 10000
_LOCAL_CLEANUP_FALLBACK_CLEANUP_BATCH = 2000
_LOCAL_CLEANUP_FALLBACK_EVICT_COUNT = 1000
_MESSAGE_DELETE_BATCH_SIZE = 1000


@dataclass(frozen=True)
class MessageDeleteResult:
    deleted_count: int
    protected_count: int


def _safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("messages cache.get failed: key=%s", key, exc_info=True)
        return default


def _safe_cache_set(key: str, value, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("messages cache.set failed: key=%s", key, exc_info=True)


def _safe_cache_add(key: str, value, timeout: int) -> bool:
    try:
        return bool(cache.add(key, value, timeout=timeout))
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("messages cache.add failed: key=%s", key, exc_info=True)
        return False


def _allow_cleanup_via_local_fallback(manor_id: int, interval_seconds: int) -> bool:
    """Fallback gate when cache is unavailable to avoid repeated full-table deletes."""
    if manor_id <= 0 or interval_seconds <= 0:
        return True

    now_monotonic = time.monotonic()
    stale_before = now_monotonic - max(interval_seconds * 2, 60)

    with _LOCAL_CLEANUP_FALLBACK_LOCK:
        last_cleanup = _LOCAL_CLEANUP_FALLBACK.get(manor_id)
        if last_cleanup is not None and now_monotonic - last_cleanup < interval_seconds:
            return False

        _LOCAL_CLEANUP_FALLBACK[manor_id] = now_monotonic
        if len(_LOCAL_CLEANUP_FALLBACK) > _LOCAL_CLEANUP_FALLBACK_MAX_SIZE:
            stale_keys = [key for key, ts in _LOCAL_CLEANUP_FALLBACK.items() if ts < stale_before]
            for key in stale_keys[:_LOCAL_CLEANUP_FALLBACK_CLEANUP_BATCH]:
                _LOCAL_CLEANUP_FALLBACK.pop(key, None)
        if len(_LOCAL_CLEANUP_FALLBACK) > _LOCAL_CLEANUP_FALLBACK_MAX_SIZE:
            overflow = len(_LOCAL_CLEANUP_FALLBACK) - _LOCAL_CLEANUP_FALLBACK_MAX_SIZE
            evict_count = max(_LOCAL_CLEANUP_FALLBACK_EVICT_COUNT, overflow)
            for key, _ in nsmallest(evict_count, _LOCAL_CLEANUP_FALLBACK.items(), key=lambda item: item[1]):
                _LOCAL_CLEANUP_FALLBACK.pop(key, None)
    return True


def _safe_cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("messages cache.delete failed: key=%s", key, exc_info=True)


def _safe_cache_delete_many(keys: list[str]) -> None:
    try:
        cache.delete_many(keys)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("messages cache.delete_many failed: keys_count=%s", len(keys), exc_info=True)


def _invalidate_unread_count_cache(manor_id: int) -> None:
    """Invalidate unread-count cache for a manor."""
    _safe_cache_delete(CacheKeys.unread_count(manor_id))


def create_message(
    manor: Manor,
    kind: str,
    title: str,
    body: str = "",
    battle_report=None,
    is_read: bool = False,
    attachments: Dict = None,
) -> Message:
    """
    为庄园创建一条消息。

    Args:
        manor: 庄园对象
        kind: 消息类型（battle/system/reward）
        title: 消息标题
        body: 消息内容
        battle_report: 关联的战报��象（可选）
        is_read: 是否已读
        attachments: 附件数据，格式：{"resources": {"grain": 100}, "items": {"item_key": 5}}

    Returns:
        创建的消息对象
    """
    normalized_attachments = attachments or {}
    message = Message.objects.create(
        manor=manor,
        kind=kind,
        title=title,
        body=body,
        battle_report=battle_report,
        is_read=is_read,
        attachments=normalized_attachments,
        is_deletion_protected=message_has_protected_attachments(
            normalized_attachments,
            is_claimed=False,
        ),
    )

    # 清除未读消息数缓存
    _invalidate_unread_count_cache(manor.id)

    return message


def bulk_create_messages(messages_data: list) -> list:
    """
    批量创建消息。

    Args:
        messages_data: 消息数据列表，每项包含 manor, kind, title, body 等字段

    Returns:
        创建的消息对象列表
    """
    if not messages_data:
        return []

    messages_to_create = []
    for data in messages_data:
        attachments = data.get("attachments") or {}
        is_claimed = data.get("is_claimed", False)
        messages_to_create.append(
            Message(
                manor=data["manor"],
                kind=data.get("kind", "system"),
                title=data["title"],
                body=data.get("body", ""),
                battle_report=data.get("battle_report"),
                is_read=data.get("is_read", False),
                attachments=attachments,
                is_claimed=is_claimed,
                is_deletion_protected=message_has_protected_attachments(
                    attachments,
                    is_claimed=is_claimed,
                ),
            )
        )

    created_messages = Message.objects.bulk_create(messages_to_create)

    # 批量清除相关庄园的未读消息缓存
    manor_ids = {data["manor"].id for data in messages_data}
    cache_keys = [CacheKeys.unread_count(manor_id) for manor_id in manor_ids]
    _safe_cache_delete_many(cache_keys)

    return created_messages


def delete_message_queryset(
    queryset: QuerySet[Message],
    *,
    batch_size: int | None = None,
    dry_run: bool = False,
    before_delete_batch: Callable[[QuerySet[Message]], object] | None = None,
) -> MessageDeleteResult:
    """删除符合资格的消息，并单独统计受保护消息。"""
    effective_batch_size = batch_size if batch_size is not None else _MESSAGE_DELETE_BATCH_SIZE
    if effective_batch_size <= 0:
        raise ValueError("batch_size must be positive")

    database_alias = queryset.db
    deleted_count = 0
    protected_count = 0
    last_message_id = 0
    while True:
        candidate_ids = list(
            queryset.filter(id__gt=last_message_id).order_by("id").values_list("id", flat=True)[:effective_batch_size]
        )
        if not candidate_ids:
            break
        last_message_id = candidate_ids[-1]

        if dry_run:
            candidates = list(
                queryset.filter(id__in=candidate_ids).only("id", "attachments", "is_claimed").order_by("id")
            )
            protected_count += sum(1 for message in candidates if message.has_protected_attachments)
            deleted_count += sum(1 for message in candidates if not message.has_protected_attachments)
            if len(candidate_ids) < effective_batch_size:
                break
            continue

        deleted_manor_ids: set[int] = set()
        with transaction.atomic(using=database_alias):
            locked_messages = list(
                queryset.select_for_update()
                .filter(id__in=candidate_ids)
                .only("id", "manor_id", "attachments", "is_claimed", "is_deletion_protected")
                .order_by("id")
            )
            protection_states = [(message, message.has_protected_attachments) for message in locked_messages]
            protected_messages = [message for message, is_protected in protection_states if is_protected]
            protected_count += len(protected_messages)
            stale_false_marker_ids = [message.id for message in protected_messages if not message.is_deletion_protected]
            if stale_false_marker_ids:
                Message.objects.using(database_alias).filter(id__in=stale_false_marker_ids).update(
                    is_deletion_protected=True
                )
            eligible_messages = [message for message, is_protected in protection_states if not is_protected]

            if eligible_messages:
                eligible_ids = [message.id for message in eligible_messages]
                eligible_queryset = Message.objects.using(database_alias).filter(id__in=eligible_ids).order_by("id")
                if before_delete_batch is not None:
                    before_delete_batch(eligible_queryset)
                    revalidated_messages = list(
                        Message.objects.using(database_alias)
                        .filter(id__in=eligible_ids)
                        .only("id", "attachments", "is_claimed", "kind", "title")
                        .order_by("id")
                    )
                    protected_after_callback = [
                        message for message in revalidated_messages if message.has_protected_attachments
                    ]
                    if protected_after_callback:
                        raise ProtectedError(
                            "未领取附件消息禁止删除",
                            set(protected_after_callback),
                        )
                    eligible_ids = [message.id for message in revalidated_messages]
                    eligible_queryset = Message.objects.using(database_alias).filter(id__in=eligible_ids).order_by("id")
                with _allow_prevalidated_message_deletions(database_alias, eligible_ids):
                    _deleted_total, deleted_details = eligible_queryset.delete()
                batch_deleted = int(deleted_details.get(Message._meta.label, 0))
                deleted_count += batch_deleted
                if batch_deleted > 0:
                    deleted_manor_ids = {message.manor_id for message in eligible_messages}

        if deleted_manor_ids:
            for manor_id in sorted(deleted_manor_ids):
                _invalidate_unread_count_cache(manor_id)

        if len(candidate_ids) < effective_batch_size:
            break

    return MessageDeleteResult(deleted_count=deleted_count, protected_count=protected_count)


def delete_expired_messages(
    cutoff,
    *,
    batch_size: int | None = None,
    dry_run: bool = False,
) -> MessageDeleteResult:
    """删除过期消息，同时保留未领取附件消息。"""
    protected_marker_result = delete_message_queryset(
        Message.objects.filter(created_at__lt=cutoff, is_deletion_protected=True),
        batch_size=batch_size,
        dry_run=dry_run,
    )
    unprotected_marker_result = delete_message_queryset(
        Message.objects.filter(created_at__lt=cutoff, is_deletion_protected=False),
        batch_size=batch_size,
        dry_run=dry_run,
    )
    return MessageDeleteResult(
        deleted_count=protected_marker_result.deleted_count + unprotected_marker_result.deleted_count,
        protected_count=protected_marker_result.protected_count + unprotected_marker_result.protected_count,
    )


def cleanup_old_messages(manor: Manor) -> MessageDeleteResult:
    """
    删除超过保留期限的旧消息。

    Args:
        manor: 庄园对象
    """
    # 保留期以“天”为单位，清理无需高频执行；这里对重复清理请求按庄园节流（默认 6 小时一次）。
    cleanup_gate_timeout = 6 * 60 * 60
    cleanup_gate_key = f"messages:cleanup_old:{manor.id}"
    if not _safe_cache_add(
        cleanup_gate_key, "1", timeout=cleanup_gate_timeout
    ) and not _allow_cleanup_via_local_fallback(manor.id, cleanup_gate_timeout):
        return MessageDeleteResult(deleted_count=0, protected_count=0)

    threshold = timezone.now() - timedelta(days=MESSAGE_RETENTION_DAYS)
    return delete_message_queryset(manor.messages.filter(created_at__lt=threshold))


def list_messages(manor: Manor):
    """
    获取庄园的最新消息列表。

    Args:
        manor: 庄园对象

    Returns:
        消息查询集（按创建时间倒序）
    """
    return manor.messages.select_related("battle_report").order_by("-created_at")


def delete_messages(manor: Manor, message_ids) -> MessageDeleteResult:
    """
    批量删除玩家选中的消息。

    Args:
        manor: 庄园对象
        message_ids: 消息ID列表
    """
    return delete_message_queryset(manor.messages.filter(id__in=message_ids))


def delete_all_messages(manor: Manor) -> MessageDeleteResult:
    """
    一键清空所有消息。

    Args:
        manor: 庄园对象
    """
    return delete_message_queryset(manor.messages.all())


def mark_messages_read(manor: Manor, message_ids):
    """
    将指定的消息标记为已读（不删除）。

    Args:
        manor: 庄园对象
        message_ids: 消息ID列表
    """
    manor.messages.filter(id__in=message_ids).update(is_read=True)

    # 清除未读消息数缓存
    _invalidate_unread_count_cache(manor.id)


def mark_all_messages_read(manor: Manor):
    """
    一键将所有消息标记为已读，清除UI中的未读标记。

    Args:
        manor: 庄园对象
    """
    manor.messages.filter(is_read=False).update(is_read=True)

    # 清除未读消息数缓存
    _invalidate_unread_count_cache(manor.id)


def unread_message_count(manor: Manor) -> int:
    """
    获取未读消息数量（用于显示通知点）。

    使用 5 秒缓存来避免频繁查询数据库。

    Args:
        manor: 庄园对象

    Returns:
        未读消息数量
    """
    cache_key = CacheKeys.unread_count(manor.id)
    count = _safe_cache_get(cache_key)

    if count is None:
        count = manor.messages.filter(is_read=False).count()
        _safe_cache_set(cache_key, count, timeout=CACHE_TIMEOUT_SHORT)

    return count


@transaction.atomic
def claim_message_attachments(message: Message) -> Dict:
    """
    领取消息附件，将资源和道具发放到玩家仓库。

    This function uses row-level locking to prevent race conditions
    in concurrent claim attempts.

    Args:
        message: 消息对象

    Returns:
        领取的物品摘要字典

    Raises:
        MessageNotFoundError: 消息不存在时抛出
        NoAttachmentError: 消息无附件时抛出
        AttachmentAlreadyClaimedError: 附件已领取时抛出
    """
    # CRITICAL: Use select_for_update to acquire row lock and prevent concurrent claims
    # This must be the first database operation in the transaction
    locked_message = Message.objects.select_for_update().select_related("manor").filter(pk=message.pk).first()

    if locked_message is None:
        raise MessageNotFoundError()

    if not locked_message.has_attachments:
        raise NoAttachmentError()

    # Check claim status AFTER acquiring lock
    if locked_message.is_claimed:
        raise AttachmentAlreadyClaimedError()

    manor = Manor.objects.select_for_update().get(pk=locked_message.manor_id)
    attachments = dict(locked_message.attachments or {})
    claimed_summary = {}
    resources, items = normalize_message_attachment_buckets(attachments)

    # 发放资源
    claimed_resources, _overflow = grant_resources_locked(
        manor,
        resources,
        note=f"邮件附件：{locked_message.title}",
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        sync_production=False,
    )
    claimed_summary.update(claimed_resources)

    # 发放道具
    claimed_items: Dict[str, int] = {}
    if items:
        # 批量预加载物品模板，避免N+1查询
        item_keys = list(items.keys())
        item_templates_map = {tpl.key: tpl for tpl in ItemTemplate.objects.filter(key__in=item_keys)}

        for item_key, quantity in items.items():
            if quantity <= 0:
                continue

            # 从预加载的字典中查找物品模板
            item_template = item_templates_map.get(item_key)
            if not item_template:
                continue

            # 获取或创建库存记录（明确指定存储位置为仓库）
            # Use select_for_update for get_or_create to prevent race conditions
            # IMPORTANT: Must explicitly specify manor in get_or_create lookup to avoid NULL manor_id
            inventory_item, _created = InventoryItem.objects.select_for_update().get_or_create(
                manor=manor,
                template=item_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                defaults={"quantity": 0},
            )

            # Atomic quantity update using F() expression
            InventoryItem.objects.filter(pk=inventory_item.pk).update(
                quantity=F("quantity") + quantity, updated_at=timezone.now()
            )

            claimed_summary[f"item_{item_key}"] = quantity
            claimed_items[item_key] = quantity

    # 标记为已领取
    locked_message.is_claimed = True
    locked_message.is_deletion_protected = False
    locked_message.is_read = True
    attachments["claimed"] = {
        "resources": claimed_resources,
        "items": claimed_items,
    }
    locked_message.attachments = attachments
    locked_message.save(update_fields=["is_claimed", "is_deletion_protected", "is_read", "attachments"])
    _invalidate_unread_count_cache(manor.id)

    return claimed_summary
