# guilds/services/technology.py

import logging
import math
from collections.abc import Iterable
from datetime import datetime
from typing import SupportsInt, cast

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import GuildMembershipError, GuildTechnologyError, GuildWarehouseError
from core.game_data.technology import get_troop_stat_bonuses_from_levels
from core.utils import safe_non_negative_int
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services import technology as player_technology_service

from .. import constants as guild_constants
from ..models import Guild, GuildMember, GuildResourceLog, GuildTechnology, GuildWarehouse
from ..technology_tree import TECHNOLOGY_NODE_BY_KEY, TECHNOLOGY_ROUTES, is_technology_route_complete
from .guild import create_announcement
from .utils import get_active_membership, lock_active_member_for_guild
from .warehouse import spend_guild_warehouse_items_locked

logger = logging.getLogger(__name__)
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
ROUTE_COST_MULTIPLIER = 1.2
RESOURCE_PRODUCTION_TECH_KEYS = {"sheep_farmland", "sheep_silver_mine"}


def _settle_guild_member_resource_production_locked(guild: Guild, *, now: datetime) -> None:
    from gameplay.services.resources import settle_resource_production_for_users_locked

    member_user_ids = GuildMember.objects.filter(guild=guild, is_active=True).values_list("user_id", flat=True)
    settle_resource_production_for_users_locked(member_user_ids, now=now)


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
    guild_levels = get_guild_technology_levels(guild)
    for tech_key in ("wolf_berserk", "wolf_force", "wolf_defense", "wolf_agility", "wolf_hp"):
        if tech_key in guild_levels:
            resolved[tech_key] = guild_levels[tech_key]
    return resolved


def get_manor_guild_troop_tech_levels(manor: Manor) -> dict[str, int]:
    user_id = getattr(manor, "user_id", None)
    if not user_id:
        return {}

    membership = (
        GuildMember.objects.filter(user_id=user_id, is_active=True, guild__is_active=True)
        .select_related("guild")
        .first()
    )
    if membership is None:
        return {}

    guild_levels = build_guild_troop_tech_levels(membership.guild)
    return {key: level for key, level in guild_levels.items() if key.startswith("wolf_")}


def _route_cost_multiplier(guild: Guild | None, tech_key: str) -> float:
    if guild is None or tech_key not in TECHNOLOGY_NODE_BY_KEY:
        return 1.0
    node = TECHNOLOGY_NODE_BY_KEY[tech_key]
    if not any(node in route["nodes"] for route in TECHNOLOGY_ROUTES):
        return 1.0
    completed = getattr(guild, "completed_technology_routes", []) or []
    return ROUTE_COST_MULTIPLIER ** len(completed)


def calculate_tech_upgrade_cost(tech_key, current_level, *, guild: Guild | None = None):
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
        level_cost = dict(override)
    elif current_level == 0:
        level_cost = dict(base)
    else:
        curve_key = guild_constants.TECH_UPGRADE_COST_CURVE_BY_TECH.get(tech_key)
        level_multiplier = guild_constants.TECH_UPGRADE_COST_CURVES.get(curve_key, {}).get(target_level)
        if level_multiplier is None:
            tech_name = guild_constants.TECH_NAMES.get(tech_key, tech_key or "该科技")
            raise GuildTechnologyError(f"{tech_name}缺少升至{target_level}级的费用配置")
        level_cost = {
            resource_key: int(resource_cost) * int(level_multiplier) for resource_key, resource_cost in base.items()
        }

    route_multiplier = _route_cost_multiplier(guild, str(tech_key))
    if route_multiplier == 1:
        return level_cost
    return {key: math.ceil(amount * route_multiplier) for key, amount in level_cost.items()}


def _route_for_technology(tech_key: str) -> dict | None:
    return next(
        (route for route in TECHNOLOGY_ROUTES if any(node["key"] == tech_key for node in route["nodes"])),
        None,
    )


def _calculate_route_investment(guild: Guild, route: dict, levels: dict[str, int]) -> dict[str, int]:
    """按当前路线倍率汇总各科技已升级等级的实际投入。"""
    invested: dict[str, int] = {}
    for node in route["nodes"]:
        if not node["enabled"]:
            continue
        level = min(max(0, int(levels.get(node["key"], 0))), int(node["max_level"]))
        for current_level in range(level):
            for resource_key, amount in calculate_tech_upgrade_cost(node["key"], current_level, guild=guild).items():
                invested[resource_key] = invested.get(resource_key, 0) + int(amount)
    return invested


