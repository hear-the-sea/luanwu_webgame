"""按当前配置同步两位蚩尤卡门客的成长范围与分配权重。"""

from django.db import migrations

GUEST_GROWTH_RANGES = {
    "hero_yang_zheng_purple": [6, 14],
    "hero_di_qinglin": [6, 14],
}
GUEST_ATTRIBUTE_WEIGHTS = {
    "hero_yang_zheng_purple": {"force": 44, "intellect": 8, "defense": 32, "agility": 16},
    "hero_di_qinglin": {"force": 46, "intellect": 8, "defense": 18, "agility": 28},
}


def sync_chiyou_guest_growth(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, growth_range in GUEST_GROWTH_RANGES.items():
        GuestTemplate.objects.using(schema_editor.connection.alias).filter(key=key).update(
            growth_range=growth_range, attribute_weights=GUEST_ATTRIBUTE_WEIGHTS[key]
        )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0222_zhaoyun_equipment_rewards")]
    operations = [migrations.RunPython(sync_chiyou_guest_growth, migrations.RunPython.noop, atomic=True)]
