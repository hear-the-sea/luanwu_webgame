# guilds/tasks.py
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from common.utils import celery as celery_utils
from common.utils.celery import safe_apply_async_with_dedup
from core.utils.atomic_cache import clear_local_int_id_set_fallback, get_int_id_set, merge_int_id_set
from core.utils.infrastructure import (
    CACHE_INFRASTRUCTURE_EXCEPTIONS,
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.task_monitoring import increment_degraded_counter
from gameplay.record_retention import MAX_RECORD_RETENTION_DAYS
from gameplay.tasks._scheduled import DEFAULT_TASK_DEDUP_TIMEOUT, maybe_reschedule_for_future

from .models import (
    Guild,
    GuildCavalryTraining,
    GuildDonationLog,
    GuildExchangeLog,
    GuildMissionRun,
    GuildRaidRun,
    GuildResourceLog,
)
from .services.contribution import reset_weekly_contributions
from .services.guild_missions import finalize_guild_mission_run
from .services.guild_raids import finalize_guild_raid, process_due_guild_raid, process_guild_raid_battle
from .services.hero_pool import cleanup_invalid_hero_pool_entries
from .services.technology_production import process_guild_technology_production

logger = logging.getLogger(__name__)
GUILD_PRODUCTION_PARTIAL_RETRY_LIMIT = 1
GUILD_LOG_CLEANUP_BATCH_SIZE = 1_000
FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY = "guilds:daily_production:failed_guild_ids"
GUILD_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    *celery_utils.CELERY_DISPATCH_INFRA_EXCEPTIONS,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


@shared_task(name="guilds.complete_cavalry_training", bind=True, max_retries=3, default_retry_delay=30)
def complete_guild_cavalry_training(self, guild_id: int):
    from .services.cavalry import finalize_guild_cavalry_training

    try:
        return finalize_guild_cavalry_training(guild_id)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        raise self.retry(exc=exc)


@shared_task(name="guilds.scan_cavalry_trainings")
def scan_guild_cavalry_trainings(limit: int = 200):
    from .services.cavalry import finalize_guild_cavalry_training

    guild_ids = list(
        GuildCavalryTraining.objects.filter(
            completed_at__isnull=True, complete_at__lte=timezone.now(), guild__is_active=True
        )
        .order_by("complete_at")
        .values_list("guild_id", flat=True)[:limit]
    )
    completed = 0
    for guild_id in guild_ids:
        try:
            completed += bool(finalize_guild_cavalry_training(guild_id))
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
            logger.exception("Failed to complete guild cavalry training: guild_id=%s", guild_id)
    return completed


class GuildMissionTaskRetryRequested(RuntimeError):
    """帮会任务补调度失败时的显式重试标记。"""


GUILD_MISSION_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    GuildMissionTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


class GuildRaidTaskRetryRequested(RuntimeError):
    """帮会掠夺补调度失败时的显式重试标记。"""


GUILD_RAID_TASK_RETRY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    GuildRaidTaskRetryRequested,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def _normalize_failed_guild_ids(failed_ids) -> list[int]:
    normalized_ids: list[int] = []
    for guild_id in failed_ids or []:
        try:
            normalized = int(guild_id)
        except (TypeError, ValueError):
            continue
        if normalized not in normalized_ids:
            normalized_ids.append(normalized)
    return normalized_ids


def _persist_failed_guild_ids(failed_guild_ids: list[int]) -> None:
    normalized_ids = _normalize_failed_guild_ids(failed_guild_ids)
    if not normalized_ids:
        return

    try:
        merge_int_id_set(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY, normalized_ids, ttl=None)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to persist failed guild production IDs", exc_info=True)


def _clear_failed_guild_ids() -> None:
    clear_local_int_id_set_fallback(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY)
    try:
        cache.delete(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to clear failed guild production IDs", exc_info=True)


def get_failed_guild_ids() -> list[int]:
    try:
        return get_int_id_set(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY, ttl=None)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to read failed guild production IDs", exc_info=True)
        return []


def _load_guild_mission_run_for_completion(run_id: int) -> GuildMissionRun | None:
    return GuildMissionRun.objects.select_related("guild", "template", "started_by__user").filter(pk=run_id).first()


def _reschedule_guild_mission_if_needed(run: GuildMissionRun, *, run_id: int) -> tuple[str | None, object]:
    failure_message = f"guild mission reschedule dispatch failed: run_id={run_id}"
    try:
        return maybe_reschedule_for_future(
            task_func=complete_guild_mission_task,
            record_id=run_id,
            eta_value=run.return_at,
            dedup_key=f"guild_mission:complete:{run_id}",
            schedule_func=safe_apply_async_with_dedup,
            logger=logger,
            now_func=timezone.now,
            log_message=f"guild mission reschedule failed: run_id={run_id}",
            failure_message=failure_message,
            dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
        )
    except RuntimeError as exc:
        raise GuildMissionTaskRetryRequested(str(exc)) from exc


def _load_guild_raid_run_for_completion(run_id: int) -> GuildRaidRun | None:
    return (
        GuildRaidRun.objects.select_related(
            "attacker_guild",
            "attacker_guild__founder",
            "defender_guild",
            "defender_guild__founder",
            "started_by__user",
        )
        .filter(pk=run_id)
        .first()
    )


def _reschedule_guild_raid_if_needed(run: GuildRaidRun, *, run_id: int) -> tuple[str | None, object]:
    eta_value = run.battle_at if run.status == GuildRaidRun.Status.MARCHING else run.return_at
    failure_message = f"guild raid reschedule dispatch failed: run_id={run_id}"
    try:
        return maybe_reschedule_for_future(
            task_func=complete_guild_raid_task,
            record_id=run_id,
            eta_value=eta_value,
            dedup_key=f"guild_raid:complete:{run_id}",
            schedule_func=safe_apply_async_with_dedup,
            logger=logger,
            now_func=timezone.now,
            log_message=f"guild raid reschedule failed: run_id={run_id}",
            failure_message=failure_message,
            dedup_timeout=DEFAULT_TASK_DEDUP_TIMEOUT,
        )
    except RuntimeError as exc:
        raise GuildRaidTaskRetryRequested(str(exc)) from exc


@shared_task(name="guilds.complete_guild_mission", bind=True, max_retries=2, default_retry_delay=30)
def complete_guild_mission_task(self, run_id: int):
    try:
        run = _load_guild_mission_run_for_completion(run_id)
        if not run:
            logger.warning("GuildMissionRun %d not found", run_id)
            return "not_found"
        if run.status == GuildMissionRun.Status.FAILED:
            return "already_failed"

        rescheduled, now = _reschedule_guild_mission_if_needed(run, run_id=run_id)
        if rescheduled is not None:
            return rescheduled

        finalized = finalize_guild_mission_run(run, now=now)
        if not finalized:
            if GuildMissionRun.objects.filter(pk=run_id, status=GuildMissionRun.Status.ACTIVE).exists():
                raise GuildMissionTaskRetryRequested(
                    f"guild mission run remains active after finalization no-op: run_id={run_id}"
                )
            if GuildMissionRun.objects.filter(pk=run_id, status=GuildMissionRun.Status.FAILED).exists():
                return "already_failed"
            return "already_completed"
        if GuildMissionRun.objects.filter(pk=run_id, status=GuildMissionRun.Status.FAILED).exists():
            return "failed"
        return "completed"
    except GUILD_MISSION_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete guild mission %d: %s", run_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="guilds.scan_due_missions")
def scan_due_guild_missions(limit: int = 200) -> int:
    now = timezone.now()
    qs = (
        GuildMissionRun.objects.select_related("guild", "guild__founder", "template", "started_by__user")
        .filter(
            status=GuildMissionRun.Status.ACTIVE,
            return_at__isnull=False,
            return_at__lte=now,
        )
        .order_by("return_at", "id")[:limit]
    )
    count = 0
    for run in qs:
        try:
            if finalize_guild_mission_run(run, now=now):
                count += 1
        except GUILD_MISSION_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize guild mission run %d", run.id)
    return count


@shared_task(name="guilds.complete_guild_raid", bind=True, max_retries=2, default_retry_delay=30)
def complete_guild_raid_task(self, run_id: int):
    try:
        run = _load_guild_raid_run_for_completion(run_id)
        if not run:
            logger.warning("GuildRaidRun %d not found", run_id)
            return "not_found"
        if run.status == GuildRaidRun.Status.COMPLETED:
            return "already_completed"
        if run.status == GuildRaidRun.Status.FAILED:
            return "already_failed"

        rescheduled, now = _reschedule_guild_raid_if_needed(run, run_id=run_id)
        if rescheduled is not None:
            return rescheduled

        if run.status == GuildRaidRun.Status.MARCHING:
            processed = process_guild_raid_battle(run, now=now)
            if not processed:
                if GuildRaidRun.objects.filter(pk=run_id, status=run.status).exists():
                    raise GuildRaidTaskRetryRequested(
                        f"guild raid run remains {run.status} after battle no-op: run_id={run_id}"
                    )
                return "already_processed"
            return "completed"

        finalized = finalize_guild_raid(run, now=now)
        if not finalized:
            if GuildRaidRun.objects.filter(pk=run_id, status=run.status).exists():
                raise GuildRaidTaskRetryRequested(
                    f"guild raid run remains {run.status} after finalization no-op: run_id={run_id}"
                )
            return "already_processed"
        return "completed"
    except GUILD_RAID_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to complete guild raid %d: %s", run_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="guilds.scan_due_raids")
def scan_due_guild_raids(limit: int = 200) -> int:
    now = timezone.now()
    qs = (
        GuildRaidRun.objects.select_related(
            "attacker_guild",
            "attacker_guild__founder",
            "defender_guild",
            "defender_guild__founder",
            "started_by__user",
        )
        .filter(
            Q(status=GuildRaidRun.Status.MARCHING, battle_at__isnull=False, battle_at__lte=now)
            | Q(
                status__in=[GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED],
                return_at__isnull=False,
                return_at__lte=now,
            )
        )
        .order_by("battle_at", "return_at", "id")[:limit]
    )
    count = 0
    for run in qs:
        try:
            if process_due_guild_raid(run, now=now):
                count += 1
        except GUILD_RAID_TASK_RETRY_EXCEPTIONS:
            logger.exception("Failed to finalize guild raid run %d", run.id)
    return count


def _process_guild_production_once(guild_id: int) -> tuple[str, bool]:
    result = process_guild_technology_production(guild_id, now=timezone.now())
    return f"processed guild {guild_id}: {', '.join(result.due_tech_keys)}", False


def _handle_guild_production_partial_failure(failed_guild_ids: list[int], retry_attempt: int) -> None:
    if not failed_guild_ids:
        return

    logger.error(
        "batch partial failure",
        extra={
            "task": "guilds.process_single_guild_production",
            "failed_ids": failed_guild_ids,
            "degraded": True,
        },
    )
    increment_degraded_counter("guilds_production")

    if retry_attempt >= GUILD_PRODUCTION_PARTIAL_RETRY_LIMIT:
        return

    dispatched = celery_utils.safe_apply_async(
        process_single_guild_production,
        args=[None, failed_guild_ids, retry_attempt + 1],
        logger=logger,
        log_message="Failed to dispatch guild production partial failure retry",
    )
    if dispatched:
        _clear_failed_guild_ids()
        return

    _persist_failed_guild_ids(failed_guild_ids)


@shared_task(name="guilds.process_single_guild_production", bind=True, max_retries=3, default_retry_delay=60)
def process_single_guild_production(self, guild_id: int | None = None, failed_ids=None, retry_attempt: int = 0):
    """
    处理单个帮会的每日科技产出
    """
    normalized_failed_ids = _normalize_failed_guild_ids(failed_ids)
    if normalized_failed_ids:
        failed_guild_ids: list[int] = []
        processed_count = 0

        for failed_guild_id in normalized_failed_ids:
            try:
                _summary, partial_failure = _process_guild_production_once(failed_guild_id)
                processed_count += 1
                if partial_failure:
                    failed_guild_ids.append(failed_guild_id)
            except Guild.DoesNotExist:
                logger.warning("Guild %s not found during daily production", failed_guild_id)
            except GUILD_TASK_RETRY_EXCEPTIONS as exc:
                logger.exception("Failed to process guild %s production: %s", failed_guild_id, exc)
                raise self.retry(exc=exc)

        if failed_guild_ids:
            _handle_guild_production_partial_failure(failed_guild_ids, retry_attempt)

        summary = f"processed {processed_count} guilds"
        if failed_guild_ids:
            summary = f"{summary}; failed_guild_ids={failed_guild_ids}"
        return summary

    try:
        if guild_id is None:
            raise AssertionError("guild_id is required when failed_ids is empty")

        summary, partial_failure = _process_guild_production_once(int(guild_id))
        if partial_failure:
            _handle_guild_production_partial_failure([int(guild_id)], retry_attempt)
        return summary
    except Guild.DoesNotExist:
        logger.warning("Guild %s not found during daily production", guild_id)
        return "guild not found"
    except GUILD_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to process guild %s production: %s", guild_id, exc)
        raise self.retry(exc=exc)


@shared_task(name="guilds.tech_daily_production", bind=True, max_retries=2, default_retry_delay=60)
def guild_tech_daily_production(self):
    """
    每日帮会科技产出（Master任务）
    执行时间：每天00:00（UTC+8）
    采用 Fan-out 模式分发任务，避免单次任务超时
    """
    try:
        dispatched_count = 0

        # Stream IDs in bounded chunks so a large guild population does not
        # remain resident in the master task's Python heap.
        guild_ids = Guild.objects.filter(is_active=True).values_list("id", flat=True).iterator(chunk_size=500)
        for guild_id in guild_ids:
            dispatched = celery_utils.safe_apply_async(
                process_single_guild_production,
                args=[guild_id],
                logger=logger,
                log_message=f"Failed to dispatch daily production for guild {guild_id}",
                raise_on_failure=True,
            )
            if dispatched:
                dispatched_count += 1

        return f"dispatched {dispatched_count} guild tasks"
    except GUILD_TASK_RETRY_EXCEPTIONS as exc:
        logger.exception("Failed to run guild tech daily production master task: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="guilds.reset_weekly_stats", bind=True, max_retries=2, default_retry_delay=60)
def reset_guild_weekly_stats(self):
    """
    重置帮会每周统计
    执行时间：每周一00:00（UTC+8）
    """
    try:
        reset_weekly_contributions()
        return "reset completed"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("Failed to reset guild weekly stats: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="guilds.cleanup_old_logs", bind=True, max_retries=2, default_retry_delay=60)
def cleanup_old_guild_logs(self):
    """
    清理旧的帮会日志
    执行时间：每小时
    最多保留最近15天的日志

    每张表单单次最多删除 GUILD_LOG_CLEANUP_BATCH_SIZE 条；积压记录由后续小时任务继续清理。
    """
    try:
        cutoff_date = timezone.now() - timedelta(days=MAX_RECORD_RETENTION_DAYS)

        donation_deleted = _delete_expired_guild_log_batch(GuildDonationLog, "donated_at", cutoff_date)
        exchange_deleted = _delete_expired_guild_log_batch(GuildExchangeLog, "exchanged_at", cutoff_date)
        resource_deleted = _delete_expired_guild_log_batch(GuildResourceLog, "created_at", cutoff_date)

        total = donation_deleted + exchange_deleted + resource_deleted
        logger.info(
            "Cleaned up %d old guild logs (donation=%d, exchange=%d, resource=%d)",
            total,
            donation_deleted,
            exchange_deleted,
            resource_deleted,
        )
        return f"cleaned up {total} logs"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("Failed to cleanup old guild logs: %s", exc)
        raise self.retry(exc=exc)


def _delete_expired_guild_log_batch(model: type[Any], time_field: str, cutoff) -> int:
    filter_kwargs = {f"{time_field}__lt": cutoff}
    ids = list(
        model.objects.filter(**filter_kwargs)
        .order_by(time_field, "pk")
        .values_list("pk", flat=True)[:GUILD_LOG_CLEANUP_BATCH_SIZE]
    )
    if not ids:
        return 0
    _deleted_total, details = model.objects.filter(pk__in=ids).delete()
    return int(details.get(model._meta.label, 0))


@shared_task(name="guilds.cleanup_invalid_hero_pool", bind=True, max_retries=2, default_retry_delay=60)
def cleanup_invalid_guild_hero_pool(self):
    """
    清理无效帮会门客池条目（不再拥有门客/离帮/脏数据，级联下阵）。
    执行时间：每5分钟
    """
    try:
        cleaned = cleanup_invalid_hero_pool_entries(limit=1000)
        if cleaned:
            logger.info("Cleaned %d invalid guild hero pool entries", cleaned)
        return f"cleaned {cleaned} invalid hero pool entries"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("Failed to cleanup invalid guild hero pool entries: %s", exc)
        raise self.retry(exc=exc)
