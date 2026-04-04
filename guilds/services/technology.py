# guilds/services/technology.py

import logging
from typing import SupportsInt, cast

from django.db import transaction
from django.db.models import F

from core.exceptions import GuildMembershipError, GuildTechnologyError, GuildWarehouseError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor

from .. import constants as guild_constants
from ..models import Guild, GuildResourceLog, GuildTechnology
from .guild import create_announcement
from .utils import get_active_membership
from .warehouse import spend_guild_warehouse_items_locked

logger = logging.getLogger(__name__)
CAPACITY_TECH_KEYS = {"guild_lineup_capacity", "guild_dispatch_capacity"}
MAX_GUILD_LINEUP_CAPACITY = 40
MAX_GUILD_DISPATCH_CAPACITY = 25


def calculate_tech_upgrade_cost(tech_key, current_level):
    """
    计算科技升级成本

    Args:
        tech_key: 科技标识
        current_level: 当前等级

    Returns:
        dict: {'silver': xxx, 'grain': xxx, 'gold_bar': xxx}
    """
    base = guild_constants.TECH_UPGRADE_COSTS.get(tech_key, {"silver": 5000, "grain": 2000, "gold_bar": 1})
    multiplier = 2**current_level  # 指数增长

    return {resource_key: int(resource_cost) * multiplier for resource_key, resource_cost in base.items()}


def upgrade_technology(guild, tech_key, operator):
    """
    升级帮会科技

    Args:
        guild: Guild对象
        tech_key: 科技标识
        operator: 操作者User对象

    Raises:
        GuildTechnologyError: 验证失败
    """
    # 验证权限
    try:
        membership = get_active_membership(guild, operator, "只有帮主和管理员可以升级科技")
    except GuildMembershipError as exc:
        raise GuildTechnologyError(str(exc)) from exc
    if not membership.can_manage:
        raise GuildTechnologyError("只有帮主和管理员可以升级科技")

    # 获取科技
    try:
        tech = GuildTechnology.objects.get(guild=guild, tech_key=tech_key)
    except GuildTechnology.DoesNotExist:
        raise GuildTechnologyError("科技不存在")

    # 验证是否可升级
    if not tech.can_upgrade:
        raise GuildTechnologyError("科技已达最高等级")

    # 并发安全的事务处理
    with transaction.atomic():
        # 步骤1：锁定帮会和科技，防止并发升级
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)
        tech_locked = GuildTechnology.objects.select_for_update().get(pk=tech.pk)

        # 成本必须基于锁内的当前等级计算，避免并发下低价升级
        cost = calculate_tech_upgrade_cost(tech_key, tech_locked.level)

        # 步骤2：在锁内重新验证条件，防止并发穿透
        if not tech_locked.can_upgrade:
            raise GuildTechnologyError("科技已达最高等级")

        if tech_key in CAPACITY_TECH_KEYS:
            ruby_cost = int(cost.get("red_ruby", 0))
            if ruby_cost > 0:
                try:
                    spend_guild_warehouse_items_locked(
                        guild_locked,
                        {"red_ruby": ruby_cost},
                        error_prefix="帮会仓库",
                    )
                except GuildWarehouseError as exc:
                    raise GuildTechnologyError(str(exc)) from exc
            GuildTechnology.objects.filter(pk=tech_locked.pk).update(level=F("level") + 1)
            tech_locked.refresh_from_db(fields=["level"])
            GuildResourceLog.objects.create(
                guild=guild_locked,
                action="tech_upgrade",
                related_user=operator,
                note=f"升级{guild_constants.TECH_NAMES.get(tech_key, tech_key)}至{tech_locked.level}级（消耗红宝石x{ruby_cost}）",
            )
        else:
            if guild_locked.silver < cost["silver"]:
                raise GuildTechnologyError(f"帮会银两不足，需要{cost['silver']}")
            warehouse_cost = {
                item_key: cost.get(item_key, 0)
                for item_key in ("grain", "gold_bar")
                if int(cost.get(item_key, 0) or 0) > 0
            }
            if warehouse_cost:
                try:
                    spend_guild_warehouse_items_locked(guild_locked, warehouse_cost, error_prefix="帮会")
                except GuildWarehouseError as exc:
                    raise GuildTechnologyError(str(exc)) from exc

            # 步骤3：银两继续使用 Guild 字段扣减
            Guild.objects.filter(pk=guild_locked.pk).update(silver=F("silver") - cost["silver"])

            # 步骤4：使用F()表达式原子性地升级科技
            GuildTechnology.objects.filter(pk=tech_locked.pk).update(level=F("level") + 1)

            # 刷新对象以获取更新后的值（用于日志和公告）
            tech_locked.refresh_from_db(fields=["level"])

            # 步骤5：记录资源流水
            GuildResourceLog.objects.create(
                guild=guild_locked,
                action="tech_upgrade",
                silver_change=-cost["silver"],
                grain_change=-cost["grain"],
                gold_bar_change=-cost["gold_bar"],
                related_user=operator,
                note=f"升级{guild_constants.TECH_NAMES.get(tech_key, tech_key)}至{tech_locked.level}级",
            )

        # 步骤6：获取操作者庄园名称（保存用于事务外使用）
        operator_user_id = operator.id
        tech_name = guild_constants.TECH_NAMES.get(tech_key, tech_key)
        tech_level = tech_locked.level

    # 事务外发布公告，减少锁持有时间。公告失败不应影响升级结果。
    operator_manor = Manor.objects.filter(user_id=operator_user_id).first()
    operator_name = getattr(operator_manor, "display_name", getattr(operator, "username", str(operator_user_id)))
    if operator_manor is None:
        logger.warning(
            "Guild tech upgrade announcement fallback name used because manor missing: user_id=%s guild_id=%s",
            operator_user_id,
            guild_locked.id,
        )
    try:
        create_announcement(
            guild_locked,
            "system",
            f"{operator_name}将{tech_name}升至{tech_level}级！",
        )
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Guild tech upgrade announcement failed: user_id=%s guild_id=%s tech_key=%s level=%s",
            operator_user_id,
            guild_locked.id,
            tech_key,
            tech_level,
        )


