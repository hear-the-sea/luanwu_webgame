from django.db import migrations


def create_xiaoyao_shanzhuang(apps, schema_editor):
    BuildingType = apps.get_model("gameplay", "BuildingType")
    Building = apps.get_model("gameplay", "Building")
    Manor = apps.get_model("gameplay", "Manor")
    database = schema_editor.connection.alias

    reference = BuildingType.objects.using(database).get(key="juxianzhuang")
    building_type, _ = BuildingType.objects.using(database).update_or_create(
        key="xiaoyao_shanzhuang",
        defaults={
            "name": "逍遥山庄",
            "description": "江湖奔波久了，也该歇歇脚。空闲门客可来此归闲，暂不参战与出征；每升一级增加一处居所，随时可以召回。",
            "category": "special",
            "resource_type": "silver",
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": reference.base_upgrade_time,
            "time_growth": reference.time_growth,
            "upgrade_time_budget": reference.upgrade_time_budget,
            "time_curve": reference.time_curve,
            "base_cost": reference.base_cost,
            "cost_growth": reference.cost_growth,
            "upgrade_cost_budget": reference.upgrade_cost_budget,
            "cost_curve": reference.cost_curve,
            "icon": "",
        },
    )
    for manor_id in Manor.objects.using(database).values_list("pk", flat=True).iterator(chunk_size=1000):
        Building.objects.using(database).get_or_create(
            manor_id=manor_id,
            building_type_id=building_type.pk,
            defaults={"level": 0},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0192_arena_growth_execution_counter"),
        ("guests", "0077_guest_vacation_status"),
    ]

    operations = [migrations.RunPython(create_xiaoyao_shanzhuang, migrations.RunPython.noop)]
