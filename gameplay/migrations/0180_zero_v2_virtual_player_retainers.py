import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def zero_v2_virtual_player_retainers(apps, schema_editor) -> None:
    database_alias = schema_editor.connection.alias
    BotProfile = apps.get_model("gameplay", "BotProfile")
    Manor = apps.get_model("gameplay", "Manor")
    profiles = BotProfile.objects.using(database_alias).filter(
        engine_version=2,
        policy_version=2,
        manor_id__isnull=False,
    )
    profile_count = profiles.count()
    if profile_count == 0:
        logger.info("V2 virtual-player retainer migration found no eligible profiles")
        return

    manor_ids = profiles.values_list("manor_id", flat=True)
    updated_count = (
        Manor.objects.using(database_alias).filter(pk__in=manor_ids).exclude(retainer_count=0).update(retainer_count=0)
    )
    logger.info(
        "V2 virtual-player retainer migration normalized %s Manor rows for %s profiles",
        updated_count,
        profile_count,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0179_bot_profile_recruitment_schedule_snapshot"),
    ]

    operations = [
        migrations.RunPython(zero_v2_virtual_player_retainers, migrations.RunPython.noop),
    ]
