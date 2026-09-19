from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from common.utils.celery import safe_apply_async
from core.exceptions import MessageError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS, NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.utils.messages import create_message
from gameplay.services.utils.notifications import notify_user

if TYPE_CHECKING:
    from gameplay.models import Manor

    from ..models import GuestRecruitment, RecruitmentPool


def schedule_guest_recruitment_completion(
    recruitment: GuestRecruitment,
    eta_seconds: int,
    *,
    logger: logging.Logger,
) -> None:
    if eta_seconds is None or isinstance(eta_seconds, bool):
        raise AssertionError(f"invalid guest recruitment completion eta: {eta_seconds!r}")
    try:
        countdown = int(eta_seconds)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid guest recruitment completion eta: {eta_seconds!r}") from exc
    if countdown < 0:
        raise AssertionError(f"invalid guest recruitment completion eta: {eta_seconds!r}")
    try:
        from guests.tasks import complete_guest_recruitment
    except ImportError as exc:
        if not is_missing_target_import(exc, "guests.tasks"):
            raise
        logger.warning("Unable to import complete_guest_recruitment task; skip scheduling", exc_info=True)
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_guest_recruitment,
            args=[recruitment.id],
            countdown=countdown,
            defer_long_eta=True,
            logger=logger,
            log_message="complete_guest_recruitment dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_guest_recruitment dispatch returned False; recruitment may remain pending",
                extra={
                    "task_name": "complete_guest_recruitment",
                    "recruitment_id": recruitment.id,
                    "manor_id": recruitment.manor_id,
                    "pool_id": recruitment.pool_id,
                },
            )

    transaction.on_commit(_dispatch_completion)


def send_recruitment_completion_notification(
    *,
    manor: Manor,
    pool: RecruitmentPool,
    candidate_count: int,
    logger: logging.Logger,
    recruitment_id: int | None = None,
) -> None:
    from gameplay.models import Message

    title = f"{pool.name}招募完成"
    body = f"您的{pool.name}已完成，生成 {candidate_count} 名候选门客，请前往聚贤庄挑选。"
    try:
        create_message(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title=title,
            body=body,
        )
    except MessageError as exc:
        logger.warning(
            "guest recruitment message creation failed: recruitment_id=%s manor_id=%s error=%s",
            recruitment_id,
            getattr(manor, "id", None),
            exc,
            exc_info=True,
        )
        return
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "guest recruitment message creation failed: recruitment_id=%s manor_id=%s error=%s",
            recruitment_id,
            getattr(manor, "id", None),
            exc,
            exc_info=True,
        )
        return

    try:
        notify_user(
            manor.user_id,
            {
                "kind": "system",
                "title": title,
                "pool_key": pool.key,
                "candidate_count": candidate_count,
            },
            log_context="guest recruitment notification",
        )
    except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "guest recruitment notification failed: recruitment_id=%s user_id=%s error=%s",
            recruitment_id,
            getattr(manor, "user_id", None),
            exc,
            exc_info=True,
        )
