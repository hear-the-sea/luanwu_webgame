"""俘虏携带丸子使用记录，收编后仍只能使用一次。"""

from django.db import migrations, models


def mark_converted_prisoners(apps, schema_editor):
    JailPrisoner = apps.get_model("gameplay", "JailPrisoner")
    JailPrisoner.objects.using(schema_editor.connection.alias).filter(
        guest_template__key__startswith="guest_rarity_conversion_"
    ).update(rarity_pill_used=True)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0214_guild_orange_mounts"),
        ("guests", "0092_guest_rarity_pill_used"),
    ]

    operations = [
        migrations.AddField(
            model_name="jailprisoner",
            name="rarity_pill_used",
            field=models.BooleanField(default=False, verbose_name="已使用升阶丸子"),
        ),
        migrations.RunPython(mark_converted_prisoners, migrations.RunPython.noop),
    ]
