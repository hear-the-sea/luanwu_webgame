from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_email_provider_daily_quota"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="email_verification_last_provider",
            field=models.CharField(
                blank=True,
                choices=[("resend", "Resend"), ("brevo", "Brevo")],
                db_default="",
                default="",
                max_length=32,
                verbose_name="最近验证邮件供应商",
            ),
        ),
    ]
