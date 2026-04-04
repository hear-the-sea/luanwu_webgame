from __future__ import annotations

from django.db import models
from django.utils import timezone


class GuildRaidRun(models.Model):
    """帮会掠夺出征记录。"""

    class Status(models.TextChoices):
        MARCHING = "marching", "行军中"
        BATTLING = "battling", "战斗中"
        RETURNING = "returning", "返程中"
        COMPLETED = "completed", "已完成"
        RETREATED = "retreated", "已撤退"

    attacker_guild = models.ForeignKey(
        "guilds.Guild",
        on_delete=models.CASCADE,
        related_name="raid_runs_sent",
        verbose_name="进攻帮会",
    )
    defender_guild = models.ForeignKey(
        "guilds.Guild",
        on_delete=models.CASCADE,
        related_name="raid_runs_received",
        verbose_name="防守帮会",
    )
    started_by = models.ForeignKey(
        "guilds.GuildMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="started_guild_raid_runs",
        verbose_name="发起成员",
    )
    selected_guest_count = models.PositiveIntegerField("出征门客数量", default=0)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.MARCHING)
    guest_ids = models.JSONField("出征门客ID", default=list, blank=True)
    guest_snapshots = models.JSONField("出征门客快照", default=list, blank=True)
    troop_loadout = models.JSONField("护院编队", default=dict, blank=True)
    travel_time = models.PositiveIntegerField("单程行军时间(秒)", default=0)
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guild_raid_runs",
        verbose_name="战报",
    )
    loot_silver = models.PositiveIntegerField("掠夺银两", default=0)
    loot_items = models.JSONField("掠夺物品", default=dict, blank=True)
    battle_rewards = models.JSONField("战斗奖励", default=dict, blank=True)
    blocked_reason = models.CharField("阻塞原因", max_length=64, blank=True, default="")
    is_attacker_victory = models.BooleanField("进攻方是否胜利", null=True, blank=True)
    started_at = models.DateTimeField("出发时间", default=timezone.now)
    battle_at = models.DateTimeField("开战时间", null=True, blank=True)
    return_at = models.DateTimeField("返程时间", null=True, blank=True)
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)

    class Meta:
        db_table = "guild_raid_runs"
        verbose_name = "帮会掠夺出征"
        verbose_name_plural = "帮会掠夺出征"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["attacker_guild", "status", "-started_at"], name="grr_attacker_status_idx"),
            models.Index(fields=["defender_guild", "status", "-started_at"], name="grr_defender_status_idx"),
            models.Index(fields=["status", "battle_at"], name="grr_status_battle_idx"),
            models.Index(fields=["status", "return_at"], name="grr_status_return_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.attacker_guild.name} -> {self.defender_guild.name} ({self.get_status_display()})"
