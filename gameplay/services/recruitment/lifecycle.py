"""
护院募兵生命周期服务。

将调度、完成结算、战斗模板补建等与配置/校验逻辑分离。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import MessageError
from core.exceptions.recruitment_extended import (
    TroopRecruitmentNotFoundError,
    TroopRecruitmentNotReadyError,
    TroopTemplateNotFoundError,
)
from core.utils import safe_int, safe_non_negative_int, safe_positive_int
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS, NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS

from ...models import PlayerTroop, TroopRecruitment

if TYPE_CHECKING:
    from battle.models import TroopTemplate

logger = logging.getLogger(__name__)


def schedule_recruitment_completion(recruitment: TroopRecruitment, eta_seconds: int) -> None:
    """
    在事务提交后调度募兵完成任务。
    """
    from django.db import transaction as db_transaction

    countdown = max(0, int(eta_seconds))

    complete_troop_recruitment: object
    try:
        from gameplay.tasks import complete_troop_recruitment
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Unable to import complete_troop_recruitment task; skip scheduling", exc_info=True)
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_troop_recruitment,
            args=[recruitment.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_troop_recruitment dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_troop_recruitment dispatch returned False; recruitment may remain recruiting",
                extra={
                    "task_name": "complete_troop_recruitment",
                    "recruitment_id": getattr(recruitment, "id", None),
                    "manor_id": getattr(recruitment, "manor_id", None),
                    "troop_key": getattr(recruitment, "troop_key", None),
                },
            )

    db_transaction.on_commit(_dispatch_completion)


def _get_or_create_battle_troop_template(recruitment: TroopRecruitment) -> TroopTemplate | None:
    """获取战斗兵种模板，缺失时按募兵配置自动补建。"""
    from battle.models import TroopTemplate

    from .recruitment import get_troop_template

    troop_template = TroopTemplate.objects.filter(key=recruitment.troop_key).first()
    if troop_template:
        return troop_template

    troop_config = get_troop_template(recruitment.troop_key)
    if not troop_config:
        logger.error("Troop template config not found: %s", recruitment.troop_key)
        return None

    defaults = {
        "name": str(troop_config.get("name") or recruitment.troop_name or recruitment.troop_key),
        "description": str(troop_config.get("description") or ""),
        "base_attack": safe_non_negative_int(troop_config.get("base_attack"), 30),
        "base_defense": safe_non_negative_int(troop_config.get("base_defense"), 20),
        "base_hp": safe_non_negative_int(troop_config.get("base_hp"), 80),
        "speed_bonus": safe_non_negative_int(troop_config.get("speed_bonus"), 10),
        "priority": safe_int(troop_config.get("priority"), default=0) or 0,
        "default_count": safe_positive_int(troop_config.get("default_count"), 120),
    }

    troop_template, created = TroopTemplate.objects.get_or_create(key=recruitment.troop_key, defaults=defaults)
    if created:
        logger.warning(
            "Auto-created missing TroopTemplate for recruitment: key=%s recruitment_id=%s",
            recruitment.troop_key,
            recruitment.id,
        )
    return troop_template


def finalize_troop_recruitment(recruitment: TroopRecruitment, send_notification: bool = False) -> None:
    """
    完成募兵，将护院添加到玩家存储。

    Raises:
        TroopRecruitmentNotFoundError: 募兵记录不存在
        TroopRecruitmentNotReadyError: 募兵尚未完成或状态不正确
        TroopTemplateNotFoundError: 战斗兵种模板不存在
    """
    with transaction.atomic():
        locked_recruitment = (
            TroopRecruitment.objects.select_for_update()
            .select_related("manor", "manor__user")
            .filter(pk=recruitment.pk)
            .first()
        )
        if not locked_recruitment:
            raise TroopRecruitmentNotFoundError(recruitment_id=recruitment.pk)

        if locked_recruitment.status != TroopRecruitment.Status.RECRUITING:
            raise TroopRecruitmentNotReadyError(
                complete_at=str(locked_recruitment.complete_at),
                message=f"募兵状态不正确: status={locked_recruitment.status}",
            )

        if locked_recruitment.complete_at > timezone.now():
            raise TroopRecruitmentNotReadyError(complete_at=str(locked_recruitment.complete_at))

        troop_template = _get_or_create_battle_troop_template(locked_recruitment)
        if not troop_template:
            raise TroopTemplateNotFoundError(troop_key=locked_recruitment.troop_key)

        troop_lookup = {
            "manor": locked_recruitment.manor,
            "troop_template": troop_template,
        }
        inventory_updated_at = timezone.now()
        updated = PlayerTroop.objects.filter(**troop_lookup).update(
            count=F("count") + locked_recruitment.quantity,
            updated_at=inventory_updated_at,
        )
        if not updated:
            try:
                with transaction.atomic():
                    PlayerTroop.objects.create(
                        **troop_lookup,
                        count=locked_recruitment.quantity,
                    )
            except IntegrityError:
                PlayerTroop.objects.filter(**troop_lookup).update(
                    count=F("count") + locked_recruitment.quantity,
                    updated_at=inventory_updated_at,
                )

        locked_recruitment.status = TroopRecruitment.Status.COMPLETED
        locked_recruitment.finished_at = timezone.now()
        locked_recruitment.save(update_fields=["status", "finished_at"])
        recruitment = locked_recruitment

    if send_notification:
        from ...models import Message
        from ..utils.messages import create_message
        from ..utils.notifications import notify_user

        quantity_text = f"x{recruitment.quantity}" if recruitment.quantity > 1 else ""
        try:
            create_message(
                manor=recruitment.manor,
                kind=Message.Kind.SYSTEM,
                title=f"{recruitment.troop_name}{quantity_text}募兵完成",
                body=f"您的{recruitment.troop_name}{quantity_text}已募兵完成。",
            )
        except MessageError as exc:
            logger.warning(
                "troop recruitment message creation failed: recruitment_id=%s manor_id=%s error=%s",
                recruitment.id,
                recruitment.manor_id,
                exc,
                exc_info=True,
            )
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "troop recruitment message creation failed: recruitment_id=%s manor_id=%s error=%s",
                recruitment.id,
                recruitment.manor_id,
                exc,
                exc_info=True,
            )

        try:
            notify_user(
                recruitment.manor.user_id,
                {
                    "kind": "system",
                    "title": f"{recruitment.troop_name}{quantity_text}募兵完成",
                    "troop_key": getattr(recruitment, "troop_key", None),
                    "quantity": getattr(recruitment, "quantity", None),
                },
                log_context="troop recruitment notification",
            )
        except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "troop recruitment notification failed: recruitment_id=%s manor_id=%s error=%s",
                recruitment.id,
                recruitment.manor_id,
                exc,
                exc_info=True,
            )
