from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0073_world_unique_guest"),
    ]

    operations = [
        migrations.AddField(
            model_name="guest",
            name="avatar_override_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="受控的门客头像覆盖 key，空值表示使用门客模板头像",
                max_length=32,
                verbose_name="头像覆盖",
            ),
        ),
        migrations.AddField(
            model_name="guest",
            name="super_disguise_applied",
            field=models.BooleanField(default=False, verbose_name="超级易容效果已生效"),
        ),
    ]
