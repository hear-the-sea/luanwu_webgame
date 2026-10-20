"""记录每名门客是否已使用绿转蓝或蓝转紫丸子。"""

from django.db import migrations, models


def mark_converted_guests(apps, schema_editor):
    Guest = apps.get_model("guests", "Guest")
    Guest.objects.using(schema_editor.connection.alias).filter(
        template__key__startswith="guest_rarity_conversion_"
    ).update(rarity_pill_used=True)


class Migration(migrations.Migration):
    dependencies = [("guests", "0091_custom_guest_daily_feeding")]

    operations = [
        migrations.AddField(
            model_name="guest",
            name="rarity_pill_used",
            field=models.BooleanField(default=False, verbose_name="已使用升阶丸子"),
        ),
        migrations.RunPython(mark_converted_guests, migrations.RunPython.noop),
    ]
