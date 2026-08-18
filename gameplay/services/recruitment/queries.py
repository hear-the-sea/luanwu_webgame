"""
护院募兵查询服务。

负责读取进行中的募兵记录与玩家当前护院数据。
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from core.exceptions import TroopCapacityFullError

from ...models import Manor, PlayerTroop, TroopRecruitment


def refresh_troop_recruitments(manor: Manor) -> int:
    """
    刷新募兵状态，完成所有到期的募兵。
    """
    from core.exceptions.recruitment_extended import (
        TroopRecruitmentNotFoundError,
        TroopRecruitmentNotReadyError,
        TroopTemplateNotFoundError,
    )

    from .lifecycle import finalize_troop_recruitment

    completed = 0
    recruiting = manor.troop_recruitments.filter(
        status=TroopRecruitment.Status.RECRUITING,
        complete_at__lte=timezone.now(),
    )

    for recruitment in recruiting:
        try:
            finalize_troop_recruitment(recruitment, send_notification=True)
            completed += 1
        except (
            TroopRecruitmentNotFoundError,
            TroopRecruitmentNotReadyError,
            TroopTemplateNotFoundError,
            TroopCapacityFullError,
        ):
            # Skip recruitments that can't be finalized
            pass

    return completed


def get_active_recruitments(manor: Manor) -> list[TroopRecruitment]:
    """
    获取正在进行的募兵列表。
    """
    return list(manor.troop_recruitments.filter(status=TroopRecruitment.Status.RECRUITING).order_by("complete_at"))


def get_player_troops(manor: Manor) -> list[dict[str, Any]]:
    """
    获取玩家已拥有的护院列表（count > 0）。
    """
    troops = PlayerTroop.objects.filter(manor=manor, count__gt=0).select_related("troop_template")

    result: list[dict[str, Any]] = []
    for pt in troops:
        template = pt.troop_template
        avatar_url = template.avatar.url if template.avatar else ""
        result.append(
            {
                "key": template.key,
                "name": template.name,
                "description": template.description,
                "count": pt.count,
                "base_attack": template.base_attack,
                "base_defense": template.base_defense,
                "base_hp": template.base_hp,
                "speed_bonus": template.speed_bonus,
                "avatar": avatar_url,
            }
        )

    return result
