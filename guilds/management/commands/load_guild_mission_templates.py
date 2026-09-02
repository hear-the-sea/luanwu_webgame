from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.utils import safe_int, safe_non_negative_int, safe_positive_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data
from guilds.constants import GUILD_MISSION_WEEKLY_LIMIT
from guilds.models import GuildMissionTemplate

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


def _normalize_task_type(value, *, allow_troops: bool) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"dispatch", "patrol"}:
        return GuildMissionTemplate.TaskType.TROOP if allow_troops else GuildMissionTemplate.TaskType.GUEST
    if normalized == "escort":
        return GuildMissionTemplate.TaskType.TROOP
    if normalized == "suppress":
        return GuildMissionTemplate.TaskType.DEFENSE
    if normalized in {
        GuildMissionTemplate.TaskType.GUEST,
        GuildMissionTemplate.TaskType.TROOP,
        GuildMissionTemplate.TaskType.DEFENSE,
    }:
        return normalized
    return GuildMissionTemplate.TaskType.TROOP if allow_troops else GuildMissionTemplate.TaskType.GUEST


class Command(BaseCommand):
    help = "Load guild mission templates (帮会任务配置) from a YAML/JSON config file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "guild_mission_templates.yaml"),
            help="Path to YAML/JSON file containing guild mission definitions.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")

        if file_path.suffix.lower() in {".yaml", ".yml"}:
            raw = load_yaml_data(
                file_path,
                logger=logger,
                context="guild mission templates import file",
                default={},
            )
            payload = ensure_mapping(raw, logger=logger, context="guild mission templates import root")
        elif file_path.suffix.lower() == ".json":
            with file_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise CommandError("JSON payload root must be an object.")
        else:
            raise CommandError("Unsupported file type. Use .yaml/.yml/.json")

        missions = ensure_list(payload.get("missions"), logger=logger, context="guild mission templates import entries")
        if not missions:
            self.stdout.write(self.style.WARNING("No guild missions found in file; nothing to import."))
            return

        for raw_entry in missions:
            entry = ensure_mapping(raw_entry, logger=logger, context="guild mission templates import entry")
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

            allow_troops = _coerce_bool(entry.get("allow_troops"), False)

            defaults = {
                "name": name,
                "description": str(entry.get("description") or ""),
                "difficulty": str(entry.get("difficulty") or "junior"),
                "task_type": _normalize_task_type(entry.get("task_type"), allow_troops=allow_troops),
                "base_duration_seconds": safe_positive_int(entry.get("base_duration_seconds"), 600),
                "ruby_reward": safe_non_negative_int(entry.get("ruby_reward"), 0),
                "weekly_limit": min(
                    safe_positive_int(entry.get("weekly_limit"), GUILD_MISSION_WEEKLY_LIMIT),
                    GUILD_MISSION_WEEKLY_LIMIT,
                ),
                "recommended_guest_count": safe_positive_int(entry.get("recommended_guest_count"), 1),
                "allow_troops": allow_troops,
                "enemy_guests": enemy_guests,
                "enemy_troops": enemy_troops,
                "enemy_technology": enemy_technology,
                "is_active": _coerce_bool(entry.get("is_active"), True),
                "sort_weight": safe_int(entry.get("sort_weight"), default=0),
            }
            obj, created = GuildMissionTemplate.objects.update_or_create(key=key, defaults=defaults)
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} guild mission {obj.key}")
