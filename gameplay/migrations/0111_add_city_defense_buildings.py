from django.db import migrations


def create_city_defense_buildings(apps, schema_editor):
    BuildingType = apps.get_model("gameplay", "BuildingType")
    Building = apps.get_model("gameplay", "Building")
    Manor = apps.get_model("gameplay", "Manor")

    wall, _ = BuildingType.objects.update_or_create(
        key="wall",
        defaults={
            "name": "城墙",
            "description": "高墙深垒，敌至城下先撞此关。升级提升战斗中的城墙血量与防御，满级30000血。",
            "category": "city_defense",
            "resource_type": "silver",
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": 900,
            "time_growth": 1.85,
            "base_cost": {"silver": 18000},
            "cost_growth": 1.85,
            "icon": "",
        },
    )
    arrow_tower, _ = BuildingType.objects.update_or_create(
        key="arrow_tower",
        defaults={
            "name": "箭塔",
            "description": "箭楼凌空，敌军未近先闻弦响。升级提升血量、防御与攻击，满级可同时攻击3个目标。",
            "category": "city_defense",
            "resource_type": "silver",
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": 1200,
            "time_growth": 1.85,
            "base_cost": {"silver": 18000},
            "cost_growth": 1.85,
            "icon": "",
        },
    )

    for manor in Manor.objects.all().iterator():
        Building.objects.get_or_create(manor=manor, building_type=wall)
        Building.objects.get_or_create(manor=manor, building_type=arrow_tower)


def remove_city_defense_buildings(apps, schema_editor):
    BuildingType = apps.get_model("gameplay", "BuildingType")
    BuildingType.objects.filter(key__in=["wall", "arrow_tower"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0110_missiontemplate_available_weekdays"),
    ]

    operations = [
        migrations.RunPython(create_city_defense_buildings, remove_city_defense_buildings),
    ]
