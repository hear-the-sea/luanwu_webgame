"""嗜血铁骑的帮会募兵与贡献兑换。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

import guilds.constants as guild_constants
from battle.models import TroopTemplate
from core.exceptions import GuildMembershipError, GuildWarehouseError, TroopCapacityFullError
from gameplay.models import Manor
from gameplay.services.manor.troop_capacity import ensure_manor_troop_capacity_locked
from gameplay.services.recruitment.troops import _add_troops_batch

from ..models import (
    Guild,
    GuildCavalryTraining,
    GuildExchangeLog,
    GuildMember,
    GuildResourceLog,
    GuildTechnology,
    GuildWarehouse,
)
from .utils import get_active_membership, lock_active_member_for_guild

CAVALRY_ITEM_KEY = "bloodthirsty_cavalry"
CAVALRY_UNLOCK_TECH = "wolf_bloodthirsty_cavalry"
CAVALRY_NAME = "嗜血铁骑"
CAVALRY_DESCRIPTION = (
    "披甲执锐的帮会精骑，行动迅捷，可在先攻回合出击。研习铁骑冲阵后，有机会同时攻击两名敌人。"
    "成员可在帮会资产消耗贡献兑换，兑换后加入庄园护院并占用护院名额。"
)
logger = logging.getLogger(__name__)


def get_cavalry_recruitment_config() -> dict[str, int]:
    """读取统一帮会规则；贡献价格始终以配置为准，避免旧库存免费兑换。"""
    config = guild_constants.load_guild_rules()["cavalry_recruitment"]
    return {
        key: max(1, int(config[key]))
        for key in ("silver_per_unit", "grain_per_unit", "contribution_per_unit", "max_batch", "seconds_per_unit")
    }


def _is_cavalry_unlocked(guild: Guild) -> bool:
    return GuildTechnology.objects.filter(guild=guild, tech_key=CAVALRY_UNLOCK_TECH, level__gte=1).exists()


def _validate_quantity(quantity: int, *, action: str) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise GuildWarehouseError(f"{action}数量必须为正整数")
    max_batch = get_cavalry_recruitment_config()["max_batch"]
    if quantity > max_batch:
        raise GuildWarehouseError(f"每次最多{action}{max_batch}名嗜血铁骑")


def _validate_guild(guild: Guild) -> None:
    if not guild.is_active:
        raise GuildWarehouseError("帮会已解散")
    if not _is_cavalry_unlocked(guild):
        raise GuildWarehouseError("请先研究训练嗜血铁骑科技")


def get_guild_cavalry_recruitment_state(guild: Guild, member: GuildMember | None = None) -> dict[str, Any]:
    """训练弹窗与仓库兑换说明；查询本身不触发训练结算。"""
    from .warehouse import get_guild_material_balances

    config = get_cavalry_recruitment_config()
    balances = get_guild_material_balances(guild)
    is_unlocked = bool(guild.is_active and _is_cavalry_unlocked(guild))
    can_manage = bool(
        guild.is_active and member and member.guild_id == guild.pk and member.is_active and member.can_manage
    )
    max_quantity = min(
        config["max_batch"],
        balances["silver"] // config["silver_per_unit"],
        balances["grain"] // config["grain_per_unit"],
    )
    training = GuildCavalryTraining.objects.filter(guild=guild, completed_at__isnull=True).first()
    return {
        **config,
        "item_key": CAVALRY_ITEM_KEY,
        "name": CAVALRY_NAME,
        "is_unlocked": is_unlocked,
        "can_manage": can_manage,
        "can_recruit": bool(is_unlocked and can_manage and max_quantity > 0 and training is None),
        "max_recruitable_quantity": max_quantity if is_unlocked and can_manage and training is None else 0,
        "training": training,
        "stock": int(
            GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY)
            .values_list("quantity", flat=True)
            .first()
            or 0
        ),
        "silver": balances["silver"],
        "grain": balances["grain"],
    }


def _finalize_cavalry_training_locked(guild: Guild, now) -> int:
    """调用者已锁定帮会；入库与完成标记在同一事务内提交。"""
    from .warehouse import add_item_to_warehouse

    training = (
        GuildCavalryTraining.objects.select_for_update()
        .filter(guild=guild, completed_at__isnull=True, complete_at__lte=now)
        .first()
    )
    if training is None or not guild.is_active:
        return 0
    add_item_to_warehouse(
        guild, CAVALRY_ITEM_KEY, training.quantity, get_cavalry_recruitment_config()["contribution_per_unit"]
    )
    training.completed_at = now
    training.save(update_fields=["completed_at"])
    return training.quantity


def finalize_guild_cavalry_training(guild_id: int) -> int:
    """后台任务与页面补偿共用的幂等训练结算。"""
    now = timezone.now()
    has_due_training = GuildCavalryTraining.objects.filter(
        guild_id=guild_id,
        completed_at__isnull=True,
        complete_at__lte=now,
    ).exists()
    if not has_due_training:
        return 0

    with transaction.atomic():
        guild = Guild.objects.select_for_update().filter(pk=guild_id).first()
        return _finalize_cavalry_training_locked(guild, timezone.now()) if guild else 0


def _schedule_cavalry_training(guild_id: int, duration: int) -> None:
    from common.utils.celery import safe_apply_async
    from guilds.tasks import complete_guild_cavalry_training

    # 长时任务由数据库扫描兜底，避免长时间占用 broker 的 ETA 队列。
    safe_apply_async(
        complete_guild_cavalry_training,
        args=[guild_id],
        countdown=duration,
        defer_long_eta=True,
        logger=logger,
        log_message="Failed to schedule guild cavalry training; scanner will recover",
    )


def recruit_guild_cavalry(guild: Guild, operator: Any, quantity: int) -> dict[str, Any]:
    """帮主管理员预付帮会资源开始训练，整批完成后入库。"""
    from .warehouse import spend_guild_warehouse_items_locked

    _validate_quantity(quantity, action="训练")
    try:
        membership = get_active_membership(guild, operator, "只有帮主和管理员可以训练嗜血铁骑")
    except GuildMembershipError as exc:
        raise GuildWarehouseError(str(exc)) from exc

    config = get_cavalry_recruitment_config()
    silver_cost = quantity * config["silver_per_unit"]
    grain_cost = quantity * config["grain_per_unit"]
    duration = quantity * config["seconds_per_unit"]
    with transaction.atomic():
        # 与科技、捐献保持一致：Guild -> GuildMember -> GuildWarehouse。
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)
        try:
            member_locked = lock_active_member_for_guild(membership)
        except GuildMembershipError as exc:
            raise GuildWarehouseError(str(exc)) from exc
        if not member_locked.can_manage:
            raise GuildWarehouseError("只有帮主和管理员可以训练嗜血铁骑")
        _validate_guild(guild_locked)
        now = timezone.now()
        _finalize_cavalry_training_locked(guild_locked, now)
        if GuildCavalryTraining.objects.filter(guild=guild_locked, completed_at__isnull=True).exists():
            raise GuildWarehouseError("已有一批嗜血铁骑正在训练，请等待完成后再开始训练")
        if not TroopTemplate.objects.filter(key=CAVALRY_ITEM_KEY).exists():
            raise GuildWarehouseError("嗜血铁骑暂时无法训练，请稍后再试")
        if guild_locked.silver < silver_cost:
            raise GuildWarehouseError(f"帮会银两不足，需要{silver_cost}")

        spend_guild_warehouse_items_locked(guild_locked, {"grain": grain_cost})
        updated = Guild.objects.filter(pk=guild_locked.pk, silver__gte=silver_cost).update(
            silver=F("silver") - silver_cost
        )
        if not updated:
            raise GuildWarehouseError("帮会银两不足，训练未开始")
        training = GuildCavalryTraining.objects.create(
            guild=guild_locked,
            operator=operator,
            quantity=quantity,
            started_at=now,
            complete_at=now + timedelta(seconds=duration),
        )
        GuildResourceLog.objects.create(
            guild=guild_locked,
            action="troop_recruit",
            silver_change=-silver_cost,
            grain_change=-grain_cost,
            related_user=operator,
            note=f"开始训练{quantity}名嗜血铁骑，整批耗时{duration}秒",
        )
        transaction.on_commit(lambda: _schedule_cavalry_training(guild.pk, duration))
    return {
        "quantity": quantity,
        "silver_cost": silver_cost,
        "grain_cost": grain_cost,
        "duration": duration,
        "complete_at": training.complete_at,
    }


def exchange_guild_cavalry(member: GuildMember, quantity: int = 1) -> None:
    """贡献兑换直接发放护院；与普通仓库共用每日次数和兑换日志。"""
    from .warehouse import _validate_weekly_personal_limit

    _validate_quantity(quantity, action="兑换")
    unit_cost = get_cavalry_recruitment_config()["contribution_per_unit"]
    total_cost = quantity * unit_cost
    with transaction.atomic():
        # Manor 是护院容量的共享锁；后续锁顺序与帮会捐献一致。
        manor_locked = Manor.objects.select_for_update().get(user_id=member.user_id)
        guild_locked = Guild.objects.select_for_update().get(pk=member.guild_id)
        try:
            member_locked = lock_active_member_for_guild(member)
        except GuildMembershipError as exc:
            raise GuildWarehouseError(str(exc)) from exc
        _validate_guild(guild_locked)
        _finalize_cavalry_training_locked(guild_locked, timezone.now())
        member_locked.reset_daily_limits()
        if member_locked.daily_exchange_count >= guild_constants.DAILY_EXCHANGE_LIMIT:
            raise GuildWarehouseError(f"今日兑换次数已达上限（{guild_constants.DAILY_EXCHANGE_LIMIT}次）")
        _validate_weekly_personal_limit(member_locked, CAVALRY_ITEM_KEY, quantity)

        stock = GuildWarehouse.objects.select_for_update().filter(guild=guild_locked, item_key=CAVALRY_ITEM_KEY).first()
        if stock is None or stock.quantity < quantity:
            raise GuildWarehouseError(f"嗜血铁骑库存不足，剩余{stock.quantity if stock else 0}名")
        if member_locked.current_contribution < total_cost:
            raise GuildWarehouseError(f"贡献度不足，需要{total_cost}贡献")
        if not TroopTemplate.objects.filter(key=CAVALRY_ITEM_KEY).exists():
            raise GuildWarehouseError("嗜血铁骑暂时无法兑换，请稍后再试")
        try:
            ensure_manor_troop_capacity_locked(manor_locked, quantity)
        except TroopCapacityFullError as exc:
            raise GuildWarehouseError(str(exc)) from exc

        GuildMember.objects.filter(pk=member_locked.pk).update(
            current_contribution=F("current_contribution") - total_cost,
            daily_exchange_count=F("daily_exchange_count") + 1,
        )
        updated = GuildWarehouse.objects.filter(pk=stock.pk, quantity__gte=quantity).update(
            quantity=F("quantity") - quantity,
            contribution_cost=unit_cost,
            total_exchanged=F("total_exchanged") + quantity,
        )
        if not updated:
            raise GuildWarehouseError("嗜血铁骑库存不足，兑换失败")
        _add_troops_batch(manor_locked, {CAVALRY_ITEM_KEY: quantity}, enforce_capacity=False)
        GuildWarehouse.objects.filter(pk=stock.pk, quantity=0).delete()
        GuildExchangeLog.objects.create(
            guild=guild_locked,
            member=member_locked,
            item_key=CAVALRY_ITEM_KEY,
            quantity=quantity,
            contribution_cost=total_cost,
        )
