"""赛季周结算与到期归档扫描。"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

_freeze_notice_logged = False


def _season_gameplay_disabled() -> bool:
    from gameplay.services.season.availability import season_gameplay_enabled

    global _freeze_notice_logged
    if season_gameplay_enabled():
        return False
    if not _freeze_notice_logged:
        _freeze_notice_logged = True
        logger.info("season gameplay disabled; settlement and expedition tasks are frozen")
    return True


@shared_task(name="gameplay.settle_seasons")
def settle_seasons_task() -> dict[str, int]:
    if _season_gameplay_disabled():
        return {}

    from gameplay.services.season.expeditions import advance_due_expeditions
    from gameplay.services.season.lifecycle import advance_seasons

    processed = advance_due_expeditions()
    result = advance_seasons()
    result.update(processed)
    return result


@shared_task(name="gameplay.advance_season_expedition")
def advance_season_expedition_task(run_id: int, expected_due: str | None = None) -> bool:
    if _season_gameplay_disabled():
        return False

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    from gameplay.models import SeasonExpedition
    from gameplay.services.season.expeditions import _schedule, process_expedition
    from gameplay.services.season.warfare import ACTIVE_STATUSES

    processed = process_expedition(run_id)
    if not processed and expected_due:
        due = parse_datetime(expected_due)
        run = SeasonExpedition.objects.filter(pk=run_id, status__in=ACTIVE_STATUSES).first()
        if run and run.status != "failed" and due == run.due_at and run.due_at > timezone.now():
            _schedule(run)
    return processed
