from django.db import migrations, models


def mark_existing_returned_loot_as_settled(apps, schema_editor):
    guild_raid_run = apps.get_model("guilds", "GuildRaidRun")
    guild_raid_run.objects.filter(status__in=("returning", "completed")).update(loot_settled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0024_expand_mysticism_technology"),
    ]

    operations = [
        migrations.AddField(
            model_name="guildraidrun",
            name="loot_item_contribution_costs",
            field=models.JSONField(blank=True, default=dict, verbose_name="掠夺物品兑换成本"),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="loot_settled",
            field=models.BooleanField(default=False, verbose_name="战利品已到账"),
        ),
        migrations.RunPython(mark_existing_returned_loot_as_settled, migrations.RunPython.noop),
    ]
