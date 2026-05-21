from __future__ import annotations

from django.db import models
from django.utils import timezone


class ScoutRecord(models.Model):
    """侦察记录"""

    class Status(models.TextChoices):
        SCOUTING = "scouting", "侦察中"
        RETURNING = "returning", "返程中"
        SUCCESS = "success", "侦察成功"
        FAILED = "failed", "侦察失败"

    attacker = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="scout_records_sent",
        verbose_name="发起方",
    )
    defender = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="scout_records_received",
        verbose_name="目标方",
    )
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.SCOUTING)
    scout_cost = models.PositiveIntegerField("消耗探子数", default=1)
    success_rate = models.FloatField("成功率", default=0.5)
    travel_time = models.PositiveIntegerField("单程时间(秒)", default=60)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("到达时间")
    return_at = models.DateTimeField("返程完成时间", null=True, blank=True)
    completed_at = models.DateTimeField("实际完成时间", null=True, blank=True)
    is_success = models.BooleanField("是否成功", null=True, blank=True)
    was_retreated = models.BooleanField("是否主动撤退", default=False)

    # 侦察结果（成功时填充）
    intel_data = models.JSONField(
        "情报数据", default=dict, blank=True, help_text="包含目标庄园的门客、兵力、资源等信息"
    )

    class Meta:
        verbose_name = "侦察记录"
        verbose_name_plural = "侦察记录"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["attacker", "defender", "-started_at"]),
            models.Index(fields=["status", "complete_at"]),
            models.Index(fields=["status", "return_at"], name="scout_status_return_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.attacker.display_name} -> {self.defender.display_name} ({self.get_status_display()})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        now = timezone.now()
        if self.status == self.Status.SCOUTING:
            delta = self.complete_at - now
            return max(0, int(delta.total_seconds()))
        if self.status == self.Status.RETURNING and self.return_at:
            delta = self.return_at - now
            return max(0, int(delta.total_seconds()))
        return 0

    @property
    def next_state_at(self):
        """下一个状态的时间点（用于事件栏显示）"""
        if self.status == self.Status.SCOUTING:
            return self.complete_at
        if self.status == self.Status.RETURNING:
            return self.return_at
        return None


class ScoutCooldown(models.Model):
    """侦察冷却记录（同一目标10分钟冷却）"""

    attacker = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="scout_cooldowns_sent")
    defender = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="scout_cooldowns_received")
    cooldown_until = models.DateTimeField("冷却截止时间")

    class Meta:
        verbose_name = "侦察冷却"
        verbose_name_plural = "侦察冷却"
        unique_together = ("attacker", "defender")
        indexes = [
            models.Index(fields=["attacker", "cooldown_until"]),
        ]

    def __str__(self) -> str:
        return f"{self.attacker.display_name} -> {self.defender.display_name} (冷却至 {self.cooldown_until})"

    @property
    def is_active(self) -> bool:
        """冷却是否仍在生效"""
        return self.cooldown_until > timezone.now()


