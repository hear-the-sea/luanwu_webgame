"""赛季持久状态；业务写入通过 season services 完成。"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

FACTIONS = [("fiction", "小说阵营"), ("history", "历史阵营")]


class Season(models.Model):
    name = models.CharField(max_length=64)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    rules = models.JSONField(default=dict)
    population = models.JSONField(default=dict)
    population_updated_at = models.DateTimeField(null=True, blank=True)
    closed = models.BooleanField(default=False, db_index=True)
    settled_weeks = models.PositiveSmallIntegerField(default=0)
    fiction_score = models.PositiveIntegerField(default=0)
    history_score = models.PositiveIntegerField(default=0)
    winner = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at", "-id"]

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError("赛季结束时间必须晚于开始时间")


class SeasonMember(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="members")
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="season_memberships")
    faction = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    preference = models.CharField(max_length=16, blank=True)
    lineup = models.JSONField(default=dict)
    strength = models.PositiveBigIntegerField(default=0)
    prestige_snapshot = models.PositiveBigIntegerField(default=0)
    pending_resources = models.JSONField(default=dict)
    pending_construction = models.PositiveBigIntegerField(default=0)
    combat_merit = models.PositiveIntegerField(default=0)
    logistics_merit = models.PositiveIntegerField(default=0)
    troop_date = models.DateField(null=True, blank=True)
    troops_drawn = models.PositiveIntegerField(default=0)
    final_rank = models.PositiveIntegerField(null=True, blank=True)
    final_tier = models.CharField(max_length=24, blank=True)
    final_reward = models.JSONField(default=dict)
    merit = models.PositiveIntegerField(default=0)
    coins = models.PositiveIntegerField(default=0)
    supplies = models.PositiveIntegerField(default=0)
    donated = models.PositiveIntegerField(default=0)
    action_date = models.DateField(null=True, blank=True)
    actions_used = models.PositiveSmallIntegerField(default=0)
    scroll_available = models.BooleanField(default=False)
    scroll_used = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "manor"], name="season_member_unique")]
        indexes = [models.Index(fields=["season", "faction"], name="season_member_faction_idx")]


class SeasonAction(models.Model):
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="actions")
    token = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    kind = models.CharField(max_length=24)
    node_key = models.CharField(max_length=40, blank=True)
    date = models.DateField(db_index=True)
    week = models.PositiveSmallIntegerField(default=0)
    war_points = models.PositiveSmallIntegerField(default=0)
    opponent = models.ForeignKey(
        SeasonMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="defenses"
    )
    report = models.ForeignKey("battle.BattleReport", on_delete=models.SET_NULL, null=True, blank=True)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "token"], name="season_action_token_unique")]
        indexes = [models.Index(fields=["member", "week", "kind"], name="season_action_week_idx")]
        ordering = ["-created_at", "-id"]


class SeasonClaim(models.Model):
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="claims")
    key = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "key"], name="season_claim_unique")]


class SeasonWeek(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="weeks")
    number = models.PositiveSmallIntegerField()
    node_key = models.CharField(max_length=40)
    fiction_points = models.PositiveIntegerField(default=0)
    history_points = models.PositiveIntegerField(default=0)
    matched_members = models.PositiveIntegerField(default=0)
    winner = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    settled_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "number"], name="season_week_unique")]
        ordering = ["number"]


class SeasonFaction(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="faction_states")
    faction = models.CharField(max_length=16, choices=FACTIONS)
    resources = models.JSONField(default=dict)
    troops = models.JSONField(default=dict)
    levels = models.JSONField(default=dict)
    technology = models.JSONField(default=dict)
    produced_at = models.DateTimeField()
    construction_points = models.PositiveBigIntegerField(default=0)
    economy_state = models.JSONField(default=dict)
    score = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "faction"], name="season_faction_unique")]


class SeasonSite(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="sites")
    key = models.CharField(max_length=40)
    owner = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    guards = models.PositiveIntegerField(default=0)
    durability = models.PositiveIntegerField(default=0)
    control = models.PositiveIntegerField(default=0)
    attacker = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    protected_until = models.DateTimeField(null=True, blank=True)
    last_score_at = models.DateTimeField()
    front_line = models.JSONField(default=dict)
    front_allocations = models.JSONField(default=dict)
    reinforcement_at = models.DateTimeField(null=True, blank=True)
    supplies = models.JSONField(default=dict)
    supply_updated_at = models.DateTimeField(null=True, blank=True)
    capture_cycle = models.PositiveIntegerField(default=0)
    npc_profile = models.JSONField(default=dict)
    defenses = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "key"], name="season_site_unique")]


class SeasonIntel(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="intel_records")
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="intel_records")
    faction = models.CharField(max_length=16, choices=FACTIONS, blank=True)
    node_key = models.CharField(max_length=40)
    kind = models.CharField(max_length=24)
    payload = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["member", "node_key", "expires_at"], name="season_intel_member_idx"),
            models.Index(fields=["season", "faction", "expires_at"], name="season_intel_faction_idx"),
        ]


class SeasonRally(models.Model):
    KIND_CHOICES = [("attack", "集结进攻"), ("siege", "集结破城")]
    STATUS_CHOICES = [("gathering", "集结中"), ("marching", "行军中"), ("resolved", "已结算"), ("cancelled", "已取消")]
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="rallies")
    leader = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="led_rallies")
    node_key = models.CharField(max_length=40)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="gathering")
    gathers_until = models.DateTimeField()
    launched_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "gathers_until"], name="season_rally_gather_idx"),
            models.Index(fields=["status", "due_at"], name="season_rally_due_idx"),
        ]


class SeasonExpedition(models.Model):
    STATUS_CHOICES = [
        (key, label)
        for key, label in (
            ("gathering", "集结中"),
            ("outbound", "行军中"),
            ("stationed", "驻防中"),
            ("transporting", "运输中"),
            ("returning", "返程中"),
            ("completed", "已归队"),
            ("failed", "异常待处理"),
        )
    ]
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="expeditions")
    rally = models.ForeignKey(SeasonRally, on_delete=models.SET_NULL, related_name="expeditions", null=True, blank=True)
    node_key = models.CharField(max_length=40)
    kind = models.CharField(max_length=24)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="outbound", db_index=True)
    guest_ids = models.JSONField(default=list)
    guests = models.ManyToManyField("guests.Guest", related_name="season_expeditions", blank=True)
    lineup = models.JSONField(default=dict)
    troops = models.JSONField(default=dict)
    initial_troops = models.JSONField(default=dict)
    technology = models.JSONField(default=dict)
    cargo = models.JSONField(default=dict)
    intercepted = models.BooleanField(default=False)
    battles = models.PositiveSmallIntegerField(default=0)
    seed = models.PositiveIntegerField(default=0)
    travel_seconds = models.PositiveIntegerField(default=0)
    launched_at = models.DateTimeField()
    due_at = models.DateTimeField(db_index=True)
    stationed_until = models.DateTimeField(null=True, blank=True)
    report = models.ForeignKey("battle.BattleReport", null=True, blank=True, on_delete=models.SET_NULL)
    result = models.JSONField(default=dict)
    error = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ["-launched_at", "-id"]
        indexes = [models.Index(fields=["status", "due_at"], name="season_exp_due_idx")]
