from django.db import models
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from core.utils.time_scale import scale_duration

from .base import Guild
from .member import GuildMember


class GuildMissionTemplate(models.Model):
    """帮会任务模板。"""

    class TaskType(models.TextChoices):
        GUEST = "guest", "门客"
        TROOP = "troop", "护院"
        DEFENSE = "defense", "防守"

    DIFFICULTY_CHOICES = [
        ("junior", "初级"),
        ("intermediate", "中级"),
        ("advanced", "高级"),
    ]

    key = models.SlugField(unique=True, verbose_name="任务标识")
    name = models.CharField(max_length=64, verbose_name="任务名称")
    description = models.TextField(blank=True, verbose_name="任务描述")
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="junior", verbose_name="难度")
    task_type = models.CharField(
        max_length=20,
        choices=TaskType.choices,
        default=TaskType.GUEST,
        verbose_name="任务类型",
    )
    base_duration_seconds = models.PositiveIntegerField(default=600, verbose_name="基础耗时(秒)")
    ruby_reward = models.PositiveIntegerField(default=0, verbose_name="红宝石奖励")
    recommended_guest_count = models.PositiveSmallIntegerField(default=1, verbose_name="推荐门客数")
    allow_troops = models.BooleanField(default=False, verbose_name="允许携带护院")
    enemy_guests = models.JSONField(default=list, blank=True, verbose_name="敌方门客配置")
    enemy_troops = models.JSONField(default=dict, blank=True, verbose_name="敌方护院配置")
    enemy_technology = models.JSONField(default=dict, blank=True, verbose_name="敌方科技配置")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    sort_weight = models.IntegerField(default=0, verbose_name="排序权重")

    class Meta:
        db_table = "guild_mission_templates"
        verbose_name = "帮会任务模板"
        verbose_name_plural = "帮会任务模板"
        ordering = ["-is_active", "sort_weight", "id"]

    def __str__(self) -> str:
        return f"{self.name}({self.key})"

    @property
    def actual_duration_seconds(self) -> int:
        return scale_duration(self.base_duration_seconds, minimum=1)


class GuildMissionRun(models.Model):
    """帮会任务进行记录。"""

    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        COMPLETED = "completed", "已完成"
        RETREATED = "retreated", "已撤退"

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="mission_runs", verbose_name="所属帮会")
    template = models.ForeignKey(
        GuildMissionTemplate,
        on_delete=models.PROTECT,
        related_name="mission_runs",
        verbose_name="任务模板",
    )
    started_by = models.ForeignKey(
        GuildMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="started_mission_runs",
        verbose_name="发起成员",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, verbose_name="状态")
    selected_guest_count = models.PositiveSmallIntegerField(default=1, verbose_name="参战门客数")
    ruby_reward = models.PositiveIntegerField(default=0, verbose_name="结算红宝石")
    guest_ids = models.JSONField(default=list, blank=True, verbose_name="参战门客ID")
    guest_snapshots = models.JSONField(default=list, blank=True, verbose_name="参战门客快照")
    troop_loadout = models.JSONField(default=dict, blank=True, verbose_name="护院编队")
    attacker_troop_tech_snapshot = models.JSONField(default=dict, blank=True, verbose_name="攻击方护院科技快照")
    battle_report = models.ForeignKey(
        BattleReport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guild_mission_runs",
        verbose_name="战报",
    )
    started_at = models.DateTimeField(default=timezone.now, verbose_name="开始时间")
    battle_at = models.DateTimeField(null=True, blank=True, verbose_name="开战时间")
    return_at = models.DateTimeField(null=True, blank=True, verbose_name="返程时间")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    class Meta:
        db_table = "guild_mission_runs"
        verbose_name = "帮会任务运行"
        verbose_name_plural = "帮会任务运行"
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["guild"],
                condition=models.Q(status="active"),
                name="gmr_one_active_per_guild_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["guild", "status", "-started_at"], name="gmr_guild_status_st_idx"),
        ]

    def __str__(self) -> str:
        return f"Guild#{self.guild_id}-{self.template.key}-{self.status}"


class GuildTroopStorage(models.Model):
    """帮会护院库存。"""

    guild = models.ForeignKey(Guild, on_delete=models.CASCADE, related_name="troop_storages", verbose_name="所属帮会")
    troop_template = models.ForeignKey(
        TroopTemplate,
        on_delete=models.PROTECT,
        related_name="guild_storages",
        verbose_name="护院模板",
    )
    count = models.PositiveIntegerField(default=0, verbose_name="库存数量")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "guild_troop_storage"
        verbose_name = "帮会护院库存"
        verbose_name_plural = "帮会护院库存"
        constraints = [
            models.UniqueConstraint(fields=["guild", "troop_template"], name="gts_guild_tpl_uq"),
        ]
        ordering = ["guild_id", "troop_template_id"]

    def __str__(self) -> str:
        return f"Guild#{self.guild_id}-{self.troop_template.key} x{self.count}"


class GuildTroopDonationLog(models.Model):
    """帮会护院捐赠日志。"""

    guild = models.ForeignKey(
        Guild,
        on_delete=models.CASCADE,
        related_name="troop_donation_logs",
        verbose_name="所属帮会",
    )
    member = models.ForeignKey(
        GuildMember,
        on_delete=models.CASCADE,
        related_name="troop_donation_logs",
        verbose_name="捐赠成员",
    )
    troop_template = models.ForeignKey(
        TroopTemplate,
        on_delete=models.PROTECT,
        related_name="guild_donation_logs",
        verbose_name="护院模板",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="捐赠数量")
    donated_at = models.DateTimeField(auto_now_add=True, verbose_name="捐赠时间")

    class Meta:
        db_table = "guild_troop_donation_logs"
        verbose_name = "帮会护院捐赠日志"
        verbose_name_plural = "帮会护院捐赠日志"
        ordering = ["-donated_at", "-id"]
        indexes = [
            models.Index(fields=["guild", "-donated_at"], name="gtdl_guild_donated_idx"),
        ]

    def __str__(self) -> str:
        return f"Guild#{self.guild_id}-Member#{self.member_id}-{self.troop_template.key} x{self.quantity}"
