from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest

from gameplay.services.manor.core import project_manor_activity_for_read
from gameplay.views.read_helpers import get_prepared_manor_for_read


def get_prepared_guest_roster_for_read(
    request: HttpRequest,
    *,
    logger: Any,
    source: str,
    available_guests_fn: Callable[[Any], Any],
) -> tuple[Any, list[Any]]:
    manor = get_prepared_manor_for_read(
        request,
        project_fn=project_manor_activity_for_read,
        logger=logger,
        source=source,
    )
    guests = list(available_guests_fn(manor))

    # 返程中的门客仍被出征记录占用。读取旧数据或状态同步延迟时，
    # 以未归队的踢馆、赛季派遣作为展示边界，避免把他们误列为空闲。
    from gameplay.models import RaidRun, SeasonExpedition
    from guests.models import GuestStatus

    active_raid_guest_ids = set(
        RaidRun.objects.filter(
            attacker=manor,
            status__in=(
                RaidRun.Status.MARCHING,
                RaidRun.Status.BATTLING,
                RaidRun.Status.RETURNING,
                RaidRun.Status.RETREATED,
            ),
            guests__isnull=False,
            guests__in=[guest.pk for guest in guests],
        ).values_list("guests__pk", flat=True)
    )
    active_raid_guest_ids.update(
        SeasonExpedition.objects.filter(member__manor=manor, guests__in=[guest.pk for guest in guests])
        .exclude(status="completed")
        .values_list("guests__pk", flat=True)
    )
    for guest in guests:
        if guest.pk in active_raid_guest_ids:
            guest.status = GuestStatus.DEPLOYED
    return manor, guests


def get_prepared_guest_detail_for_read(
    request: HttpRequest,
    guest_pk: int,
    *,
    logger: Any,
    source: str,
    load_guest_detail_fn: Callable[[Any, int], Any],
) -> tuple[Any, Any]:
    manor = get_prepared_manor_for_read(
        request,
        project_fn=project_manor_activity_for_read,
        logger=logger,
        source=source,
    )
    return manor, load_guest_detail_fn(manor, guest_pk)
