"""修复三档修行宝箱的压缩资源路径。"""

from django.db import migrations

CHEST_KEYS = (
    "mission_chest_v3_yaolu_hujing",
    "mission_chest_v3_xisui_yougu",
    "mission_chest_v3_yijin_mijing",
)
CHEST_IMAGE = "items/mission_chest_v1.webp"


def fix_chest_images(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    ItemTemplate.objects.using(schema_editor.connection.alias).filter(key__in=CHEST_KEYS).update(image=CHEST_IMAGE)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0205_cultivation_skill_book_tiers")]
    operations = [migrations.RunPython(fix_chest_images, migrations.RunPython.noop, atomic=True)]
