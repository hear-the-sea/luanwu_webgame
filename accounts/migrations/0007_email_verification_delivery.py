import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0006_email_verification_last_provider_db_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailVerificationDelivery",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("resend", "Resend"), ("brevo", "Brevo")],
                        max_length=32,
                        verbose_name="当前邮件供应商",
                    ),
                ),
                ("verification_url", models.TextField(verbose_name="验证链接")),
                ("quota_month", models.DateField(db_index=True, verbose_name="额度月份")),
                ("quota_day", models.DateField(db_index=True, verbose_name="额度日期")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待发送"),
                            ("processing", "发送中"),
                            ("retry", "等待重试"),
                            ("sent", "已发送"),
                            ("unknown", "结果未知"),
                            ("failed", "发送失败"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0, verbose_name="发送尝试次数")),
                ("fallback_used", models.BooleanField(default=False, verbose_name="是否已使用备用供应商")),
                (
                    "next_attempt_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="下次尝试时间"),
                ),
                (
                    "lease_until",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="处理租约到期时间"),
                ),
                (
                    "last_attempt_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="最近尝试时间"),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="发送时间")),
                ("last_error_code", models.CharField(blank=True, max_length=64, verbose_name="最近错误类型")),
                ("last_error", models.TextField(blank=True, verbose_name="最近错误")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_verification_deliveries",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "邮箱验证邮件投递",
                "verbose_name_plural": "邮箱验证邮件投递",
                "ordering": ("created_at",),
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="acct_email_del_due_idx",
                    ),
                    models.Index(
                        fields=["status", "lease_until"],
                        name="acct_email_del_lease_idx",
                    ),
                    models.Index(
                        fields=["user", "created_at"],
                        name="acct_email_del_user_idx",
                    ),
                ],
            },
        ),
    ]
