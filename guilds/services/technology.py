# guilds/services/technology.py

import logging
import math
from typing import SupportsInt, cast

from django.db import transaction
from django.db.models import F

from core.exceptions import GuildMembershipError, GuildTechnologyError, GuildWarehouseError
from core.game_data.technology import get_troop_stat_bonuses_from_levels
from core.utils import safe_non_negative_int
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services import technology as player_technology_service

from .. import constants as guild_constants
from ..models import Guild, GuildResourceLog, GuildTechnology
from .guild import create_announcement
from .utils import get_active_membership, lock_active_member_for_guild
from .warehouse import spend_guild_warehouse_items_locked

logger = logging.getLogger(__name__)
CAPACITY_TECH_KEYS = {"guild_lineup_capacity", "guild_dispatch_capacity"}
WAREHOUSE_FUNDED_TECH_KEYS = CAPACITY_TECH_KEYS | {"mysticism"}
WAREHOUSE_TECH_RESOURCE_KEYS = ("red_ruby", "grain", "gold_bar")
TECH_RESOURCE_LABELS = {
    "silver": "银两",
    "grain": "粮食",
    "gold_bar": "金条",
    "red_ruby": "红宝石",
}
MAX_GUILD_LINEUP_CAPACITY = 40
MAX_GUILD_DISPATCH_CAPACITY = 25
GUEST_BONUS_TYPES = {"guest_force", "guest_intellect", "guest_defense"}
TROOP_TACTICS_RUNTIME_MAX_LEVEL = 10


def _is_supported_tech_key(tech_key: str) -> bool:
    return tech_key in guild_constants.get_supported_guild_technology_keys()


def get_effective_guild_tech_max_level(tech_key: str, stored_max_level: object) -> int:
    if tech_key == "troop_tactics":
        return TROOP_TACTICS_RUNTIME_MAX_LEVEL
    return safe_non_negative_int(stored_max_level)


def _can_upgrade_guild_technology(tech: GuildTechnology) -> bool:
    return tech.level < get_effective_guild_tech_max_level(tech.tech_key, tech.max_level)


def build_guild_troop_tech_levels(guild: Guild) -> dict[str, int]:
    troop_tactics_level = get_guild_tech_level(guild, "troop_tactics")
    troop_tactics_max_level = get_effective_guild_tech_max_level("troop_tactics", TROOP_TACTICS_RUNTIME_MAX_LEVEL)
    projected_level = max(0, min(troop_tactics_level, troop_tactics_max_level))

    resolved: dict[str, int] = {}
    data = player_technology_service.load_technology_templates()
    for template in data.get("technologies", []) or []:
        if not isinstance(template, dict):
            continue
        tech_key = str(template.get("key") or "").strip()
        troop_class = str(template.get("troop_class") or "").strip()
        if not tech_key or not troop_class:
            continue
        personal_max_level = safe_non_negative_int(template.get("max_level"))
        mapped_level = (projected_level * personal_max_level) // troop_tactics_max_level
        resolved[tech_key] = min(personal_max_level, mapped_level)
    return resolved


def calculate_tech_upgrade_cost(tech_key, current_level):
    """
    计算科技升级成本

    Args:
        tech_key: 科技标识
        current_level: 当前等级

    Returns:
        dict: {'silver': xxx, 'grain': xxx, 'gold_bar': xxx}
    """
    current_level = int(current_level)
    target_level = str(current_level + 1)
    base = guild_constants.TECH_UPGRADE_COSTS.get(tech_key, {"silver": 5000, "grain": 2000, "gold_bar": 1})
    override = guild_constants.TECH_UPGRADE_COST_OVERRIDES.get(tech_key, {}).get(target_level)
    if override is not None:
        return dict(override)
    if current_level == 0:
        return dict(base)

    curve_key = guild_constants.TECH_UPGRADE_COST_CURVE_BY_TECH.get(tech_key)
    multiplier = guild_constants.TECH_UPGRADE_COST_CURVES.get(curve_key, {}).get(target_level)
    if multiplier is None:
        tech_name = guild_constants.TECH_NAMES.get(tech_key, tech_key or "该科技")
        raise GuildTechnologyError(f"{tech_name}缺少升至{target_level}级的费用配置")

    return {resource_key: int(resource_cost) * int(multiplier) for resource_key, resource_cost in base.items()}


