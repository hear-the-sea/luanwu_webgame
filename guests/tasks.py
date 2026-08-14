from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Callable

from celery import shared_task
from django.db import transaction
from django.db.models import F, IntegerField, Q, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from core.config import GUEST, GUEST_LOYALTY
from core.exceptions import MessageError
from core.exceptions.task import TaskRescheduleError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import BotMaintenanceRecovery
from gameplay.services.virtual_player_core.maintenance_completion import record_virtual_player_maintenance_completion
from gameplay.services.virtual_player_core.recovery import (
    classify_failure,
    clear_recovery_failure,
    exclude_blocked_entity_recoveries,
    record_recovery_failure,
    recovery_circuit_is_open,
)

logger = logging.getLogger(__name__)

# 向后兼容导出：供测试与外部调用方使用
DEFECTION_PROBABILITY = GUEST_LOYALTY.DEFECTION_PROBABILITY
DEFECTION_BATCH_SIZE = GUEST_LOYALTY.DEFECTION_BATCH_SIZE
DEFECTION_QUERY_CHUNK_SIZE = GUEST_LOYALTY.DEFECTION_QUERY_CHUNK_SIZE
GUEST_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = DATABASE_INFRASTRUCTURE_EXCEPTIONS
GUEST_DEFECTION_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _dedup_timeout_for_remaining(remaining: int) -> int:
    return max(int(remaining) + 60, 60)


def _guest_recovery_key(guest_id: int) -> str:
    return f"guest:{int(guest_id)}"


def _record_guest_training_failure(
    *,
    guest_id: int,
    error: BaseException,
    now,
    phase: str,
) -> None:
    record_recovery_failure(
        scope=BotMaintenanceRecovery.Scope.GUEST,
        entity_key=_guest_recovery_key(guest_id),
        failure_code=classify_failure(error),
        error=error,
        operation_id=f"guest-training-{int(guest_id)}-{int(now.timestamp())}"[:64],
        payload={"guest_id": int(guest_id), "phase": str(phase)},
        now=now,
    )


@shared_task(name="guests.complete_training", bind=True, max_retries=2, default_retry_delay=30)
def complete_guest_training(self, guest_id: int) -> str:
    from guests.models import Guest
    from guests.services.training import finalize_guest_training

    try:
        guest = Guest.objects.select_related("manor").filter(pk=guest_id).first()
        if not guest:
            logger.warning("Guest %d not found", guest_id)
            return "not_found"
        now = timezone.now()
        if recovery_circuit_is_open(path="guest", now=now):
            return "recovery_required"
        if guest.training_complete_at and guest.training_complete_at > now:
            remaining = math.ceil((guest.training_complete_at - now).total_seconds())
            if remaining > 0:
                dispatched = safe_apply_async_with_dedup(
                    complete_guest_training,
                    dedup_key=f"guest:training:{guest_id}",
                    dedup_timeout=_dedup_timeout_for_remaining(remaining),
                    args=[guest_id],
                    countdown=remaining,
                    logger=logger,
                    log_message=f"guest training reschedule failed: guest_id={guest_id}",
                )
                if not dispatched:
                    logger.warning(
                        "guest training reschedule dispatch returned False: guest_id=%d — "
                        "scan_guest_training fallback will handle completion",
                        guest_id,
                    )
                    raise TaskRescheduleError("complete_guest_training调度失败")
                return "rescheduled"
        origin_completed_at = guest.training_complete_at
        finalized = finalize_guest_training(guest, now=now)
        if finalized:
            record_virtual_player_maintenance_completion(
                manor_id=getattr(guest, "manor_id", None),
                domain_event_kind="guest_training",
                domain_object_id=int(guest_id),
                origin_completed_at=origin_completed_at,
            )
        return "completed" if finalized else "skipped"
    except GUEST_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete guest training %d: %s", guest_id, exc)
        raise self.retry(exc=exc)
    except TaskRescheduleError:
        raise
    except Exception as exc:
        now = timezone.now()
        _record_guest_training_failure(
            guest_id=int(guest_id),
            error=exc,
            now=now,
            phase="complete_task",
        )
        logger.exception("Guest training completion was quarantined for guest %d", guest_id)
        return "recovery_required"


