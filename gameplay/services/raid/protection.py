"""
保护机制服务

提供新手保护、免战牌等功能。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from core.exceptions import PeaceShieldUnavailableError

from ...models import Manor
from .combat import get_active_raid_count, get_incoming_raids


def activate_peace_shield(manor: Manor, duration_seconds: int) -> None:
    """
    激活免战牌保护。

    Args:
        manor: 庄园
        duration_seconds: 保护时长（秒）

    Raises:
        PeaceShieldUnavailableError: 当前无法使用免战牌时
    """
    with transaction.atomic():
        manor = Manor.objects.select_for_update().get(pk=manor.pk)

        # 检查是否有出征中的队伍
        active_raids = get_active_raid_count(manor)
        if active_raids > 0:
            raise PeaceShieldUnavailableError("active_raids")

        # 检查是否有敌军来袭
        incoming = get_incoming_raids(manor)
        if incoming:
            raise PeaceShieldUnavailableError("incoming_raids")

        now = timezone.now()
        current_until = manor.peace_shield_until or now

        # 叠加时长
        if current_until > now:
            new_until = current_until + timedelta(seconds=duration_seconds)
        else:
            new_until = now + timedelta(seconds=duration_seconds)

        manor.peace_shield_until = new_until
        manor.save(update_fields=["peace_shield_until"])


def get_protection_status(manor: Manor) -> Dict[str, Any]:
    """
    获取庄园的保护状态。

    Returns:
        保护状态信息
    """

    def _format_remaining(seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        parts = []
        if hours > 0:
            parts.append(f"{hours}小时")
        if hours > 0 or minutes > 0:
            parts.append(f"{minutes}分钟")
        parts.append(f"{secs}秒")
        return "".join(parts)

    now = timezone.now()
    status: Dict[str, Any] = {
        "is_protected": manor.is_protected,
        "type": None,
        "type_display": "",
        "expires_at": None,
        "remaining_seconds": 0,
        "remaining_display": "",
        "newbie_protection": None,
        "defeat_protection": None,  # 保留旧接口键，当前不再提供自动战败免战
        "peace_shield": None,
    }
    active: List[Tuple[str, str, datetime, int]] = []

    if manor.is_under_newbie_protection:
        newbie_until: Optional[datetime] = manor.newbie_protection_until
        if newbie_until is not None:
            remaining = int((newbie_until - now).total_seconds())
            status["newbie_protection"] = {
                "until": newbie_until.isoformat(),
                "remaining_seconds": remaining,
            }
            active.append(("newbie_protection", "新手保护", newbie_until, remaining))

    if manor.is_under_peace_shield:
        shield_until: Optional[datetime] = manor.peace_shield_until
        if shield_until is not None:
            remaining = int((shield_until - now).total_seconds())
            status["peace_shield"] = {
                "until": shield_until.isoformat(),
                "remaining_seconds": remaining,
            }
            active.append(("peace_shield", "免战牌保护", shield_until, remaining))

    if active:
        # 选择到期时间最晚的那个作为“当前保护状态”的主显示
        active.sort(key=lambda x: x[2], reverse=True)
        kind, display, until, remaining = active[0]
        status["type"] = kind
        status["type_display"] = display
        status["expires_at"] = until.isoformat()
        status["remaining_seconds"] = max(0, int(remaining))
        status["remaining_display"] = _format_remaining(remaining)

    return status
