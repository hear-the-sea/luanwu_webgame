"""
护院募兵查询服务。

负责读取进行中的募兵记录与玩家当前护院数据。
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TroopCapacityFullError
from core.game_data.technology import troop_display_sort_key

from ...models import Manor, PlayerTroop, TroopBankStorage, TroopRecruitment


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


def _build_troop_row(template: TroopTemplate, count: int) -> dict[str, Any]:
    return {
        "key": template.key,
        "name": template.name,
        "description": template.description,
        "count": count,
        "base_attack": template.base_attack,
        "base_defense": template.base_defense,
        "base_hp": template.base_hp,
        "speed_bonus": template.speed_bonus,
        "avatar": template.avatar.url if template.avatar else "",
    }


def get_player_troops(manor: Manor, *, include_bank: bool = False) -> list[dict[str, Any]]:
    """
    获取玩家已拥有的护院列表（count > 0），出征时可合计钱庄护院。
    """
    troops = PlayerTroop.objects.filter(manor=manor, count__gt=0).select_related("troop_template")

    rows_by_key: dict[str, dict[str, Any]] = {}
    for pt in troops:
        template = pt.troop_template
        rows_by_key[template.key] = _build_troop_row(template, pt.count)
        if include_bank:
            rows_by_key[template.key].update(manor_count=pt.count, bank_count=0)

    if include_bank:
        bank_troops = TroopBankStorage.objects.filter(manor=manor, count__gt=0).select_related("troop_template")
        for bank_troop in bank_troops:
            template = bank_troop.troop_template
            if template.key not in rows_by_key:
                rows_by_key[template.key] = _build_troop_row(template, 0)
                rows_by_key[template.key].update(manor_count=0, bank_count=0)
            row = rows_by_key[template.key]
            row["bank_count"] = bank_troop.count
            row["count"] += bank_troop.count

    return sorted(rows_by_key.values(), key=lambda row: troop_display_sort_key(row["key"]))