@shared_task(name="guests.scan_training")
def scan_guest_training(limit: int = 200) -> int:
    """
    Scan-fallback: finalize overdue guest training sessions.

    Primary path: each training session schedules a dedicated
    ``complete_guest_training`` task at ``training_complete_at`` time.

    This periodic scan compensates for tasks that were lost, delayed, or failed
    due to broker restarts, worker crashes, or transient infrastructure errors.
    It prioritizes overdue timers, then uses the remaining batch capacity to
    restart eligible real-player guests whose timer is missing.
    """
    from guests.models import Guest, GuestStatus
    from guests.services.training import ensure_auto_training, finalize_guest_training

    now = timezone.now()
    batch_limit = max(0, int(limit))
    if recovery_circuit_is_open(path="guest", now=now):
        logger.warning("Guest training recovery circuit is open; preserving due training rows")
        return 0
    due_query = (
        Guest.objects.select_related("manor")
        .filter(training_complete_at__isnull=False, training_complete_at__lte=now)
        .order_by("training_complete_at", "id")
    )
    due_guests = list(
        exclude_blocked_entity_recoveries(
            due_query,
            scope=BotMaintenanceRecovery.Scope.GUEST,
            now=now,
        )[:batch_limit]
    )
    count = 0
    for guest in due_guests:
        try:
            origin_completed_at = guest.training_complete_at
            finalized = finalize_guest_training(guest, now=now)
            if finalized:
                count += 1
                record_virtual_player_maintenance_completion(
                    manor_id=getattr(guest, "manor_id", None),
                    domain_event_kind="guest_training",
                    domain_object_id=int(guest.id),
                    origin_completed_at=origin_completed_at,
                )
            clear_recovery_failure(
                scope=BotMaintenanceRecovery.Scope.GUEST,
                entity_key=_guest_recovery_key(int(guest.id)),
                now=now,
            )
        except GUEST_TASK_RETRY_EXCEPTIONS:
            # A database/broker outage is a scan-level infrastructure failure,
            # not a poisoned Guest.  Let Celery retry the task; successful
            # guests before this point are already committed and the
            # finalize operation is idempotent.
            logger.exception("Failed to finalize guest training %d", guest.id)
            raise
        except Exception as exc:
            _record_guest_training_failure(
                guest_id=int(guest.id),
                error=exc,
                now=now,
                phase="finalize_scan",
            )
            logger.exception("Guest training finalize was isolated for guest %d", guest.id)

    remaining = batch_limit - len(due_guests)
    if remaining <= 0:
        return count

    orphan_query = (
        Guest.objects.select_related("template")
        .filter(
            status=GuestStatus.IDLE,
            level__lt=int(GUEST.MAX_LEVEL),
            training_complete_at__isnull=True,
        )
        .order_by("id")
    )
    orphan_guests = list(
        exclude_blocked_entity_recoveries(
            orphan_query,
            scope=BotMaintenanceRecovery.Scope.GUEST,
            now=now,
        )[:remaining]
    )
    for guest in orphan_guests:
        try:
            if ensure_auto_training(guest):
                count += 1
            clear_recovery_failure(
                scope=BotMaintenanceRecovery.Scope.GUEST,
                entity_key=_guest_recovery_key(int(guest.id)),
                now=now,
            )
        except GUEST_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to reconcile guest training %d", guest.id)
            raise
        except Exception as exc:
            _record_guest_training_failure(
                guest_id=int(guest.id),
                error=exc,
                now=now,
                phase="orphan_scan",
            )
            logger.exception("Guest training reconciliation was isolated for guest %d", guest.id)
    return count


