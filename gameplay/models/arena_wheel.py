from __future__ import annotations

from django.db import models


class ArenaWheelEvent(models.Model):
    """竞技场三对三车轮战活动。"""

    class Status(models.TextChoices):
        RECRUITING = "recruiting", "报名中"
        PREPARING = "preparing", "准备中"
        RUNNING = "running", "进行中"
        COMPLETED = "completed", "已结束"
        CANCELLED = "cancelled", "已取消"

    class Team(models.TextChoices):
        A = "A", "甲队"
        B = "B", "乙队"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECRUITING, db_index=True)
    player_limit = models.PositiveSmallIntegerField(default=6)
    guest_limit_per_entry = models.PositiveSmallIntegerField(default=10)
    prepare_duration_seconds = models.PositiveIntegerField(default=120)
    prepare_ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    base_seed = models.PositiveIntegerField(default=0)
    rng_version = models.PositiveSmallIntegerField(default=0)
    battle_engine_version = models.CharField(max_length=16, default="legacy")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    current_bout_number = models.PositiveSmallIntegerField(default=0, verbose_name="当前对决场次")
    team_a_active_entry_id = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="甲队当前选手编号")
    team_b_active_entry_id = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="乙队当前选手编号")
    winner_team = models.CharField(max_length=1, choices=Team.choices, blank=True, default="", verbose_name="获胜队伍")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "竞技场车轮战活动"
        verbose_name_plural = "竞技场车轮战活动"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="recruiting"),
                name="unique_recruiting_arena_wheel_event",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "prepare_ends_at"], name="arena_wheel_status_prep_idx"),
        ]

    def __str__(self) -> str:
        return f"金羽擂台#{self.pk} {self.get_status_display()}"


class ArenaWheelEntry(models.Model):
    """车轮战玩家报名及随机队伍顺序。"""

    class Status(models.TextChoices):
        REGISTERED = "registered", "已报名"
        ACTIVE = "active", "出战中"
        ELIMINATED = "eliminated", "已淘汰"
        COMPLETED = "completed", "已结束"
        CANCELLED = "cancelled", "已撤销"

    event = models.ForeignKey("gameplay.ArenaWheelEvent", on_delete=models.CASCADE, related_name="entries")
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="arena_wheel_entries")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REGISTERED, db_index=True)
    team = models.CharField(
        max_length=1, choices=ArenaWheelEvent.Team.choices, blank=True, default="", verbose_name="所属队伍"
    )
    team_order = models.PositiveSmallIntegerField(default=0, verbose_name="出战顺序")
    debuff_stacks = models.PositiveSmallIntegerField(default=0, verbose_name="久战疲敝层数")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="撤销时间")
    joined_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "竞技场车轮战报名"
        verbose_name_plural = "竞技场车轮战报名"
        ordering = ("joined_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["event", "manor"], name="unique_arena_wheel_event_manor"),
        ]
        indexes = [models.Index(fields=["event", "team", "team_order"], name="arena_wheel_team_order_idx")]

    def __str__(self) -> str:
        return f"{self.manor} - wheel-{self.event_id}"


class ArenaWheelEntryGuest(models.Model):
    """车轮战报名门客及逐场继承的战斗快照。"""

    entry = models.ForeignKey("gameplay.ArenaWheelEntry", on_delete=models.CASCADE, related_name="entry_guests")
    guest = models.ForeignKey(
        "guests.Guest",
        on_delete=models.CASCADE,
        related_name="arena_wheel_entry_links",
        null=True,
        blank=True,
    )
    slot_index = models.PositiveSmallIntegerField(default=0)
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "竞技场车轮战报名门客"
        verbose_name_plural = "竞技场车轮战报名门客"
        constraints = [
            models.UniqueConstraint(fields=["entry", "guest"], name="unique_arena_wheel_entry_guest"),
            models.UniqueConstraint(fields=["entry", "slot_index"], name="unique_arena_wheel_entry_slot"),
        ]
        indexes = [models.Index(fields=["entry", "slot_index"], name="arena_wheel_guest_slot_idx")]

    def __str__(self) -> str:
        return f"{self.entry_id}:{self.guest_id}"


class ArenaWheelBout(models.Model):
    """车轮战中的一场两队门客对决。"""

    event = models.ForeignKey("gameplay.ArenaWheelEvent", on_delete=models.CASCADE, related_name="bouts")
    bout_number = models.PositiveSmallIntegerField(verbose_name="对决场次")
    team_a_entry = models.ForeignKey(
        "gameplay.ArenaWheelEntry",
        on_delete=models.CASCADE,
        related_name="team_a_bouts",
        verbose_name="甲队报名",
    )
    team_b_entry = models.ForeignKey(
        "gameplay.ArenaWheelEntry",
        on_delete=models.CASCADE,
        related_name="team_b_bouts",
        verbose_name="乙队报名",
    )
    winner_entry = models.ForeignKey(
        "gameplay.ArenaWheelEntry",
        on_delete=models.SET_NULL,
        related_name="won_wheel_bouts",
        null=True,
        blank=True,
    )
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        related_name="arena_wheel_bouts",
        null=True,
        blank=True,
    )
    base_seed = models.PositiveIntegerField(default=0)
    rng_version = models.PositiveSmallIntegerField(default=0)
    battle_engine_version = models.CharField(max_length=16, default="legacy")
    team_a_is_attacker = models.BooleanField(default=True, verbose_name="甲队先攻")
    team_a_debuff_stacks = models.PositiveSmallIntegerField(default=0, verbose_name="甲队疲敝层数")
    team_b_debuff_stacks = models.PositiveSmallIntegerField(default=0, verbose_name="乙队疲敝层数")
    winner_note = models.CharField(max_length=255, blank=True, default="", verbose_name="对决说明")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "竞技场车轮战对决"
        verbose_name_plural = "竞技场车轮战对决"
        ordering = ("bout_number", "id")
        constraints = [
            models.UniqueConstraint(fields=["event", "bout_number"], name="unique_arena_wheel_bout_number"),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} 第{self.bout_number}场"
