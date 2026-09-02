import django.db.models.deletion
from django.db import migrations, models


def seed_world_unique_guest_states(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    WorldUniqueGuest = apps.get_model("guests", "WorldUniqueGuest")

    for template in GuestTemplate.objects.filter(is_world_unique=True).only("id"):
        WorldUniqueGuest.objects.get_or_create(
            template_id=template.id,
            defaults={"status": "wild"},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0072_recruitmentcandidate_expiry_idx"),
        ("gameplay", "0188_alter_missiontemplate_mission_card_daily_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="guesttemplate",
            name="is_world_unique",
            field=models.BooleanField(default=False, verbose_name="全服唯一"),
        ),
        migrations.CreateModel(
            name="WorldUniqueGuest",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("wild", "在野"), ("serving", "仕官")],
                        db_index=True,
                        default="wild",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("version", models.PositiveBigIntegerField(default=0, verbose_name="状态版本")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner_guest",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="world_unique_state",
                        to="guests.guest",
                        verbose_name="当前门客实例",
                    ),
                ),
                (
                    "owner_manor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="world_unique_guests",
                        to="gameplay.manor",
                        verbose_name="当前庄园",
                    ),
                ),
                (
                    "template",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="world_unique_state",
                        to="guests.guesttemplate",
                        verbose_name="门客模板",
                    ),
                ),
            ],
            options={
                "verbose_name": "全服唯一门客状态",
                "verbose_name_plural": "全服唯一门客状态",
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(status="wild", owner_manor__isnull=True, owner_guest__isnull=True)
                            | models.Q(status="serving", owner_manor__isnull=False, owner_guest__isnull=False)
                        ),
                        name="world_unique_guest_owner_consistency",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_world_unique_guest_states, migrations.RunPython.noop),
    ]
