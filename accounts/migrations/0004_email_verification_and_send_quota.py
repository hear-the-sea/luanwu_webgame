from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_useractivesession"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified",
            field=models.BooleanField(
                db_default=True,
                db_index=True,
                default=True,
                verbose_name="邮箱已验证",
            ),
        ),
        migrations.CreateModel(
            name="EmailSendQuota",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("month", models.DateField(db_index=True, unique=True, verbose_name="月份")),
                ("sent_count", models.PositiveIntegerField(default=0, verbose_name="已预占发信数")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "月度邮件额度",
                "verbose_name_plural": "月度邮件额度",
                "ordering": ("-month",),
            },
        ),
    ]
