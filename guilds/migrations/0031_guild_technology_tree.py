from __future__ import annotations

import math

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

PUBLIC_TECHS = {
    "guild_lineup_capacity": ("public", 20),
    "guild_dispatch_capacity": ("public", 20),
    "troop_tactics": ("public", 10),
    "march_speed": ("public", 5),
}

ROUTE_TECHS = {
    "hero": {
        "hero_fruit_blueprint": 3,
        "hero_skill_books": 10,
        "hero_rebirth_card": 3,
        "hero_green_blue_pill": 3,
        "hero_blue_purple_pill": 3,
        "hero_reset_card": 3,
        "hero_marrow_pill": 3,
    },
    "wolf": {
        "wolf_bloodthirsty_cavalry": 1,
        "wolf_berserk": 3,
        "wolf_force": 5,
        "wolf_defense": 5,
        "wolf_agility": 5,
        "wolf_hp": 5,
        "wolf_plunder_card": 3,
        "wolf_lightning_horse": 3,
        "wolf_golden_white_horse": 3,
        "wolf_soul_container": 3,
    },
    "sheep": {
        "sheep_farmland": 3,
        "sheep_silver_mine": 3,
        "sheep_grain_card": 3,
        "sheep_silver_card": 3,
        "sheep_metallurgy": 3,
        "sheep_breeding": 3,
        "sheep_horsemanship": 3,
        "sheep_wrath": 1,
    },
    "forging": {
        "forging_mechanism_blueprint": 5,
        "forging_slave_card": 3,
        "forging_theft_card": 3,
        "forging_injury_card": 3,
    },
}


def _ceil_scaled(level: int, old_max: int, new_max: int) -> int:
    if level <= 0 or old_max <= 0:
        return 0
    return min(new_max, math.ceil(level * new_max / old_max))


def migrate_legacy_technologies(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")
    database = schema_editor.connection.alias

    for guild in Guild.objects.using(database).all().iterator():
        legacy = {row.tech_key: row for row in GuildTechnology.objects.using(database).filter(guild_id=guild.pk)}

        for route_key, nodes in ROUTE_TECHS.items():
            for tech_key, max_level in nodes.items():
                GuildTechnology.objects.using(database).get_or_create(
                    guild_id=guild.pk,
                    tech_key=tech_key,
                    defaults={
                        "category": route_key,
                        "level": 0,
                        "max_level": max_level,
                    },
                )

        for tech_key, (category, max_level) in PUBLIC_TECHS.items():
            row = legacy.get(tech_key)
            if row:
                GuildTechnology.objects.using(database).filter(pk=row.pk).update(
                    category=category,
                    max_level=max_level,
                    level=min(max_level, int(row.level)),
                )
            else:
                GuildTechnology.objects.using(database).get_or_create(
                    guild_id=guild.pk,
                    tech_key=tech_key,
                    defaults={"category": category, "level": 0, "max_level": max_level},
                )

        equipment = legacy.get("equipment_forge")
        experience = legacy.get("experience_refine")
        mysticism = legacy.get("mysticism")
        resource_supply = legacy.get("resource_supply")
        resource_boost = legacy.get("resource_boost")
        guard_armory = legacy.get("guard_armory")

        mapped_levels = {
            "hero_skill_books": min(10, int(experience.level)) if experience else 0,
            "forging_mechanism_blueprint": _ceil_scaled(int(equipment.level), 10, 5) if equipment else 0,
            "wolf_soul_container": min(3, int(mysticism.level)) if mysticism else 0,
            "hero_rebirth_card": 1 if mysticism and mysticism.level >= 2 else 0,
            "hero_reset_card": 1 if mysticism and mysticism.level >= 2 else 0,
            "hero_marrow_pill": 1 if mysticism and mysticism.level >= 3 else 0,
            "sheep_grain_card": _ceil_scaled(int(resource_supply.level), 10, 3) if resource_supply else 0,
            "sheep_silver_card": _ceil_scaled(int(resource_supply.level), 10, 3) if resource_supply else 0,
            "sheep_farmland": _ceil_scaled(int(resource_boost.level), 5, 3) if resource_boost else 0,
            "sheep_silver_mine": _ceil_scaled(int(resource_boost.level), 5, 3) if resource_boost else 0,
            "wolf_bloodthirsty_cavalry": 1 if guard_armory and guard_armory.level > 0 else 0,
        }
        production_timestamps = {
            "hero_skill_books": experience.last_production_at if experience else None,
            "forging_mechanism_blueprint": equipment.last_production_at if equipment else None,
            "wolf_soul_container": mysticism.last_production_at if mysticism else None,
            "hero_rebirth_card": mysticism.last_production_at if mysticism else None,
            "hero_reset_card": mysticism.last_production_at if mysticism else None,
            "hero_marrow_pill": mysticism.last_production_at if mysticism else None,
            "sheep_grain_card": resource_supply.last_production_at if resource_supply else None,
            "sheep_silver_card": resource_supply.last_production_at if resource_supply else None,
        }

        for tech_key, level in mapped_levels.items():
            fields = {"level": level}
            timestamp = production_timestamps.get(tech_key)
            if timestamp is not None:
                fields["last_production_at"] = timestamp
            GuildTechnology.objects.using(database).filter(guild_id=guild.pk, tech_key=tech_key).update(**fields)

        GuildTechnology.objects.using(database).filter(guild_id=guild.pk).exclude(
            tech_key__in=[*PUBLIC_TECHS, *(key for nodes in ROUTE_TECHS.values() for key in nodes)]
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("guilds", "0030_guildmember_shanghai_date_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="guild",
            name="active_technology_route",
            field=models.CharField(blank=True, default="", max_length=20, verbose_name="当前科技路线"),
        ),
        migrations.AddField(
            model_name="guild",
            name="completed_technology_routes",
            field=models.JSONField(blank=True, default=list, verbose_name="已完成科技路线"),
        ),
        migrations.AlterField(
            model_name="guildtechnology",
            name="category",
            field=models.CharField(
                choices=[
                    ("hero", "英雄系"),
                    ("wolf", "狼系"),
                    ("sheep", "羊系"),
                    ("forging", "锻造系"),
                    ("public", "公共科技"),
                ],
                default="hero",
                max_length=20,
                verbose_name="科技分类",
            ),
        ),
        migrations.AlterField(
            model_name="guildtechnology",
            name="tech_key",
            field=models.CharField(
                help_text="如: hero_fruit_blueprint, troop_tactics",
                max_length=50,
                verbose_name="科技标识",
            ),
        ),
        migrations.CreateModel(
            name="GuildTechnologyAbilityUse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tech_key", models.CharField(max_length=50, verbose_name="科技标识")),
                ("used_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="使用时间")),
                (
                    "guild",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="technology_ability_uses",
                        to="guilds.guild",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guild_technology_ability_uses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "帮会科技能力使用记录",
                "verbose_name_plural": "帮会科技能力使用记录",
                "db_table": "guild_technology_ability_uses",
            },
        ),
        migrations.AddConstraint(
            model_name="guildtechnologyabilityuse",
            constraint=models.UniqueConstraint(fields=("user", "tech_key"), name="guild_tech_ability_user_uniq"),
        ),
        migrations.AddIndex(
            model_name="guildtechnologyabilityuse",
            index=models.Index(fields=["tech_key", "used_at"], name="guild_tech_ability_time_idx"),
        ),
        migrations.RunPython(migrate_legacy_technologies, migrations.RunPython.noop),
    ]
