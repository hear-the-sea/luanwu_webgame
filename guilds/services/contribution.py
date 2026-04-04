# guilds/services/contribution.py

from django.db import transaction
from django.db.models import F
from django.utils import timezone

import guilds.constants as guild_constants
from core.exceptions import GuildContributionError
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent
from gameplay.services.resources import spend_resources_locked

from ..models import Guild, GuildDonationLog, GuildMember, GuildResourceLog
from .warehouse import add_item_to_warehouse

# 安全修复：贡献度累积上限，防止PositiveIntegerField溢出（最大2147483647）
MAX_CONTRIBUTION = 2_000_000_000  # 20亿安全上限
GOLD_BAR_ITEM_KEY = "gold_bar"
DONATION_RESOURCE_LABELS = {
    "silver": "银两",
    "grain": "粮食",
    "gold_bar": "金条",
}


def donate_resource(member, resource_type, amount):
    """
    捐赠资源获得贡献（并发安全版本）

    使用数据库行锁和F()表达式确保并发安全：
    - 锁定Manor防止资源透支
    - 锁定Guild防止资源覆盖
    - 锁定GuildMember防止贡献度覆盖和每日上限绕过

    锁定顺序：Manor -> Guild -> GuildMember，避免死锁

    Args:
        member: GuildMember对象
        resource_type: 'silver'、'grain' 或 'gold_bar'
        amount: 捐赠数量

    Raises:
        GuildContributionError: 验证失败
    """
    # 验证资源类型
    if resource_type not in guild_constants.CONTRIBUTION_RATES:
        raise GuildContributionError(f"不支持捐赠{resource_type}")

    # 验证捐赠数量
    if resource_type != GOLD_BAR_ITEM_KEY and amount < guild_constants.MIN_DONATION_AMOUNT:
        raise GuildContributionError(f"单次捐赠最少{guild_constants.MIN_DONATION_AMOUNT}单位")
    if resource_type == GOLD_BAR_ITEM_KEY and amount < 1:
        raise GuildContributionError("金条捐赠数量必须大于0")

    # 安全修复：添加单次捐赠上限，防止整数溢出和异常数据
    MAX_DONATION_AMOUNT = 100_000_000  # 1亿上限
    if amount > MAX_DONATION_AMOUNT:
        raise GuildContributionError(f"单次捐赠最多{MAX_DONATION_AMOUNT:,}单位")

    # 获取今日日期，用于重置每日统计
    today = timezone.localdate()

    # 并发安全的事务处理
    with transaction.atomic():
        # 锁定顺序：Manor -> Guild -> GuildMember，避免与资源消费等路径产生死锁
        manor = Manor.objects.select_for_update().get(user=member.user)

        # 步骤1：锁定帮会（用于增加资源）
        guild_locked = Guild.objects.select_for_update().get(pk=member.guild_id)

        # 步骤2：锁定成员并验证每日捐赠上限
        member_locked = GuildMember.objects.select_for_update().get(pk=member.pk)

        # 在锁内重置每日统计，避免并发绕过上限
        current_daily_silver = member_locked.daily_donation_silver
        current_daily_grain = member_locked.daily_donation_grain
        current_daily_gold_bar = member_locked.daily_donation_gold_bar

        # 如果日期已过，重置每日计数
        if member_locked.daily_donation_reset_at is None or member_locked.daily_donation_reset_at < today:
            current_daily_silver = 0
            current_daily_grain = 0
            current_daily_gold_bar = 0

        daily_limit = guild_constants.DAILY_DONATION_LIMITS.get(resource_type)
        if resource_type == "silver":
            if daily_limit is not None and current_daily_silver + amount > daily_limit:
                raise GuildContributionError(f"今日银两捐赠已达上限（{daily_limit}）")
            new_daily_silver = current_daily_silver + amount
            new_daily_grain = current_daily_grain
            new_daily_gold_bar = current_daily_gold_bar
        elif resource_type == "grain":
            if daily_limit is not None and current_daily_grain + amount > daily_limit:
                raise GuildContributionError(f"今日粮食捐赠已达上限（{daily_limit}）")
            new_daily_silver = current_daily_silver
            new_daily_grain = current_daily_grain + amount
            new_daily_gold_bar = current_daily_gold_bar
        else:
            if daily_limit is not None and current_daily_gold_bar + amount > daily_limit:
                raise GuildContributionError(f"今日金条捐赠已达上限（{daily_limit}）")
            new_daily_silver = current_daily_silver
            new_daily_grain = current_daily_grain
            new_daily_gold_bar = current_daily_gold_bar + amount

        # 计算获得的贡献
        contribution = amount * guild_constants.CONTRIBUTION_RATES[resource_type]

        # 安全修复：检查贡献度累积上限，防止整数溢出
        if member_locked.total_contribution + contribution > MAX_CONTRIBUTION:
            raise GuildContributionError(f"贡献度已达上限（{MAX_CONTRIBUTION:,}）")

        # 步骤3：扣除玩家资源（银两/粮食走庄园资源，金条走仓库InventoryItem）
        if resource_type == GOLD_BAR_ITEM_KEY:
            try:
                gold_bar_template = ItemTemplate.objects.get(key=GOLD_BAR_ITEM_KEY)
            except ItemTemplate.DoesNotExist:
                raise GuildContributionError("金条物品不存在，请联系管理员")

            gold_bar_item = (
                InventoryItem.objects.select_for_update()
                .filter(
                    manor=manor,
                    template=gold_bar_template,
                    storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                )
                .first()
            )
            if not gold_bar_item or gold_bar_item.quantity < amount:
                raise GuildContributionError("金条不足")

            updated = InventoryItem.objects.filter(pk=gold_bar_item.pk, quantity__gte=amount).update(
                quantity=F("quantity") - amount
            )
            if not updated:
                raise GuildContributionError("金条不足")
            gold_bar_item.refresh_from_db(fields=["quantity"])
            if gold_bar_item.quantity == 0:
                gold_bar_item.delete()
        else:
            spend_resources_locked(
                manor, {resource_type: amount}, note="帮会捐献", reason=ResourceEvent.Reason.GUILD_DONATION
            )

        # 步骤4：银两继续写 Guild 字段；粮食/金条写入真实帮会仓库行
        if resource_type == "silver":
            Guild.objects.filter(pk=guild_locked.pk).update(silver=F("silver") + amount)
        else:
            add_item_to_warehouse(
                guild_locked,
                resource_type,
                amount,
                guild_constants.CONTRIBUTION_RATES[resource_type],
            )

        # 步骤5：使用F()表达式原子性地更新成员贡献和每日统计
        # 注意：每日计数不能用F()表达式，因为需要在重置后再累加
        GuildMember.objects.filter(pk=member_locked.pk).update(
            total_contribution=F("total_contribution") + contribution,
            current_contribution=F("current_contribution") + contribution,
            weekly_contribution=F("weekly_contribution") + contribution,
            daily_donation_silver=new_daily_silver,
            daily_donation_grain=new_daily_grain,
            daily_donation_gold_bar=new_daily_gold_bar,
            daily_donation_reset_at=today,
        )

        # 步骤6：记录捐赠日志
        GuildDonationLog.objects.create(
            guild=guild_locked,
            member=member_locked,
            resource_type=resource_type,
            amount=amount,
            contribution_gained=contribution,
        )

        # 步骤7：记录资源流水
        GuildResourceLog.objects.create(
            guild=guild_locked,
            action="donation",
            silver_change=amount if resource_type == "silver" else 0,
            grain_change=amount if resource_type == "grain" else 0,
            gold_bar_change=amount if resource_type == GOLD_BAR_ITEM_KEY else 0,
            related_user=member_locked.user,
            note=f"捐赠{amount}{DONATION_RESOURCE_LABELS.get(resource_type, resource_type)}，获得{contribution}贡献",
        )


