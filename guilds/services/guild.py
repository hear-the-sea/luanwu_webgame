import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import GuildMembershipError, GuildPermissionError, GuildValidationError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services.utils.messages import bulk_create_messages

from .. import constants as guild_constants
from ..models import Guild, GuildAnnouncement, GuildHeroPoolEntry, GuildMember, GuildTechnology
from .utils import get_active_membership

logger = logging.getLogger(__name__)


def validate_guild_name(name: str) -> None:
    """
    校验帮会名称

    Args:
        name: 帮会名称

    Raises:
        GuildValidationError: 校验失败时抛出
    """
    if not name or not name.strip():
        raise GuildValidationError("帮会名称不能为空")

    name = name.strip()

    if len(name) < guild_constants.GUILD_NAME_MIN_LENGTH:
        raise GuildValidationError(f"帮会名称至少需要{guild_constants.GUILD_NAME_MIN_LENGTH}个字符")

    if len(name) > guild_constants.GUILD_NAME_MAX_LENGTH:
        raise GuildValidationError(f"帮会名称最多{guild_constants.GUILD_NAME_MAX_LENGTH}个字符")

    if not guild_constants.GUILD_NAME_PATTERN.match(name):
        raise GuildValidationError("帮会名称只能包含中文、英文、数字和下划线")


def create_guild(user, name, description="", emblem="default"):
    """
    创建帮会

    Args:
        user: 创建者User对象
        name: 帮会名称
        description: 帮会简介
        emblem: 帮会徽章key

    Returns:
        Guild对象

    Raises:
        GuildValidationError | GuildMembershipError: 验证失败
    """
    from gameplay.models import InventoryItem, ItemTemplate

    # 校验帮会名称格式
    name = name.strip() if name else ""
    validate_guild_name(name)

    required_gold_bars = guild_constants.GUILD_CREATION_COST["gold_bar"]

    with transaction.atomic():
        # 并发安全：锁定并校验当前用户的帮会成员记录（OneToOneField 防止重复入帮）
        membership = GuildMember.objects.select_for_update().filter(user=user).first()
        if membership and membership.is_active:
            raise GuildMembershipError("您已加入帮会，无法创建新帮会")
        if membership and not membership.is_active:
            membership.delete()

        # 锁定庄园行（同时作为创建流程的串行化锚点）
        manor = Manor.objects.select_for_update().get(user=user)

        # 锁定并扣减金条库存（仓库）
        try:
            gold_bar_template = ItemTemplate.objects.get(key="gold_bar")
        except ItemTemplate.DoesNotExist:
            raise GuildValidationError("金条物品不存在，请联系管理员")

        gold_bar_item = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template=gold_bar_template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .first()
        )
        if not gold_bar_item or gold_bar_item.quantity < required_gold_bars:
            raise GuildValidationError(f"金条不足，需要{required_gold_bars}金条")

        updated = InventoryItem.objects.filter(pk=gold_bar_item.pk, quantity__gte=required_gold_bars).update(
            quantity=F("quantity") - required_gold_bars
        )
        if not updated:
            raise GuildValidationError(f"金条不足，需要{required_gold_bars}金条")
        gold_bar_item.refresh_from_db(fields=["quantity"])
        if gold_bar_item.quantity == 0:
            gold_bar_item.delete()

        # 创建帮会
        try:
            guild = Guild.objects.create(
                name=name,
                description=description,
                emblem=emblem,
                founder=user,
                level=1,
            )
        except IntegrityError:
            # unique=True on Guild.name
            raise GuildValidationError("帮会名称已存在")

        # 创建者自动成为帮主
        try:
            GuildMember.objects.create(
                guild=guild,
                user=user,
                position="leader",
            )
        except IntegrityError:
            raise GuildMembershipError("您已加入帮会，无法创建新帮会")

        # 初始化帮会科技（等级0）
        initialize_guild_technologies(guild)

        # 发布系统公告
        create_announcement(
            guild,
            "system",
            f"帮会成立！帮主{manor.display_name}创建了{name}！",
        )

    return guild


def upgrade_guild(guild, operator):
    """
    升级帮会

    Args:
        guild: Guild对象
        operator: 操作者User对象

    Raises:
        GuildPermissionError | GuildValidationError: 验证失败
    """
    # 验证权限
    membership = get_active_membership(guild, operator, "只有帮主可以升级帮会")
    if not membership.is_leader:
        raise GuildPermissionError("只有帮主可以升级帮会")

    # 验证等级
    if guild.level >= 10:
        raise GuildValidationError("帮会已达最高等级")

    # 并发安全的事务处理
    with transaction.atomic():
        # 步骤1：锁定帮会，防止并发升级
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)

        # 步骤2：在锁内重新验证条件，防止并发穿透
        if guild_locked.level >= 10:
            raise GuildValidationError("帮会已达最高等级")

        # 计算升级成本（必须在锁内根据当前等级计算，避免并发下低价升级）
        cost = calculate_guild_upgrade_cost(guild_locked.level)

        if guild_locked.gold_bar < cost:
            raise GuildValidationError(f"帮会金条不足，需要{cost}金条")

        # 步骤3：使用F()表达式原子性地扣除金条并提升等级
        Guild.objects.filter(pk=guild_locked.pk).update(gold_bar=F("gold_bar") - cost, level=F("level") + 1)

        # 刷新对象以获取更新后的值（用于日志和公告）
        guild_locked.refresh_from_db(fields=["level", "gold_bar"])

        # 步骤4：记录资源流水
        from ..models import GuildResourceLog

        GuildResourceLog.objects.create(
            guild=guild_locked,
            action="guild_upgrade",
            gold_bar_change=-cost,
            related_user=operator,
            note=f"帮会升级至{guild_locked.level}级",
        )

        # 步骤5：获取操作者庄园名称并发布公告
        operator_manor = Manor.objects.get(user=operator)
        create_announcement(
            guild_locked,
            "system",
            f"{operator_manor.display_name}将帮会提升至{guild_locked.level}级！成员上限增加至{guild_locked.member_capacity}人。",
        )


