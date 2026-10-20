"""同步重绘的赵云甲胄与龙胆枪图标，兼顾已有词缀装备。"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import migrations, transaction
from django.db.models import Q

from core.utils.image_utils import compress_and_resize_image

IMAGE_SOURCES = {
    "equip_shuduanbaijinpao": "equip_shuduanbaijinpao.png",
    "equip_longdanqiang": "equip_longdanqiang.png",
}


def sync_zhaoyun_equipment_art(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    stored = {}
    for key, filename in IMAGE_SOURCES.items():
        source = Path(settings.BASE_DIR) / "data/images/items" / filename
        digest = hashlib.sha256(b"zhaoyun-art-v2:webp:200:85:" + source.read_bytes()).hexdigest()[:24]
        target = f"items/zhaoyun/{key}_{digest}.webp"
        if not default_storage.exists(target):
            content, _ = compress_and_resize_image(source, max_size=(200, 200), quality=85, convert_to_webp=True)
            target = default_storage.save(target, content)
        stored[key] = target
    with transaction.atomic(using=database):
        items = (
            ItemTemplate.objects.using(database)
            .select_for_update()
            .filter(Q(key__in=IMAGE_SOURCES) | Q(effect_payload__equipment_base_key__in=list(IMAGE_SOURCES)))
        )
        for item in items:
            base_key = item.key if item.key in stored else item.effect_payload["equipment_base_key"]
            if item.image.name != stored[base_key]:
                item.image = stored[base_key]
                item.save(using=database, update_fields=["image"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0225_remove_ordinary_yang_zheng")]
    operations = [migrations.RunPython(sync_zhaoyun_equipment_art, migrations.RunPython.noop, atomic=True)]