@shared_task(name="guests.complete_recruitment", bind=True, max_retries=2, default_retry_delay=30)
def complete_guest_recruitment(self, recruitment_id: int) -> str:
    from guests.models import GuestRecruitment
    from guests.services.recruitment import finalize_guest_recruitment

    try:
        recruitment = (
            GuestRecruitment.objects.select_related("manor", "manor__user", "pool").filter(pk=recruitment_id).first()
        )
        if not recruitment:
            logger.warning("GuestRecruitment %d not found", recruitment_id)
            return "not_found"

        now = timezone.now()
        # Immediate virtual-player recruitment is persisted as COMPLETED with
        # a zero-duration timestamp.  Status is the durable source of truth;
        # do not reschedule a completion task merely because a caller supplied
        # a future logical timestamp while settling the batch.
        if getattr(recruitment, "source", None) == "virtual" and getattr(recruitment, "status", None) == "completed":
            return "skipped"
        if recruitment.complete_at and recruitment.complete_at > now:
            remaining = math.ceil((recruitment.complete_at - now).total_seconds())
            if remaining > 0:
                dispatched = safe_apply_async_with_dedup(
                    complete_guest_recruitment,
                    dedup_key=f"guest:recruitment:{recruitment_id}",
                    dedup_timeout=_dedup_timeout_for_remaining(remaining),
                    args=[recruitment_id],
                    countdown=remaining,
                    logger=logger,
                    log_message=f"guest recruitment reschedule failed: recruitment_id={recruitment_id}",
                )
                if not dispatched:
                    logger.warning(
                        "guest recruitment reschedule dispatch returned False: recruitment_id=%d — "
                        "scan_guest_recruitments fallback will handle completion",
                        recruitment_id,
                    )
                    raise TaskRescheduleError("complete_guest_recruitment调度失败")
                return "rescheduled"

        origin_completed_at = getattr(recruitment, "complete_at", None)
        finalized = finalize_guest_recruitment(recruitment, now=now, send_notification=True)
        if finalized:
            record_virtual_player_maintenance_completion(
                manor_id=getattr(recruitment, "manor_id", None),
                domain_event_kind="guest_recruitment",
                domain_object_id=int(recruitment_id),
                origin_completed_at=origin_completed_at,
            )
        return "completed" if finalized else "skipped"
    except GUEST_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete guest recruitment %d: %s", recruitment_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="guests.scan_recruitments")
def scan_guest_recruitments(limit: int = 200) -> int:
    """
    Scan-fallback: finalize overdue guest recruitment processes.

    Primary path: each recruitment schedules a dedicated
    ``complete_guest_recruitment`` task at ``complete_at`` time.

    This periodic scan compensates for tasks that were lost, delayed, or failed
    due to broker restarts, worker crashes, or transient infrastructure errors.
    It queries for all recruitments in PENDING status whose ``complete_at`` has
    passed, processing up to *limit* recruitments per invocation.
    """
    from guests.models import GuestRecruitment
    from guests.services.recruitment import finalize_guest_recruitment

    now = timezone.now()
    qs = (
        GuestRecruitment.objects.select_related("manor", "manor__user", "pool")
        .filter(status=GuestRecruitment.Status.PENDING, complete_at__lte=now)
        .order_by("complete_at")[:limit]
    )
    count = 0
    for recruitment in qs:
        try:
            origin_completed_at = getattr(recruitment, "complete_at", None)
            finalized = finalize_guest_recruitment(recruitment, now=now, send_notification=True)
            if finalized:
                count += 1
                record_virtual_player_maintenance_completion(
                    manor_id=getattr(recruitment, "manor_id", None),
                    domain_event_kind="guest_recruitment",
                    domain_object_id=int(recruitment.id),
                    origin_completed_at=origin_completed_at,
                )
        except GUEST_TASK_RETRY_EXCEPTIONS:
            # Per-recruitment failure should not abort the scan; the next cycle
            # will retry any recruitments that were skipped.
            logger.exception("Failed to finalize guest recruitment %d", recruitment.id)
    return count


