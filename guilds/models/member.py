from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from .base import Guild

User = get_user_model()


class GuildMember(models.Model):
    """帮会成员"""

    POSITION_CHOICES = [
        ("leader", "帮主"),
        ("admin", "管理员"),
        ("member", "成员"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="members", verbose_name="所属帮会")
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="guild_membership", verbose_name="玩家")
    position = models.CharField(max_length=10, choices=POSITION_CHOICES, default="member", verbose_name="职位")

    # 贡献统计
    total_contribution = models.PositiveIntegerField(
        default=0, verbose_name="总贡献", help_text="历史累计贡献（包括已消费的）"
    )
    current_contribution = models.PositiveIntegerField(default=0, verbose_name="当前贡献", help_text="可用于兑换的贡献")
    weekly_contribution = models.PositiveIntegerField(default=0, verbose_name="本周贡献", help_text="每周一0点重置")
    weekly_reset_at = models.DateField(default=timezone.now, verbose_name="本周重置时间")

    # 时间记录
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name="加入时间")
    last_active_at = models.DateTimeField(auto_now=True, verbose_name="最后活跃时间")

    # 状态
    is_active = models.BooleanField(default=True, verbose_name="是否在帮")
    left_at = models.DateTimeField(null=True, blank=True, verbose_name="离开时间")

    # 捐赠限制（每日）
    daily_donation_silver = models.PositiveIntegerField(default=0, verbose_name="今日捐赠银两")
    daily_donation_grain = models.PositiveIntegerField(default=0, verbose_name="今日捐赠粮食")
    daily_donation_gold_bar = models.PositiveIntegerField(default=0, verbose_name="今日捐赠金条")
    daily_donation_reset_at = models.DateField(default=timezone.now, verbose_name="每日捐赠重置时间")

    # 兑换限制（每日）
    daily_exchange_count = models.PositiveIntegerField(default=0, verbose_name="今日兑换次数")
    daily_exchange_reset_at = models.DateField(default=timezone.now, verbose_name="每日兑换重置时间")

    class Meta:
        db_table = "guild_members"
        verbose_name = "帮会成员"
        verbose_name_plural = "帮会成员"
        unique_together = [["guild", "user"]]
        ordering = ["-position", "-total_contribution"]
        indexes = [
            models.Index(fields=["guild", "is_active"], name="guildmember_guild_active_idx"),
            models.Index(fields=["guild", "position", "is_active"], name="guildmember_guild_pos_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} @ {self.guild.name} ({self.get_position_display()})"

    @property
    def is_leader(self):
        return self.position == "leader"

    @property
    def is_admin(self):
        return self.position == "admin"

    @property
    def can_manage(self):
        """是否有管理权限"""
        return self.position in ["leader", "admin"]

    def reset_weekly_contribution(self):
        """重置本周贡献"""
        self.weekly_contribution = 0
        self.weekly_reset_at = timezone.now().date()
        self.save(update_fields=["weekly_contribution", "weekly_reset_at"])

    def reset_daily_limits(self):
        """重置每日限制"""
        today = timezone.now().date()
        updated = False

        if self.daily_donation_reset_at is None or self.daily_donation_reset_at < today:
            self.daily_donation_silver = 0
            self.daily_donation_grain = 0
            self.daily_donation_gold_bar = 0
            self.daily_donation_reset_at = today
            updated = True

        if self.daily_exchange_reset_at is None or self.daily_exchange_reset_at < today:
            self.daily_exchange_count = 0
            self.daily_exchange_reset_at = today
            updated = True

        if updated:
            self.save()
