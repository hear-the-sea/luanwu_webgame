from __future__ import annotations

from django.db import models


class TargetItemEffectCooldown(models.Model):
    """Per-target cooldowns shared by all levels of a hostile manor item."""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="target_item_cooldowns")
    effect_key = models.CharField(max_length=32)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["manor", "effect_key"], name="uniq_manor_target_item_cooldown"),
        ]
        indexes = [models.Index(fields=["manor", "expires_at"], name="target_item_cooldown_idx")]


class TemporaryGuestOccupation(models.Model):
    """A guest temporarily held unavailable by a hostile item."""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="temporary_guest_occupations")
    guest = models.OneToOneField("guests.Guest", on_delete=models.CASCADE, related_name="temporary_occupation")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["manor", "expires_at"], name="guest_occupation_due_idx")]


class ManorResourceProductionBuff(models.Model):
    """A timed production bonus applied to one resource for a single Manor."""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="resource_production_buffs")
    resource_type = models.CharField(max_length=16)
    bonus_percent = models.PositiveSmallIntegerField(default=25)
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["manor", "resource_type"],
                name="uniq_manor_resource_production_buff",
            ),
        ]
        indexes = [models.Index(fields=["manor", "expires_at"], name="manor_resource_buff_exp_idx")]