def get_guild_tech_level(guild, tech_key):
    """
    获取帮会科技等级

    Args:
        guild: Guild对象
        tech_key: 科技标识

    Returns:
        int: 科技等级
    """
    try:
        tech = GuildTechnology.objects.get(guild=guild, tech_key=tech_key)
        return tech.level
    except GuildTechnology.DoesNotExist:
        return 0


def get_guild_lineup_capacity(guild):
    return min(
        MAX_GUILD_LINEUP_CAPACITY,
        int(guild_constants.GUILD_BATTLE_LINEUP_LIMIT) + get_guild_tech_level(guild, "guild_lineup_capacity"),
    )


def get_guild_dispatch_capacity(guild):
    return min(
        MAX_GUILD_DISPATCH_CAPACITY,
        int(guild_constants.GUILD_DISPATCH_GUEST_BASE_LIMIT) + get_guild_tech_level(guild, "guild_dispatch_capacity"),
    )


def _calc_military_study_bonus(level: int, bonus_type: str) -> float:
    if bonus_type == "guest_force":
        bonus = 0.0
        if level >= 1:
            bonus += 0.02 * min(level, 2)
        if level >= 3:
            bonus += 0.02 * (level - 2)
        return bonus

    if bonus_type == "guest_intellect":
        return 0.02 * (level - 2) if level >= 3 else 0.0

    if bonus_type == "guest_defense":
        return 0.02 if level >= 5 else 0.0

    return 0.0


def _calc_troop_tactics_bonus(level: int, bonus_type: str) -> float:
    if bonus_type == "troop_attack":
        return 0.03 * level

    if bonus_type == "troop_defense":
        return 0.03 * (level - 2) if level >= 3 else 0.0

    if bonus_type == "troop_hp":
        return 0.05 if level >= 5 else 0.0

    return 0.0


def get_tech_bonus(guild, bonus_type):
    """
    获取科技加成

    Args:
        guild: Guild对象
        bonus_type: 加成类型

    Returns:
        float: 加成系数（如0.1表示10%加成）
    """
    if bonus_type in {"guest_force", "guest_intellect", "guest_defense"}:
        level = get_guild_tech_level(guild, "military_study")
        return _calc_military_study_bonus(level, bonus_type)

    if bonus_type in {"troop_attack", "troop_defense", "troop_hp"}:
        level = get_guild_tech_level(guild, "troop_tactics")
        return _calc_troop_tactics_bonus(level, bonus_type)

    if bonus_type == "resource_production":
        level = get_guild_tech_level(guild, "resource_boost")
        return 0.05 * level

    if bonus_type == "march_speed":
        level = get_guild_tech_level(guild, "march_speed")
        return 0.05 * level

    return 0.0


def apply_guild_bonus_to_guest(guest):
    """
    应用帮会科技加成到门客

    Args:
        guest: Guest对象

    Returns:
        dict: 加成后的属性
    """
    base_defense_raw = getattr(guest, "defense_stat", None)
    if base_defense_raw is None:
        # 兼容旧调用方（例如历史测试桩）使用 defense 字段
        base_defense_raw = getattr(guest, "defense", 0)
    try:
        base_defense = int(cast(SupportsInt | str | bytes | bytearray, base_defense_raw))
    except (TypeError, ValueError):
        base_defense = 0

    # 检查玩家是否在帮会中
    user = guest.manor.user
    if not hasattr(user, "guild_membership") or not user.guild_membership.is_active:
        return {
            "force": guest.force,
            "intellect": guest.intellect,
            "defense": base_defense,
        }

    guild = user.guild_membership.guild

    # 应用加成
    force_bonus = get_tech_bonus(guild, "guest_force")
    intellect_bonus = get_tech_bonus(guild, "guest_intellect")
    defense_bonus = get_tech_bonus(guild, "guest_defense")

    return {
        "force": int(guest.force * (1 + force_bonus)),
        "intellect": int(guest.intellect * (1 + intellect_bonus)),
        "defense": int(base_defense * (1 + defense_bonus)),
    }


def apply_guild_bonus_to_troop(troop_stats, user):
    """
    应用帮会科技加成到兵种

    Args:
        troop_stats: dict - 兵种属性字典
        user: User对象

    Returns:
        dict: 加成后的兵种属性
    """
    # 检查玩家是否在帮会中
    if not hasattr(user, "guild_membership") or not user.guild_membership.is_active:
        return troop_stats

    guild = user.guild_membership.guild

    # 应用加成
    attack_bonus = get_tech_bonus(guild, "troop_attack")
    defense_bonus = get_tech_bonus(guild, "troop_defense")
    hp_bonus = get_tech_bonus(guild, "troop_hp")

    return {
        "attack": int(troop_stats.get("attack", 0) * (1 + attack_bonus)),
        "defense": int(troop_stats.get("defense", 0) * (1 + defense_bonus)),
        "hp": int(troop_stats.get("hp", 0) * (1 + hp_bonus)),
    }