def reset_weekly_contributions():
    """重置所有帮会成员的本周贡献（每周一执行）"""
    from datetime import date

    today = date.today()

    # 性能优化：使用批量更新替代循环中的逐个 save()
    # 避免 N 次数据库写入，改为 1 次批量更新
    GuildMember.objects.filter(is_active=True, weekly_reset_at__lt=today).update(
        weekly_contribution=0, weekly_reset_at=today
    )


def get_contribution_ranking(guild, ranking_type="total", limit=10):
    """
    获取贡献排行榜

    Args:
        guild: Guild对象
        ranking_type: 'total'(总贡献) 或 'weekly'(本周贡献)
        limit: 返回数量，None表示返回所有

    Returns:
        QuerySet
    """
    members = guild.members.filter(is_active=True).select_related("user", "user__manor")

    if ranking_type == "weekly":
        members = members.order_by("-weekly_contribution", "-total_contribution")
    else:
        members = members.order_by("-total_contribution", "-weekly_contribution")

    if limit is not None:
        return members[:limit]
    return members


def get_my_contribution_rank(member, ranking_type="total"):
    """
    获取我的贡献排名

    Args:
        member: GuildMember对象
        ranking_type: 'total'(总贡献) 或 'weekly'(本周贡献)

    Returns:
        dict: {'rank': 排名, 'contribution': 贡献值}
    """
    guild = member.guild
    members = guild.members.filter(is_active=True)

    if ranking_type == "weekly":
        higher_ranked = members.filter(weekly_contribution__gt=member.weekly_contribution).count()
        contribution = member.weekly_contribution
    else:
        higher_ranked = members.filter(total_contribution__gt=member.total_contribution).count()
        contribution = member.total_contribution

    return {"rank": higher_ranked + 1, "contribution": contribution}
