from __future__ import annotations

import logging
from typing import Any, Literal

from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import MessageError
from core.utils import safe_non_negative_int
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.side_effects import DEFAULT_SIDE_EFFECT_EXCEPTIONS, schedule_best_effort_after_commit

from ..utils.messages import create_message
from . import scout_refresh as scout_refresh_command

logger = logging.getLogger(__name__)
SCOUT_MESSAGE_DELIVERY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)

ScoutFollowupAction = Literal[
    "detected_message",
    "failure_result_message",
    "retreat_result_message",
    "success_result_message",
]


def _normalize_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def log_scout_followup_failure(action: str, **context: Any) -> None:
    context_str = " ".join(f"{key}={value}" for key, value in context.items())
    if context_str:
        logger.warning("Scout %s follow-up failed: %s", action, context_str, exc_info=True)
    else:
        logger.warning("Scout %s follow-up failed", action, exc_info=True)


def send_scout_success_message(record: Any) -> None:
    """发送侦察成功消息。"""
    intel = _normalize_mapping(record.intel_data)
    troop_description = str(intel.get("troop_description") or "未知")
    guest_count = safe_non_negative_int(intel.get("guest_count", 0), 0)
    avg_guest_level = safe_non_negative_int(intel.get("avg_guest_level", 0), 0)
    asset_level = str(intel.get("asset_level") or "未知")

    body = f"""探子已成功潜入 {record.defender.display_name}，获取到以下情报：

【护院情况】{troop_description}
【门客数量】{guest_count} 人
【门客等级】平均 {avg_guest_level} 级
【资产状况】{asset_level}"""

    create_message(
        manor=record.attacker,
        kind="system",
        title=f"侦察报告 - {record.defender.display_name}",
        body=body,
    )


def send_scout_fail_message(record: Any) -> None:
    """发送侦察失败消息。"""
    body = f"""派往 {record.defender.display_name} 的探子被对方发现，侦察任务失败。
探子已损失。"""

    create_message(
        manor=record.attacker,
        kind="system",
        title="侦察失败",
        body=body,
    )


def send_scout_retreat_message(record: Any) -> None:
    """发送侦察撤退消息。"""
    body = f"""派往 {record.defender.display_name} 的探子已按命令撤回。
探子已安全返回，未获得情报。"""

    create_message(
        manor=record.attacker,
        kind="system",
        title="侦察已撤退",
        body=body,
    )


def send_scout_detected_message(record: Any) -> None:
    """发送被侦察警告消息给防守方。"""
    body = f"""我方巡逻队在庄园附近发现并抓获了一名探子！

探子来源：{record.attacker.display_name}
所在地区：{record.attacker.location_display}
发现时间：{timezone.now().strftime('%Y-%m-%d %H:%M:%S')}

建议加强防备，敌人可能即将来袭！"""

    create_message(
        manor=record.defender,
        kind="system",
        title="发现敌方探子！",
        body=body,
    )


def run_scout_followup(action: ScoutFollowupAction, record: Any, **context: Any) -> None:
    try:
        if action == "detected_message":
            send_scout_detected_message(record)
        elif action == "success_result_message":
            send_scout_success_message(record)
        elif action == "retreat_result_message":
            send_scout_retreat_message(record)
        else:
            send_scout_fail_message(record)
    except SCOUT_MESSAGE_DELIVERY_EXCEPTIONS:
        log_scout_followup_failure(action, **context)


def schedule_scout_followup(action: ScoutFollowupAction, record: Any, **context: Any) -> None:
    schedule_best_effort_after_commit(
        lambda: run_scout_followup(action, record, **context),
        logger=logger,
        log_message=(
            "Scout follow-up callback failed: "
            f"action={action} record_id={getattr(record, 'id', None)} "
            f"attacker={getattr(record, 'attacker_id', None)} defender={getattr(record, 'defender_id', None)}"
        ),
        expected_exceptions=DEFAULT_SIDE_EFFECT_EXCEPTIONS,
        degraded_component="scout_followup_callback",
    )


def dispatch_scout_task(
    task_name: str,
    *,
    countdown: int,
    record: Any,
    log_message: str,
    false_log_message: str,
) -> None:
    task: Any
    try:
        task = scout_refresh_command.resolve_scout_task(task_name)
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks.pvp"):
            raise
        logger.warning(
            "%s import failed: record_id=%s attacker=%s defender=%s error=%s",
            task_name,
            record.id,
            record.attacker_id,
            record.defender_id,
            exc,
            exc_info=True,
        )
        return

    dispatched = safe_apply_async(
        task,
        args=[record.id],
        countdown=countdown,
        logger=logger,
        log_message=log_message,
    )
    if dispatched:
        return
    logger.error(
        false_log_message,
        extra={
            "task_name": getattr(task, "name", str(task)),
            "record_id": record.id,
            "attacker_id": record.attacker_id,
            "defender_id": record.defender_id,
        },
    )


def _schedule_scout_dispatch_after_commit(
    task_name: str,
    *,
    countdown: int,
    record: Any,
    log_message: str,
    false_log_message: str,
) -> None:
    schedule_best_effort_after_commit(
        lambda: dispatch_scout_task(
            task_name,
            countdown=countdown,
            record=record,
            log_message=log_message,
            false_log_message=false_log_message,
        ),
        logger=logger,
        log_message=(
            f"{task_name} callback failed: "
            f"record_id={record.id} attacker={record.attacker_id} defender={record.defender_id}"
        ),
        expected_exceptions=DEFAULT_SIDE_EFFECT_EXCEPTIONS,
        degraded_component="scout_task_dispatch",
    )


def schedule_scout_completion(record: Any, countdown: int) -> None:
    _schedule_scout_dispatch_after_commit(
        "complete_scout_task",
        countdown=countdown,
        record=record,
        log_message="complete_scout_task dispatch failed",
        false_log_message="complete_scout_task dispatch returned False; scout may remain in outbound state",
    )


def schedule_scout_return_completion(record: Any, countdown: int) -> None:
    _schedule_scout_dispatch_after_commit(
        "complete_scout_return_task",
        countdown=countdown,
        record=record,
        log_message="complete_scout_return_task dispatch failed",
        false_log_message="complete_scout_return_task dispatch returned False; scout may remain returning",
    )


def schedule_scout_return_completion_after_retreat(record: Any, countdown: int) -> None:
    _schedule_scout_dispatch_after_commit(
        "complete_scout_return_task",
        countdown=countdown,
        record=record,
        log_message="complete_scout_return_task dispatch failed for retreat",
        false_log_message="complete_scout_return_task dispatch returned False after scout retreat; scout may remain returning",
    )
