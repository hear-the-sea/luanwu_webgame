from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.utils import safe_positive_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data
from gameplay.models import MissionTemplate

logger = logging.getLogger(__name__)


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return default


def _normalize_available_weekdays(value) -> list[int]:
    if not isinstance(value, list):
        return []

    weekdays: set[int] = set()
    for raw_weekday in value:
        if isinstance(raw_weekday, bool):
            continue
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            continue
        if 1 <= weekday <= 7:
            weekdays.add(weekday)
    return sorted(weekdays)


class Command(BaseCommand):
    help = "Load mission templates (掉落/敌人/耗时) from a YAML/JSON config file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "mission_templates.yaml"),
            help="Path to YAML/JSON file containing mission definitions.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")

        if file_path.suffix.lower() in {".yaml", ".yml"}:
            raw = load_yaml_data(
                file_path,
                logger=logger,
                context="mission templates import file",
                default={},
            )
            payload = ensure_mapping(raw, logger=logger, context="mission templates import root")
        elif file_path.suffix.lower() == ".json":
            with file_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise CommandError("JSON payload root must be an object.")
        else:
            raise CommandError("Unsupported file type. Use .yaml/.yml/.json")

        missions = ensure_list(payload.get("missions"), logger=logger, context="mission templates import entries")
        if not missions:
            self.stdout.write(self.style.WARNING("No missions found in file; nothing to import."))
            return

        for raw_entry in missions:
            entry = ensure_mapping(raw_entry, logger=logger, context="mission templates import entry")
            if not entry:
                self.stdout.write(self.style.WARNING(f"Skip entry {raw_entry!r}: invalid entry format"))
                continue

            key = str(entry.get("key") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not key or not name:
                self.stdout.write(self.style.WARNING(f"Skip entry {entry}: missing key or name"))
                continue

            enemy_guests = entry.get("enemy_guests")
            if not isinstance(enemy_guests, list):
                enemy_guests = []

            enemy_troops = entry.get("enemy_troops")
            if not isinstance(enemy_troops, dict):
                enemy_troops = {}

            enemy_technology = entry.get("enemy_technology")
            if not isinstance(enemy_technology, dict):
                enemy_technology = {}

            entry_cost = entry.get("entry_cost")
            if not isinstance(entry_cost, dict):
                entry_cost = {}

            drop_table = entry.get("drop_table")
            if not isinstance(drop_table, dict):
                drop_table = {}

            probability_drop_table = entry.get("probability_drop_table")
            if not isinstance(probability_drop_table, dict):
                probability_drop_table = {}
            defaults = {
                "name": name,
                "description": str(entry.get("description") or ""),
                "difficulty": str(entry.get("difficulty") or "junior"),
                "battle_type": str(entry.get("battle_type") or "task"),
                "is_defense": _coerce_bool(entry.get("is_defense"), False),
                "guest_only": _coerce_bool(entry.get("guest_only"), False),
                "enemy_guests": enemy_guests,
                "enemy_troops": enemy_troops,
                "enemy_technology": enemy_technology,
                "entry_cost": entry_cost,
                "drop_table": drop_table,
                "probability_drop_table": probability_drop_table,
                "available_weekdays": _normalize_available_weekdays(entry.get("available_weekdays")),
                "base_travel_time": safe_positive_int(entry.get("base_travel_time"), 1200),
                "daily_limit": safe_positive_int(entry.get("daily_limit"), 3),
            }
            obj, created = MissionTemplate.objects.update_or_create(key=key, defaults=defaults)
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} mission {obj.key}")
