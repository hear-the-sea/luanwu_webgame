from django.db import migrations, models

DEFAULT_PRESTIGE_BANDS = (
    ("newbie", 0, 500),
    ("junior", 500, 2000),
    ("middle", 2000, 8000),
    ("senior", 8000, 30000),
    ("veteran", 30000, None),
)


def prestige_band_for_value(prestige):
    value = max(0, int(prestige or 0))
    for band_name, low, high in DEFAULT_PRESTIGE_BANDS:
        if value >= low and (high is None or value < high):
            return band_name
    return ""


def backfill_bot_profile_bands(apps, schema_editor):
    BotProfile = apps.get_model("gameplay", "BotProfile")
    BotProfile.objects.filter(target_prestige_band="").update(target_prestige_band=models.F("prestige_band"))
    for profile in BotProfile.objects.filter(current_prestige_band="").select_related("manor").iterator():
        profile.current_prestige_band = prestige_band_for_value(profile.manor.prestige) or profile.prestige_band
        profile.save(update_fields=["current_prestige_band"])


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0118_botbackfilldemand"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="current_prestige_band",
            field=models.CharField("当前声望段", db_index=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="botprofile",
            name="target_prestige_band",
            field=models.CharField("目标声望段", db_index=True, default="", max_length=32),
        ),
        migrations.RunPython(backfill_bot_profile_bands, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="botprofile",
            index=models.Index(fields=["target_prestige_band", "state"], name="bot_target_band_state_idx"),
        ),
        migrations.AddIndex(
            model_name="botprofile",
            index=models.Index(fields=["current_prestige_band", "state"], name="bot_current_band_state_idx"),
        ),
    ]
