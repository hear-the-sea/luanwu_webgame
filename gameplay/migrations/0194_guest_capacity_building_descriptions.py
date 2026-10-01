from django.db import migrations


def update_capacity_descriptions(apps, schema_editor):
    """同步容量说明，保留已有建筑等级、升级进度和资源配置。"""
    BuildingType = apps.get_model("gameplay", "BuildingType")
    database = schema_editor.connection.alias
    descriptions = {
        "juxianzhuang": "天下英雄，入我彀中。广纳贤才，礼贤下士，方能成就一番霸业。初始0级可容纳5位门客，每升一级增加1位，满级15级可容纳20位。",
        "youxibaota": "登高望远，运筹帷幄。立于塔顶，可观四方风云，调兵遣将，决胜千里。初始0级可派出5位门客，每升一级增加1位，满级15级可同时派出20位。",
    }
    for key, description in descriptions.items():
        BuildingType.objects.using(database).filter(key=key).update(description=description)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0193_xiaoyao_shanzhuang")]

    operations = [migrations.RunPython(update_capacity_descriptions, migrations.RunPython.noop)]
