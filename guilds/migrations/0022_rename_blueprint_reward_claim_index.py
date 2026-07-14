from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("guilds", "0021_guild_blueprint_reward_claim")]

    operations = [
        migrations.RenameIndex(
            model_name="guildblueprintrewardclaim",
            old_name="guild_bp_claim_member_rarity_idx",
            new_name="guild_bp_member_rarity_idx",
        ),
    ]
