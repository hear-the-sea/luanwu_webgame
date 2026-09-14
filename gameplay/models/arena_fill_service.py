from django.db import models


class ArenaFillServiceEpisode(models.Model):
    """Durable service clock for an activity opening, independent of demand versions."""

    mode = models.CharField(max_length=16, choices=[("tournament", "普通"), ("coop", "共斗")])
    event_id = models.PositiveBigIntegerField()
    # Keep historical evidence when activity cleanup deletes the source rows.
    demand = models.ForeignKey(
        "gameplay.ArenaVirtualDemand",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fill_service_episodes",
    )
    virtual_fill_at = models.DateTimeField()
    opening_ordinal = models.PositiveIntegerField(default=1)
    checkpoint_at = models.DateTimeField()
    first_observed_at = models.DateTimeField()
    first_real_entry_at = models.DateTimeField(null=True, blank=True)
    first_demand_at = models.DateTimeField(null=True, blank=True)
    initial_missing_count = models.PositiveIntegerField(default=0)
    first_completed_at = models.DateTimeField(null=True, blank=True)
    completion_kind = models.CharField(max_length=32, blank=True, default="")
    closed_at = models.DateTimeField(null=True, blank=True)
    terminal_reason = models.CharField(max_length=32, blank=True, default="")
    slo_result = models.CharField(max_length=32, blank=True, default="")
    slo_observed_at = models.DateTimeField(null=True, blank=True)
    last_observed_at = models.DateTimeField()
    last_snapshot = models.JSONField(default=dict)
    # At most two immutable payloads: the SLO result and eventual completion.
    pending_outcomes = models.JSONField(default=dict, blank=True)
    publication_pending = models.BooleanField(default=False, db_index=True)
    sampled_gap_seconds = models.FloatField(default=0)
    unknown_observation_seconds = models.FloatField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "竞技场补位服务期次"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["mode", "event_id", "virtual_fill_at", "opening_ordinal"],
                name="arena_fill_episode_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["closed_at", "last_observed_at"],
                name="arena_fill_pending_idx",
            )
        ]


class ArenaFillServiceOutcome(models.Model):
    """Immutable published service evidence; intentionally separate from safety windows."""

    episode = models.ForeignKey(ArenaFillServiceEpisode, on_delete=models.PROTECT, related_name="outcomes")
    phase = models.CharField(max_length=16, choices=[("slo", "服务期限"), ("completion", "最终补齐")])
    result = models.CharField(max_length=32)
    logical_at = models.DateTimeField(db_index=True)
    observed_at = models.DateTimeField()
    published_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()

    class Meta:
        verbose_name = "竞技场补位服务结果"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["episode", "phase"],
                name="arena_fill_outcome_unique",
            )
        ]
