from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone


class MissionTemplate(models.Model):
    class Difficulty(models.TextChoices):
        JUNIOR = "junior", "初级"
        INTERMEDIATE = "intermediate", "中级"
        ADVANCED = "advanced", "高级"

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    difficulty = models.CharField(
        max_length=16,
        choices=Difficulty.choices,
        default=Difficulty.JUNIOR,
        verbose_name="难度",
        help_text="任务难度分级",
    )
    battle_type = models.CharField(max_length=32, default="task")
    is_defense = models.BooleanField(default=False, help_text="敌方主动来袭，玩家为防守方")
    guest_only = models.BooleanField(default=False, help_text="仅允许门客出征，不可带护院")
    enemy_guests = models.JSONField(default=list, blank=True)
    enemy_troops = models.JSONField(default=dict, blank=True)
    enemy_technology = models.JSONField(default=dict, blank=True, help_text="敌方护院科技配置")
    entry_cost = models.JSONField(default=dict, blank=True, help_text="任务入场消耗")
    drop_table = models.JSONField(default=dict, blank=True)
    probability_drop_table = models.JSONField(default=dict, blank=True, help_text="概率掉落表")
    available_weekdays = models.JSONField(
        default=list,
        blank=True,
        help_text="任务开放星期，ISO weekday：1=周一，7=周日；空列表表示每日开放",
    )
    base_travel_time = models.PositiveIntegerField(default=1200, help_text="往返基础耗时（秒）")
    daily_limit = models.PositiveIntegerField(default=3)

    class Meta:
        verbose_name = "任务模板"
        verbose_name_plural = "任务模板"

    def __str__(self) -> str:
        return self.name


class MissionRun(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "出征中"
        COMPLETED = "completed", "已返程"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="mission_runs")
    mission = models.ForeignKey(MissionTemplate, on_delete=models.CASCADE, related_name="runs")
    guests = models.ManyToManyField("guests.Guest", related_name="mission_runs", blank=True)
    guest_snapshots = models.JSONField(default=list, blank=True)
    troop_loadout = models.JSONField(default=dict, blank=True)
    battle_report = models.ForeignKey("battle.BattleReport", null=True, blank=True, on_delete=models.SET_NULL)
    travel_time = models.PositiveIntegerField(default=0, help_text="单程耗时（秒）")
    started_at = models.DateTimeField(auto_now_add=True)
    return_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    is_retreating = models.BooleanField(default=False)

    class Meta:
        verbose_name = "任务出征"
        verbose_name_plural = "任务出征"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["status", "return_at"]),
            models.Index(fields=["manor", "status"]),
            models.Index(fields=["manor", "-started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.manor} - {self.mission.name}"

    @property
    def battle_at(self):
        """战斗时间点（去程结束时间）"""
        if not self.started_at or not self.travel_time:
            return None
        return self.started_at + timedelta(seconds=self.travel_time)

    @property
    def is_returning(self) -> bool:
        """是否处于返程阶段"""
        if self.is_retreating:
            return True
        battle_at = self.battle_at
        if not battle_at:
            return False
        return timezone.now() >= battle_at

    @property
    def next_state_at(self):
        """下一个状态的时间点（用于事件栏显示）"""
        if self.is_returning:
            return self.return_at
        return self.battle_at

    @property
    def time_remaining(self) -> int:
        next_at = self.next_state_at
        if not next_at:
            return 0
        delta = next_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class MissionExtraAttempt(models.Model):
    """
    任务额外次数记录（通过任务卡获得）

    每条记录表示某任务在某日获得的额外尝试次数。
    次数按自然日（服务器时区00:00）重置。
    """

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="mission_extra_attempts")
    mission = models.ForeignKey(MissionTemplate, on_delete=models.CASCADE, related_name="extra_attempts")
    date = models.DateField(help_text="额外次数生效的日期")
    extra_count = models.PositiveIntegerField(default=0, help_text="已使用任务卡增加的次数")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "任务额外次数"
        verbose_name_plural = "任务额外次数"
        unique_together = ("manor", "mission", "date")
        indexes = [
            models.Index(fields=["manor", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.manor} - {self.mission.name} - {self.date} (+{self.extra_count})"