def _format_tech_resource_cost(cost: dict[str, int]) -> str:
    parts = [
        f"{TECH_RESOURCE_LABELS.get(resource_key, resource_key)}×{amount}"
        for resource_key, amount in cost.items()
        if int(amount) > 0
    ]
    return "、".join(parts) if parts else "无"


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

    if not _is_supported_tech_key(tech_key):
        raise GuildTechnologyError("科技不存在")

    # 获取科技
    try:
        tech = GuildTechnology.objects.get(guild=guild, tech_key=tech_key)
    except GuildTechnology.DoesNotExist:
        raise GuildTechnologyError("科技不存在")

    # 验证是否可升级
    if not _can_upgrade_guild_technology(tech):
        raise GuildTechnologyError("科技已达最高等级")

    # 并发安全的事务处理
    with transaction.atomic():
        # 步骤1：锁定帮会和科技，防止并发升级
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)
        tech_locked = GuildTechnology.objects.select_for_update().get(pk=tech.pk)
        if getattr(membership, "pk", None) is not None:
            try:
                membership = lock_active_member_for_guild(
                    membership,
                    error_msg="只有帮主和管理员可以升级科技",
                )
            except GuildMembershipError as exc:
                raise GuildTechnologyError(str(exc)) from exc
        if not membership.can_manage:
            raise GuildTechnologyError("只有帮主和管理员可以升级科技")

        # 步骤2：在锁内重新验证条件，防止并发穿透
        if not _can_upgrade_guild_technology(tech_locked):
            raise GuildTechnologyError("科技已达最高等级")

        # 成本必须基于锁内的当前等级计算，避免并发下低价升级
        cost = calculate_tech_upgrade_cost(tech_key, tech_locked.level)

        if tech_key in WAREHOUSE_FUNDED_TECH_KEYS:
            warehouse_cost = {
                item_key: int(cost.get(item_key, 0) or 0)
                for item_key in WAREHOUSE_TECH_RESOURCE_KEYS
                if int(cost.get(item_key, 0) or 0) > 0
            }
            if warehouse_cost:
                try:
                    spend_guild_warehouse_items_locked(
                        guild_locked,
                        warehouse_cost,
                        error_prefix="帮会仓库",
                    )
                except GuildWarehouseError as exc:
                    raise GuildTechnologyError(str(exc)) from exc
            GuildTechnology.objects.filter(pk=tech_locked.pk).update(level=F("level") + 1)
            tech_locked.refresh_from_db(fields=["level"])
            GuildResourceLog.objects.create(
                guild=guild_locked,
                action="tech_upgrade",
                grain_change=-warehouse_cost.get("grain", 0),
                gold_bar_change=-warehouse_cost.get("gold_bar", 0),
                related_user=operator,
                note=(
                    f"升级{guild_constants.TECH_NAMES.get(tech_key, '该科技')}至{tech_locked.level}级"
                    f"（消耗{_format_tech_resource_cost(cost)}）"
                ),
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
                note=f"升级{guild_constants.TECH_NAMES.get(tech_key, '该科技')}至{tech_locked.level}级",
            )

        # 步骤6：获取操作者庄园名称（保存用于事务外使用）
        operator_user_id = operator.id
        tech_name = guild_constants.TECH_NAMES.get(tech_key, "该科技")
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


def get_tech_bonus(guild, bonus_type):
    """
    获取科技加成

    Args:
        guild: Guild对象
        bonus_type: 加成类型

    Returns:
        float: 加成系数（如0.1表示10%加成）
    """
    if bonus_type in GUEST_BONUS_TYPES:
        return 0.0

    if bonus_type in {"troop_attack", "troop_defense", "troop_hp"}:
        return 0.0

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

    return {
        "force": guest.force,
        "intellect": guest.intellect,
        "defense": base_defense,
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
    troop_key = str(troop_stats.get("troop_key") or troop_stats.get("key") or "").strip()
    if not troop_key:
        return {
            "attack": troop_stats.get("attack", 0),
            "defense": troop_stats.get("defense", 0),
            "hp": troop_stats.get("hp", 0),
        }

    bonuses = get_troop_stat_bonuses_from_levels(build_guild_troop_tech_levels(guild), troop_key)
    attack_bonus = float(bonuses.get("attack", 0.0) or 0.0)
    defense_bonus = float(bonuses.get("defense", 0.0) or 0.0)
    hp_bonus = float(bonuses.get("hp", 0.0) or 0.0)

    def apply_bonus(base_value, bonus):
        # Compensated addition avoids representation noise without rounding the result.
        return math.fsum((base_value, base_value * bonus))

    return {
        "attack": apply_bonus(troop_stats.get("attack", 0), attack_bonus),
        "defense": apply_bonus(troop_stats.get("defense", 0), defense_bonus),
        "hp": apply_bonus(troop_stats.get("hp", 0), hp_bonus),
    }