def update_guild_info(guild, operator, description="", auto_accept=False):
    """
    更新帮会简介与自动审批配置。

    Args:
        guild: Guild对象
        operator: 操作者User对象
        description: 帮会简介
        auto_accept: 是否自动接受申请

    Returns:
        更新后的 Guild 对象

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    normalized_description = (description or "").strip()[:200]

    with transaction.atomic():
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk, is_active=True)
        membership = (
            GuildMember.objects.select_for_update().filter(guild=guild_locked, user=operator, is_active=True).first()
        )
        if not membership:
            raise GuildMembershipError("您不是该帮会成员")
        if not membership.is_leader:
            raise GuildPermissionError("只有帮主可以修改帮会信息")

        guild_locked.description = normalized_description
        guild_locked.auto_accept = bool(auto_accept)
        guild_locked.save(update_fields=["description", "auto_accept"])

    return guild_locked


def calculate_guild_upgrade_cost(current_level):
    """计算帮会升级成本"""
    if current_level >= 10:
        return None
    return guild_constants.GUILD_UPGRADE_BASE_COST * (2 ** (current_level - 1))


def initialize_guild_technologies(guild):
    """初始化帮会科技"""
    tech_configs = [
        # 生产类
        ("equipment_forge", "production", 5),
        ("experience_refine", "production", 5),
        ("resource_supply", "production", 5),
        # 战斗类
        ("military_study", "combat", 5),
        ("troop_tactics", "combat", 5),
        # 福利类
        ("resource_boost", "welfare", 5),
        ("march_speed", "welfare", 5),
        # 帮会任务容量科技
        ("guild_lineup_capacity", "combat", 20),
        ("guild_dispatch_capacity", "combat", 20),
    ]

    technologies_to_create = [
        GuildTechnology(
            guild=guild,
            tech_key=tech_key,
            category=category,
            level=0,
            max_level=max_level,
        )
        for tech_key, category, max_level in tech_configs
    ]
    GuildTechnology.objects.bulk_create(technologies_to_create)


def disband_guild(guild, operator):
    """
    解散帮会

    Args:
        guild: Guild对象
        operator: 操作者User对象

    Raises:
        GuildPermissionError: 验证失败
    """
    # 验证权限
    membership = get_active_membership(guild, operator, "只有帮主可以解散帮会")
    if not membership.is_leader:
        raise GuildPermissionError("只有帮主可以解散帮会")

    # 预加载成员 user_id 和对应的 manor（避免事务中的 N+1 查询）
    member_user_ids = list(guild.members.filter(is_active=True).values_list("user_id", flat=True))
    user_to_manor = {m.user_id: m for m in Manor.objects.filter(user_id__in=member_user_ids)}
    guild_name = guild.name  # 缓存名称，事务外使用

    with transaction.atomic():
        # 标记帮会为不活跃
        guild.is_active = False
        guild.save(update_fields=["is_active"])

        # 标记所有成员为离开
        guild.members.filter(is_active=True).update(
            is_active=False,
            left_at=timezone.now(),
        )
        GuildHeroPoolEntry.objects.filter(guild=guild).delete()

    messages_data = []
    for user_id in member_user_ids:
        manor = user_to_manor.get(user_id)
        if manor:
            messages_data.append(
                {
                    "manor": manor,
                    "kind": "system",
                    "title": "帮会解散通知",
                    "body": f"您所在的帮会【{guild_name}】已被帮主解散。",
                }
            )

    if messages_data:
        try:
            bulk_create_messages(messages_data)
        except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
            logger.exception(
                "Guild disband follow-up message send failed: guild_id=%s member_count=%s",
                guild.id,
                len(messages_data),
            )


def get_guild_list(ordering="-level", search=None, page=1, page_size=20):
    """
    获取帮会列表

    Args:
        ordering: 排序字段
        search: 搜索关键词
        page: 页码
        page_size: 每页数量

    Returns:
        QuerySet (带 _member_count 注解，优化 N+1)
    """
    # 使用自定义管理器预加载成员数（优化模板中的 N+1）
    queryset = Guild.objects.with_member_count().filter(is_active=True)

    if search:
        queryset = queryset.filter(name__icontains=search)

    # 如果按成员数排序，使用预加载的 _member_count
    if ordering in ["-current_member_count", "current_member_count"]:
        if ordering == "-current_member_count":
            queryset = queryset.order_by("-_member_count")
        else:
            queryset = queryset.order_by("_member_count")
    else:
        queryset = queryset.order_by(ordering)

    # 简单分页
    start = (page - 1) * page_size
    end = start + page_size

    return queryset[start:end]


def create_announcement(guild, type, content, author=None):
    """
    创建帮会公告

    Args:
        guild: Guild对象
        type: 'system' 或 'leader'
        content: 公告内容
        author: 发布人User对象（leader类型必须）
    """
    GuildAnnouncement.objects.create(
        guild=guild,
        type=type,
        content=content,
        author=author,
    )

    # 性能优化：保留最近30条公告，使用数据库层面的高效删除
    # 先获取需要保留的公告ID（前30条），然后删除不在列表中的
    keep_ids = list(guild.announcements.order_by("-created_at").values_list("id", flat=True)[:30])
    if keep_ids:
        guild.announcements.exclude(id__in=keep_ids).delete()