class RaidRun(models.Model):
    """踢馆出征记录"""

    class Status(models.TextChoices):
        MARCHING = "marching", "行军中"
        BATTLING = "battling", "战斗中"
        RETURNING = "returning", "返程中"
        COMPLETED = "completed", "已完成"
        RETREATED = "retreated", "已撤退"

    attacker = models.ForeignKey(
        "gameplay.Manor", on_delete=models.CASCADE, related_name="raid_runs_sent", verbose_name="进攻方"
    )
    defender = models.ForeignKey(
        "gameplay.Manor", on_delete=models.CASCADE, related_name="raid_runs_received", verbose_name="防守方"
    )
    guests = models.ManyToManyField("guests.Guest", related_name="raid_runs", blank=True, verbose_name="出征门客")
    guest_snapshots = models.JSONField("出征门客快照", default=list, blank=True)
    troop_loadout = models.JSONField("兵种配置", default=dict, blank=True)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.MARCHING)
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="raid_runs",
        verbose_name="战报",
    )

    # 时间相关
    travel_time = models.PositiveIntegerField("单程行军时间(秒)", default=0)
    started_at = models.DateTimeField("出发时间", auto_now_add=True)
    battle_at = models.DateTimeField("战斗时间", null=True, blank=True)
    return_at = models.DateTimeField("返程完成时间", null=True, blank=True)
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)

    # 战利品
    loot_resources = models.JSONField("掠夺资源", default=dict, blank=True)
    loot_items = models.JSONField("掠夺物品", default=dict, blank=True)

    # 声望变化
    attacker_prestige_change = models.IntegerField("进攻方声望变化", default=0)
    defender_prestige_change = models.IntegerField("防守方声望变化", default=0)

    # 撤退标记
    is_retreating = models.BooleanField("是否撤退中", default=False)

    # 战斗结果
    is_attacker_victory = models.BooleanField("进攻方是否胜利", null=True, blank=True)

    # 战斗通用奖励（经验果和装备回收）
    battle_rewards = models.JSONField("战斗奖励", default=dict, blank=True)

    class Meta:
        verbose_name = "踢馆出征"
        verbose_name_plural = "踢馆出征"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["attacker", "status", "-started_at"]),
            models.Index(fields=["defender", "status", "-started_at"]),
            # 性能：支持按 defender + started_at 统计 24h 被攻击次数（不按 status 过滤）
            models.Index(fields=["defender", "-started_at"], name="raidrun_def_start_idx"),
            models.Index(fields=["status", "battle_at"]),
            models.Index(fields=["status", "return_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.attacker.display_name} -> {self.defender.display_name} ({self.get_status_display()})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        now = timezone.now()
        if self.status == self.Status.MARCHING:
            if self.battle_at:
                delta = self.battle_at - now
                return max(0, int(delta.total_seconds()))
        elif self.status in {self.Status.RETURNING, self.Status.RETREATED}:
            if self.return_at:
                delta = self.return_at - now
                return max(0, int(delta.total_seconds()))
        return 0

    @property
    def can_retreat(self) -> bool:
        """是否可以撤退（仅在行军中且未开始撤退时可撤退）"""
        return self.status == self.Status.MARCHING and not self.is_retreating

    @property
    def next_state_at(self):
        """下一个状态的时间点（用于事件栏/倒计时显示）"""
        if self.status == self.Status.MARCHING:
            return self.battle_at
        if self.status in {self.Status.RETURNING, self.Status.RETREATED, self.Status.BATTLING}:
            return self.return_at
        return None

    @property
    def arrive_at(self):
        """兼容旧模板字段：预计到达时间（行军结束进入战斗时刻）"""
        return self.battle_at


class OathBond(models.Model):
    """结义关系：结义门客不可被俘获。"""

    manor = models.ForeignKey(
        "gameplay.Manor", on_delete=models.CASCADE, related_name="oath_bonds", verbose_name="庄园"
    )
    guest = models.OneToOneField(
        "guests.Guest", on_delete=models.CASCADE, related_name="oath_bond", verbose_name="门客"
    )
    created_at = models.DateTimeField("结义时间", auto_now_add=True)

    class Meta:
        verbose_name = "结义关系"
        verbose_name_plural = "结义关系"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["manor", "-created_at"], name="oathbond_manor_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.display_name} - {self.guest.display_name}"


class JailPrisoner(models.Model):
    """监牢囚徒记录：踢馆胜利方俘获失败方出战门客。"""

    class Status(models.TextChoices):
        HELD = "held", "关押中"
        RECRUITED = "recruited", "已招募"
        RELEASED = "released", "已释放"

    captor = models.ForeignKey(
        "gameplay.Manor", on_delete=models.CASCADE, related_name="jail_prisoners", verbose_name="俘获方"
    )
    original_manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="captured_prisoners",
        verbose_name="原属庄园",
    )
    guest_template = models.ForeignKey(
        "guests.GuestTemplate",
        on_delete=models.PROTECT,
        related_name="captured_prisoners",
        verbose_name="门客模板",
    )
    original_guest_name = models.CharField("原门客名", max_length=64, blank=True, default="")
    original_level = models.PositiveIntegerField("原等级", default=1)
    loyalty = models.PositiveIntegerField("忠诚度", default=25)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.HELD, db_index=True)
    captured_at = models.DateTimeField("俘获时间", auto_now_add=True, db_index=True)
    raid_run = models.ForeignKey(
        "gameplay.RaidRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captured_prisoners",
        verbose_name="关联踢馆",
    )

    class Meta:
        verbose_name = "监牢囚徒"
        verbose_name_plural = "监牢囚徒"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["captor", "status", "-captured_at"], name="jail_captor_status_ca_idx"),
            models.Index(fields=["original_manor", "-captured_at"], name="jail_orig_ca_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.captor.display_name} 囚徒 {self.original_guest_name or self.guest_template.name}"

    @property
    def display_name(self) -> str:
        return self.original_guest_name or self.guest_template.name
