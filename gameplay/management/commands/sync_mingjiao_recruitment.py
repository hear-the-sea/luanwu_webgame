"""定向同步光明顶招募道具，不触碰其它物品或玩家库存。"""

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gameplay.management.commands.load_item_templates import _build_item_defaults, _load_item_image, _load_payload
from gameplay.models import ItemTemplate

ITEM_KEYS = ("mingjiao_holy_fire_token", "mingjiao_fire_seed", "xiaozhao_recruit_scroll")


class Command(BaseCommand):
    help = "定向同步小昭、明教圣火令、火种和小昭招募卷轴，不改玩家库存。"

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        payload = _load_payload(base_dir / "data" / "item_templates.yaml")
        entries = {entry["key"]: entry for entry in payload.get("items", []) if entry.get("key") in ITEM_KEYS}
        if set(entries) != set(ITEM_KEYS):
            raise CommandError("光明顶招募道具配置不完整。")
        image_dir = base_dir / "data" / "images" / "items"
        for entry in entries.values():
            if not (image_dir / entry["image"]).is_file():
                raise CommandError(f"缺少道具图片：{entry['image']}")
        with transaction.atomic():
            call_command("sync_xiaozhao_template", stdout=self.stdout)
            for key in ITEM_KEYS:
                entry = entries[key]
                item, _ = ItemTemplate.objects.update_or_create(key=key, defaults=_build_item_defaults(entry))
                _load_item_image(self, item, entry, image_dir)
                self.stdout.write(self.style.SUCCESS(f"已同步：{item.name}"))
        self.stdout.write(self.style.SUCCESS("光明顶招募数据同步完成。"))
