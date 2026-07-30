from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from core.exceptions import ProductionCancelError


def cancel_active_production(
    *,
    production_model: Any,
    manor: Any,
    production_id: int,
    active_status: str,
    cancelled_status: str,
) -> Any:
    """取消未到期的生产记录；开始生产时扣除的资源保持不变。"""
    with transaction.atomic():
        production = production_model.objects.select_for_update().filter(pk=production_id, manor_id=manor.pk).first()
        if production is None:
            raise ProductionCancelError("未找到该生产任务")
        if production.status != active_status:
            raise ProductionCancelError("该生产任务已结束，无法取消")

        cancelled_at = timezone.now()
        if production.complete_at <= cancelled_at:
            raise ProductionCancelError("该生产任务已到期，无法取消")

        production.status = cancelled_status
        production.finished_at = cancelled_at
        production.save(update_fields=["status", "finished_at"])

    return production
