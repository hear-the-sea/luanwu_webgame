"""清除中高级普通任务的特定顺序，交由模拟难度排序。"""

from django.db import migrations

EXCLUDED_MISSION_KEYS = (
    "sishui_huaxiong_chengwei",
    "hulao_qunying_zhan_lvbu",
    "baimenlou_mingyun_jueze",
    "biyou_candeng",
    "shier_xianyin",
    "fengbang_tianmen",
    "zhanlong_yanshan_shizi",
    "zhanlong_changban_tuwei",
    "zhanlong_changban_duanqiao",
    "zhanlong_wuhu_shizhen",
    "guild_personal_zhenlong_qipu",
    "guild_personal_shaolin_night_shadow",
    "guild_personal_yanziwu_secret_letter",
)


def clear_regular_mission_order(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    MissionTemplate.objects.using(schema_editor.connection.alias).filter(
        difficulty__in=("intermediate", "advanced")
    ).exclude(key__in=EXCLUDED_MISSION_KEYS).update(display_order=1000)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0238_surname_li_simplified_name")]
    operations = [migrations.RunPython(clear_regular_mission_order, migrations.RunPython.noop)]
