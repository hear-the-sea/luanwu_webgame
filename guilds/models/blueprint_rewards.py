from django.db import models
from django.utils import timezone

from .member import GuildMember


class GuildBlueprintRewardClaim(models.Model):
    member = models.ForeignKey(GuildMember, on_delete=models.CASCADE, related_name="blueprint_claims")
    blueprint_key = models.CharField(max_length=100)
    rarity = models.CharField(max_length=16)
    claimed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "guild_blueprint_reward_claims"
        indexes = [
            models.Index(fields=["member", "claimed_at"], name="guild_bp_claim_member_time_idx"),
            models.Index(fields=["member", "rarity", "claimed_at"], name="guild_bp_member_rarity_idx"),
        ]
