"""统一战龙第三、四关张飞的指定形象，仅更新头像。"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import migrations, transaction

from core.utils.image_utils import compress_and_resize_image

TEMPLATE_KEYS = ("task_zhanlong_zhangfei_bridge", "task_zhanlong_zhangfei")
SOURCE_IMAGE = "guests/monkey/任务/武047_男_张飞.png"


def sync_zhangfei_portraits(apps, schema_editor):
    database = schema_editor.connection.alias
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    source = Path(settings.BASE_DIR) / "data/images" / SOURCE_IMAGE
    digest = hashlib.sha256(b"zhangfei-portrait-v1:webp:400:85:" + source.read_bytes()).hexdigest()[:24]
    target = f"guests/zhanlong/zhangfei_wu047_{digest}.webp"
    if not default_storage.exists(target):
        content, _ = compress_and_resize_image(source, max_size=(400, 400), quality=85, convert_to_webp=True)
        target = default_storage.save(target, content, max_length=GuestTemplate._meta.get_field("avatar").max_length)
    with transaction.atomic(using=database):
        for template in GuestTemplate.objects.using(database).select_for_update().filter(key__in=TEMPLATE_KEYS):
            if template.avatar.name != target:
                template.avatar = target
                template.save(using=database, update_fields=["avatar"])
        transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0223_chiyou_guest_growth")]
    operations = [migrations.RunPython(sync_zhangfei_portraits, migrations.RunPython.noop, atomic=True)]