def _credit_guild_warehouse_items_locked(guild: Guild, amounts: dict[str, int]) -> None:
    """将科技退款记入帮会资产，不计为科技产出。"""
    for item_key, amount in amounts.items():
        if amount <= 0:
            continue
        item, _created = GuildWarehouse.objects.get_or_create(
            guild=guild,
            item_key=item_key,
            defaults={"contribution_cost": int(guild_constants.CONTRIBUTION_RATES.get(item_key, 0) or 0)},
        )
        GuildWarehouse.objects.filter(pk=item.pk).update(quantity=F("quantity") + amount)


def get_guild_technology_levels(guild: Guild | None) -> dict[str, int]:
    if guild is None or not getattr(guild, "pk", None):
        return {}
    return {
        str(tech_key): int(level)
        for tech_key, level in GuildTechnology.objects.filter(guild_id=guild.pk).values_list("tech_key", "level")
    }


def get_guild_resource_production_bonus(guild: Guild | None, resource_type: str) -> float:
    """读取全体成员适用的庄园资源产出加成。"""
    if resource_type == "grain":
        return 0.05 * get_guild_tech_level(guild, "sheep_farmland")
    if resource_type == "silver":
        return 0.05 * get_guild_tech_level(guild, "sheep_silver_mine")
    return 0.0


def get_guild_resource_bonuses_for_users(user_ids: Iterable[int]) -> dict[int, dict[str, float]]:
    """一次读取多位在帮成员的粮食、银两增产，避免批量结算逐庄园查询。"""
    normalized_ids = tuple(dict.fromkeys(int(user_id) for user_id in user_ids))
    if not normalized_ids:
        return {}
    resource_by_key = {"sheep_farmland": "grain", "sheep_silver_mine": "silver"}
    rows = GuildTechnology.objects.filter(
        guild__members__user_id__in=normalized_ids,
        guild__members__is_active=True,
        guild__is_active=True,
        tech_key__in=resource_by_key,
        level__gt=0,
    ).values_list("guild__members__user_id", "tech_key", "level")
    bonuses: dict[int, dict[str, float]] = {}
    for user_id, tech_key, level in rows:
        effective_level = min(TECHNOLOGY_NODE_BY_KEY[tech_key]["max_level"], max(0, int(level)))
        bonuses.setdefault(int(user_id), {})[resource_by_key[tech_key]] = effective_level * 0.05
    return bonuses


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

    node = TECHNOLOGY_NODE_BY_KEY.get(tech_key)
    if node is not None and not node["enabled"]:
        raise GuildTechnologyError("该科技暂未开放")

    # 获取科技
    try:
        tech = GuildTechnology.objects.get(guild=guild, tech_key=tech_key)
    except GuildTechnology.DoesNotExist:
        raise GuildTechnologyError("科技不存在")

    # 验证是否可升级
    if not _can_upgrade_guild_technology(tech):
        raise GuildTechnologyError("科技已达最高等级")

    route = _route_for_technology(tech_key)

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

        if route is not None:
            if guild_locked.active_technology_route != route["key"]:
                raise GuildTechnologyError("请先选择并开启该科技路线")
            node = TECHNOLOGY_NODE_BY_KEY[tech_key]
            missing_prerequisites = [
                TECHNOLOGY_NODE_BY_KEY[key]["name"]
                for key in node["requires"]
                if get_guild_tech_level(guild_locked, key) < 1
            ]
            if missing_prerequisites:
                raise GuildTechnologyError(f"前置科技未达到1级：{'、'.join(missing_prerequisites)}")

        # 成本必须基于锁内的当前等级计算，避免并发下低价升级
        cost = calculate_tech_upgrade_cost(tech_key, tech_locked.level, guild=guild_locked)

        # 科技成本可能由银两和任意仓库资源组成，统一扣费可避免新增纯金条/红宝石科技
        # 误进入旧的 cost["silver"] 分支。所有资源检查和扣减都在同一组行锁内完成。
        silver_cost = int(cost.get("silver", 0) or 0)
        warehouse_cost = {
            item_key: int(cost.get(item_key, 0) or 0)
            for item_key in WAREHOUSE_TECH_RESOURCE_KEYS
            if int(cost.get(item_key, 0) or 0) > 0
        }

        if silver_cost > int(guild_locked.silver):
            raise GuildTechnologyError(f"帮会银两不足，需要{silver_cost}")

        if tech_key in RESOURCE_PRODUCTION_TECH_KEYS:
            _settle_guild_member_resource_production_locked(guild_locked, now=timezone.now())

        if warehouse_cost:
            try:
                spend_guild_warehouse_items_locked(
                    guild_locked,
                    warehouse_cost,
                    error_prefix="帮会资产",
                )
            except GuildWarehouseError as exc:
                raise GuildTechnologyError(str(exc)) from exc

        if silver_cost:
            Guild.objects.filter(pk=guild_locked.pk).update(silver=F("silver") - silver_cost)

        GuildTechnology.objects.filter(pk=tech_locked.pk).update(level=F("level") + 1)
        tech_locked.refresh_from_db(fields=["level"])

        route_completed = False
        if route is not None:
            route_levels = {
                key: int(level)
                for key, level in GuildTechnology.objects.filter(
                    guild=guild_locked,
                    tech_key__in=[node["key"] for node in route["nodes"]],
                ).values_list("tech_key", "level")
            }
            route_completed = is_technology_route_complete(route, route_levels)
            if route_completed:
                completed_routes = list(guild_locked.completed_technology_routes or [])
                if route["key"] not in completed_routes:
                    completed_routes.append(route["key"])
                Guild.objects.filter(pk=guild_locked.pk).update(
                    completed_technology_routes=completed_routes,
                    active_technology_route="",
                )
                guild_locked.completed_technology_routes = completed_routes
                guild_locked.active_technology_route = ""

        GuildResourceLog.objects.create(
            guild=guild_locked,
            action="tech_upgrade",
            silver_change=-silver_cost,
            grain_change=-warehouse_cost.get("grain", 0),
            gold_bar_change=-warehouse_cost.get("gold_bar", 0),
            related_user=operator,
            note=(
                f"升级{guild_constants.TECH_NAMES.get(tech_key, '该科技')}至{tech_locked.level}级"
                f"（消耗{_format_tech_resource_cost(cost)}）"
            ),
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
            (
                f"{operator_name}将{tech_name}升至{tech_level}级！"
                + (f"{route['title']}路线已全部研究完成！" if route is not None and route_completed else "")
            ),
        )
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Guild tech upgrade announcement failed: user_id=%s guild_id=%s tech_key=%s level=%s",
            operator_user_id,
            guild_locked.id,
            tech_key,
            tech_level,
        )


