from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from gameplay.models import ItemTemplate, Manor
from gameplay.services.inventory.core import GRAIN_ITEM_KEY
from gameplay.services.resources import sync_resource_production_batch

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.sync_resource_production")
def sync_resource_production_task(limit: int | None = None) -> int:
    """按资源更新时间轮转结算庄园产出，确保仓库粮食账本持续落库。"""
    configured_limit: object = getattr(settings, "RESOURCE_SYNC_TASK_BATCH_SIZE", 500)
    requested_limit: object = limit if limit is not None else configured_limit
    if isinstance(requested_limit, bool) or not isinstance(requested_limit, int):
        raise ValueError("resource sync task limit must be an integer")
    batch_size = max(1, requested_limit)
    configured_transaction_batch_size: object = getattr(settings, "RESOURCE_SYNC_TRANSACTION_BATCH_SIZE", 50)
    if isinstance(configured_transaction_batch_size, bool) or not isinstance(configured_transaction_batch_size, int):
        raise ValueError("resource sync transaction batch size must be an integer")
    transaction_batch_size = max(1, configured_transaction_batch_size)
    now = timezone.now()
    min_interval = max(0, int(getattr(settings, "RESOURCE_SYNC_MIN_INTERVAL_SECONDS", 0)))
    manor_queryset = Manor.objects.order_by("resource_updated_at", "id")
    if min_interval > 0:
        manor_queryset = manor_queryset.filter(
            resource_updated_at__lte=now - timedelta(seconds=min_interval),
        )
    manor_ids = list(manor_queryset.values_list("id", flat=True)[:batch_size])
    if not manor_ids:
        logger.info("Settled resource production: processed=0")
        return 0
    grain_template = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).only("id", "key").first()
    processed = 0
    for offset in range(0, len(manor_ids), transaction_batch_size):
        processed += sync_resource_production_batch(
            manor_ids[offset : offset + transaction_batch_size],
            grain_template=grain_template,
            grain_template_resolved=True,
            now=now,
        )
    logger.info("Settled resource production: processed=%d", processed)
    return processed
