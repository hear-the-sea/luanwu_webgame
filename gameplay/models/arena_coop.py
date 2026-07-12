from __future__ import annotations

from django.db import models


class ArenaCoopEvent(models.Model):
    """竞技场共斗活动实例。"""

    class Status(models.TextChoices):
        RECRUITING = "recruiting", "报名中"
        PREPARING = "preparing", "准备中"
        RUNNING = "running", "结算中"
        COMPLETED = "completed", "已结束"
        CANCELLED = "cancelled", "已取消"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECRUITING, db_index=True)
    player_limit = models.PositiveSmallIntegerField(default=5)
    guest_limit_per_entry = models.PositiveSmallIntegerField(default=3)
    prepare_duration_seconds = models.PositiveIntegerField(default=120)
    prepare_ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    virtual_fill_at = models.DateTimeField(null=True, blank=True, db_index=True)
    virtual_fill_completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arena_coop_events",
    )
    boss_name = models.CharField(max_length=64, default="张无忌")
    boss_template_key = models.SlugField(max_length=64, default="arena_gl_top_zhang_wuji_boss")
    boss_initial_hp = models.PositiveIntegerField(default=0)
    boss_remaining_hp = models.PositiveIntegerField(default=0)
    boss_defeated = models.BooleanField(default=False)
    enemy_snapshot = models.JSONField(default=dict, blank=True)
    reward_snapshot = models.JSONField(default=dict, blank=True)
    daily_rule_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "竞技场共斗活动"
        verbose_name_plural = "竞技场共斗活动"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="recruiting"),
                name="unique_recruiting_arena_coop_event",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "prepare_ends_at"], name="arena_coop_status_prepare_idx"),
            models.Index(fields=["status", "virtual_fill_at"], name="arena_coop_status_fill_idx"),
        ]

    def __str__(self) -> str:
        return f"围攻光明顶#{self.pk} {self.get_status_display()}"


class ArenaCoopEntry(models.Model):
    """玩家在共斗活动中的报名记录。"""

    class Status(models.TextChoices):
        REGISTERED = "registered", "报名中"
        CANCELLED = "cancelled", "已撤销"
        COMPLETED = "completed", "已完成"

    class Source(models.TextChoices):
        PLAYER = "player", "玩家"
        VIRTUAL = "virtual", "虚拟"

    event = models.ForeignKey("gameplay.ArenaCoopEvent", on_delete=models.CASCADE, related_name="entries")
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="arena_coop_entries")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REGISTERED, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PLAYER, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    seed_order = models.PositiveSmallIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "竞技场共斗报名"
        verbose_name_plural = "竞技场共斗报名"
        ordering = ("joined_at",)
        constraints = [
            models.UniqueConstraint(fields=["event", "manor"], name="unique_arena_coop_event_manor"),
        ]
        indexes = [
            models.Index(fields=["event", "status"], name="arena_coop_evt_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor} - coop-{self.event_id}"


class ArenaCoopEntryGuest(models.Model):
    """共斗报名中的门客快照。"""

    entry = models.ForeignKey("gameplay.ArenaCoopEntry", on_delete=models.CASCADE, related_name="entry_guests")
    guest = models.ForeignKey(
        "guests.Guest",
        on_delete=models.CASCADE,
        related_name="arena_coop_entry_links",
        null=True,
        blank=True,
    )
    slot_index = models.PositiveSmallIntegerField(default=0)
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "竞技场共斗报名门客"
        verbose_name_plural = "竞技场共斗报名门客"
        constraints = [
            models.UniqueConstraint(fields=["entry", "guest"], name="unique_arena_coop_entry_guest"),
            models.UniqueConstraint(fields=["entry", "slot_index"], name="unique_arena_coop_entry_slot"),
        ]
        indexes = [
            models.Index(fields=["entry", "created_at"], name="arena_coop_ent_guest_entry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}:{self.guest_id}"


class ArenaCoopContribution(models.Model):
    """共斗活动个人结算与奖励明细。"""

    event = models.ForeignKey("gameplay.ArenaCoopEvent", on_delete=models.CASCADE, related_name="contributions")
    entry = models.OneToOneField(
        "gameplay.ArenaCoopEntry",
        on_delete=models.CASCADE,
        related_name="contribution",
    )
    total_damage = models.PositiveIntegerField(default=0)
    boss_damage = models.PositiveIntegerField(default=0)
    guard_damage = models.PositiveIntegerField(default=0)
    effective_damage = models.PositiveIntegerField(default=0)
    damage_share_bps = models.PositiveIntegerField(default=0)
    damage_rank = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    met_minimum_contribution = models.BooleanField(default=False)
    participation_coins = models.PositiveIntegerField(default=0)
    damage_coins = models.PositiveIntegerField(default=0)
    rank_coins = models.PositiveIntegerField(default=0)
    clear_coins = models.PositiveIntegerField(default=0)
    total_coins = models.PositiveIntegerField(default=0)
    rare_drop_item_key = models.SlugField(max_length=64, blank=True, default="")
    rare_drop_quantity = models.PositiveIntegerField(default=0)
    rare_drop_granted = models.BooleanField(default=False)
    reward_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "竞技场共斗贡献"
        verbose_name_plural = "竞技场共斗贡献"
        ordering = ("damage_rank", "id")
        indexes = [
            models.Index(fields=["event", "damage_rank"], name="arena_coop_contrib_rank_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_id}:{self.entry_id}:{self.total_damage}"
