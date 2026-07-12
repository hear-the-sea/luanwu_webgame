import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guilds", "0020_guildapplication_unique_pending")]

    operations = [
        migrations.CreateModel(
            name="GuildBlueprintRewardClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("blueprint_key", models.CharField(max_length=100)),
                ("rarity", models.CharField(max_length=16)),
                ("claimed_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="blueprint_claims",
                        to="guilds.guildmember",
                    ),
                ),
            ],
            options={"db_table": "guild_blueprint_reward_claims"},
        ),
        migrations.AddIndex(
            model_name="guildblueprintrewardclaim",
            index=models.Index(fields=["member", "claimed_at"], name="guild_bp_claim_member_time_idx"),
        ),
        migrations.AddIndex(
            model_name="guildblueprintrewardclaim",
            index=models.Index(fields=["member", "rarity", "claimed_at"], name="guild_bp_claim_member_rarity_idx"),
        ),
    ]
