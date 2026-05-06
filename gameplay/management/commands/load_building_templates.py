from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.utils import safe_float, safe_non_negative_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data
from gameplay.models import BuildingType
from gameplay.services.utils.template_cache import clear_building_template_cache

logger = logging.getLogger(__name__)


def _coerce_non_negative_float(value, default: float) -> float:
    parsed = safe_float(value, default=default)
    if parsed is None or parsed < 0:
        return default
    return parsed


class Command(BaseCommand):
    help = "Load BuildingType definitions from a YAML/JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "building_templates.yaml"),
            help="Path to YAML/JSON file containing building templates.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")

        if file_path.suffix.lower() in {".yaml", ".yml"}:
            raw = load_yaml_data(
                file_path,
                logger=logger,
                context="building templates import file",
                default={},
            )
            payload = ensure_mapping(raw, logger=logger, context="building templates import root")
        elif file_path.suffix.lower() == ".json":
            with file_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise CommandError("JSON payload root must be an object.")
        else:
            raise CommandError("Unsupported file type. Use .yaml/.yml/.json")

        buildings = ensure_list(payload.get("buildings"), logger=logger, context="building templates import entries")
        if not buildings:
            self.stdout.write(self.style.WARNING("No buildings found; nothing to import."))
            return

        updated = 0
        created = 0
        skipped = 0
        for raw_entry in buildings:
            entry = ensure_mapping(raw_entry, logger=logger, context="building templates import entry")
            if not entry:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Skip entry {raw_entry!r}: invalid entry format."))
                continue

            key = str(entry.get("key") or "").strip()
            name = str(entry.get("name") or "").strip()
            resource_type = str(entry.get("resource_type") or "").strip()
            if not key or not name or not resource_type:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Skip entry {entry}: missing key/name/resource_type."))
                continue

            base_cost = entry.get("base_cost")
            if not isinstance(base_cost, dict):
                base_cost = {}
            defaults = {
                "name": name,
                "description": str(entry.get("description") or ""),
                "category": str(entry.get("category") or BuildingType._meta.get_field("category").default),
                "resource_type": resource_type,
                "base_rate_per_hour": safe_non_negative_int(entry.get("base_rate_per_hour"), 0),
                "rate_growth": _coerce_non_negative_float(entry.get("rate_growth"), 0.0),
                "base_upgrade_time": safe_non_negative_int(entry.get("base_upgrade_time"), 60),
                "time_growth": _coerce_non_negative_float(entry.get("time_growth"), 1.25),
                "base_cost": base_cost,
                "cost_growth": _coerce_non_negative_float(entry.get("cost_growth"), 1.35),
                "icon": str(entry.get("icon") or ""),
            }
            obj, was_created = BuildingType.objects.update_or_create(key=key, defaults=defaults)
            if was_created:
                created += 1
            else:
                updated += 1
            self.stdout.write(f"{'Created' if was_created else 'Updated'} building type {obj.key}")

        clear_building_template_cache()
        self.stdout.write(
            self.style.SUCCESS(f"Building templates synced. created={created} updated={updated} skipped={skipped}")
        )
