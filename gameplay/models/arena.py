from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class ArenaTournament(models.Model):
    """竞技场赛事（满员自动开赛）。"""

    class Status(models.TextChoices):
        RECRUITING = "recruiting", "报名中"
        RUNNING = "running", "进行中"
        COMPLETED = "completed", "已结束"
        CANCELLED = "cancelled", "已取消"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECRUITING, db_index=True)
    player_limit = models.PositiveSmallIntegerField(default=10)
    round_interval_seconds = models.PositiveIntegerField(default=600)
    current_round = models.PositiveIntegerField(default=0)
    next_round_at = models.DateTimeField(null=True, blank=True, db_index=True)
    virtual_fill_at = models.DateTimeField(null=True, blank=True, db_index=True)
    virtual_fill_completed = models.BooleanField(default=False)
    base_seed = models.PositiveIntegerField(default=0)
    rng_version = models.PositiveSmallIntegerField(default=0)
    battle_engine_version = models.CharField(max_length=16, default="legacy")
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    winner_entry = models.ForeignKey(
        "gameplay.ArenaEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_tournaments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "竞技场赛事"
        verbose_name_plural = "竞技场赛事"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="recruiting"),
                name="unique_recruiting_tournament",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_round_at"], name="arena_tour_status_next_idx"),
            models.Index(fields=["status", "virtual_fill_at"], name="arena_tour_status_fill_idx"),
        ]

    def __str__(self) -> str:
        return f"竞技场#{self.pk} {self.get_status_display()}"


class ArenaEntry(models.Model):
    """玩家赛事报名记录。"""

    class Status(models.TextChoices):
        REGISTERED = "registered", "参赛中"
        ELIMINATED = "eliminated", "已淘汰"
        WINNER = "winner", "冠军"

    class Source(models.TextChoices):
        PLAYER = "player", "玩家"
        VIRTUAL = "virtual", "虚拟"

    tournament = models.ForeignKey("gameplay.ArenaTournament", on_delete=models.CASCADE, related_name="entries")
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="arena_entries")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REGISTERED, db_index=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PLAYER, db_index=True)
    eliminated_round = models.PositiveIntegerField(null=True, blank=True)
    final_rank = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    coin_reward = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "竞技场参赛记录"
        verbose_name_plural = "竞技场参赛记录"
        ordering = ("joined_at",)
        constraints = [
            models.UniqueConstraint(fields=["tournament", "manor"], name="unique_arena_tournament_manor"),
        ]
        indexes = [
            models.Index(fields=["manor", "joined_at"], name="arena_entry_manor_joined_idx"),
            models.Index(fields=["tournament", "status"], name="arena_entry_tour_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor} - {self.tournament_id}"


class ArenaEntryGuest(models.Model):
    """参赛名单中的门客快照关联。"""

    entry = models.ForeignKey("gameplay.ArenaEntry", on_delete=models.CASCADE, related_name="entry_guests")
    guest = models.ForeignKey(
        "guests.Guest",
        on_delete=models.CASCADE,
        related_name="arena_entry_links",
        null=True,
        blank=True,
    )
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "竞技场门客报名"
        verbose_name_plural = "竞技场门客报名"
        constraints = [
            models.UniqueConstraint(fields=["entry", "guest"], name="unique_arena_entry_guest"),
        ]
        indexes = [
            models.Index(fields=["entry", "created_at"], name="arena_entry_guest_entry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}:{self.guest_id}"


class ArenaMatch(models.Model):
    """竞技场每轮对战记录。"""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "待结算"
        COMPLETED = "completed", "已完成"
        FORFEIT = "forfeit", "弃权"
        BYE = "bye", "轮空"

    tournament = models.ForeignKey("gameplay.ArenaTournament", on_delete=models.CASCADE, related_name="matches")
    round_number = models.PositiveIntegerField()
    match_index = models.PositiveIntegerField(default=0)
    attacker_entry = models.ForeignKey(
        "gameplay.ArenaEntry",
        on_delete=models.CASCADE,
        related_name="arena_matches_as_attacker",
    )
    defender_entry = models.ForeignKey(
        "gameplay.ArenaEntry",
        on_delete=models.CASCADE,
        related_name="arena_matches_as_defender",
        null=True,
        blank=True,
    )
    winner_entry = models.ForeignKey(
        "gameplay.ArenaEntry",
        on_delete=models.SET_NULL,
        related_name="arena_matches_won",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    base_seed = models.PositiveIntegerField(default=0)
    rng_version = models.PositiveSmallIntegerField(default=0)
    battle_engine_version = models.CharField(max_length=16, default="legacy")
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arena_matches",
    )
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "竞技场对战"
        verbose_name_plural = "竞技场对战"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["tournament", "round_number"], name="arena_match_tour_round_idx"),
            models.Index(fields=["tournament", "match_index"], name="arena_match_tour_index_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "round_number", "match_index"],
                name="unique_arena_match_slot",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.attacker_entry_id and self.tournament_id:
            attacker_tournament_id = getattr(self.attacker_entry, "tournament_id", None)
            if attacker_tournament_id != self.tournament_id:
                errors["attacker_entry"] = "攻击方报名必须属于当前赛事"
        if self.defender_entry_id and self.tournament_id:
            defender_tournament_id = getattr(self.defender_entry, "tournament_id", None)
            if defender_tournament_id != self.tournament_id:
                errors["defender_entry"] = "防守方报名必须属于当前赛事"
        participant_ids = {self.attacker_entry_id, self.defender_entry_id}
        if self.winner_entry_id and self.winner_entry_id not in participant_ids:
            errors["winner_entry"] = "胜者必须是本场攻方或守方"

        if self.status == self.Status.SCHEDULED:
            if self.winner_entry_id is not None:
                errors["winner_entry"] = "待结算对局不能预设胜者"
            if self.battle_report_id is not None:
                errors["battle_report"] = "待结算对局不能预设战报"
            if self.resolved_at is not None:
                errors["resolved_at"] = "待结算对局不能预设结算时间"
        else:
            if self.winner_entry_id is None:
                errors["winner_entry"] = "已结算对局必须有胜者"
            if self.resolved_at is None:
                errors["resolved_at"] = "已结算对局必须有结算时间"
        if self.status == self.Status.BYE and self.defender_entry_id is not None:
            errors["defender_entry"] = "轮空对局不能有防守方"
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"T{self.tournament_id}-R{self.round_number}-M{self.match_index}"


class ArenaExchangeRecord(models.Model):
    """竞技场兑换记录。"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="arena_exchange_records")
    reward_key = models.SlugField(max_length=64)
    reward_name = models.CharField(max_length=128)
    cost_coins = models.PositiveIntegerField(default=0)
    quantity = models.PositiveIntegerField(default=1)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "竞技场兑换记录"
        verbose_name_plural = "竞技场兑换记录"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["manor", "reward_key", "created_at"], name="arena_ex_manor_reward_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor_id}:{self.reward_key}x{self.quantity}"
