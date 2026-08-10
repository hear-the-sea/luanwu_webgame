from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0068_performance_scan_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecruitmentExtraAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("date", models.DateField(verbose_name="额外次数生效日期")),
                ("extra_count", models.PositiveIntegerField(default=0, verbose_name="额外招募次数")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manor",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="recruitment_extra_attempts",
                        to="gameplay.manor",
                    ),
                ),
                (
                    "pool",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="extra_attempts",
                        to="guests.recruitmentpool",
                    ),
                ),
            ],
            options={
                "verbose_name": "招募额外次数",
                "verbose_name_plural": "招募额外次数",
                "unique_together": {("manor", "pool", "date")},
            },
        ),
        migrations.AddIndex(
            model_name="recruitmentextraattempt",
            index=models.Index(fields=["manor", "date"], name="recruit_extra_manor_date_idx"),
        ),
    ]
