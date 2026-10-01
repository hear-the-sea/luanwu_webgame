from django.db import migrations


def create_xiaoyao_shanzhuang(apps, schema_editor):
    BuildingType = apps.get_model("gameplay", "BuildingType")
    Building = apps.get_model("gameplay", "Building")
    Manor = apps.get_model("gameplay", "Manor")
    database = schema_editor.connection.alias

    reference = BuildingType.objects.using(database).filter(key="juxianzhuang").first()
    inherited_config = {
        "base_upgrade_time": 3107,
        "time_growth": 1.5,
        "upgrade_time_budget": 15552000,
        "time_curve": 1.10,
        "base_cost": {"grain": 3432, "silver": 17164},
        "cost_growth": 1.7,
        "upgrade_cost_budget": {},
        "cost_curve": 1.0,
    }
    if reference is not None:
        inherited_config = {field: getattr(reference, field) for field in inherited_config}
    building_type, _ = BuildingType.objects.using(database).update_or_create(
        key="xiaoyao_shanzhuang",
        defaults={
            "name": "逍遥山庄",
            "description": "江湖奔波久了，也该歇歇脚。空闲门客可来此归闲，暂不参战与出征；每升一级增加一处居所，随时可以召回。",
            "category": "special",
            "resource_type": "silver",
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            **inherited_config,
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
