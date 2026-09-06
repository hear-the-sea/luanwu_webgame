from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_email_verification_and_send_quota"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verification_last_provider",
            field=models.CharField(
                blank=True,
                choices=[("resend", "Resend"), ("brevo", "Brevo")],
                default="",
                max_length=32,
                verbose_name="最近验证邮件供应商",
            ),
        ),
        migrations.CreateModel(
            name="EmailProviderDailyQuota",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("resend", "Resend"), ("brevo", "Brevo")],
                        max_length=32,
                        verbose_name="邮件供应商",
                    ),
                ),
                ("day", models.DateField(db_index=True, verbose_name="日期")),
                ("sent_count", models.PositiveIntegerField(default=0, verbose_name="已预占发信数")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "供应商每日邮件额度",
                "verbose_name_plural": "供应商每日邮件额度",
                "ordering": ("-day", "provider"),
            },
        ),
        migrations.AddConstraint(
            model_name="emailproviderdailyquota",
            constraint=models.UniqueConstraint(
                fields=("provider", "day"),
                name="accounts_email_provider_daily_quota_unique",
            ),
        ),
    ]
