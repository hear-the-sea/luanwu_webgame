from django.db import models


class ArenaRuntimeBudget(models.Model):
    """Small shared ledgers; reservations commit with their owning mutations."""

    key = models.CharField(max_length=24, primary_key=True)
    entries = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(key__in=["materialization", "training", "growth"]),
                name="arena_runtime_budget_key_valid",
            ),
        ]
