"""Bounded retention for ring snapshots and their expired operation receipts."""

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from gameplay.models import ArenaRingAction, ArenaRingDaily, ArenaRingInspection, ArenaRingMatch, ArenaRingTenure

RING_HISTORY_RETENTION = timedelta(hours=24)
RING_DAILY_RETENTION = timedelta(days=15)


def _delete_batch(queryset: QuerySet[Any], batch_size: int) -> int:
    model = queryset.model
    with transaction.atomic():
        ids = list(queryset.select_for_update().order_by("pk").values_list("pk", flat=True)[:batch_size])
        if not ids:
            return 0
        _total, counts = model.objects.filter(pk__in=ids).delete()
        return int(counts.get(model._meta.label, 0))


def cleanup_ring_history(*, now: datetime | None = None, batch_size: int = 500) -> dict[str, int]:
    """Remove bounded expired history while preserving active-tenure receipts.

    Action tokens remain replayable throughout the referenced tenure and its
    24-hour history window. Once expired, monotonic ring versions and the expired
    tenure check reject old input even after its receipt has been removed.
    Reports themselves retain the existing battle-report retention policy.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("ring retention batch_size must be a positive integer")
    current_time = now or timezone.now()
    expired_ids = list(
        ArenaRingTenure.objects.filter(
            ended_at__lte=current_time - RING_HISTORY_RETENTION,
            current_on_rings__isnull=True,
        )
        .order_by("ended_at", "pk")
        .values_list("pk", flat=True)[:batch_size]
    )
    counts = {"inspections": 0, "actions": 0, "matches": 0, "tenures": 0, "daily": 0}
    if expired_ids:
        counts["inspections"] = _delete_batch(ArenaRingInspection.objects.filter(tenure_id__in=expired_ids), batch_size)
        counts["actions"] = _delete_batch(ArenaRingAction.objects.filter(result__tenure_id__in=expired_ids), batch_size)
        counts["matches"] = _delete_batch(ArenaRingMatch.objects.filter(tenure_id__in=expired_ids), batch_size)
        pending_actions = ArenaRingAction.objects.filter(result__tenure_id=OuterRef("pk"))
        # 先分批清理子记录，避免单个历史任期级联删除无界的侦察快照。
        empty_tenures = (
            ArenaRingTenure.objects.filter(
                pk__in=expired_ids,
                current_on_rings__isnull=True,
                inspections__isnull=True,
                matches__isnull=True,
            )
            .alias(has_actions=Exists(pending_actions))
            .filter(has_actions=False)
        )
        counts["tenures"] = _delete_batch(empty_tenures, batch_size)
    counts["daily"] = _delete_batch(
        ArenaRingDaily.objects.filter(date__lt=timezone.localdate(current_time - RING_DAILY_RETENTION)), batch_size
    )
    return counts
