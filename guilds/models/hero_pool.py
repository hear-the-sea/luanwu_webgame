from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .base import Guild
from .member import GuildMember


class GuildHeroPoolEntry(models.Model):
    """帮会门客池条目（成员最多2个槽位，映射真实门客）。

    ``source_guest`` 只提供帮会阵容的快照来源，不代表帮会拥有或占用该门客；
    玩家门客叛逃时由外键级联清理，成员离会时由成员生命周期服务清理。
    """

    guild = models.ForeignKey(
        Guild, on_delete=models.CASCADE, related_name="hero_pool_entries", verbose_name="所属帮会"
    )
    owner_member = models.ForeignKey(
        GuildMember,
        on_delete=models.CASCADE,
        related_name="hero_pool_entries",
        verbose_name="所属成员",
    )
    source_guest = models.ForeignKey(
        "guests.Guest",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="guild_hero_pool_entries",
        verbose_name="源门客",
    )
    slot_index = models.PositiveSmallIntegerField(
        verbose_name="槽位",
        validators=[MinValueValidator(1), MaxValueValidator(2)],
        help_text="每位成员最多2个门客槽位",
    )
    last_submitted_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="最后提交时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "guild_hero_pool_entries"
        verbose_name = "帮会门客池"
        verbose_name_plural = "帮会门客池"
        ordering = ["guild_id", "owner_member_id", "slot_index"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_index__gte=1) & models.Q(slot_index__lte=2),
                name="ghp_slot_range_ck",
            ),
            models.UniqueConstraint(
                fields=["guild", "owner_member", "slot_index"],
                name="ghp_guild_member_slot_uq",
            ),
            models.UniqueConstraint(
                fields=["guild", "owner_member", "source_guest"],
                condition=models.Q(source_guest__isnull=False),
                name="ghp_guild_member_guest_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["guild", "owner_member"], name="ghp_guild_member_idx"),
            models.Index(fields=["guild", "last_submitted_at"], name="ghp_guild_submit_idx"),
        ]

    def __str__(self) -> str:
        return f"Guild#{self.guild_id}-Member#{self.owner_member_id}-Slot{self.slot_index}"


class GuildBattleLineupEntry(models.Model):
    """帮会出战门客名单（容量可由科技扩展至最多40名）。

    出战时只读取源门客并生成独立快照，帮会战斗不直接驱动源门客状态。
    """

    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="battle_lineup_entries",
        verbose_name="所属帮会",
    )
    pool_entry = models.ForeignKey(
        GuildHeroPoolEntry,
        on_delete=models.CASCADE,
        related_name="lineup_entries",
        verbose_name="门客池条目",
    )
    slot_index = models.PositiveSmallIntegerField(
        verbose_name="出战位",
        validators=[MinValueValidator(1), MaxValueValidator(40)],
        help_text="帮会出战名单最多40名",
    )
    selected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guild_lineup_selected_entries",
        verbose_name="设置人",
    )
    selected_at = models.DateTimeField(auto_now_add=True, verbose_name="设置时间")

    class Meta:
        db_table = "guild_battle_lineup_entries"
        verbose_name = "帮会出战名单"
        verbose_name_plural = "帮会出战名单"
        ordering = ["slot_index", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_index__gte=1) & models.Q(slot_index__lte=40),
                name="gbl_slot_range_ck",
            ),
            models.UniqueConstraint(fields=["guild", "slot_index"], name="gbl_guild_slot_uq"),
            models.UniqueConstraint(fields=["guild", "pool_entry"], name="gbl_guild_pool_uq"),
        ]
        indexes = [
            models.Index(fields=["guild", "selected_at"], name="gbl_guild_sel_idx"),
        ]

    def __str__(self) -> str:
        return f"Guild#{self.guild_id}-Lineup#{self.slot_index}"
