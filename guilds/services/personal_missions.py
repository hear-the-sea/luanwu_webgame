from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import GuildMembershipError, GuildValidationError

from ..models import Guild, GuildMember, GuildPersonalMissionParticipation, GuildResourceLog
from .warehouse import add_item_to_warehouse

PERSONAL_GUILD_MISSION_WEEKLY_LIMIT = 10
PERSONAL_GUILD_MISSION_KEYS = frozenset(
    {
        "guild_personal_zhenlong_qipu",
        "guild_personal_shaolin_night_shadow",
        "guild_personal_yanziwu_secret_letter",
    }
)


def is_personal_guild_mission(mission: Any) -> bool:
    return str(getattr(mission, "key", "") or "") in PERSONAL_GUILD_MISSION_KEYS


def _get_week_bounds(now=None):
    from .guild_missions import get_guild_mission_week_bounds

    return get_guild_mission_week_bounds(now=now)


def _get_weekly_usage(user_id: int, *, now=None) -> int:
    week_start, week_end = _get_week_bounds(now=now)
    return GuildPersonalMissionParticipation.objects.filter(
        user_id=user_id,
        started_at__gte=week_start,
        started_at__lt=week_end,
    ).count()


def get_personal_guild_mission_status(user, *, now=None) -> dict[str, Any]:
    used = _get_weekly_usage(user.pk, now=now) if getattr(user, "is_authenticated", False) else 0
    membership = None
    if getattr(user, "is_authenticated", False):
        membership = (
            GuildMember.objects.select_related("guild").filter(user=user, is_active=True, guild__is_active=True).first()
        )
    return {
        "has_guild": membership is not None,
        "guild_name": membership.guild.name if membership else "",
        "used": used,
        "remaining": max(0, PERSONAL_GUILD_MISSION_WEEKLY_LIMIT - used),
        "limit": PERSONAL_GUILD_MISSION_WEEKLY_LIMIT,
    }


def _get_contribution_reward(mission) -> int:
    drop_table = getattr(mission, "drop_table", None) or {}
    raw_reward = drop_table.get("guild_contribution", 0)
    if isinstance(raw_reward, bool):
        raise GuildValidationError("帮会任务贡献奖励配置无效")
    try:
        reward = int(raw_reward)
    except (TypeError, ValueError) as exc:
        raise GuildValidationError("帮会任务贡献奖励配置无效") from exc
    if reward < 0:
        raise GuildValidationError("帮会任务贡献奖励配置无效")
    return reward


def register_personal_guild_mission_run(mission, manor, run) -> GuildPersonalMissionParticipation | None:
    if not is_personal_guild_mission(mission):
        return None
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("register_personal_guild_mission_run must run inside transaction.atomic()")

    candidate = (
        GuildMember.objects.select_related("guild")
        .filter(user_id=manor.user_id, is_active=True, guild__is_active=True)
        .first()
    )
    if candidate is None:
        raise GuildMembershipError("加入帮会后才能参加帮会任务")

    locked_guild = Guild.objects.select_for_update().filter(pk=candidate.guild_id, is_active=True).first()
    if locked_guild is None:
        raise GuildMembershipError("当前帮会不可用，暂时无法参加帮会任务")
    locked_member = (
        GuildMember.objects.select_for_update()
        .filter(pk=candidate.pk, guild=locked_guild, user_id=manor.user_id, is_active=True)
        .first()
    )
    if locked_member is None:
        raise GuildMembershipError("加入帮会后才能参加帮会任务")

    used = _get_weekly_usage(manor.user_id, now=run.started_at)
    if used >= PERSONAL_GUILD_MISSION_WEEKLY_LIMIT:
        raise GuildValidationError("本周帮会任务次数已用完（初、中、高级共享 10 次）")

    return GuildPersonalMissionParticipation.objects.create(
        mission_run=run,
        guild=locked_guild,
        member=locked_member,
        user_id=manor.user_id,
        contribution_reward=_get_contribution_reward(mission),
        started_at=run.started_at or timezone.now(),
    )


def settle_personal_guild_mission_drops(run, drops: dict[str, int]) -> dict[str, int] | None:
    """把个人帮会任务的资源奖励转入发起时的帮会，并结算玩家贡献。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("settle_personal_guild_mission_drops must run inside transaction.atomic()")

    participation = GuildPersonalMissionParticipation.objects.select_related("guild").filter(mission_run=run).first()
    if participation is None:
        # 兼容没有参与记录的旧任务战报，不能把内部贡献键当作普通物品发给玩家。
        drops.pop("guild_contribution", None)
        return None

    guild_id = participation.guild_id
    if guild_id is None:
        drops.pop("guild_contribution", None)
        return None

    guild = Guild.objects.select_for_update().filter(pk=guild_id).first()
    if guild is None:
        drops.pop("guild_contribution", None)
        return None

    contribution = participation.contribution_reward
    drops.pop("guild_contribution", None)
    guild_drops: dict[str, int] = {}
    silver = int(drops.pop("silver", 0) or 0)
    grain = int(drops.pop("grain", 0) or 0)
    red_ruby = int(drops.pop("red_ruby", 0) or 0)

    if silver > 0:
        Guild.objects.filter(pk=guild.pk).update(silver=F("silver") + silver)
        guild_drops["silver"] = silver
    if grain > 0:
        add_item_to_warehouse(guild, "grain", grain, 0, count_as_production=False)
        guild_drops["grain"] = grain
    if red_ruby > 0:
        add_item_to_warehouse(guild, "red_ruby", red_ruby, 0, count_as_production=False)
        guild_drops["red_ruby"] = red_ruby

    member_id = participation.member_id
    if contribution > 0 and member_id is not None:
        member = (
            GuildMember.objects.select_for_update()
            .filter(
                pk=member_id,
                user_id=participation.user_id,
                guild_id=guild.pk,
                is_active=True,
            )
            .first()
        )
        if member is not None:
            week_start, _week_end = _get_week_bounds()
            reset_date = week_start.date()
            weekly_contribution = (
                F("weekly_contribution") + contribution
                if member.weekly_reset_at and member.weekly_reset_at >= reset_date
                else contribution
            )
            GuildMember.objects.filter(pk=member.pk).update(
                total_contribution=F("total_contribution") + contribution,
                current_contribution=F("current_contribution") + contribution,
                weekly_contribution=weekly_contribution,
                weekly_reset_at=reset_date,
            )
            guild_drops["guild_contribution"] = contribution

    contribution_not_credited = contribution > 0 and "guild_contribution" not in guild_drops
    note = f"完成{run.mission.name}，入库银两{silver}、粮食{grain}、红宝石{red_ruby}"
    if contribution_not_credited:
        note += "；玩家已离开发起帮会，贡献未记入"
    GuildResourceLog.objects.create(
        guild=guild,
        action="mission_reward",
        silver_change=silver,
        grain_change=grain,
        related_user_id=participation.user_id,
        note=note,
    )
    return guild_drops
