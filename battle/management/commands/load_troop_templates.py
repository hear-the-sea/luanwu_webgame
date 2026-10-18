from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from battle.models import TroopTemplate
from core.utils import safe_int
from core.utils.image_utils import compress_and_resize_image
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data
from gameplay.services.utils.template_cache import clear_troop_template_caches

logger = logging.getLogger(__name__)
TROOP_AVATAR_LOAD_RECOVERABLE_EXCEPTIONS = (OSError,)


def _build_troop_defaults(data: dict) -> dict:
    priority = safe_int(data.get("priority"), default=0)
    default_count = safe_int(data.get("default_count"), default=120)
    if priority is None:
        priority = 0
    if default_count is None or default_count < 0:
        default_count = 120
    return {
        "name": str(data.get("name") or ""),
        "description": str(data.get("description") or ""),
        "base_attack": data.get("base_attack", 30),
        "base_defense": data.get("base_defense", 20),
        "base_hp": data.get("base_hp", 80),
        "speed_bonus": data.get("speed_bonus", 10),
        "priority": priority,
        "default_count": default_count,
    }


def _log_info(command: BaseCommand, verbosity: int, message: str) -> None:
    if verbosity >= 1:
        command.stdout.write(message)


def _load_avatar_for_troop(
    command: BaseCommand, obj: TroopTemplate, data: dict, image_source_dir: Path, verbosity: int
) -> None:
    avatar_filename = data.get("avatar")
    if not avatar_filename:
        return

    avatar_path = image_source_dir / avatar_filename
    if not avatar_path.exists():
        _log_info(command, verbosity, command.style.WARNING(f"  [NOT FOUND] Avatar not found: {avatar_path}"))
        return

    try:
        compressed_file, new_filename = compress_and_resize_image(
            avatar_path,
            max_size=(300, 300),
            quality=85,
            convert_to_webp=True,
        )
        if obj.avatar:
            obj.avatar.delete(save=False)
        obj.avatar.save(new_filename, compressed_file, save=True)
        _log_info(
            command,
            verbosity,
            command.style.SUCCESS(f"  [OK] Compressed and loaded avatar: {avatar_filename} -> {new_filename}"),
        )
    except TROOP_AVATAR_LOAD_RECOVERABLE_EXCEPTIONS as exc:
        _log_info(command, verbosity, command.style.WARNING(f"  [FAIL] Failed to load avatar {avatar_filename}: {exc}"))


class Command(BaseCommand):
    help = "Load troop templates from a YAML file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "troop_templates.yaml"),
            help="Path to YAML file containing troop templates.",
        )
        parser.add_argument(
            "--skip-images",
            action="store_true",
            help="Skip avatar compression/storage. Useful for CI/tests.",
        )

    def _load_payload(self, file_path: Path) -> dict:
        if not file_path.exists():
            raise CommandError(f"File {file_path} does not exist.")

        raw = load_yaml_data(
            file_path,
            logger=logger,
            context="troop templates import file",
            default={},
        )
        payload = ensure_mapping(raw, logger=logger, context="troop templates import root")

        if not payload:
            self.stdout.write(self.style.WARNING("Empty payload, nothing to import."))
            return {}
        return payload

    def _upsert_troop(self, data: dict) -> tuple[TroopTemplate, bool]:
        defaults = _build_troop_defaults(data)
        return TroopTemplate.objects.update_or_create(key=data["key"], defaults=defaults)

    def handle(self, *args, **options):
        verbosity = int(options.get("verbosity", 1) or 1)
        skip_images = bool(options.get("skip_images"))

        file_path = Path(options["file"])
        payload = self._load_payload(file_path)
        if not payload:
            return

        troop_data = payload.get("troops") or []
        troop_data = ensure_list(troop_data, logger=logger, context="troop templates import entries")
        image_source_dir = Path(settings.BASE_DIR) / "data" / "images" / "troops"
        if not skip_images and not image_source_dir.exists():
            raise CommandError(
                f"Troop avatar directory does not exist: {image_source_dir}. "
                "Please include source images in the runtime image or use --skip-images."
            )

        for raw_data in troop_data:
            data = ensure_mapping(raw_data, logger=logger, context="troop templates import entry")
            if not data:
                _log_info(self, verbosity, self.style.WARNING(f"Skip entry {raw_data!r}: invalid entry format"))
                continue
            key = str(data.get("key") or "").strip()
            name = str(data.get("name") or "").strip()
            if not key or not name:
                _log_info(self, verbosity, self.style.WARNING(f"Skip entry {data}: missing key or name"))
                continue
            data["key"] = key
            data["name"] = name
            obj, created = self._upsert_troop(data)
            if not skip_images:
                _load_avatar_for_troop(self, obj, data, image_source_dir, verbosity)

            action = "Created" if created else "Updated"
            _log_info(self, verbosity, f"{action} troop template {obj.key}")

        clear_troop_template_caches()
        _log_info(self, verbosity, self.style.SUCCESS("Troop templates synced."))
