from django.contrib.auth import get_user_model
from django.db import models

from .base import Guild
from .member import GuildMember

User = get_user_model()


class GuildExchangeLog(models.Model):
    """兑换日志"""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="exchange_logs", verbose_name="所属帮会")
    member = models.ForeignKey(
        GuildMember, on_delete=models.CASCADE, related_name="exchange_logs", verbose_name="兑换成员"
    )
    item_key = models.CharField(max_length=100, verbose_name="物品key")
    quantity = models.PositiveIntegerField(default=1, verbose_name="兑换数量")
    contribution_cost = models.PositiveIntegerField(verbose_name="消耗贡献")
    exchanged_at = models.DateTimeField(auto_now_add=True, verbose_name="兑换时间")

    class Meta:
        db_table = "guild_exchange_logs"
        verbose_name = "兑换日志"
        verbose_name_plural = "兑换日志"
        ordering = ["-exchanged_at"]

    def __str__(self):
        return f"{self.member.user.username} - {self.item_key} x{self.quantity}"


class GuildDonationLog(models.Model):
    """捐赠日志"""

    RESOURCE_CHOICES = [
        ("silver", "银两"),
        ("grain", "粮食"),
        ("gold_bar", "金条"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="donation_logs", verbose_name="所属帮会")
    member = models.ForeignKey(
        GuildMember, on_delete=models.CASCADE, related_name="donation_logs", verbose_name="捐赠成员"
    )
    resource_type = models.CharField(max_length=10, choices=RESOURCE_CHOICES, verbose_name="资源类型")
    amount = models.PositiveIntegerField(verbose_name="捐赠数量")
    contribution_gained = models.PositiveIntegerField(verbose_name="获得贡献")
    donated_at = models.DateTimeField(auto_now_add=True, verbose_name="捐赠时间")

    class Meta:
        db_table = "guild_donation_logs"
        verbose_name = "捐赠日志"
        verbose_name_plural = "捐赠日志"
        ordering = ["-donated_at"]

    def __str__(self):
        return f"{self.member.user.username} - {self.get_resource_type_display()} x{self.amount}"


class GuildResourceLog(models.Model):
    """帮会资源流水"""

    ACTION_CHOICES = [
        ("donation", "成员捐赠"),
        ("tech_upgrade", "科技升级"),
        ("guild_upgrade", "帮会升级"),
        ("production", "科技产出"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="resource_logs", verbose_name="所属帮会")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="操作类型")
    silver_change = models.IntegerField(default=0, verbose_name="银两变化")
    grain_change = models.IntegerField(default=0, verbose_name="粮食变化")
    gold_bar_change = models.IntegerField(default=0, verbose_name="金条变化")
    related_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="相关玩家")
    note = models.CharField(max_length=200, blank=True, verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="记录时间")

    class Meta:
        db_table = "guild_resource_logs"
        verbose_name = "帮会资源流水"
        verbose_name_plural = "帮会资源流水"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.guild.name} - {self.get_action_display()} @ {self.created_at}"
