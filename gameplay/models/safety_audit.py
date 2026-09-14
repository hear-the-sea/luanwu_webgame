from django.db import models


class BotSafetyDecisionAudit(models.Model):
    """Append-only decision evidence; a routing CAS and its audit commit together."""

    window_id = models.CharField(max_length=128)
    policy_version = models.CharField(max_length=64)
    policy_mode = models.CharField(max_length=16)
    operation = models.CharField(max_length=16, default="monitor")
    reasons = models.JSONField(default=list)
    effective_pause = models.BooleanField(default=False)
    # Counterfactual global pause under the scoped policy (including shadow
    # evaluation). This is not a flag for the presence of scoped warnings.
    scoped_pause = models.BooleanField(default=False)
    previous_mode = models.CharField(max_length=32)
    resulting_mode = models.CharField(max_length=32)
    previous_reason = models.TextField(blank=True)
    resulting_reason = models.TextField(blank=True)
    previous_revision = models.PositiveBigIntegerField()
    resulting_revision = models.PositiveBigIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=["window_id", "policy_version"], name="bot_safety_audit_window")]
        verbose_name = "虚拟玩家安全决策审计"
        verbose_name_plural = verbose_name