def select_technology_route(guild: Guild, route_key: str, operator) -> None:
    """由帮会管理者开启一条尚未完成的专精路线。"""
    try:
        membership = get_active_membership(guild, operator, "只有帮主和管理员可以开启科技路线")
    except GuildMembershipError as exc:
        raise GuildTechnologyError(str(exc)) from exc
    if not membership.can_manage:
        raise GuildTechnologyError("只有帮主和管理员可以开启科技路线")

    route = next((route for route in TECHNOLOGY_ROUTES if route["key"] == route_key), None)
    if route is None:
        raise GuildTechnologyError("科技路线不存在")

    with transaction.atomic():
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)
        if getattr(membership, "pk", None) is not None:
            try:
                membership = lock_active_member_for_guild(
                    membership,
                    error_msg="只有帮主和管理员可以开启科技路线",
                )
            except GuildMembershipError as exc:
                raise GuildTechnologyError(str(exc)) from exc
        if not membership.can_manage:
            raise GuildTechnologyError("只有帮主和管理员可以开启科技路线")
        completed_routes = list(guild_locked.completed_technology_routes or [])
        if guild_locked.active_technology_route:
            active_route = next(
                (route for route in TECHNOLOGY_ROUTES if route["key"] == guild_locked.active_technology_route), None
            )
            levels = dict(GuildTechnology.objects.filter(guild=guild_locked).values_list("tech_key", "level"))
            if active_route is None or not is_technology_route_complete(active_route, levels):
                raise GuildTechnologyError("当前路线的开放节点尚未全部研究完成")
            if active_route["key"] not in completed_routes:
                completed_routes.append(active_route["key"])
        if route_key in completed_routes:
            raise GuildTechnologyError("该科技路线已经完成")
        Guild.objects.filter(pk=guild_locked.pk).update(
            active_technology_route=route_key, completed_technology_routes=completed_routes
        )
        guild_locked.active_technology_route = route_key
        guild_locked.completed_technology_routes = completed_routes

    try:
        create_announcement(guild_locked, "system", f"帮会开启了{route['title']}科技路线！")
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Failed to announce guild technology route selection: guild_id=%s route=%s", guild.pk, route_key
        )


