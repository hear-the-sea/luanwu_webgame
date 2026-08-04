from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.utils.image_utils import compress_and_resize_image
from core.utils.yaml_loader import ensure_mapping, load_yaml_data
from gameplay.models import ItemTemplate
from gameplay.services.equipment_template_sync import synchronize_equipment_templates
from gameplay.services.inventory.core import GRAIN_ITEM_KEY

logger = logging.getLogger(__name__)


def _load_payload(file_path: Path):
    if file_path.suffix.lower() in {".yaml", ".yml"}:
        raw = load_yaml_data(
            file_path,
            logger=logger,
            context="item templates import file",
            default={},
        )
        return ensure_mapping(raw, logger=logger, context="item templates import root")
    with file_path.open("r", encoding="utf-8") as fh:
        if file_path.suffix.lower() == ".json":
            payload = json.load(fh)
            if isinstance(payload, dict):
                return payload
            raise CommandError("JSON payload root must be an object.")
    raise CommandError("Unsupported file type. Use .yaml/.yml/.json")


def _build_item_defaults(entry: dict) -> dict:
    return {
        "name": entry.get("name"),
        "description": entry.get("description", ""),
        "effect_type": entry.get("effect_type", ItemTemplate.EffectType.RESOURCE_PACK),
        "effect_payload": entry.get("effect_payload") or {},
        "icon": entry.get("icon", ""),
        "rarity": entry.get("rarity", "gray"),
        "tradeable": entry.get("tradeable", False),
        "price": entry.get("price", 0),
        "storage_space": entry.get("storage_space", 1),
        "is_usable": entry.get("is_usable", False),
    }


def _load_item_image(command: BaseCommand, obj: ItemTemplate, entry: dict, image_source_dir: Path) -> None:
    image_filename = entry.get("image")
    if not image_filename:
        return

    image_path = image_source_dir / image_filename
    if not image_path.exists():
        command.stdout.write(command.style.WARNING(f"  [NOT FOUND] Image not found: {image_path}"))
        return

    try:
        compressed_file, new_filename = compress_and_resize_image(
            image_path,
            max_size=(200, 200),
            quality=85,
            convert_to_webp=True,
        )
        if obj.image:
            obj.image.delete(save=False)
        obj.image.save(new_filename, compressed_file, save=True)
        command.stdout.write(
            command.style.SUCCESS(f"  [OK] Compressed and loaded image: {image_filename} -> {new_filename}")
        )
    except OSError as exc:
        command.stdout.write(command.style.WARNING(f"  [FAIL] Failed to load image {image_filename}: {exc}"))


class Command(BaseCommand):
    help = "Load ItemTemplate definitions from a YAML/JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "item_templates.yaml"),
            help="Path to YAML/JSON file containing item templates.",
        )
        parser.add_argument(
            "--repair-grain-ledger",
            action="store_true",
            help="同步模板后补建缺失的庄园仓库粮食账本。",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")

        payload = _load_payload(file_path)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not items:
            self.stdout.write(self.style.WARNING("No items found; nothing to import."))
            return

        image_source_dir = Path(settings.BASE_DIR) / "data" / "images" / "items"
        imported_keys: list[str] = []

        for entry in items:
            key = entry.get("key")
            name = entry.get("name")
            if not key or not name:
                self.stdout.write(self.style.WARNING(f"Skip entry {entry}: missing key or name."))
                continue

            obj, created = ItemTemplate.objects.update_or_create(key=key, defaults=_build_item_defaults(entry))
            imported_keys.append(obj.key)
            _load_item_image(self, obj, entry, image_source_dir)

            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} item template {obj.key}")

        sync_report = synchronize_equipment_templates(imported_keys)
        if sync_report.changed:
            self.stdout.write(
                self.style.SUCCESS(
                    "Synchronized equipment templates "
                    f"(updated={sync_report.gear_templates_updated}, "
                    f"aliases={sync_report.item_aliases_merged}, "
                    f"guests={sync_report.guests_reconciled})"
                )
            )

        # 默认不隐式执行全库修复；bootstrap_game_data 会显式开启该参数，
        # 保持一键部署的既有行为。
        if options.get("repair_grain_ledger") and GRAIN_ITEM_KEY in imported_keys:
            call_command("repair_grain_warehouse_ledger", verbosity=0)
