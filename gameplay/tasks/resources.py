from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from gameplay.models import ItemTemplate, Manor
from gameplay.services.inventory.core import GRAIN_ITEM_KEY
from gameplay.services.resources import sync_resource_production

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.sync_resource_production")
def sync_resource_production_task(limit: int | None = None) -> int:
    """按资源更新时间轮转结算庄园产出，确保仓库粮食账本持续落库。"""
    configured_limit: object = getattr(settings, "RESOURCE_SYNC_TASK_BATCH_SIZE", 500)
    requested_limit: object = limit if limit is not None else configured_limit
    if isinstance(requested_limit, bool) or not isinstance(requested_limit, int):
        raise ValueError("resource sync task limit must be an integer")
    batch_size = max(1, requested_limit)
    processed = 0
    manors = Manor.objects.only("id", "resource_updated_at").order_by("resource_updated_at", "id")[:batch_size]
    grain_template = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).only("id", "key").first()
    for manor in manors:
        # 该任务不会继续使用原始对象；避免结算后再次回读整组资源字段。
        sync_resource_production(
            manor,
            persist=True,
            refresh=False,
            grain_template=grain_template,
            grain_template_resolved=True,
        )
        processed += 1
    logger.info("Settled resource production: processed=%d", processed)
    return processed
