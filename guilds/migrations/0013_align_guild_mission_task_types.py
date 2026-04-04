from django.db import migrations, models


def migrate_guild_mission_task_types(apps, schema_editor):
    GuildMissionTemplate = apps.get_model("guilds", "GuildMissionTemplate")

    GuildMissionTemplate.objects.filter(task_type__in=["dispatch", "patrol"], allow_troops=False).update(
        task_type="guest"
    )
    GuildMissionTemplate.objects.filter(task_type__in=["dispatch", "patrol"], allow_troops=True).update(
        task_type="troop"
    )
    GuildMissionTemplate.objects.filter(task_type="escort").update(task_type="troop")
    GuildMissionTemplate.objects.filter(task_type="suppress").update(task_type="defense")


def rollback_guild_mission_task_types(apps, schema_editor):
    GuildMissionTemplate = apps.get_model("guilds", "GuildMissionTemplate")

    rollback_mapping = {
        "guest": "patrol",
        "troop": "escort",
        "defense": "suppress",
    }

    for new_value, old_value in rollback_mapping.items():
        GuildMissionTemplate.objects.filter(task_type=new_value).update(task_type=old_value)


class Migration(migrations.Migration):

    dependencies = [
        ("guilds", "0012_alter_guilddonationlog_resource_type"),
    ]

    operations = [
        migrations.RunPython(migrate_guild_mission_task_types, rollback_guild_mission_task_types),
        migrations.AlterField(
            model_name="guildmissiontemplate",
            name="task_type",
            field=models.CharField(
                choices=[("guest", "门客"), ("troop", "护院"), ("defense", "防守")],
                default="guest",
                max_length=20,
                verbose_name="任务类型",
            ),
        ),
    ]
