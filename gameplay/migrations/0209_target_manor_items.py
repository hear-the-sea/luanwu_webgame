import django.db.models.deletion
from django.db import migrations, models

ITEM_TEMPLATES = {
    "guild_slave_card": {
        "name": "奴隶卡",
        "description": "对可攻击的目标庄园使用，使一名空闲门客被占用1小时。同一庄园1小时内最多受到一张奴隶卡影响。",
        "effect_payload": {"action": "occupy_target_guest"},
    },
    "guild_theft_card": {
        "name": "抢夺卡",
        "description": "对可攻击的目标庄园使用，随机夺取仓库中一件可交易、未绑定的蓝色及以上物品。同一庄园1小时内最多受到一张抢夺卡影响。",
        "effect_payload": {"action": "steal_target_item"},
    },
    "guild_injury_card": {
        "name": "重伤卡",
        "description": "对可攻击的目标庄园使用，使一名门客重伤并将生命降至1。同一庄园1小时内最多受到一张重伤卡影响。",
        "effect_payload": {"action": "injure_target_guest"},
    },
}


def create_target_item_templates(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    for key, item in ITEM_TEMPLATES.items():
        defaults = {
            **item,
            "effect_type": "tool",
            "rarity": "blue",
            "tradeable": True,
            "default_bound": False,
            "price": 0,
            "storage_space": 25,
            "is_usable": True,
        }
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0208_align_cultivation_difficulty"),
        ("guests", "0091_custom_guest_daily_feeding"),
    ]

    operations = [
        migrations.CreateModel(
            name="TargetItemEffectCooldown",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("effect_key", models.CharField(max_length=32)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="target_item_cooldowns",
                        to="gameplay.manor",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="TemporaryGuestOccupation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "guest",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temporary_occupation",
                        to="guests.guest",
                    ),
                ),
                (
                    "manor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temporary_guest_occupations",
                        to="gameplay.manor",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="targetitemeffectcooldown",
            constraint=models.UniqueConstraint(fields=("manor", "effect_key"), name="uniq_manor_target_item_cooldown"),
        ),
        migrations.AddIndex(
            model_name="targetitemeffectcooldown",
            index=models.Index(fields=["manor", "expires_at"], name="target_item_cooldown_idx"),
        ),
        migrations.AddIndex(
            model_name="temporaryguestoccupation",
            index=models.Index(fields=["manor", "expires_at"], name="guest_occupation_due_idx"),
        ),
        migrations.RunPython(create_target_item_templates, migrations.RunPython.noop),
    ]
