from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import MessageError
from core.utils.atomic_cache import merge_int_id_set
from core.utils.infrastructure import (
    CACHE_INFRASTRUCTURE_EXCEPTIONS,
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.task_monitoring import increment_degraded_counter
from gameplay.models import GlobalMailCampaign, Manor
from gameplay.services.global_mail import deliver_campaign_to_manor

logger = logging.getLogger(__name__)

GLOBAL_MAIL_BACKFILL_DEFAULT_BATCH_SIZE = 500
GLOBAL_MAIL_BACKFILL_MIN_BATCH_SIZE = 50
GLOBAL_MAIL_BACKFILL_ERROR_LOG_LIMIT = 5
FAILED_GLOBAL_MAIL_MANOR_IDS_CACHE_KEY = "gameplay:global_mail:failed_manor_ids:{campaign_id}"
FAILED_GLOBAL_MAIL_MANOR_IDS_TTL = 86400 * 7  # 7 days
GLOBAL_MAIL_DELIVERY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _get_failed_manor_ids_cache_key(campaign_id: int) -> str:
    return FAILED_GLOBAL_MAIL_MANOR_IDS_CACHE_KEY.format(campaign_id=int(campaign_id))


def persist_failed_manor_ids(campaign_id: int, failed_ids: list[int]) -> None:
    """Persist failed manor IDs to cache for later retry inspection."""
    if not failed_ids:
        return
    key = _get_failed_manor_ids_cache_key(campaign_id)
    try:
        merge_int_id_set(key, failed_ids, ttl=FAILED_GLOBAL_MAIL_MANOR_IDS_TTL)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning(
            "Failed to persist global mail failed manor IDs: campaign_id=%s",
            campaign_id,
            exc_info=True,
        )


def get_failed_manor_ids(campaign_id: int) -> list[int]:
    """Read persisted failed manor IDs for a campaign."""
    key = _get_failed_manor_ids_cache_key(campaign_id)
    try:
        value = cache.get(key)
        if isinstance(value, list):
            return [int(x) for x in value]
        return []
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning(
            "Failed to read global mail failed manor IDs: campaign_id=%s",
            campaign_id,
            exc_info=True,
        )
        return []


def clear_failed_manor_ids(campaign_id: int) -> None:
    """Clear persisted failed manor IDs for a campaign after successful retry."""
    key = _get_failed_manor_ids_cache_key(campaign_id)
    try:
        cache.delete(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning(
            "Failed to clear global mail failed manor IDs: campaign_id=%s",
            campaign_id,
            exc_info=True,
        )


@shared_task(name="gameplay.backfill_global_mail_campaign")
def backfill_global_mail_campaign_task(
    campaign_id: int, batch_size: int = GLOBAL_MAIL_BACKFILL_DEFAULT_BATCH_SIZE
) -> dict[str, Any]:
    """
    异步补发全服邮件活动（幂等）。

    说明：
    - 若活动不存在，返回 not_found；
    - 若活动当前不生效，返回 inactive；
    - 投递过程为 best-effort：单个庄园失败会记录并继续，避免整任务中断。
    """
    campaign = GlobalMailCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        logger.warning("Global mail backfill skipped: campaign not found (campaign_id=%s)", campaign_id)
        return {
            "status": "not_found",
            "campaign_id": int(campaign_id),
            "scanned": 0,
            "delivered": 0,
            "failed": 0,
            "failed_manor_ids": [],
            "summary": f"campaign {int(campaign_id)} not found",
        }

    current_time = timezone.now()
    if not campaign.is_active_at(current_time):
        logger.info(
            "Global mail backfill skipped: campaign inactive at dispatch time (campaign_id=%s key=%s)",
            campaign.id,
            campaign.key,
        )
        return {
            "status": "inactive",
            "campaign_id": int(campaign.id),
            "scanned": 0,
            "delivered": 0,
            "failed": 0,
            "failed_manor_ids": [],
            "summary": f"campaign {int(campaign.id)} inactive",
        }

    normalized_batch_size = max(
        GLOBAL_MAIL_BACKFILL_MIN_BATCH_SIZE, int(batch_size or GLOBAL_MAIL_BACKFILL_DEFAULT_BATCH_SIZE)
    )
    delivered_count = 0
    failed_count = 0
    scanned_count = 0
    failed_manor_ids: list[int] = []

    for manor in Manor.objects.only("id").order_by("id").iterator(chunk_size=normalized_batch_size):
        scanned_count += 1
        try:
            if deliver_campaign_to_manor(campaign, manor, now=current_time):
                delivered_count += 1
        except GLOBAL_MAIL_DELIVERY_EXCEPTIONS as exc:
            failed_count += 1
            failed_manor_ids.append(int(manor.id))
            if failed_count <= GLOBAL_MAIL_BACKFILL_ERROR_LOG_LIMIT:
                logger.exception(
                    "Global mail backfill delivery failed: campaign_id=%s manor_id=%s error=%s",
                    campaign.id,
                    manor.id,
                    exc,
                )

    if failed_manor_ids:
        logger.error(
            "batch partial failure",
            extra={
                "task": "gameplay.backfill_global_mail_campaign",
                "failed_ids": failed_manor_ids,
                "degraded": True,
            },
        )
        increment_degraded_counter("global_mail")
        persist_failed_manor_ids(campaign.id, failed_manor_ids)

    final_status = "partial_failure" if failed_manor_ids else "ok"
    logger.info(
        "Global mail backfill completed: campaign_id=%s key=%s scanned=%s delivered=%s failed=%s status=%s",
        campaign.id,
        campaign.key,
        scanned_count,
        delivered_count,
        failed_count,
        final_status,
    )
    return {
        "status": final_status,
        "campaign_id": int(campaign.id),
        "scanned": int(scanned_count),
        "delivered": int(delivered_count),
        "failed": int(failed_count),
        "failed_manor_ids": failed_manor_ids,
        "summary": (
            f"campaign {int(campaign.id)} backfill completed: "
            f"scanned={int(scanned_count)} delivered={int(delivered_count)} "
            f"failed={int(failed_count)} failed_manor_ids={failed_manor_ids}"
        ),
    }


def enqueue_global_mail_backfill(campaign_id: int, *, batch_size: int = GLOBAL_MAIL_BACKFILL_DEFAULT_BATCH_SIZE):
    """提交异步补发任务并返回是否成功入队。"""
    return safe_apply_async(
        backfill_global_mail_campaign_task,
        args=[int(campaign_id), int(batch_size)],
        logger=logger,
        log_message="global mail backfill task dispatch failed",
    )
