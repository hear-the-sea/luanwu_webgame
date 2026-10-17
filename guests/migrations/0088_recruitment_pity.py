import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guests", "0087_guest_avatar_switch")]
    operations = [
        migrations.CreateModel(
            name="RecruitmentPity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purple_misses", models.PositiveIntegerField(default=0)),
                ("orange_misses", models.PositiveIntegerField(default=0)),
                (
                    "manor",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recruitment_pity",
                        to="gameplay.manor",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="recruitmentcandidate",
            name="pity_rarity",
            field=models.CharField(
                "保底类型",
                max_length=16,
                blank=True,
                default="",
                choices=[("purple", "紫色及以上"), ("orange", "橙色")],
            ),
        ),
    ]
