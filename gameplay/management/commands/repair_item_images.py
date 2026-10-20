"""修复物品图片断链；只修改图片字段，不重新导入业务配置。"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from core.utils.image_utils import compress_and_resize_image
from core.utils.yaml_loader import load_yaml_data
from gameplay.models import ItemTemplate

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "修复物品缺失图片：优先复用已有 WebP，否则从配置中的源图重新生成。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="仅检查并报告，不写入数据库或媒体文件。")
        parser.add_argument("--keys", nargs="+", help="仅处理指定的物品 key。")

    def handle(self, *args, **options):
        source_root = Path(settings.BASE_DIR) / "data" / "images" / "items"
        payload = load_yaml_data(
            Path(settings.BASE_DIR) / "data" / "item_templates.yaml",
            logger=logger,
            context="item image repair config",
            default={},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise CommandError("物品图片配置格式错误")
        sources = {
            entry["key"]: entry["image"]
            for entry in payload["items"]
            if isinstance(entry, dict) and entry.get("key") and isinstance(entry.get("image"), str)
        }
        items = ItemTemplate.objects.only("pk", "key", "image").order_by("pk")
        if options.get("keys"):
            items = items.filter(key__in=options["keys"])
        dry_run = options["dry_run"]
        checked = repaired = planned = unresolved = 0
        for item in items.iterator(chunk_size=1000):
            checked += 1
            old_name = item.image.name or ""
            storage = item.image.storage
            # 空字段可能刻意使用图标或文字占位，只修复已有路径的断链。
            if not old_name or storage.exists(old_name):
                continue
            source_filename = sources.get(item.key)
            if not source_filename:
                unresolved += 1
                self.stdout.write(self.style.WARNING(f"[UNRESOLVED] {item.key}: 未配置源图"))
                continue
            source = source_root / source_filename
            if not source.resolve().is_relative_to(source_root.resolve()):
                unresolved += 1
                self.stdout.write(self.style.WARNING(f"[UNRESOLVED] {item.key}: 源图路径越界"))
                continue
            target = f"items/{source.stem}.webp"
            try:
                reuse = storage.exists(target)
                if reuse:
                    with storage.open(target, "rb") as file:
                        with Image.open(file) as image:
                            image.verify()
                else:
                    if not source.is_file():
                        raise FileNotFoundError(f"找不到源图 {source_filename}")
                    with Image.open(source) as image:
                        image.verify()
                if dry_run:
                    planned += 1
                    self.stdout.write(f"[DRY RUN] {item.key}: {old_name or '(空)'} -> {target}")
                    continue
                if not reuse:
                    content, _ = compress_and_resize_image(source, max_size=(200, 200), quality=85)
                    target = storage.save(target, content)
                # 并发导入已改变图片时，不覆盖对方刚保存的路径。
                updated = ItemTemplate.objects.filter(pk=item.pk, image=item.image.name).update(image=target)
                repaired += updated
                if updated:
                    self.stdout.write(self.style.SUCCESS(f"[FIXED] {item.key}: {old_name or '(空)'} -> {target}"))
            except (OSError, ValueError) as exc:
                unresolved += 1
                self.stdout.write(self.style.WARNING(f"[UNRESOLVED] {item.key}: {exc}"))
        self.stdout.write(
            f"物品图片检查完成：checked={checked}, repaired={repaired}, planned={planned}, unresolved={unresolved}"
        )