@shared_task(name="guests.scan_passive_hp_recovery")
def scan_passive_hp_recovery(limit: int = 200) -> int:
    """
    Scan-fallback: recover HP for injured or idle guests.

    There is no per-guest primary task for passive HP recovery; this periodic
    scan is the sole mechanism.  It acts as a fallback in the sense that HP
    recovery is intentionally batched and tolerant of missed cycles -- if a
    scan cycle is skipped, guests simply wait longer and the next cycle catches
    up.  The ``last_hp_recovery_at`` timestamp ensures idempotent recovery
    regardless of scan frequency.
    """
    from guests.constants import TimeConstants
    from guests.models import Guest, GuestStatus
    from guests.services.health import recover_guest_hp_for_guest

    now = timezone.now()
    batch_limit = max(0, int(limit))
    cutoff = now - timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)
    max_hp_expr = Greatest(
        F("template__base_hp") + F("hp_bonus") + F("defense_stat") * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
        Value(int(GUEST.MIN_HP_FLOOR)),
        output_field=IntegerField(),
    )
    guest_ids = list(
        Guest.objects.filter(
            last_hp_recovery_at__lte=cutoff,
            status__in=[GuestStatus.IDLE, GuestStatus.INJURED],
        )
        .filter(Q(current_hp__lt=max_hp_expr) | Q(status=GuestStatus.INJURED, current_hp__gte=max_hp_expr))
        .order_by("last_hp_recovery_at", "id")
        .values_list("id", flat=True)[:batch_limit]
    )
    count = 0
    for guest_id in guest_ids:
        try:
            if recover_guest_hp_for_guest(int(guest_id), now=now):
                count += 1
        except GUEST_TASK_RETRY_EXCEPTIONS:
            # Per-guest HP recovery failure should not abort the scan.
            logger.exception("Failed to recover passive HP for guest %d", guest_id)
    return count


@shared_task(name="guests.scan_injury_loyalty_decay")
def scan_injury_loyalty_decay(limit: int = 200) -> int:
    """结算战斗重伤门客每满三小时产生的忠诚度损失。"""
    from guests.models import Guest, GuestStatus
    from guests.services.loyalty import INJURY_LOYALTY_DECAY_INTERVAL, process_injury_loyalty_decay_for_guest

    now = timezone.now()
    cutoff = now - INJURY_LOYALTY_DECAY_INTERVAL
    guest_ids = list(
        Guest.objects.filter(
            status=GuestStatus.INJURED,
            injury_loyalty_processed_at__lte=cutoff,
        )
        .order_by("injury_loyalty_processed_at", "id")
        .values_list("id", flat=True)[: max(0, int(limit))]
    )

    processed_count = 0
    for guest_id in guest_ids:
        try:
            if process_injury_loyalty_decay_for_guest(int(guest_id), now=now) > 0:
                processed_count += 1
        except GUEST_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to process injury loyalty decay for guest %d", guest_id)
    return processed_count


@shared_task(name="guests.process_daily_loyalty", bind=True, max_retries=2, default_retry_delay=60)
def process_daily_loyalty(self) -> str:
    """
    处理每日门客忠诚度变化
    建议每日凌晨执行一次
    """
    import hashlib
    from datetime import timedelta

    from django.db.models import F, Q
    from django.db.models.functions import Greatest, Least

    from gameplay.services.utils.messages import create_message
    from guests.models import Guest, SalaryPayment

    try:
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)

        paid_guest_ids_qs = SalaryPayment.objects.filter(for_date=yesterday).values_list("guest_id", flat=True)
        base_qs = Guest.objects.filter(
            Q(loyalty_processed_for_date__lt=today) | Q(loyalty_processed_for_date__isnull=True)
        )

        # A guest owes no salary on its creation date. The first loyalty
        # settlement after recruitment therefore records processing without a
        # gain or loss; normal salary-based settlement starts the next day.
        creation_day_exempt_qs = base_qs.filter(created_at__date__gte=yesterday)
        salary_eligible_qs = base_qs.filter(created_at__date__lt=yesterday)

        paid_qs = salary_eligible_qs.filter(id__in=paid_guest_ids_qs)
        unpaid_qs = salary_eligible_qs.exclude(id__in=paid_guest_ids_qs)

        increased_count = paid_qs.update(
            loyalty=Least(100, F("loyalty") + 1),
            loyalty_processed_for_date=today,
        )
        decreased_count = unpaid_qs.update(
            loyalty=Greatest(0, F("loyalty") - 1),
            loyalty_processed_for_date=today,
        )
        exempt_count = creation_day_exempt_qs.update(
            loyalty_processed_for_date=today,
        )

        # 处理叛逃：仅对未支付工资且 loyalty < 30 的候选做抽样，再对命中者逐个处理。
        # 说明：概率逻辑必须在 Python 侧执行，避免 DB 随机函数带来不可控性能和可重复性问题。
        defection_count = 0
        batch: list[int] = []

        # NOTE: `paid_qs`/`unpaid_qs` are derived from `base_qs` which filters by
        # `loyalty_processed_for_date < today`. After the bulk updates above,
        # those querysets would become empty if evaluated again.
        # So for defections we re-select candidates by `loyalty_processed_for_date=today`.
        defection_candidate_ids = (
            Guest.objects.filter(
                created_at__date__lt=yesterday,
                loyalty_processed_for_date=today,
                loyalty__lt=30,
            )
            .exclude(id__in=paid_guest_ids_qs)
            .values_list("id", flat=True)
        )

        for guest_id in defection_candidate_ids.iterator(chunk_size=DEFECTION_QUERY_CHUNK_SIZE):
            guest_id_int = int(guest_id)
            if _should_defect(guest_id_int, today, probability=DEFECTION_PROBABILITY, hasher=hashlib.sha256):
                batch.append(guest_id_int)
                if len(batch) >= DEFECTION_BATCH_SIZE:
                    defection_count += _process_defection_batch(batch, create_message=create_message)
                    batch = []

        if batch:
            defection_count += _process_defection_batch(batch, create_message=create_message)

        updated_count = increased_count + decreased_count + exempt_count
        return f"处理了 {updated_count} 个门客的忠诚度，{defection_count} 个门客叛逃"
    except GUEST_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to process daily loyalty: %s", exc)
        raise self.retry(exc=exc)


