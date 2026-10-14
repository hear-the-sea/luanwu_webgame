from django.db import models


class ArenaRing(models.Model):
    key = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=32)
    current_tenure = models.ForeignKey(
        "gameplay.ArenaRingTenure",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_on_rings",
    )
    version = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class ArenaRingTenure(models.Model):
    ring = models.ForeignKey(ArenaRing, on_delete=models.CASCADE, related_name="tenures")
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="ring_tenures")
    lineup = models.JSONField(default=dict)
    started_at = models.DateTimeField()
    ends_at = models.DateTimeField(db_index=True)
    settled_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    end_reason = models.CharField(max_length=16, blank=True)
    defenses = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    lineup_updated_at = models.DateTimeField()

    def __str__(self):
        return f"占领记录 #{self.pk}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["manor"],
                condition=models.Q(ended_at__isnull=True),
                name="ring_one_active_per_manor",
            )
        ]


class ArenaRingMatch(models.Model):
    tenure = models.ForeignKey(ArenaRingTenure, on_delete=models.CASCADE, related_name="matches")
    attacker = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="ring_attacks")
    defender = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="ring_defenses")
    winner = models.ForeignKey(
        "gameplay.Manor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ring_wins",
    )
    battle_report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arena_ring_matches",
    )
    pair_low = models.PositiveBigIntegerField()
    pair_high = models.PositiveBigIntegerField()
    reward_date = models.DateField()
    created_at = models.DateTimeField()

    def __str__(self):
        return f"擂台挑战 #{self.pk}"

    class Meta:
        indexes = [
            models.Index(
                fields=["reward_date", "pair_low", "pair_high"],
                name="ring_pair_day_idx",
            )
        ]


class ArenaRingDaily(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE)
    date = models.DateField()
    seconds = models.PositiveIntegerField(default=0)
    time_coins = models.PositiveIntegerField(default=0)
    defense_rewards = models.PositiveIntegerField(default=0)
    participation_rewards = models.PositiveIntegerField(default=0)
    challenges = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"擂台每日统计 {self.date}"

    class Meta:
        constraints = [models.UniqueConstraint(fields=["manor", "date"], name="ring_daily_manor_date")]


class ArenaRingAction(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE)
    token = models.UUIDField()
    kind = models.CharField(max_length=16)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["manor", "token"], name="ring_action_manor_token")]


class ArenaRingInspection(models.Model):
    buyer = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="ring_inspections")
    tenure = models.ForeignKey(ArenaRingTenure, on_delete=models.CASCADE, related_name="inspections")
    version = models.PositiveIntegerField()
    lineup = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    def __str__(self):
        return f"擂台侦察 #{self.pk}"
