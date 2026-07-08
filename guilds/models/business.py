from django.contrib.auth import get_user_model
from django.db import models

from .base import Guild

User = get_user_model()


class GuildTechnology(models.Model):
    """帮会科技"""

    CATEGORY_CHOICES = [
        ("production", "生产类"),
        ("combat", "战斗类"),
        ("welfare", "福利类"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="technologies", verbose_name="所属帮会")
    tech_key = models.CharField(max_length=50, verbose_name="科技标识", help_text="如: equipment_forge, troop_tactics")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="production", verbose_name="科技分类")
    level = models.PositiveIntegerField(default=0, verbose_name="科技等级")
    max_level = models.PositiveIntegerField(default=5, verbose_name="最高等级")

    # 最后产出时间（仅生产类科技）
    last_production_at = models.DateTimeField(null=True, blank=True, verbose_name="最后产出时间")

    class Meta:
        db_table = "guild_technologies"
        verbose_name = "帮会科技"
        verbose_name_plural = "帮会科技"
        unique_together = [["guild", "tech_key"]]

    def __str__(self):
        return f"{self.guild.name} - {self.tech_key} Lv.{self.level}"

    @property
    def effective_max_level(self):
        """返回运行时生效的最高等级。"""
        from guilds.services.technology import get_effective_guild_tech_max_level

        return get_effective_guild_tech_max_level(self.tech_key, self.max_level)

    @property
    def can_upgrade(self):
        """是否可升级"""
        return self.level < self.effective_max_level


class GuildWarehouse(models.Model):
    """帮会仓库"""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="warehouse_items", verbose_name="所属帮会")
    item_key = models.CharField(max_length=100, verbose_name="物品key", help_text="对应ItemTemplate的key")
    quantity = models.PositiveIntegerField(default=0, verbose_name="数量")
    contribution_cost = models.PositiveIntegerField(default=0, verbose_name="兑换成本（贡献度）")

    # 统计
    total_produced = models.PositiveIntegerField(default=0, verbose_name="总产出数量")
    total_exchanged = models.PositiveIntegerField(default=0, verbose_name="总兑换数量")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "guild_warehouse"
        verbose_name = "帮会仓库"
        verbose_name_plural = "帮会仓库"
        unique_together = [["guild", "item_key"]]
        indexes = [
            models.Index(fields=["guild", "-contribution_cost"], name="guildwh_guild_contrib_idx"),
        ]

    def __str__(self):
        return f"{self.guild.name} - {self.item_key} x{self.quantity}"

    @property
    def is_available(self):
        """是否有库存"""
        return self.quantity > 0


class GuildApplication(models.Model):
    """入帮申请"""

    STATUS_CHOICES = [
        ("pending", "待审批"),
        ("approved", "已通过"),
        ("rejected", "已拒绝"),
        ("cancelled", "已取消"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="applications", verbose_name="目标帮会")
    applicant = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="guild_applications", verbose_name="申请人"
    )
    message = models.TextField(max_length=200, blank=True, verbose_name="申请留言")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending", verbose_name="状态")

    # 审批信息
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_applications",
        verbose_name="审批人",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="审批时间")
    review_note = models.TextField(max_length=200, blank=True, verbose_name="审批备注")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="申请时间")

    class Meta:
        db_table = "guild_applications"
        verbose_name = "入帮申请"
        verbose_name_plural = "入帮申请"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["guild", "status", "-created_at"], name="gapp_guild_sts_cr_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["guild", "applicant"],
                condition=models.Q(status="pending"),
                name="uniq_pending_guild_application",
            ),
        ]

    def __str__(self):
        return f"{self.applicant.username} -> {self.guild.name} ({self.get_status_display()})"


class GuildAnnouncement(models.Model):
    """帮会公告"""

    TYPE_CHOICES = [
        ("system", "系统公告"),
        ("leader", "帮主公告"),
    ]

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="announcements", verbose_name="所属帮会")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="system", verbose_name="公告类型")
    content = models.TextField(max_length=500, verbose_name="公告内容")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="发布人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="发布时间")

    class Meta:
        db_table = "guild_announcements"
        verbose_name = "帮会公告"
        verbose_name_plural = "帮会公告"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.guild.name} - {self.get_type_display()} @ {self.created_at}"
