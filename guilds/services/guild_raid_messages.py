from __future__ import annotations

import logging
from typing import Any

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services.utils.messages import bulk_create_messages
from gameplay.utils.template_loader import get_item_template_names_by_keys

from ..models import GuildMember, GuildRaidRun

logger = logging.getLogger(__name__)


def _format_duration(seconds: int) -> str:
    total_seconds = max(0, int(seconds or 0))
    minutes, remain_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if remain_seconds > 0 or not parts:
        parts.append(f"{remain_seconds}秒")
    return "".join(parts)


def _format_item_summary(items: dict[str, int]) -> str:
    if not items:
        return "无"
    item_names = get_item_template_names_by_keys(items.keys())
    return "、".join(
        f"{item_names.get(item_key, '未知物品')} ×{quantity}" for item_key, quantity in sorted(items.items())
    )


def send_guild_raid_warning_messages(run: GuildRaidRun) -> None:
    defender_member_user_ids = list(
        GuildMember.objects.filter(guild=run.defender_guild, is_active=True).values_list("user_id", flat=True)
    )
    if not defender_member_user_ids:
        return

    manor_by_user_id = {manor.user_id: manor for manor in Manor.objects.filter(user_id__in=defender_member_user_ids)}
    travel_text = _format_duration(int(run.travel_time or 0))
    payloads = [
        {
            "manor": manor_by_user_id[user_id],
            "kind": "system",
            "title": "帮会来袭预警",
            "body": f"{run.attacker_guild.name} 已出征来袭，预计 {travel_text} 后抵达。",
        }
        for user_id in defender_member_user_ids
        if user_id in manor_by_user_id
    ]
    if not payloads:
        return
    try:
        bulk_create_messages(payloads)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Guild raid warning delivery failed: run_id=%s attacker_id=%s defender_id=%s recipients=%s",
            run.id,
            run.attacker_guild_id,
            run.defender_guild_id,
            len(payloads),
        )


def _build_message_payloads(run: GuildRaidRun, report: Any) -> list[dict[str, Any]]:
    attacker_member_user_ids = list(
        GuildMember.objects.filter(guild=run.attacker_guild, is_active=True).values_list("user_id", flat=True)
    )
    defender_member_user_ids = list(
        GuildMember.objects.filter(guild=run.defender_guild, is_active=True).values_list("user_id", flat=True)
    )
    manor_by_user_id = {
        manor.user_id: manor
        for manor in Manor.objects.filter(user_id__in=attacker_member_user_ids + defender_member_user_ids)
    }
    attacker_title = "帮会掠夺战报 - 进攻胜利" if run.is_attacker_victory else "帮会掠夺战报 - 进攻失利"
    defender_title = "帮会掠夺战报 - 防守失利" if run.is_attacker_victory else "帮会掠夺战报 - 防守成功"
    attacker_body = (
        f"目标：{run.defender_guild.name}\n"
        f"银两：{run.loot_silver}\n"
        f"物资：{_format_item_summary(dict(run.loot_items or {}))}"
    )
    defender_body = (
        f"来犯：{run.attacker_guild.name}\n"
        f"损失银两：{run.loot_silver}\n"
        f"损失物资：{_format_item_summary(dict(run.loot_items or {}))}"
    )

    payloads: list[dict[str, Any]] = []
    for user_id in attacker_member_user_ids:
        manor = manor_by_user_id.get(user_id)
        if manor is None:
            continue
        payloads.append(
            {
                "manor": manor,
                "kind": "battle",
                "title": attacker_title,
                "body": attacker_body,
                "battle_report": report,
            }
        )
    for user_id in defender_member_user_ids:
        manor = manor_by_user_id.get(user_id)
        if manor is None:
            continue
        payloads.append(
            {
                "manor": manor,
                "kind": "battle",
                "title": defender_title,
                "body": defender_body,
                "battle_report": report,
            }
        )
    return payloads


def send_guild_raid_report_messages(run: GuildRaidRun, report: Any) -> None:
    payloads = _build_message_payloads(run, report)
    if not payloads:
        return
    try:
        bulk_create_messages(payloads)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Guild raid report delivery failed: run_id=%s attacker_id=%s defender_id=%s recipients=%s",
            run.id,
            run.attacker_guild_id,
            run.defender_guild_id,
            len(payloads),
        )