def _should_defect(guest_id: int, date_value, *, probability: float, hasher) -> bool:
    payload = f"{date_value.isoformat()}:{int(guest_id)}".encode("utf-8")
    digest = hasher(payload).digest()
    value = int.from_bytes(digest[:8], "big") / 2**64
    return value < float(probability)


def _build_defection_message_payload(guest) -> dict:
    rarity_display = guest.template.get_rarity_display()
    return {
        "manor": guest.manor,
        "kind": "system",
        "title": "【门客叛逃】门客离开了庄园",
        "body": (
            f"由于长期未支付工资，您的门客 {guest.display_name}（等级{guest.level}）"
            f"对您失去了信任，已经离开了庄园。\n\n"
            f"门客信息：\n"
            f"- 名称：{guest.display_name}\n"
            f"- 等级：{guest.level}\n"
            f"- 稀有度：{rarity_display}\n"
            f"- 叛逃时忠诚度：{guest.loyalty}\n\n"
            f"提示：请及时支付门客工资以保持他们的忠诚。"
        ),
    }


def _process_defection_batch(guest_ids: list[int], *, create_message: Callable) -> int:
    from gameplay.models import Manor
    from guests.models import Guest, GuestDefection

    defection_count = 0
    for guest_id in guest_ids:
        try:
            message_payload = None
            processed = False

            with transaction.atomic():
                # 先只读定位父行，持有 Manor 锁前不得锁 Guest。
                manor_id = Guest.objects.filter(id=guest_id).values_list("manor_id", flat=True).first()
                if manor_id is None:
                    continue
                locked_manor = Manor.objects.select_for_update().filter(pk=manor_id).first()
                if locked_manor is None:
                    continue
                guest = Guest.objects.select_for_update().filter(id=guest_id, manor_id=locked_manor.pk).first()
                if guest is None:
                    continue
                guest.manor = locked_manor

                defection, created = GuestDefection.objects.get_or_create(
                    guest_id=guest.id,
                    defaults={
                        "manor": locked_manor,
                        "guest_name": guest.display_name,
                        "guest_level": guest.level,
                        "guest_rarity": guest.rarity,
                        "loyalty_at_defection": guest.loyalty,
                    },
                )
                if created:
                    message_payload = _build_defection_message_payload(guest)
                else:
                    logger.warning(
                        "Guest defection already recorded; deleting lingering guest record: "
                        "guest_id=%d defection_id=%d",
                        guest.id,
                        defection.id,
                    )

                guest.delete()
                processed = True

            if message_payload is not None:
                try:
                    create_message(**message_payload)
                except GUEST_DEFECTION_MESSAGE_EXCEPTIONS:
                    logger.exception("Failed to send defection message for guest %d", guest_id)

            if processed:
                defection_count += 1
        except GUEST_TASK_RETRY_EXCEPTIONS:
            # Per-guest defection failure should not abort the batch; the guest
            # will remain with low loyalty and be retried on the next daily run.
            logger.exception("Failed to process defection for guest %d", guest_id)

    return defection_count