def reset_technology_route(guild: Guild, route_key: str, operator) -> dict[str, int]:
    """取消当前未完成的专精路线，清空等级并返还一半累计投入。"""
    try:
        membership = get_active_membership(guild, operator, "只有帮主和管理员可以重置科技路线")
    except GuildMembershipError as exc:
        raise GuildTechnologyError(str(exc)) from exc
    if not membership.can_manage:
        raise GuildTechnologyError("只有帮主和管理员可以重置科技路线")

    route = next((item for item in TECHNOLOGY_ROUTES if item["key"] == route_key), None)
    if route is None:
        raise GuildTechnologyError("科技路线不存在")

    with transaction.atomic():
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)
        if getattr(membership, "pk", None) is not None:
            try:
                membership = lock_active_member_for_guild(
                    membership,
                    error_msg="只有帮主和管理员可以重置科技路线",
                )
            except GuildMembershipError as exc:
                raise GuildTechnologyError(str(exc)) from exc
        if not membership.can_manage:
            raise GuildTechnologyError("只有帮主和管理员可以重置科技路线")
        if guild_locked.active_technology_route != route_key:
            raise GuildTechnologyError("只能重置当前正在研究的路线")

        route_tech_keys = [node["key"] for node in route["nodes"]]
        tech_rows = list(
            GuildTechnology.objects.select_for_update().filter(guild=guild_locked, tech_key__in=route_tech_keys)
        )
        levels = {tech.tech_key: int(tech.level) for tech in tech_rows}
        if is_technology_route_complete(route, levels):
            raise GuildTechnologyError("已完成的科技路线不能重置")

        if RESOURCE_PRODUCTION_TECH_KEYS.intersection(route_tech_keys):
            _settle_guild_member_resource_production_locked(guild_locked, now=timezone.now())

        invested = _calculate_route_investment(guild_locked, route, levels)
        refund = {resource_key: amount // 2 for resource_key, amount in invested.items()}
        silver_refund = refund.get("silver", 0)
        warehouse_refund = {
            resource_key: amount
            for resource_key, amount in refund.items()
            if resource_key in WAREHOUSE_TECH_RESOURCE_KEYS and amount > 0
        }

        if silver_refund:
            Guild.objects.filter(pk=guild_locked.pk).update(silver=F("silver") + silver_refund)
        _credit_guild_warehouse_items_locked(guild_locked, warehouse_refund)
        GuildTechnology.objects.filter(guild=guild_locked, tech_key__in=route_tech_keys).update(
            level=0,
            last_production_at=None,
        )
        Guild.objects.filter(pk=guild_locked.pk).update(active_technology_route="")

        GuildResourceLog.objects.create(
            guild=guild_locked,
            action="tech_reset",
            silver_change=silver_refund,
            grain_change=refund.get("grain", 0),
            gold_bar_change=refund.get("gold_bar", 0),
            related_user=operator,
            note=f"取消{route['title']}路线并清零等级，返还已投入资源的50%（{_format_tech_resource_cost(refund)}）",
        )

    return refund


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


def calculate_guild_lineup_capacity(level: int) -> int:
    """根据已加载的科技等级计算上阵容量。"""
    return min(
        MAX_GUILD_LINEUP_CAPACITY,
        int(guild_constants.GUILD_BATTLE_LINEUP_LIMIT) + level,
    )


def calculate_guild_dispatch_capacity(level: int) -> int:
    """根据已加载的科技等级计算派遣容量。"""
    return min(
        MAX_GUILD_DISPATCH_CAPACITY,
        int(guild_constants.GUILD_DISPATCH_GUEST_BASE_LIMIT) + level,
    )


def get_guild_lineup_capacity(guild):
    return calculate_guild_lineup_capacity(get_guild_tech_level(guild, "guild_lineup_capacity"))


def get_guild_dispatch_capacity(guild):
    return calculate_guild_dispatch_capacity(get_guild_tech_level(guild, "guild_dispatch_capacity"))


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
        return get_guild_resource_production_bonus(guild, "grain") + get_guild_resource_production_bonus(
            guild, "silver"
        )

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
