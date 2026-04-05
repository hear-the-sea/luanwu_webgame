from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from gameplay.constants import REGION_DICT

_STATUS_MARCHING = "marching"
_STATUS_BATTLING = "battling"
_STATUS_RETURNING = "returning"
_STATUS_RETREATED = "retreated"


@dataclass(frozen=True)
class GuildPvpRunDisplay:
    run: Any
    display_status_key: str
    display_status_label: str
    display_hint: str
    display_eta_at: datetime | None
    display_eta_label: str
    can_retreat: bool
    action_label: str
    action_kind: str


@dataclass(frozen=True)
class GuildPvpTargetCardDisplay:
    guild: Any
    status_key: str
    status_label: str
    detail_message: str
    region_key: str
    region_display: str
    search_text: str
    travel_time_seconds: int
    travel_projection_label: str


def project_active_guild_pvp_run(run: Any, *, now: datetime, can_manage: bool) -> GuildPvpRunDisplay:
    status = getattr(run, "status", "")
    battle_at = getattr(run, "battle_at", None)
    return_at = getattr(run, "return_at", None)
    defender_name = getattr(getattr(run, "defender_guild", None), "name", "目标帮会")

    if status == _STATUS_MARCHING:
        if battle_at is not None and battle_at <= now:
            return GuildPvpRunDisplay(
                run=run,
                display_status_key="battling",
                display_status_label="战斗中",
                display_hint=f"已抵达{defender_name}，正在交战",
                display_eta_at=return_at,
                display_eta_label="结束",
                can_retreat=False,
                action_label="",
                action_kind="none",
            )
        can_retreat = bool(can_manage)
        return GuildPvpRunDisplay(
            run=run,
            display_status_key="marching",
            display_status_label=_resolve_run_status_label(run, fallback="行军中"),
            display_hint=f"正在向{defender_name}进军",
            display_eta_at=battle_at,
            display_eta_label="到达",
            can_retreat=can_retreat,
            action_label="撤回" if can_retreat else "",
            action_kind="retreat" if can_retreat else "none",
        )

    if status == _STATUS_BATTLING:
        return GuildPvpRunDisplay(
            run=run,
            display_status_key="battling",
            display_status_label=_resolve_run_status_label(run, fallback="战斗中"),
            display_hint=f"已抵达{defender_name}，正在交战",
            display_eta_at=return_at,
            display_eta_label="结束",
            can_retreat=False,
            action_label="",
            action_kind="none",
        )

    if status in {_STATUS_RETURNING, _STATUS_RETREATED}:
        return GuildPvpRunDisplay(
            run=run,
            display_status_key="returning",
            display_status_label=_resolve_run_status_label(run, fallback="返程中"),
            display_hint=f"正在从{defender_name}返程",
            display_eta_at=return_at,
            display_eta_label="返回",
            can_retreat=False,
            action_label="",
            action_kind="none",
        )

    return GuildPvpRunDisplay(
        run=run,
        display_status_key="unknown",
        display_status_label=_resolve_run_status_label(run, fallback="进行中"),
        display_hint="当前帮会出征正在处理中",
        display_eta_at=return_at or battle_at,
        display_eta_label="结束",
        can_retreat=False,
        action_label="",
        action_kind="none",
    )


def project_incoming_guild_pvp_run(run: Any, *, now: datetime) -> GuildPvpRunDisplay:
    status = getattr(run, "status", "")
    battle_at = getattr(run, "battle_at", None)
    return_at = getattr(run, "return_at", None)

    if status == _STATUS_MARCHING and battle_at and battle_at > now:
        return GuildPvpRunDisplay(
            run=run,
            display_status_key="marching",
            display_status_label="进军中",
            display_hint="敌方帮会正在向本帮进军",
            display_eta_at=battle_at,
            display_eta_label="到达",
            can_retreat=False,
            action_label="",
            action_kind="none",
        )

    if status in {_STATUS_MARCHING, _STATUS_BATTLING}:
        display_status_key = "arrived" if status == _STATUS_MARCHING else "battling"
        display_status_label = (
            "已抵达" if status == _STATUS_MARCHING else _resolve_run_status_label(run, fallback="战斗中")
        )
        return GuildPvpRunDisplay(
            run=run,
            display_status_key=display_status_key,
            display_status_label=display_status_label,
            display_hint="敌方帮会已抵达，正在交战",
            display_eta_at=return_at or battle_at,
            display_eta_label="结束",
            can_retreat=False,
            action_label="",
            action_kind="none",
        )

    return GuildPvpRunDisplay(
        run=run,
        display_status_key="unknown",
        display_status_label=_resolve_run_status_label(run, fallback="进行中"),
        display_hint="敌方帮会行动状态同步中",
        display_eta_at=return_at or battle_at,
        display_eta_label="结束",
        can_retreat=False,
        action_label="",
        action_kind="none",
    )


def project_guild_pvp_target_card(
    guild: Any,
    *,
    can_attack: bool,
    blocked_reason: str,
    travel_time_seconds: int,
) -> GuildPvpTargetCardDisplay:
    region_key = getattr(getattr(getattr(guild, "founder", None), "manor", None), "region", "")
    region_display = REGION_DICT.get(region_key, region_key or "未知区域")
    status_key = "attackable" if can_attack else "blocked"
    status_label = "可进攻" if can_attack else "暂不可进攻"
    resolved_travel_time = max(0, int(travel_time_seconds))
    return GuildPvpTargetCardDisplay(
        guild=guild,
        status_key=status_key,
        status_label=status_label,
        detail_message=blocked_reason or "符合当前条件，可作为本次进攻目标。",
        region_key=region_key,
        region_display=region_display,
        search_text=f"{guild.name} {region_display} lv {guild.level} {status_key}",
        travel_time_seconds=resolved_travel_time,
        travel_projection_label=f"基础阵容预计 {resolved_travel_time} 秒",
    )


def _resolve_run_status_label(run: Any, *, fallback: str) -> str:
    get_status_display = getattr(run, "get_status_display", None)
    if callable(get_status_display):
        resolved = get_status_display()
        if isinstance(resolved, str) and resolved:
            return resolved
    return fallback
